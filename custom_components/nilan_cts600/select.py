from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError
from .control_entity import CTS600SettingEntity
from .menu_catalog import SETTINGS, BY_KEY

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600Select(entry.runtime_data,spec) for spec in SETTINGS if spec.kind=='select'])
    async_add_entities([CTS600VentilationLevel(entry.runtime_data)])
    async_add_entities([CTS600AuxTemperature(entry.runtime_data,BY_KEY['dhw_aux_temperature'])])

class CTS600Select(CTS600SettingEntity,SelectEntity):
    @property
    def options(self):
        if self.spec.options is not None:return list(self.spec.options)
        choices=list(self.coordinator.menu.choices.get(self.spec.key,()))
        current=self.controller_value
        if current is not None and str(current) not in choices:choices.append(str(current))
        return choices

    @property
    def current_option(self):
        value=self.controller_value
        return str(value) if value is not None and str(value) in self.options else None

    async def async_select_option(self,option):
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':option})


class CTS600VentilationLevel(CoordinatorEntity,SelectEntity):
    """The main panel fan level, sharing the climate control's guarded command."""
    _attr_options=['1','2','3','4']
    _attr_icon='mdi:fan'

    def __init__(self,coordinator):
        super().__init__(coordinator)
        self._attr_name=f'{coordinator.name} ventilation_level'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-ventilation-level'
        self._attr_device_info=coordinator.device_info

    @property
    def available(self):
        value=(self.coordinator.data or {}).get('flow')
        return super().available and self.coordinator.t15_reading().valid and type(value) is int and 1<=value<=4

    @property
    def current_option(self):
        value=(self.coordinator.data or {}).get('flow')
        return str(value) if type(value) is int and 1<=value<=4 else None

    async def async_select_option(self,option):
        if option not in self._attr_options:raise HomeAssistantError('Stupeň ventilace musí být 1–4')
        await self.coordinator.async_command('fan',int(option))


class CTS600AuxTemperature(CTS600SettingEntity,SelectEntity):
    """Represent the observed '--' value explicitly, with no fabricated temperature."""
    _attr_entity_category=None
    _attr_icon='mdi:water-boiler'

    @property
    def options(self):
        return ['Vypnuto']+[f'{value} °C' for value in range(self.spec.minimum,self.spec.maximum+1)]

    @property
    def current_option(self):
        value=self.controller_value
        if value=='OFF':return 'Vypnuto'
        if type(value) is int and self.spec.minimum<=value<=self.spec.maximum:return f'{value} °C'
        return None

    async def async_select_option(self,option):
        if option not in self.options:raise HomeAssistantError('Neplatná požadovaná teplota spirály')
        value='OFF' if option=='Vypnuto' else int(option.split(' ')[0])
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':value})
