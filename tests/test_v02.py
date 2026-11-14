"""0.2: day types (cool/warm/hot), light-rain scaling, one-at-a-time, close all on start."""

from datetime import datetime, timedelta

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DOMAIN
from custom_components.waterha.hub import band_for

from .conftest import TAP, TAP2, tap_data

TAP3 = "valve.tap_front"


def tomorrow(hour: int, minute: int = 0, second: int = 0) -> datetime:
    day = dt_util.now().date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 10) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


def run_target(hass: HomeAssistant, name: str) -> float | None:
    run = hass.states.get(f"sensor.{name}_status").attributes.get("run")
    return run["target"] if run else None


@pytest.mark.parametrize(
    ("temp", "band"), [(15.9, "cool"), (16.0, "warm"), (22.9, "warm"), (23.0, "hot"), (None, "warm")]
)
def test_band_boundaries(temp, band):
    assert band_for(temp, 16, 23) == band


@pytest.mark.parametrize(("temp", "minutes"), [(12, 5), (19, 10), (27, 15)])
async def test_schedule_uses_day_type_amount(hass: HomeAssistant, setup, rain, freezer, temp, minutes):
    start = tomorrow(5, 59, 50)
    freezer.move_to(start)
    rain(0, 0, temp=temp)
    await setup()
    await advance(hass, freezer, 10)
    assert run_target(hass, "lawn") == minutes


async def test_splits_change_without_reload(hass: HomeAssistant, setup, rain):
    rain(0, 0, temp=20)
    entry, _ = await setup()
    status_before = hass.states.get("sensor.lawn_status")
    assert hass.states.get("sensor.garden_watering_day_type").state == "warm"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.garden_watering_hot_day_from", "value": 19}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options["hot_from"] == 19
    assert hass.states.get("sensor.garden_watering_day_type").state == "hot"
    assert hass.states.get("sensor.lawn_status").attributes["amount_today"] == 15
    # Same entity (no reload): its context-free state object was only updated.
    assert hass.states.get("sensor.lawn_status").attributes["tap_entity"] == status_before.attributes["tap_entity"]
    # Moving cool past hot pushes hot along.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.garden_watering_cool_day_below", "value": 21}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options["cool_below"] == 21 and entry.options["hot_from"] == 21.5


@pytest.mark.parametrize(("past", "nxt", "expected"), [(0, 0, 10), (1.0, 1.5, 5), (2.0, 2.0, 2)])
async def test_light_rain_scales_scheduled_run(hass: HomeAssistant, setup, rain, freezer, past, nxt, expected):
    """5 mm threshold: 2.5 mm of rain waters 50%, 4 mm waters 20%."""
    start = tomorrow(5, 59, 50)
    freezer.move_to(start)
    rain(past, nxt, temp=20)
    await setup()
    status = hass.states.get("sensor.lawn_status").attributes
    assert status["scheduled_amount"] == pytest.approx(expected, abs=0.3)
    await advance(hass, freezer, 10)
    assert run_target(hass, "lawn") == pytest.approx(expected, abs=0.3)
    last = hass.states.get("sensor.lawn_status")
    assert last.state == "watering"


async def test_light_rain_does_not_scale_water_now(hass: HomeAssistant, setup, rain):
    rain(1.0, 1.5, temp=20)
    await setup()
    await hass.services.async_call("button", "press", {"entity_id": "button.lawn_water_now"}, blocking=True)
    await hass.async_block_till_done()
    assert run_target(hass, "lawn") == 10


async def test_same_time_taps_run_one_after_another(hass: HomeAssistant, setup, rain, freezer):
    start = tomorrow(5, 59, 50)
    freezer.move_to(start)
    rain(0, 0, temp=12)  # cool day
    await setup([
        tap_data(name="Lawn", tap_entity=TAP, amount_cool=2),
        tap_data(name="Roses", tap_entity=TAP2, amount_cool=3),
        tap_data(name="Front", tap_entity=TAP3, amount_cool=1),
    ])
    hass.states.async_set(TAP3, "closed")
    await advance(hass, freezer, 10)
    assert [hass.states.get(t).state for t in (TAP, TAP2, TAP3)] == ["open", "closed", "closed"]
    assert hass.states.get("sensor.roses_status").attributes["queue_position"] == 1
    assert hass.states.get("sensor.front_status").attributes["queue_position"] == 2
    await advance(hass, freezer, 125)  # lawn's 2 min done -> roses
    assert [hass.states.get(t).state for t in (TAP, TAP2, TAP3)] == ["closed", "open", "closed"]
    await advance(hass, freezer, 185)  # roses' 3 min done -> front
    assert [hass.states.get(t).state for t in (TAP, TAP2, TAP3)] == ["closed", "closed", "open"]
    await advance(hass, freezer, 65)
    assert hass.states.get(TAP3).state == "closed"
    assert hass.states.get("sensor.front_last_run").attributes["minutes"] == pytest.approx(1, abs=0.2)


async def test_tap_not_one_at_a_time_runs_alongside(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    await setup([tap_data(), tap_data(name="Roses", tap_entity=TAP2, sequential=False)])
    for name in ("lawn", "roses"):
        await hass.services.async_call("button", "press", {"entity_id": f"button.{name}_water_now"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(TAP).state == "open" and hass.states.get(TAP2).state == "open"
    # Switching one at a time on from the dashboard needs no reload.
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.roses_one_at_a_time"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.roses_status").attributes["one_at_a_time"] is True


async def test_ha_start_closes_every_tap(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    hass.set_state(CoreState.not_running)
    entry, taps = await setup([tap_data(), tap_data(name="Roses", tap_entity=TAP2)])
    hass.states.async_set(TAP, "open")  # stuck open from before the restart
    hass.states.async_set(TAP2, "closed")
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert ("close_valve", TAP) in taps.calls and ("close_valve", TAP2) in taps.calls
    assert hass.states.get(TAP).state == "closed"
    # A tap that reports open again (radio not ready the first time) is closed on the recheck.
    hass.states.async_set(TAP2, "open")
    await advance(hass, freezer, 70)
    assert hass.states.get(TAP2).state == "closed"


async def test_reload_while_running_leaves_manual_taps(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, taps = await setup()
    hass.states.async_set(TAP, "open")  # opened by hand, not by WaterHA
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TAP).state == "open"
    assert ("close_valve", TAP) not in taps.calls


async def test_0_1_tap_with_single_amount_still_works(hass: HomeAssistant, setup, rain, make_entry):
    """A tap saved by 0.1.x has `amount` only; every day type uses it."""
    rain(0, 0, temp=27)
    old = tap_data()
    for key in ("amount_cool", "amount_warm", "amount_hot", "sequential"):
        old.pop(key)
    old["amount"] = 7.0
    registry = er.async_get(hass)
    registry.async_get_or_create("number", DOMAIN, "tap1_amount", suggested_object_id="lawn_minutes_to_water")
    await setup([old])
    assert hass.states.get("number.lawn_hot_day_amount").state == "7.0"
    assert hass.states.get("switch.lawn_one_at_a_time").state == "on"
    assert registry.async_get_entity_id("number", DOMAIN, "tap1_amount") is None
    await hass.services.async_call("button", "press", {"entity_id": "button.lawn_water_now"}, blocking=True)
    await hass.async_block_till_done()
    assert run_target(hass, "lawn") == 7
