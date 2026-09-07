# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==17.0.1"]
# ///
"""Temporary TLS WebSocket observer. No application replies or packet logging."""
import argparse
import asyncio
from datetime import datetime, timezone
from http import HTTPStatus
import json
import logging
import ssl

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


def emit(event, **fields):
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                      "event": event, **fields}), flush=True)


def tls_context(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


def observer(context, port=8443, report=emit):
    """Bind only loopback; an explicitly configured SSH forward provides access."""
    # Library exceptions/debug output may contain HTTP headers or close reasons.
    logger = logging.getLogger("catch.passive_wss.transport")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False

    def request(connection, request):
        accepted = request.path == "/srwe"
        report("http_request", accepted_path=accepted)
        if not accepted:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")

    async def receive(connection):
        transport_tls = connection.transport.get_extra_info("ssl_object")
        report("connected", tls_version=transport_tls.version())
        count = 0
        total = 0
        try:
            async for message in connection:
                size = len(message) if isinstance(message, bytes) else len(message.encode("utf-8"))
                count += 1
                total += size
                report("message", number=count,
                       kind="binary" if isinstance(message, bytes) else "text", bytes=size)
        except ConnectionClosed:
            pass
        finally:
            report("disconnected", messages=count, bytes=total,
                   close_code=connection.close_code)

    return serve(receive, "127.0.0.1", port, ssl=context,
                 process_request=request, compression=None,
                 ping_interval=None, close_timeout=3, open_timeout=10,
                 max_size=65536, max_queue=4, logger=logger, server_header=None)


async def run(args):
    context = tls_context(args.cert, args.key)
    async with observer(context, args.port):
        emit("listening", bind="127.0.0.1", port=args.port, duration_seconds=args.duration)
        await asyncio.sleep(args.duration)
    emit("stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True, help="PEM certificate chain for the endpoint hostname")
    parser.add_argument("--key", required=True, help="PEM private key; keep in ignored .secrets/")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--duration", type=int, default=600, help="Stop after this many seconds")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.duration <= 3600:
        parser.error("port must be 1024..65535 and duration 1..3600 seconds")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        emit("stopped")


if __name__ == "__main__":
    main()
