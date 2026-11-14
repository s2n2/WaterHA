"""Per-tap slot times (two daily slots)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_SLOT_TIMES, SLOTS
from .entity import TapEntity, add_entities
from .hub import WaterHub, parse_time


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_entities(
        hass,
        entry,
        async_add_entities,
        [],
        lambda hub, tap_id: [SlotTime(hub, tap_id, n, key) for n, key, _on, _d in SLOTS],
    )


class SlotTime(TapEntity, TimeEntity):
    def __init__(self, hub: WaterHub, tap_id: str, slot: int, key: str) -> None:
        # Slot 1 keeps the 0.2 key (start_time) so its entity id is unchanged.
        super().__init__(hub, tap_id, key)
        self._slot = slot
        self._key = key

    @property
    def native_value(self) -> time | None:
        return parse_time(self.cfg.get(self._key) or DEFAULT_SLOT_TIMES[self._slot])

    async def async_set_value(self, value: time) -> None:
        value = value.replace(second=0, microsecond=0)
        await self.hub.async_set_option(self.tap_id, self._key, value.isoformat())
