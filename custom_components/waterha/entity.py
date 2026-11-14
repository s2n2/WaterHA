"""Shared entity bases: one device for the hub, one per tap."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_NAME, DOMAIN, VERSION
from .hub import WaterHub


def hub_device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="WaterHA",
        model="Rain-aware watering",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


class WaterEntity(Entity):
    """Updates whenever the hub says something changed."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: WaterHub, key: str) -> None:
        self.hub = hub
        self._attr_translation_key = key
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_device_info = hub_device(hub.entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, self.hub.signal, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class TapEntity(WaterEntity):
    """An entity belonging to one tap (config subentry)."""

    def __init__(self, hub: WaterHub, tap_id: str, key: str) -> None:
        super().__init__(hub, key)
        self.tap_id = tap_id
        self._attr_unique_id = f"{tap_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, tap_id)},
            name=hub.tap(tap_id).get(CONF_NAME) or "Tap",
            manufacturer="WaterHA",
            model="Automated tap",
            via_device_id=hub.device_id,
        )

    @property
    def cfg(self) -> dict[str, Any]:
        return self.hub.tap(self.tap_id)

    @property
    def available(self) -> bool:
        return self.tap_id in self.hub.entry.subentries


def add_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    hub_entities: Iterable[Entity],
    tap_factory: Callable[[WaterHub, str], Iterable[Entity]],
) -> None:
    """Add hub entities, then each tap's entities linked to its subentry."""
    hub: WaterHub = entry.runtime_data
    hub_list = list(hub_entities)
    if hub_list:
        async_add_entities(hub_list)
    for tap_id in hub.taps:
        async_add_entities(list(tap_factory(hub, tap_id)), config_subentry_id=tap_id)
