"""Offline decoder for an already captured opcode-30 reply; no network/BLE I/O.

Layout cross-checked against Configurator 2.10.15 and firmware 12718.
Runtime capability flags still determine which metrics are meaningful.
"""
from construct import Byte, Bytes, Int8sl, Int16sl, Int16ul, Int32sl, Int32ul, Padded, Struct
from catch_control.protocol import parse_frame
from catch_control.schemas import PAYLOAD_SIZE

WIFI_HEALTH_FIELDS = Struct(
    'cap_tx_rate' / Byte,
    'cap_retry' / Byte,
    'cap_snr' / Byte,
    'cap_gw_rtt' / Byte,
    'cap_co_ssid' / Byte,
    'cap_app_disconnect' / Byte,
    'rssi' / Int8sl,
    'bssid' / Bytes(6),
    'bssid_changed' / Byte,
    'max_rate_mbps_x10' / Int16sl,
    'tx_rate_mbps_x10' / Int16sl,
    'snr_db_x10' / Int16sl,
    'gw_rtt_median_ms' / Int32sl,
    'gw_rtt_jitter_ms' / Int32sl,
    'co_ssid_count' / Byte,
    'wifi_disconnect_count' / Int16ul,
    'wss_disconnect_count' / Int16ul,
    'wss_send_fail_count' / Int16ul,
    'window_seconds' / Int16ul,
    'health_score' / Byte,
    'dominant_metric' / Byte,
    'collected_at_s' / Int32ul,
)
WIFI_HEALTH_PAYLOAD = Padded(PAYLOAD_SIZE, WIFI_HEALTH_FIELDS)


def decode_wifi_health(frame: bytes) -> dict:
    """Return diagnostics without the AP's identifying BSSID or raw packet.

    All-zero data is the app's unsupported/unavailable sentinel. A populated
    reply is not proof of current cloud access. Metric sentinel ranges and
    the collection clock's epoch are not yet verified, so retain raw values.
    """
    fields = WIFI_HEALTH_PAYLOAD.parse(parse_frame(frame, 30).payload)
    supported = any(any(value) if isinstance(value, bytes) else bool(value)
                    for key, value in fields.items() if not key.startswith('_'))
    if not supported:
        return {'supported': False}
    return {
        'supported': True,
        'capabilities_raw': {key.removeprefix('cap_'): value for key, value in fields.items() if key.startswith('cap_')},
        'rssi_dbm_raw': fields.rssi,
        'bssid_changed_raw': fields.bssid_changed,
        'max_rate_mbps_x10_raw': fields.max_rate_mbps_x10,
        'tx_rate_mbps_x10_raw': fields.tx_rate_mbps_x10,
        'snr_db_x10_raw': fields.snr_db_x10,
        'gateway_rtt_median_ms_raw': fields.gw_rtt_median_ms,
        'gateway_rtt_jitter_ms_raw': fields.gw_rtt_jitter_ms,
        'co_ssid_count_raw': fields.co_ssid_count,
        'wifi_disconnect_count': fields.wifi_disconnect_count,
        'wss_disconnect_count': fields.wss_disconnect_count,
        'wss_send_fail_count': fields.wss_send_fail_count,
        'window_seconds': fields.window_seconds,
        'health_score_raw': fields.health_score,
        'dominant_metric_raw': fields.dominant_metric,
        'collected_at_s_raw': fields.collected_at_s,
    }
