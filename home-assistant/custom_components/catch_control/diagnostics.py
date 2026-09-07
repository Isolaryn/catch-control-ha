"""Deliberately exclude credentials, addresses, serials and raw packets."""
async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    telemetry = coordinator.data.get('telemetry', {}) if coordinator.data else {}
    return {
        'model': 10004,
        'firmware': coordinator.identity.firmware if coordinator.identity else None,
        'last_update_success': coordinator.last_update_success,
        'write_configured': bool(coordinator.password),
        'update_interval_seconds': coordinator.update_interval.total_seconds(),
        'control_mode': telemetry.get('control_mode'),
        'reported_server_status': telemetry.get('server_status'),
    }
