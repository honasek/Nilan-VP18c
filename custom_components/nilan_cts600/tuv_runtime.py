"""Isolated HA adapter for the thermal observer; no control services or I/O to Nilan."""
from __future__ import annotations

from datetime import timedelta, datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
from copy import deepcopy

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .panel_indicators import indicators
from .tuv_model import ThermalObserver, Sample, finite
from .tuv_power import PowerTracker
from .tuv_eta import forecast,blank
from .tuv_shadow import ShadowLearner,Settings,make_plan,empty_plan,stamp
from dataclasses import asdict
from statistics import median

_LOGGER=logging.getLogger(__name__)
SHADOW_HELPERS={
    'enabled':'input_boolean.nilan_tuv_shadow_enabled',
    'day_enabled':'input_boolean.nilan_tuv_shadow_day_window_enabled',
    'window_start':'input_datetime.nilan_tuv_shadow_window_start',
    'window_end':'input_datetime.nilan_tuv_shadow_window_end',
    'day_start':'input_datetime.nilan_tuv_shadow_day_start',
    'day_end':'input_datetime.nilan_tuv_shadow_day_end',
    'reserve_pct':'input_number.nilan_tuv_shadow_reserve_pct',
    'top_min_c':'input_number.nilan_tuv_shadow_top_min_c',
    'prior_daily_l':'input_number.nilan_tuv_shadow_prior_daily_l',
    'margin_min':'input_number.nilan_tuv_shadow_margin_min',
}
SHADOW_INITIALIZED='input_boolean.nilan_tuv_shadow_initialized'


class TUVRuntime(DataUpdateCoordinator):
    def __init__(self,hass,main):
        super().__init__(hass,_LOGGER,name=f'{main.name} TUV model',config_entry=main.entry,update_interval=None)
        self.main=main
        self.model=ThermalObserver()
        self.store=Store(hass,1,f'nilan_cts600.{main.entry.entry_id}.tuv_model')
        self.identity={key:main.data.get(key) for key in ('unit_type','software_1')}
        self.archive=Path(hass.config.path('nilan_tuv_model',main.entry.entry_id))
        self._cached_dump=None
        self._task=None
        self._closed=False
        self._last_save=0.0
        self._unsub=[]
        self._stop_unsub=None
        self._attempted_at=0.0
        self.io_error=None
        self.model_error=None
        self.power_tracker=PowerTracker()
        self._eta_key=None
        self.shadow=ShadowLearner()
        self.shadow_store=Store(hass,1,f'nilan_cts600.{main.entry.entry_id}.tuv_shadow')
        self.shadow_archive=Path(hass.config.path('nilan_tuv_shadow',main.entry.entry_id))
        self._shadow_plan=None
        self._shadow_key=None
        self._cached_shadow_dump=None
        self.shadow_io_error=None
        self.shadow_error=None

    async def async_start(self):
        try:
            stored=await self.store.async_load()
            if all(self.identity.values()) and isinstance(stored,dict) and stored.get('identity')==self.identity:
                self.model=ThermalObserver.restore(stored['model'])
                if self.model.last.at>time.time()+5:raise ValueError('Model timestamp is in the future')
                self._cached_dump=stored
                if stored.get('power_context') is not None:
                    try:
                        self.power_tracker=PowerTracker.restore(stored['power_context'])
                        if self.power_tracker.changed_at>self.model.last.at+5:raise ValueError('Power context timestamp is in the future')
                    except ValueError:
                        self.power_tracker=PowerTracker()
                        _LOGGER.warning('TUV: neplatný kontext příkonu; čekám na nové hlášení')
                else:
                    last=self.model.last
                    self.power_tracker.observe((last.compressor,last.auxiliary,last.mode,last.fan_key),last.at)
        except Exception:
            self.model=ThermalObserver()
            _LOGGER.warning('TUV: uložený model nelze obnovit, začíná s širokým rozsahem odhadu',exc_info=True)
        try:
            stored_shadow=await self.shadow_store.async_load()
            if isinstance(stored_shadow,dict) and all(self.identity.values()) and stored_shadow.get('identity')==self.identity:
                self.shadow=ShadowLearner.restore(stored_shadow['learner'],time.time())
        except Exception:
            self.shadow=ShadowLearner()
            self.shadow_error='Uložené učení shadow nelze obnovit; sběr začíná znovu'
            _LOGGER.warning('TUV shadow: neplatné uložené učení; skutečný model zůstává zachován',exc_info=True)
        self._unsub.append(self.main.async_add_listener(self._schedule))
        self._unsub.append(async_track_time_interval(self.hass,self._timer,timedelta(seconds=60)))
        self._unsub.append(async_track_state_change_event(self.hass,[*SHADOW_HELPERS.values(),SHADOW_INITIALIZED],self._shadow_changed))
        self._stop_unsub=self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP,self._stop_event)
        self._schedule()
        if self._task is not None:await self._task

    @callback
    def _timer(self,now):self._schedule()

    @callback
    def _shadow_changed(self,event):self._schedule()

    def _shadow_settings(self):
        if not self.hass.states.is_state(SHADOW_INITIALIZED,'on'):
            return Settings(enabled=False),'Čekám na inicializaci nastavení shadow'
        values={'timezone':self.hass.config.time_zone}
        try:
            for key,entity_id in SHADOW_HELPERS.items():
                state=self.hass.states.get(entity_id)
                if state is None or state.state in ('unknown','unavailable') or state.attributes.get('restored'):
                    raise ValueError('Nastavení shadow není dostupné')
                if key in ('enabled','day_enabled'):
                    if state.state not in ('on','off'):raise ValueError('Neznámé povolení shadow')
                    values[key]=state.state=='on'
                elif key in ('window_start','window_end','day_start','day_end'):values[key]=state.state[:5]
                else:values[key]=float(state.state)
            return Settings(**values).validate(),None
        except (ValueError,TypeError,KeyError) as exc:return Settings(enabled=False),str(exc)

    def _shadow_signature(self,settings,error):
        key=self._forecast_key()
        last=self.model.last
        context_ready=last is not None and key[2:]==(last.compressor,last.auxiliary,last.mode,last.fan_key)
        return (tuple(asdict(settings).items()),error,int(time.time()//900),key[1:],
                self.model.active_draw,self.model.phase if self.model.phase in ('initializing','gap','mismatch','restored') else 'stable',
                context_ready,self.shadow.summary()['learning_ready'])

    def _validity(self):
        at=getattr(self.main,'last_show_data_success',None)
        if not self.main.last_update_success:return False,'Spojení s jednotkou není dostupné'
        if not finite(at) or time.time()-at > 300 or at > time.time()+5:return False,'Čeká na čerstvé T11/T12 z přehledu dat'
        return True,None

    @callback
    def _schedule(self):
        if self._closed:return
        if self._task is not None and not self._task.done():return
        valid,reason=self._validity()
        if not valid:
            self._shadow_key=None
            self.async_set_updated_data({**(self.data or {}),'valid':False,'quality':'waiting','reason':reason,
                                        'shadow':{**empty_plan(time.time(),reason,'waiting'),'learning':self.shadow.summary()}})
            return
        at=self.main.last_show_data_success
        if self.model.last is not None and at<=self.model.last.at:
            key=self._forecast_key()
            settings,error=self._shadow_settings()
            if (key!=self._eta_key or self._shadow_signature(settings,error)!=self._shadow_key) and (self._task is None or self._task.done()):
                self._task=self.hass.async_create_task(self._refresh_eta(),f'Nilan TUV heating forecast {self.main.entry.entry_id}')
            return
        if at<=self._attempted_at:return
        if self._task is None or self._task.done():
            self._task=self.hass.async_create_task(self._step(),f'Nilan TUV observer {self.main.entry.entry_id}')

    def _power(self,at):
        state=self.hass.states.get('sensor.rekuperace_vykon')
        reported=getattr(state,'last_reported',None)
        return self.power_tracker.choose(getattr(state,'state',None),state.attributes.get('unit_of_measurement') if state else None,
                                         reported.timestamp() if reported else None,at,
                                         restored=bool(state and state.attributes.get('restored')))

    def _sample(self):
        data=self.main.data or {}
        flags=indicators(data)
        at=self.main.last_show_data_success
        fan_key=f"{data.get('inletFlow','?')}/{data.get('exhaustFlow','?')}"
        mode=str(data.get('status','unknown'))
        gap=self.model.last is not None and at-self.model.last.at>300
        self.power_tracker.observe((flags['compressor_running'],flags['dhw_aux_running'],mode,fan_key),at,gap=gap)
        power=self._power(at)
        ambient=self.hass.states.get('sensor.nilan_t15')
        try:
            room=float(ambient.state)
            if ambient.attributes.get('restored') or ambient.attributes.get('unit_of_measurement')!='°C' or not finite(room) or not 0<=room<=40:room=20.0
        except (TypeError,ValueError,AttributeError):room=20.0
        return Sample(at=at,top=data.get('T11'),bottom=data.get('T12'),
                      compressor=flags['compressor_running'],auxiliary=flags['dhw_aux_running'],
                      mode=mode,power_w=power.watts,ambient=room,fan_key=fan_key,
                      power_quality=power.quality,power_reported_at=power.reported_at,power_age_s=power.age_s,power_reason=power.reason)

    def _forecast_key(self):
        data=self.main.data or {};flags=indicators(data)
        raw=getattr(getattr(self.main,'menu',None),'values',{}).get('dhw_temperature')
        try:target=float(raw) if not isinstance(raw,bool) else None
        except (TypeError,ValueError):target=None
        if not finite(target) or not 5<=target<=60:target=None
        return (self.main.last_show_data_success,target,flags['compressor_running'],flags['dhw_aux_running'],
                str(data.get('status','unknown')),f"{data.get('inletFlow','?')}/{data.get('exhaustFlow','?')}")

    def _forecast_sync(self,key):
        last=self.model.last
        changed=last is not None and key[2:]!=(last.compressor,last.auxiliary,last.mode,last.fan_key)
        try:return forecast(self.model,key[1],context_changed=changed)
        except Exception:
            _LOGGER.warning('TUV: čas do ohřevu nelze spočítat; odhad zásoby pokračuje',exc_info=True)
            return blank(key[1],'forecast_error','Čas do ohřevu není právě dostupný')

    async def _refresh_eta(self):
        key=self._forecast_key()
        result=await self.hass.async_add_executor_job(self._forecast_sync,key)
        if key!=self._forecast_key():return
        settings,error=self._shadow_settings()
        shadow,record=await self.hass.async_add_executor_job(self._shadow_sync,settings,error,result,False)
        result['shadow']=shadow
        if record is not None:
            try:
                await self.hass.async_add_executor_job(self._archive_shadow_sync,None,result,record)
                self.shadow_io_error=None
            except Exception:
                self.shadow_io_error='Nelze uložit archiv shadow'
                _LOGGER.warning('TUV shadow: zápis plánu se nezdařil',exc_info=True)
        self._eta_key=key
        valid,reason=self._validity()
        if not valid:result['shadow']={**empty_plan(time.time(),reason,'waiting'),'learning':self.shadow.summary()}
        self.async_set_updated_data({**(self.data or {}),**result,'valid':valid,'reason':reason})

    def _shadow_sync(self,settings,error,result,observe):
        try:
            if observe and error is None and settings.enabled:
                self.shadow.observe(self.model,result.get('eta_target_c'),settings)
                self.shadow.register_eta(self.model,result)
            signature=self._shadow_signature(settings,error)
            record=None
            if signature!=self._shadow_key or self._shadow_plan is None:
                if error is not None:plan=empty_plan(time.time(),error,'waiting')
                elif result.get('eta_assumption') is not None and any(result.get(k,{}).get('status')=='context_changed' for k in ('minimum_heat_remaining','full_heat_remaining')):
                    plan=empty_plan(time.time(),'Čekám na úplný odečet po změně provozu','waiting')
                else:plan=make_plan(self.model,self.shadow,settings,result.get('eta_target_c'),time.time())
                self._shadow_plan=plan;self._shadow_key=signature
                record=deepcopy(plan)
            self._cached_shadow_dump={'identity':self.identity,'learner':self.shadow.dump()}
            public={key:value for key,value in self._shadow_plan.items() if key not in ('initial_profiles','forecast_demand')}
            public['learning']=self.shadow.summary()
            public['io_error']=self.shadow_io_error
            public['restore_warning']=self.shadow_error
            return public,record
        except Exception:
            self._shadow_key=None
            _LOGGER.warning('TUV shadow: výpočet není dostupný; odhad zásoby i provoz jednotky pokračují',exc_info=True)
            return {**empty_plan(time.time(),'Shadow výpočet má chybu'),'learning':self.shadow.summary()},None

    def _compute(self,sample,key,settings,error):
        result=self.model.step(sample)
        record=None
        if result is not None:
            result={**result,**self._forecast_sync(key)}
            result['shadow'],record=self._shadow_sync(settings,error,result,True)
        return result,self.model.dump(),record

    async def _step(self):
        try:
            sample=self._sample()
            self._attempted_at=sample.at
            key=self._forecast_key()
            settings,error=self._shadow_settings()
            result,dump,record=await self.hass.async_add_executor_job(self._compute,sample,key,settings,error)
            if result is None:return
            self.model_error=None
            self._eta_key=key
            self._cached_dump={'identity':self.identity,'model':dump,'power_context':self.power_tracker.dump()}
            try:
                await self.hass.async_add_executor_job(self._archive_sync,sample,result)
                self.io_error=None
            except Exception:
                self.io_error='Nelze uložit výukový archiv'
                _LOGGER.warning('TUV: zápis archivu se nezdařil',exc_info=True)
            try:
                if error is None and settings.enabled:
                    await self.hass.async_add_executor_job(self._archive_shadow_sync,sample,result,record)
                self.shadow_io_error=None
            except Exception:
                self.shadow_io_error='Nelze uložit archiv shadow'
                _LOGGER.warning('TUV shadow: archiv nelze uložit; řízení jednotky není ovlivněno',exc_info=True)
            if time.monotonic()-self._last_save>300:
                try:
                    await self.store.async_save(self._cached_dump)
                    self._last_save=time.monotonic()
                except Exception:
                    self.io_error='Nelze uložit stav modelu'
                    _LOGGER.warning('TUV: uložení stavu se nezdařilo',exc_info=True)
                if self._cached_shadow_dump is not None:
                    try:await self.shadow_store.async_save(self._cached_shadow_dump)
                    except Exception:
                        self.shadow_io_error='Nelze uložit učení shadow'
                        _LOGGER.warning('TUV shadow: učení nelze uložit',exc_info=True)
            result['shadow']['io_error']=self.shadow_io_error
            valid,reason=self._validity()
            if not valid:result['shadow']={**empty_plan(time.time(),reason,'waiting'),'learning':self.shadow.summary()}
            self.async_set_updated_data({**result,'valid':valid,'reason':reason,'io_error':self.io_error})
        except Exception:
            self.model_error='Model nemůže vyhodnotit aktuální data'
            _LOGGER.warning('TUV: model nebyl aktualizován; ovládání jednotky není ovlivněno',exc_info=True)
            self.async_set_updated_data({**(self.data or {}),'valid':False,'quality':'waiting','reason':self.model_error,
                                        'shadow':empty_plan(time.time(),self.model_error,'waiting')})

    def _archive_shadow_sync(self,sample,result,record):
        self.shadow_archive.mkdir(parents=True,exist_ok=True)
        at=time.time() if sample is None else sample.at
        day=datetime.fromtimestamp(at,timezone.utc).date()
        def append(name,value):
            with (self.shadow_archive/name).open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(value,allow_nan=False,separators=(',',':'))+'\n')
        if sample is not None:
            append('samples-'+day.isoformat()+'.jsonl',{
                'sample':asdict(sample),'target_c':result.get('eta_target_c'),
                'soc':result['soc'],'lower':result['lower'],'upper':result['upper'],
                'draw_l_min_model':median(p.draw_l_min for p in self.model.particles) if self.model.active_draw else 0,
                'draw_probability_model':self.model.draw_probability,'drawing_model':self.model.active_draw,
                'plan_issued_at':result.get('shadow',{}).get('issued_at'),'shadow_version':1})
        if record is not None:
            append('plans-'+day.isoformat()+'.jsonl',record)
            summary={'as_of':stamp(at),'learning':self.shadow.summary(),'days':deepcopy(self.shadow.days)}
            # Atomic replacement; a failed summary write never destroys its previous copy.
            destination=self.shadow_archive/('summary-'+day.isoformat()+'.json')
            temporary=destination.with_suffix('.tmp')
            temporary.write_text(json.dumps(summary,allow_nan=False,separators=(',',':')),encoding='utf-8')
            temporary.replace(destination)
        for path in self.shadow_archive.iterdir():
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r'(?:samples|plans)-\d{4}-\d{2}-\d{2}\.jsonl|summary-\d{4}-\d{2}-\d{2}\.json',path.name):continue
            try:age=(day-datetime.strptime(path.name.split('-',1)[1].rsplit('.',1)[0],'%Y-%m-%d').date()).days
            except ValueError:continue
            if age>45:path.unlink()

    def _archive_sync(self,sample,result):
        # Dedicated model-owned archive, bounded retention; never touches Recorder.
        self.archive.mkdir(parents=True,exist_ok=True)
        day=datetime.fromtimestamp(sample.at,timezone.utc).date()
        target=self.archive/(day.isoformat()+'.jsonl')
        record={'sample':{key:getattr(sample,key) for key in sample.__dataclass_fields__},
                'soc':result['soc'],'lower':result['lower'],'upper':result['upper'],'phase':result['phase'],
                'eta':{'target_c':result.get('eta_target_c'),
                       'full':result.get('full_heat_remaining'),'minimum':result.get('minimum_heat_remaining')}}
        with target.open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,allow_nan=False,separators=(',',':'))+'\n')
        # Only our dated files are eligible; symlinks and unrelated files are ignored.
        for path in self.archive.iterdir():
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.jsonl',path.name):continue
            try:age=(day-datetime.strptime(path.stem,'%Y-%m-%d').date()).days
            except ValueError:continue
            if age>45:path.unlink()

    async def _stop_event(self,event):
        self._stop_unsub=None
        await self.async_shutdown()

    async def async_shutdown(self):
        if self._closed:return
        self._closed=True
        if self._stop_unsub is not None:self._stop_unsub();self._stop_unsub=None
        for unsub in self._unsub:unsub()
        self._unsub=[]
        if self._task is not None and not self._task.done():await self._task
        if self._cached_dump is not None:
            try:await self.store.async_save(self._cached_dump)
            except Exception:_LOGGER.warning('TUV: závěrečné uložení modelu se nezdařilo',exc_info=True)
        if self._cached_shadow_dump is not None:
            try:await self.shadow_store.async_save(self._cached_shadow_dump)
            except Exception:_LOGGER.warning('TUV shadow: závěrečné uložení se nezdařilo',exc_info=True)
        await super().async_shutdown()
