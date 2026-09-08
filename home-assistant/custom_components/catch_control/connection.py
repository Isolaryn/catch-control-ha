"""Use HA's shared Bluetooth manager, including connectable proxies."""
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from ._vendor.catch_control.ble import CatchClient


def client_for(hass, address):
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise ConnectionError('Catch is not visible through a connectable Bluetooth adapter')

    async def connector(device, disconnected_callback, timeout):
        return await establish_connection(
            BleakClientWithServiceCache, device, device.name or address,
            disconnected_callback=disconnected_callback,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(hass, address, connectable=True),
            timeout=timeout,
        )

    return CatchClient(device, timeout=10, connect_timeout=30, connector=connector)


async def probe(hass, address, password=''):
    async with client_for(hass, address) as client:
        if password:
            await client.authenticate(password=password)
        return client.identity


async def configure_websocket_server(hass, address, password, host, port, expected_serial):
    """Set and verify the outbound endpoint over an authenticated BLE session."""
    async with client_for(hass, address) as client:
        if str(client.identity.serial) != str(expected_serial):
            raise ValueError('A different Catch responded at the configured Bluetooth address')
        plan = await client.plan_websocket_server(host, port, password=password)
        result = await client.apply_websocket_server(plan, password=password)
        return plan.before, result
