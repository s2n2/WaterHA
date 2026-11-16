"""0.4: the watering log behind the history card."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DOMAIN

from .conftest import ENTRY_ID, TAP, TAP2, tap_data

LOG = "sensor.garden_watering_watering_log"
_DAY = dt_util.now().date() + timedelta(days=1)


def tomorrow(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 30) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def test_runs_and_live_run_are_logged(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    await setup([tap_data(amount=2, flow_sensor="sensor.lawn_flow")])
    hass.states.async_set("sensor.lawn_flow", "0.6", {"unit_of_measurement": "m³/h"})  # 10 L/min
    assert hass.states.get(LOG).state == "0"
    await press(hass, "button.lawn_water_now")
    live = hass.states.get(LOG).attributes["runs"][0]
    assert live["result"] == "running" and live["end"] is None and live["name"] == "Lawn"
    await advance(hass, freezer, 130)
    log = hass.states.get(LOG)
    assert log.state == "1"
    run = log.attributes["runs"][0]
    assert run["result"] == "completed" and run["reason"] == "manual"
    assert run["minutes"] == pytest.approx(2, abs=0.2)
    assert run["litres"] == pytest.approx(20, abs=2)
    assert log.attributes["minutes_24h"] == pytest.approx(2, abs=0.2)
    assert log.attributes["taps"] == [{"id": "tap1", "name": "Lawn"}]


async def test_schedule_skip_and_rain_reduction_are_logged(hass: HomeAssistant, setup, rain, freezer):
    freezer.move_to(tomorrow(5, 59, 50))
    rain(2.5, 3.125, temp=20)  # 5.6 mm counted = 2.5 mm effective, of 5 mm: half
    await setup([
        tap_data(amount=2),
        tap_data(name="Roses", tap_entity=TAP2, amount=2, rain_threshold=2.0),  # 2.5 effective >= 2: skip
    ])
    await advance(hass, freezer, 10, 10)
    runs = hass.states.get(LOG).attributes["runs"]
    skip = next(r for r in runs if r["name"] == "Roses")
    assert skip["result"] == "skipped_rain" and skip["rain_mm"] == pytest.approx(5.6, abs=0.2)
    assert skip["rain_effective_mm"] == pytest.approx(2.5, abs=0.2)
    await advance(hass, freezer, 90)
    run = next(r for r in hass.states.get(LOG).attributes["runs"] if r["name"] == "Lawn")
    assert run["result"] == "completed" and run["slot"] == 1 and run["band"] == "warm"
    assert run["rain_factor"] == pytest.approx(0.5, abs=0.05)
    # Skips are listed but are not counted as runs.
    assert hass.states.get(LOG).state == "1"


async def test_log_survives_reload_and_drops_old_entries(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    old = (dt_util.utcnow() - timedelta(days=9)).isoformat()
    recent = (dt_util.utcnow() - timedelta(hours=3)).isoformat()
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {"taps": {}, "active": {}, "log": [
            {"tap": "tap1", "name": "Lawn", "start": old, "end": old, "minutes": 5, "result": "completed"},
            {"tap": "tap1", "name": "Lawn", "start": recent, "end": recent, "minutes": 4, "result": "completed"},
        ]},
    }
    entry, _ = await setup()
    runs = hass.states.get(LOG).attributes["runs"]
    assert [r["start"] for r in runs] == [recent]
    await press(hass, "button.lawn_water_now")
    await press(hass, "button.lawn_stop")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runs = hass.states.get(LOG).attributes["runs"]
    assert [r["result"] for r in runs] == ["stopped", "completed"]
    assert hass.states.get(TAP).state == "closed"
