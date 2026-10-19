"""Native HA controls, serialized and verified by the coordinator."""
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode, HVACAction, ClimateEntityFeature
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError
from .operating import air_action, PRESETS
import time


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CTS600Climate(entry.runtime_data)])


class CTS600Climate(CoordinatorEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
    _attr_fan_modes = ["1", "2", "3", "4"]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
                                | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.PRESET_MODE)
    _enable_turn_on_off_backwards_compatibility = False
    _modes = {"AUTO": HVACMode.AUTO, "HEAT": HVACMode.HEAT, "COOL": HVACMode.COOL, "OFF": HVACMode.OFF}

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = coordinator.name
        self._attr_unique_id = f"{coordinator.entry.entry_id}-climate"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self):
        return super().available and self.coordinator.t15_reading().valid

    @property
    def hvac_mode(self):
        return self._modes.get((self.coordinator.data or {}).get("mode"))

    @property
    def target_temperature(self):
        return (self.coordinator.data or {}).get("thermostat")

    @property
    def current_temperature(self):
        reading = self.coordinator.t15_reading()
        return reading.value if reading.valid else None

    @property
    def fan_mode(self):
        value = (self.coordinator.data or {}).get("flow")
        return str(value) if value in range(1, 5) else None

    @property
    def preset_modes(self):
        return list(PRESETS) if self.coordinator.menu.values.get('air_mode') in PRESETS.values() else []

    @property
    def preset_mode(self):
        value=self.coordinator.menu.values.get('air_mode')
        return next((name for name,raw in PRESETS.items() if raw==value),None)

    @property
    def hvac_action(self):
        at=self.coordinator.last_show_data_success
        if at is None or not 0<=time.time()-at<=300:return None
        value=air_action(self.coordinator.data or {})
        return HVACAction(value) if value is not None else None

    async def async_set_preset_mode(self,preset_mode):
        if preset_mode not in self.preset_modes:raise HomeAssistantError('Nepodporovaná předvolba výměny vzduchu')
        await self.coordinator.async_menu_command('setting',{'key':'air_mode','value':PRESETS[preset_mode]})

    async def async_set_temperature(self, **kwargs):
        value = kwargs.get("temperature")
        if value is None or not 5 <= float(value) <= 30 or float(value) != int(float(value)):
            raise HomeAssistantError("Požadovaná pokojová teplota musí být celé číslo 5–30 °C")
        await self.coordinator.async_command("temperature", int(value))

    async def async_set_fan_mode(self, fan_mode):
        if fan_mode not in self._attr_fan_modes:
            raise HomeAssistantError("Stupeň ventilace musí být 1–4")
        await self.coordinator.async_command("fan", int(fan_mode))

    async def async_set_hvac_mode(self, hvac_mode):
        inverse = {v: k for k, v in self._modes.items()}
        if hvac_mode not in inverse:
            raise HomeAssistantError("Nepodporovaný režim CTS600")
        await self.coordinator.async_command("mode", inverse[hvac_mode])

    async def async_turn_on(self):
        await self.coordinator.async_command("mode", "AUTO")

    async def async_turn_off(self):
        await self.coordinator.async_command("mode", "OFF")
