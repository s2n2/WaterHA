"""0.7: rolling 12-month savings against a fixed daily baseline."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DOMAIN

from .conftest import ENTRY_ID, TAP2, tap_data

SAVED = "sensor.garden_watering_water_saved_12_months"
BASELINE = "number.garden_watering_savings_baseline"


def storage(days_ago: int, daily: dict, flows: dict) -> dict:
    return {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {
            "taps": {tap: {"flow_lpm": f} for tap, f in flows.items()},
            "active": {}, "log": [], "daily": daily,
            "tracking_since": (dt_util.now().date() - timedelta(days=days_ago)).isoformat(),
        },
    }


async def test_savings_against_baseline(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    today = dt_util.now().date()
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    # 10 completed days; Lawn 10 L/min, Roses 5 L/min; 30 min/day baseline:
    # baseline = 10 days * 30 min * 15 L/min = 4500 L. Used 1000 L (+ today's 999 L not counted).
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = storage(
        10,
        {day(1): {"tap1": 600.0, "tap2": 100.0}, day(4): {"tap1": 300.0}, day(0): {"tap1": 999.0},
         day(30): {"tap1": 5000.0}},  # before tracking started: ignored
        {"tap1": 10.0, "tap2": 5.0},
    )
    await setup([tap_data(), tap_data(name="Roses", tap_entity=TAP2)])
    s = hass.states.get(SAVED)
    a = s.attributes
    assert a["days"] == 10
    assert a["baseline_litres"] == 4500 and a["actual_litres"] == 1000 and a["saved_litres"] == 3500
    assert float(s.state) == pytest.approx(3500 * 1.48 / 1000, abs=0.01)
    assert a["per_year_litres"] == pytest.approx(350 * 365, abs=1)
    assert a["flows"] == {"Lawn": 10.0, "Roses": 5.0} and a["not_counted"] == []
    # The cost card carries the same figures.
    cost = hass.states.get("sensor.garden_watering_water_cost_today").attributes
    assert cost["savings"]["saved_litres"] == 3500
    # A smaller baseline (15 min a day) saves less.
    await hass.services.async_call("number", "set_value", {"entity_id": BASELINE, "value": 15}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SAVED).attributes["saved_litres"] == 2250 - 1000


async def test_window_is_twelve_months(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = storage(500, {}, {"tap1": 10.0})
    await setup()
    a = hass.states.get(SAVED).attributes
    assert a["days"] == 365 and a["saved_litres"] == 365 * 30 * 10


async def test_first_day_and_unknown_flow(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    await setup()  # fresh: tracking starts today, no flow known (no flow sensor history)
    a = hass.states.get(SAVED).attributes
    assert a["days"] == 0 and float(hass.states.get(SAVED).state) == 0
    assert a["not_counted"] == ["Lawn"]


async def test_flow_learned_from_runs(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    hass.states.async_set("sensor.lawn_flow", "0.72", {"unit_of_measurement": "m³/h"})  # 12 L/min
    entry, _ = await setup([tap_data(amount=2, flow_sensor="sensor.lawn_flow")])
    await hass.services.async_call("button", "press", {"entity_id": "button.lawn_water_now"}, blocking=True)
    for _ in range(13):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert entry.runtime_data.flow_lpm("tap1") == pytest.approx(12, abs=1)
