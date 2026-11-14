"""Per-tap numbers: amount, rain threshold, maximum run time."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfPrecipitationDepth, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_AMOUNT,
    CONF_MAX_MINUTES,
    CONF_MODE,
    CONF_RAIN_THRESHOLD,
    DEFAULT_MAX_MINUTES,
    MODE_LITRES,
)
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
        lambda hub, tap_id: [
            TapNumber(hub, tap_id, CONF_AMOUNT),
            TapNumber(hub, tap_id, CONF_RAIN_THRESHOLD),
            TapNumber(hub, tap_id, CONF_MAX_MINUTES),
        ],
    )


class TapNumber(TapEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, hub: WaterHub, tap_id: str, key: str) -> None:
        super().__init__(hub, tap_id, key)
        self._key = key
        if key == CONF_AMOUNT:
            litres = self.cfg.get(CONF_MODE) == MODE_LITRES
            self._attr_translation_key = "amount_litres" if litres else "amount_minutes"
            self._attr_unique_id = f"{tap_id}_amount"
            self._attr_native_unit_of_measurement = (
                UnitOfVolume.LITERS if litres else UnitOfTime.MINUTES
            )
            self._attr_native_min_value = 1
            self._attr_native_max_value = 2000 if litres else 240
            self._attr_native_step = 1
            self._attr_entity_category = None
        elif key == CONF_RAIN_THRESHOLD:
            self._attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS
            self._attr_native_min_value = 0
            self._attr_native_max_value = 200
            self._attr_native_step = 0.5
            self._attr_entity_category = None
        else:
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_native_min_value = 1
            self._attr_native_max_value = 240
            self._attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        value = self.cfg.get(self._key, DEFAULT_MAX_MINUTES if self._key == CONF_MAX_MINUTES else None)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        if self._key == CONF_MAX_MINUTES:
            value = int(value)
        await self.hub.async_set_option(self.tap_id, self._key, value)
