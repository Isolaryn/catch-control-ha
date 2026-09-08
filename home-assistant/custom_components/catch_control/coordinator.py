"""Serialize polling and schedule writes; reconnect for each transaction."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from ._vendor.catch_control.configuration import Schedule
from ._vendor.catch_control.enums import ControlMode
from ._vendor.catch_control.protocol import Identity
from .connection import client_for
from .const import (
    CONF_ADDRESS, CONF_INTERVAL, CONF_PASSWORD, CONF_TRANSPORT, DEFAULT_INTERVAL,
    DOMAIN, TRANSPORT_BLUETOOTH, TRANSPORT_WIFI,
)

_LOGGER = logging.getLogger(__name__)


class CatchCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, wifi_server=None):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)))
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.transport = entry.data.get(CONF_TRANSPORT, TRANSPORT_BLUETOOTH)
        self.password = entry.options.get(CONF_PASSWORD, entry.data.get(CONF_PASSWORD, ''))
        self.wifi_server = wifi_server
        self.identity = None
        self.configuration_error = None
        self.last_read_stage = None
        self._transaction = asyncio.Lock()

    @property
    def can_write(self):
        return self.transport == TRANSPORT_WIFI or bool(self.password)

    def _check_identity(self, identity):
        if identity.model != 10004 or identity.firmware != 12718:
            raise ValueError('The connected Catch model or firmware is not supported')
        if str(identity.serial) != str(self.entry.data['serial']):
            raise ValueError('A different Catch responded to this integration entry')
        self.identity = identity

    async def _async_update_data(self):
        if self.transport == TRANSPORT_WIFI:
            return await self._async_update_data_wifi()
        async with self._transaction:
            try:
                self.last_read_stage = 'connection/identity'
                async with client_for(self.hass, self.address) as client:
                    self._check_identity(client.identity)
                    self.last_read_stage = 'telemetry'
                    telemetry = await client.telemetry()
                    self.last_read_stage = 'configuration'
                    try:
                        configuration = await client.configuration()
                    except Exception as exc:
                        # A missing configuration reply must not hide a valid
                        # telemetry sample. Never present old schedules as fresh.
                        error = type(exc).__name__
                        if self.configuration_error != error:
                            _LOGGER.warning('Configuration read failed (%s); telemetry remains available, schedule controls are unavailable; configuration will be retried on the next poll', error)
                        self.configuration_error = error
                        configuration = None
                    else:
                        if self.configuration_error:
                            _LOGGER.info('Configuration reads recovered')
                        self.configuration_error = None
                    return {'telemetry': telemetry, 'configuration': configuration}
            except Exception as exc:
                # No packet, password or config-entry data is logged.
                raise UpdateFailed(f'Bluetooth read failed during {self.last_read_stage} ({type(exc).__name__})') from None

    async def _async_update_data_wifi(self):
        async with self._transaction:
            session = None
            try:
                self.last_read_stage = 'websocket connection'
                session = await self.wifi_server.async_session()
                self.last_read_stage = 'telemetry'
                telemetry = await session.telemetry()
                identity = Identity(telemetry['model'], telemetry['serial'], telemetry['firmware'])
                self._check_identity(identity)
                self.last_read_stage = 'configuration'
                try:
                    configuration = await session.configuration()
                except Exception as exc:
                    error = type(exc).__name__
                    if self.configuration_error != error:
                        _LOGGER.warning('WebSocket configuration read failed (%s); telemetry remains available', error)
                    self.configuration_error = error
                    configuration = None
                    await self.wifi_server.async_discard(session)
                else:
                    self.configuration_error = None
                return {'telemetry': telemetry, 'configuration': configuration}
            except Exception as exc:
                if session is not None:
                    await self.wifi_server.async_discard(session)
                raise UpdateFailed(f'Wi-Fi read failed during {self.last_read_stage} ({type(exc).__name__})') from None

    async def async_edit_schedule(self, slot, **changes):
        if not self.can_write:
            raise HomeAssistantError('Set the local device password in integration options to enable schedule changes')
        if self.transport == TRANSPORT_WIFI:
            return await self._async_edit_schedule_wifi(slot, **changes)
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
        self.configuration_error = None
        self.async_set_updated_data(data)

    async def _async_edit_schedule_wifi(self, slot, **changes):
        session = None
        async with self._transaction:
            try:
                session = await self.wifi_server.async_session()
                current = (await session.configuration())['overrides'][slot - 1]
                schedule = Schedule(
                    changes.get('active', bool(current['active_raw'])),
                    ControlMode(changes.get('mode', current['mode_raw'])),
                    changes.get('start_minutes', current['start_minutes']),
                    changes.get('stop_minutes', current['stop_minutes']),
                )
                plan = await session.plan_schedule(slot, schedule)
                await session.apply_schedule(plan)
                telemetry = await session.telemetry()
                identity = Identity(telemetry['model'], telemetry['serial'], telemetry['firmware'])
                self._check_identity(identity)
                data = {'telemetry': telemetry, 'configuration': await session.configuration()}
            except Exception as exc:
                if session is not None:
                    await self.wifi_server.async_discard(session)
                if isinstance(exc, ValueError):
                    raise HomeAssistantError(str(exc)) from None
                raise HomeAssistantError('Schedule change was not verified; refresh configuration before retrying') from None
        self.configuration_error = None
        self.async_set_updated_data(data)
