import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from catch_control_cli.cli import main, parse_time, run


class CliTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_arguments_fail_before_bluetooth(self):
        for argv in [['read', '--apply'], ['watch', '--interval', 'nan'], ['read', '--count', '1'], ['schedule', '--slot', '4']]:
            with self.subTest(argv=argv), patch('sys.argv', ['catch-control', *argv]), redirect_stderr(io.StringIO()), patch('catch_control.ble.discover') as discover:
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                discover.assert_not_called()

    def test_time_parser(self):
        self.assertEqual(parse_time('14:05'), 845)
        for value in ('24:00', '14:60', '14:05:00', '1:00'):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_time(value)

    async def test_read_outputs_json_and_closes_connection(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.telemetry.return_value = {'voltage_v': 240.1}
        args = SimpleNamespace(command='read', scan_timeout=1, address=None, timeout=1)
        output = io.StringIO()
        with patch('catch_control.ble.discover', return_value=[(SimpleNamespace(address='test'), None)]), patch('catch_control.ble.CatchClient', return_value=client), redirect_stdout(output):
            await run(args)
        self.assertEqual(json.loads(output.getvalue()), {'voltage_v': 240.1})
        client.__aexit__.assert_awaited_once()

    async def test_preview_does_not_apply_and_checks_private_file(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.plan_schedule.return_value.summary = lambda: {'changed': True}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'password'
            path.write_text('test-password\n')
            path.chmod(0o600)
            args = SimpleNamespace(command='schedule', scan_timeout=1, address=None, timeout=1,
                                   password_file=str(path), slot=4, active='no', mode='turn_off', start=840, stop=845, apply=False)
            with patch('catch_control.ble.discover', return_value=[(SimpleNamespace(address='test'), None)]), patch('catch_control.ble.CatchClient', return_value=client), redirect_stdout(io.StringIO()):
                await run(args)
                client.plan_schedule.assert_awaited_once()
                client.apply_schedule.assert_not_called()
                path.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'only to its owner'):
                    await run(args)


if __name__ == '__main__':
    unittest.main()
