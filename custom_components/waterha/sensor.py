"""Rain sensors (hub) and status / schedule sensors (per tap)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    BANDS,
    CONF_FLOW_SENSOR,
    CONF_MODE,
    CONF_NAME,
    DOMAIN,
    STATUS_DISABLED,
    STATUS_IDLE,
    STATUS_QUEUED,
    STATUS_RAIN_HOLD,
    STATUS_WATERING,
)
from .entity import TapEntity, WaterEntity, add_entities
from .hub import WaterHub

LOG_SHOWN = 150  # newest log entries exposed to the history card

# Role -> (platform, unique-id key) of each tap entity, for the card.
TAP_ROLES = {
    "status": ("sensor", "status"),
    "next_run": ("sensor", "next_run"),
    "last_run": ("sensor", "last_run"),
    "automatic": ("switch", "automatic"),
    "one_at_a_time": ("switch", "one_at_a_time"),
    "amount_cool": ("number", "amount_cool"),
    "amount_warm": ("number", "amount_warm"),
    "amount_hot": ("number", "amount_hot"),
    "rain_threshold": ("number", "rain_threshold"),
    "start_time": ("time", "start_time"),
    "start_time_2": ("time", "start_time_2"),
    "slot_1": ("switch", "slot_1"),
    "slot_2": ("switch", "slot_2"),
    "slot_1_when": ("select", "slot_1_when"),
    "slot_2_when": ("select", "slot_2_when"),
    "slot_1_share": ("number", "slot_1_share"),
    "slot_2_share": ("number", "slot_2_share"),
    "water_now": ("button", "water_now"),
    "stop": ("button", "stop"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub: WaterHub = entry.runtime_data
    add_entities(
        hass,
        entry,
        async_add_entities,
        [
            RainSensor(hub, "rain_past_24h"),
            RainSensor(hub, "rain_next_24h"),
            RainWindowSensor(hub),
            TempTodaySensor(hub),
            EtTodaySensor(hub),
            DayTypeSensor(hub),
            WateringLogSensor(hub),
            WaterUsedSensor(hub),
            WaterCostSensor(hub),
            WaterSavedSensor(hub),
        ],
        lambda hub, tap_id: [
            TapStatusSensor(hub, tap_id),
            NextRunSensor(hub, tap_id),
            LastRunSensor(hub, tap_id),
            LastWaterSensor(hub, tap_id),
        ],
    )


class RainSensor(WaterEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        rain = self.hub.rain
        if rain is None:
            return None
        return rain.past_mm if self._attr_translation_key == "rain_past_24h" else rain.next_mm



class RainWindowSensor(RainSensor):
    """Past 24 h + next 24 h: the number the thresholds are compared with.

    Its attributes carry the hourly series and the tap entity ids for the card.
    """

    _unrecorded_attributes = frozenset({"hours", "taps", "source", "error", "updated"})

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "rain_window")

    @property
    def native_value(self) -> float | None:
        return self.hub.rain_total()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rain = self.hub.rain
        registry = er.async_get(self.hass)
        taps = []
        for tap_id, sub in self.hub.taps.items():
            ids = {
                role: registry.async_get_entity_id(platform, DOMAIN, f"{tap_id}_{key}")
                for role, (platform, key) in TAP_ROLES.items()
            }
            taps.append({"id": tap_id, "name": sub.data.get(CONF_NAME), **ids})
        hub_id = self.hub.entry.entry_id
        return {
            "day_type": self.hub.band_today(),
            "day_type_by": self.hub.band_reason(),
            "temp_max_today": self.hub.temp_max_today(),
            "et_today": self.hub.et_today(),
            "et_cool_below": self.hub.et_cool_below,
            "et_hot_from": self.hub.et_hot_from,
            "et_cool_below_entity": registry.async_get_entity_id("number", DOMAIN, f"{hub_id}_et_cool_below"),
            "et_hot_from_entity": registry.async_get_entity_id("number", DOMAIN, f"{hub_id}_et_hot_from"),
            "cool_below": self.hub.cool_below,
            "hot_from": self.hub.hot_from,
            "cool_below_entity": registry.async_get_entity_id("number", DOMAIN, f"{hub_id}_cool_below"),
            "hot_from_entity": registry.async_get_entity_id("number", DOMAIN, f"{hub_id}_hot_from"),
            "waterha": "rain",
            "hours": [h.as_dict() for h in rain.hours] if rain else [],
            "past_24h": rain.past_mm if rain else None,
            "next_24h": rain.next_mm if rain else None,
            "source": rain.source if rain else None,
            "updated": self.hub.rain_updated.isoformat() if self.hub.rain_updated else None,
            "error": self.hub.rain_error,
            "taps": taps,
        }


class TempTodaySensor(WaterEntity, SensorEntity):
    """Today's forecast high, which picks cool, warm or hot amounts."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "temp_max_today")

    @property
    def native_value(self) -> float | None:
        return self.hub.temp_max_today()


class EtTodaySensor(WaterEntity, SensorEntity):
    """Today's evapotranspiration: how much water the day will pull out (heat, sun, wind, dry air)."""

    _attr_native_unit_of_measurement = "mm"
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "et_today")

    @property
    def native_value(self) -> float | None:
        return self.hub.et_today()


class DayTypeSensor(WaterEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(BANDS)

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "day_type")

    @property
    def native_value(self) -> str:
        return self.hub.band_today()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "by": self.hub.band_reason(),
            "temp_max_today": self.hub.temp_max_today(),
            "cool_below": self.hub.cool_below,
            "hot_from": self.hub.hot_from,
            "et_today": self.hub.et_today(),
            "et_cool_below": self.hub.et_cool_below,
            "et_hot_from": self.hub.et_hot_from,
        }


class WateringLogSensor(WaterEntity, SensorEntity):
    """Runs in the last 24 h; the log itself (7 days) is in its attributes for the history card."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"runs", "taps", "minutes_24h", "litres_24h"})

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "watering_log")

    def _last_day(self) -> list[dict[str, Any]]:
        cutoff = (dt_util.utcnow() - timedelta(hours=24)).isoformat()
        return [
            item for item in self.hub.log_view()
            if item.get("result") != "skipped_rain" and (item.get("end") or "9") >= cutoff
        ]

    @property
    def native_value(self) -> int:
        return len(self._last_day())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._last_day()
        litres = [item["litres"] for item in day if item.get("litres") is not None]
        return {
            "waterha": "log",
            "taps": [{"id": tap_id, "name": sub.data.get(CONF_NAME)} for tap_id, sub in self.hub.taps.items()],
            "minutes_24h": round(sum(item.get("minutes") or 0 for item in day), 1),
            "litres_24h": round(sum(litres), 1) if litres else None,
            "runs": self.hub.log_view()[:LOG_SHOWN],
        }


class WaterUsedSensor(WaterEntity, SensorEntity):
    """All water WaterHA has measured (flow-sensor taps), for the Energy dashboard."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 0

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "water_used")

    @property
    def native_value(self) -> float:
        return self.hub.total_with_live()


class WaterCostSensor(WaterEntity, SensorEntity):
    """What today's watering cost; daily usage and cost totals are attributes for the cost card."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset(
        {"series", "per_tap_30d", "week", "days30", "month", "projected_month", "today", "not_measured", "savings"}
    )

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "water_cost_today")

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency

    @property
    def native_value(self) -> float:
        return self.hub.usage_view(1)["today"]["cost"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"waterha": "cost", "currency": self.hass.config.currency, **self.hub.usage_view()}


class WaterSavedSensor(WaterEntity, SensorEntity):
    """Money saved over the last 12 months against the baseline (15 min x 2 a day per tap)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"flows", "not_counted"})

    def __init__(self, hub: WaterHub) -> None:
        super().__init__(hub, "water_saved")

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency

    @property
    def native_value(self) -> float:
        return self.hub.savings_view()["saved_cost"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.hub.savings_view()


class TapStatusSensor(TapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_IDLE, STATUS_WATERING, STATUS_QUEUED, STATUS_DISABLED, STATUS_RAIN_HOLD]
    _unrecorded_attributes = frozenset(
        {"run", "days", "tap_entity", "rain_window_mm", "amounts", "queue_position", "slots", "slot_plan"}
    )

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "status")

    @property
    def native_value(self) -> str:
        return self.hub.status(self.tap_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.hub.tap_attributes(self.tap_id)


class NextRunSensor(TapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "next_run")

    @property
    def native_value(self) -> datetime | None:
        return self.hub.next_run(self.tap_id)


class LastRunSensor(TapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "last_run")

    @property
    def native_value(self) -> datetime | None:
        value = self.hub.tap_state(self.tap_id).get("last_run")
        return dt_util.parse_datetime(value) if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.hub.tap_state(self.tap_id)
        return {
            "result": state.get("last_result"),
            "reason": state.get("last_reason"),
            "minutes": state.get("last_minutes"),
            "litres": state.get("last_litres"),
            "last_skip": state.get("last_skip"),
            "last_skip_rain_mm": state.get("last_skip_rain"),
        }


class LastWaterSensor(TapEntity, SensorEntity):
    """Water given by the current or last run: litres with a flow sensor, else minutes."""

    _attr_suggested_display_precision = 1

    def __init__(self, hub: WaterHub, tap_id: str) -> None:
        super().__init__(hub, tap_id, "last_water")
        if self.cfg.get(CONF_FLOW_SENSOR):
            # Volume sensors may only be totals; this is a per-run figure, so no state class.
            self._attr_device_class = SensorDeviceClass.VOLUME
            self._attr_native_unit_of_measurement = UnitOfVolume.LITERS
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float | None:
        run = self.hub.runs.get(self.tap_id)
        litres = bool(self.cfg.get(CONF_FLOW_SENSOR))
        if run is not None:
            if litres:
                return round(run.litres, 1)
            return round((dt_util.utcnow() - run.started).total_seconds() / 60, 1)
        state = self.hub.tap_state(self.tap_id)
        return state.get("last_litres") if litres else state.get("last_minutes")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"mode": self.cfg.get(CONF_MODE)}
