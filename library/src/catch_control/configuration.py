"""Authenticated schedule edits that preserve the rest of a 2CH configuration.

The supplied credential is required to match before any write is built. The
device's credential is never returned or used as a replacement for user input.
Only firmware 12718 is supported for writes until other layouts are verified.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from construct import Bytes, Const, ConstructError, Struct, Terminated

from .enums import ControlMode
from .protocol import GET_CONFIGURATION, MODEL_2CH, ProtocolError, parse_frame
from .schemas import FRAME, PAYLOAD_SIZE, configuration_fields

SET_CONFIGURATION = 2
WRITE_FIRMWARE = 12718


class AuthenticationError(ValueError):
    pass


class ConfigurationConflict(RuntimeError):
    pass


class WriteVerificationError(RuntimeError):
    """A write may have happened, but the expected settings were not confirmed."""


@dataclass(frozen=True)
class Schedule:
    active: bool
    mode: ControlMode
    start_minutes: int
    stop_minutes: int

    def __post_init__(self):
        if type(self.active) is not bool:
            raise ValueError("Schedule active must be a bool")
        object.__setattr__(self, 'mode', ControlMode(self.mode))
        if any(type(x) is not int or not 0 <= x <= 1439
               for x in (self.start_minutes, self.stop_minutes)):
            raise ValueError("Schedule times must be whole minutes from 0 to 1439")
        if self.active and (self.mode == ControlMode.DEFAULT or self.start_minutes >= self.stop_minutes):
            raise ValueError("An active schedule needs a mode and a start before its stop; overnight windows are not yet verified")

    def to_wire(self):
        return dict(active_raw=int(self.active), mode_raw=int(self.mode),
                    start_minutes=self.start_minutes, stop_minutes=self.stop_minutes)


@dataclass(repr=False)
class _Snapshot:
    schema: object = field(repr=False)
    fields: object = field(repr=False)

    def stable_fields(self):
        def plain(value):
            if isinstance(value, dict):
                return {key: plain(item) for key, item in value.items() if key != '_io'}
            if isinstance(value, list):
                return [plain(item) for item in value]
            return value
        data = plain(self.fields)
        # Readback naturally contains a later clock. update_time remains zero.
        data.pop('device_time')
        return data


def _authenticated_snapshot(frame: bytes, password: str) -> _Snapshot:
    try:
        encoded = password.encode('ascii')
    except UnicodeEncodeError:
        raise AuthenticationError("Device password must be ASCII") from None
    if not 1 <= len(encoded) <= 16 or b'\0' in encoded:
        raise AuthenticationError("Device password must contain 1–16 ASCII bytes without NUL")
    body = parse_frame(frame, GET_CONFIGURATION)
    fields = configuration_fields(Const(encoded.ljust(16, b'\0')))
    schema = Struct(*fields.subcons,
                    '_extension' / Bytes(PAYLOAD_SIZE - fields.sizeof()), Terminated)
    try:
        data = schema.parse(body.payload)
    except ConstructError:
        # ConstError includes actual bytes. Do not expose that exception or chain.
        raise AuthenticationError("Device password did not match; no write was prepared") from None
    if data.identity.model != MODEL_2CH or data.identity.firmware != WRITE_FIRMWARE:
        raise ProtocolError("Configuration writes are supported only for 2CH firmware 12718")
    if data.update_time_raw != 0 or data.always_one_raw != 1:
        raise ProtocolError("Unexpected configuration flags; refusing to write")
    return _Snapshot(schema, data)


@dataclass(frozen=True, repr=False)
class SchedulePlan:
    slot: int
    before: Mapping[str, int]
    after: Mapping[str, int]
    _original: _Snapshot = field(repr=False)
    _updated: _Snapshot = field(repr=False)

    @property
    def changed(self):
        return self.before != self.after

    def summary(self):
        return {'slot': self.slot, 'changed': self.changed,
                'before': dict(self.before), 'after': dict(self.after)}

    def _packet(self):
        return FRAME.build({'body': {'value': {
            'opcode': SET_CONFIGURATION,
            'payload': self._updated.schema.build(self._updated.fields),
        }}})

    def _check_before(self, frame, password):
        current = _authenticated_snapshot(frame, password)
        if current.stable_fields() != self._original.stable_fields():
            raise ConfigurationConflict("Configuration changed after planning; no write was sent")

    def _check_after(self, frame, password):
        current = _authenticated_snapshot(frame, password)
        if current.stable_fields() != self._updated.stable_fields():
            raise WriteVerificationError("Configuration readback differs from the requested change; inspect settings before retrying")


def plan_schedule(frame: bytes, password: str, slot: int, schedule: Schedule) -> SchedulePlan:
    if type(slot) is not int or not 1 <= slot <= 4:
        raise ValueError("Schedule slot must be between 1 and 4")
    original = _authenticated_snapshot(frame, password)
    updated = _Snapshot(original.schema, deepcopy(original.fields))
    before = {key: value for key, value in original.fields.overrides[slot - 1].items()
              if not key.startswith('_')}
    after = schedule.to_wire()
    if schedule.active and before != after:
        for index, other in enumerate(original.fields.overrides, start=1):
            if index == slot or not other.active_raw:
                continue
            if other.active_raw != 1 or not 0 <= other.start_minutes < other.stop_minutes <= 1439:
                raise ValueError(f"Active slot {index} has an unverified window; refusing to introduce another active schedule")
            # Treat touching endpoints as overlap until inclusivity is verified.
            if schedule.start_minutes <= other.stop_minutes and other.start_minutes <= schedule.stop_minutes:
                raise ValueError(f"Schedule overlaps active slot {index}; existing schedules were preserved")
    updated.fields.overrides[slot - 1] = dict(after)
    return SchedulePlan(slot, MappingProxyType(before), MappingProxyType(after), original, updated)
