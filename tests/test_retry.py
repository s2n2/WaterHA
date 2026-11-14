"""0.7.2: while Open-Meteo fails, retry after 2, 4, 8 … minutes (not only every 30)."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.rain import OPEN_METEO_URL

from .test_backup import fake_weather

WINDOW = "sensor.garden_watering_rain_past_and_next_24_h"


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 10) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


def om_calls(aioclient_mock) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]).startswith(OPEN_METEO_URL))


async def test_backoff_then_open_meteo_takes_over(hass: HomeAssistant, setup, rain, aioclient_mock, freezer):
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    fake_weather(hass, hourly_mm=0.1)
    hub = (await setup())[0].runtime_data
    assert om_calls(aioclient_mock) == 1
    await advance(hass, freezer, 125)  # 2 min: retry 1
    assert om_calls(aioclient_mock) == 2
    await advance(hass, freezer, 125)  # not yet: next is 4 min after that
    assert om_calls(aioclient_mock) == 2
    await advance(hass, freezer, 125)  # 4 min: retry 2
    assert om_calls(aioclient_mock) == 3
    assert hub._retry_delay == 960  # the 8-min retry is pending; the one after would be 16 min
    rain(0.5, 0.0)  # Open-Meteo answers again
    await advance(hass, freezer, 490)  # 8 min: retry 3 succeeds
    assert hass.states.get(WINDOW).attributes["source"] == "Open-Meteo"
    assert hub._retry_delay == 120 and hub._retry_unsub is None


async def test_startup_gap_fills_when_weather_is_ready(hass: HomeAssistant, setup, aioclient_mock, freezer):
    """Weather entities not loaded yet at startup: the 2-minute retry picks them up."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    await setup()
    assert hass.states.get("sensor.garden_watering_forecast_high_today").state == "unknown"
    fake_weather(hass, hourly_mm=0.0, high=19.5)  # the weather integration finishes loading
    await advance(hass, freezer, 125)
    assert float(hass.states.get("sensor.garden_watering_forecast_high_today").state) == 19.5
    assert "weather.pirateweather (forecast)" in hass.states.get(WINDOW).attributes["source"]


async def test_backoff_caps_at_thirty_minutes(hass: HomeAssistant, setup, aioclient_mock, freezer):
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    hub = (await setup())[0].runtime_data
    await advance(hass, freezer, 2 * 3600, 60)
    assert hub._retry_delay == 1800
