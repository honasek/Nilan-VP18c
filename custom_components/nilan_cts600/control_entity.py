"""Shared availability/identity: a missing menu is never an optimistic control."""
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

class CTS600SettingEntity(CoordinatorEntity):
    _attr_entity_category=EntityCategory.CONFIG

    def __init__(self,coordinator,spec):
        super().__init__(coordinator)
        self.spec=spec
        self._attr_name=f'{coordinator.name} {spec.key}'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-setting-{spec.key}'
        self._attr_device_info=coordinator.device_info
        self._attr_entity_registry_enabled_default=True

    @property
    def available(self):
        return super().available and self.coordinator.t15_reading().valid and self.spec.key in self.coordinator.menu.values

    @property
    def extra_state_attributes(self):
        return {'description':self.spec.title,'manual_page':self.spec.page,
                'service_unlock_required':self.spec.protected,
                'vp18_k_hardware_verified':False}

    @property
    def controller_value(self):
        return self.coordinator.menu.values.get(self.spec.key)
