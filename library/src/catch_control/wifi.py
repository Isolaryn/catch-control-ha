"""Protocol session for a CATCH-initiated local WebSocket connection."""

import asyncio
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Mapping

from .configuration import ConfigurationConflict, Schedule, WriteVerificationError
from .protocol import ProtocolError, decode_telemetry_payload
from .schemas import (
    SCHEDULE_OVERRIDE,
    TELEMETRY_FIELDS,
    WIFI_CONFIGURATION_READ_FIELDS,
    WIFI_CONFIGURATION_SCHEDULE_OFFSET,
    WIFI_CONFIGURATION_SCHEDULE_SIZE,
    WIFI_CONFIGURATION_SIZE,
)

CONFIGURATION_REQUEST = b"\x01"
SET_CONFIGURATION = 2
TELEMETRY_REQUEST = b"\x09"
TELEMETRY_MESSAGE_SIZE = TELEMETRY_FIELDS.sizeof()


def decode_websocket_telemetry(message: bytes) -> dict:
    """Decode the exact binary response to a WebSocket telemetry request."""
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise ProtocolError("Expected a binary WebSocket telemetry message")
    raw = bytes(message)
    if len(raw) != TELEMETRY_MESSAGE_SIZE:
        raise ProtocolError(
            f"Expected {TELEMETRY_MESSAGE_SIZE} WebSocket telemetry bytes, received {len(raw)}"
        )
    return decode_telemetry_payload(raw)


def _binary_message(message, expected_size: int, name: str) -> bytes:
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise ProtocolError(f"Expected a binary WebSocket {name} message")
    raw = bytes(message)
    if len(raw) != expected_size:
        raise ProtocolError(
            f"Expected {expected_size} WebSocket {name} bytes, received {len(raw)}"
        )
    return raw


def decode_websocket_configuration(message: bytes) -> dict:
    """Decode schedules from an exact GETCFG response without exposing credentials."""
    raw = _binary_message(message, WIFI_CONFIGURATION_SIZE, "configuration")
    fields = WIFI_CONFIGURATION_READ_FIELDS.parse(raw)
    return {
        "overrides": [
            {key: value for key, value in schedule.items() if not key.startswith("_")}
            for schedule in fields.overrides
        ]
    }


def _schedule_bytes(raw: bytes) -> tuple[bytes, ...]:
    fields = WIFI_CONFIGURATION_READ_FIELDS.parse(raw)
    return tuple(SCHEDULE_OVERRIDE.build(schedule) for schedule in fields.overrides)


@dataclass(frozen=True, repr=False)
class WifiSchedulePlan:
    """A single-record update tied to a fresh schedule snapshot."""

    slot: int
    before: Mapping[str, int]
    after: Mapping[str, int]
    _all_before: tuple[bytes, ...] = field(repr=False)
    _updated_record: bytes = field(repr=False)

    @property
    def changed(self):
        return self.before != self.after

    def summary(self):
        return {
            "slot": self.slot,
            "changed": self.changed,
            "before": dict(self.before),
            "after": dict(self.after),
        }


def plan_websocket_schedule(message: bytes, slot: int, schedule: Schedule) -> WifiSchedulePlan:
    """Prepare one validated 7-byte schedule record from a fresh GETCFG reply."""
    if type(slot) is not int or not 1 <= slot <= 4:
        raise ValueError("Schedule slot must be between 1 and 4")
    raw = _binary_message(message, WIFI_CONFIGURATION_SIZE, "configuration")
    fields = WIFI_CONFIGURATION_READ_FIELDS.parse(raw)
    current = fields.overrides[slot - 1]
    before = {key: value for key, value in current.items() if not key.startswith("_")}
    after = schedule.to_wire()
    if schedule.active and before != after:
        for index, other in enumerate(fields.overrides, start=1):
            if index == slot or not other.active_raw:
                continue
            if other.active_raw != 1 or not 0 <= other.start_minutes < other.stop_minutes <= 1439:
                raise ValueError(
                    f"Active slot {index} has an unverified window; refusing to introduce another active schedule"
                )
            if schedule.start_minutes <= other.stop_minutes and other.start_minutes <= schedule.stop_minutes:
                raise ValueError(f"Schedule overlaps active slot {index}; existing schedules were preserved")
    record = SCHEDULE_OVERRIDE.build(after)
    return WifiSchedulePlan(
        slot,
        MappingProxyType(before),
        MappingProxyType(after),
        _schedule_bytes(raw),
        record,
    )


class CatchWebSocketSession:
    """Issue serialized read requests on an accepted WebSocket connection.

    The supplied connection must provide async send() and recv() methods, such
    as websockets.asyncio.server.ServerConnection. CATCH initiates the network
    connection, so a caller normally creates this session inside a TLS server's
    connection handler.
    """

    def __init__(self, connection, timeout: float = 10):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be a finite positive number")
        self.connection = connection
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._failed = False

    async def telemetry(self) -> dict:
        message = await self._request(TELEMETRY_REQUEST)
        return decode_websocket_telemetry(message)

    async def configuration(self) -> dict:
        message = await self._request(CONFIGURATION_REQUEST)
        return decode_websocket_configuration(message)

    async def plan_schedule(self, slot: int, schedule: Schedule) -> WifiSchedulePlan:
        message = await self._request(CONFIGURATION_REQUEST)
        return plan_websocket_schedule(message, slot, schedule)

    async def apply_schedule(self, plan: WifiSchedulePlan) -> dict:
        async with self._lock:
            current = await self._request_unlocked(CONFIGURATION_REQUEST)
            if _schedule_bytes(_binary_message(current, WIFI_CONFIGURATION_SIZE, "configuration")) != plan._all_before:
                raise ConfigurationConflict("Schedules changed after planning; no write was sent")
            if not plan.changed:
                return {**plan.summary(), "verified": True, "written": False}
            offset = WIFI_CONFIGURATION_SCHEDULE_OFFSET + (plan.slot - 1) * WIFI_CONFIGURATION_SCHEDULE_SIZE
            response = await self._request_unlocked(
                bytes((SET_CONFIGURATION, offset, WIFI_CONFIGURATION_SCHEDULE_SIZE)) + plan._updated_record
            )
            if _binary_message(response, 1, "write acknowledgement") != b"\x01":
                self._failed = True
                raise WriteVerificationError("Device did not acknowledge the schedule write")
            readback = await self._request_unlocked(CONFIGURATION_REQUEST)
            expected = list(plan._all_before)
            expected[plan.slot - 1] = plan._updated_record
            if _schedule_bytes(_binary_message(readback, WIFI_CONFIGURATION_SIZE, "configuration")) != tuple(expected):
                self._failed = True
                raise WriteVerificationError("Schedule readback differs from the requested change")
            return {**plan.summary(), "verified": True, "written": True}

    async def _request(self, request: bytes):
        async with self._lock:
            return await self._request_unlocked(request)

    async def _request_unlocked(self, request: bytes):
        """Send one request while the caller owns ``_lock`` when required."""
        if self._failed:
            raise ConnectionError("WebSocket session cannot be reused after a failed request")
        try:
            async with asyncio.timeout(self.timeout):
                await self.connection.send(request)
                return await self.connection.recv()
        except BaseException:
            self._failed = True
            raise
