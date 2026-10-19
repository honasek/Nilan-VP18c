from homeassistant.components.number import NumberEntity, NumberMode
from .control_entity import CTS600SettingEntity
from .menu_catalog import SETTINGS

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600Number(entry.runtime_data,spec) for spec in SETTINGS if spec.kind=='number'])

class CTS600Number(CTS600SettingEntity,NumberEntity):
    _attr_native_step=1
    _attr_mode=NumberMode.BOX

    def __init__(self,coordinator,spec):
        super().__init__(coordinator,spec)
        self._attr_native_min_value=spec.minimum
        self._attr_native_max_value=spec.maximum
        self._attr_native_unit_of_measurement=spec.unit
        if spec.key in ('dhw_temperature','dhw_compressor_max'):self._attr_entity_category=None

    @property
    def native_value(self):
        value=self.controller_value
        return value if isinstance(value,(int,float)) else None

    async def async_set_native_value(self,value):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':value})
