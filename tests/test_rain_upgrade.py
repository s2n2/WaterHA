"""0.8 upgrade: rain credited by 0.7's last scheduled run is not counted again."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.waterha.const import DOMAIN

from .conftest import ENTRY_ID, TAP2, tap_data


async def test_upgrade_seeds_rain_checks_from_log(hass: HomeAssistant, setup, rain, hass_storage):
    when = (dt_util.utcnow() - timedelta(hours=15)).isoformat()
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {"taps": {}, "active": {}, "log": [
            # Monday under 0.7: vege skipped for 6.9 mm, lawn cut to 31% (threshold 10).
            {"tap": "tap1", "name": "Lawn", "start": when, "end": when, "result": "skipped_rain",
             "reason": "schedule", "rain_mm": 6.9, "rain_factor": 0.0},
            {"tap": "tap2", "name": "Roses", "start": when, "end": when, "result": "completed",
             "reason": "schedule", "rain_factor": 0.31, "minutes": 5},
        ]},
    }
    rain(3.1, 0.0)  # what actually fell, all since those checks
    entry, _ = await setup([tap_data(rain_threshold=6), tap_data(name="Roses", tap_entity=TAP2, rain_threshold=10)])
    hub = entry.runtime_data
    assert hub.tap_state("tap1")["rain_check"]["forecast_mm"] == 6.9
    assert hub.tap_state("tap2")["rain_check"]["forecast_mm"] == pytest.approx(6.9)
    for name in ("lawn", "roses"):
        a = hass.states.get(f"sensor.{name}_status").attributes
        assert a["rain_counted"]["fallen_mm"] == 0  # covered by what was credited
        assert a["rain_factor"] == 1.0
