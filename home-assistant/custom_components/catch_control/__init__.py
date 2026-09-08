"""Local CATCH Control integration over Bluetooth or device-initiated Wi-Fi."""
import asyncio
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .connection import configure_websocket_server
from .const import (
    CONF_ADDRESS, CONF_BIND_HOST, CONF_ENDPOINT_CONFIGURED, CONF_PASSWORD,
    CONF_SERVER_HOST, CONF_SERVER_PORT, CONF_TRANSPORT, DEFAULT_BIND_HOST,
    DEFAULT_SERVER_PORT, TRANSPORT_BLUETOOTH, TRANSPORT_WIFI,
)
from .coordinator import CatchCoordinator
from .wifi_server import CatchWifiServer

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.TIME]


async def async_setup_entry(hass, entry):
    transport = entry.data.get(CONF_TRANSPORT, TRANSPORT_BLUETOOTH)
    server = None
    if transport == TRANSPORT_WIFI:
        server_host = entry.options.get(CONF_SERVER_HOST, entry.data[CONF_SERVER_HOST])
        server_port = entry.options.get(CONF_SERVER_PORT, entry.data.get(CONF_SERVER_PORT, DEFAULT_SERVER_PORT))
        bind_host = entry.options.get(CONF_BIND_HOST, entry.data.get(CONF_BIND_HOST, DEFAULT_BIND_HOST))
        server = CatchWifiServer(
            hass,
            bind_host,
            server_port,
        )
        try:
            await server.async_start()
            endpoint_changed = (server_host, server_port) != (
                entry.data.get(CONF_SERVER_HOST), entry.data.get(CONF_SERVER_PORT)
            )
            if not entry.data.get(CONF_ENDPOINT_CONFIGURED) or endpoint_changed:
                password = entry.options.get(CONF_PASSWORD, entry.data.get(CONF_PASSWORD, ''))
                if not password:
                    raise ValueError('The device password is required for initial Wi-Fi setup')
                before, _result = await configure_websocket_server(
                    hass,
                    entry.data[CONF_ADDRESS],
                    password,
                    server_host,
                    server_port,
                    entry.data['serial'],
                )
                updated = {
                    **entry.data,
                    CONF_ENDPOINT_CONFIGURED: True,
                    CONF_SERVER_HOST: server_host,
                    CONF_SERVER_PORT: server_port,
                    CONF_BIND_HOST: bind_host,
                    'previous_server1': entry.data.get('previous_server1', before.primary_host),
                    'previous_server1_port': entry.data.get('previous_server1_port', before.primary_port),
                    'previous_server2': entry.data.get('previous_server2', before.secondary_host),
                    'previous_server2_port': entry.data.get('previous_server2_port', before.secondary_port),
                }
                hass.config_entries.async_update_entry(entry, data=updated)
            elif bind_host != entry.data.get(CONF_BIND_HOST):
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_BIND_HOST: bind_host}
                )
        except asyncio.CancelledError:
            await server.async_stop()
            raise
        except Exception as exc:
            await server.async_stop()
            raise ConfigEntryNotReady(f'Local Wi-Fi server setup failed ({type(exc).__name__})') from None
    coordinator = CatchCoordinator(hass, entry, wifi_server=server)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if server is not None:
            await server.async_stop()
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data.wifi_server is not None:
        await entry.runtime_data.wifi_server.async_stop()
    return unloaded


async def async_update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass, entry):
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TRANSPORT: TRANSPORT_BLUETOOTH},
            version=2,
        )
    return True
