"""Local maintenance evidence, deliberately independent of controller alarm reset."""
from datetime import date
import asyncio
import logging
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import HomeAssistantError

_LOGGER=logging.getLogger(__name__)

def days_since(maintenance,today):
    if type(maintenance) is not date or maintenance>today:return None
    return (today-maintenance).days

def interval_days(raw):
    if type(raw) is str and raw in ('30','90','180','360'):return int(raw)
    if type(raw) is int and raw in (30,90,180,360):return raw
    return None

class FilterMaintenance(DataUpdateCoordinator):
    def __init__(self,hass,main):
        super().__init__(hass,_LOGGER,name=f'{main.name} evidence filtru',config_entry=main.entry,update_interval=None)
        self.main=main
        self.store=Store(hass,1,f'nilan_cts600.{main.entry.entry_id}.filter_maintenance')
        self.maintenance=None
        self.io_error=None
        self._lock=asyncio.Lock()
        self._unsubs=[]

    async def async_start(self):
        try:
            value=await self.store.async_load()
            if value is not None:
                if not isinstance(value,dict) or not isinstance(value.get('last_maintenance'),str):raise ValueError('Invalid maintenance record')
                parsed=date.fromisoformat(value['last_maintenance'])
                if parsed>dt_util.now().date():raise ValueError('Future maintenance record')
                self.maintenance=parsed
        except Exception:
            self.io_error='Uložené datum údržby se nepodařilo načíst'
            _LOGGER.warning('Nilan: evidence údržby filtru není dostupná',exc_info=True)
        self._unsubs.append(self.main.async_add_listener(self.refresh))
        self._unsubs.append(async_track_time_change(self.hass,self._midnight,hour=0,minute=0,second=0))
        self.refresh()

    @callback
    def _midnight(self,now):self.refresh()

    @callback
    def refresh(self):
        elapsed=days_since(self.maintenance,dt_util.now().date())
        interval=interval_days(self.main.menu.values.get('filter_interval')) if self.main.last_update_success else None
        remaining=None if elapsed is None or interval is None else max(0,interval-elapsed)
        self.async_set_updated_data({'last_maintenance':self.maintenance,'days_since':elapsed,'days_remaining':remaining,
            'interval_days':interval,'overdue_days':max(0,elapsed-interval) if elapsed is not None and interval is not None else None,
            'source':'Evidence údržby v Home Assistantu; nejde o počítadlo jednotky','io_error':self.io_error})

    async def async_set_date(self,value):
        if type(value) is not date or value>dt_util.now().date():raise HomeAssistantError('Datum údržby musí být dnešní nebo minulé datum')
        async with self._lock:
            try:await self.store.async_save({'last_maintenance':value.isoformat()})
            except Exception as exc:
                self.io_error='Datum údržby se nepodařilo uložit';self.refresh()
                raise HomeAssistantError(self.io_error) from exc
            self.maintenance=value;self.io_error=None;self.refresh()

    async def async_shutdown(self):
        for unsub in self._unsubs:unsub()
        self._unsubs=[]
        await super().async_shutdown()
