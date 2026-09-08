"""Declarative wire layouts from Configurator 2.10.15, catalogue 20862.

Fields are packed in declaration order, without native alignment. Scalars
are little-endian except the frame CRC, which is transmitted high byte first.
Payload padding is intentionally opaque: later firmware may add fields there.
"""

from construct import (
    Array, Byte, Bytes, Checksum, Error, IfThenElse, Int8sl, Int16sl, Int16ul, Int16ub,
    Int32sl, Padded, PaddedString, Padding, RawCopy, Struct, Terminated, this,
)

from .checksum import crc16

PAYLOAD_SIZE = 252
FRAME_SIZE = 1 + PAYLOAD_SIZE + 2

FRAME = Struct(
    "body" / RawCopy(Struct(
        "opcode" / Byte,
        "payload" / Bytes(PAYLOAD_SIZE),
    )),
    "crc" / Checksum(Int16ub, crc16, this.body.data),
    Terminated,
)

IDENTITY_FIELDS = Struct(
    "model" / Int16ul,
    "serial" / Int16ul,
    "firmware" / Int16ul,
)
IDENTITY_PAYLOAD = Padded(PAYLOAD_SIZE, IDENTITY_FIELDS)

CHANNEL_MEASUREMENTS = Struct(
    "current_deciamps" / Int16ul,
    "power_w" / Int32sl,
    "power_factor_hundredths" / Int16sl,
    "apparent_power_va" / Int32sl,
    "reactive_power_var" / Int32sl,
)
CHANNEL_ENERGY = Struct(
    "export_wh" / Int32sl,
    "import_wh" / Int32sl,
)
DEVICE_TIME = Struct(
    "second" / Int16sl,
    "minute" / Int16sl,
    "hour" / Int16sl,
    "day" / Int16sl,
    "month_raw" / Int16sl,
    "year_raw" / Int16sl,
    "weekday" / Int16sl,
    "yearday" / Int16sl,
    "is_dst" / Int16sl,
)
TELEMETRY_FIELDS = Struct(
    "voltage_decivolts" / Int16ul,
    "channel_1" / CHANNEL_MEASUREMENTS,
    "frequency_millihertz" / Int16ul,
    "channel_1_energy" / CHANNEL_ENERGY,
    "channel_2" / CHANNEL_MEASUREMENTS,
    "channel_2_energy" / CHANNEL_ENERGY,
    "runtime_minutes" / Int16ul,
    "duty_raw" / Byte,
    "control_mode_raw" / Byte,
    "inverter_lock_raw" / Byte,
    "rs485_signal_found_raw" / Int16ul,
    "rs485_received_packets" / Int16ul,
    "identity" / IDENTITY_FIELDS,
    "device_time" / DEVICE_TIME,
    "server_status_raw" / Int16ul,
    "ip_address" / PaddedString(16, "ascii"),
    "server_ip_address" / PaddedString(16, "ascii"),
    "cloud_power_w" / Int32sl,
    "cloud_tethered_raw" / Byte,
    "channels_chosen_raw" / Array(10, Byte),
    "tether_message_count" / Int16ul,
    "rs485_crc_errors" / Int16ul,
    "rs485_timeouts" / Int16ul,
    "rs485_missed_messages" / Int16ul,
    "rs485_other_messages" / Int16ul,
    "wifi_rssi_dbm" / Int8sl,
)
TELEMETRY_PAYLOAD = Padded(PAYLOAD_SIZE, TELEMETRY_FIELDS)

# Read-only projection. Credential storage is consumed but never returned.
# This layout must not be used to build configuration writes: omitted fields
# would be zeroed, and writes require a separately verified authentication flow.
SCHEDULE_OVERRIDE = Struct(
    "active_raw" / Byte,
    "mode_raw" / Int16ul,
    "start_minutes" / Int16ul,
    "stop_minutes" / Int16ul,
)


def threshold_control(threshold_type):
    return Struct(
        "minimum_on_minutes" / Int16ul,
        "above_threshold" / threshold_type,
        "above_minutes" / Int16ul,
        "below_threshold" / threshold_type,
        "below_minutes" / Int16ul,
    )


def control_configuration_prefix(credential_field):
    return Struct(
        "device_time" / DEVICE_TIME,
        credential_field,
        "ct_ratio" / Int16ul,
        "frequency" / threshold_control(Int16ul),
        "voltage" / threshold_control(Int16ul),
        "export" / threshold_control(Int32sl),
        "modbus_device_id" / Int16ul,
        "modbus_baud" / Int16ul,
        "modbus_stopbits_raw" / Byte,
        "modbus_parity_raw" / Byte,
    )


def control_configuration_fields(credential_field):
    return Struct(
        *control_configuration_prefix(credential_field).subcons,
        "overrides" / Array(4, SCHEDULE_OVERRIDE),
        "meter_type_raw" / Int32sl,
        "meter_export_limit" / Int32sl,
        "clock_calibration_raw" / Int8sl,
        "cloud_tethered_raw" / Byte,
        "cloud_tether_offline_mode_raw" / Byte,
        "long_power_raw" / Byte,
        "static_export_limit_defined_raw" / Int16ul,
        "static_export_limit" / Int32sl,
        "channel_1_purpose_raw" / Byte,
        "channel_2_purpose_raw" / Byte,
        "channel_1_reversed_raw" / Byte,
        "channel_2_reversed_raw" / Byte,
        "always_one_raw" / Byte,
        "update_time_raw" / Byte,
    )


def configuration_fields(credential_field):
    return Struct(
        "identity" / IDENTITY_FIELDS,
        "_dummy" / Byte,
        *control_configuration_fields(credential_field).subcons,
    )

CONFIGURATION_READ_FIELDS = configuration_fields(Padding(16))
CONFIGURATION_READ_PAYLOAD = IfThenElse(
    this._parsing, Padded(PAYLOAD_SIZE, CONFIGURATION_READ_FIELDS), Error,
)

# The WebSocket GETCFG response is the device's internal 128-byte control
# structure. Credentials are consumed without being exposed to callers.
WIFI_CONFIGURATION_SIZE = 128
WIFI_CONFIGURATION_SCHEDULE_OFFSET = control_configuration_prefix(Padding(16)).sizeof()
WIFI_CONFIGURATION_SCHEDULE_SIZE = SCHEDULE_OVERRIDE.sizeof()
WIFI_CONFIGURATION_READ_FIELDS = Struct(
    *control_configuration_fields(Padding(16)).subcons,
    Terminated,
)
