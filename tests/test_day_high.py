"""0.7.1: today's high is the day's forecast high, not whatever is left of the day."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from custom_components.waterha.const import DOMAIN
from custom_components.waterha.rain import OPEN_METEO_URL

from .conftest import ENTRY_ID

HIGH = "sensor.garden_watering_forecast_high_today"


def two_weather_entities(hass: HomeAssistant, highs: dict[str, float]) -> None:
    """Weather entities whose daily forecast gives `highs`; hourly is dry."""
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    for entity_id in highs:
        hass.states.async_set(entity_id, "cloudy", {"supported_features": 3})

    async def get_forecasts(call: ServiceCall):
        out = {}
        for entity_id in call.data["entity_id"] if isinstance(call.data["entity_id"], list) else [call.data["entity_id"]]:
            if call.data["type"] == "daily":
                out[entity_id] = {"forecast": [{"datetime": dt_util.now().isoformat(), "temperature": highs[entity_id]}]}
            else:
                out[entity_id] = {"forecast": [
                    {"datetime": (now + timedelta(hours=h)).isoformat(), "precipitation": 0, "temperature": 14.0}
                    for h in range(24)
                ]}
        return out

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)


async def test_backup_uses_highest_daily_forecast(hass: HomeAssistant, setup, aioclient_mock):
    """Evening on backup: Met.no's 'today' is just the current 14.5; Pirate Weather says 19.5."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    two_weather_entities(hass, {"weather.home": 14.5, "weather.pirateweather": 19.5})
    await setup(backup_weather=["weather.home", "weather.pirateweather"])
    assert float(hass.states.get(HIGH).state) == 19.5
    source = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h").attributes["source"]
    assert "weather.home (forecast)" in source  # forecast still from the first that answers


async def test_high_does_not_drop_later_in_the_day(hass: HomeAssistant, setup, rain):
    rain(0, 0, temp=21.6)  # morning: Open-Meteo, high 21.6
    await setup()
    assert float(hass.states.get(HIGH).state) == 21.6
    rain(0, 0, temp=14.5)  # evening data no longer includes the warm hours
    await hass.services.async_call(DOMAIN, "refresh_rain", {}, blocking=True)
    await hass.async_block_till_done()
    assert float(hass.states.get(HIGH).state) == 21.6
    assert hass.states.get("sensor.garden_watering_day_type").state == "warm"


async def test_remembered_high_survives_restart_but_not_midnight(hass: HomeAssistant, setup, rain, hass_storage):
    today = dt_util.now().date()
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {"taps": {}, "active": {}, "log": [], "day_high": {"date": today.isoformat(), "high": 25.0}},
    }
    rain(0, 0, temp=14.0)
    entry, _ = await setup()
    assert float(hass.states.get(HIGH).state) == 25.0
    # Yesterday's high is not carried into a new day.
    entry.runtime_data._day_high = {"date": (today - timedelta(days=1)).isoformat(), "high": 30.0}
    assert entry.runtime_data.temp_max_today() == pytest.approx(14.0)
