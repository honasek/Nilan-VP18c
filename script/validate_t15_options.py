"""Validate T15 options in isolated native HA; no transport or controller writes."""
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
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


async def validate(component: Path) -> dict:
    package = ModuleType('nilan_t15_validation')
    package.__path__ = [str(component.resolve())]
    sys.modules[package.__name__] = package
    flow_module = importlib.import_module(package.__name__+'.config_flow')
    coordinator_module = importlib.import_module(package.__name__+'.coordinator')
    model_module = importlib.import_module(package.__name__+'.tuv_runtime')
    primary, backup = 'sensor.room_main', 'sensor.room_backup'
    data = {'sensor_T15': primary, 'sensor_T15_backup': backup,
            'host': 'example.invalid', 'tcp_port': 4196, 'name': 'Synthetic Nilan'}
    entry = SimpleNamespace(entry_id='t15-validation', data=data, options={},
                            async_on_unload=lambda fn: None)
    cases = 0
    with tempfile.TemporaryDirectory(prefix='nilan-t15-validation-') as directory:
        hass = HomeAssistant(directory)
        hass.set_state(CoreState.running)
        hass.config_entries = SimpleNamespace(async_get_known_entry=lambda _id: entry,
                                              async_get_entry=lambda _id: entry)
        own = 'sensor.renamed_nilan_t15'
        registry = SimpleNamespace(async_get_entity_id=MagicMock(return_value=own))
        for entity, temp in ((primary, '21.5'), (backup, '20'), ('sensor.other_room', '22')):
            hass.states.async_set(entity, temp, {'unit_of_measurement': '°C'})
        hass.states.async_set('sensor.nilan_t15', '35', {'unit_of_measurement': '°C'})
        flow = flow_module.CTS600ConfigFlow.async_get_options_flow(entry)
        assert isinstance(flow, OptionsFlowWithReload) and flow.automatic_reload
        flow.hass = hass
        flow.handler = entry.entry_id

        with patch.object(hass.config_entries, 'async_get_known_entry', return_value=entry), \
             patch.object(flow_module.er, 'async_get', return_value=registry):
            result = await flow.async_step_init()
            assert result['type'] == 'form' and result['step_id'] == 'init'
            fields = {str(key): (key.description or {}).get('suggested_value') for key in result['data_schema'].schema}
            assert fields == {'sensor_T15': primary, 'sensor_T15_backup': backup}
            cases += 1
            registry.async_get_entity_id.assert_called_with('sensor', 'nilan_cts600', f'{entry.entry_id}-sensor-T15')
            entry.options = {'sensor_T15': 'sensor.other_room', 'sensor_T15_backup': None, 'future_option': 7}
            result = await flow.async_step_init()
            fields = {str(key): (key.description or {}).get('suggested_value') for key in result['data_schema'].schema}
            assert fields == {'sensor_T15': 'sensor.other_room', 'sensor_T15_backup': None}
            saved = await flow.async_step_init({'sensor_T15': primary})
            assert saved['type'] == 'create_entry' and saved['data'] == {
                'sensor_T15': primary, 'sensor_T15_backup': None, 'future_option': 7}
            assert entry.data == data  # No host, port or commissioning rewrite.
            cases += 1
            for submitted, field, error in [
                ({'sensor_T15': primary, 'sensor_T15_backup': primary}, 'sensor_T15_backup', 'duplicate_t15'),
                ({'sensor_T15': own}, 'sensor_T15', 'recursive_t15'),
                ({'sensor_T15': primary, 'sensor_T15_backup': own}, 'sensor_T15_backup', 'recursive_t15'),
                ({'sensor_T15': 'sensor.nilan_t15'}, 'sensor_T15', 'recursive_t15'),
                ({'sensor_T15': 'light.not_a_sensor'}, 'sensor_T15', 'invalid_t15'),
            ]:
                result = await flow.async_step_init(submitted)
                assert result['type'] == 'form' and result['errors'][field] == error
                cases += 1
            hass.states.async_set(primary, 'unavailable', {'unit_of_measurement': '°C'})
            result = await flow.async_step_init({'sensor_T15': primary, 'sensor_T15_backup': backup})
            assert result['type'] == 'create_entry'  # Valid backup permits offline primary.
            cases += 1
            hass.states.async_set(backup, 'unavailable', {'unit_of_measurement': '°C'})
            result = await flow.async_step_init({'sensor_T15': primary, 'sensor_T15_backup': backup})
            assert result['errors']['sensor_T15'] == 'invalid_t15'
            cases += 1
            hass.states.async_set(primary, '21.5', {'unit_of_measurement': '°C'})
            hass.states.async_set(backup, '20', {'unit_of_measurement': '°C'})
            hass.states.async_set('input_number.nilan_t15_max_age_minutes', 'unknown')
            result = await flow.async_step_init({'sensor_T15': primary})
            assert result['errors']['sensor_T15'] == 'invalid_t15'
            cases += 1
            hass.states.async_remove('input_number.nilan_t15_max_age_minutes')

        # Construct the actual coordinator behind mocked, unopened transport boundaries.
        entry.options = {'sensor_T15': 'sensor.other_room', 'sensor_T15_backup': None}
        with patch.object(coordinator_module, 'TransparentTCP', MagicMock()), \
             patch.object(coordinator_module, 'CTS600', MagicMock()):
            coordinator = coordinator_module.CTS600Coordinator(hass, entry)
            assert coordinator.sensor_entity_id == 'sensor.other_room'
            assert coordinator.backup_sensor_entity_id is None
            assert coordinator.t15_reading().value == 22
            assert coordinator.selected_sensor_entity_id == 'sensor.other_room'
            cases += 1
            # The real model adapter must use the selected input, not legacy 35 °C.
            coordinator.data = {'unit_type': 'test', 'software_1': 'test'}
            coordinator.last_show_data_success = __import__('time').time()
            runtime = model_module.TUVRuntime(hass, coordinator)
            assert runtime._sample().ambient == 22
            await runtime.async_shutdown()
            await coordinator.async_shutdown()
            cases += 1

    return {'home_assistant': version('homeassistant'), 'cases': cases,
            'options_reload_contract': True, 'saved_choices_preserved': True,
            'backup_removal_persists': True, 'self_reference_rejected': True,
            'coordinator_uses_options': True, 'model_uses_selected_ambient': True,
            'device_writes': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', type=Path, default=Path(__file__).resolve().parents[1]
                        / 'custom_components/nilan_cts600')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.component))))
