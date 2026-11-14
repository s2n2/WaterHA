"""0.3.1: when Open-Meteo is down, rain comes from HA weather entities and rain-rate history."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from custom_components.waterha.hub import WaterHub
from custom_components.waterha.rain import OPEN_METEO_URL, RainHour, intensity_hours

WEATHER = "weather.pirateweather"


def test_intensity_hours_integrates_rate_history():
    start = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    points = [
        (start - timedelta(minutes=30), 0.0),
        (start + timedelta(minutes=30), 2.0),  # 2 mm/h for 1 h
        (start + timedelta(minutes=90), 0.0),
        (start + timedelta(hours=2), 1.0),  # 1 mm/h for the last hour
    ]
    hours = intensity_hours(points, start, end)
    assert [h.mm for h in hours] == [1.0, 1.0, 1.0]
    assert sum(h.mm for h in intensity_hours(points, start, end, 25.4)) == pytest.approx(76.2)
    assert intensity_hours([], start, end) == []


def fake_weather(hass: HomeAssistant, hourly_mm: float = 0.5, high: float = 26.0):
    """A weather entity answering weather.get_forecasts (hourly and daily)."""
    hass.states.async_set(WEATHER, "rainy", {"supported_features": 3})
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    async def get_forecasts(call: ServiceCall):
        if call.data["type"] == "daily":
            return {WEATHER: {"forecast": [{"datetime": dt_util.now().isoformat(), "temperature": high}]}}
        return {
            WEATHER: {
                "forecast": [
                    {
                        "datetime": (now + timedelta(hours=h)).isoformat(),
                        "precipitation": hourly_mm,
                        "temperature": 18.0,
                    }
                    for h in range(0, 30)
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY
    )


@pytest.fixture
def open_meteo_down(aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())


async def test_backup_forecast_and_high_from_weather_entity(hass: HomeAssistant, setup, open_meteo_down):
    fake_weather(hass, hourly_mm=0.1, high=26.0)
    hass.states.async_set(
        "sensor.pirateweather_precip_intensity", "0", {"device_class": "precipitation_intensity"}
    )
    past = [RainHour(dt_util.utcnow() - timedelta(hours=h), 0.25) for h in range(0, 24)]
    with patch.object(WaterHub, "_past_from_history", return_value=past) as history:
        await setup()
    assert history.call_args.args[0] == "sensor.pirateweather_precip_intensity"
    window = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h")
    nxt = float(hass.states.get("sensor.garden_watering_rain_next_24_h").state)
    assert nxt == pytest.approx(2.4, abs=0.15)
    assert float(hass.states.get("sensor.garden_watering_rain_past_24_h").state) == pytest.approx(6.0, abs=0.3)
    assert "Backup" in window.attributes["source"] and WEATHER in window.attributes["source"]
    assert "Open-Meteo unavailable" in window.attributes["error"]
    # The high from the daily forecast (26) picks the hot amount.
    assert hass.states.get("sensor.garden_watering_day_type").state == "hot"
    assert hass.states.get("sensor.lawn_status").attributes["amount_today"] == 15


async def test_forecast_only_backup_still_works(hass: HomeAssistant, setup, open_meteo_down):
    fake_weather(hass, hourly_mm=0.2)
    await setup()
    nxt = float(hass.states.get("sensor.garden_watering_rain_next_24_h").state)
    assert nxt == pytest.approx(4.8, abs=0.25)
    assert hass.states.get("sensor.garden_watering_rain_past_24_h").state == "unknown"
    error = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h").attributes["error"]
    assert "no past 24 h" in error


async def test_configured_backups_are_used(hass: HomeAssistant, setup, open_meteo_down):
    fake_weather(hass, hourly_mm=0.0)
    hass.states.async_set("weather.other", "sunny", {"supported_features": 2})
    hass.states.async_set("sensor.station_rain_rate", "0", {"device_class": "precipitation_intensity"})
    with patch.object(WaterHub, "_past_from_history", return_value=[]) as history:
        entry, _ = await setup(backup_weather=[WEATHER], past_rain_sensor="sensor.station_rain_rate")
    assert entry.runtime_data.backup_weather() == [WEATHER]
    assert history.call_args.args[0] == "sensor.station_rain_rate"


async def test_auto_past_sensor_skips_forecast_sensors(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    for suffix in ("_3h", "_1d"):
        hass.states.async_set(
            f"sensor.pirateweather_precip_intensity{suffix}", "0", {"device_class": "precipitation_intensity"}
        )
    entry, _ = await setup()
    assert entry.runtime_data.past_rain_sensor() is None
    hass.states.async_set(
        "sensor.pirateweather_precip_intensity", "0", {"device_class": "precipitation_intensity"}
    )
    assert entry.runtime_data.past_rain_sensor() == "sensor.pirateweather_precip_intensity"


async def test_open_meteo_back_replaces_backup(hass: HomeAssistant, setup, aioclient_mock, rain):
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    fake_weather(hass, hourly_mm=0.5)
    await setup()
    assert "Backup" in hass.states.get("sensor.garden_watering_rain_past_and_next_24_h").attributes["source"]
    rain(1.0, 0.0, temp=12)
    await hass.services.async_call("waterha", "refresh_rain", {}, blocking=True)
    await hass.async_block_till_done()
    window = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h")
    assert window.attributes["source"] == "Open-Meteo" and window.attributes["error"] is None
    # The high seen earlier today (26 from the backup) is kept for the rest of the day.
    assert hass.states.get("sensor.garden_watering_day_type").state == "hot"
