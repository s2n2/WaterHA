"""Per-slot select: which day types a slot runs on (every day, warm and hot, hot only)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SLOT_RULES, SLOT_WHEN, WHEN_ALWAYS
from .entity import TapEntity, add_entities
from .hub import WaterHub


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_entities(
        hass,
        entry,
        async_add_entities,
        [],
        lambda hub, tap_id: [SlotWhenSelect(hub, tap_id, n) for n in SLOT_RULES],
    )


class SlotWhenSelect(TapEntity, SelectEntity):
    _attr_options = list(SLOT_WHEN)

    def __init__(self, hub: WaterHub, tap_id: str, slot: int) -> None:
        self._key = SLOT_RULES[slot][0]
        super().__init__(hub, tap_id, self._key)
        self._attr_translation_key = "slot_when"
        self._attr_translation_placeholders = {"slot": str(slot)}

    @property
    def current_option(self) -> str:
        return self.cfg.get(self._key) or WHEN_ALWAYS

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_set_option(self.tap_id, self._key, option)
