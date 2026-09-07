from copy import deepcopy
import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.catch_control import async_setup_entry, async_unload_entry
from custom_components.catch_control._vendor.catch_control.protocol import Identity
from custom_components.catch_control._vendor.catch_control.configuration import AuthenticationError
from custom_components.catch_control.config_flow import CatchConfigFlow, CatchOptionsFlow
from custom_components.catch_control.coordinator import CatchCoordinator
from custom_components.catch_control.diagnostics import async_get_config_entry_diagnostics
from custom_components.catch_control.sensor import CatchSensor, SENSORS
from custom_components.catch_control.switch import ScheduleSwitch, schedule_minutes
from custom_components.catch_control.select import ScheduleMode
from custom_components.catch_control.time import ScheduleTime

IDENTITY = Identity(10004, 4242, 12718)
SCHEDULE = {'active_raw': 0, 'mode_raw': 3, 'start_minutes': 840, 'stop_minutes': 845}
DATA = {
    'configuration': {'overrides': [deepcopy(SCHEDULE) for _ in range(4)]},
    'telemetry': dict(voltage_v=240.1, frequency_hz=50.0, control_mode='turn_on',
                      server_status='server_good', wifi_rssi_dbm=-55, runtime_minutes=20,
                      **{f'channel_{i}': dict(current_a=1.0, power_w=240, apparent_power_va=240,
                                            reactive_power_var=0, power_factor=1.0) for i in (1, 2)}),
}


def fake_client():
    client = AsyncMock()
    client.identity = IDENTITY
    client.__aenter__.return_value = client
    client.configuration.return_value = deepcopy(DATA['configuration'])
    client.telemetry.return_value = deepcopy(DATA['telemetry'])
    return client


async def test_coordinator_reads_and_failure(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    client = fake_client()
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client):
        assert await coordinator._async_update_data() == DATA
    client.apply_schedule.assert_not_called()
    client.__aexit__.assert_awaited_once()
    with patch('custom_components.catch_control.coordinator.client_for', side_effect=ConnectionError('backend detail')):
        with pytest.raises(UpdateFailed, match='ConnectionError') as error:
            await coordinator._async_update_data()
    assert 'backend detail' not in str(error.value)


async def test_schedule_uses_current_values_and_publishes_readback(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    client = fake_client()
    final = deepcopy(DATA['configuration'])
    final['overrides'][3]['mode_raw'] = 4
    client.configuration.side_effect = [deepcopy(DATA['configuration']), final]
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client):
        await coordinator.async_edit_schedule(4, mode=4)
    args = client.plan_schedule.call_args
    assert args.args[0] == 4
    assert args.args[1].to_wire() == {**SCHEDULE, 'mode_raw': 4}
    assert args.kwargs == {'password': 'test-password'}
    client.apply_schedule.assert_awaited_once_with(client.plan_schedule.return_value, password='test-password')
    assert coordinator.data['configuration'] == final


@pytest.mark.parametrize('failure', [TimeoutError('secret backend data'), ValueError('Invalid window')])
async def test_failed_writes_are_not_retried(hass, entry, failure):
    coordinator = CatchCoordinator(hass, entry)
    client = fake_client()
    client.apply_schedule.side_effect = failure
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client):
        with pytest.raises(HomeAssistantError) as error:
            await coordinator.async_edit_schedule(4, mode=4)
    client.apply_schedule.assert_awaited_once()
    assert 'secret backend data' not in str(error.value)
    assert coordinator.data is None


async def test_wrong_device_and_missing_password_prevent_writes(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    client = fake_client()
    client.identity = Identity(10004, 9999, 12718)
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client):
        with pytest.raises(HomeAssistantError, match='different Catch'):
            await coordinator.async_edit_schedule(4, mode=4)
        coordinator.password = ''
        with pytest.raises(HomeAssistantError, match='password'):
            await coordinator.async_edit_schedule(4, mode=4)
    client.plan_schedule.assert_not_called()
    client.apply_schedule.assert_not_called()


async def test_entities_and_actions(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    coordinator.identity = IDENTITY
    coordinator.async_set_updated_data(deepcopy(DATA))
    sensors = [CatchSensor(coordinator, *description) for description in SENSORS]
    assert len(sensors) == 16
    assert all(entity.available for entity in sensors)
    assert all(entity.native_value is not None for entity in sensors)
    assert sensors[0].native_value == 240.1
    assert sensors[0].native_unit_of_measurement == 'V'
    switch, mode, start = ScheduleSwitch(coordinator, 4), ScheduleMode(coordinator, 4), ScheduleTime(coordinator, 4, 'start')
    assert switch.unique_id == '10004-4242-schedule_4_enabled'
    assert switch.is_on is False
    assert mode.current_option == 'turn_off'
    assert start.native_value == time(14, 0)
    coordinator.async_edit_schedule = AsyncMock()
    await switch.async_turn_on()
    coordinator.async_edit_schedule.assert_awaited_with(4, active=True)
    await mode.async_select_option('top_up')
    coordinator.async_edit_schedule.assert_awaited_with(4, mode=4)
    await start.async_set_value(time(14, 2))
    coordinator.async_edit_schedule.assert_awaited_with(4, start_minutes=842)
    with pytest.raises(HomeAssistantError):
        await start.async_set_value(time(14, 2, 1))
    await switch.async_set_schedule(False, 'turn_off', 840, 845)
    coordinator.async_edit_schedule.assert_awaited_with(4, active=False, mode=3, start_minutes=840, stop_minutes=845)
    coordinator.password = ''
    assert not switch.available and sensors[0].available
    coordinator.password = 'test-password'
    coordinator.identity = Identity(10004, 4242, 12719)
    assert not switch.available
    coordinator.async_set_update_error(UpdateFailed('offline'))
    assert not sensors[0].available


def test_service_time_validation():
    assert schedule_minutes('14:05') == 845
    for value in ('24:00', '14:05:01', '14:05+10:00', 'invalid', '1405'):
        with pytest.raises(vol.Invalid):
            schedule_minutes(value)


async def test_user_flow(hass):
    flow = CatchConfigFlow()
    flow.hass = hass
    flow.context = {'source': 'user'}
    with patch('custom_components.catch_control.config_flow.probe', return_value=IDENTITY):
        result = await flow.async_step_user({'address': ' AA:BB:CC:DD:EE:FF ', 'password': 'test-password'})
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'] == {'address': 'AA:BB:CC:DD:EE:FF', 'serial': 4242, 'firmware': 12718}
    assert result['options']['password'] == 'test-password'
    assert flow.unique_id == '10004-4242'


async def test_invalid_password_flow_and_unsupported_discovery(hass):
    flow = CatchConfigFlow()
    flow.hass = hass
    flow.context = {'source': 'user'}
    with patch('custom_components.catch_control.config_flow.probe', side_effect=AuthenticationError('no match')), patch('custom_components.catch_control.config_flow.bluetooth.async_discovered_service_info', return_value=[]):
        result = await flow.async_step_user({'address': 'test', 'password': 'wrong'})
    assert result['type'] == FlowResultType.FORM
    assert result['errors'] == {'base': 'invalid_auth'}
    assert 'wrong' not in repr(result)
    result = await flow.async_step_bluetooth(SimpleNamespace(manufacturer_data={0: b'\x27\x15'}))
    assert result['reason'] == 'not_supported'


@pytest.mark.parametrize('clear,expected', [(False, 'test-password'), (True, '')])
async def test_options_keep_or_clear_password(hass, entry, clear, expected):
    flow = CatchOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    with patch.object(hass.config_entries, 'async_get_known_entry', return_value=entry), patch('custom_components.catch_control.config_flow.probe', return_value=IDENTITY) as probe:
        result = await flow.async_step_init({'scan_interval': 45, 'clear_password': clear, 'password': ''})
    assert result['data'] == {'scan_interval': 45, 'password': expected}
    probe.assert_awaited_once_with(hass, entry.data['address'], expected)


async def test_setup_unload_and_diagnostics(hass, entry):
    entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
    with patch('custom_components.catch_control.coordinator.client_for', return_value=fake_client()), patch.object(hass.config_entries, 'async_forward_entry_setups', new_callable=AsyncMock) as forward, patch.object(hass.config_entries, 'async_unload_platforms', new_callable=AsyncMock, return_value=True):
        assert await async_setup_entry(hass, entry)
        forward.assert_awaited_once()
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        assert 'test-password' not in repr(diagnostic)
        assert entry.data['address'] not in repr(diagnostic)
        assert '4242' not in repr(diagnostic)
        assert await async_unload_entry(hass, entry)
    await entry.runtime_data.async_shutdown()


@pytest.mark.parametrize('configuration_timeout', [False, True])
async def test_registered_entities_and_combined_service(hass, entry, configuration_timeout):
    """Exercise actual platform setup, state publishing, action routing and unload."""
    from homeassistant.helpers import device_registry, entity_registry
    from homeassistant.setup import async_setup_component

    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    # Hardware dependency is simulated; the real HA platform machinery runs.
    hass.config.components.update({'bluetooth', 'bluetooth_adapters'})
    hass.config_entries._entries[entry.entry_id] = entry
    for platform in ('sensor', 'switch', 'select', 'time'):
        assert await async_setup_component(hass, platform, {})
    client = fake_client()
    if configuration_timeout:
        client.configuration.side_effect = TimeoutError('private backend text')
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client), patch('homeassistant.setup.async_process_deps_reqs', new_callable=AsyncMock):
        async with asyncio.timeout(20):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        states = hass.states.async_all()
        assert len(states) == 32
        if configuration_timeout:
            assert sum(state.state == 'unavailable' for state in states) == 16
            assert all(state.state not in ('unknown', 'unavailable') for state in states if state.entity_id.startswith('sensor.'))
            diagnostic = await async_get_config_entry_diagnostics(hass, entry)
            assert diagnostic['configuration_error'] == 'TimeoutError'
            assert diagnostic['configuration_available'] is False
            assert 'private backend text' not in repr(diagnostic)
            # The next poll reads configuration on a new connection and
            # automatically makes schedule entities available again.
            client.configuration.side_effect = None
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            states = hass.states.async_all()
            assert entry.runtime_data.configuration_error is None
        assert all(state.state not in ('unknown', 'unavailable') for state in states)
        switch = next(state.entity_id for state in states if state.entity_id.startswith('switch.') and 'schedule_4' in state.entity_id)
        assert hass.services.has_service('catch_control', 'set_schedule')
        with patch.object(entry.runtime_data, 'async_edit_schedule', new_callable=AsyncMock) as edit:
            await hass.services.async_call('catch_control', 'set_schedule',
                {'entity_id': switch, 'active': False, 'mode': 'turn_off', 'start': '14:00', 'stop': '14:05'}, blocking=True)
            edit.assert_awaited_once_with(4, active=False, mode=3, start_minutes=840, stop_minutes=845)
        coordinator = entry.runtime_data
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert all(state.state == 'unavailable' for state in hass.states.async_all())
        assert entry.state == ConfigEntryState.NOT_LOADED
        assert not list(coordinator.async_contexts())


async def test_reload_cancellation_is_not_converted_to_read_failure(hass, entry):
    coordinator = CatchCoordinator(hass, entry)
    client = fake_client()
    waiting = asyncio.Event()

    async def wait_for_reply():
        waiting.set()
        await asyncio.Event().wait()

    client.configuration.side_effect = wait_for_reply
    with patch('custom_components.catch_control.coordinator.client_for', return_value=client):
        task = asyncio.create_task(coordinator._async_update_data())
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert coordinator.data is None
    client.__aexit__.assert_awaited_once()
    client.apply_schedule.assert_not_called()


async def test_connection_uses_connectable_ha_device(hass):
    from custom_components.catch_control.connection import client_for
    device = SimpleNamespace(address='AA:BB:CC:DD:EE:FF', name='Test Catch')
    backend = object()
    disconnected = lambda _: None
    with patch('custom_components.catch_control.connection.bluetooth.async_ble_device_from_address', return_value=device) as lookup, patch('custom_components.catch_control.connection.establish_connection', return_value=backend) as establish:
        client = client_for(hass, device.address)
        assert await client._connector(device, disconnected, 30) is backend
        assert establish.call_args.kwargs['disconnected_callback'] is disconnected
        assert establish.call_args.kwargs['ble_device_callback']() is device
        assert all(call.kwargs == {'connectable': True} for call in lookup.call_args_list)
    with patch('custom_components.catch_control.connection.bluetooth.async_ble_device_from_address', return_value=None):
        with pytest.raises(ConnectionError, match='not visible'):
            client_for(hass, device.address)
