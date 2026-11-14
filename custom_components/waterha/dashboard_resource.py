"""Serve the bundled WaterHA card and register it as a Lovelace resource.

HACS installs only custom_components/waterha, so the card is served from
there. Only our own resource is ever created or updated.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import VERSION

_LOGGER = logging.getLogger(__name__)

CARD_PATH = "/waterha/waterha-card.js"
CARD_URL = f"{CARD_PATH}?v={VERSION}"
CARD_FILE = Path(__file__).parent / "frontend" / "waterha-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_PATH, str(CARD_FILE), False)]
        )
    except RuntimeError:
        _LOGGER.debug("WaterHA card path is already registered")

    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        return
    mode = getattr(lovelace, "resource_mode", getattr(lovelace, "mode", "storage"))
    if mode != "storage":
        _LOGGER.info("Lovelace resources are in YAML mode; add %s as a module", CARD_URL)
        return
    resources = lovelace.resources
    # Load saved resources first so we never overwrite the user's list.
    if hasattr(resources, "async_get_info"):
        await resources.async_get_info()
    elif not getattr(resources, "loaded", False):
        await resources.async_load()
    for item in resources.async_items():
        if urlsplit(item.get("url", "")).path == CARD_PATH:
            if item.get("url") != CARD_URL:
                await resources.async_update_item(item["id"], {"url": CARD_URL, "res_type": "module"})
            return
    await resources.async_create_item({"url": CARD_URL, "res_type": "module"})
