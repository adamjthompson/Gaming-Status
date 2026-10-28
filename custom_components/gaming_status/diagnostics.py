"""Config-entry diagnostics (Settings -> Devices & services -> Gaming Status
-> Download diagnostics), so a user's bug report can carry the integration's
real state instead of a round of "turn on debug logging and send logs"."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.loader import async_get_integration

from .const import DOMAIN

REDACTED = "**REDACTED**"

# Pattern match (same approach as public_page._DENY_ATTR_RE) so a credential
# or contact field added later is redacted without touching this file.
# Gamertags are deliberately kept -- they're what a bug report is about.
_REDACT_KEY_RE = re.compile(
    r"(token|npsso|oauth|password|secret|api_key|webhook|target|phone|email|"
    r"xuid|account_id|user_id|steam_?id|online_id|discord_server|backup)",
    re.IGNORECASE,
)
# Exact-match keys too generic for the pattern above: a player's "discord"
# slot holds their Discord user ID.
_REDACT_EXACT_KEYS = {"discord"}
# Secrets embedded inside otherwise-useful string values: image_proxy access
# tokens, and the Discord user ID in avatar URLs.
_REDACT_IN_STRING = (
    (re.compile(r"([?&]token=)[^&]+"), r"\1" + REDACTED),
    (re.compile(r"(cdn\.discordapp\.com/avatars/)\d+"), r"\1" + REDACTED),
)

# Library sensors carry whole game lists; a few samples are enough to see
# their shape without making the download megabytes long.
_MAX_LIST_ITEMS = 5


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED
            if isinstance(k, str)
            and v
            and (_REDACT_KEY_RE.search(k) or k in _REDACT_EXACT_KEYS)
            else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        items = [_redact(v) for v in value[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            items.append(f"... {len(value) - _MAX_LIST_ITEMS} more")
        return items
    if isinstance(value, str) and value[:1] in "{[":
        # Several options (players, endpoints, ...) are stored as JSON text.
        try:
            return _redact(json.loads(value))
        except ValueError:
            return value
    if isinstance(value, str):
        for pattern, repl in _REDACT_IN_STRING:
            value = pattern.sub(repl, value)
    return value


def _rate_limiters(hass: HomeAssistant) -> dict[str, Any]:
    now = time.monotonic()
    out = {}
    for name, limiter in hass.data.get("gaming_status_rate_limiters", {}).items():
        blocked_until = getattr(limiter, "_blocked_until", None)
        out[name] = {
            "tokens": round(getattr(limiter, "_tokens", 0.0), 2),
            "capacity": getattr(limiter, "_capacity", None),
            "blocked_for_seconds": round(blocked_until - now, 1)
            if blocked_until and blocked_until > now
            else 0,
        }
    return out


def _library_coordinators(hass: HomeAssistant) -> dict[str, Any]:
    out = {}
    coordinators = hass.data.get(DOMAIN, {}).get("library_coordinators", {})
    for key, coordinator in coordinators.items():
        data = coordinator.data or {}
        out[key] = {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval": str(coordinator.update_interval),
            "last_synced": data.get("last_synced"),
            "platforms": {
                platform: {
                    k: v
                    for k, v in (pdata or {}).items()
                    if k != "games" and not isinstance(v, list | dict)
                }
                | {"game_count": len((pdata or {}).get("games") or [])}
                for platform, pdata in (data.get("platforms") or {}).items()
            },
        }
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    integration = await async_get_integration(hass, DOMAIN)

    entity_reg = er.async_get(hass)
    entities = {}
    for reg_entry in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        state = hass.states.get(reg_entry.entity_id)
        entities[reg_entry.entity_id] = {
            "disabled_by": reg_entry.disabled_by,
            "state": state.state if state else None,
            "last_changed": state.last_changed.isoformat() if state else None,
            "attributes": _redact(dict(state.attributes)) if state else None,
        }

    issues = [
        {"issue_id": issue.issue_id, "translation_key": issue.translation_key}
        for (domain, _), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue.active
    ]

    return {
        "version": str(integration.version),
        "entry": {
            "version": entry.version,
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
        "entities": entities,
        "library_scans": _library_coordinators(hass),
        "rate_limiters": _rate_limiters(hass),
        "repairs_issues": issues,
    }
