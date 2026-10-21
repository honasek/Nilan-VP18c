"""Exercise the DHW observer in isolated HA with synthetic data and no device I/O."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import importlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


async def validate(component: Path) -> dict:
    package = ModuleType("nilan_observer_validation")
    package.__path__ = [str(component.resolve())]
    sys.modules[package.__name__] = package
    module = importlib.import_module(package.__name__ + ".tuv_runtime")
    sensors = importlib.import_module(package.__name__ + ".tuv_sensor")
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture(logging.WARNING)
    module._LOGGER.addHandler(handler)
    try:
        with tempfile.TemporaryDirectory(prefix="nilan-observer-validation-") as directory:
            hass = HomeAssistant(directory)
            hass.set_state(CoreState.running)
            main = DataUpdateCoordinator(hass, logging.getLogger(__name__), config_entry=None,
                                         name="Synthetic Nilan", update_interval=None)
            main.entry = SimpleNamespace(entry_id="observer-validation", async_on_unload=lambda fn: None)
            main.device_info = {"identifiers": {("nilan_cts600", "synthetic")}}
            main.menu = SimpleNamespace(values={"dhw_temperature": 50})
            main.data = {"unit_type": "synthetic", "software_1": "synthetic", "T11": 55,
                         "T12": 50, "display": "AUTO\n1 20", "led": "off", "status": "VENTILAT",
                         "inletFlow": 1, "exhaustFlow": 1}
            base = time.time() - 180
            main.last_show_data_success = base
            runtime = module.TUVRuntime(hass, main)
            outputs = {key: sensors.TUVSensor(runtime, key, key) for key in (
                "model", "soc", "full_heat_remaining", "minimum_heat_remaining", "shadow_reserve")}

            def archive():
                return {str(p.relative_to(directory)): p.read_bytes()
                        for p in Path(directory).rglob("*.jsonl")}

            def waiting():
                assert runtime.data["valid"] is False
                assert runtime.data["quality"] == "waiting"
                assert runtime.data["reason"] == "Čekám na T11/T12"
                assert runtime.data["shadow"]["status"] == "waiting"
                assert outputs["model"].native_value == "waiting"
                assert all(not entity.available for key, entity in outputs.items() if key != "model")

            async def resume():
                runtime._schedule()
                await runtime._task
                assert runtime.data["valid"] is True
                assert runtime.data["quality"] == runtime.model.last_snapshot["quality"] != "waiting"
                assert outputs["soc"].available

            async def lose_input_during(method):
                entered, release = threading.Event(), threading.Event()
                original = getattr(runtime, method)

                def blocked(*args):
                    entered.set()
                    assert release.wait(10), "Validation worker timed out"
                    return original(*args)

                setattr(runtime, method, blocked)
                task = asyncio.create_task(runtime._step() if method == "_compute" else runtime._refresh_eta())
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    main.data.pop("T11")
                    release.set()
                    await task
                    waiting()
                finally:
                    release.set()
                    await task
                    setattr(runtime, method, original)
                    main.data["T11"] = 55

            try:
                await runtime.async_start()
                assert runtime.model.samples == 1 and runtime.data["valid"]
                baseline = deepcopy(runtime.model.dump())
                power = deepcopy(runtime.power_tracker.dump())
                learner = deepcopy(runtime.shadow.dump())
                archived = archive()
                attempted = runtime._attempted_at
                main.last_show_data_success = base + 60
                cases = [{"T11": None}, {"T12": None}, {"T11": None, "T12": None},
                         {"T11": float("nan")}, {"T12": float("inf")},
                         {"T11": True}, {"T12": "50"}]
                for change in cases:
                    main.data.update(T11=55, T12=50)
                    for key, value in change.items():
                        if value is None:
                            main.data.pop(key)
                        else:
                            main.data[key] = value
                    runtime._schedule()
                    waiting()
                    # Also cover work queued immediately before inputs disappear.
                    await runtime._step()
                    await runtime._refresh_eta()
                    waiting()
                    assert runtime.model.dump() == baseline
                    assert runtime.power_tracker.dump() == power
                    assert runtime.shadow.dump() == learner
                    assert archive() == archived
                    assert runtime._attempted_at == attempted
                    assert not records

                main.data.update(T11=55, T12=50)
                await resume()
                assert runtime.model.samples == 2
                # A temporary loss with the same sample time must restore quality too.
                baseline = deepcopy(runtime.model.dump())
                main.data.pop("T11")
                runtime._schedule()
                waiting()
                main.data["T11"] = 55
                await resume()
                assert runtime.model.dump() == baseline

                main.last_show_data_success = base + 120
                runtime._task = asyncio.create_task(runtime._step())
                main.data.pop("T12")  # Before the scheduled coroutine starts.
                await runtime._task
                waiting()
                assert runtime.model.dump() == baseline
                main.data["T12"] = 50
                await resume()
                assert runtime.model.samples == 3

                await lose_input_during("_forecast_sync")
                await resume()
                main.last_show_data_success = base + 180
                await lose_input_during("_compute")
                assert runtime.model.samples == 4  # The captured sample itself was complete.
                await resume()
                assert not records

                # Availability checks must not weaken physical validation or hide bugs.
                baseline = deepcopy(runtime.model.dump())
                main.data["T11"] = -1
                await runtime._step()
                assert runtime.data["valid"] is False and runtime.model.dump() == baseline
                assert len(records) == 1 and records[0].exc_info
                assert isinstance(records[0].exc_info[1], ValueError)
            finally:
                await runtime.async_shutdown()
                await main.async_shutdown()
    finally:
        module._LOGGER.removeHandler(handler)
    return {"ha_version": version("homeassistant"), "missing_input_cases": len(cases),
            "learning_preserved": True, "archive_unchanged_for_missing_inputs": True,
            "waiting_sensors_unavailable": True, "automatic_recovery": True,
            "same_sample_recovery": True, "queued_work_rechecked": True,
            "inflight_forecast_rechecked": True, "inflight_sample_rechecked": True,
            "unexpected_error_traceback_preserved": True, "device_writes": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, default=Path(__file__).resolve().parents[1]
                        / "custom_components" / "nilan_cts600")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.component))))
