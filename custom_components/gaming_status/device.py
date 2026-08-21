"""Shared per-player HA Device helpers -- one Device per tracked player,
grouping every sensor/button entity that belongs to them regardless of
platform, plus a single non-player "hub" device for the two
integration-wide entities (players-online count, anyone-gaming binary
sensor).

Device identity is keyed on the player's existing safe_owner slug -- the
same value already used for every unique_id/entity_id/Store filename in
this integration -- rather than a new synthetic id. Gaming Status has no
player-rename flow today; adding one would already require solving
unique_id/Store-file continuity independently of this module, so reusing
the existing slug introduces no new instability.

safe_owner_slug() is centralized here because it was previously
recomputed independently in ~15 places across the integration, with one
inconsistent copy (binary_sensor.py skipped the regex sanitization step,
so a player name with characters outside [a-z0-9 ] produced a different
slug there than everywhere else).
"""

from __future__ import annotations

import re

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, PLATFORM_CONFIG

HUB_DEVICE_ID = "hub"


def safe_owner_slug(player_name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", player_name.lower().replace(" ", "_"))


def resolve_registered_entity_id(hass, unique_id: str, guessed_entity_id: str) -> str:
    """Look up a Gaming Status sensor's real, already-registered entity_id
    by its unique_id, instead of trusting a freshly-guessed string built
    from the player's name. Home Assistant silently re-slugifies (via its
    own slugify()) any assigned entity_id containing adjacent/leading/
    trailing underscores -- which safe_owner_slug's output can produce for
    names with adjacent punctuation, e.g. "Phil (ItchyKiller23)" ->
    "phil__itchykiller23_" -- permanently diverging from the guess from
    then on. Falls back to the guess only if nothing is registered yet (a
    brand-new sensor on its first-ever setup) -- self-corrects on the next
    reload once it is."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, unique_id) or guessed_entity_id


def resolve_master_entity_id(hass, player_name: str) -> str:
    safe_owner = safe_owner_slug(player_name)
    return resolve_registered_entity_id(
        hass,
        f"gaming_status_{safe_owner}_master_v6",
        f"sensor.gaming_status_{safe_owner}_master",
    )


def resolve_pc_entity_id(hass, player_name: str) -> str:
    safe_owner = safe_owner_slug(player_name)
    return resolve_registered_entity_id(
        hass,
        f"gaming_status_{safe_owner}_pc_v2",
        f"sensor.gaming_status_{safe_owner}_pc",
    )


def resolve_platform_entity_id(
    hass, player_name: str, platform: str, source_entity_id: str
) -> str:
    safe_owner = safe_owner_slug(player_name)
    return resolve_registered_entity_id(
        hass,
        f"gaming_status_{safe_owner}_{source_entity_id}_tracker_v6",
        f"sensor.gaming_status_{safe_owner}_{platform}",
    )


def player_device_info(
    player_name: str, safe_owner: str, platforms: list[str]
) -> DeviceInfo:
    model = " + ".join(
        PLATFORM_CONFIG[p]["name_suffix"] for p in platforms if p in PLATFORM_CONFIG
    )
    return DeviceInfo(
        identifiers={(DOMAIN, safe_owner)},
        name=player_name,
        manufacturer="Gaming Status",
        model=model or "Player Profile",
    )


def hub_device_info() -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, HUB_DEVICE_ID)},
        name="Gaming Status",
        manufacturer="Gaming Status",
        model="Hub",
    )
