"""Loopback-only TLS tests with a temporary, explicitly trusted test certificate."""
import asyncio
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from passive_wss import observer, tls_context


class PassiveObserverTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert, cls.key = root / "cert.pem", root / "key.pem"
        config = root / "openssl.cnf"
        config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                          "[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\n"
                          "basicConstraints=critical,CA:TRUE\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-days", "1", "-config", str(config), "-keyout", str(cls.key),
                        "-out", str(cls.cert)], check=True, capture_output=True)
        cls.server_tls = tls_context(cls.cert, cls.key)
        cls.client_tls = ssl.create_default_context(cafile=str(cls.cert))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def test_metadata_only_and_no_application_reply(self):
        events = []
        def report(event, **fields):
            events.append({"event": event, **fields})
        async with observer(self.server_tls, 0, report) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"wss://127.0.0.1:{port}/srwe", ssl=self.client_tls,
                               server_hostname="localhost", proxy=None,
                               additional_headers={"Authorization": "Bearer dummy-secret"}) as client:
                await client.send(b"private-payload")
                await client.send("sensitive-text")
                pong = await client.ping()  # Normal protocol ping/pong remains available.
                await asyncio.wait_for(pong, 1)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(client.recv(), .1)
        messages = [e for e in events if e["event"] == "message"]
        self.assertEqual([(e["kind"], e["bytes"]) for e in messages],
                         [("binary", 15), ("text", 14)])
        serialized = json.dumps(events)
        for forbidden in ("private-payload", "sensitive-text", "dummy-secret", "Authorization"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(events[-1]["event"], "disconnected")

    async def test_other_paths_rejected_without_logging_path(self):
        events = []
        async with observer(self.server_tls, 0, lambda event, **fields: events.append(fields)) as server:
            port = server.sockets[0].getsockname()[1]
            with self.assertRaises(InvalidStatus) as error:
                async with connect(f"wss://127.0.0.1:{port}/private-token", ssl=self.client_tls,
                                   server_hostname="localhost", proxy=None):
                    self.fail("Unexpected connection")
            self.assertEqual(error.exception.response.status_code, 404)
        self.assertEqual(events, [{"accepted_path": False}])
