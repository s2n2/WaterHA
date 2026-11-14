"""0.6: evapotranspiration day type, per-slot rules (hot days only, share), days service."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.hub import hotter, slot_runs_on

from .conftest import TAP, tap_data

_DAY = dt_util.now().date() + timedelta(days=1)


def tomorrow(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())


async def tick_to(hass: HomeAssistant, freezer, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def target(hass: HomeAssistant) -> float | None:
    run = hass.states.get("sensor.lawn_status").attributes.get("run")
    return run["target"] if run else None


def test_hotter_and_slot_rules():
    assert hotter("warm", "hot") == "hot"
    assert hotter("cool", None) == "cool"
    assert hotter(None, None) == "warm"
    assert slot_runs_on("hot", "hot") and not slot_runs_on("hot", "warm")
    assert slot_runs_on("warm_hot", "warm") and not slot_runs_on("warm_hot", "cool")
    assert slot_runs_on("always", "cool") and slot_runs_on(None, "cool")


@pytest.mark.parametrize(
    ("temp", "et", "band", "by"),
    [
        (20, 6.0, "hot", "evapotranspiration"),  # mild but dry and windy
        (20, 4.0, "warm", "temperature"),
        (12, 2.0, "cool", "temperature"),
        (12, 4.0, "warm", "evapotranspiration"),  # cool by temperature, warm by ET
        (27, 2.0, "hot", "temperature"),  # hot by temperature whatever ET says
        (20, None, "warm", "temperature"),  # no ET: temperature alone
    ],
)
async def test_day_type_is_hotter_of_temperature_and_et(hass: HomeAssistant, setup, rain, temp, et, band, by):
    rain(0, 0, temp=temp, et=et)
    await setup()
    day = hass.states.get("sensor.garden_watering_day_type")
    assert day.state == band and day.attributes["by"] == by
    if et is not None:
        assert float(hass.states.get("sensor.garden_watering_evapotranspiration_today").state) == pytest.approx(et, abs=0.2)


async def test_et_splits_editable(hass: HomeAssistant, setup, rain):
    rain(0, 0, temp=20, et=4.5)
    entry, _ = await setup()
    assert hass.states.get("sensor.garden_watering_day_type").state == "warm"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.garden_watering_hot_day_et_from", "value": 4.0}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options["et_hot_from"] == 4.0
    assert hass.states.get("sensor.garden_watering_day_type").state == "hot"


@pytest.mark.parametrize(("et", "runs"), [(6.0, True), (3.5, False)])
async def test_hot_day_top_up_slot(hass: HomeAssistant, setup, rain, freezer, et, runs):
    """Slot 2 at 16:00 on hot days only, 30% of the hot amount (10 min -> 3 min)."""
    freezer.move_to(tomorrow(15, 59, 50))
    rain(0, 0, temp=20, et=et)
    await setup([tap_data(amount_hot=10, slot_1=False, slot_2=True, start_time_2="16:00:00",
                          slot_2_when="hot", slot_2_share=30)])
    plan = hass.states.get("sensor.lawn_status").attributes["slot_plan"][1]
    assert plan["when"] == "hot" and plan["share"] == 30 and plan["runs_today"] is runs
    next_run = hass.states.get("sensor.lawn_next_run").state
    if runs:
        assert dt_util.parse_datetime(next_run) == tomorrow(16)
    else:
        assert next_run == "unknown"  # a hot-only slot is not promised on a warm day
    await tick_to(hass, freezer, tomorrow(16, 0, 0))
    if runs:
        assert hass.states.get(TAP).state == "open" and target(hass) == pytest.approx(3.0)
    else:
        assert hass.states.get(TAP).state == "closed"
        assert hass.states.get("sensor.garden_watering_watering_log").attributes["runs"] == []


async def test_slot_rule_and_share_from_entities(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.lawn_slot_2_runs_on", "option": "warm_hot"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.lawn_slot_2_amount", "value": 40}, blocking=True
    )
    await hass.async_block_till_done()
    data = entry.subentries["tap1"].data
    assert data["slot_2_when"] == "warm_hot" and data["slot_2_share"] == 40
    window = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h")
    assert window.attributes["taps"][0]["slot_2_when"] == "select.lawn_slot_2_runs_on"


async def test_set_days_service(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    await hass.services.async_call(
        "waterha", "set_days", {"entity_id": "sensor.lawn_status", "days": ["sat", "mon", "wed"]}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.subentries["tap1"].data["days"] == ["mon", "wed", "sat"]
    assert hass.states.get("sensor.lawn_status").attributes["days"] == ["mon", "wed", "sat"]
    # By the tap's own switch too; no days means no scheduled runs.
    await hass.services.async_call("waterha", "set_days", {"entity_id": TAP, "days": []}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lawn_next_run").state == "unknown"
