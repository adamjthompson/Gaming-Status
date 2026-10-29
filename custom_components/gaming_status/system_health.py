"""System health info (Settings -> System -> Repairs -> System information),
so a user can check API reachability and scan/rate-limit status themselves
before filing an issue. Everything here is computed only when that page is
opened -- nothing polls in the background."""

from __future__ import annotations

import json
import time
from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, OPT_PLAYERS, PSN_TROPHY_API_BASE, STEAM_API_BASE

_STEAMGRIDDB_URL = "https://www.steamgriddb.com/api/v2"
# python-xbox's people/presence endpoint host.
_XBOX_URL = "https://peoplehub.xboxlive.com"


@callback
def async_register(
    hass: HomeAssistant,  # noqa: ARG001 -- required by the system_health platform API
    register: system_health.SystemHealthRegistration,
) -> None:
    register.async_register_info(_system_health_info)


def _player_count(hass: HomeAssistant) -> int:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return 0
    raw = entries[0].options.get(OPT_PLAYERS) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except ValueError:
            return 0
    return len(raw) if isinstance(raw, dict) else 0


def _library_scans(hass: HomeAssistant) -> str:
    coordinators = list(
        hass.data.get(DOMAIN, {}).get("library_coordinators", {}).values()
    )
    if not coordinators:
        return "off"
    failed = [c.owner_name for c in coordinators if not c.last_update_success]
    if failed:
        return f"last scan failed for {', '.join(failed)}"
    return f"ok ({len(coordinators)} players)"


def _rate_limits(hass: HomeAssistant) -> str:
    now = time.monotonic()
    blocked = [
        name
        for name, limiter in hass.data.get("gaming_status_rate_limiters", {}).items()
        if (until := getattr(limiter, "_blocked_until", None)) and until > now
    ]
    return f"backing off: {', '.join(blocked)}" if blocked else "ok"


def _steamgriddb_key() -> str:
    from . import utils

    if not utils.STEAMGRIDDB_API_KEY:
        return "not set"
    return "ok" if utils.steamgriddb_key_usable() else "rejected"


def _discord_bot(hass: HomeAssistant) -> str | None:
    bot = hass.data.get(DOMAIN, {}).get("discord_bot")
    if bot is None:
        return None
    return "connected" if bot.is_ready() else "not connected"


async def _system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    info: dict[str, Any] = {
        "players": _player_count(hass),
        "steam_reachable": system_health.async_check_can_reach_url(
            hass, STEAM_API_BASE
        ),
        "psn_reachable": system_health.async_check_can_reach_url(
            hass, PSN_TROPHY_API_BASE
        ),
        "xbox_reachable": system_health.async_check_can_reach_url(hass, _XBOX_URL),
        "steamgriddb_reachable": system_health.async_check_can_reach_url(
            hass, _STEAMGRIDDB_URL
        ),
        "steamgriddb_api_key": _steamgriddb_key(),
        "library_scans": _library_scans(hass),
        "rate_limits": _rate_limits(hass),
        "repairs_issues": sum(
            1
            for (domain, _), issue in ir.async_get(hass).issues.items()
            if domain == DOMAIN and issue.active
        ),
    }
    if (discord := _discord_bot(hass)) is not None:
        info["discord_bot"] = discord
    return info
