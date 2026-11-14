"""Hub config flow, options and the Add tap subentry flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.waterha.const import DOMAIN

from .conftest import FLOW, TAP, TAP2, tap_data


async def test_user_flow_creates_single_hub(hass: HomeAssistant, rain):
    rain(0, 0)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    hub = {"cool_below": 16, "hot_from": 23}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"forecast_source": "weather_entity", **hub}
    )
    assert result["errors"] == {"weather_entity": "weather_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"forecast_source": "open_meteo", "cool_below": 20, "hot_from": 18}
    )
    assert result["errors"] == {"hot_from": "splits_order"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"forecast_source": "open_meteo", "cool_below": 14, "hot_from": 25}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"forecast_source": "open_meteo", "cool_below": 14.0, "hot_from": 25.0}
    await hass.async_block_till_done()

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_add_tap_subentry_validates_and_creates(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup([])
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "tap"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    data = tap_data(name="Roses", tap_entity=TAP2, mode="litres", amount=40)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["errors"] == {"flow_sensor": "flow_required"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**data, "flow_sensor": FLOW, "days": ["sun", "mon"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.title == "Roses"
    assert sub.data["days"] == ["mon", "sun"]
    await hass.async_block_till_done()
    # A new tap needs new entities, so the entry reloaded and made them.
    assert hass.states.get("sensor.roses_status") is not None


async def test_same_tap_cannot_be_added_twice(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "tap"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], tap_data(name="Again", tap_entity=TAP)
    )
    assert result["errors"] == {"tap_entity": "already_added"}


async def test_reconfigure_tap(hass: HomeAssistant, setup, rain):
    rain(0, 0)
    entry, _ = await setup()
    result = await entry.start_subentry_reconfigure_flow(hass, "tap1")
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], tap_data(rain_threshold=12, start_time="19:30:00")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries["tap1"].data["rain_threshold"] == 12.0
    await hass.async_block_till_done()
    assert hass.states.get("time.lawn_start_time").state == "19:30:00"
