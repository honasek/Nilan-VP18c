"""Validate real HA temperature serialization without controller or transport I/O."""
import asyncio
import importlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM


async def validate():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    logging.getLogger().addHandler(handler)
    try:
        package = ModuleType('nilan_temperature_validation')
        package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/nilan_cts600')]
        sys.modules[package.__name__] = package
        climate = importlib.import_module(package.__name__ + '.climate')
        water = importlib.import_module(package.__name__ + '.water_heater')
        cases = 0
        with tempfile.TemporaryDirectory(prefix='nilan-temperature-') as directory:
            hass = HomeAssistant(directory)
            coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name='test', config_entry=None)
            coordinator.name = 'Synthetic Nilan'
            coordinator.entry = SimpleNamespace(entry_id='temperature-test')
            coordinator.device_info = None
            coordinator.last_show_data_success = time.time()
            coordinator.data = {'mode': 'HEAT', 'thermostat': 22, 'flow': 3,
                                'T11': 51, 'T12': 47, 'status': 'HOTWATER'}
            coordinator.menu = SimpleNamespace(values={'dhw_temperature': 52, 'dhw_aux_temperature': 40})
            reading = SimpleNamespace(valid=True, value=21)
            coordinator.t15_reading = lambda: reading
            coordinator.async_command = AsyncMock()
            coordinator.async_menu_command = AsyncMock()
            entities = [climate.CTS600Climate(coordinator),
                        water.CTS600WaterHeater(coordinator, water.BY_KEY['dhw_temperature']),
                        water.CTS600SeparateHeater(coordinator, 'compressor'),
                        water.CTS600SeparateHeater(coordinator, 'auxiliary')]
            native = hasattr(climate.ClimateEntity, 'native_temperature_unit')
            expected = [(21, 22), (51, 52), (47, 52), (51, 40)]
            for entity, values in zip(entities, expected):
                entity.hass = hass
                if native:
                    for old in ('_attr_temperature_unit', 'current_temperature', 'target_temperature'):
                        assert old not in type(entity).__dict__, (type(entity), old)
                    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
                assert (entity.native_current_temperature, entity.native_target_temperature) == values
                for units in (METRIC_SYSTEM, US_CUSTOMARY_SYSTEM):
                    hass.config.units = units
                    attrs = entity.state_attributes
                    converted = values if units is METRIC_SYSTEM else tuple(round(v * 1.8 + 32) for v in values)
                    assert (attrs['current_temperature'], attrs['temperature']) == converted, attrs
                    if native:
                        assert attrs['temperature_unit'] == units.temperature_unit
                    assert not any(key.startswith('native_') for key in attrs)
                    cases += 1
            reading.valid = False
            assert entities[0].native_current_temperature is None and not entities[0].available
            reading.valid = True
            coordinator.data.pop('T11')
            assert entities[1].native_current_temperature is None
            coordinator.menu.values['dhw_aux_temperature'] = 'OFF'
            assert entities[3].native_target_temperature is None and entities[3].current_operation == 'off'
            cases += 1
            for entity, target in zip(entities, (23, 53, 54, 41)):
                await entity.async_set_temperature(temperature=target)
            coordinator.async_command.assert_awaited_once_with('temperature', 23)
            assert [call.args for call in coordinator.async_menu_command.await_args_list] == [
                ('setting', {'key': 'dhw_temperature', 'value': 53}),
                ('setting', {'key': 'dhw_temperature', 'value': 54}),
                ('setting', {'key': 'dhw_aux_temperature', 'value': 41})]
            cases += 1
            await hass.async_stop()
        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING
                    and 'deprecated' in r.getMessage().lower() and 'nilan_temperature_validation' in r.getMessage()]
        assert not warnings, warnings
        return {'home_assistant': version('homeassistant'), 'cases': cases,
                'native_api': native, 'deprecation_warnings': len(warnings), 'device_writes': 0}
    finally:
        logging.getLogger().removeHandler(handler)


if __name__ == '__main__':
    print(json.dumps(asyncio.run(validate())))
