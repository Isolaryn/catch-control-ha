"""UI setup and options for Bluetooth and local Wi-Fi operation."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers import selector

from ._vendor.catch_control.ble import advertised_model
from ._vendor.catch_control.configuration import AuthenticationError
from ._vendor.catch_control.network import validate_websocket_endpoint
from .connection import probe
from .const import (
    CONF_ADDRESS, CONF_BIND_HOST, CONF_ENDPOINT_CONFIGURED, CONF_INTERVAL,
    CONF_PASSWORD, CONF_SERVER_HOST, CONF_SERVER_PORT, CONF_TRANSPORT,
    DEFAULT_BIND_HOST, DEFAULT_INTERVAL, DEFAULT_SERVER_PORT, DOMAIN,
    TRANSPORT_BLUETOOTH, TRANSPORT_WIFI,
)

PASSWORD_SELECTOR = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
TRANSPORT_SELECTOR = selector.SelectSelector(selector.SelectSelectorConfig(
    options=[
        selector.SelectOptionDict(value=TRANSPORT_BLUETOOTH, label='Bluetooth'),
        selector.SelectOptionDict(value=TRANSPORT_WIFI, label='Wi-Fi WebSocket server'),
    ],
    mode=selector.SelectSelectorMode.LIST,
))


class CatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    _address = ''

    def _address_field(self):
        discovered = {
            info.address: info.name
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
            if advertised_model(info) == 10004
        }
        if not discovered:
            return str
        return selector.SelectSelector(selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=address, label=f'{name} ({address})')
                for address, name in discovered.items()
            ],
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        ))

    async def async_step_bluetooth(self, discovery_info):
        if advertised_model(discovery_info) != 10004:
            return self.async_abort(reason='not_supported')
        self._address = discovery_info.address
        self._async_abort_entries_match({CONF_ADDRESS: self._address})
        self.context['title_placeholders'] = {'name': discovery_info.name}
        return await self.async_step_bluetooth_device()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            if user_input[CONF_TRANSPORT] == TRANSPORT_WIFI:
                return await self.async_step_wifi()
            return await self.async_step_bluetooth_device()
        return self.async_show_form(step_id='user', data_schema=vol.Schema({
            vol.Required(CONF_TRANSPORT, default=TRANSPORT_BLUETOOTH): TRANSPORT_SELECTOR,
        }))

    async def _create(self, address, password, transport, extra):
        try:
            identity = await probe(self.hass, address, password)
        except AuthenticationError:
            return None, 'invalid_auth'
        except Exception:
            return None, 'cannot_connect'
        await self.async_set_unique_id(f'{identity.model}-{identity.serial}')
        self._abort_if_unique_id_configured(updates={CONF_ADDRESS: address})
        return self.async_create_entry(
            title=f'CATCH Control {identity.serial}',
            data={
                CONF_ADDRESS: address,
                CONF_TRANSPORT: transport,
                'serial': identity.serial,
                'firmware': identity.firmware,
                **extra,
            },
            options={CONF_PASSWORD: password, CONF_INTERVAL: DEFAULT_INTERVAL},
        ), None

    async def async_step_bluetooth_device(self, user_input=None):
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            password = user_input.get(CONF_PASSWORD, '')
            result, error = await self._create(address, password, TRANSPORT_BLUETOOTH, {})
            if result is not None:
                return result
            errors['base'] = error
            self._address = address
        return self.async_show_form(step_id='bluetooth_device', errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_ADDRESS, default=self._address): self._address_field(),
            vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
        }))

    async def async_step_wifi(self, user_input=None):
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            password = user_input[CONF_PASSWORD]
            try:
                host, _ = validate_websocket_endpoint(
                    user_input[CONF_SERVER_HOST], user_input[CONF_SERVER_PORT]
                )
            except ValueError:
                errors[CONF_SERVER_HOST] = 'invalid_host'
                host = user_input[CONF_SERVER_HOST].strip()
            bind = user_input[CONF_BIND_HOST].strip()
            if not errors:
                result, error = await self._create(address, password, TRANSPORT_WIFI, {
                    CONF_SERVER_HOST: host,
                    CONF_SERVER_PORT: user_input[CONF_SERVER_PORT],
                    CONF_BIND_HOST: bind,
                    CONF_ENDPOINT_CONFIGURED: False,
                })
                if result is not None:
                    return result
                errors['base'] = error
            self._address = address
        return self.async_show_form(step_id='wifi', errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_ADDRESS, default=self._address): self._address_field(),
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Required(CONF_SERVER_HOST): str,
            vol.Required(CONF_SERVER_PORT, default=DEFAULT_SERVER_PORT): vol.All(vol.Coerce(int), vol.Range(min=1024, max=32767)),
            vol.Required(CONF_BIND_HOST, default=DEFAULT_BIND_HOST): str,
        }))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CatchOptionsFlow()


class CatchOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        transport = entry.data.get(CONF_TRANSPORT, TRANSPORT_BLUETOOTH)
        errors = {}
        if user_input is not None:
            previous = entry.options.get(CONF_PASSWORD, '')
            password = '' if user_input.get('clear_password') else user_input.get(CONF_PASSWORD) or previous
            if transport == TRANSPORT_BLUETOOTH:
                try:
                    identity = await probe(self.hass, entry.data[CONF_ADDRESS], password)
                    if identity.serial != entry.data['serial']:
                        raise ValueError('Device identity changed')
                except AuthenticationError:
                    errors['base'] = 'invalid_auth'
                except Exception:
                    errors['base'] = 'cannot_connect'
            else:
                port = user_input[CONF_SERVER_PORT]
                try:
                    host, _ = validate_websocket_endpoint(user_input[CONF_SERVER_HOST], port)
                except ValueError:
                    errors[CONF_SERVER_HOST] = 'invalid_host'
                    host = user_input[CONF_SERVER_HOST].strip()
                bind = user_input[CONF_BIND_HOST].strip()
                endpoint_changed = (host, port) != (
                    entry.options.get(CONF_SERVER_HOST, entry.data[CONF_SERVER_HOST]),
                    entry.options.get(CONF_SERVER_PORT, entry.data[CONF_SERVER_PORT]),
                )
                if endpoint_changed and not errors:
                    if not password:
                        errors['base'] = 'password_required'
                    else:
                        try:
                            identity = await probe(self.hass, entry.data[CONF_ADDRESS], password)
                            if identity.serial != entry.data['serial']:
                                raise ValueError('Device identity changed')
                        except AuthenticationError:
                            errors['base'] = 'invalid_auth'
                        except Exception:
                            errors['base'] = 'cannot_connect'
            if not errors:
                options = {
                    CONF_PASSWORD: password,
                    CONF_INTERVAL: user_input[CONF_INTERVAL],
                }
                if transport == TRANSPORT_WIFI:
                    options.update({
                        CONF_SERVER_HOST: host,
                        CONF_SERVER_PORT: port,
                        CONF_BIND_HOST: bind,
                    })
                return self.async_create_entry(title='', data=options)
        schema = {
            vol.Required(CONF_INTERVAL, default=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
            vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Optional('clear_password', default=False): bool,
        }
        if transport == TRANSPORT_WIFI:
            schema.update({
                vol.Required(CONF_SERVER_HOST, default=entry.options.get(CONF_SERVER_HOST, entry.data[CONF_SERVER_HOST])): str,
                vol.Required(CONF_SERVER_PORT, default=entry.options.get(CONF_SERVER_PORT, entry.data[CONF_SERVER_PORT])): vol.All(vol.Coerce(int), vol.Range(min=1024, max=32767)),
                vol.Required(CONF_BIND_HOST, default=entry.options.get(CONF_BIND_HOST, entry.data.get(CONF_BIND_HOST, DEFAULT_BIND_HOST))): str,
            })
        return self.async_show_form(step_id='init', errors=errors, data_schema=vol.Schema(schema))
