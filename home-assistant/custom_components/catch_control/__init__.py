"""Local CATCH Control Bluetooth integration."""
from homeassistant.const import Platform
from .coordinator import CatchCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.TIME]


async def async_setup_entry(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
