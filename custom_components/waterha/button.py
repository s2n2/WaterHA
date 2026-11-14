"""Buttons: water a tap now, stop a tap, stop everything, refresh rain."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_AMOUNT
from .entity import TapEntity, WaterEntity, add_entities
from .hub import WaterHub


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub: WaterHub = entry.runtime_data
    add_entities(
        hass,
        entry,
        async_add_entities,
        [StopAllButton(hub, "stop_all"), RefreshRainButton(hub, "refresh_rain")],
        lambda hub, tap_id: [WaterNowButton(hub, tap_id, "water_now"), StopButton(hub, tap_id, "stop")],
    )


class WaterNowButton(TapEntity, ButtonEntity):
    """Water now with the tap's set amount (ignores the rain threshold)."""

    async def async_press(self) -> None:
        await self.hub.async_water(self.tap_id, float(self.cfg.get(CONF_AMOUNT) or 0), None, "manual")


class StopButton(TapEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.hub.async_stop_tap(self.tap_id)


class StopAllButton(WaterEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.hub.async_stop_all()


class RefreshRainButton(WaterEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.hub.async_refresh_rain()
