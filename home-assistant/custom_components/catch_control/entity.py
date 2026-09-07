from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


class CatchEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        serial = str(coordinator.entry.data['serial'])
        self._attr_unique_id = f'10004-{serial}-{key}'
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f'10004-{serial}')}, name=f'CATCH Control {serial}',
            manufacturer='CATCH Power', model='CATCH Control 2CH',
            sw_version=str(coordinator.identity.firmware),
        )


class ScheduleEntity(CatchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, slot, suffix, name):
        super().__init__(coordinator, f'schedule_{slot}_{suffix}', f'Schedule {slot} {name}')
        self.slot = slot

    @property
    def schedule(self):
        configuration = self.coordinator.data.get('configuration') if self.coordinator.data else None
        return configuration['overrides'][self.slot - 1] if configuration else None

    @property
    def available(self):
        return super().available and self.schedule is not None and bool(self.coordinator.password) and self.coordinator.identity.firmware == 12718
