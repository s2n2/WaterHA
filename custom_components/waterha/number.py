"""Numbers: per-tap cool/warm/hot amounts, rain threshold, maximum run time;
hub-wide temperature splits between cool, warm and hot days."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import (
    EntityCategory,
    UnitOfPrecipitationDepth,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    BANDS,
    CONF_COOL_BELOW,
    CONF_HOT_FROM,
    CONF_MAX_MINUTES,
    CONF_MODE,
    CONF_RAIN_THRESHOLD,
    DEFAULT_MAX_MINUTES,
    MODE_LITRES,
    amount_key,
)
from .entity import TapEntity, WaterEntity, add_entities
from .hub import WaterHub, band_amount


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub: WaterHub = entry.runtime_data
    add_entities(
        hass,
        entry,
        async_add_entities,
        [SplitNumber(hub, CONF_COOL_BELOW), SplitNumber(hub, CONF_HOT_FROM)],
        lambda hub, tap_id: [
            *(AmountNumber(hub, tap_id, band) for band in BANDS),
            TapNumber(hub, tap_id, CONF_RAIN_THRESHOLD),
            TapNumber(hub, tap_id, CONF_MAX_MINUTES),
        ],
    )


class AmountNumber(TapEntity, NumberEntity):
    """How much a tap waters on a cool, warm or hot day (minutes or litres)."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_step = 1

    def __init__(self, hub: WaterHub, tap_id: str, band: str) -> None:
        super().__init__(hub, tap_id, amount_key(band))
        self._band = band
        litres = self.cfg.get(CONF_MODE) == MODE_LITRES
        self._attr_native_unit_of_measurement = UnitOfVolume.LITERS if litres else UnitOfTime.MINUTES
        self._attr_native_max_value = 2000 if litres else 240

    @property
    def native_value(self) -> float:
        return band_amount(self.cfg, self._band)

    async def async_set_native_value(self, value: float) -> None:
        await self.hub.async_set_option(self.tap_id, amount_key(self._band), float(value))


class TapNumber(TapEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, hub: WaterHub, tap_id: str, key: str) -> None:
        super().__init__(hub, tap_id, key)
        self._key = key
        if key == CONF_RAIN_THRESHOLD:
            self._attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS
            self._attr_native_min_value = 0
            self._attr_native_max_value = 200
            self._attr_native_step = 0.5
        else:
            self._attr_entity_category = EntityCategory.CONFIG
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


class SplitNumber(WaterEntity, NumberEntity):
    """Where cool ends (cool below) and hot begins (hot from), for every tap."""

    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = -10
    _attr_native_max_value = 50
    _attr_native_step = 0.5

    def __init__(self, hub: WaterHub, key: str) -> None:
        super().__init__(hub, key)
        self._key = key

    @property
    def native_value(self) -> float:
        return self.hub.cool_below if self._key == CONF_COOL_BELOW else self.hub.hot_from

    async def async_set_native_value(self, value: float) -> None:
        # Keep cool below hot: moving one past the other pushes the other along.
        value = float(value)
        if self._key == CONF_COOL_BELOW and value >= self.hub.hot_from:
            await self.hub.async_set_hub_option(CONF_HOT_FROM, value + 0.5)
        if self._key == CONF_HOT_FROM and value <= self.hub.cool_below:
            await self.hub.async_set_hub_option(CONF_COOL_BELOW, value - 0.5)
        await self.hub.async_set_hub_option(self._key, value)
