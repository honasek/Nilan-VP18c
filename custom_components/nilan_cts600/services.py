"""Typed advanced operations for clock, 42 program slots and menu choices."""
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .menu_catalog import BY_KEY
from .weekly_program import DAYS

SERVICES=('set_weekly_slot','set_clock','discover_menu_options','acknowledge_alarm')

def register_services(hass):
    if hass.services.has_service(DOMAIN,'set_weekly_slot'):return

    def coordinator(call):
        entries=hass.config_entries.async_entries(DOMAIN)
        if call.data.get('entry_id'):entries=[e for e in entries if e.entry_id==call.data['entry_id']]
        loaded=[e for e in entries if getattr(e,'runtime_data',None) is not None]
        if len(loaded)!=1:raise HomeAssistantError('Vyberte právě jednu načtenou integraci Nilan pomocí entry_id')
        return loaded[0].runtime_data

    async def weekly(call):
        payload={key:call.data[key] for key in ('day','slot','clock','fan','temperature')}
        await coordinator(call).async_menu_command('weekly_slot',payload)

    async def clock(call):
        when=dt_util.parse_datetime(call.data['datetime']) if call.data.get('datetime') else dt_util.now()
        if when is None or when.tzinfo is None:raise HomeAssistantError('Zadejte datum a čas s časovou zónou')
        await coordinator(call).async_menu_command('clock',{'when':dt_util.as_local(when)})

    async def discover(call):
        await coordinator(call).async_menu_command('discover_choices',{'key':call.data['setting']})

    async def alarm(call):
        await coordinator(call).async_menu_command('acknowledge_alarm',{'code':call.data['code']})

    common={vol.Optional('entry_id'):str}
    specs=(('set_weekly_slot',weekly,{vol.Required('day'):vol.In(tuple(DAYS)),vol.Required('slot'):vol.All(int,vol.Range(min=1,max=6)),
            vol.Required('clock'):str,vol.Required('fan'):vol.All(int,vol.Range(min=0,max=4)),vol.Required('temperature'):vol.All(int,vol.Range(min=5,max=30))}),
           ('set_clock',clock,{vol.Optional('datetime'):str}),
           ('discover_menu_options',discover,{vol.Required('setting'):vol.In(tuple(BY_KEY))}),
           ('acknowledge_alarm',alarm,{vol.Required('code'):vol.All(int,vol.Range(min=1,max=99))}))
    for name,handler,fields in specs:hass.services.async_register(DOMAIN,name,handler,schema=vol.Schema({**common,**fields}))

def remove_services_if_unloaded(hass):
    if any(getattr(e,'runtime_data',None) is not None for e in hass.config_entries.async_entries(DOMAIN)):return
    for name in SERVICES:hass.services.async_remove(DOMAIN,name)
