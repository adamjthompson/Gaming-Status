"""Registers the Gaming Status icon set (frontend/gaming-status-icons.js)
with Home Assistant's frontend.

Material Design Icons has no Epic Games, Ubisoft, Discord or Playnite logo.
The script registers them as a custom icon set under the "gaming-status"
prefix, so entities (and Gaming Status Cards) can use "gaming-status:epic"
etc. exactly like an "mdi:" icon: drawn inline, in the active theme's
colors. It's served from the integration's own folder and added to every
frontend page; the public status page loads it too.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STATIC_URL = "/gaming_status_static"
_ICON_FILE = "gaming-status-icons.js"
_REGISTERED_KEY = "gaming_status_icon_set_url"


async def async_register_icon_set(hass: HomeAssistant) -> None:
    """Serve and register the icon set once per Home Assistant run (static
    paths and frontend scripts can't be removed, so reloads skip this)."""
    if hass.data.get(_REGISTERED_KEY):
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    _STATIC_URL, str(Path(__file__).parent / "frontend"), True
                )
            ]
        )
    except RuntimeError as err:
        # Already registered by an earlier setup in this run.
        _LOGGER.debug("Gaming Status icon set path already registered: %s", err)
    integration = await async_get_integration(hass, DOMAIN)
    # The version busts browser caches when the icons change with an update.
    url = f"{_STATIC_URL}/{_ICON_FILE}?v={integration.version}"
    add_extra_js_url(hass, url)
    hass.data[_REGISTERED_KEY] = url


def icon_set_url(hass: HomeAssistant) -> str | None:
    """The icon set script's URL, once registered (for the public page)."""
    return hass.data.get(_REGISTERED_KEY)
