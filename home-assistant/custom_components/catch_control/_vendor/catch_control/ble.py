"""Bluetooth transport for the owner's CATCH Control 2CH."""

import asyncio
from contextlib import suppress
import math
from bleak import BleakClient, BleakScanner
from .configuration import (
    Schedule, SchedulePlan, WriteVerificationError, plan_schedule as make_schedule_plan,
)
from .protocol import (
    CHARACTERISTIC_UUID, SERVICE_UUID, MODEL_2CH, IDENTITY, LIVE_DATA, GET_CONFIGURATION,
    FrameBuffer, ProtocolError, frame_opcode, decode_identity, decode_telemetry,
    decode_configuration, read_request,
)


def advertised_model(advertisement) -> int | None:
    # Bleak separates the first two manufacturer bytes as a little-endian
    # company identifier. Configurator interprets all four as a big-endian ID.
    for company, payload in advertisement.manufacturer_data.items():
        raw = company.to_bytes(2, "little") + payload
        if len(raw) == 4 and int.from_bytes(raw, "big") == MODEL_2CH:
            return MODEL_2CH
    return None


async def discover(timeout: float = 10):
    results = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [(device, advert) for device, advert in results.values()
            if advertised_model(advert) == MODEL_2CH]


class CatchClient:
    def __init__(self, device, timeout: float = 10, *, connector=None):
        """Create a client; connector may supply an already-connected Bleak client.

        The async connector receives (device, disconnected_callback, timeout).
        This allows hosts such as Home Assistant to select adapters/proxies.
        The resulting connection is owned and closed by this context manager.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be a finite positive number")
        self.timeout = timeout
        self._buffer = FrameBuffer()
        self._pending = None
        self._expected = None
        self._lock = asyncio.Lock()
        self._configuration_lock = asyncio.Lock()
        self._device = device
        self._connector = connector
        self._client = None if connector else BleakClient(device, timeout=timeout, disconnected_callback=self._disconnected)
        self._characteristic = None
        self.identity = None

    def _disconnected(self, _client):
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(ConnectionError("Catch disconnected"))

    def _notification(self, _sender, data):
        try:
            frames = self._buffer.feed(bytes(data))
        except ProtocolError as exc:
            if self._pending is not None and not self._pending.done():
                self._pending.set_exception(exc)
            return
        for frame in frames:
            if (self._pending is not None and not self._pending.done()
                    and frame_opcode(frame) == self._expected):
                self._pending.set_result(frame)

    async def __aenter__(self):
        try:
            async with asyncio.timeout(self.timeout):
                if self._connector:
                    self._client = await self._connector(self._device, self._disconnected, self.timeout)
                else:
                    await self._client.connect()
            service = self._client.services.get_service(SERVICE_UUID)
            self._characteristic = service.get_characteristic(CHARACTERISTIC_UUID) if service else None
            if self._characteristic is None:
                raise ProtocolError("Expected Catch BLE service/characteristic not present")
            if not {"notify", "write-without-response"}.issubset(self._characteristic.properties):
                raise ProtocolError("Catch characteristic lacks expected notification/write properties")
            await self._client.start_notify(self._characteristic, self._notification)
            self.identity = decode_identity(await self._request(IDENTITY))
            if self.identity.model != MODEL_2CH:
                raise ProtocolError(f"Unsupported model {self.identity.model}")
            return self
        except BaseException:
            if self._client is not None:
                with suppress(Exception):
                    await self._client.disconnect()
            raise

    async def __aexit__(self, exc_type, *_):
        if exc_type is None:
            await self._client.disconnect()
        else:
            with suppress(Exception):
                await self._client.disconnect()

    async def _request(self, opcode: int) -> bytes:
        packet = read_request(opcode)
        async with self._lock:
            if not self._client.is_connected:
                raise ConnectionError("Catch is not connected")
            self._buffer.clear()
            self._expected = opcode
            future = asyncio.get_running_loop().create_future()
            self._pending = future
            try:
                async with asyncio.timeout(self.timeout):
                    await self._write_chunks(packet)
                    response = await future
                await asyncio.sleep(0.1)
                return response
            except BaseException:
                # Replies have no transaction ID. Disconnect after failure so
                # a delayed response cannot be mistaken for the next request.
                self._pending = None
                with suppress(Exception):
                    await self._client.disconnect()
                raise
            finally:
                self._pending = None
                self._expected = None
                if not future.done():
                    future.cancel()
                else:
                    if not future.cancelled():
                        future.exception()

    async def _write_chunks(self, packet):
        size = min(255, self._characteristic.max_write_without_response_size)
        if size <= 0:
            raise ProtocolError("Invalid negotiated write size")
        for offset in range(0, len(packet), size):
            await self._client.write_gatt_char(self._characteristic, packet[offset:offset+size], response=False)
            if offset + size < len(packet):
                await asyncio.sleep(0.04)

    async def _write_configuration(self, packet):
        # Match the settings page: send, settle, GET_CONFIG and verify.
        # An opcode-2 notification is not treated as proof of a successful save.
        async with self._lock:
            if not self._client.is_connected:
                raise ConnectionError("Catch is not connected")
            try:
                async with asyncio.timeout(self.timeout):
                    await self._write_chunks(packet)
                await asyncio.sleep(2)
            except BaseException:
                with suppress(Exception):
                    await self._client.disconnect()
                raise

    async def plan_schedule(self, slot: int, schedule: Schedule, *, password: str) -> SchedulePlan:
        frame = await self._request(GET_CONFIGURATION)
        data = decode_configuration(frame)
        if (data['identity']['serial'], data['identity']['firmware']) != (self.identity.serial, self.identity.firmware):
            raise ProtocolError("Configuration identity differs from the connected device")
        return make_schedule_plan(frame, password, slot, schedule)

    async def authenticate(self, *, password: str) -> None:
        """Validate a supplied credential without changing any setting."""
        from .configuration import _authenticated_snapshot
        frame = await self._request(GET_CONFIGURATION)
        snapshot = _authenticated_snapshot(frame, password)
        if snapshot.fields.identity.serial != self.identity.serial:
            raise ProtocolError("Configuration identity differs from the connected device")

    async def apply_schedule(self, plan: SchedulePlan, *, password: str) -> dict:
        async with self._configuration_lock:
            plan._check_before(await self._request(GET_CONFIGURATION), password)
            if not plan.changed:
                return {**plan.summary(), 'verified': True, 'written': False}
            try:
                await self._write_configuration(plan._packet())
                plan._check_after(await self._request(GET_CONFIGURATION), password)
            except Exception:
                raise WriteVerificationError(
                    "Save could not be fully verified and may have taken effect; read configuration before retrying"
                ) from None
            return {**plan.summary(), 'verified': True, 'written': True}

    async def telemetry(self):
        data = decode_telemetry(await self._request(LIVE_DATA))
        if (data['serial'], data['firmware']) != (self.identity.serial, self.identity.firmware):
            raise ProtocolError("Telemetry identity differs from the connected device")
        return data

    async def configuration(self):
        data = decode_configuration(await self._request(GET_CONFIGURATION))
        identity = data['identity']
        if (identity['serial'], identity['firmware']) != (self.identity.serial, self.identity.firmware):
            raise ProtocolError("Configuration identity differs from the connected device")
        return data
