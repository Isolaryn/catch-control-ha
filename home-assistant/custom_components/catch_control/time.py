from datetime import time
from homeassistant.components.time import TimeEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import ScheduleEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(ScheduleTime(entry.runtime_data, slot, field) for slot in range(1, 5) for field in ('start', 'stop'))


class ScheduleTime(ScheduleEntity, TimeEntity):
    def __init__(self, coordinator, slot, field):
        super().__init__(coordinator, slot, field, field)
        self.field = field

    @property
    def native_value(self):
        if self.schedule is None:
            return None
        value = self.schedule[f'{self.field}_minutes']
        return time(value // 60, value % 60) if 0 <= value < 1440 else None

    async def async_set_value(self, value):
        if value.second or value.microsecond or value.tzinfo:
            raise HomeAssistantError('Catch schedules use whole minutes in device local time')
        await self.coordinator.async_edit_schedule(self.slot, **{f'{self.field}_minutes': value.hour * 60 + value.minute})
