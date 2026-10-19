"""Editable local date; setting it never sends a command to the ventilation unit."""
from homeassistant.components.date import DateEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([FilterMaintenanceDate(entry.runtime_data._filter_runtime)])

class FilterMaintenanceDate(CoordinatorEntity,DateEntity):
    _attr_icon='mdi:calendar-check'
    _attr_entity_category=EntityCategory.CONFIG

    def __init__(self,runtime):
        super().__init__(runtime)
        self.entity_id='date.nilan_vp18_k_filter_last_maintenance'
        self._attr_unique_id=f'{runtime.main.entry.entry_id}-filter-last-maintenance'
        self._attr_name=f'{runtime.main.name} Filtr poslední údržba v HA'
        self._attr_device_info=runtime.main.device_info

    @property
    def native_value(self):return self.coordinator.maintenance

    @property
    def extra_state_attributes(self):return {'source':self.coordinator.data['source'],'io_error':self.coordinator.io_error}

    async def async_set_value(self,value):await self.coordinator.async_set_date(value)
