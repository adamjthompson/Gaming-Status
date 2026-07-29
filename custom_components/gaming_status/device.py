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

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, PLATFORM_CONFIG

HUB_DEVICE_ID = "hub"


def safe_owner_slug(player_name: str) -> str:
    return re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))


def player_device_info(player_name: str, safe_owner: str, platforms: list[str]) -> DeviceInfo:
    model = " + ".join(
        PLATFORM_CONFIG[p]["name_suffix"] for p in platforms if p in PLATFORM_CONFIG
    )
    return DeviceInfo(
        identifiers={(DOMAIN, safe_owner)},
        name=player_name,
        manufacturer="Gaming Status",
        model=f"{model} Player Profile" if model else "Player Profile",
    )


def hub_device_info() -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, HUB_DEVICE_ID)},
        name="Gaming Status",
        manufacturer="Gaming Status",
        model="Hub",
    )
