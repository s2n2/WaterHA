"""0.5: water usage and cost."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.waterha.const import DOMAIN

from .conftest import ENTRY_ID, TAP2, tap_data

COST = "sensor.garden_watering_water_cost_today"
USED = "sensor.garden_watering_water_used"
PRICE = "number.garden_watering_water_price"


async def advance(hass: HomeAssistant, freezer, seconds: float, step: float = 30) -> None:
    done = 0.0
    while done < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        done += step


async def test_measured_run_costs_at_default_price(hass: HomeAssistant, setup, rain, freezer):
    rain(0, 0)
    hass.config.currency = "NZD"
    hass.states.async_set("sensor.lawn_flow", "0.6", {"unit_of_measurement": "m³/h"})  # 10 L/min
    await setup([tap_data(amount=100, mode="litres", flow_sensor="sensor.lawn_flow")])
    assert float(hass.states.get(PRICE).state) == 1.48
    assert hass.states.get(PRICE).attributes["unit_of_measurement"] == "NZD/m³"
    await hass.services.async_call("button", "press", {"entity_id": "button.lawn_water_now"}, blocking=True)
    await advance(hass, freezer, 300)
    # Mid-run the totals include water so far.
    assert float(hass.states.get(USED).state) == pytest.approx(50, abs=3)
    await advance(hass, freezer, 330)
    used = float(hass.states.get(USED).state)
    assert used == pytest.approx(100, abs=3)
    cost = hass.states.get(COST)
    assert cost.attributes["unit_of_measurement"] == "NZD"
    assert float(cost.state) == pytest.approx(used * 1.48 / 1000, abs=0.01)
    a = cost.attributes
    assert a["waterha"] == "cost" and a["price"] == 1.48
    assert a["today"]["litres"] == pytest.approx(used, abs=0.2)
    assert a["week"]["litres"] == a["today"]["litres"]
    assert a["per_tap_30d"][0]["name"] == "Lawn"
    assert len(a["series"]) == 31 and a["series"][-1]["taps"]["Lawn"] == pytest.approx(used, abs=0.2)
    assert a["projected_month"]["litres"] >= a["month"]["litres"]


async def test_price_change_recosts_without_reload(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    today = dt_util.now().date().isoformat()
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {"taps": {}, "active": {}, "log": [], "total_litres": 3000.0,
                 "daily": {today: {"tap1": 1000.0}, yesterday: {"tap1": 1500.0, "tap2": 500.0}}},
    }
    entry, _ = await setup([tap_data(), tap_data(name="Roses", tap_entity=TAP2)])
    assert float(hass.states.get(USED).state) == 3000
    assert float(hass.states.get(COST).state) == pytest.approx(1.48)
    await hass.services.async_call("number", "set_value", {"entity_id": PRICE, "value": 2.0}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["water_price"] == 2.0
    a = hass.states.get(COST).attributes
    assert float(hass.states.get(COST).state) == pytest.approx(2.0)
    assert a["week"] == {"litres": 3000.0, "cost": 6.0}
    assert [t["name"] for t in a["per_tap_30d"]] == ["Lawn", "Roses"]
    # Taps without a flow sensor are named as not counted.
    assert a["not_measured"] == ["Lawn", "Roses"]


async def test_old_days_are_dropped(hass: HomeAssistant, setup, rain, hass_storage):
    rain(0, 0)
    ancient = (dt_util.now().date() - timedelta(days=500)).isoformat()
    hass_storage[f"{DOMAIN}.{ENTRY_ID}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{ENTRY_ID}",
        "data": {"taps": {}, "active": {}, "log": [], "daily": {ancient: {"tap1": 10.0}}},
    }
    hass.states.async_set("sensor.lawn_flow", "0.6", {"unit_of_measurement": "m³/h"})
    entry, _ = await setup([tap_data(amount=1, flow_sensor="sensor.lawn_flow")])
    hub = entry.runtime_data
    hub._add_usage("tap1", 5.0, dt_util.utcnow())
    assert ancient not in hub.daily
