"""2CH display values verified against Configurator catalogue 20862."""

from enum import IntEnum


class ControlMode(IntEnum):
    DEFAULT = 0
    EXPORT = 1
    TURN_ON = 2
    TURN_OFF = 3
    TOP_UP = 4
    VOLTAGE = 5
    FREQUENCY = 6


class ServerStatus(IntEnum):
    NO_WIFI = 0
    WIFI = 1
    IP_ASSIGNED = 2
    IP_CONFLICT = 3
    SERVER_GOOD = 4


def enum_name(enum_type, value: int) -> str:
    """Keep future/unknown values readable without rejecting good telemetry."""
    try:
        return enum_type(value).name.lower()
    except ValueError:
        return f"unknown_{value}"
