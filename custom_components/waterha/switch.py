"""Per-tap switches: automatic (scheduled) watering, and one at a time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ENABLED, CONF_SEQUENTIAL
from .entity import TapEntity, add_entities
from .hub import WaterHub


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_entities(
        hass, entry, async_add_entities, [], lambda hub, tap_id: [AutoSwitch(hub, tap_id), OneAtATimeSwitch(hub, tap_id)]
    )


class AutoSwitch(TapEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "automatic")

    @property
    def is_on(self) -> bool:
        return bool(self.cfg.get(CONF_ENABLED, True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, CONF_ENABLED, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, CONF_ENABLED, False)


class OneAtATimeSwitch(TapEntity, SwitchEntity):
    """On: wait for any other one-at-a-time tap to finish before starting."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "one_at_a_time")

    @property
    def is_on(self) -> bool:
        return bool(self.cfg.get(CONF_SEQUENTIAL, True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, CONF_SEQUENTIAL, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, CONF_SEQUENTIAL, False)
