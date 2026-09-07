from homeassistant.components.select import SelectEntity
from ._vendor.catch_control.enums import ControlMode
from .entity import ScheduleEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(ScheduleMode(entry.runtime_data, slot) for slot in range(1, 5))


class ScheduleMode(ScheduleEntity, SelectEntity):
    _attr_options = [mode.name.lower() for mode in ControlMode]

    def __init__(self, coordinator, slot):
        super().__init__(coordinator, slot, 'mode', 'mode')

    @property
    def current_option(self):
        try:
            return ControlMode(self.schedule['mode_raw']).name.lower()
        except ValueError:
            return None

    async def async_select_option(self, option):
        await self.coordinator.async_edit_schedule(self.slot, mode=ControlMode[option.upper()])
