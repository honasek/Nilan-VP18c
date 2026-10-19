from homeassistant.components.switch import SwitchEntity
from .control_entity import CTS600SettingEntity
from .menu_catalog import SETTINGS

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600Switch(entry.runtime_data,spec) for spec in SETTINGS if spec.kind=='switch'])

class CTS600Switch(CTS600SettingEntity,SwitchEntity):
    @property
    def is_on(self):return self.controller_value=='ON'

    async def async_turn_on(self,**kwargs):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':'ON'})

    async def async_turn_off(self,**kwargs):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':'OFF'})
