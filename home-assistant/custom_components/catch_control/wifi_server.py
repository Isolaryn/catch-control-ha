"""Private TLS WebSocket listener for CATCH-initiated local connections."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import ssl
from tempfile import NamedTemporaryFile

from aiohttp import WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ._vendor.catch_control.wifi import CatchWebSocketSession


def _write_private(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def ensure_certificate(directory: Path) -> tuple[Path, Path]:
    """Create one persistent ten-year RSA certificate for device compatibility."""
    certificate_path = directory / 'server-cert.pem'
    key_path = directory / 'server-key.pem'
    if certificate_path.is_file() and key_path.is_file():
        os.chmod(directory, 0o700)
        os.chmod(key_path, 0o600)
        return certificate_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CATCH Control local server')])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write_private(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        0o600,
    )
    _write_private(certificate_path, certificate.public_bytes(serialization.Encoding.PEM), 0o600)
    return certificate_path, key_path


@dataclass
class _Closed:
    error: Exception


class _AioWebSocketConnection:
    def __init__(self, socket: web.WebSocketResponse):
        self.socket = socket
        self.messages = asyncio.Queue(maxsize=2)

    async def send(self, data: bytes):
        await self.socket.send_bytes(data)

    async def recv(self):
        item = await self.messages.get()
        if isinstance(item, _Closed):
            raise item.error
        return item

    async def close(self):
        await self.socket.close()


class CatchWifiServer:
    """Own a dedicated local listener and the device's current protocol session."""

    def __init__(self, hass, bind_host: str, port: int):
        self.hass = hass
        self.bind_host = bind_host
        self.port = port
        self._runner = None
        self._site = None
        self._connection = None
        self._session = None
        self._connected = asyncio.Event()
        self._connection_lock = asyncio.Lock()

    async def async_start(self):
        directory = Path(self.hass.config.path('.storage', 'catch_control'))
        certificate_path, key_path = await self.hass.async_add_executor_job(ensure_certificate, directory)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate_path, key_path)
        application = web.Application()
        application.router.add_get('/srwe', self._async_websocket)
        self._runner = web.AppRunner(application, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.bind_host, self.port, ssl_context=context)
        await self._site.start()

    async def _async_websocket(self, request):
        socket = web.WebSocketResponse(autoping=True, heartbeat=None, compress=False, max_msg_size=1024)
        await socket.prepare(request)
        connection = _AioWebSocketConnection(socket)
        session = CatchWebSocketSession(connection, timeout=10)
        async with self._connection_lock:
            previous = self._connection
            self._connection = connection
            self._session = session
            self._connected.set()
        if previous is not None:
            await previous.close()
        error = ConnectionError('CATCH WebSocket connection closed')
        try:
            async for message in socket:
                if message.type == WSMsgType.BINARY:
                    await connection.messages.put(bytes(message.data))
                else:
                    error = ConnectionError('CATCH sent a non-binary WebSocket message or closed')
                    break
        except Exception:
            error = ConnectionError('CATCH WebSocket receive failed')
        finally:
            if connection.messages.empty():
                await connection.messages.put(_Closed(error))
            async with self._connection_lock:
                if self._connection is connection:
                    self._connection = None
                    self._session = None
                    self._connected.clear()
        return socket

    async def async_session(self, timeout: float = 45) -> CatchWebSocketSession:
        async with asyncio.timeout(timeout):
            while True:
                await self._connected.wait()
                async with self._connection_lock:
                    if self._session is not None:
                        return self._session

    async def async_discard(self, session):
        async with self._connection_lock:
            if self._session is not session:
                return
            connection = self._connection
            self._connection = None
            self._session = None
            self._connected.clear()
        if connection is not None:
            await connection.close()

    async def async_stop(self):
        async with self._connection_lock:
            connection = self._connection
            self._connection = None
            self._session = None
            self._connected.clear()
        if connection is not None:
            await connection.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
