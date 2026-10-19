"""Physical panel buttons exposed for supervised commissioning."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from .nilan_cts600 import Key
from homeassistant.util import dt as dt_util


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CTS600Button(entry.runtime_data, key) for key in
                        (Key.UP, Key.DOWN, Key.ENTER, Key.ESC, Key.ON, Key.OFF)])
    async_add_entities([CTS600ActionButton(entry.runtime_data,action) for action in
                       ('sync_clock','read_weekly','reset_filter','discover_service','read_profile','read_alarm_history')])
    async_add_entities([FilterMaintenanceButton(entry.runtime_data._filter_runtime)])


class FilterMaintenanceButton(CoordinatorEntity,ButtonEntity):
    _attr_entity_category=EntityCategory.CONFIG
    _attr_icon='mdi:calendar-check'

    def __init__(self,runtime):
        super().__init__(runtime)
        self.entity_id='button.nilan_vp18_k_filter_record_maintenance'
        self._attr_unique_id=f'{runtime.main.entry.entry_id}-filter-record-maintenance'
        self._attr_name=f'{runtime.main.name} Zapsat dnešní údržbu filtru v HA'
        self._attr_device_info=runtime.main.device_info

    async def async_press(self):await self.coordinator.async_set_date(dt_util.now().date())


class CTS600Button(CoordinatorEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = f"{coordinator.name} {key.name.lower()}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}-button-{key.name}"
        self._attr_device_info = coordinator.device_info

    async def async_press(self):
        await self.coordinator.async_command("button", self.key)

class CTS600ActionButton(CoordinatorEntity,ButtonEntity):
    _attr_entity_category=EntityCategory.CONFIG

    def __init__(self,coordinator,action):
        super().__init__(coordinator)
        self.action=action
        self._attr_name=f'{coordinator.name} {action}'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-action-{action}'
        self._attr_device_info=coordinator.device_info
        self._attr_entity_registry_enabled_default=True

    async def async_press(self):
        if self.action=='sync_clock':command,payload='clock',{'when':dt_util.now()}
        elif self.action=='reset_filter':command,payload='acknowledge_alarm',{'code':19}
        else:command,payload=self.action,{}
        await self.coordinator.async_menu_command(command,payload)
