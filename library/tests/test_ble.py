from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import patch

from catch_control.ble import CatchClient, advertised_model
from catch_control.protocol import CHARACTERISTIC_UUID, SERVICE_UUID, read_request
from catch_control.protocol import validate_frame
from catch_control.configuration import Schedule, WriteVerificationError
from catch_control.enums import ControlMode
from test_configuration import PASSWORD, as_readback, configuration_frame
from test_protocol import response as make_response


class FakeBleak:
    def __init__(self, device, **kwargs):
        self.is_connected = False
        self.disconnected = kwargs['disconnected_callback']
        self.char = SimpleNamespace(properties=['notify', 'write-without-response'],
                                    max_write_without_response_size=20)
        self.services = self
        self.writes = []
        self.packet = bytearray()
        self.silent = False

    def get_service(self, uuid):
        assert uuid == SERVICE_UUID
        return self

    def get_characteristic(self, uuid):
        assert uuid == CHARACTERISTIC_UUID
        return self.char

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.disconnected(self)

    async def start_notify(self, characteristic, callback):
        self.callback = callback

    async def write_gatt_char(self, characteristic, data, response):
        assert not response
        assert len(data) <= 20
        self.writes.append(bytes(data))
        self.packet.extend(data)
        if len(self.packet) == 255:
            opcode = self.packet[0]
            assert bytes(self.packet) == read_request(opcode)
            self.packet.clear()
            if self.silent:
                return
            packet = make_response(opcode, (
                [(1, 'H', 10004), (3, 'H', 4242), (5, 'H', 12718)] if opcode == 0
                else [(62, 'H', 10004), (64, 'H', 4242), (66, 'H', 12718)]
            ))
            # An unrelated valid message must not satisfy the pending request.
            self.callback(characteristic, read_request(3 if opcode == 0 else 0))
            self.callback(characteristic, packet[:73])
            self.callback(characteristic, packet[73:])


class BleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_configuration_read_propagates_and_disconnects(self):
        with patch('catch_control.ble.BleakClient', FakeBleak):
            async with CatchClient('test') as client:
                await client.telemetry()
                client._client.silent = True
                # Match HA setup: identity and telemetry worked, then the
                # owning task is cancelled while configuration is outstanding.
                requested = asyncio.Event()
                write = client._write_chunks

                async def send(packet):
                    await write(packet)
                    requested.set()

                with patch.object(client, '_write_chunks', side_effect=send):
                    task = asyncio.create_task(client.configuration())
                    await asyncio.wait_for(requested.wait(), timeout=2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertFalse(client._client.is_connected)
                self.assertIsNone(client._pending)

    def test_manufacturer_bytes(self):
        self.assertEqual(advertised_model(SimpleNamespace(manufacturer_data={0: b'\x27\x14'})), 10004)
        self.assertIsNone(advertised_model(SimpleNamespace(manufacturer_data={0x1427: b'\0\0'})))
        self.assertIsNone(advertised_model(SimpleNamespace(manufacturer_data={})))

    async def test_fragmented_requests_and_notifications(self):
        with patch('catch_control.ble.BleakClient', FakeBleak):
            async with CatchClient('test') as client:
                self.assertEqual(client.identity.serial, 4242)
                result = await client.telemetry()
                self.assertEqual(result['model'], 10004)
                self.assertEqual(len(client._client.writes), 26)
            self.assertFalse(client._client.is_connected)

    async def test_timeout_disconnects_before_reuse(self):
        with patch('catch_control.ble.BleakClient', FakeBleak):
            async with CatchClient('test') as client:
                client._client.silent = True
                client.timeout = 0.6
                with self.assertRaises(TimeoutError):
                    await client.telemetry()
                self.assertFalse(client._client.is_connected)
                with self.assertRaises(ConnectionError):
                    await client.telemetry()

    async def test_configuration_timeout_has_metadata_and_bounded_cleanup(self):
        with patch('catch_control.ble.BleakClient', FakeBleak), patch('catch_control.ble.DISCONNECT_TIMEOUT', 0.01):
            async with CatchClient('test') as client:
                client._client.silent = True
                client.timeout = 0.6

                async def stuck_disconnect():
                    await asyncio.Event().wait()

                with patch.object(client._client, 'disconnect', side_effect=stuck_disconnect), self.assertLogs('catch_control.ble', level='DEBUG') as logs:
                    # A stuck backend cleanup must not turn a read timeout
                    # into an indefinitely blocked HA setup task.
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(client.configuration(), timeout=1)
                    # Even if disconnect did not complete, the old transport
                    # must never accept another request with no transaction ID.
                    with self.assertRaises(ConnectionError):
                        await client.configuration()
                output = '\n'.join(logs.output)
                self.assertIn('opcode=1 outcome=TimeoutError', output)
                self.assertIn('rx_bytes=0', output)
                self.assertIn('tx_chunks=13', output)
                self.assertNotIn(PASSWORD, output)

    def test_invalid_connection_timeout(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                CatchClient('test', connect_timeout=value)


class FakeConfigBleak(FakeBleak):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.char.max_write_without_response_size = 255
        self.config = configuration_frame()
        self.ignore_save = False
        self.save_count = 0

    async def write_gatt_char(self, characteristic, data, response):
        assert not response
        validate_frame(data)
        if data[0] == 0:
            reply = make_response(0, [(1, 'H', 10004), (3, 'H', 4242), (5, 'H', 12718)])
        elif data[0] == 1:
            reply = self.config
        elif data[0] == 2:
            self.save_count += 1
            if not self.ignore_save:
                self.config = as_readback(data)
            return  # Saving need not produce an acknowledgement notification.
        else:
            raise AssertionError('Unexpected test opcode')
        self.callback(characteristic, reply)


class ConfigurationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_connector_authenticates_without_writing(self):
        clients = []

        async def connector(device, disconnected, timeout):
            self.assertEqual(device, 'host-selected-device')
            self.assertEqual(timeout, 10)
            backend = FakeConfigBleak(device, disconnected_callback=disconnected)
            await backend.connect()
            clients.append(backend)
            return backend

        with self.assertLogs('catch_control.ble', level='DEBUG') as logs:
            async with CatchClient('host-selected-device', connector=connector) as client:
                await client.authenticate(password=PASSWORD)
                from catch_control.configuration import AuthenticationError
                with self.assertRaises(AuthenticationError):
                    await client.authenticate(password='wrong')
                self.assertEqual(clients[0].save_count, 0)
        self.assertNotIn(PASSWORD, '\n'.join(logs.output))
        self.assertFalse(clients[0].is_connected)

    async def test_failed_injected_connection_preserves_error(self):
        async def connector(*args):
            raise ConnectionError('adapter unavailable')

        with self.assertRaisesRegex(ConnectionError, 'adapter unavailable'):
            async with CatchClient('test', connector=connector):
                self.fail('Failed connection must not enter context')

    async def test_save_without_ack_then_readback(self):
        with patch('catch_control.ble.BleakClient', FakeConfigBleak):
            async with CatchClient('test') as client:
                plan = await client.plan_schedule(4, Schedule(False, ControlMode.TURN_OFF, 840, 845), password=PASSWORD)
                result = await client.apply_schedule(plan, password=PASSWORD)
                self.assertTrue(result['verified'])
                self.assertTrue(result['written'])
                self.assertEqual(client._client.save_count, 1)

    async def test_ignored_save_is_not_success_or_retried(self):
        with patch('catch_control.ble.BleakClient', FakeConfigBleak):
            async with CatchClient('test') as client:
                client._client.ignore_save = True
                plan = await client.plan_schedule(4, Schedule(False, ControlMode.TURN_OFF, 840, 845), password=PASSWORD)
                with self.assertRaises(WriteVerificationError):
                    await client.apply_schedule(plan, password=PASSWORD)
                self.assertEqual(client._client.save_count, 1)

    async def test_unchanged_plan_does_not_write(self):
        with patch('catch_control.ble.BleakClient', FakeConfigBleak):
            async with CatchClient('test') as client:
                plan = await client.plan_schedule(4, Schedule(False, ControlMode.DEFAULT, 0, 0), password=PASSWORD)
                result = await client.apply_schedule(plan, password=PASSWORD)
                self.assertFalse(result['written'])
                self.assertEqual(client._client.save_count, 0)
