import struct
import unittest
from catch_control.checksum import crc16
from catch_control.protocol import ProtocolError
from wifi_diagnostics import WIFI_HEALTH_FIELDS, decode_wifi_health


def reply(payload, opcode=30):
    body = bytes([opcode]) + payload.ljust(252, b'\0')
    return body + crc16(body).to_bytes(2, 'big')


class WifiDiagnosticsTests(unittest.TestCase):
    def test_independent_layout_and_privacy(self):
        # Independently packed fixture rather than a production-schema round trip.
        payload = struct.pack('<6Bb6sB3h2iB4H2BI',
            1, 0, 1, 1, 1, 1, -65, b'\xaa\xbb\xcc\xdd\xee\xff', 1,
            720, 650, 250, 12, 3, 2, 4, 7, 9, 60, 80, 2, 123456)
        self.assertEqual(len(payload), 43)
        self.assertEqual(WIFI_HEALTH_FIELDS.sizeof(), 43)
        result = decode_wifi_health(reply(payload))
        self.assertTrue(result['supported'])
        self.assertEqual(result['rssi_dbm_raw'], -65)
        self.assertEqual(result['gateway_rtt_median_ms_raw'], 12)
        self.assertEqual(result['wss_send_fail_count'], 9)
        self.assertEqual(result['collected_at_s_raw'], 123456)
        self.assertEqual(result['capabilities_raw']['retry'], 0)
        self.assertNotIn('bssid', result)
        self.assertNotIn('aa:bb', repr(result))

    def test_unsupported_sentinel(self):
        self.assertEqual(decode_wifi_health(reply(bytes(43))), {'supported': False})

    def test_wrong_operation_and_invalid_frame(self):
        for packet in (reply(bytes(43), opcode=3), reply(bytes(43))[:-1], bytes(255)):
            with self.assertRaises(ProtocolError):
                decode_wifi_health(packet)
