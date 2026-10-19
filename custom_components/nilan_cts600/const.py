"""CTS600 panel replacement; local preparation for VP18 K."""
from homeassistant.const import Platform

DOMAIN = "nilan_cts600"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BUTTON, Platform.NUMBER,
             Platform.SELECT, Platform.SWITCH, Platform.WATER_HEATER, Platform.BINARY_SENSOR, Platform.DATE]
DEFAULT_T15 = "sensor.th_sensor_115e4b_lywsd03mmc_mjwsd05mmc_pvvx_bthome_tempc"
DEFAULT_T15_BACKUP = "sensor.th_sensor_9d80cf_lywsd03mmc_mjwsd05mmc_pvvx_bthome_tempc"
DEFAULT_GATE = "input_boolean.nilan_control_enabled"
DEFAULT_MAX_AGE = 900
VERSION = "2026.10.17.4"
