"""CTS600 panel replacement; local preparation for VP18 K."""
from homeassistant.const import Platform

DOMAIN = "nilan_cts600"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BUTTON, Platform.NUMBER,
             Platform.SELECT, Platform.SWITCH, Platform.WATER_HEATER, Platform.BINARY_SENSOR, Platform.DATE]
DEFAULT_T15 = None
DEFAULT_T15_BACKUP = None
DEFAULT_GATE = None
DEFAULT_MAX_AGE = 900
VERSION = "2026.11.4.0"
