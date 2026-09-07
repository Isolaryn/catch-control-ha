import unittest

from catch_control.configuration import (
    AuthenticationError, ConfigurationConflict, Schedule, WriteVerificationError,
    plan_schedule,
)
from catch_control.enums import ControlMode
from catch_control.protocol import crc16, decode_configuration, parse_frame
from catch_control.schemas import FRAME
from test_protocol import response

PASSWORD = 'test-password'


def configuration_frame(extra=()):
    packet = bytearray(response(1, [
        (1, 'H', 10004), (3, 'H', 4242), (5, 'H', 12718),
        (26, '16s', PASSWORD.encode()), (42, 'H', 3003), (80, 'H', 9600),
        (84, 'B', 1), (85, 'H', 1), (87, 'H', 420), (89, 'H', 840),
        (98, 'B', 1), (99, 'H', 2), (101, 'H', 0), (103, 'H', 1439),
        (134, 'B', 1), *extra,
    ]))
    # Preserve nonzero, unknown extension bytes rather than zero-filling them.
    packet[136:253] = bytes(range(117))
    packet[-2:] = crc16(packet[:-2]).to_bytes(2, 'big')
    return bytes(packet)


def as_readback(write_packet):
    body = parse_frame(write_packet)
    return FRAME.build({'body': {'value': {'opcode': 1, 'payload': body.payload}}})


class ConfigurationTests(unittest.TestCase):
    def test_only_selected_schedule_changes(self):
        original = configuration_frame()
        plan = plan_schedule(original, PASSWORD, 4, Schedule(False, ControlMode.TURN_OFF, 840, 845))
        packet = plan._packet()
        self.assertEqual(parse_frame(packet).opcode, 2)
        # Independent wire check: only opcode, selected fields and CRC differ.
        changed = {index for index, (a, b) in enumerate(zip(original, packet)) if a != b}
        self.assertTrue(changed <= {0, 105, 106, 107, 108, 109, 110, 111, 253, 254})
        self.assertTrue(plan.changed)
        plan._check_after(as_readback(packet), PASSWORD)
        self.assertNotIn(PASSWORD, repr(plan))
        self.assertNotIn(PASSWORD, repr(plan.summary()))
        self.assertEqual(decode_configuration(as_readback(packet))['ct_ratio'], 3003)
        with self.assertRaises(TypeError):
            plan.after['active_raw'] = 1
        summary = plan.summary()
        summary['after']['active_raw'] = 1
        self.assertEqual(plan._packet(), packet)

    def test_wrong_password_and_firmware_never_produce_write(self):
        schedule = Schedule(False, ControlMode.TURN_OFF, 840, 845)
        with self.assertRaises(AuthenticationError) as error:
            plan_schedule(configuration_frame(), 'wrong', 4, schedule)
        self.assertNotIn(PASSWORD, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        with self.assertRaisesRegex(ValueError, 'firmware 12718'):
            plan_schedule(configuration_frame([(5, 'H', 12719)]), PASSWORD, 4, schedule)

    def test_overlap_and_unknown_windows_rejected(self):
        for schedule in [Schedule(True, ControlMode.TURN_OFF, 900, 930),
                         Schedule(True, ControlMode.TURN_OFF, 100, 420)]:
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                plan_schedule(configuration_frame(), PASSWORD, 4, schedule)
        with self.assertRaises(ValueError):
            Schedule(True, ControlMode.TURN_OFF, 1380, 60)
        with self.assertRaises(ValueError):
            Schedule(True, ControlMode.DEFAULT, 60, 120)
        with self.assertRaises(ValueError):
            Schedule(False, ControlMode.TURN_OFF, 0, 1440)

    def test_existing_overlap_does_not_prevent_disabling_a_slot(self):
        plan = plan_schedule(configuration_frame(), PASSWORD, 3,
                             Schedule(False, ControlMode.TURN_ON, 0, 1439))
        self.assertFalse(plan.after['active_raw'])

    def test_stale_plan_and_failed_readback(self):
        original = configuration_frame()
        plan = plan_schedule(original, PASSWORD, 4, Schedule(False, ControlMode.TURN_OFF, 840, 845))
        # Advancing clock is expected; changed electrical settings are not.
        plan._check_before(configuration_frame([(8, 'h', 22)]), PASSWORD)
        with self.assertRaises(ConfigurationConflict):
            plan._check_before(configuration_frame([(42, 'H', 3004)]), PASSWORD)
        with self.assertRaises(WriteVerificationError):
            plan._check_after(original, PASSWORD)

    def test_unchanged_plan(self):
        plan = plan_schedule(configuration_frame(), PASSWORD, 4, Schedule(False, ControlMode.DEFAULT, 0, 0))
        self.assertFalse(plan.changed)
