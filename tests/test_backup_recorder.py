"""The past-rain backup against a real recorder database (no patching)."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.waterha.rain import OPEN_METEO_URL

SENSOR = "sensor.pirateweather_precip_intensity"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must be set up before Home Assistant; override conftest's order."""
    yield


async def test_past_rain_from_recorded_history(hass: HomeAssistant, setup, aioclient_mock, freezer):
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, exc=TimeoutError())
    attrs = {"device_class": "precipitation_intensity", "unit_of_measurement": "mm/h"}
    start = dt_util.utcnow() - timedelta(hours=5)
    freezer.move_to(start)
    hass.states.async_set(SENSOR, "0", attrs)
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(hours=1))
    hass.states.async_set(SENSOR, "3", attrs)  # 3 mm/h for 2 h = 6 mm
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(hours=3))
    hass.states.async_set(SENSOR, "0", attrs)
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(hours=5))
    await setup()
    await hass.async_block_till_done()
    past = hass.states.get("sensor.garden_watering_rain_past_24_h")
    assert float(past.state) == pytest.approx(6.0, abs=0.1)
    source = hass.states.get("sensor.garden_watering_rain_past_and_next_24_h").attributes["source"]
    assert f"{SENSOR} history" in source


async def test_flow_rate_seeded_from_history(hass: HomeAssistant, setup, rain, freezer):
    """A tap with no runs yet gets its typical flow from its flow sensor's history."""
    rain(0, 0)
    flow = "sensor.lawn_flow"
    attrs = {"unit_of_measurement": "m³/h"}
    start = dt_util.utcnow() - timedelta(days=2)
    for i, value in enumerate(["0", "0.6", "0", "0.72", "0", "0.66"]):  # 10, 12, 11 L/min
        freezer.move_to(start + timedelta(hours=i))
        hass.states.async_set(flow, value, attrs)
        await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(days=2))
    from .conftest import tap_data

    entry, _ = await setup([tap_data(flow_sensor=flow)])
    await hass.async_block_till_done()
    assert entry.runtime_data.flow_lpm("tap1") == pytest.approx(11, abs=0.1)
