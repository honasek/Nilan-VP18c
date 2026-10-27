from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .control_entity import CTS600SettingEntity
from .menu_catalog import SETTINGS
from .unit_power import power_state

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600Switch(entry.runtime_data,spec) for spec in SETTINGS if spec.kind=='switch'])
    async_add_entities([TUVLiveSwitch(entry.runtime_data._tuv_live_runtime)])
    async_add_entities([CTS600PowerSwitch(entry.runtime_data)])


class CTS600PowerSwitch(CoordinatorEntity, SwitchEntity):
    """Main unit ON/OFF, preserving the controller's selected operating mode."""
    _attr_icon = 'mdi:power'

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entity_id = 'switch.nilan_vp18_k_power'
        self._attr_name = f'{coordinator.name} Zapnout / vypnout'
        self._attr_unique_id = f'{coordinator.entry.entry_id}-power'
        self._attr_device_info = coordinator.device_info

    @property
    def available(self):
        return super().available and self.coordinator.t15_reading().valid

    @property
    def is_on(self):
        return power_state((self.coordinator.data or {}).get('mode'))

    @property
    def extra_state_attributes(self):
        return {'operating_mode': (self.coordinator.data or {}).get('mode'),
                'control_gate': self.coordinator.gate_entity}

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_command('power', True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_command('power', False)


class TUVLiveSwitch(CoordinatorEntity, SwitchEntity):
    """Explicit opt-in; off after every restart, with pending target recovery."""
    _attr_name = 'Nilan TUV live delay control'
    _attr_icon = 'mdi:water-boiler-auto'

    def __init__(self, runtime):
        super().__init__(runtime)
        self.entity_id = 'switch.nilan_tuv_live_control'
        self._attr_unique_id = f'{runtime.main.entry.entry_id}-tuv-live-control'
        self._attr_device_info = runtime.main.device_info

    @property
    def is_on(self):
        return self.coordinator.control.enabled

    @property
    def extra_state_attributes(self):
        return self.coordinator.data

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_enable(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_enable(False)

class CTS600Switch(CTS600SettingEntity,SwitchEntity):
    @property
    def is_on(self):return self.controller_value=='ON'

    async def async_turn_on(self,**kwargs):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':'ON'})

    async def async_turn_off(self,**kwargs):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':'OFF'})
