"""Discovered display values and explicit connectivity diagnostics."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import UnitOfTemperature


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    keys=list(dict.fromkeys([*coordinator.data,'T1','T2','T4','T5','T6','T10','T11','T12','T15','inletFlow','exhaustFlow','humidity','co2','bypass']))
    entities = [CTS600Sensor(coordinator, key) for key in keys]
    entities += [CTS600Health(coordinator, "connection"), CTS600Health(coordinator, "diagnostics")]
    entities += [CTS600MenuSensor(coordinator,key) for key in ('alarms','weekly_schedule','menu_capabilities','controller_clock','alarm_history')]
    from .tuv_sensor import async_model_entities
    entities += await async_model_entities(hass, coordinator)
    entities += [FilterMaintenanceSensor(coordinator._filter_runtime,key) for key in ('days_since','days_remaining')]
    async_add_entities(entities)


class FilterMaintenanceSensor(CoordinatorEntity,SensorEntity):
    _attr_device_class=SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement='d'
    _attr_suggested_display_precision=0
    _attr_icon='mdi:air-filter'

    def __init__(self,runtime,key):
        super().__init__(runtime)
        self.key=key
        self.entity_id=f'sensor.nilan_vp18_k_filter_{key}'
        self._attr_unique_id=f'{runtime.main.entry.entry_id}-filter-{key}'
        self._attr_name=f'{runtime.main.name} Filtr {"dny od údržby" if key=="days_since" else "zbývající dny"}'
        self._attr_device_info=runtime.main.device_info

    @property
    def available(self):return self.coordinator.data.get(self.key) is not None

    @property
    def native_value(self):return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        return {k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in self.coordinator.data.items() if k not in ('days_since','days_remaining')}


class CTS600Sensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = f"{coordinator.name} {key}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}-sensor-{key}"
        self._attr_device_info = coordinator.device_info
        if key in ('T1','T2','T4','T5','T6','T10','T11','T12','T15') or coordinator.cts600.metaData.get(key, {}).get("kind") == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        if key in ('humidity','co2'):
            self._attr_device_class=SensorDeviceClass.HUMIDITY if key=='humidity' else SensorDeviceClass.CO2
            self._attr_native_unit_of_measurement='%' if key=='humidity' else 'ppm'
            self._attr_state_class=SensorStateClass.MEASUREMENT
        if key in ("display", "program", "led"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self):
        return super().available and self.key in (self.coordinator.data or {})

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.key)


class CTS600Health(CoordinatorEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = f"{coordinator.name} {key}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}-{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        if not self.coordinator.t15_reading().valid:
            return "t15_invalid"
        return "connected" if self.coordinator.last_update_success else "disconnected"

    @property
    def extra_state_attributes(self):
        if self.key == "connection":
            return None
        return {"last_success": self.coordinator.last_success,
                "last_error": self.coordinator.last_error,
                "crc_failures": self.coordinator.cts600.crc_fails,
                "t15_reason": self.coordinator.t15_reading().reason,
                "source_entity": self.coordinator.selected_sensor_entity_id,
                "primary_entity": self.coordinator.sensor_entity_id,
                "backup_entity": self.coordinator.backup_sensor_entity_id,
                "last_command": self.coordinator.last_command,
                "menu_errors":self.coordinator.menu_errors,
                "vp18_k_hardware_verified": False}

class CTS600MenuSensor(CoordinatorEntity,SensorEntity):
    _attr_entity_category=EntityCategory.DIAGNOSTIC

    def __init__(self,coordinator,key):
        super().__init__(coordinator)
        self.key=key
        self._attr_name=f'{coordinator.name} {key}'
        self._attr_unique_id=f'{coordinator.entry.entry_id}-menu-{key}'
        self._attr_device_info=coordinator.device_info

    @property
    def available(self):
        if not super().available:return False
        if self.key=='alarms':return self.coordinator.alarms is not None
        if self.key=='weekly_schedule':return bool(self.coordinator.menu.weekly_slots)
        if self.key=='controller_clock':return self.coordinator.menu.clock is not None
        if self.key=='alarm_history':return self.coordinator.menu.alarm_history is not None
        return True

    @property
    def native_value(self):
        if self.key=='alarms':return len([code for code in (self.coordinator.alarms or {}) if code!=0])
        if self.key=='weekly_schedule':return len(self.coordinator.menu.weekly_slots)
        if self.key=='controller_clock':return self.coordinator.menu.clock
        if self.key=='alarm_history':return 'Načteno' if self.coordinator.menu.alarm_history is not None else None
        return len(self.coordinator.menu.values)

    @property
    def extra_state_attributes(self):
        if self.key=='alarms':return {'alarms':self.coordinator.alarms}
        if self.key=='weekly_schedule':return {'slots':self.coordinator.menu.weekly_slots}
        if self.key=='controller_clock':return {'time_basis':'Místní čas řídicí jednotky; ne čas HA'}
        if self.key=='alarm_history':return {'records':self.coordinator.menu.alarm_history}
        return {'supported_controls':sorted(self.coordinator.menu.values),
                'discovered_options':self.coordinator.menu.choices,
                'root_menus':self.coordinator.menu.root_menus,'raw_menus':self.coordinator.menu.raw_menus,
                'panel_language':self.coordinator.menu.values.get('panel_language'),
                'unavailable_menus':self.coordinator.menu_errors,'vp18_k_hardware_verified':False}
