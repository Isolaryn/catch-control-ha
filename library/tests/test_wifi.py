import asyncio
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from catch_control.protocol import ProtocolError
from catch_control.configuration import Schedule
from catch_control.enums import ControlMode
from catch_control.wifi import (
    CONFIGURATION_REQUEST, CatchWebSocketSession, TELEMETRY_MESSAGE_SIZE,
    TELEMETRY_REQUEST, decode_websocket_configuration, decode_websocket_telemetry,
)


def telemetry_message():
    # Independent packed fixture using offsets from the 145-byte wire layout.
    data = bytearray(145)
    struct.pack_into('<H', data, 0, 2424)
    struct.pack_into('<H', data, 2, 46)
    struct.pack_into('<i', data, 4, -920)
    struct.pack_into('<h', data, 8, -83)
    struct.pack_into('<H', data, 18, 49990)
    struct.pack_into('<i', data, 20, -12345)
    struct.pack_into('<i', data, 30, 3500)
    struct.pack_into('<B', data, 55, 2)
    struct.pack_into('<HHH', data, 61, 10004, 4242, 12718)
    struct.pack_into('<H', data, 85, 4)
    struct.pack_into('<16s', data, 87, b'192.0.2.20')
    struct.pack_into('<b', data, 144, -61)
    return bytes(data)


def configuration_message(slot4=(0, 3, 840, 845)):
    data = bytearray(128)
    data[18:34] = b'private-password'
    for index in range(4):
        struct.pack_into('<BHHH', data, 76 + index * 7, *(slot4 if index == 3 else (0, 0, 0, 0)))
    data[126] = 1
    return bytes(data)


class WifiProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_construct_backed_telemetry_decode(self):
        message = telemetry_message()
        self.assertEqual(len(message), TELEMETRY_MESSAGE_SIZE)
        decoded = decode_websocket_telemetry(message)
        self.assertEqual(decoded['voltage_v'], 242.4)
        self.assertEqual(decoded['frequency_hz'], 49.99)
        self.assertEqual(decoded['channel_1']['power_w'], -920)
        self.assertEqual(decoded['channel_2']['power_w'], 3500)
        self.assertEqual(decoded['ip_address'], '192.0.2.20')
        self.assertEqual(decoded['wifi_rssi_dbm'], -61)

    def test_rejects_text_and_non_exact_binary_size(self):
        for message in ('text', b'', telemetry_message() + b'\0'):
            with self.subTest(message_type=type(message).__name__, size=len(message)):
                with self.assertRaises(ProtocolError):
                    decode_websocket_telemetry(message)

    def test_configuration_decoder_hides_credentials(self):
        result = decode_websocket_configuration(configuration_message())
        self.assertEqual(result['overrides'][3], {
            'active_raw': 0, 'mode_raw': 3, 'start_minutes': 840, 'stop_minutes': 845,
        })
        self.assertNotIn('private-password', repr(result))

    async def test_session_sends_read_and_decodes_response(self):
        connection = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(return_value=telemetry_message()))
        session = CatchWebSocketSession(connection, timeout=1)
        result = await session.telemetry()
        connection.send.assert_awaited_once_with(TELEMETRY_REQUEST)
        connection.recv.assert_awaited_once_with()
        self.assertEqual(result['model'], 10004)
        self.assertEqual(result['firmware'], 12718)

    async def test_failed_request_poisons_session(self):
        connection = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(side_effect=RuntimeError('closed')))
        session = CatchWebSocketSession(connection, timeout=1)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await session.telemetry()
        with self.assertRaisesRegex(ConnectionError, 'cannot be reused'):
            await session.telemetry()
        connection.send.assert_awaited_once()

    async def test_schedule_write_uses_one_structured_record_and_verifies(self):
        original = configuration_message()
        changed = configuration_message((0, 4, 840, 845))
        connection = SimpleNamespace(
            send=AsyncMock(), recv=AsyncMock(side_effect=[original, original, b'\x01', changed])
        )
        session = CatchWebSocketSession(connection, timeout=1)
        plan = await session.plan_schedule(4, Schedule(False, ControlMode.TOP_UP, 840, 845))
        result = await session.apply_schedule(plan)
        self.assertTrue(result['verified'])
        self.assertEqual(connection.send.await_args_list[0].args[0], CONFIGURATION_REQUEST)
        self.assertEqual(connection.send.await_args_list[1].args[0], CONFIGURATION_REQUEST)
        write = connection.send.await_args_list[2].args[0]
        self.assertEqual(write[:3], bytes((2, 97, 7)))
        self.assertEqual(write[3:], struct.pack('<BHHH', 0, 4, 840, 845))
        self.assertEqual(connection.send.await_args_list[3].args[0], CONFIGURATION_REQUEST)

    async def test_timeout_validation_and_timeout(self):
        for timeout in (0, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                CatchWebSocketSession(SimpleNamespace(), timeout=timeout)
        never = asyncio.Event()
        async def wait_forever():
            await never.wait()
        connection = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(side_effect=wait_forever))
        with self.assertRaises(TimeoutError):
            await CatchWebSocketSession(connection, timeout=.01).telemetry()


if __name__ == '__main__':
    unittest.main()
