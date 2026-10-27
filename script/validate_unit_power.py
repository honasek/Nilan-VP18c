"""Exercise power switch, climate and coordinator in isolated HA without device I/O."""
import argparse
import asyncio
from contextlib import nullcontext
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError


async def validate(component):
    pkg = ModuleType('nilan_power_native');pkg.__path__ = [str(component.resolve())];sys.modules[pkg.__name__] = pkg
    co = importlib.import_module(pkg.__name__+'.coordinator')
    sw = importlib.import_module(pkg.__name__+'.switch')
    climate = importlib.import_module(pkg.__name__+'.climate')
    buttons = importlib.import_module(pkg.__name__+'.button')
    Key = co.Key

    class Panel:
        def __init__(self):
            self.mode = self.resume = 'HEAT'
            self.data = {'mode': 'HEAT', 'flow': 3, 'thermostat': 22}
            self.keys = [];self.bad_final = False
        def resetMenu(self):return self.screen()
        def screen(self):return f'{self.mode}   */>3< 22°C'
        def setT15(self, value):assert 5 <= value <= 40
        def setMode(self, value):self.mode = self.resume = value
        def display(self):return self.screen()
        def key_on(self):return self.key(Key.ON)
        def key_off(self):return self.key(Key.OFF)
        def key(self, value=Key.NONE):
            self.keys.append(value)
            if value is Key.OFF:self.resume, self.mode = self.mode, 'OFF'
            elif value is Key.ON:self.mode = self.resume
            return self.screen()
        def updateData(self, full):
            if not self.bad_final:self.data['mode'] = self.mode

    with tempfile.TemporaryDirectory(prefix='nilan-power-validation-') as directory:
        hass = HomeAssistant(directory);hass.set_state(CoreState.running)
        entry = SimpleNamespace(entry_id='power-test',options={},async_on_unload=lambda fn: None,
            data={'host':'example.invalid','tcp_port':4196,'name':'Synthetic Nilan',
                  'sensor_T15':'sensor.room','sensor_T15_backup':None,'commands_enabled_entity':'input_boolean.unlock'})
        hass.states.async_set('sensor.room','21',{'unit_of_measurement':'°C'})
        hass.states.async_set('input_boolean.unlock','on')
        panel = Panel();transport = MagicMock();transport.operation.side_effect = lambda **kwargs: nullcontext()
        with patch.object(co,'CTS600',return_value=panel),patch.object(co,'TransparentTCP',return_value=transport):
            main = co.CTS600Coordinator(hass,entry);main._initialized = True;main.data = panel.data.copy()
            switch = sw.CTS600PowerSwitch(main);thermostat = climate.CTS600Climate(main)
            cases = 0
            try:
                assert switch.is_on is True and switch.available
                await switch.async_turn_off();assert switch.is_on is False and main.data['mode'] == 'OFF';cases += 1
                await switch.async_turn_on();assert switch.is_on is True and main.data['mode'] == 'HEAT';cases += 1
                count = len([k for k in panel.keys if k is not Key.NONE])
                await switch.async_turn_on();assert len([k for k in panel.keys if k is not Key.NONE]) == count;cases += 1
                await thermostat.async_turn_off();await thermostat.async_turn_on()
                assert main.data['mode'] == 'HEAT' and main.data['thermostat'] == 22 and main.data['flow'] == 3;cases += 1
                for key in (Key.OFF,Key.ON):await buttons.CTS600Button(main,key).async_press()
                assert main.data['mode'] == 'HEAT' and main.last_command == 'power';cases += 1
                await thermostat.async_set_hvac_mode(climate.HVACMode.OFF)
                assert switch.is_on is False
                await thermostat.async_turn_on();assert main.data['mode'] == 'HEAT';cases += 1
                await thermostat.async_set_hvac_mode(climate.HVACMode.AUTO)
                assert main.data['mode'] == 'AUTO';cases += 1
                for missing in ('gate','T15'):
                    before = panel.keys[:]
                    if missing == 'gate':hass.states.async_set('input_boolean.unlock','off')
                    else:hass.states.async_set('sensor.room','unavailable',{'unit_of_measurement':'°C'})
                    try:await switch.async_turn_off()
                    except HomeAssistantError:pass
                    else:raise AssertionError('Power command bypassed a guard')
                    assert panel.keys == before
                    hass.states.async_set('input_boolean.unlock','on')
                    hass.states.async_set('sensor.room','21',{'unit_of_measurement':'°C'});cases += 1
                before = panel.keys[:]
                try:await main.async_command('power','ON')
                except HomeAssistantError:pass
                else:raise AssertionError('Non-boolean power target accepted')
                assert panel.keys == before;cases += 1
                main.data = {'mode':'UNKNOWN'};assert switch.is_on is None;cases += 1
                main.data = panel.data.copy();panel.bad_final = True
                before = len([k for k in panel.keys if k is Key.OFF])
                try:await switch.async_turn_off()
                except HomeAssistantError:pass
                else:raise AssertionError('Missing final power state was accepted')
                assert not main._initialized and not main.last_update_success and transport.close.call_count == 1
                assert len([k for k in panel.keys if k is Key.OFF]) == before+1;cases += 1
            finally:await main.async_shutdown()
    return {'home_assistant':version('homeassistant'),'cases':cases,'native_switch_verified':True,
            'climate_and_buttons_share_verified_path':True,'mode_preserved':True,'guard_checks':True,
            'uncertain_write_not_replayed':True,'device_writes':0}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',type=Path,default=Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600')
    print(json.dumps(asyncio.run(validate(parser.parse_args().component))))
