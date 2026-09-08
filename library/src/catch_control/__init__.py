"""Local CATCH Control 2CH reads and authenticated schedule edits."""

from .protocol import Identity, ProtocolError, decode_configuration, decode_identity, decode_telemetry
from .configuration import Schedule, SchedulePlan
from .network import WifiServerPlan, WifiServerSettings
from .enums import ControlMode, ServerStatus
from .wifi import (
    CatchWebSocketSession, WifiSchedulePlan, decode_websocket_configuration,
    decode_websocket_telemetry,
)

__all__ = ["Identity", "ProtocolError", "decode_identity", "decode_telemetry",
           "decode_configuration", "Schedule", "SchedulePlan", "ControlMode", "ServerStatus",
           "CatchWebSocketSession", "WifiSchedulePlan", "decode_websocket_configuration",
           "decode_websocket_telemetry", "WifiServerPlan", "WifiServerSettings"]
