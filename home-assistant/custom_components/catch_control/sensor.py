from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory
from .entity import CatchEntity

# path, name, unit, device class. Energy counters are intentionally omitted:
# their signed/reset semantics have not been validated for HA statistics.
SENSORS = [
    (('voltage_v',), 'Voltage', 'V', SensorDeviceClass.VOLTAGE),
    (('frequency_hz',), 'Frequency', 'Hz', SensorDeviceClass.FREQUENCY),
    (('control_mode',), 'Control mode', None, None),
    (('server_status',), 'Reported server status', None, None),
    (('wifi_rssi_dbm',), 'Wi-Fi signal', 'dBm', SensorDeviceClass.SIGNAL_STRENGTH),
    (('runtime_minutes',), 'Reported runtime', 'min', SensorDeviceClass.DURATION),
]
for channel in (1, 2):
    for key, label, unit, device_class in [
        ('current_a', 'current', 'A', SensorDeviceClass.CURRENT),
        ('power_w', 'power', 'W', SensorDeviceClass.POWER),
        ('apparent_power_va', 'apparent power', 'VA', SensorDeviceClass.APPARENT_POWER),
        ('reactive_power_var', 'reactive power', 'var', SensorDeviceClass.REACTIVE_POWER),
        ('power_factor', 'power factor', None, None),
    ]:
        SENSORS.append(((f'channel_{channel}', key), f'Channel {channel} {label}', unit, device_class))


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(CatchSensor(entry.runtime_data, *description) for description in SENSORS)


class CatchSensor(CatchEntity, SensorEntity):
    def __init__(self, coordinator, path, name, unit, device_class):
        super().__init__(coordinator, '_'.join(path), name)
        self.path = path
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT if unit and device_class != SensorDeviceClass.DURATION else None
        if path[0] in ('server_status', 'wifi_rssi_dbm', 'runtime_minutes'):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        value = self.coordinator.data['telemetry']
        for key in self.path:
            value = value[key]
        return value
