"""Exercise real HA callbacks, selectors and journal recovery with synthetic hardware."""
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
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from homeassistant.core import CoreState, HassJob, HassJobType, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


async def validate(component):
    package=ModuleType('night_native');package.__path__=[str(component.resolve())]
    sys.modules[package.__name__]=package
    module=importlib.import_module(package.__name__+'.tuv_night_runtime')
    model_module=importlib.import_module(package.__name__+'.tuv_runtime')
    shadow=importlib.import_module(package.__name__+'.tuv_shadow')
    config=importlib.import_module(package.__name__+'.entity_config')
    checks=0
    with tempfile.TemporaryDirectory(prefix='night-native-') as directory:
        hass=HomeAssistant(directory);hass.config.time_zone='Europe/Prague';hass.set_state(CoreState.running)
        main=DataUpdateCoordinator(hass,logging.getLogger(__name__),config_entry=None,name='Synthetic CTS600',update_interval=None)
        main.entry=SimpleNamespace(entry_id='night-native',async_on_unload=lambda _:None)
        main.data={'unit_type':'synthetic','software_1':'synthetic','T11':56,'T12':49,'mode':'HEAT','status':'HEATING'}
        main.menu=SimpleNamespace(values={'dhw_temperature':50})
        main.config={key:f'{domain}.chosen_{key}' for key,domain in config.NIGHT_FIELDS.items()}
        main.config.update(commands_enabled_entity='input_boolean.chosen_manual',automatic_control_entity='input_boolean.chosen_auto')
        main.gate_entity=main.config['commands_enabled_entity']
        main.t15_reading=lambda:SimpleNamespace(valid=True,value=21)
        main.last_show_data_success=time.time()
        main._tuv_live_runtime=SimpleNamespace(control=SimpleNamespace(enabled=False,lease=None,forecast_target=lambda value:value))
        main.async_set_updated_data(dict(main.data))
        for key,value in [('night_enabled_entity','off'),('night_target_entity','55'),('night_start_entity','01:00:00'),
                          ('night_end_entity','05:00:00'),('night_duration_entity','180')]:
            hass.states.async_set(main.config[key],value,{'has_date':False} if 'start' in key or 'end' in key else {})
        for key in ('commands_enabled_entity','automatic_control_entity'):
            hass.states.async_set(main.config[key],'on')
        hass.states.async_set(main.config['price_entity'],'1',{'unit_of_measurement':'Kč/kWh'})
        physical={'target':50};writes=[]
        runtime=module.TUVNightRuntime(hass,main);main._tuv_night_runtime=runtime
        async def read_target():return physical['target']
        async def send(expected,value,restoring,guard):
            journal=await runtime.store.async_load()
            assert journal['control']['lease'] is not None
            if not restoring:assert guard()
            if physical['target']!=expected:return False
            writes.append((expected,value,restoring))
            physical['target']=value;main.menu.values['dhw_temperature']=value
            return True
        main.async_read_dhw_target=read_target;main.async_live_dhw=send
        now=time.time()
        selected={'window_start':now-900,'window_end':now+14400,'start':now-1,'end':now+10800,'average_price':1.0}
        with patch.object(module,'plan',return_value=(selected,None)):
            # Automatic-control permission belongs to live delay, not night.
            for value in ('off','on','unknown','unavailable'):
                hass.states.async_set(main.config['automatic_control_entity'],value)
                assert runtime._context()['permitted'];checks+=1
            hass.states.async_remove(main.config['automatic_control_entity'])
            assert runtime._context()['permitted'];checks+=1
            hass.states.async_set(main.config['automatic_control_entity'],'off')
            hass.states.async_set(main.config['commands_enabled_entity'],'off')
            assert not runtime._context()['permitted'];checks+=1
            hass.states.async_set(main.config['commands_enabled_entity'],'on')
            main.t15_reading=lambda:SimpleNamespace(valid=False)
            assert not runtime._context()['permitted'];checks+=1
            main.t15_reading=lambda:SimpleNamespace(valid=True,value=21)
            sample=main.last_show_data_success
            main.last_show_data_success=time.time()-181
            assert not runtime._context()['valid'];checks+=1
            main.last_show_data_success=sample
            main._tuv_live_runtime.control.lease={'pending':True}
            assert not runtime._context()['permitted'];checks+=1
            main._tuv_live_runtime.control.lease=None
            await runtime.async_start()
            assert HassJob(runtime._schedule).job_type is HassJobType.Callback;checks+=1
            await runtime._task
            assert not writes and runtime.control.status=='disabled';checks+=1
            hass.states.async_set(main.config['night_enabled_entity'],'on')
            await hass.async_block_till_done()
            assert physical['target']==55 and runtime.control.status=='heating';checks+=1
            for value in ('on','off'):
                hass.states.async_set(main.config['automatic_control_entity'],value)
                await runtime._tick()
                assert physical['target']==55 and runtime.control.status=='heating' and len(writes)==1;checks+=1
            # The stock/ETA target remains normal during a temporary boost.
            model=model_module.TUVRuntime(hass,main)
            assert model._forecast_key()[1]==50;checks+=1
            with patch.object(shadow,'ZoneInfo',side_effect=AssertionError('Blocking timezone read')):
                settings,error=model._shadow_settings()
                assert error is None and not settings.enabled;checks+=1
            # Armed delay cannot coexist; cancellation restores through read/compare.
            main._tuv_live_runtime.control.enabled=True
            await runtime._tick()
            assert physical['target']==50 and runtime.control.lease is None;checks+=1
            main._tuv_live_runtime.control.enabled=False
            await runtime._tick()
            assert len(writes)==2;checks+=1
            await runtime.async_shutdown()
            # Journal recovery remains active with the enable helper OFF.
            recovery=module.TUVNightRuntime(hass,main);main._tuv_night_runtime=recovery
            stored=await runtime.store.async_load()
            assert stored['control']['consumed_until']==selected['window_end'];checks+=1
            hass.states.async_set(main.config['night_enabled_entity'],'off')
            await recovery.async_start();await recovery._task
            assert recovery.control.consumed_until==selected['window_end'] and not recovery.control.lease;checks+=1
            hass.states.async_set(main.config['night_enabled_entity'],'on')
            await hass.async_block_till_done()
            assert recovery.control.status=='finished' and len(writes)==2;checks+=1
            await recovery.async_shutdown()
            await model.async_shutdown()
        # Read-only model works with no external helpers and arbitrary entity names.
        assert config.selected({},'power_entity') is None;checks+=1
        await main.async_shutdown();await hass.async_stop()
    return {'home_assistant':version('homeassistant'),'checks':checks,'synthetic_target_writes':writes,
            'real_device_writes':0,'native_callbacks':True,'normal_stock_reference_preserved':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',type=Path,default=Path(__file__).resolve().parents[1]/'custom_components/nilan_cts600')
    args=parser.parse_args()
    print(json.dumps(asyncio.run(validate(args.component))))
