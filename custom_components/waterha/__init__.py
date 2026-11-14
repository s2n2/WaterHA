"""WaterHA: rain-aware garden watering for Home Assistant taps and valves."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BACKUP_WEATHER,
    CONF_FLOW_SENSOR,
    CONF_MODE,
    CONF_PAST_RAIN_SENSOR,
    CONF_TAP_ENTITY,
    DOMAIN,
    MODE_LITRES,
    MODE_MINUTES,
    PLATFORMS,
    SOFT_OPTIONS,
)
from .dashboard_resource import async_register_card
from .entity import hub_device
from .hub import WaterHub

_LOGGER = logging.getLogger(__name__)

type WaterConfigEntry = ConfigEntry[WaterHub]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

WATER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("amount"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10000)),
        vol.Optional("mode"): vol.In((MODE_MINUTES, MODE_LITRES)),
    }
)
STOP_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})


def _structure(entry: ConfigEntry) -> tuple[Any, ...]:
    """What, when changed, needs the entry reloaded (entities differ)."""
    taps = tuple(
        sorted(
            (sid, sub.data.get(CONF_TAP_ENTITY), sub.data.get(CONF_MODE), sub.data.get(CONF_FLOW_SENSOR))
            for sid, sub in entry.subentries.items()
        )
    )
    options = tuple(sorted((k, v) for k, v in entry.options.items() if k not in SOFT_OPTIONS))
    return taps, options


def _backups(entry: ConfigEntry) -> tuple[Any, ...]:
    return (
        tuple(entry.options.get(CONF_BACKUP_WEATHER) or ()),
        entry.options.get(CONF_PAST_RAIN_SENSOR),
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the dashboard card and the services."""
    try:
        await async_register_card(hass)
    except Exception:  # noqa: BLE001 - a card problem must never stop watering
        _LOGGER.exception("Could not register the WaterHA card")

    def _targets(call: ServiceCall) -> list[tuple[WaterHub, str]]:
        """Resolve entity ids (any WaterHA tap entity, or the tap switch itself) to taps."""
        registry = er.async_get(hass)
        found: list[tuple[WaterHub, str]] = []
        for entity_id in call.data.get(ATTR_ENTITY_ID, []):
            entry_reg = registry.async_get(entity_id)
            for entry in hass.config_entries.async_loaded_entries(DOMAIN):
                hub: WaterHub = entry.runtime_data
                if entry_reg and entry_reg.config_entry_id == entry.entry_id and entry_reg.config_subentry_id:
                    found.append((hub, entry_reg.config_subentry_id))
                    break
                match = [sid for sid, sub in hub.taps.items() if sub.data.get(CONF_TAP_ENTITY) == entity_id]
                if match:
                    found.append((hub, match[0]))
                    break
            else:
                raise HomeAssistantError(f"{entity_id} is not a WaterHA tap")
        return found

    async def water_now(call: ServiceCall) -> None:
        for hub, tap_id in _targets(call):
            # Without an amount: the tap's amount for today's day type.
            await hub.async_water(tap_id, call.data.get("amount"), call.data.get("mode"), "service")

    async def stop(call: ServiceCall) -> None:
        if call.data.get(ATTR_ENTITY_ID):
            for hub, tap_id in _targets(call):
                await hub.async_stop_tap(tap_id)
            return
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_stop_all()

    async def refresh_rain(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_refresh_rain()

    hass.services.async_register(DOMAIN, "water_now", water_now, schema=WATER_SCHEMA)
    hass.services.async_register(DOMAIN, "stop", stop, schema=STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "refresh_rain", refresh_rain)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    hub = WaterHub(hass, entry)
    entry.runtime_data = hub
    # Created first so each tap's device can name it as its hub.
    hub.device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device(entry)
    ).id
    _remove_old_entities(hass, entry)
    await hub.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Every platform is up: refresh attributes that list other entities (the card's map).
    hub.notify()
    structure = _structure(entry)

    backups = _backups(entry)

    async def _updated(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
        # Settings edited from entities only need a refresh; added/removed taps,
        # a changed tap entity, mode or hub options need new entities.
        nonlocal structure, backups
        new = _structure(entry)
        if new != structure:
            structure = new
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        if _backups(entry) != backups:
            backups = _backups(entry)
            hass.async_create_background_task(hub.async_refresh_rain(), f"{DOMAIN} rain")
        hub.notify()

    entry.async_on_unload(entry.add_update_listener(_updated))
    return True


def _remove_old_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """0.1.x had one amount per tap; 0.2 has cool/warm/hot amounts."""
    registry = er.async_get(hass)
    for tap_id in entry.subentries:
        if entity_id := registry.async_get_entity_id("number", DOMAIN, f"{tap_id}_amount"):
            registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unloaded
