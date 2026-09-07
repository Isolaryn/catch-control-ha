import struct
import unittest
from construct import ConstructError
from catch_control.schemas import CONFIGURATION_READ_PAYLOAD

from catch_control.protocol import (
    FrameBuffer, ProtocolError, crc16, decode_configuration, decode_identity, decode_telemetry,
    read_request, validate_frame,
)


def response(opcode, fields=()):
    body = bytearray(253)
    body[0] = opcode
    for offset, fmt, value in fields:
        struct.pack_into('<' + fmt, body, offset, value)
    return bytes(body) + crc16(body).to_bytes(2, 'big')


class ProtocolTests(unittest.TestCase):
    def test_crc_and_app_request_vectors(self):
        # Compared independently with Configurator's table-based algorithm.
        self.assertEqual(crc16(b'123456789'), 0x4B37)
        self.assertEqual(read_request(0), bytes(253) + bytes.fromhex('55e8'))
        self.assertEqual(read_request(3), b'\x03' + bytes(252) + bytes.fromhex('94ad'))
        with self.assertRaises(ValueError):
            read_request(8)

    def test_fragmentation_and_coalescing(self):
        packet = read_request(0)
        for boundary in range(1, 255):
            buf = FrameBuffer()
            self.assertEqual(buf.feed(packet[:boundary]), [])
            self.assertEqual(buf.feed(packet[boundary:] + packet), [packet, packet])

    def test_bad_crc_discards_buffer(self):
        packet = bytearray(read_request(0))
        packet[22] ^= 1
        buf = FrameBuffer()
        with self.assertRaises(ProtocolError):
            buf.feed(packet + read_request(3)[:20])
        self.assertEqual(buf.feed(read_request(0)), [read_request(0)])
        with self.assertRaises(ProtocolError):
            validate_frame(b'\0')
        with self.assertRaises(ProtocolError):
            validate_frame(read_request(0) + b'\0')
        # CRC bytes use network order even though payload scalars do not.
        with self.assertRaises(ProtocolError):
            validate_frame(bytes(253) + bytes.fromhex('e855'))
        with self.assertRaises(ProtocolError):
            validate_frame(read_request(0), 3)

    def test_identity(self):
        packet = response(0, [(1, 'H', 10004), (3, 'H', 4242), (5, 'H', 12718)])
        identity = decode_identity(packet)
        self.assertEqual((identity.model, identity.serial, identity.firmware), (10004, 4242, 12718))

    def test_signed_telemetry_and_scaling(self):
        # Synthetic fixture: deliberately includes negative exported power/PF.
        packet = response(3, [
            (1, 'H', 2424), (3, 'H', 46), (5, 'i', -920), (9, 'h', -83),
            (19, 'H', 49990), (21, 'i', -12345), (31, 'i', 3500),
            (62, 'H', 10004), (64, 'H', 4242), (66, 'H', 12718),
            (56, 'B', 2), (86, 'H', 4),
            (88, '16s', b'192.0.2.20'),
            (145, 'b', -61),
        ])
        data = decode_telemetry(packet)
        self.assertEqual(data['voltage_v'], 242.4)
        self.assertEqual(data['frequency_hz'], 49.99)
        self.assertEqual(data['channel_1']['current_a'], 4.6)
        self.assertEqual(data['channel_1']['power_w'], -920)
        self.assertEqual(data['channel_1']['power_factor'], -0.83)
        self.assertEqual(data['channel_1']['export_energy_raw_wh'], -12345)
        self.assertEqual(data['channel_2']['power_w'], 3500)
        self.assertEqual(data['ip_address'], '192.0.2.20')
        self.assertEqual(data['wifi_rssi_dbm'], -61)
        self.assertEqual(data['control_mode'], 'turn_on')
        self.assertEqual(data['server_status'], 'server_good')
        with self.assertRaises(ProtocolError):
            decode_telemetry(response(3, [(62, 'H', 10003)]))

    def test_unknown_modes_remain_readable(self):
        data = decode_telemetry(response(3, [(62, 'H', 10004), (56, 'B', 250), (86, 'H', 321)]))
        self.assertEqual(data['control_mode_raw'], 250)
        self.assertEqual(data['control_mode'], 'unknown_250')
        self.assertEqual(data['server_status'], 'unknown_321')

    def test_configuration_layout_and_credential_omission(self):
        packet = response(1, [
            (1, 'H', 10004), (3, 'H', 4242), (5, 'H', 12718),
            (26, '16s', b'synthetic-secret'), (42, 'H', 2000),
            (66, 'i', -3000), (72, 'i', 10), (80, 'H', 9600),
            (84, 'B', 1), (85, 'H', 2), (87, 'H', 60), (89, 'H', 120),
            (106, 'H', 3), (108, 'H', 600), (110, 'H', 660),
            (130, 'B', 1), (131, 'B', 2),
        ])
        data = decode_configuration(packet)
        self.assertEqual(data['ct_ratio'], 2000)
        self.assertEqual(data['export']['above_threshold'], -3000)
        self.assertEqual(data['modbus_baud'], 9600)
        self.assertEqual(data['overrides'][0], {
            'active_raw': 1, 'mode_raw': 2, 'mode': 'turn_on',
            'start_minutes': 60, 'stop_minutes': 120,
        })
        self.assertEqual(data['overrides'][3]['stop_minutes'], 660)
        self.assertEqual(data['channel_2_purpose_raw'], 2)
        self.assertNotIn('synthetic-secret', repr(data))
        self.assertNotIn('password', repr(data))
        with self.assertRaises(ConstructError):
            CONFIGURATION_READ_PAYLOAD.build(data)
