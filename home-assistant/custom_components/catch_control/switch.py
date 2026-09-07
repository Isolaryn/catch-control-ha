from datetime import time
import re
import voluptuous as vol
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers import config_validation as cv, entity_platform
from ._vendor.catch_control.enums import ControlMode
from .entity import ScheduleEntity


def schedule_minutes(value):
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise vol.Invalid('Schedule times require HH:MM')
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        raise vol.Invalid('Schedule time must be between 00:00 and 23:59') from None
    if parsed.second or parsed.microsecond or parsed.tzinfo:
        raise vol.Invalid('Schedule times require HH:MM without seconds or timezone')
    return parsed.hour * 60 + parsed.minute


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(ScheduleSwitch(entry.runtime_data, slot) for slot in range(1, 5))
    entity_platform.async_get_current_platform().async_register_entity_service(
        'set_schedule', {
            vol.Required('active'): cv.boolean,
            vol.Required('mode'): vol.In([mode.name.lower() for mode in ControlMode]),
            vol.Required('start'): vol.All(str, schedule_minutes),
            vol.Required('stop'): vol.All(str, schedule_minutes),
        }, 'async_set_schedule',
    )


class ScheduleSwitch(ScheduleEntity, SwitchEntity):
    def __init__(self, coordinator, slot):
        super().__init__(coordinator, slot, 'enabled', 'enabled')

    @property
    def is_on(self):
        return bool(self.schedule['active_raw']) if self.schedule is not None else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_edit_schedule(self.slot, active=True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_edit_schedule(self.slot, active=False)

    async def async_set_schedule(self, active, mode, start, stop):
        await self.coordinator.async_edit_schedule(self.slot, active=active, mode=ControlMode[mode.upper()], start_minutes=start, stop_minutes=stop)
