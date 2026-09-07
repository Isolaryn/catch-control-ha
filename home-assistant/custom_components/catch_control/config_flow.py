"""UI setup and options using HA's Bluetooth discovery cache."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers import selector
from ._vendor.catch_control.ble import advertised_model
from ._vendor.catch_control.configuration import AuthenticationError
from .connection import probe
from .const import CONF_ADDRESS, CONF_INTERVAL, CONF_PASSWORD, DEFAULT_INTERVAL, DOMAIN

PASSWORD_SELECTOR = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


class CatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _address = ''

    async def async_step_bluetooth(self, discovery_info):
        if advertised_model(discovery_info) != 10004:
            return self.async_abort(reason='not_supported')
        self._address = discovery_info.address
        self._async_abort_entries_match({CONF_ADDRESS: self._address})
        self.context['title_placeholders'] = {'name': discovery_info.name}
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            password = user_input.get(CONF_PASSWORD, '')
            try:
                identity = await probe(self.hass, address, password)
            except AuthenticationError:
                errors['base'] = 'invalid_auth'
            except Exception:
                errors['base'] = 'cannot_connect'
            else:
                await self.async_set_unique_id(f'{identity.model}-{identity.serial}')
                self._abort_if_unique_id_configured(updates={CONF_ADDRESS: address})
                return self.async_create_entry(
                    title=f'CATCH Control {identity.serial}',
                    data={CONF_ADDRESS: address, 'serial': identity.serial, 'firmware': identity.firmware},
                    options={CONF_PASSWORD: password, CONF_INTERVAL: DEFAULT_INTERVAL},
                )
            self._address = address
        discovered = {info.address: info.name for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
                      if advertised_model(info) == 10004}
        address_field = selector.SelectSelector(selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=address, label=f'{name} ({address})') for address, name in discovered.items()],
            custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN,
        )) if discovered else str
        return self.async_show_form(step_id='user', errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_ADDRESS, default=self._address): address_field,
            vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
        }))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CatchOptionsFlow()


class CatchOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            previous = self.config_entry.options.get(CONF_PASSWORD, '')
            password = '' if user_input.get('clear_password') else user_input.get(CONF_PASSWORD) or previous
            try:
                identity = await probe(self.hass, self.config_entry.data[CONF_ADDRESS], password)
                if identity.serial != self.config_entry.data['serial']:
                    raise ValueError('Device identity changed')
            except AuthenticationError:
                errors['base'] = 'invalid_auth'
            except Exception:
                errors['base'] = 'cannot_connect'
            else:
                return self.async_create_entry(title='', data={
                    CONF_PASSWORD: password, CONF_INTERVAL: user_input[CONF_INTERVAL],
                })
        return self.async_show_form(step_id='init', errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_INTERVAL, default=self.config_entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
            vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Optional('clear_password', default=False): bool,
        }))
