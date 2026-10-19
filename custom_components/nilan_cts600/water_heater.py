"""DHW target is T12; it does not switch off the whole ventilation unit."""
from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from .control_entity import CTS600SettingEntity
from .menu_catalog import BY_KEY
from .menu_catalog import canonical
from .panel_indicators import indicators

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([CTS600WaterHeater(entry.runtime_data,BY_KEY['dhw_temperature']),
                       CTS600SeparateHeater(entry.runtime_data,'compressor'),
                       CTS600SeparateHeater(entry.runtime_data,'auxiliary')])

class CTS600WaterHeater(CTS600SettingEntity,WaterHeaterEntity):
    _attr_entity_category=None
    _attr_temperature_unit=UnitOfTemperature.CELSIUS
    _attr_min_temp=5
    _attr_max_temp=60
    _attr_target_temperature_step=1
    _attr_supported_features=WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_operation_list=['off','ready','heating']

    def __init__(self,coordinator,spec):
        super().__init__(coordinator,spec)
        self._attr_name=f'{coordinator.name} TUV'

    @property
    def target_temperature(self):
        value=self.controller_value
        return value if isinstance(value,int) else None

    @property
    def current_temperature(self):
        return (self.coordinator.data or {}).get('T11')

    @property
    def current_operation(self):
        data=self.coordinator.data or {}
        if self.controller_value=='OFF' or data.get('mode')=='OFF':return 'off'
        if indicators(data)['dhw_aux_running'] is True or canonical(data.get('status','')) in ('HOTWATER','DHW','WATERHEAT','VARMVAND','OHREVVODY'):
            return 'heating'
        return 'ready'

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes,'current_probe':'T11','target_probe':'T12',
                'tank_bottom_temperature':(self.coordinator.data or {}).get('T12')}

    async def async_set_temperature(self,**kwargs):
        await self.coordinator.async_menu_command('setting',{'key':'dhw_temperature','value':kwargs.get('temperature')})


class CTS600SeparateHeater(CTS600SettingEntity,WaterHeaterEntity):
    """Expose each existing target with its actual probe; no fabricated on/off setpoint."""
    _attr_entity_category=None
    _attr_temperature_unit=UnitOfTemperature.CELSIUS
    _attr_min_temp=5
    _attr_target_temperature_step=1
    _attr_supported_features=WaterHeaterEntityFeature.TARGET_TEMPERATURE

    def __init__(self,coordinator,kind):
        self.kind=kind
        key='dhw_temperature' if kind=='compressor' else 'dhw_aux_temperature'
        super().__init__(coordinator,BY_KEY[key])
        self.probe='T12' if kind=='compressor' else 'T11'
        slug='kompresor' if kind=='compressor' else 'spirala'
        self.entity_id=f'water_heater.nilan_vp18_k_tuv_{slug}'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-water-heater-{kind}'
        self._attr_name=f'{coordinator.name} TUV {"kompresor" if kind=="compressor" else "spirála"}'
        self._attr_max_temp=self.spec.maximum
        self._attr_operation_list=['off','heat_pump' if kind=='compressor' else 'electric']
        self._attr_icon='mdi:heat-pump' if kind=='compressor' else 'mdi:water-boiler'

    @property
    def current_temperature(self):return (self.coordinator.data or {}).get(self.probe)

    @property
    def target_temperature(self):return self.controller_value if type(self.controller_value) is int else None

    @property
    def current_operation(self):
        if self.controller_value=='OFF' or (self.coordinator.data or {}).get('mode')=='OFF':return 'off'
        return 'heat_pump' if self.kind=='compressor' else 'electric'

    @property
    def extra_state_attributes(self):
        data=self.coordinator.data or {};flags=indicators(data)
        if self.kind=='auxiliary':active=flags['dhw_aux_running']
        elif flags['compressor_running'] is False:active=False
        elif flags['compressor_running'] is None:active=None
        else:
            status=canonical(data.get('status',''))
            active=True if status in ('HOTWATER','DHW','WATERHEAT','VARMVAND','OHREVVODY') else (False if status in ('OFF','HEATING','COOLING','VENTILATION') else None)
        return {**super().extra_state_attributes,'current_probe':self.probe,'target_probe':self.probe,
                'heating_active':active,'controller_status':data.get('status'),
                'off_control_entity':'select.nilan_vp18_k_dhw_aux_temperature' if self.kind=='auxiliary' else None}

    async def async_set_temperature(self,**kwargs):
        value=kwargs.get('temperature')
        try:self.spec.validate(value)
        except (ValueError,TypeError) as exc:raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_menu_command('setting',{'key':self.spec.key,'value':value})
