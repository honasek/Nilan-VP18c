from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .panel_indicators import indicators

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600Indicator(entry.runtime_data,key) for key in indicators({})])

class CTS600Indicator(CoordinatorEntity,BinarySensorEntity):
    def __init__(self,coordinator,key):
        super().__init__(coordinator)
        self.key=key
        self._attr_name=f'{coordinator.name} {key}'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-indicator-{key}'
        self._attr_device_info=coordinator.device_info

    @property
    def available(self):return super().available and indicators(self.coordinator.data or {})[self.key] is not None

    @property
    def is_on(self):return indicators(self.coordinator.data or {})[self.key]
