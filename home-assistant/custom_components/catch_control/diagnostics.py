"""Deliberately exclude credentials, addresses, serials and raw packets."""
async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    telemetry = coordinator.data.get('telemetry', {}) if coordinator.data else {}
    return {
        'model': 10004,
        'firmware': coordinator.identity.firmware if coordinator.identity else None,
        'last_update_success': coordinator.last_update_success,
        'last_read_stage': coordinator.last_read_stage,
        'configuration_available': bool(coordinator.data and coordinator.data.get('configuration')),
        'configuration_error': coordinator.configuration_error,
        'transport': coordinator.transport,
        'write_configured': coordinator.can_write,
        'update_interval_seconds': coordinator.update_interval.total_seconds(),
        'control_mode': telemetry.get('control_mode'),
        'reported_server_status': telemetry.get('server_status'),
    }
