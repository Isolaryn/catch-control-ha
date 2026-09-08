"""TLS listener for CATCH-initiated, read-only WebSocket sessions."""

import asyncio
from http import HTTPStatus
import json
import logging
from pathlib import Path
import ssl
import sys

from catch_control.wifi import CatchWebSocketSession
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


def _tls_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    cert = Path(cert_file)
    key = Path(key_file)
    if not cert.is_file() or not key.is_file():
        raise ValueError("Certificate and key must be readable files")
    if cert.stat().st_size > 1024 * 1024 or key.stat().st_size > 1024 * 1024:
        raise ValueError("Certificate or key file is unexpectedly large")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


async def listen(args):
    """Serve telemetry reads until interrupted or the requested count is met."""
    context = _tls_context(args.cert, args.key)
    finished = asyncio.Event()
    active = asyncio.Lock()
    samples = 0
    terminal_error = None

    # Suppress library handshake logs because they may include HTTP headers.
    logger = logging.getLogger("catch_control_cli.wifi_transport")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    def request(connection, incoming):
        if incoming.path != "/srwe":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")

    async def handler(connection):
        nonlocal samples, terminal_error
        if active.locked():
            await connection.close(1013, "Another CATCH connection is active")
            return
        async with active:
            session = CatchWebSocketSession(connection, timeout=args.timeout)
            try:
                while args.count is None or samples < args.count:
                    telemetry = await session.telemetry()
                    print(json.dumps(telemetry), flush=True)
                    samples += 1
                    if args.count is not None and samples >= args.count:
                        finished.set()
                        return
                    await asyncio.sleep(args.interval)
            except ConnectionClosed:
                return
            except BaseException as exc:
                terminal_error = exc
                finished.set()

    async with serve(
        handler, args.bind, args.port, ssl=context, process_request=request,
        origins=[None], compression=None, ping_interval=None, open_timeout=args.timeout,
        close_timeout=3, max_size=145, max_queue=1, logger=logger, server_header=None,
    ):
        print(json.dumps({"listening": args.bind, "port": args.port, "path": "/srwe"}),
              file=sys.stderr, flush=True)
        await finished.wait()
    if terminal_error is not None:
        raise terminal_error
