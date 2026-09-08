"""Safe Bluetooth setup of the device's outbound local WebSocket endpoint."""

from copy import deepcopy
from dataclasses import dataclass, field
import re

from construct import Byte, Bytes, Int16sl, Int16ul, Struct, Terminated

from .configuration import ConfigurationConflict, WriteVerificationError
from .protocol import ProtocolError, parse_frame
from .schemas import FRAME, PAYLOAD_SIZE

GET_WIFI_SETTINGS = 7
SET_WIFI_SETTINGS = 8

_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,27}[A-Za-z0-9])?")

WIFI_SETTINGS_FIELDS = Struct(
    "ip_type" / Int16ul,
    "ip" / Bytes(16),
    "gateway" / Bytes(16),
    "dns" / Bytes(16),
    "subnet" / Bytes(16),
    "server1" / Bytes(30),
    "server1_port" / Int16sl,
    "server2" / Bytes(30),
    "server2_port" / Int16sl,
    "wifi_password" / Bytes(20),
    "security" / Byte,
    "ssid" / Bytes(33),
    "wifi_password2" / Bytes(44),
)
WIFI_SETTINGS_PAYLOAD = Struct(
    "settings" / WIFI_SETTINGS_FIELDS,
    "extension" / Bytes(PAYLOAD_SIZE - WIFI_SETTINGS_FIELDS.sizeof()),
    Terminated,
)


def _text(raw: bytes) -> str:
    try:
        return raw.split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError:
        raise ProtocolError("Wi-Fi settings contain a non-ASCII endpoint") from None


@dataclass(frozen=True)
class WifiServerSettings:
    primary_host: str
    primary_port: int
    secondary_host: str
    secondary_port: int


def decode_server_settings(frame: bytes) -> WifiServerSettings:
    payload = parse_frame(frame, GET_WIFI_SETTINGS).payload
    fields = WIFI_SETTINGS_PAYLOAD.parse(payload).settings
    return WifiServerSettings(
        _text(fields.server1),
        fields.server1_port,
        _text(fields.server2),
        fields.server2_port,
    )


@dataclass(frozen=True, repr=False)
class WifiServerPlan:
    before: WifiServerSettings
    after: WifiServerSettings
    _original_payload: bytes = field(repr=False)
    _updated_payload: bytes = field(repr=False)

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def summary(self):
        return {"changed": self.changed, "before": self.before, "after": self.after}

    def _packet(self) -> bytes:
        return FRAME.build({
            "body": {"value": {"opcode": SET_WIFI_SETTINGS, "payload": self._updated_payload}}
        })


def validate_websocket_endpoint(host: str, port: int) -> tuple[str, int]:
    """Validate and normalize the endpoint representation accepted by firmware 12718."""
    host = host.strip()
    if not _HOST.fullmatch(host) or len(host.encode("ascii")) > 29:
        raise ValueError("Server host must be a DNS name or IPv4 address of at most 29 ASCII characters")
    if type(port) is not int or not 1 <= port <= 32767:
        raise ValueError("Server port must be between 1 and 32767")
    return host, port


def plan_websocket_server(frame: bytes, host: str, port: int) -> WifiServerPlan:
    """Plan a primary and fallback endpoint change while preserving every other byte."""
    host, port = validate_websocket_endpoint(host, port)
    payload = parse_frame(frame, GET_WIFI_SETTINGS).payload
    original = WIFI_SETTINGS_PAYLOAD.parse(payload)
    if WIFI_SETTINGS_PAYLOAD.build(original) != payload:
        raise ProtocolError("Wi-Fi settings did not round-trip through the verified layout")
    if original.settings.ip_type not in (0, 1) or not _text(original.settings.ssid):
        raise ProtocolError("Unexpected Wi-Fi settings; refusing to prepare an endpoint change")
    updated = deepcopy(original)
    encoded = host.encode("ascii").ljust(30, b"\0")
    updated.settings.server1 = encoded
    updated.settings.server1_port = port
    updated.settings.server2 = encoded
    updated.settings.server2_port = port
    after_payload = WIFI_SETTINGS_PAYLOAD.build(updated)
    return WifiServerPlan(
        decode_server_settings(frame),
        WifiServerSettings(host, port, host, port),
        payload,
        after_payload,
    )
