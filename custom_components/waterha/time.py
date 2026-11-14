"""Per-tap start time."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_START_TIME
from .entity import TapEntity, add_entities
from .hub import WaterHub, parse_time


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_entities(hass, entry, async_add_entities, [], lambda hub, tap_id: [StartTime(hub, tap_id)])


class StartTime(TapEntity, TimeEntity):
    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "start_time")

    @property
    def native_value(self) -> time | None:
        return parse_time(self.cfg.get(CONF_START_TIME))

    async def async_set_value(self, value: time) -> None:
        value = value.replace(second=0, microsecond=0)
        await self.hub.async_set_option(self.tap_id, CONF_START_TIME, value.isoformat())
