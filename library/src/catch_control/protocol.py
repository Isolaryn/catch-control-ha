"""2CH framing derived from Configurator 2.10.15 / catalogue 20862.

Ordinary BLE messages are 255 bytes: opcode, padded data, then a CRC over
the first 253 bytes. The CRC uses the Modbus polynomial, transmitted high
byte first. This module exposes the verified identity, telemetry and
configuration read requests. Authenticated writes live in configuration.py.
"""

from dataclasses import dataclass
from construct import ConstructError

from .checksum import crc16
from .enums import ControlMode, ServerStatus, enum_name
from .schemas import (
    FRAME, FRAME_SIZE, PAYLOAD_SIZE, IDENTITY_PAYLOAD, TELEMETRY_PAYLOAD,
    CONFIGURATION_READ_PAYLOAD, TELEMETRY_FIELDS,
)

MODEL_2CH = 10004
SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
CHARACTERISTIC_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"
IDENTITY = 0
GET_CONFIGURATION = 1
LIVE_DATA = 3
GET_WIFI_SETTINGS = 7


class ProtocolError(ValueError):
    """A response cannot be interpreted safely."""


@dataclass(frozen=True)
class Identity:
    model: int
    serial: int
    firmware: int


def read_request(opcode: int) -> bytes:
    if opcode not in (IDENTITY, GET_CONFIGURATION, LIVE_DATA, GET_WIFI_SETTINGS):
        raise ValueError("Unsupported read opcode")
    return FRAME.build({"body": {"value": {"opcode": opcode, "payload": bytes(PAYLOAD_SIZE)}}})


def parse_frame(frame: bytes, opcode: int | None = None):
    try:
        body = FRAME.parse(frame).body.value
    except ConstructError as exc:
        raise ProtocolError(f"Invalid Catch frame: {exc}") from exc
    if opcode is not None and body.opcode != opcode:
        raise ProtocolError(f"Unexpected response opcode {body.opcode}")
    return body


def validate_frame(frame: bytes, opcode: int | None = None) -> None:
    parse_frame(frame, opcode)


def frame_opcode(frame: bytes) -> int:
    return parse_frame(frame).opcode


def _parse_payload(schema, frame, opcode):
    body = parse_frame(frame, opcode)
    try:
        return schema.parse(body.payload)
    except ConstructError as exc:
        raise ProtocolError(f"Invalid Catch payload: {exc}") from exc


def decode_identity(frame: bytes) -> Identity:
    data = _parse_payload(IDENTITY_PAYLOAD, frame, IDENTITY)
    return Identity(data.model, data.serial, data.firmware)


def decode_configuration(frame: bytes) -> dict:
    """Read settings without exposing the device's credential field."""
    data = _parse_payload(CONFIGURATION_READ_PAYLOAD, frame, GET_CONFIGURATION)
    if data.identity.model != MODEL_2CH:
        raise ProtocolError(f"Configuration layout for model {data.identity.model} is not supported")

    def plain(value):
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items() if not key.startswith('_')}
        if isinstance(value, list):
            return [plain(item) for item in value]
        return value

    result = plain(data)
    for override in result['overrides']:
        override['mode'] = enum_name(ControlMode, override['mode_raw'])
    return result


def _telemetry_result(data) -> dict:
    identity = data.identity
    if identity.model != MODEL_2CH:
        raise ProtocolError(f"Telemetry layout for model {identity.model} is not supported")

    def channel(measurements, energy):
        return {
            "current_a": measurements.current_deciamps / 10,
            "power_w": measurements.power_w,
            "power_factor": measurements.power_factor_hundredths / 100,
            "apparent_power_va": measurements.apparent_power_va,
            "reactive_power_var": measurements.reactive_power_var,
            "export_energy_raw_wh": energy.export_wh,
            "import_energy_raw_wh": energy.import_wh,
        }

    return {
        "model": identity.model, "serial": identity.serial, "firmware": identity.firmware,
        "voltage_v": data.voltage_decivolts / 10,
        "frequency_hz": data.frequency_millihertz / 1000,
        "channel_1": channel(data.channel_1, data.channel_1_energy),
        "channel_2": channel(data.channel_2, data.channel_2_energy),
        "runtime_minutes": data.runtime_minutes, "duty_raw": data.duty_raw,
        "control_mode_raw": data.control_mode_raw, "server_status_raw": data.server_status_raw,
        "control_mode": enum_name(ControlMode, data.control_mode_raw),
        "server_status": enum_name(ServerStatus, data.server_status_raw),
        "ip_address": data.ip_address,
        "wifi_rssi_dbm": data.wifi_rssi_dbm,
        "cloud_tethered_raw": data.cloud_tethered_raw,
    }


def decode_telemetry_payload(payload: bytes) -> dict:
    """Decode the 145-byte telemetry structure shared by BLE and WebSocket."""
    try:
        if len(payload) != TELEMETRY_FIELDS.sizeof():
            raise ProtocolError(
                f"Expected {TELEMETRY_FIELDS.sizeof()} telemetry bytes, received {len(payload)}"
            )
        data = TELEMETRY_FIELDS.parse(payload)
    except (ConstructError, TypeError) as exc:
        raise ProtocolError(f"Invalid Catch telemetry payload: {exc}") from exc
    return _telemetry_result(data)


def decode_telemetry(frame: bytes) -> dict:
    body = parse_frame(frame, LIVE_DATA)
    return decode_telemetry_payload(body.payload[:TELEMETRY_FIELDS.sizeof()])


class FrameBuffer:
    """Accumulate ATT fragments, retaining complete coalesced frames."""

    def __init__(self):
        self._data = bytearray()

    def clear(self):
        self._data.clear()

    def feed(self, data: bytes) -> list[bytes]:
        self._data.extend(data)
        frames = []
        while len(self._data) >= FRAME_SIZE:
            frame = bytes(self._data[:FRAME_SIZE])
            del self._data[:FRAME_SIZE]
            try:
                validate_frame(frame)
            except ProtocolError:
                self.clear()
                raise
            frames.append(frame)
        return frames
