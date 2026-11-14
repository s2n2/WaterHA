"""Fixtures: a real Home Assistant, fake taps, and mocked Open-Meteo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.waterha.const import DOMAIN
from custom_components.waterha.rain import OPEN_METEO_URL

ENTRY_ID = "entry1"
TAP = "valve.tap_lawn"
TAP2 = "valve.tap_roses"
FLOW = "sensor.tap_lawn_flow"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def tap_data(**over: Any) -> dict[str, Any]:
    """A tap: 5 / 10 / 15 min on cool / warm / hot days. amount=X sets all three."""
    data = {
        "name": "Lawn",
        "tap_entity": TAP,
        "mode": "minutes",
        "amount_cool": 5.0,
        "amount_warm": 10.0,
        "amount_hot": 15.0,
        "sequential": True,
        "start_time": "06:00:00",
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "rain_threshold": 5.0,
        "max_minutes": 45,
        "enabled": True,
    }
    if "amount" in over:
        amount = float(over.pop("amount"))
        data.update(amount_cool=amount, amount_warm=amount, amount_hot=amount)
    data.update(over)
    return data


def open_meteo_payload(now: datetime, past_mm: float, next_mm: float, temp: float = 20.0) -> dict[str, Any]:
    """48 h either side of now; rain spread evenly over the 24 h each side; a flat temperature."""
    base = now.replace(minute=0, second=0, microsecond=0)
    times, rain = [], []
    for h in range(-48, 49):
        end = base + timedelta(hours=h)
        times.append(int(end.timestamp()))
        if now - timedelta(hours=24) < end <= now:
            rain.append(past_mm / 24)
        elif now < end <= now + timedelta(hours=24):
            rain.append(next_mm / 24)
        else:
            rain.append(0.0)
    return {
        "hourly": {
            "time": times,
            "precipitation": rain,
            "precipitation_probability": [50] * len(times),
            "temperature_2m": [temp] * len(times),
        }
    }


class Taps:
    """Fake valve services that open/close tap states and record calls.

    Valves, because WaterHA itself provides switch entities.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[tuple[str, str]] = []
        self.refuse_open: set[str] = set()

    def install(self) -> None:
        async def handler(call: ServiceCall) -> None:
            ids = call.data["entity_id"]
            for entity_id in [ids] if isinstance(ids, str) else ids:
                self.calls.append((call.service, entity_id))
                if call.service == "open_valve" and entity_id in self.refuse_open:
                    continue
                self.hass.states.async_set(entity_id, "open" if call.service == "open_valve" else "closed")

        for service in ("open_valve", "close_valve"):
            self.hass.services.async_register("valve", service, handler)


@pytest.fixture
def rain(aioclient_mock):
    """Call rain(past, next, now, temp=) to set what Open-Meteo returns."""

    def _set(past_mm: float, next_mm: float, now: datetime | None = None, temp: float = 20.0) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            OPEN_METEO_URL,
            json=open_meteo_payload(now or datetime.now(timezone.utc), past_mm, next_mm, temp),
        )

    return _set


@pytest.fixture
def make_entry(hass: HomeAssistant):
    def _make(taps: list[dict[str, Any]] | None = None, **options: Any) -> MockConfigEntry:
        taps = [tap_data()] if taps is None else taps
        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id=ENTRY_ID,
            title="Garden watering",
            unique_id=DOMAIN,
            options={"forecast_source": "open_meteo", "cool_below": 16.0, "hot_from": 23.0, **options},
            subentries_data=[
                ConfigSubentryData(
                    data=t, subentry_id=f"tap{i + 1}", subentry_type="tap", title=t["name"], unique_id=None
                )
                for i, t in enumerate(taps)
            ],
        )
        entry.add_to_hass(hass)
        return entry

    return _make


@pytest.fixture
async def setup(hass: HomeAssistant, make_entry):
    """Set up WaterHA with the given taps; returns (entry, Taps)."""

    async def _setup(taps: list[dict[str, Any]] | None = None, **options: Any):
        hass.states.async_set(TAP, "closed")
        hass.states.async_set(TAP2, "closed")
        entry = make_entry(taps, **options)
        fake = Taps(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        fake.install()
        return entry, fake

    return _setup
