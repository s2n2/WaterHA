"""0.3: two daily slots per tap, each with its own on/off."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import TAP, tap_data

_DAY = dt_util.now().date() + timedelta(days=1)  # fixed before any test moves the clock


def tomorrow(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())


async def tick_to(hass: HomeAssistant, freezer, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 30) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


async def test_slot_2_is_off_by_default(hass: HomeAssistant, setup, rain, freezer):
    freezer.move_to(tomorrow(18, 59, 50))
    rain(0, 0)
    await setup([tap_data(amount=2)])
    assert hass.states.get("switch.lawn_slot_1").state == "on"
    assert hass.states.get("switch.lawn_slot_2").state == "off"
    assert hass.states.get("time.lawn_slot_2_time").state == "19:00:00"
    await tick_to(hass, freezer, tomorrow(19, 0, 0))
    assert hass.states.get(TAP).state == "closed"


async def test_morning_and_evening_both_run(hass: HomeAssistant, setup, rain, freezer):
    freezer.move_to(tomorrow(5, 59, 50))
    rain(0, 0)
    await setup([tap_data(amount=2, slot_2=True, start_time_2="19:30:00")])
    assert dt_util.parse_datetime(hass.states.get("sensor.lawn_next_run").state) == tomorrow(6)
    await tick_to(hass, freezer, tomorrow(6, 0, 0))
    assert hass.states.get(TAP).state == "open"
    await advance(hass, freezer, 150)
    assert hass.states.get(TAP).state == "closed"
    # Next run is now the evening slot.
    assert dt_util.parse_datetime(hass.states.get("sensor.lawn_next_run").state) == tomorrow(19, 30)
    await tick_to(hass, freezer, tomorrow(19, 30, 0))
    assert hass.states.get(TAP).state == "open"
    assert hass.states.get("sensor.lawn_status").attributes["last_slot"] == 2
    await advance(hass, freezer, 150)
    assert hass.states.get(TAP).state == "closed"
    # Each slot runs once a day: the same minute again does nothing.
    await tick_to(hass, freezer, tomorrow(19, 30, 0) + timedelta(seconds=20))
    assert hass.states.get(TAP).state == "closed"


async def test_evening_only_from_the_dashboard(hass: HomeAssistant, setup, rain, freezer):
    freezer.move_to(tomorrow(5, 59, 50))
    rain(0, 0)
    entry, _ = await setup([tap_data(amount=2)])
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.lawn_slot_1"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.lawn_slot_2"}, blocking=True)
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.lawn_slot_2_time", "time": "20:15:00"}, blocking=True
    )
    await hass.async_block_till_done()
    data = entry.subentries["tap1"].data
    assert data["slot_1"] is False and data["slot_2"] is True and data["start_time_2"] == "20:15:00"
    assert dt_util.parse_datetime(hass.states.get("sensor.lawn_next_run").state) == tomorrow(20, 15)
    await tick_to(hass, freezer, tomorrow(6, 0, 0))
    assert hass.states.get(TAP).state == "closed"
    await tick_to(hass, freezer, tomorrow(20, 15, 0))
    assert hass.states.get(TAP).state == "open"


async def test_no_slot_on_means_no_next_run(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    await setup([tap_data(slot_1=False, slot_2=False)])
    assert hass.states.get("sensor.lawn_next_run").state == "unknown"
    window = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h")
    assert window.attributes["taps"][0]["slot_2"] == "switch.lawn_slot_2"
