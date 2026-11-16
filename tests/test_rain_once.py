"""0.8: each mm of rain is counted once per tap, and only effective rain counts."""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DOMAIN
from custom_components.waterha.rain import OPEN_METEO_URL

from .conftest import TAP, tap_data

_DAY = dt_util.now().date() + timedelta(days=1)


def at(day_offset: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    d = _DAY + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())


def payload(start: datetime, rain_by_hour: dict[int, float]) -> dict:
    """Hourly rain for 5 days from `start - 72 h`; rain_by_hour maps hours after `start` to mm."""
    base = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times, rain = [], []
    for h in range(-72, 72):
        times.append(int((base + timedelta(hours=h)).timestamp()))
        rain.append(rain_by_hour.get(h, 0.0))
    return {"hourly": {"time": times, "precipitation": rain, "precipitation_probability": [50] * len(times),
                       "temperature_2m": [20.0] * len(times)}}


def serve(aioclient_mock, data: dict) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, json=data)


async def tick_to(hass: HomeAssistant, freezer, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def target(hass: HomeAssistant) -> float | None:
    run = hass.states.get("sensor.lawn_status").attributes.get("run")
    return run["target"] if run else None


async def test_forecast_rain_that_skipped_a_day_is_not_counted_again(
    hass: HomeAssistant, setup, aioclient_mock, freezer
):
    """Your Monday: 6.9 mm forecast skipped the vege; 3.1 mm of it fell. Tuesday waters in full."""
    first = at(0, 5, 29, 50)
    freezer.move_to(first)
    # Rain forecast for 06:00-18:00 on day 0: 0.575 mm/h x 12 = 6.9 mm.
    forecast = {h: 0.575 for h in range(1, 13)}
    serve(aioclient_mock, payload(first, forecast))
    await setup([tap_data(amount=2, rain_threshold=3, start_time="05:30:00")])  # 0.8 x 6.9 - 2 = 3.5 effective >= 3
    await tick_to(hass, freezer, at(0, 5, 30))
    log = hass.states.get("sensor.garden_watering_watering_log").attributes["runs"]
    assert log[0]["result"] == "skipped_rain"
    # It rained only 3.1 mm (drizzle), and nothing more is forecast.
    fell = {h: 3.1 / 12 for h in range(1, 13)}
    serve(aioclient_mock, payload(first, fell))
    await tick_to(hass, freezer, at(1, 5, 0))
    await hass.services.async_call(DOMAIN, "refresh_rain", {}, blocking=True)
    await hass.async_block_till_done()
    counted = hass.states.get("sensor.lawn_status").attributes["rain_counted"]
    assert counted["fallen_mm"] == 0 and counted["forecast_mm"] == 0  # already credited yesterday
    assert hass.states.get("sensor.lawn_status").attributes["rain_factor"] == 1.0
    await tick_to(hass, freezer, at(1, 5, 30))
    assert hass.states.get(TAP).state == "open" and target(hass) == 2.0


async def test_rain_beyond_the_credited_forecast_still_counts(hass: HomeAssistant, setup, aioclient_mock, freezer):
    """Forecast 2 mm, but 12 mm fell: the 10 mm extra counts at the next check."""
    first = at(0, 5, 29, 50)
    freezer.move_to(first)
    serve(aioclient_mock, payload(first, {h: 2 / 12 for h in range(1, 13)}))
    await setup([tap_data(amount=10, rain_threshold=10, start_time="05:30:00")])
    await tick_to(hass, freezer, at(0, 5, 30))
    hass.states.async_set(TAP, "closed")  # end the day-0 run (2 mm forecast: 0 effective, full run)
    await hass.async_block_till_done()
    serve(aioclient_mock, payload(first, {h: 1.0 for h in range(1, 13)}))  # 12 mm fell
    await tick_to(hass, freezer, at(1, 5, 0))
    await hass.services.async_call(DOMAIN, "refresh_rain", {}, blocking=True)
    await hass.async_block_till_done()
    counted = hass.states.get("sensor.lawn_status").attributes["rain_counted"]
    # ~10 mm beyond the credited 2 (the first rain hour straddles the 05:30 check, so a little less)
    assert counted["fallen_mm"] == pytest.approx(10, abs=0.5)
    assert counted["effective_mm"] == pytest.approx(6, abs=0.4)  # 0.8 x 10 - 2
    assert hass.states.get("sensor.lawn_status").attributes["rain_factor"] == pytest.approx(0.4, abs=0.04)


async def test_drizzle_does_not_count(hass: HomeAssistant, setup, rain):
    rain(1.0, 1.0)  # 2 mm in 48 h: 0.8 x 2 - 2 < 0
    await setup()
    a = hass.states.get("sensor.lawn_status").attributes
    assert a["rain_counted"]["counted_mm"] == pytest.approx(2.0, abs=0.1)
    assert a["rain_counted"]["effective_mm"] == 0 and a["rain_factor"] == 1.0
