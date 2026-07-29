"""Full game-library scan subsystem -- opt-in, nested under
OPT_ENABLE_ACHIEVEMENT_TRACKING (see const.py): every game a player has
ever played, not just the one currently running. Unlike the rest of Gaming
Status (which predates DataUpdateCoordinator and uses ad hoc event-driven
updates), this is a genuine "poll on a long interval, many entities read
one shared result" shape, so it uses HA's idiomatic tool for exactly that.

One LibraryScanCoordinator per player, covering every platform (steam/xbox/
playstation) that player already has a PersistentStatusSensor for -- reuses
the exact same credential-resolution helpers (utils.resolve_*) as the
current-game-only enrichment, so no separate credential setup is needed.

No multi-cycle backfill/budgeting machinery needed here: Xbox's
title-history call already returns every title's achievement/gamerscore
summary in ONE request (no per-title lookup needed), and PSN's full
trophy-titles list is similarly a handful of paginated requests, not one
per game. Only Steam genuinely needs one achievement call per owned game
(Steam's Web API has no bulk equivalent) -- for a very large library this
can take a while on the first scan, which is acceptable for a background
job on a multi-hour interval; the per-appid schema (achievement totals) is
cached forever afterward (see utils.STEAM_SCHEMA_CACHE), so only the
earned-count call repeats on later scans.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import utils
from .utils import _normalize_game_name, _safe_parse_datetime

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_TIER_KEYS = ("bronze", "silver", "gold", "platinum")


def _percent(earned, total):
    if not total:
        return None
    return round(100 * earned / total, 1)


class LibraryScanCoordinator(DataUpdateCoordinator):
    """One per player. `platform_sources` is {"steam"|"xbox"|"playstation":
    source_entity_id} for whichever platforms that player already has a
    PersistentStatusSensor for -- passed in directly from
    sensor.py's async_setup_entry, since that's exactly where these are
    already known, rather than re-deriving them via another entity-registry
    scan."""

    def __init__(self, hass, owner_name, platform_sources, scan_interval_hours):
        safe_owner = re.sub(r'[^a-z0-9_]', '_', owner_name.lower().replace(" ", "_"))
        super().__init__(
            hass, _LOGGER,
            name=f"gaming_status_library_{safe_owner}",
            update_interval=timedelta(hours=scan_interval_hours),
        )
        self._owner_name = owner_name
        self.owner_name = owner_name  # public -- read by button.py's entity naming
        self._platform_sources = platform_sources
        self._store = Store(hass, _STORAGE_VERSION, f"gaming_status_library_{safe_owner}")
        # {normalized_title: {"grid":.., "hero":.., "logo":.., "icon":..}} --
        # resolved SteamGridDB URLs rarely change, so persisting this means
        # repeat scans skip the search+asset lookup entirely for games
        # already resolved (only new/never-seen titles cost anything).
        self._art_cache = {}

    async def async_load_stored(self):
        """Restore the last scan's result + resolved-art cache immediately
        on startup, so a restart doesn't blank the sensors or force an
        immediate rescan across every tracked player at once."""
        stored = await self._store.async_load()
        if stored:
            self._art_cache = stored.get("art_cache") or {}
            self.data = stored.get("data")

    async def async_schedule_or_refresh(self):
        """Call after async_load_stored(), instead of an unconditional
        async_refresh(). A full scan only actually fires here if the
        restored data is missing or already older than one full scan
        interval -- otherwise this just resumes the periodic schedule from
        wherever it should naturally be (based on the ORIGINAL last-scan
        time, not now), via a one-shot delayed callback.

        This matters because Gaming Status reloads its whole config entry
        on ANY options save (see __init__.py's _async_options_updated) --
        without this guard, editing something unrelated (a title override,
        a grace period) would force a brand-new full library scan on top of
        one that may have completed minutes ago, hammering Steam/Xbox/PSN
        for no reason every time settings get touched."""
        last_synced = _safe_parse_datetime((self.data or {}).get("last_synced"))
        remaining = 0.0
        if last_synced:
            elapsed = (dt_util.now() - last_synced).total_seconds()
            remaining = self.update_interval.total_seconds() - elapsed

        if remaining <= 0:
            await self.async_refresh()
        else:
            _LOGGER.debug(
                "Gaming Status: library scan for %s already fresh (next due in %.0fs) -- "
                "resuming schedule instead of rescanning now.",
                self._owner_name, remaining,
            )
            async_call_later(self.hass, remaining, self._handle_scheduled_refresh)

    async def _handle_scheduled_refresh(self, _now):
        await self.async_refresh()

    async def _async_update_data(self):
        raw_by_platform = {}
        if "steam" in self._platform_sources:
            raw_by_platform["steam"] = await self._scan_steam(self._platform_sources["steam"])
        if "xbox" in self._platform_sources:
            raw_by_platform["xbox"] = await self._scan_xbox(self._platform_sources["xbox"])
        if "playstation" in self._platform_sources:
            raw_by_platform["playstation"] = await self._scan_psn(self._platform_sources["playstation"])

        result = _aggregate(raw_by_platform)
        await self._store.async_save({"data": result, "art_cache": self._art_cache})
        return result

    async def _async_art_for(self, title):
        """External-URL-only SteamGridDB lookup (see
        utils.fetch_game_grid_urls_remote) -- never downloaded/cached
        locally, unlike the current-game sensors' artwork. Persisted here by
        resolved title so a 12h+ rescan doesn't repeat the search for games
        already resolved."""
        if not utils.STEAMGRIDDB_API_KEY or not title:
            return {"grid": None, "hero": None, "logo": None, "icon": None}
        cache_key = _normalize_game_name(title)
        if cache_key in self._art_cache:
            return self._art_cache[cache_key]
        art = await utils.fetch_game_grid_urls_remote(self.hass, title)
        if any(art.values()):
            self._art_cache[cache_key] = art
        return art

    async def _scan_steam(self, source_entity_id):
        api_key, steamid64 = utils.resolve_steam_credentials(self.hass, source_entity_id)
        if not api_key or not steamid64:
            return {"games": [], "error": "not_configured"}

        owned_games = await utils.fetch_steam_owned_games(self.hass, api_key, steamid64)
        games = []
        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name:
                continue
            result = await utils.fetch_steam_achievements(self.hass, steamid64, api_key, appid)
            earned = (result or {}).get("earned", 0)
            total = (result or {}).get("total", 0)
            art = await self._async_art_for(name)
            games.append({
                "title": name, "platform": "steam", "id": str(appid),
                "achievements_earned": earned, "achievements_total": total,
                "percent": _percent(earned, total),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
            })
        return {"games": games, "error": None}

    async def _scan_xbox(self, source_entity_id):
        entry, session, xuid = await utils.resolve_xbox_entry_and_session(self.hass, source_entity_id)
        if not entry or not session or not xuid:
            return {"games": [], "error": "not_configured"}

        titles = await utils.fetch_xbox_title_history(self.hass, entry, session, xuid)
        games = []
        for title in titles:
            name = getattr(title, "name", None)
            if not name:
                continue
            achievement = getattr(title, "achievement", None)
            earned = getattr(achievement, "current_achievements", 0) or 0
            total = getattr(achievement, "total_achievements", 0) or 0
            gs_earned = getattr(achievement, "current_gamerscore", 0) or 0
            gs_total = getattr(achievement, "total_gamerscore", 0) or 0
            art = await self._async_art_for(name)
            games.append({
                "title": name, "platform": "xbox", "id": str(getattr(title, "title_id", "") or ""),
                "achievements_earned": earned, "achievements_total": total,
                "gamerscore_earned": gs_earned, "gamerscore_total": gs_total,
                "percent": _percent(earned, total),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
            })
        return {"games": games, "error": None}

    async def _scan_psn(self, source_entity_id):
        npsso, account_id = utils.resolve_psn_credentials(self.hass, source_entity_id)
        if not npsso or not account_id:
            return {"games": [], "error": "not_configured"}

        titles = await utils.fetch_psn_full_library(self.hass, npsso, account_id)
        games = []
        for title in titles:
            name = title.get("trophyTitleName")
            if not name:
                continue
            earned = title.get("earnedTrophies") or {}
            defined = title.get("definedTrophies") or {}
            earned_counts = {k: int(earned.get(k, 0)) for k in _TIER_KEYS}
            total_counts = {k: int(defined.get(k, 0)) for k in _TIER_KEYS}
            total_earned = sum(earned_counts.values())
            total_defined = sum(total_counts.values())
            art = await self._async_art_for(name)
            games.append({
                "title": name, "platform": "playstation", "id": title.get("npCommunicationId"),
                "achievements_earned": total_earned, "achievements_total": total_defined,
                "trophies_earned": earned_counts, "trophies_total": total_counts,
                "percent": _percent(total_earned, total_defined),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
            })
        return {"games": games, "error": None}


def _aggregate(raw_by_platform):
    """Pure function (no I/O) -- turns the per-platform raw scan results
    into the summary + per-platform shapes the two new sensors read
    directly."""
    all_games = []
    tracked_platforms = []
    platform_errors = {}
    platform_summaries = {}

    for platform, raw in raw_by_platform.items():
        games = raw.get("games") or []
        tracked_platforms.append(platform)
        if raw.get("error"):
            platform_errors[platform] = raw["error"]

        summary = {
            "achievements_earned": sum(g["achievements_earned"] for g in games),
            "achievements_total": sum(g["achievements_total"] for g in games),
            "game_count": len(games),
            "games": games,
        }
        if platform == "xbox":
            summary["gamerscore_earned"] = sum(g.get("gamerscore_earned", 0) for g in games)
            summary["gamerscore_total"] = sum(g.get("gamerscore_total", 0) for g in games)
        elif platform == "playstation":
            summary["trophies_earned"] = {k: sum(g.get("trophies_earned", {}).get(k, 0) for g in games) for k in _TIER_KEYS}
            summary["trophies_total"] = {k: sum(g.get("trophies_total", {}).get(k, 0) for g in games) for k in _TIER_KEYS}
        platform_summaries[platform] = summary
        all_games.extend(games)

    percents = [g["percent"] for g in all_games if g["percent"] is not None]

    return {
        "total_achievements_earned": sum(g["achievements_earned"] for g in all_games),
        "total_achievements_possible": sum(g["achievements_total"] for g in all_games),
        "total_gamerscore": sum(g.get("gamerscore_earned", 0) for g in all_games if g["platform"] == "xbox"),
        "total_platinum_trophies": sum(
            g.get("trophies_earned", {}).get("platinum", 0) for g in all_games if g["platform"] == "playstation"
        ),
        "average_completion_percent": round(sum(percents) / len(percents), 1) if percents else None,
        "game_count": len(all_games),
        "tracked_platforms": tracked_platforms,
        "last_sync_success": not platform_errors,
        "platform_errors": platform_errors or None,
        "games": all_games,
        "platforms": platform_summaries,
        "last_synced": dt_util.now().isoformat(),
    }
