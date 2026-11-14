"""Per-tap switches: automatic watering, one at a time, and each daily slot on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ENABLED, CONF_SEQUENTIAL, SLOTS
from .entity import TapEntity, add_entities
from .hub import WaterHub


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_entities(
        hass, entry, async_add_entities, [], lambda hub, tap_id: [
            AutoSwitch(hub, tap_id),
            OneAtATimeSwitch(hub, tap_id),
            *(SlotSwitch(hub, tap_id, key, default) for _n, _t, key, default in SLOTS),
        ]
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


class SlotSwitch(TapEntity, SwitchEntity):
    """Whether a daily slot (e.g. morning or evening) runs."""

    def __init__(self, hub: WaterHub, tap_id: str, key: str, default: bool) -> None:
        super().__init__(hub, tap_id, key)
        self._key = key
        self._default = default

    @property
    def is_on(self) -> bool:
        return bool(self.cfg.get(self._key, self._default))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set_option(self.tap_id, self._key, False)
