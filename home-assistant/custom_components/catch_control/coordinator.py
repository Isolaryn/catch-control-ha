"""Serialize polling and schedule writes; reconnect for each transaction."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from ._vendor.catch_control.configuration import Schedule
from ._vendor.catch_control.enums import ControlMode
from .connection import client_for
from .const import CONF_ADDRESS, CONF_INTERVAL, CONF_PASSWORD, DEFAULT_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CatchCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)))
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.password = entry.options.get(CONF_PASSWORD, entry.data.get(CONF_PASSWORD, ''))
        self.identity = None
        self._transaction = asyncio.Lock()

    def _check_identity(self, identity):
        if str(identity.serial) != str(self.entry.data['serial']):
            raise ValueError('A different Catch responded at the configured Bluetooth address')
        self.identity = identity

    async def _async_update_data(self):
        async with self._transaction:
            try:
                async with client_for(self.hass, self.address) as client:
                    self._check_identity(client.identity)
                    return {'telemetry': await client.telemetry(), 'configuration': await client.configuration()}
            except Exception as exc:
                # No packet, password or config-entry data is logged.
                raise UpdateFailed(f'Bluetooth read failed ({type(exc).__name__})') from None

    async def async_edit_schedule(self, slot, **changes):
        if not self.password:
            raise HomeAssistantError('Set the local device password in integration options to enable schedule changes')
        async with self._transaction:
            try:
                async with client_for(self.hass, self.address) as client:
                    self._check_identity(client.identity)
                    current = (await client.configuration())['overrides'][slot - 1]
                    schedule = Schedule(
                        changes.get('active', bool(current['active_raw'])),
                        ControlMode(changes.get('mode', current['mode_raw'])),
                        changes.get('start_minutes', current['start_minutes']),
                        changes.get('stop_minutes', current['stop_minutes']),
                    )
                    plan = await client.plan_schedule(slot, schedule, password=self.password)
                    await client.apply_schedule(plan, password=self.password)
                    data = {'telemetry': await client.telemetry(), 'configuration': await client.configuration()}
            except Exception as exc:
                # Library errors are sanitized, but BLE backends may include data.
                if isinstance(exc, ValueError):
                    raise HomeAssistantError(str(exc)) from None
                raise HomeAssistantError('Schedule change was not verified; refresh configuration before retrying') from None
        self.async_set_updated_data(data)
