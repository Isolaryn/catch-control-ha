import unittest

from catch_control.network import (
    WIFI_SETTINGS_PAYLOAD, decode_server_settings, plan_websocket_server,
)
from catch_control.schemas import FRAME, PAYLOAD_SIZE


def wifi_frame():
    payload = WIFI_SETTINGS_PAYLOAD.build({
        'settings': {
            'ip_type': 1,
            'ip': b'192.0.2.20'.ljust(16, b'\0'),
            'gateway': b'192.0.2.1'.ljust(16, b'\0'),
            'dns': b'192.0.2.53'.ljust(16, b'\0'),
            'subnet': b'255.255.255.0'.ljust(16, b'\0'),
            'server1': b'old.example'.ljust(30, b'\0'),
            'server1_port': 443,
            'server2': b'backup.example'.ljust(30, b'\0'),
            'server2_port': 444,
            'wifi_password': b'private'.ljust(20, b'\0'),
            'security': 3,
            'ssid': b'private-network'.ljust(33, b'\0'),
            'wifi_password2': b'private'.ljust(44, b'\0'),
        },
        'extension': bytes(PAYLOAD_SIZE - 228),
    })
    return FRAME.build({'body': {'value': {'opcode': 7, 'payload': payload}}})


class NetworkSettingsTests(unittest.TestCase):
    def test_read_projection_hides_network_credentials(self):
        result = decode_server_settings(wifi_frame())
        self.assertEqual(result.primary_host, 'old.example')
        self.assertEqual(result.secondary_port, 444)
        self.assertNotIn('private', repr(result))

    def test_plan_changes_only_two_server_records(self):
        plan = plan_websocket_server(wifi_frame(), 'ha.example', 8443)
        before = plan._original_payload
        after = plan._updated_payload
        changed = {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
        allowed = set(range(66, 98)) | set(range(98, 130))
        self.assertTrue(changed)
        self.assertLessEqual(changed, allowed)
        self.assertEqual(plan.after.primary_host, 'ha.example')
        self.assertEqual(plan.after.secondary_port, 8443)

    def test_rejects_unverified_endpoint_encodings(self):
        for host, port in [('', 8443), ('https://ha.example', 8443), ('ha.example', 65535)]:
            with self.subTest(host=host, port=port):
                with self.assertRaises(ValueError):
                    plan_websocket_server(wifi_frame(), host, port)


if __name__ == '__main__':
    unittest.main()
