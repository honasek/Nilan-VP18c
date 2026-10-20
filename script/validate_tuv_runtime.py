"""Check live scheduler dispatch with real HA, temporary storage and no device I/O."""
from __future__ import annotations

import argparse
import asyncio
import importlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
from datetime import timedelta
from types import ModuleType, SimpleNamespace

from homeassistant.core import CoreState, HassJob, HassJobType, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


async def validate(component: Path) -> dict:
    package = ModuleType("nilan_runtime_validation")
    package.__path__ = [str(component.resolve())]
    sys.modules[package.__name__] = package
    module = importlib.import_module(package.__name__ + ".tuv_live_runtime")
    with tempfile.TemporaryDirectory(prefix="nilan-runtime-validation-") as directory:
        hass = HomeAssistant(directory)
        hass.set_state(CoreState.running)
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        errors = []
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        main = DataUpdateCoordinator(
            hass, logging.getLogger(__name__), config_entry=None,
            name="Synthetic Nilan", update_interval=None,
        )
        main.entry = SimpleNamespace(entry_id="runtime-validation", async_on_unload=lambda fn: None)
        main.data = {"unit_type": "synthetic", "software_1": "synthetic", "T11": 55, "T12": 45}
        main.menu = SimpleNamespace(values={"dhw_temperature": 50})
        main.gate_entity = "input_boolean.nilan_control_enabled"
        main.last_show_data_success = time.time()
        main.t15_reading = lambda: SimpleNamespace(valid=True)
        main._tuv_runtime = SimpleNamespace(data={"valid": True})
        device_writes = []

        async def reject_device_write(*args, **kwargs):
            device_writes.append(args)
            raise AssertionError("Validation must never issue a controller command")

        main.async_live_dhw = reject_device_write
        runtime = module.TUVLiveRuntime(hass, main)
        # Without @callback, HA classifies this function as Executor.
        assert HassJob(runtime._schedule).job_type is HassJobType.Callback
        track_interval = module.async_track_time_interval
        module.async_track_time_interval = lambda h, action, interval: track_interval(
            h, action, timedelta(milliseconds=25)
        )
        tick_threads = []
        active = maximum = 0
        original_tick = runtime._tick

        async def observed_tick():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            tick_threads.append(threading.get_ident())
            try:
                await original_tick()
                await asyncio.sleep(.005)
            finally:
                active -= 1

        runtime._tick = observed_tick
        try:
            await runtime.async_start()
            await asyncio.sleep(.10)
            assert len(tick_threads) >= 2
            # Isolate the other triggers from the periodic timer.
            runtime._unsub[1]()
            runtime._unsub[1] = lambda: None
            await runtime._task
            count = len(tick_threads)
            main.async_set_updated_data({**main.data, "validation_sequence": 1})
            await runtime._task
            assert len(tick_threads) > count
            count = len(tick_threads)
            hass.states.async_set(main.gate_entity, "off")
            await asyncio.sleep(.02)
            assert len(tick_threads) > count
            runtime._schedule()
            task = runtime._task
            for _ in range(20):
                runtime._schedule()
            assert runtime._task is task
            await task
            assert maximum == 1 and all(t == loop_thread for t in tick_threads)
            assert not runtime.control.enabled and runtime.control.lease is None
            await runtime.async_shutdown()
            count = len(tick_threads)
            runtime._schedule()
            main.async_set_updated_data(dict(main.data))
            hass.states.async_set(main.gate_entity, "on")
            await asyncio.sleep(.06)
            assert len(tick_threads) == count and not runtime._unsub
            assert not device_writes and not errors, errors
        finally:
            await runtime.async_shutdown()
            await main.async_shutdown()
            module.async_track_time_interval = track_interval
            loop.set_exception_handler(old_handler)
    return {
        "ha_version": version("homeassistant"), "timer_on_loop": True,
        "state_event_on_loop": True, "coordinator_on_loop": True,
        "maximum_concurrent_ticks": maximum, "pending_tick_coalescing": True,
        "shutdown_unsubscribed": True, "loop_errors": 0, "device_writes": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, default=Path(__file__).resolve().parents[1]
                        / "custom_components" / "nilan_cts600")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.component))))
