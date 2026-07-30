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

Xbox's title-history call and PSN's full trophy-titles list each already
return every title's achievement/gamerscore SUMMARY (counts) in one/a
handful of bulk requests -- no per-title lookup needed for that. Only
Steam genuinely needs one call per owned game for even the summary
(Steam's Web API has no bulk equivalent) -- acceptable on a multi-hour
interval; the per-appid schema is cached forever afterward (see
utils.STEAM_SCHEMA_CACHE).

Getting real per-achievement UNLOCK DETAIL (name/timestamp, not just
counts) for recent_achievements IS a genuine per-title cost for Xbox/PSN,
though -- so this module does have multi-cycle backfill/budgeting
machinery after all, just confined to that narrower need. A free,
already-fetched per-title "last activity" timestamp (Xbox:
title_history.last_time_played; PSN: lastUpdatedDateTime, confirmed to
specifically track "last trophy earned") lets a normal scan cheaply
detect which titles changed since last time and only pay for a detail
call on those (see the _activity_cursor diff logic in _scan_xbox/
_scan_psn). Separately, a paced, budgeted backfill pass
(async_run_backfill_pass, driven by an independent timer in __init__.py,
decoupled from this coordinator's own scan interval) walks through titles
never yet resolved a few at a time, so a large existing library doesn't
get flooded with per-title calls in one burst just to seed real
historical data. Steam needs none of this -- its per-game achievement
call already returns recent_unlocks for free, every scan.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import utils
from .utils import _normalize_game_name, _safe_parse_datetime
from .device import safe_owner_slug
from .const import (
    DOMAIN,
    PLATFORM_CONFIG,
    XBOX_LIBRARY_BACKFILL_BUDGET_PER_CYCLE,
    PSN_LIBRARY_BACKFILL_BUDGET_PER_CYCLE,
)

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_TIER_KEYS = ("bronze", "silver", "gold", "platinum")


def _percent(earned, total):
    # total == 0 covers both "this title genuinely has no achievements" and
    # any remaining bad-data case -- either way 0.0% is the honest display
    # value; returning None here used to surface as the literal string
    # "None%" in dashboards.
    if not total:
        return 0.0
    return round(100 * earned / total, 1)


def _target_sensor(hass, owner_name, platform):
    """Resolves the live PersistentStatusSensor entity for one player's
    platform -- the same hass.data[DOMAIN]["platform_sensors"] lookup
    __init__.py's rename_game/delete_game services already use. Lets
    library-scan-discovered achievements feed into the exact same
    recent_achievements history/Store the real-time tracker maintains,
    rather than needing a separate one. Returns None if that sensor
    doesn't exist (e.g. mid-reload) -- callers must handle that."""
    safe_owner = safe_owner_slug(owner_name)
    return hass.data.get(DOMAIN, {}).get("platform_sensors", {}).get(
        f"sensor.gaming_status_{safe_owner}_{platform}"
    )


def _dominant_color_for(sensor_obj, game_name):
    """Best-effort read of a sensor's own persisted per-game color cache --
    no new local-download/extraction pipeline is added to this module just
    for library-scan-discovered unlocks; a title never actually played
    through this integration's own current-game tracking simply has no
    entry yet, and the frontend already renders that gracefully."""
    entry = sensor_obj._color_history_cache.get(game_name)
    if isinstance(entry, dict):
        return entry.get("color")
    return None


class LibraryScanCoordinator(DataUpdateCoordinator):
    """One per player. `platform_sources` is {"steam"|"xbox"|"playstation":
    source_entity_id} for whichever platforms that player already has a
    PersistentStatusSensor for -- passed in directly from
    sensor.py's async_setup_entry, since that's exactly where these are
    already known, rather than re-deriving them via another entity-registry
    scan."""

    def __init__(self, hass, owner_name, platform_sources, scan_interval_hours, excluded_games=None):
        safe_owner = safe_owner_slug(owner_name)
        super().__init__(
            hass, _LOGGER,
            name=f"gaming_status_library_{safe_owner}",
            update_interval=timedelta(hours=scan_interval_hours),
        )
        self._owner_name = owner_name
        self.owner_name = owner_name  # public -- read by button.py's entity naming
        self._platform_sources = platform_sources
        # Same global + per-player exclusion lists PersistentStatusSensor
        # already applies to "currently playing" tracking -- normalized the
        # same way (_normalize_game_name) so a title excluded there is
        # excluded here too, regardless of punctuation/casing differences.
        self._excluded_normalized = {_normalize_game_name(g) for g in (excluded_games or [])}
        self._store = Store(hass, _STORAGE_VERSION, f"gaming_status_library_{safe_owner}")
        # {normalized_title: {"grid":.., "hero":.., "logo":.., "icon":..}} --
        # resolved SteamGridDB URLs rarely change, so persisting this means
        # repeat scans skip the search+asset lookup entirely for games
        # already resolved (only new/never-seen titles cost anything).
        self._art_cache = {}
        # {"xbox"|"playstation": {title_id: iso_timestamp}} -- per-title
        # "last known activity" cursor for delta detection (see _scan_xbox/
        # _scan_psn). Steam needs none: its per-game achievement call
        # already returns recent_unlocks at zero extra cost every scan.
        self._activity_cursor = {"xbox": {}, "playstation": {}}
        # {"xbox"|"playstation": {title_id: True}} -- which titles have
        # ever had their real per-achievement/trophy unlock DETAIL
        # successfully resolved for THIS player. Deliberately per-player
        # (not shared across players the way Trophy Hub's achievement-total
        # cache is), since this tracks per-player unlock HISTORY, not a
        # game-intrinsic fact like an achievement's total count.
        self._backfill_done = {"xbox": {}, "playstation": {}}

    async def async_load_stored(self):
        """Restore the last scan's result + resolved-art cache immediately
        on startup, so a restart doesn't blank the sensors or force an
        immediate rescan across every tracked player at once."""
        stored = await self._store.async_load()
        if stored:
            self._art_cache = stored.get("art_cache") or {}
            self._activity_cursor = stored.get("activity_cursor") or {"xbox": {}, "playstation": {}}
            self._backfill_done = stored.get("backfill_done") or {"xbox": {}, "playstation": {}}
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
        previous_platforms = (self.data or {}).get("platforms", {})
        raw_by_platform = {}
        if "steam" in self._platform_sources:
            raw_by_platform["steam"] = await self._scan_platform_safely(
                "steam", self._scan_steam, self._platform_sources["steam"], previous_platforms
            )
        if "xbox" in self._platform_sources:
            raw_by_platform["xbox"] = await self._scan_platform_safely(
                "xbox", self._scan_xbox, self._platform_sources["xbox"], previous_platforms
            )
        if "playstation" in self._platform_sources:
            raw_by_platform["playstation"] = await self._scan_platform_safely(
                "playstation", self._scan_psn, self._platform_sources["playstation"], previous_platforms
            )

        result = _aggregate(raw_by_platform)
        await self._store.async_save({
            "data": result, "art_cache": self._art_cache,
            "activity_cursor": self._activity_cursor, "backfill_done": self._backfill_done,
        })
        return result

    async def _scan_platform_safely(self, platform, scan_fn, source_entity_id, previous_platforms):
        """Wraps one platform's scan so a single platform's failure can
        never (a) crash the WHOLE coordinator update -- one
        LibraryScanCoordinator covers every platform for a given player, so
        an uncaught exception from just one of them would otherwise mark
        ALL of that player's library sensors "Unavailable" -- or (b) zero
        out that platform's own contribution to the totals just because its
        top-level bulk list fetch failed (e.g. a Xbox title-history
        ReadTimeout), as opposed to a single game/title within it, which the
        per-title loops inside _scan_steam/_scan_xbox/_scan_psn already
        protect. Falls back to this platform's own last-known-good games
        list on either failure mode -- the same "never regress on a fetch
        failure" principle already applied per-game, just one level up."""
        try:
            raw = await scan_fn(source_entity_id)
        except Exception as e:
            _LOGGER.warning(
                "Gaming Status: %s library scan crashed for %s -- keeping last known data: %s: %s",
                platform, self._owner_name, type(e).__name__, e,
            )
            raw = {"games": [], "error": f"{type(e).__name__}: {e}"}

        if not raw.get("games") and raw.get("error"):
            previous_games = previous_platforms.get(platform, {}).get("games")
            if previous_games:
                _LOGGER.debug(
                    "Gaming Status: %s library scan failed for %s (%s) -- falling back to last "
                    "known %d games instead of zeroing this platform's totals for this cycle",
                    platform, self._owner_name, raw["error"], len(previous_games),
                )
                raw = {**raw, "games": previous_games}

        return raw

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

        owned_games, fetch_error = await utils.fetch_steam_owned_games(self.hass, api_key, steamid64)
        games = []
        target_sensor = _target_sensor(self.hass, self._owner_name, "steam")
        # Keyed by appid (str) -- lets a per-game fetch failure below fall
        # back to the last successfully-scanned value instead of silently
        # zeroing a game out of the total. A whole-list fetch failure is
        # already visible via `error` above; a single game's fetch failing
        # partway through this loop (Steam has no bulk achievement endpoint,
        # so this is one call per owned game, every scan -- a real target
        # for transient rate-limiting under repeated rescans) previously had
        # no fallback and no visibility at all: it just silently contributed
        # 0/0 to that scan's total, corrupting the aggregate with nothing
        # showing up in platform_errors to explain it.
        previous_games = {
            g.get("id"): g
            for g in (self.data or {}).get("platforms", {}).get("steam", {}).get("games", [])
        }
        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name or _normalize_game_name(name) in self._excluded_normalized:
                continue
            # Apply the user's Title Overrides + display cleanup here, same
            # as the real-time "currently playing" pipeline already does
            # (sensor.py's _unified_update) -- otherwise a game discovered
            # via the library scan (as opposed to actually being played
            # right now) shows its raw, un-overridden platform title in
            # recent_achievements and the Library sensor's games list.
            name = utils._format_game_name_for_display(name)
            result = await utils.fetch_steam_achievements(self.hass, steamid64, api_key, appid)
            if result is not None:
                earned = result.get("earned", 0)
                total = result.get("total", 0)
            else:
                # Fetch failed -- keep this game's last known counts rather
                # than reporting 0/0 for it this cycle. Only a brand-new,
                # never-successfully-scanned game has nothing to fall back
                # to, in which case 0/0 is the best available answer.
                previous = previous_games.get(str(appid))
                earned = previous.get("achievements_earned", 0) if previous else 0
                total = previous.get("achievements_total", 0) if previous else 0
                _LOGGER.debug(
                    "Gaming Status: Steam achievement fetch failed for %s (appid %s) -- "
                    "keeping last known count (%s/%s) instead of zeroing it",
                    name, appid, earned, total,
                )
            art = await self._async_art_for(name)
            # Steam's per-game achievement call already returns
            # recent_unlocks at zero extra cost (unlike Xbox/PSN) -- feed
            # it straight into the real-time sensor's own accumulating
            # recent_achievements history, so a Steam achievement earned on
            # a game that isn't currently being played still shows up, with
            # no delta/backfill machinery needed for this platform at all.
            if target_sensor is not None and result and result.get("recent_unlocks"):
                target_sensor._ingest_recent_unlocks(
                    result["recent_unlocks"], game_name=name,
                    platform_label=PLATFORM_CONFIG.get("steam", {}).get("name_suffix", "Steam"),
                    hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, name),
                )
            games.append({
                "title": name, "platform": "steam", "id": str(appid),
                "achievements_earned": earned, "achievements_total": total,
                "percent": _percent(earned, total),
                # playtime_forever is already part of the GetOwnedGames
                # response fetch_steam_owned_games returns -- no extra call.
                "playtime_hours": round((game.get("playtime_forever") or 0) / 60, 1),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
            })
        return {"games": games, "error": fetch_error}

    async def _scan_xbox(self, source_entity_id):
        entry, session, xuid = await utils.resolve_xbox_entry_and_session(self.hass, source_entity_id)
        if not entry or not session or not xuid:
            return {"games": [], "error": "not_configured"}

        titles, fetch_error = await utils.fetch_xbox_title_history(self.hass, entry, session, xuid)
        games = []
        target_sensor = _target_sensor(self.hass, self._owner_name, "xbox")
        xbox_cursor = self._activity_cursor.setdefault("xbox", {})
        xbox_done = self._backfill_done.setdefault("xbox", {})
        # Keyed by title_id (str) -- same "never let a per-title count go
        # backward" floor Steam's per-appid loop already has, generalized
        # here since achievements/gamerscore can only ever increase for a
        # real player. Xbox's bulk title-history response has no per-title
        # error signal (unlike Steam's one-call-per-game loop, there's
        # nothing to catch a "this one fetch failed" exception on) -- but
        # Xbox Live's own achievement sync is documented as asynchronous/
        # eventually-consistent (Microsoft states this can take up to 72h),
        # so a given scan's bulk data for one title can be transiently
        # stale/incomplete relative to what a previous scan already
        # correctly recorded. Without this floor, that shows up as the
        # whole-library total visibly dropping and not recovering until
        # the next scan happens to catch fresher data.
        previous_games = {
            g.get("id"): g
            for g in (self.data or {}).get("platforms", {}).get("xbox", {}).get("games", [])
        }
        for title in titles:
            name = getattr(title, "name", None)
            if not name or _normalize_game_name(name) in self._excluded_normalized:
                continue
            # Apply the user's Title Overrides + display cleanup, matching
            # the real-time "currently playing" pipeline (see the Steam
            # scan above for the full rationale).
            name = utils._format_game_name_for_display(name)
            achievement = getattr(title, "achievement", None)
            earned = getattr(achievement, "current_achievements", 0) or 0
            total = getattr(achievement, "total_achievements", 0) or 0
            title_id = str(getattr(title, "title_id", "") or "")
            art = await self._async_art_for(name)

            # title-history's own totalAchievements is live-confirmed
            # unreliable for some titles (observed 0 there despite a
            # nonzero currentAchievements for the same title) -- total <
            # earned is otherwise impossible, so treat it as a sure sign
            # this title's summary is bad and re-fetch an authoritative
            # count from the per-title endpoint instead of showing a
            # misleadingly-low percent.
            if title_id and total < earned:
                detail = await utils.fetch_xbox_title_achievement_counts(
                    self.hass, entry, session, xuid, title_id, recent_limit=utils.RECENT_UNLOCKS_LIMIT
                )
                if detail:
                    earned = detail.get("earned", earned)
                    total = detail.get("total", total)
                    # Free bonus: this call already returns recent_unlocks,
                    # so feed it in and mark this title resolved -- no
                    # separate delta/backfill call needed for it this cycle.
                    if target_sensor is not None and detail.get("recent_unlocks"):
                        target_sensor._ingest_recent_unlocks(
                            detail["recent_unlocks"], game_name=name,
                            platform_label=PLATFORM_CONFIG.get("xbox", {}).get("name_suffix", "Xbox"),
                            hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, name),
                        )
                    xbox_done[title_id] = True

            # --- Delta-detect new activity via the free, already-fetched
            # last_time_played timestamp, and only pay for a per-title
            # detail call for titles that actually changed since last scan.
            title_history = getattr(title, "title_history", None)
            last_played = getattr(title_history, "last_time_played", None)
            last_played_iso = last_played.isoformat() if last_played else None
            if title_id and last_played_iso:
                old_ts = xbox_cursor.get(title_id)
                if old_ts is None:
                    # First sighting -- seed the baseline, no detail call.
                    # This title becomes a backfill candidate (see
                    # async_run_backfill_pass) unless the sanity re-fetch
                    # above already resolved it this same cycle.
                    xbox_cursor[title_id] = last_played_iso
                elif last_played_iso > old_ts:
                    detail = await utils.fetch_xbox_title_achievement_counts(
                        self.hass, entry, session, xuid, title_id, recent_limit=utils.RECENT_UNLOCKS_LIMIT
                    )
                    if detail is not None:
                        if target_sensor is not None and detail.get("recent_unlocks"):
                            target_sensor._ingest_recent_unlocks(
                                detail["recent_unlocks"], game_name=name,
                                platform_label=PLATFORM_CONFIG.get("xbox", {}).get("name_suffix", "Xbox"),
                                hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, name),
                            )
                        xbox_cursor[title_id] = last_played_iso
                        xbox_done[title_id] = True
                    # else: failed/rate-limited -- cursor untouched, retried next cycle.

            gs_earned = getattr(achievement, "current_gamerscore", 0) or 0
            gs_total = getattr(achievement, "total_gamerscore", 0) or 0
            previous = previous_games.get(title_id)
            if previous:
                if earned < previous.get("achievements_earned", 0) or total < previous.get("achievements_total", 0):
                    _LOGGER.debug(
                        "Gaming Status: Xbox title-history data for %s (title_id %s) looks stale this "
                        "scan (%s/%s vs previously-recorded %s/%s) -- keeping the higher, last known count",
                        name, title_id, earned, total,
                        previous.get("achievements_earned", 0), previous.get("achievements_total", 0),
                    )
                earned = max(earned, previous.get("achievements_earned", 0))
                total = max(total, previous.get("achievements_total", 0))
                gs_earned = max(gs_earned, previous.get("gamerscore_earned", 0))
                gs_total = max(gs_total, previous.get("gamerscore_total", 0))
            games.append({
                "title": name, "platform": "xbox", "id": title_id,
                "achievements_earned": earned, "achievements_total": total,
                "gamerscore_earned": gs_earned, "gamerscore_total": gs_total,
                "percent": _percent(earned, total),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
                "_activity_ts": last_played_iso,
            })
        return {"games": games, "error": fetch_error}

    async def _scan_psn(self, source_entity_id):
        npsso, account_id = utils.resolve_psn_credentials(self.hass, source_entity_id)
        if not npsso or not account_id:
            return {"games": [], "error": "not_configured"}

        titles, fetch_error = await utils.fetch_psn_full_library(self.hass, npsso, account_id)
        games = []
        target_sensor = _target_sensor(self.hass, self._owner_name, "playstation")
        psn_cursor = self._activity_cursor.setdefault("playstation", {})
        psn_done = self._backfill_done.setdefault("playstation", {})
        # Same "never let a per-title count go backward" floor as Xbox/
        # Steam -- PSN's bulk trophyTitles response has no per-title error
        # signal either, and trophies can only ever accumulate for a real
        # player, so a lower count this scan than last scan is bad/stale
        # data, not a real regression.
        previous_games = {
            g.get("id"): g
            for g in (self.data or {}).get("platforms", {}).get("playstation", {}).get("games", [])
        }
        for title in titles:
            name = title.get("trophyTitleName")
            if not name or _normalize_game_name(name) in self._excluded_normalized:
                continue
            # Apply the user's Title Overrides + display cleanup, matching
            # the real-time "currently playing" pipeline (see the Steam
            # scan's comment above for the full rationale).
            name = utils._format_game_name_for_display(name)
            earned = title.get("earnedTrophies") or {}
            defined = title.get("definedTrophies") or {}
            earned_counts = {k: int(earned.get(k, 0)) for k in _TIER_KEYS}
            total_counts = {k: int(defined.get(k, 0)) for k in _TIER_KEYS}
            np_comm_id = title.get("npCommunicationId")
            previous = previous_games.get(np_comm_id)
            if previous:
                prev_earned = previous.get("trophies_earned") or {}
                prev_total = previous.get("trophies_total") or {}
                if any(earned_counts[k] < int(prev_earned.get(k, 0)) for k in _TIER_KEYS) or any(
                    total_counts[k] < int(prev_total.get(k, 0)) for k in _TIER_KEYS
                ):
                    _LOGGER.debug(
                        "Gaming Status: PSN trophyTitles data for %s (id %s) looks stale this scan -- "
                        "keeping the higher, last known count per tier",
                        name, np_comm_id,
                    )
                for k in _TIER_KEYS:
                    earned_counts[k] = max(earned_counts[k], int(prev_earned.get(k, 0)))
                    total_counts[k] = max(total_counts[k], int(prev_total.get(k, 0)))
            total_earned = sum(earned_counts.values())
            total_defined = sum(total_counts.values())
            art = await self._async_art_for(name)

            last_updated = title.get("lastUpdatedDateTime")
            # --- Delta-detect new trophy activity via the free,
            # already-fetched lastUpdatedDateTime -- confirmed to
            # specifically track "last trophy earned", not just last
            # played. First sighting of ANY title always just seeds the
            # baseline and never fetches immediately, regardless of
            # total_earned -- otherwise a player with substantial EXISTING
            # trophy history would trigger an expensive per-title fetch for
            # every single one of those titles on the very first scan after
            # enabling this feature, exactly the flood async_run_backfill_
            # pass's pacing exists to prevent. Titles with existing
            # progress get resolved by that paced backfill pass instead;
            # this delta check only fires for genuinely NEW activity since
            # a baseline was already established.
            if np_comm_id and last_updated:
                old_ts = psn_cursor.get(np_comm_id)
                if old_ts is None:
                    psn_cursor[np_comm_id] = last_updated
                elif total_earned > 0 and last_updated > old_ts:
                    detail = await utils.fetch_psn_trophies(
                        self.hass, npsso, account_id, name, title_id=np_comm_id, include_recent_unlocks=True,
                    )
                    if detail is not None:
                        if target_sensor is not None and detail.get("recent_unlocks"):
                            target_sensor._ingest_recent_unlocks(
                                detail["recent_unlocks"], game_name=name,
                                platform_label=PLATFORM_CONFIG.get("playstation", {}).get("name_suffix", "PlayStation"),
                                hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, name),
                            )
                        psn_cursor[np_comm_id] = last_updated
                        psn_done[np_comm_id] = True
                    # else: failed -- cursor untouched, retried next cycle.

            games.append({
                "title": name, "platform": "playstation", "id": np_comm_id,
                "achievements_earned": total_earned, "achievements_total": total_defined,
                "trophies_earned": earned_counts, "trophies_total": total_counts,
                "percent": _percent(total_earned, total_defined),
                "game_cover_art": art.get("grid"), "game_hero_art": art.get("hero"),
                "game_logo_art": art.get("logo"), "game_icon_art": art.get("icon"),
                "_activity_ts": last_updated,
            })
        return {"games": games, "error": fetch_error}

    async def async_run_backfill_pass(self):
        """Independent-timer-driven (see __init__.py's
        _library_backfill_tick), NOT part of the normal scan-interval
        refresh cycle -- never touches _async_update_data/async_refresh/
        async_schedule_or_refresh, and never re-fetches the owned-games/
        title-history/full-library bulk lists (that's _async_update_data's
        own job, gated by the user's configured
        OPT_LIBRARY_SCAN_INTERVAL_HOURS). Only walks this coordinator's own
        already-cached self.data for titles never yet resolved into
        self._backfill_done, and resolves a small budgeted batch of them
        via the same per-title detail calls the delta-detection above
        uses. Steam needs no backfill pass -- it already gets recent_unlocks
        for free on every regular scan."""
        if not self.data:
            return

        resolved_this_pass = 0
        if "xbox" in self._platform_sources:
            resolved_this_pass += await self._backfill_platform("xbox", XBOX_LIBRARY_BACKFILL_BUDGET_PER_CYCLE)
        if "playstation" in self._platform_sources:
            resolved_this_pass += await self._backfill_platform("playstation", PSN_LIBRARY_BACKFILL_BUDGET_PER_CYCLE)

        if not resolved_this_pass:
            return

        remaining = sum(
            1
            for platform in ("xbox", "playstation")
            for g in (self.data.get("platforms", {}).get(platform, {}).get("games", []))
            if str(g["id"]) not in self._backfill_done.get(platform, {})
        )
        if remaining:
            _LOGGER.info(
                "Gaming Status: achievement backfill for %s -- resolved %d titles this pass (%d remaining)",
                self._owner_name, resolved_this_pass, remaining,
            )
        else:
            total_resolved = sum(len(v) for v in self._backfill_done.values())
            _LOGGER.info(
                "Gaming Status: achievement backfill for %s complete -- %d titles resolved",
                self._owner_name, total_resolved,
            )
        await self._store.async_save({
            "data": self.data, "art_cache": self._art_cache,
            "activity_cursor": self._activity_cursor, "backfill_done": self._backfill_done,
        })

    async def _backfill_platform(self, platform, budget):
        games = self.data.get("platforms", {}).get(platform, {}).get("games", [])
        done = self._backfill_done.setdefault(platform, {})
        cursor = self._activity_cursor.setdefault(platform, {})
        pending = [g for g in games if str(g["id"]) not in done]
        if not pending:
            return 0

        # Titles confirmed to have zero achievements/trophies total need no
        # detail call at all -- mark them done for free so they don't keep
        # consuming budget or reappearing as "pending" on every future tick.
        # Counted into `resolved` too (not just real API-resolved titles),
        # so a pass that only finds zero-achievement titles still gets its
        # progress saved below rather than silently discarded.
        still_pending = []
        resolved = 0
        for g in pending:
            if not g.get("achievements_total"):
                done[str(g["id"])] = True
                resolved += 1
            else:
                still_pending.append(g)
        pending = still_pending
        if not pending:
            return resolved

        source_entity_id = self._platform_sources.get(platform)
        target_sensor = _target_sensor(self.hass, self._owner_name, platform)

        if platform == "xbox":
            entry, session, xuid = await utils.resolve_xbox_entry_and_session(self.hass, source_entity_id)
            if not entry or not session or not xuid:
                return resolved
            for game in pending[:budget]:
                title_id = str(game["id"])
                detail = await utils.fetch_xbox_title_achievement_counts(
                    self.hass, entry, session, xuid, title_id, recent_limit=utils.RECENT_UNLOCKS_LIMIT
                )
                if detail is None:
                    continue
                if target_sensor is not None and detail.get("recent_unlocks"):
                    art = await self._async_art_for(game["title"])
                    target_sensor._ingest_recent_unlocks(
                        detail["recent_unlocks"], game_name=game["title"],
                        platform_label=PLATFORM_CONFIG.get("xbox", {}).get("name_suffix", "Xbox"),
                        hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, game["title"]),
                    )
                done[title_id] = True
                if game.get("_activity_ts"):
                    cursor[title_id] = game["_activity_ts"]
                resolved += 1

        elif platform == "playstation":
            npsso, account_id = utils.resolve_psn_credentials(self.hass, source_entity_id)
            if not npsso or not account_id:
                return resolved
            for game in pending[:budget]:
                np_comm_id = str(game["id"])
                detail = await utils.fetch_psn_trophies(
                    self.hass, npsso, account_id, game["title"], title_id=np_comm_id, include_recent_unlocks=True,
                )
                if detail is None:
                    continue
                if target_sensor is not None and detail.get("recent_unlocks"):
                    art = await self._async_art_for(game["title"])
                    target_sensor._ingest_recent_unlocks(
                        detail["recent_unlocks"], game_name=game["title"],
                        platform_label=PLATFORM_CONFIG.get("playstation", {}).get("name_suffix", "PlayStation"),
                        hero_art_url=art.get("hero"), game_dominant_color=_dominant_color_for(target_sensor, game["title"]),
                    )
                done[np_comm_id] = True
                if game.get("_activity_ts"):
                    cursor[np_comm_id] = game["_activity_ts"]
                resolved += 1

        return resolved


def has_pending_library_backfill(coordinator) -> bool:
    """Pure, cheap (no I/O) check used by __init__.py's independent
    backfill timer to skip a coordinator entirely once every Xbox/
    PlayStation title it knows about has been resolved."""
    if not coordinator.data:
        return False
    for platform in ("xbox", "playstation"):
        games = coordinator.data.get("platforms", {}).get(platform, {}).get("games", [])
        done = coordinator._backfill_done.get(platform, {})
        if any(str(g["id"]) not in done for g in games):
            return True
    return False


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
        if platform == "steam":
            summary["playtime_hours"] = round(sum(g.get("playtime_hours", 0) for g in games), 1)
        elif platform == "xbox":
            summary["gamerscore_earned"] = sum(g.get("gamerscore_earned", 0) for g in games)
            summary["gamerscore_total"] = sum(g.get("gamerscore_total", 0) for g in games)
        elif platform == "playstation":
            summary["trophies_earned"] = {k: sum(g.get("trophies_earned", {}).get(k, 0) for g in games) for k in _TIER_KEYS}
            summary["trophies_total"] = {k: sum(g.get("trophies_total", {}).get(k, 0) for g in games) for k in _TIER_KEYS}
        platform_summaries[platform] = summary
        all_games.extend(games)

    percents = [g["percent"] for g in all_games]

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
        # A bare None here renders as the literal word "Unknown" in HA's
        # frontend attribute list, which reads as broken/missing data --
        # "None" is the honest no-errors value.
        "platform_errors": platform_errors or "None",
        "games": all_games,
        "platforms": platform_summaries,
        "last_synced": dt_util.now().isoformat(),
    }
