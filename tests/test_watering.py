"""Watering runs, schedules, rain skips and restart safety in a real HA."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DAYS, DOMAIN

from .conftest import ENTRY_ID, FLOW, TAP, TAP2, tap_data


def local(hour: int, minute: int = 0, second: int = 0) -> datetime:
    day = dt_util.now().date() + timedelta(days=1)
    return datetime(
        day.year, day.month, day.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone()
    )


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 10) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def test_entities_and_rain(hass: HomeAssistant, setup, rain):
    rain(3.0, 1.5)
    await setup()
    past = hass.states.get("sensor.garden_watering_rain_past_24_h")
    assert float(past.state) == pytest.approx(3.0, abs=0.01)
    assert float(hass.states.get("sensor.garden_watering_rain_next_24_h").state) == pytest.approx(1.5, abs=0.01)
    window = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h")
    assert float(window.state) == pytest.approx(4.5, abs=0.01)
    assert window.attributes["taps"][0]["water_now"] == "button.lawn_water_now"
    assert len(window.attributes["hours"]) >= 48
    assert hass.states.get("sensor.lawn_status").state == "idle"
    assert hass.states.get("number.lawn_minutes_to_water").state == "10.0"
    assert hass.states.get("switch.lawn_automatic_watering").state == "on"


async def test_water_now_minutes(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    await setup()
    await press(hass, "button.lawn_water_now")
    assert hass.states.get(TAP).state == "open"
    assert hass.states.get("sensor.lawn_status").state == "watering"
    await advance(hass, freezer, 9 * 60, 60)
    assert hass.states.get(TAP).state == "open"
    await advance(hass, freezer, 70, 10)
    assert hass.states.get(TAP).state == "closed"
    last = hass.states.get("sensor.lawn_last_run")
    assert last.attributes["result"] == "completed"
    assert last.attributes["minutes"] == pytest.approx(10, abs=0.3)


async def test_litres_mode_stops_at_target(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    hass.states.async_set(FLOW, "0", {"unit_of_measurement": "m³/h"})
    await setup([tap_data(mode="litres", amount=50, flow_sensor=FLOW)])
    await press(hass, "button.lawn_water_now")
    hass.states.async_set(FLOW, "1.2", {"unit_of_measurement": "m³/h"})  # 20 L/min
    await hass.async_block_till_done()
    await advance(hass, freezer, 140)
    assert hass.states.get(TAP).state == "open"
    await advance(hass, freezer, 30)
    assert hass.states.get(TAP).state == "closed"
    last = hass.states.get("sensor.lawn_last_run").attributes
    assert last["result"] == "completed"
    assert 50 <= last["litres"] <= 54
    assert float(hass.states.get("sensor.lawn_water_given").state) == last["litres"]


async def test_litres_mode_stops_with_no_flow(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    hass.states.async_set(FLOW, "0", {"unit_of_measurement": "m³/h"})
    await setup([tap_data(mode="litres", amount=50, flow_sensor=FLOW)])
    await press(hass, "button.lawn_water_now")
    await advance(hass, freezer, 310)
    assert hass.states.get(TAP).state == "closed"
    assert hass.states.get("sensor.lawn_last_run").attributes["result"] == "no_flow"


async def test_schedule_runs_when_dry(hass: HomeAssistant, setup, rain, freezer):
    start = local(5, 59, 50)
    freezer.move_to(start)
    rain(1.0, 1.0)
    await setup()
    expected = start.replace(hour=6, minute=0, second=0)
    assert dt_util.parse_datetime(hass.states.get("sensor.lawn_next_run").state) == expected
    await advance(hass, freezer, 10)
    assert hass.states.get(TAP).state == "open"


async def test_schedule_skips_for_rain(hass: HomeAssistant, setup, rain, freezer):
    freezer.move_to(local(5, 59, 50))
    rain(2.0, 4.0)  # 6 mm >= the 5 mm threshold
    await setup()
    assert hass.states.get("sensor.lawn_status").state == "rain_hold"
    await advance(hass, freezer, 10)
    assert hass.states.get(TAP).state == "closed"
    last = hass.states.get("sensor.lawn_last_run").attributes
    assert last["result"] == "skipped_rain"
    assert last["last_skip_rain_mm"] == pytest.approx(6.0, abs=0.15)
    # Manual watering still works while holding for rain.
    await press(hass, "button.lawn_water_now")
    assert hass.states.get(TAP).state == "open"


async def test_schedule_respects_days_and_auto_switch(hass: HomeAssistant, setup, rain, freezer):
    start = local(5, 59, 50)
    freezer.move_to(start)
    rain(0, 0)
    other_days = [d for d in DAYS if d != DAYS[start.weekday()]]
    await setup([tap_data(days=other_days), tap_data(name="Roses", tap_entity=TAP2)])
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.roses_automatic_watering"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.roses_status").state == "disabled"
    await advance(hass, freezer, 10)
    assert hass.states.get(TAP).state == "closed"
    assert hass.states.get(TAP2).state == "closed"


async def test_sequential_queue(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    await setup([tap_data(amount=2), tap_data(name="Roses", tap_entity=TAP2, amount=2)])
    await press(hass, "button.lawn_water_now")
    await press(hass, "button.roses_water_now")
    assert hass.states.get(TAP2).state == "closed"
    assert hass.states.get("sensor.roses_status").state == "queued"
    await advance(hass, freezer, 130)
    assert hass.states.get(TAP).state == "closed"
    assert hass.states.get(TAP2).state == "open"


async def test_settings_from_entities(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.lawn_rain_threshold", "value": 8}, blocking=True
    )
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.lawn_start_time", "time": "18:15:00"}, blocking=True
    )
    await hass.async_block_till_done()
    data = entry.subentries["tap1"].data
    assert data["rain_threshold"] == 8 and data["start_time"] == "18:15:00"
    assert hass.states.get("number.lawn_rain_threshold").state == "8.0"
    assert hass.states.get("sensor.lawn_status").attributes["start_time"] == "18:15:00"


async def test_tap_closed_elsewhere_ends_run(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    await setup()
    await press(hass, "button.lawn_water_now")
    hass.states.async_set(TAP, "closed")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lawn_status").state == "idle"
    assert hass.states.get("sensor.lawn_last_run").attributes["result"] == "stopped_externally"


async def test_tap_that_never_opens_fails(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    _, taps = await setup()
    taps.refuse_open.add(TAP)
    await press(hass, "button.lawn_water_now")
    await advance(hass, freezer, 100)
    assert hass.states.get("sensor.lawn_last_run").attributes["result"] == "failed"
    assert ("close_valve", TAP) in taps.calls


async def test_service_and_stop_all(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    await setup()
    await hass.services.async_call(DOMAIN, "water_now", {"entity_id": TAP, "amount": 3}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lawn_status").attributes["run"]["target"] == 3
    await press(hass, "button.garden_watering_stop_all_taps")
    assert hass.states.get(TAP).state == "closed"


async def test_restart_mid_run_closes_tap(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {
            "taps": {},
            "active": {"tap1": {"started": dt_util.utcnow().isoformat(), "entity_id": TAP, "litres": 4}},
        },
    }
    calls = []

    async def off(call):
        calls.append(call.data["entity_id"])
        hass.states.async_set(TAP, "closed")

    hass.services.async_register("valve", "close_valve", off)
    await setup()
    assert calls  # closed during startup recovery
    assert hass.states.get("sensor.lawn_last_run").attributes["result"] == "interrupted_by_restart"


async def test_unload_closes_open_tap(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    await press(hass, "button.lawn_water_now")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TAP).state == "closed"


async def test_switch_taps_use_switch_services(hass: HomeAssistant, setup, rain):
    """Real taps here are switches (SONOFF SWV): turn_on / turn_off."""
    rain(0, 0)
    hass.states.async_set("switch.tap_front", "off")
    await setup([tap_data(tap_entity="switch.tap_front")])
    seen = []

    async def handler(call):
        seen.append((call.service, call.data["entity_id"]))
        hass.states.async_set("switch.tap_front", "on" if call.service == "turn_on" else "off")

    # Replaces the switch services for this test only.
    hass.services.async_register("switch", "turn_on", handler)
    hass.services.async_register("switch", "turn_off", handler)
    await press(hass, "button.lawn_water_now")
    await press(hass, "button.lawn_stop")
    assert [s for s, _ in seen] == ["turn_on", "turn_off"]
    assert hass.states.get("sensor.lawn_last_run").attributes["result"] == "stopped"
