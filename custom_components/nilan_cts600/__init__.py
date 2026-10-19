"""CTS600 TCP integration. No configuration entry is created before hardware."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import PLATFORMS
from .coordinator import CTS600Coordinator
from .services import register_services, remove_services_if_unloaded


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CTS600Coordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_restore_options()
        from .filter_maintenance import FilterMaintenance
        coordinator._filter_runtime=FilterMaintenance(hass,coordinator)
        await coordinator._filter_runtime.async_start()
        from .tuv_live_runtime import TUVLiveRuntime
        coordinator._tuv_live_runtime = TUVLiveRuntime(hass, coordinator)
        await coordinator._tuv_live_runtime.async_start()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        register_services(hass)
    except Exception:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        entry.runtime_data=None
        remove_services_if_unloaded(hass)
        return True
    return False
