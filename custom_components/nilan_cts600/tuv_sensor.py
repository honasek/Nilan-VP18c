"""Model outputs; high-frequency internal diagnostics are not recorded."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from .tuv_runtime import TUVRuntime
from datetime import datetime


async def async_model_entities(hass,main):
    runtime=TUVRuntime(hass,main)
    main._tuv_runtime=runtime
    await runtime.async_start()
    return [TUVSensor(runtime,key,name) for key,name in (
        ('soc','Nilan TUV zásoba'),('soc_minimum','Nilan TUV zásoba minimum'),
        ('soc_maximum','Nilan TUV zásoba maximum'),('model','Nilan TUV model'),
        ('full_heat_remaining','Nilan TUV do úplného ohřevu'),
        ('minimum_heat_remaining','Nilan TUV do minimálního ohřevu'),
        ('shadow','Nilan TUV shadow'),
        ('shadow_latest_start','Nilan TUV shadow nejpozdější start'),
        ('shadow_finish','Nilan TUV shadow dokončení'),
        ('shadow_reserve','Nilan TUV shadow nejnižší rezerva'))]


class TUVSensor(CoordinatorEntity,SensorEntity):
    def __init__(self,runtime,key,name):
        super().__init__(runtime)
        self.key=key
        self.entity_id=f'sensor.nilan_tuv_{key}'
        self._attr_name=name
        self._attr_unique_id=f'{runtime.main.entry.entry_id}-tuv-observer-{key}'
        self._attr_device_info=runtime.main.device_info
        self._attr_icon='mdi:water-thermometer' if key!='model' else 'mdi:chart-bell-curve-cumulative'
        if key in ('model','shadow'):self._attr_entity_category=EntityCategory.DIAGNOSTIC
        elif key in ('shadow_latest_start','shadow_finish'):
            self._attr_device_class=SensorDeviceClass.TIMESTAMP
            self._attr_icon='mdi:clock-outline'
        elif key=='shadow_reserve':
            self._attr_native_unit_of_measurement='%'
            self._attr_icon='mdi:water-percent'
            self._attr_suggested_display_precision=0
        elif key in ('full_heat_remaining','minimum_heat_remaining'):
            self._attr_native_unit_of_measurement='min'
            self._attr_device_class=SensorDeviceClass.DURATION
            self._attr_icon='mdi:timer-sand'
            self._attr_suggested_display_precision=0
        else:
            self._attr_native_unit_of_measurement='%'
            self._attr_state_class=SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision=0

    @property
    def available(self):
        if self.key=='shadow':return True
        if self.key.startswith('shadow_'):
            return bool((self.coordinator.data or {}).get('valid')) and self.native_value is not None
        if self.key in ('full_heat_remaining','minimum_heat_remaining'):
            data=self.coordinator.data or {}
            return bool(data.get('valid')) and data.get(self.key,{}).get('status') in ('forecast','reached','reached_hysteresis')
        if self.key in ('soc','soc_minimum','soc_maximum'):
            return bool((self.coordinator.data or {}).get('valid')) and (self.coordinator.data or {}).get('stock_reference_valid',True)
        return True if self.key=='model' else bool((self.coordinator.data or {}).get('valid'))

    @property
    def native_value(self):
        data=self.coordinator.data or {}
        shadow=data.get('shadow',{})
        if self.key=='shadow':return shadow.get('status','waiting') if data.get('valid') else 'waiting'
        if self.key in ('shadow_latest_start','shadow_finish'):
            value=shadow.get('latest_start' if self.key=='shadow_latest_start' else 'finish_at')
            return datetime.fromisoformat(value) if isinstance(value,str) else None
        if self.key=='shadow_reserve':return shadow.get('expected_reserve_pct')
        if self.key=='model':return data.get('quality','waiting')
        if self.key in ('full_heat_remaining','minimum_heat_remaining'):return data.get(self.key,{}).get('minutes')
        return data.get({'soc':'soc','soc_minimum':'lower','soc_maximum':'upper'}[self.key])

    @property
    def extra_state_attributes(self):
        if self.key=='shadow':
            data=self.coordinator.data or {}
            return {**data.get('shadow',{}),'valid':bool(data.get('valid')),'shadow_only':True,'validated_for_control':False}
        if self.key.startswith('shadow_'):
            return {'shadow_only':True,'validated_for_control':False,'assumption':'Orientační simulace; skutečný ohřev řídí dosavadní regulace'}
        if self.key in ('full_heat_remaining','minimum_heat_remaining'):
            data=self.coordinator.data or {}
            return {**{key:value for key,value in data.get(self.key,{}).items() if key!='minutes'},
                    'target_temperature':data.get('eta_target_c'),'assumption':data.get('eta_assumption')}
        if self.key!='model':return None
        data=self.coordinator.data or {}
        return {key:value for key,value in data.items() if key not in ('soc','lower','upper','layer_medians','shadow')}
