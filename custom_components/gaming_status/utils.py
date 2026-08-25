"""
Utilities for Gaming Status
"""

import ipaddress
import logging
import os
import re
import socket
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from dateutil import parser
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .platform_exceptions import ApiError, AuthError

_LOGGER = logging.getLogger(__name__)

# Initialize empty globals (Populated securely by setup)
GAME_TITLE_OVERRIDES = {}
GAME_COLOR_OVERRIDES = {}
RATING_OVERRIDES = {}
TITLE_CLEANUPS = []
COMPILED_TITLE_CLEANUPS = []
STEAMGRIDDB_API_KEY = None

# Native platform achievement/trophy/rating enrichment (current game only --
# see steam_client.py/psn_client.py). Two independent opt-in flags, off by
# default -- see const.py's OPT_ENABLE_NATIVE_RATINGS/OPT_ENABLE_ACHIEVEMENT_TRACKING
# for why these are split rather than one combined toggle.
ENABLE_NATIVE_RATINGS = False
ENABLE_ACHIEVEMENT_TRACKING = False
STEAM_ACHIEVEMENTS_API_KEY_OVERRIDE = None
PSN_NPSSO_OVERRIDE = None
ACHIEVEMENT_RECHECK_SECONDS = 900

# Static per-game data that never needs re-fetching once known -- unlike
# earned/total achievement *counts* (which the caller re-fetches on its own
# recheck interval, since those genuinely change), a game's total-achievement
# count and PSN title_id/np_communication_id resolution are effectively
# permanent, so they're cached forever (LRU-capped) rather than time-limited
# like RATING_CACHE.
STEAM_SCHEMA_CACHE = OrderedDict()
PSN_TITLE_ID_CACHE = (
    OrderedDict()
)  # normalized_game_name -> title_id, rare-fallback-path only
MAX_ENRICHMENT_CACHE_SIZE = 500

# Cache Settings
USE_LOCAL_CACHE = True
ENABLE_VIBRANT_COLOR = True
CACHE_MAX_FILES = 200
CACHE_MAX_DAYS = 30

# The New Custom Image Maps
CUSTOM_GRID_MAP = {}
CUSTOM_HERO_MAP = {}
CUSTOM_LOGO_MAP = {}
CUSTOM_ICON_MAP = {}

# Size-capped LRU Cache to prevent unbounded memory growth
ASSET_URL_CACHE = OrderedDict()
MAX_CACHE_SIZE = 500
_MISSING_KEY_WARNED = False
_KEY_PROBLEM_WARNED = False


def _warn_steamgriddb_key_problem_once(status):
    """A 401/403/429 from SteamGridDB otherwise looks identical to "no art
    for this title" -- warn once (same one-shot shape as
    _MISSING_KEY_WARNED above) so a revoked/rate-limited key doesn't look
    like a silent, permanent lack of artwork."""
    global _KEY_PROBLEM_WARNED
    if status in (401, 403, 429) and not _KEY_PROBLEM_WARNED:
        _LOGGER.warning(
            "[Gaming Status] SteamGridDB returned HTTP %s -- the configured "
            "API key may be invalid, revoked, or rate-limited.",
            status,
        )
        _KEY_PROBLEM_WARNED = True


# A "SteamGridDB has no art for this title" result is cached too (like any
# other result), but isn't trusted forever -- SGDB's catalog grows over
# time, so it's re-checked after ASSET_NOT_FOUND_RECHECK_SECONDS instead of
# re-querying on every single fetch (the previous behavior) or sticking
# forever (which would miss art added to SGDB later).
_ASSET_NOT_FOUND_CHECKED_AT: dict = {}
ASSET_NOT_FOUND_RECHECK_SECONDS = 86400  # 24 hours

# Content-rating cache: a confirmed rating is immutable and cached forever
# (just the LRU size cap below). An "unrated" result is NOT trusted forever,
# since it may reflect a transient lookup failure or native data that hasn't
# been added yet, so it gets re-checked after RATING_RECHECK_SECONDS instead
# of sticking until the next HA restart.
RATING_CACHE = OrderedDict()
MAX_RATING_CACHE_SIZE = 500
RATING_RECHECK_SECONDS = 86400  # 24 hours

# Throttle for the image cache retention sweep -- it used to fire on every
# single cache-miss (a new game entering ASSET_URL_CACHE); this caps it to
# once per interval regardless of how many games enter the cache in between.
_LAST_CACHE_CLEANUP_AT = 0.0
CACHE_CLEANUP_MIN_INTERVAL_SECONDS = 300  # 5 minutes

# Display label for a manually-overridden age floor (RATING_OVERRIDES).
AGE_FLOOR_LABELS = {
    0: "Everyone",
    10: "Everyone 10+",
    13: "Teen",
    17: "Mature",
    18: "Adults Only",
}


# Title Cleanups patterns are user-authored and run synchronously, uncapped,
# on every observed game title in the real-time tracking hot path -- these
# two guards catch the obvious ways a pattern could hang that hot path
# (Python's re module has no built-in per-call timeout). Not foolproof, but
# closes the common cases at negligible cost for the overwhelming majority
# of normal, safe patterns.
MAX_TITLE_CLEANUP_PATTERN_LENGTH = 200
# A group containing a quantifier, itself immediately followed by another
# quantifier -- the classic catastrophic-backtracking shape, e.g. "(a+)+",
# "(a*)*", "(a+){2,}".
_CATASTROPHIC_BACKTRACKING_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*{]")


def compile_title_cleanups():
    """Pre-compile regex patterns for performance."""
    global COMPILED_TITLE_CLEANUPS
    compiled = []
    for pattern in TITLE_CLEANUPS:
        if len(pattern) > MAX_TITLE_CLEANUP_PATTERN_LENGTH:
            _LOGGER.warning(
                "Skipping Title Cleanups pattern over %d characters: %r",
                MAX_TITLE_CLEANUP_PATTERN_LENGTH,
                pattern,
            )
            continue
        if _CATASTROPHIC_BACKTRACKING_RE.search(pattern):
            _LOGGER.warning(
                "Skipping Title Cleanups pattern with a nested-quantifier "
                "shape known to cause catastrophic regex backtracking "
                '(e.g. "(a+)+"): %r',
                pattern,
            )
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as err:
            _LOGGER.warning(
                "Skipping invalid Title Cleanups pattern %r: %s", pattern, err
            )
    COMPILED_TITLE_CLEANUPS = compiled


_IS_RASPBERRY_PI: bool | None = None


async def is_raspberry_pi(hass) -> bool:
    """Whether this HA install is running on a Raspberry Pi -- true for the
    life of the process, so checked (via a blocking sysfs read, hence the
    executor job) only once and cached, instead of on every config-flow
    render/integration setup that wants to know."""
    global _IS_RASPBERRY_PI
    if _IS_RASPBERRY_PI is None:

        def _check_is_pi():
            try:
                with Path("/sys/firmware/devicetree/base/model").open() as f:
                    return "Raspberry Pi" in f.read()
            except Exception:
                return False

        _IS_RASPBERRY_PI = await hass.async_add_executor_job(_check_is_pi)
    return _IS_RASPBERRY_PI


def _clean_image_cache(cache_dir_path: Path):
    """Enforce user retention policies based on age and total file count."""
    if not cache_dir_path.exists():
        return

    files = [f for f in cache_dir_path.iterdir() if f.is_file()]
    if not files:
        return

    now = time.time()

    # 1. Prune by Age (if feature is enabled)
    if CACHE_MAX_DAYS > 0:
        max_age_seconds = CACHE_MAX_DAYS * 86400
        for f in files[:]:  # Iterate over a copy of the list
            try:
                file_age = now - f.stat().st_mtime
                if file_age > max_age_seconds:
                    f.unlink()
                    files.remove(f)
            except OSError as e:
                _LOGGER.error(
                    "Gaming Status failed to delete aged image %s: %s", f.name, e
                )

    # 2. Prune by File Count (if feature is enabled)
    if CACHE_MAX_FILES > 0 and len(files) > CACHE_MAX_FILES:
        # Sort files by modification time, oldest first
        files.sort(key=lambda x: x.stat().st_mtime)
        files_to_delete = files[:-CACHE_MAX_FILES]

        for f in files_to_delete:
            try:
                f.unlink()
            except OSError as e:
                _LOGGER.error(
                    "Gaming Status failed to delete excess image %s: %s", f.name, e
                )


async def fetch_game_assets(hass, game_name):
    """
    Fetch Grid, Hero, Logo, and Icon.
    Prioritizes local custom overrides, then Memory Cache, then SteamGridDB.
    Custom overrides are downloaded to the local cache if they are external URLs.
    """
    import asyncio

    global _MISSING_KEY_WARNED

    assets = {"grid": None, "hero": None, "logo": None, "icon": None}

    if not game_name:
        return assets

    # Canonical key for caching/locking/override lookups/filenames, so
    # punctuation differences (commas, colons, dashes) never cause a miss.
    # The SteamGridDB search query below intentionally keeps the real title.
    cache_key = _normalize_game_name(game_name)

    # 0. Check Memory Cache BEFORE touching the disk or creating sessions!
    if cache_key in ASSET_URL_CACHE:
        not_found_at = _ASSET_NOT_FOUND_CHECKED_AT.get(cache_key)
        is_stale = (
            not_found_at is not None
            and (time.time() - not_found_at) > ASSET_NOT_FOUND_RECHECK_SECONDS
        )
        if not is_stale:
            ASSET_URL_CACHE.move_to_end(cache_key)
            return ASSET_URL_CACHE[cache_key]

    # --- THE MEMORY LOCK ---
    # Prevent race conditions by making simultaneous requests wait
    if "gaming_status_locks" not in hass.data:
        hass.data["gaming_status_locks"] = {}

    if cache_key in hass.data["gaming_status_locks"]:
        # Another sensor is currently downloading this game! Wait for it to finish.
        await hass.data["gaming_status_locks"][cache_key].wait()
        # The first downloader should have populated the cache, grab it and return!
        if cache_key in ASSET_URL_CACHE:
            ASSET_URL_CACHE.move_to_end(cache_key)
            return ASSET_URL_CACHE[cache_key]
        return assets

    # Lock the game while we download it
    lock = asyncio.Event()
    hass.data["gaming_status_locks"][cache_key] = lock

    try:
        # 1. Setup Session and Cache Directory early
        session = async_get_clientsession(hass)
        cache_dir = Path(hass.config.path("www/gaming_status_cache"))

        try:
            base_url = get_url(hass, prefer_external=True)
        except NoURLAvailableError:
            base_url = ""

        def _ensure_dir():
            if not cache_dir.exists():
                cache_dir.mkdir(parents=True, exist_ok=True)

        if USE_LOCAL_CACHE:
            await hass.async_add_executor_job(_ensure_dir)

        # 2. Check Custom UI Overrides & Ensure Local Cache
        search_name = cache_key
        override_maps = {
            "grid": CUSTOM_GRID_MAP,
            "hero": CUSTOM_HERO_MAP,
            "logo": CUSTOM_LOGO_MAP,
            "icon": CUSTOM_ICON_MAP,
        }

        safe_file_prefix = re.sub(r"[^a-z0-9]", "_", cache_key)
        safe_file_prefix = re.sub(r"_+", "_", safe_file_prefix).strip("_")

        for asset_type, map_dict in override_maps.items():
            # Safety net: re-normalize keys in case older un-migrated data exists in the dictionary
            safe_map = {_normalize_game_name(k): v for k, v in map_dict.items()}

            if search_name in safe_map:
                remote_url = safe_url(safe_map[search_name])
                if not remote_url:
                    continue

                # If the user provided a raw local path, map it directly without downloading!
                if not remote_url.startswith("http"):
                    if remote_url.startswith("/local/"):
                        assets[asset_type] = f"{base_url}{remote_url}"
                    else:
                        assets[asset_type] = remote_url
                    continue

                # With local caching disabled, hotlink the override's own
                # remote URL directly rather than downloading a local copy --
                # this is the toggle's actual point of control, not just the
                # background cleanup task below.
                if not USE_LOCAL_CACHE:
                    assets[asset_type] = remote_url
                    continue

                # Determine extension for external HTTP links
                ext = safe_image_ext(remote_url)
                file_name = f"{safe_file_prefix}_{asset_type}.{ext}"
                file_path = cache_dir / file_name

                # ALWAYS download overrides to ensure the user's latest choice overwrites the old SteamGridDB file!
                try:
                    if not await is_public_url(hass, remote_url):
                        _LOGGER.warning(
                            "Refusing to fetch override art for %s (%s): URL does not resolve to a public host",
                            game_name,
                            asset_type,
                        )
                    else:
                        async with session.get(remote_url, timeout=15) as img_resp:
                            if img_resp.status == 200 and await is_public_url(
                                hass, str(img_resp.url)
                            ):
                                img_bytes = await _read_capped(img_resp)
                                if img_bytes is not None:
                                    await hass.async_add_executor_job(
                                        lambda: file_path.write_bytes(img_bytes)
                                    )
                except Exception as e:
                    _LOGGER.error(
                        "Failed to cache override for %s (%s): %s",
                        game_name,
                        asset_type,
                        e,
                    )

                try:
                    mt = int(
                        await hass.async_add_executor_job(os.path.getmtime, file_path)
                    )
                except Exception:
                    mt = int(time.time())
                assets[asset_type] = (
                    f"{base_url}/local/gaming_status_cache/{file_name}?v={mt}"
                )

        def _update_cache(name, data_dict, *, cache_as_not_found=True):
            final_dict = {k: assets[k] or data_dict.get(k) for k in assets}

            # A "not found" result is only trustworthy -- and thus only
            # worth caching for a full day -- when SteamGridDB itself was
            # actually asked and genuinely had nothing. Callers pass
            # cache_as_not_found=False for a missing API key or a network/
            # request failure, neither of which is evidence the game has
            # no art -- caching those would silently block ever retrying
            # once the real problem (key added, connectivity restored) is
            # fixed, since nothing else ever invalidates this cache early.
            if not any(final_dict.values()) and not cache_as_not_found:
                return final_dict

            # Cache the result either way -- including "nothing found", so a
            # title SteamGridDB has no art for doesn't re-run the full
            # search+fetch sequence on every single call. A found result is
            # trusted until evicted; a not-found one is re-checked after
            # ASSET_NOT_FOUND_RECHECK_SECONDS in case SGDB's catalog grew.
            ASSET_URL_CACHE[name] = final_dict
            ASSET_URL_CACHE.move_to_end(name)
            if len(ASSET_URL_CACHE) > MAX_CACHE_SIZE:
                evicted_name, _ = ASSET_URL_CACHE.popitem(last=False)
                # Keep this companion negative-cache dict from outliving its
                # own entry in ASSET_URL_CACHE -- without this it has no
                # eviction of its own and can grow past MAX_CACHE_SIZE.
                _ASSET_NOT_FOUND_CHECKED_AT.pop(evicted_name, None)

            if any(final_dict.values()):
                _ASSET_NOT_FOUND_CHECKED_AT.pop(name, None)

                # Fire off non-blocking cache cleanup whenever a NEW game
                # enters RAM, throttled so a burst of new games doesn't
                # trigger a full directory sweep for each one.
                global _LAST_CACHE_CLEANUP_AT
                now = time.time()
                if (
                    USE_LOCAL_CACHE
                    and now - _LAST_CACHE_CLEANUP_AT
                    > CACHE_CLEANUP_MIN_INTERVAL_SECONDS
                ):
                    _LAST_CACHE_CLEANUP_AT = now

                    async def _run_cleanup():
                        await hass.async_add_executor_job(_clean_image_cache, cache_dir)

                    hass.async_create_task(_run_cleanup())
            else:
                _ASSET_NOT_FOUND_CHECKED_AT[name] = time.time()

            return final_dict

        # If the user provided ALL 4 custom images manually, skip the API entirely!
        if all(assets.values()):
            return _update_cache(cache_key, assets)

        if not STEAMGRIDDB_API_KEY:
            if not _MISSING_KEY_WARNED:
                _LOGGER.warning(
                    "[Gaming Status] SteamGridDB API key is not configured."
                )
                _MISSING_KEY_WARNED = True
            return _update_cache(cache_key, assets, cache_as_not_found=False)

        # 4. Fetch from SteamGridDB
        fetched_assets = {"grid": None, "hero": None, "logo": None, "icon": None}
        headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}
        fetch_failed = False

        try:
            from .const import RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS

            limiter = _get_rate_limiter(hass, "steamgriddb")

            safe_title = quote(game_name, safe="")
            search_url = (
                f"https://www.steamgriddb.com/api/v2/search/autocomplete/{safe_title}"
            )

            await limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    _warn_steamgriddb_key_problem_once(resp.status)
                    return _update_cache(cache_key, fetched_assets)
                search_data = await resp.json()

            if not search_data.get("data"):
                return _update_cache(cache_key, fetched_assets)

            game_id = search_data["data"][0]["id"]
            endpoints = {
                "grid": f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}",
                "hero": f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}",
                "logo": f"https://www.steamgriddb.com/api/v2/logos/game/{game_id}",
                "icon": f"https://www.steamgriddb.com/api/v2/icons/game/{game_id}",
            }

            for asset_type, endpoint in endpoints.items():
                if assets[asset_type]:
                    continue  # Already filled by override

                await limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
                async with session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        _warn_steamgriddb_key_problem_once(resp.status)
                    if resp.status == 200:
                        asset_data = await resp.json()
                        if asset_data.get("data"):
                            # Scoring Algorithm
                            def get_score(img):
                                score = 0
                                if img.get("style") == "official":
                                    score += 10
                                if img.get("mime") == "image/png":
                                    score += 5
                                return score

                            best_art = sorted(
                                asset_data["data"], key=get_score, reverse=True
                            )[0]
                            remote_url = best_art["url"]

                            # Same "toggle actually controls the download" fix
                            # as the override branch above -- hotlink SteamGridDB's
                            # own CDN URL directly instead of caching it locally.
                            if not USE_LOCAL_CACHE:
                                fetched_assets[asset_type] = remote_url
                                continue

                            ext = safe_image_ext(remote_url)
                            file_name = f"{safe_file_prefix}_{asset_type}.{ext}"
                            file_path = cache_dir / file_name

                            if not await hass.async_add_executor_job(file_path.exists):
                                if not await is_public_url(hass, remote_url):
                                    _LOGGER.warning(
                                        "Refusing to fetch %s art for %s: URL does not resolve to a public host",
                                        asset_type,
                                        game_name,
                                    )
                                else:
                                    async with session.get(
                                        remote_url, timeout=15
                                    ) as img_resp:
                                        if (
                                            img_resp.status == 200
                                            and await is_public_url(
                                                hass, str(img_resp.url)
                                            )
                                        ):
                                            img_bytes = await _read_capped(img_resp)
                                            if img_bytes is not None:
                                                await hass.async_add_executor_job(
                                                    lambda: file_path.write_bytes(
                                                        img_bytes
                                                    )
                                                )

                            try:
                                mt = int(
                                    await hass.async_add_executor_job(
                                        os.path.getmtime, file_path
                                    )
                                )
                            except Exception:
                                mt = int(time.time())
                            fetched_assets[asset_type] = (
                                f"{base_url}/local/gaming_status_cache/{file_name}?v={mt}"
                            )

        except Exception as e:
            _LOGGER.error("Failed to fetch assets for %s: %s", game_name, e)
            fetch_failed = True

        return _update_cache(
            cache_key, fetched_assets, cache_as_not_found=not fetch_failed
        )

    finally:
        # ALWAYS release the lock, even if the API throws an unexpected error
        lock.set()
        hass.data["gaming_status_locks"].pop(cache_key, None)


async def fetch_game_grid_urls_remote(hass, game_name):
    """Full-library-scan-only SteamGridDB lookup: a deliberately separate,
    minimal search+score against SteamGridDB (not routed through
    fetch_game_assets above) that ALWAYS returns SteamGridDB's own remote
    CDN URLs -- never downloads/writes to www/gaming_status_cache,
    regardless of the USE_LOCAL_CACHE setting. That setting stays scoped to
    the handful of currently-playing games fetch_game_assets serves; caching
    a full library's worth of art (hundreds of games) is what caused a 2GB+
    local cache previously.

    Kept as its own small function rather than threaded through
    fetch_game_assets's local-cache/custom-override/memory-cache machinery,
    which doesn't apply here and would risk destabilizing that already-
    exercised current-game path for a feature that needs none of it. The
    user's CUSTOM_GRID/HERO/LOGO/ICON_MAP overrides are the one piece of
    that machinery still worth checking here, though -- a manual override is
    exactly as relevant to a library-scanned game's artwork as it is to a
    currently-playing one, and since an override is either a direct remote
    URL or a local path reference, honoring it needs no download/caching
    step, so it doesn't reintroduce any of the machinery this function
    otherwise deliberately avoids.

    Paced by its own shared rate limiter (5 capacity, 2/sec) -- a library
    scan can mean hundreds of lookups in one pass, unlike the current-game
    path's implicit one-request-per-session-cover pacing. Never raises; returns
    {"grid": None, "hero": None, "logo": None, "icon": None} on any
    failure or missing API key."""
    assets = {"grid": None, "hero": None, "logo": None, "icon": None}
    if not game_name:
        return assets

    search_name = _normalize_game_name(game_name)
    override_maps = {
        "grid": CUSTOM_GRID_MAP,
        "hero": CUSTOM_HERO_MAP,
        "logo": CUSTOM_LOGO_MAP,
        "icon": CUSTOM_ICON_MAP,
    }
    for asset_type, map_dict in override_maps.items():
        remote_url = safe_url(map_dict.get(search_name))
        if not remote_url:
            continue
        if not remote_url.startswith("http") and remote_url.startswith("/local/"):
            try:
                base_url = get_url(hass, prefer_external=True)
            except NoURLAvailableError:
                base_url = ""
            assets[asset_type] = f"{base_url}{remote_url}"
        else:
            assets[asset_type] = remote_url

    # If the user provided all 4 overrides, skip the SteamGridDB API
    # entirely -- same short-circuit fetch_game_assets already applies.
    if all(assets.values()) or not STEAMGRIDDB_API_KEY:
        return assets

    try:
        from .const import RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS

        limiter = _get_rate_limiter(hass, "steamgriddb")
        session = async_get_clientsession(hass)
        headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}

        await limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
        safe_title = quote(game_name, safe="")
        async with session.get(
            f"https://www.steamgriddb.com/api/v2/search/autocomplete/{safe_title}",
            headers=headers,
            timeout=10,
        ) as resp:
            if resp.status != 200:
                _warn_steamgriddb_key_problem_once(resp.status)
                return assets
            search_data = await resp.json()
        if not search_data.get("data"):
            return assets
        game_id = search_data["data"][0]["id"]

        endpoints = {
            "grid": f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}",
            "hero": f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}",
            "logo": f"https://www.steamgriddb.com/api/v2/logos/game/{game_id}",
            "icon": f"https://www.steamgriddb.com/api/v2/icons/game/{game_id}",
        }

        def _score(img):
            score = 0
            if img.get("style") == "official":
                score += 10
            if img.get("mime") == "image/png":
                score += 5
            return score

        for asset_type, endpoint in endpoints.items():
            if assets[asset_type]:
                continue  # Already filled by an override above.
            await limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
            async with session.get(endpoint, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    _warn_steamgriddb_key_problem_once(resp.status)
                    continue
                asset_data = await resp.json()
            if not asset_data.get("data"):
                continue
            best_art = sorted(asset_data["data"], key=_score, reverse=True)[0]
            assets[asset_type] = best_art["url"]
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] SteamGridDB remote-only art fetch failed for %s: %s",
            game_name,
            e,
        )

    return assets


async def get_steamgriddb_game_cover(hass, game_name):
    """Backward compatibility wrapper."""
    if not game_name:
        return None
    assets = await fetch_game_assets(hass, game_name)
    return assets.get("hero") or assets.get("grid")


async def fetch_game_rating(hass, game_name, platform=None, platform_context=None):
    """
    Fetch content/age-rating metadata for a game.
    A manual entry in RATING_OVERRIDES always takes priority and skips
    everything else. Otherwise, if platform enrichment is enabled and the
    caller passed enough context, tries a platform-native rating (see
    _fetch_native_rating). There is no third-party fallback -- RAWG.io was
    removed once native ratings covered Steam/Xbox/PSN; anything native
    can't resolve (including Custom/Playnite/Discord platforms, which have
    no native rating source at all) is reported as "unrated" and left to a
    manual RATING_OVERRIDES entry. A confirmed rating is cached forever
    (ratings don't change); an "unrated" result is only cached for
    RATING_RECHECK_SECONDS, then retried, since native data can appear later
    (e.g. a store page gets ESRB info added after this was first checked).

    `platform`/`platform_context` are optional and only used for the native
    lookup: platform="xbox" -> {"min_age": <already-known value, no HTTP
    call needed>}; platform="steam" -> {"appid": int}; platform="psn" ->
    {"npsso": str, "title_id": str}.

    Returns a dict like {"esrb": "M", "pegi": None, "age_floor": 17,
    "descriptors": [...], "unrated": False, "source": "steam_native"}, or
    None if game_name is empty.
    """
    if not game_name:
        return None

    cache_key = _normalize_game_name(game_name)

    if cache_key in RATING_OVERRIDES:
        age_floor = RATING_OVERRIDES[cache_key]
        return {
            "esrb": AGE_FLOOR_LABELS.get(age_floor, str(age_floor)),
            "pegi": None,
            "age_floor": age_floor,
            "descriptors": [],
            "unrated": False,
            "source": "override",
        }

    if cache_key in RATING_CACHE:
        cached = RATING_CACHE[cache_key]
        is_stale = (
            cached.get("unrated")
            and (time.time() - cached.get("checked_at", 0)) > RATING_RECHECK_SECONDS
        )
        if not is_stale:
            RATING_CACHE.move_to_end(cache_key)
            return cached

    if ENABLE_NATIVE_RATINGS and platform and platform_context:
        native = await _fetch_native_rating(hass, platform, platform_context)
        if native is not None:
            native["checked_at"] = time.time()
            RATING_CACHE[cache_key] = native
            RATING_CACHE.move_to_end(cache_key)
            if len(RATING_CACHE) > MAX_RATING_CACHE_SIZE:
                RATING_CACHE.popitem(last=False)
            return native

    unrated = {
        "esrb": None,
        "pegi": None,
        "age_floor": None,
        "descriptors": [],
        "unrated": True,
        "source": None,
        "checked_at": time.time(),
    }
    RATING_CACHE[cache_key] = unrated
    RATING_CACHE.move_to_end(cache_key)
    if len(RATING_CACHE) > MAX_RATING_CACHE_SIZE:
        RATING_CACHE.popitem(last=False)
    return unrated


# ---------------------------------------------------------------------------
# Native platform achievement/trophy/rating enrichment (current game only --
# see steam_client.py/psn_client.py for the actual HTTP clients this section
# orchestrates). Opt-in (ENABLE_NATIVE_RATINGS / ENABLE_ACHIEVEMENT_TRACKING), off by default.
# ---------------------------------------------------------------------------


def resolve_owning_config_entry(hass, source_entity_id):
    """Walks source_entity_id (an HA entity Gaming Status already watches) to
    the config entry that owns it -- e.g. the steam_online/playstation_network/
    xbox entry backing that entity. Shared by both the real-time sensor
    (PersistentStatusSensor.async_added_to_hass) and the library-scan
    subsystem, which both need this same entity-registry -> config-entry walk.
    Returns (registry_entry, owning_config_entry, subentry_unique_id); any
    element is None if not resolvable. Never raises."""
    from homeassistant.helpers import entity_registry as er

    try:
        registry = er.async_get(hass)
        entry = registry.async_get(source_entity_id)
        owning_entry = None
        subentry_unique_id = None
        if entry and entry.config_entry_id:
            owning_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
            if owning_entry and entry.config_subentry_id:
                subentry = (owning_entry.subentries or {}).get(entry.config_subentry_id)
                subentry_unique_id = getattr(subentry, "unique_id", None)
        return entry, owning_entry, subentry_unique_id
    except Exception:
        _LOGGER.debug(
            "[Gaming Status] Owning config entry resolution failed for %s",
            source_entity_id,
            exc_info=True,
        )
        return None, None, None


_STEAM_ENTITY_UNIQUE_ID_PREFIX = "sensor.steam_"


def resolve_steam_credentials(hass, source_entity_id):
    """Returns (api_key, steamid64), reusing steam_online's own config entry
    (entry.data[CONF_API_KEY], a stable/public ConfigEntry field) if present,
    falling back to the manual Advanced Settings override. Never raises.

    steam_online has two incompatible generations for tracking friends:
    -- 2026.8+ (not yet in any shipped stable release as of this writing):
       one ConfigSubentry per tracked friend, subentry.unique_id holding
       that friend's own steamid64 -- handled by subentry_unique_id above.
    -- every currently-shipped stable release: the account owner AND every
       tracked friend share one flat config entry with no subentry
       distinction at all, so config_subentry_id/subentry_unique_id is
       always None for everyone -- confirmed live against steam_online's
       real sensor.py. There, the only thing that actually distinguishes
       one tracked account from another is each entity's own unique_id,
       which steam_online sets to literally f"sensor.steam_{steamid}".
       Falling back to owning_entry.unique_id here (the API key owner's
       own steamid) would silently resolve every tracked friend to the
       account owner's own Steam data -- so parse the entity's own
       unique_id first, and only fall back to the owner's id if that
       fails (e.g. a genuinely unresolvable/legacy entity)."""
    from homeassistant.const import CONF_API_KEY

    from .const import HA_STEAM_ONLINE_DOMAIN

    try:
        entry, owning_entry, subentry_unique_id = resolve_owning_config_entry(
            hass, source_entity_id
        )
        api_key = None
        steam_id64 = None
        if owning_entry and owning_entry.domain == HA_STEAM_ONLINE_DOMAIN:
            api_key = owning_entry.data.get(CONF_API_KEY)
            steam_id64 = subentry_unique_id
            if (
                not steam_id64
                and entry
                and entry.unique_id
                and entry.unique_id.startswith(_STEAM_ENTITY_UNIQUE_ID_PREFIX)
            ):
                steam_id64 = entry.unique_id[len(_STEAM_ENTITY_UNIQUE_ID_PREFIX) :]
            if not steam_id64:
                steam_id64 = owning_entry.unique_id
        if not api_key:
            api_key = STEAM_ACHIEVEMENTS_API_KEY_OVERRIDE
            steam_id64 = (
                steam_id64
                or subentry_unique_id
                or (owning_entry.unique_id if owning_entry else None)
            )
        return api_key, steam_id64
    except Exception:
        _LOGGER.debug(
            "[Gaming Status] Steam credential resolution failed for %s",
            source_entity_id,
            exc_info=True,
        )
        return None, None


def resolve_psn_credentials(hass, source_entity_id):
    """Returns (npsso, account_id), reusing playstation_network's own config
    entry (entry.data["npsso"]) if present, falling back to the manual
    Advanced Settings override. Never raises."""
    from .const import HA_PLAYSTATION_NETWORK_DOMAIN, HA_PSN_NPSSO_KEY

    try:
        _, owning_entry, subentry_unique_id = resolve_owning_config_entry(
            hass, source_entity_id
        )
        npsso = None
        account_id = None
        if owning_entry and owning_entry.domain == HA_PLAYSTATION_NETWORK_DOMAIN:
            npsso = owning_entry.data.get(HA_PSN_NPSSO_KEY)
            if not npsso:
                _LOGGER.warning(
                    "Gaming Status: found the playstation_network config entry for %s "
                    "but it has no NPSSO stored under the expected key -- native "
                    "PlayStation enrichment will fall back to the manual override, if set.",
                    source_entity_id,
                )
            account_id = subentry_unique_id or owning_entry.unique_id
        if not npsso:
            npsso = PSN_NPSSO_OVERRIDE
            account_id = (
                account_id
                or subentry_unique_id
                or (owning_entry.unique_id if owning_entry else None)
            )
        return npsso, account_id
    except Exception:
        _LOGGER.debug(
            "[Gaming Status] PSN credential resolution failed for %s",
            source_entity_id,
            exc_info=True,
        )
        return None, None


async def resolve_xbox_entry_and_session(hass, source_entity_id):
    """Returns (config_entry, OAuth2Session, xuid) for the owning xbox config
    entry backing source_entity_id, or (None, None, None) if not found/not
    an xbox entity. Built entirely from public HA core OAuth2 helpers --
    config_entry_oauth2_flow.async_get_config_entry_implementation() +
    OAuth2Session() -- the exact same calls the xbox integration's own
    __init__.py makes on itself. Never touches entry.runtime_data. The
    returned session's .async_ensure_token_valid()/.token are what
    xbox_client.py's AsyncConfigEntryAuth bridges into pythonxbox.

    `xuid` is the tracked player's own Xbox user id -- the owning entry's
    unique_id for the account owner, or the matching friend subentry's
    unique_id for a tracked friend (HA's own xbox integration populates both
    from the live client.xuid, per its migration code -- confirmed live).
    Needed because the shared OAuth session authenticates *a* session, not
    which account's presence/achievements are being asked for, same
    reasoning as PSN's account_id. Never raises."""
    from homeassistant.helpers import config_entry_oauth2_flow

    from .const import HA_XBOX_DOMAIN

    try:
        _, owning_entry, subentry_unique_id = resolve_owning_config_entry(
            hass, source_entity_id
        )
        if not owning_entry or owning_entry.domain != HA_XBOX_DOMAIN:
            return None, None, None
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, owning_entry
            )
        )
        session = config_entry_oauth2_flow.OAuth2Session(
            hass, owning_entry, implementation
        )
        xuid = subentry_unique_id or owning_entry.unique_id
        return owning_entry, session, xuid
    except Exception:
        _LOGGER.debug(
            "[Gaming Status] Xbox OAuth session resolution failed for %s",
            source_entity_id,
            exc_info=True,
        )
        return None, None, None


def _get_rate_limiter(hass, platform: str):
    """One shared token-bucket limiter per platform, since credentials here
    are shared (reused from the official steam_online/playstation_network
    integration, or one manual override) across every tracked player, not
    per-player -- the budget has to be shared too."""
    from .const import (
        PSN_RATE_LIMIT_CAPACITY,
        PSN_RATE_LIMIT_PER_SECOND,
        STEAM_RATE_LIMIT_CAPACITY,
        STEAM_RATE_LIMIT_PER_SECOND,
        STEAMGRIDDB_RATE_LIMIT_CAPACITY,
        STEAMGRIDDB_RATE_LIMIT_PER_SECOND,
    )
    from .rate_limiter import RateLimiter

    limiters = hass.data.setdefault("gaming_status_rate_limiters", {})
    if platform not in limiters:
        if platform == "steam":
            limiters[platform] = RateLimiter(
                STEAM_RATE_LIMIT_CAPACITY, STEAM_RATE_LIMIT_PER_SECOND, name="steam"
            )
        elif platform == "psn":
            limiters[platform] = RateLimiter(
                PSN_RATE_LIMIT_CAPACITY, PSN_RATE_LIMIT_PER_SECOND, name="psn"
            )
        elif platform == "steamgriddb":
            limiters[platform] = RateLimiter(
                STEAMGRIDDB_RATE_LIMIT_CAPACITY,
                STEAMGRIDDB_RATE_LIMIT_PER_SECOND,
                name="steamgriddb",
            )
        else:
            raise ValueError(f"No rate limiter configured for platform {platform!r}")
    return limiters[platform]


def _get_steam_client(hass, api_key: str = ""):
    """Steam Web API client. A blank api_key is valid for the public,
    unauthenticated appdetails (rating) lookup -- only the achievement
    endpoints actually need a real key. Cached per-key so repeated calls for
    the same player reuse one client rather than constructing a new one
    every time."""
    from .steam_client import SteamClient

    clients = hass.data.setdefault("gaming_status_steam_clients", {})
    if api_key not in clients:
        clients[api_key] = SteamClient(
            async_get_clientsession(hass), api_key, _get_rate_limiter(hass, "steam")
        )
    return clients[api_key]


def _get_psn_client(hass, npsso: str):
    """PSN client singleton per NPSSO -- holds live OAuth token state, so
    reusing one instance across every player sharing the same NPSSO (the
    common case: one playstation_network entry, many tracked friends) avoids
    each of them independently re-deriving a separate session."""
    from .psn_client import PsnClient

    clients = hass.data.setdefault("gaming_status_psn_clients", {})
    if npsso not in clients:
        clients[npsso] = PsnClient(
            async_get_clientsession(hass), npsso, _get_rate_limiter(hass, "psn")
        )
    return clients[npsso]


_STEAM_ESRB_AGE_FLOOR = {"e": 0, "e10": 10, "e10+": 10, "t": 13, "m": 17, "ao": 18}

# Maps a raw Xbox catalog rating_id (board:tier) to a human-readable label.
# An unrecognized/future tier falls back to the text after the colon rather
# than being dropped, same "prefer mapped, fall back to raw" philosophy as
# _STEAM_ESRB_AGE_FLOOR's own required_age fallback below.
_XBOX_RATING_ID_LABELS = {
    "ESRB:EC": "Early Childhood",
    "ESRB:E": "Everyone",
    "ESRB:E10+": "Everyone 10+",
    "ESRB:T": "Teen",
    "ESRB:M": "Mature",
    "ESRB:AO": "Adults Only",
    "PEGI:3": "PEGI 3",
    "PEGI:7": "PEGI 7",
    "PEGI:12": "PEGI 12",
    "PEGI:16": "PEGI 16",
    "PEGI:18": "PEGI 18",
}


async def _fetch_native_rating(hass, platform, platform_context):
    """Tries a platform-native rating source. Returns a rating dict in the
    same shape fetch_game_rating returns (and caches), or None if
    unavailable/the lookup fails -- callers report "unrated" in that case
    (no third-party fallback). Never raises."""
    try:
        if platform == "xbox":
            min_age = platform_context.get("min_age")
            # Xbox's min_age is a numeric age floor Microsoft synthesizes
            # from whatever regional rating board applies to that title --
            # not guaranteed to sit on the exact same 0/10/13/17/18 buckets
            # Steam's native rating uses, but age_floor is already a "board-agnostic
            # numeric" field meant to be compared with >=, not exact-matched
            # against those 5 labels, so a raw numeric value from a
            # different board is still meaningful here. This is free (a
            # sibling entity's own attribute, no API call) and left
            # completely untouched by the catalog lookup below -- esrb/pegi
            # text is additive, never a replacement for it.
            age_floor = int(min_age) if min_age is not None else None

            esrb = None
            pegi = None
            descriptors = []
            xbox_config_entry = platform_context.get("xbox_config_entry")
            oauth_session = platform_context.get("oauth_session")
            xuid = platform_context.get("xuid")
            if xbox_config_entry and oauth_session and xuid:
                try:
                    from . import xbox_client

                    client = xbox_client.get_xbox_client(
                        hass, xbox_config_entry, oauth_session
                    )
                    title_id = await xbox_client.async_get_current_title_id(
                        client, xuid
                    )
                    if title_id:
                        ratings = await xbox_client.async_get_catalog_content_ratings(
                            client, title_id
                        )
                        by_system = {
                            r["rating_system"]: r
                            for r in ratings or []
                            if r.get("rating_system")
                        }
                        # Never populate both -- matches the strict
                        # either/or invariant every other branch here
                        # already upholds (Steam always leaves pegi: None;
                        # PSN sets exactly one based on authority), which is
                        # what makes the master sensor's esrb-or-pegi
                        # fallback correct.
                        chosen = by_system.get("ESRB") or by_system.get("PEGI")
                        if chosen:
                            rating_id = chosen.get("rating_id") or ""
                            label = _XBOX_RATING_ID_LABELS.get(
                                rating_id,
                                rating_id.split(":", 1)[-1]
                                if ":" in rating_id
                                else rating_id,
                            )
                            if chosen["rating_system"] == "ESRB":
                                esrb = label
                            else:
                                pegi = label
                            descriptors = chosen.get("descriptors") or []
                except Exception as e:
                    _LOGGER.debug(
                        "[Gaming Status] Xbox catalog rating lookup failed: %s", e
                    )

            if age_floor is None and esrb is None and pegi is None:
                return None

            return {
                "esrb": esrb,
                "pegi": pegi,
                "age_floor": age_floor,
                "descriptors": descriptors,
                "unrated": False,
                "source": "xbox_native",
            }

        if platform == "steam":
            appid = platform_context.get("appid")
            if not appid:
                return None
            client = _get_steam_client(hass)
            details = await client.async_get_appdetails(appid)
            if not details:
                return None
            esrb = (details.get("ratings") or {}).get("esrb") or {}
            rating_code = str(esrb.get("rating") or "").lower()
            age_floor = _STEAM_ESRB_AGE_FLOOR.get(rating_code)
            if age_floor is None:
                required_age = details.get("required_age")
                age_floor = int(required_age) if required_age else None
            if age_floor is None:
                return None
            descriptors = [
                d.strip()
                for d in str(esrb.get("descriptors") or "").split("\n")
                if d.strip()
            ]
            return {
                "esrb": rating_code.upper() or None,
                "pegi": None,
                "age_floor": age_floor,
                "descriptors": descriptors,
                "unrated": False,
                "source": "steam_native",
            }

        if platform == "psn":
            npsso = platform_context.get("npsso")
            title_id = platform_context.get("title_id")
            if not npsso or not title_id:
                return None
            client = _get_psn_client(hass, npsso)
            concepts = await client.async_get_title_concepts(title_id)
            if not concepts:
                return None
            min_age = concepts.get("minimumAge")
            if min_age is None:
                return None
            content_rating = concepts.get("contentRating") or {}
            authority = content_rating.get("authority")
            description = content_rating.get("description") or content_rating.get(
                "name"
            )
            return {
                "esrb": description if authority == "ESRB" else None,
                "pegi": description if authority == "PEGI" else None,
                "age_floor": int(min_age),
                "descriptors": [],
                "unrated": False,
                "source": "psn_native",
            }
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Native rating lookup failed for platform=%s: %s",
            platform,
            e,
        )
        return None
    return None


RECENT_UNLOCKS_LIMIT = 10


async def fetch_steam_achievements(hass, steamid64, api_key, appid):
    """Earned/total achievement counts, plus a bounded newest-first
    recent-unlocks list, for one game on one Steam account. Never raises --
    returns None on any failure (missing key, network error, or Steam's own
    per-account achievement-data restriction, see steam_client.py). The
    schema call (total achievements, and display names/icons/descriptions for
    the recent-unlocks list) is cached forever per appid, since it's static;
    the earned count/unlock list is always fetched fresh -- the caller
    (sensor.py) controls how often via its own recheck-interval guard, so
    caching it here would just serve stale data.

    recent_unlocks is essentially free: GetPlayerAchievements already
    returns each achievement's `unlocktime`, previously fetched and
    discarded after summing -- no new API call versus before.
    """
    if not steamid64 or not api_key or not appid:
        return None
    try:
        client = _get_steam_client(hass, api_key)

        if appid in STEAM_SCHEMA_CACHE:
            STEAM_SCHEMA_CACHE.move_to_end(appid)
            total, display_names, icons, descriptions = STEAM_SCHEMA_CACHE[appid]
        else:
            schema = await client.async_get_schema_for_game(appid)
            total = schema.get("total_achievements", 0)
            display_names = schema.get("display_names") or {}
            icons = schema.get("icons") or {}
            descriptions = schema.get("descriptions") or {}
            STEAM_SCHEMA_CACHE[appid] = (total, display_names, icons, descriptions)
            STEAM_SCHEMA_CACHE.move_to_end(appid)
            if len(STEAM_SCHEMA_CACHE) > MAX_ENRICHMENT_CACHE_SIZE:
                STEAM_SCHEMA_CACHE.popitem(last=False)

        if not total:
            return {
                "earned": 0,
                "total": 0,
                "recent_unlocks": [],
                "last_achievement_at": None,
            }

        achievements = await client.async_get_player_achievements(steamid64, appid)
        earned_achievements = [a for a in achievements if a.get("achieved")]
        earned_achievements.sort(key=lambda a: a.get("unlocktime") or 0, reverse=True)
        recent_unlocks = [
            {
                "name": display_names.get(a.get("apiname"), a.get("apiname")),
                "description": descriptions.get(a.get("apiname")),
                "unlocked_at": (
                    datetime.fromtimestamp(a["unlocktime"], tz=UTC).isoformat()
                    if a.get("unlocktime")
                    else None
                ),
                "icon_url": icons.get(a.get("apiname")),
            }
            for a in earned_achievements[:RECENT_UNLOCKS_LIMIT]
        ]
        # Most recent achievement unlock, if any -- Steam's own analog to
        # Xbox's last-played timestamp / PSN's last-trophy-earned timestamp
        # (see _scan_steam's use of this as that game's _activity_ts).
        # earned_achievements is already sorted newest-first above, so this
        # is free; None for a game with zero achievements earned yet, same
        # as a game with no achievements at all.
        last_achievement_at = (
            datetime.fromtimestamp(
                earned_achievements[0]["unlocktime"], tz=UTC
            ).isoformat()
            if earned_achievements and earned_achievements[0].get("unlocktime")
            else None
        )
        return {
            "earned": len(earned_achievements),
            "total": total,
            "recent_unlocks": recent_unlocks,
            "last_achievement_at": last_achievement_at,
        }
    except AuthError as e:
        _LOGGER.warning(
            "[Gaming Status] Steam rejected the API key fetching achievements "
            "for appid %s -- it may be invalid or revoked: %s",
            appid,
            e,
        )
        return None
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Steam achievement fetch failed for appid %s: %s", appid, e
        )
        return None


async def resolve_psn_title_id(hass, npsso, account_id):
    """Resolves the PSN `npTitleId` for whatever the account is currently
    playing, via one lightweight get_presence() call -- works identically
    for the account owner or any tracked friend (see psn_client.py). Never
    raises; returns None if presence isn't visible this cycle or no game
    title info is present."""
    if not npsso or not account_id:
        return None
    try:
        client = _get_psn_client(hass, npsso)
        presence = await client.async_get_presence(account_id)
        if not presence:
            return None
        titles = ((presence.get("basicPresence") or {}).get("gameTitleInfoList")) or []
        return titles[0].get("npTitleId") if titles else None
    except AuthError as e:
        _LOGGER.warning(
            "[Gaming Status] PSN rejected the request resolving a title_id -- "
            "the NPSSO cookie may have expired and need replacing: %s",
            e,
        )
        return None
    except Exception as e:
        _LOGGER.debug("[Gaming Status] PSN presence/title_id resolution failed: %s", e)
        return None


async def fetch_psn_trophies(
    hass, npsso, account_id, game_name, title_id=None, include_recent_unlocks=False
):
    """Earned/total trophy counts (by tier) for one game on one PSN account,
    optionally with a bounded newest-first recent-unlocks list. Never raises
    -- returns None on any failure. Resolution order:

    1. If `title_id` is already known (resolved via a fresh get_presence()
       call in sensor.py), call the targeted, non-paginated
       trophy_titles_for_title lookup directly -- one request, works
       identically for the account owner or any tracked friend.
    2. Otherwise (presence didn't yield a usable title_id this cycle):
       check the in-memory {normalized_game_name: title_id} cache from a
       previous fallback resolution for this same game, and use that
       instead of scanning again.
    3. Only if neither of the above works: fall back to scanning the full
       trophyTitles list by name -- a rare path, and only ever hit once per
       distinct game per HA runtime (the resolved title_id gets cached for
       next time).

    `include_recent_unlocks` costs two EXTRA requests (see psn_client.py's
    async_get_title_trophies_with_progress -- individual trophy detail
    genuinely isn't available from the single summary call above) -- the
    caller should only set it when it actually wants the detail refreshed
    (e.g. the tier counts just changed), not on every recheck.
    """
    if not npsso or not account_id:
        return None
    try:
        client = _get_psn_client(hass, npsso)
        cache_key = _normalize_game_name(game_name) if game_name else None

        entry = None
        if title_id:
            entry = await client.async_get_trophy_summary_for_title(
                account_id, title_id
            )
        elif cache_key and cache_key in PSN_TITLE_ID_CACHE:
            cached_title_id = PSN_TITLE_ID_CACHE[cache_key]
            PSN_TITLE_ID_CACHE.move_to_end(cache_key)
            entry = await client.async_get_trophy_summary_for_title(
                account_id, cached_title_id
            )

        if entry is None and cache_key:
            # Rare fallback -- full list scan, name-matched. Only reached
            # when presence-based title_id resolution isn't available.
            titles = await client.async_get_trophy_titles(account_id)
            for candidate in titles:
                if (
                    _normalize_game_name(candidate.get("trophyTitleName") or "")
                    == cache_key
                ):
                    entry = candidate
                    break

        if entry is None:
            return None

        if cache_key and title_id:
            PSN_TITLE_ID_CACHE[cache_key] = title_id
            PSN_TITLE_ID_CACHE.move_to_end(cache_key)
            if len(PSN_TITLE_ID_CACHE) > MAX_ENRICHMENT_CACHE_SIZE:
                PSN_TITLE_ID_CACHE.popitem(last=False)

        earned = entry.get("earnedTrophies") or {}
        defined = entry.get("definedTrophies") or {}
        result = {
            "earned": {
                k: int(earned.get(k, 0))
                for k in ("bronze", "silver", "gold", "platinum")
            },
            "total": {
                k: int(defined.get(k, 0))
                for k in ("bronze", "silver", "gold", "platinum")
            },
            "recent_unlocks": [],
        }

        # Prefer the caller's own already-known title_id over re-deriving it
        # from this response -- confirmed live that PSN's single-title
        # npTitleIds-filtered trophyTitles lookup can omit npCommunicationId
        # from the returned entry for some titles, even though the exact
        # same ID was just used to filter for it. When that happens the
        # entry-derived value is silently None/falsy, the guard below skips
        # the detail fetch entirely, and recent_unlocks stays permanently
        # empty with zero errors anywhere (aggregate counts still come from
        # this same entry and stay correct, masking the gap). Only the
        # fallback full-listing branch above (no explicit title_id) has to
        # rely on entry.get(...) alone, since that response is confirmed to
        # always carry the field.
        np_communication_id = entry.get("npCommunicationId") or title_id
        if include_recent_unlocks and not np_communication_id:
            _LOGGER.debug(
                "[Gaming Status] PSN trophy detail skipped for %s -- no "
                "npCommunicationId available from either the response or "
                "the caller",
                game_name,
            )
        if include_recent_unlocks and np_communication_id:
            # PS5-native titles are registered under a different trophy-set
            # generation ("trophy2") than PS4/PS3/Vita-era titles ("trophy")
            # -- confirmed live that querying with the wrong one 404s this
            # detail endpoint even though the tier-count summary above
            # (this same `entry`) doesn't care and always succeeds. The
            # summary response carries which generation this title actually
            # uses; fall back to the older default only if it's absent.
            np_service_name = entry.get("npServiceName") or "trophy"
            trophies = await client.async_get_title_trophies_with_progress(
                account_id, np_communication_id, np_service_name=np_service_name
            )
            # Only require "earned" -- Sony's per-trophy detail endpoint
            # (which this list comes from) is known to sometimes lag behind
            # the tier-count summary endpoint (which the aggregate earned/
            # total counts above come from), so a trophy can be genuinely
            # earned before its own earned_at timestamp is populated here.
            # Excluding it entirely on a missing timestamp silently drops a
            # real unlock rather than just showing it undated.
            earned_trophies = [t for t in trophies if t.get("earned")]
            earned_trophies.sort(key=lambda t: t.get("earned_at") or "", reverse=True)
            result["recent_unlocks"] = [
                {
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "unlocked_at": t.get("earned_at"),
                    "tier": t.get("type"),
                    "icon_url": t.get("icon_url"),
                }
                for t in earned_trophies[:RECENT_UNLOCKS_LIMIT]
            ]

        return result
    except AuthError as e:
        _LOGGER.warning(
            "[Gaming Status] PSN rejected the request fetching trophies for "
            "%s -- the NPSSO cookie may have expired and need replacing: %s",
            game_name,
            e,
        )
        return None
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] PSN trophy fetch failed for %s: %s", game_name, e
        )
        return None


async def fetch_xbox_achievements(
    hass, xbox_config_entry, oauth_session, xuid, recent_limit=None
):
    """Earned/total achievement counts + a bounded newest-first
    recent-unlocks list for whatever `xuid` is currently playing, reusing
    the official xbox integration's own OAuth session (see
    utils.resolve_xbox_entry_and_session / xbox_client.py) instead of a
    separate credential. Never raises; returns None on any failure
    (presence not visible, no game currently playing, API error)."""
    if not xbox_config_entry or not oauth_session or not xuid:
        return None
    try:
        from . import xbox_client

        client = xbox_client.get_xbox_client(hass, xbox_config_entry, oauth_session)
        title_id = await xbox_client.async_get_current_title_id(client, xuid)
        if not title_id:
            return None
        return await xbox_client.async_get_achievements(
            client, xuid, title_id, recent_limit=recent_limit or RECENT_UNLOCKS_LIMIT
        )
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Xbox achievement fetch failed for xuid %s: %s", xuid, e
        )
        return None


async def fetch_xbox_title_achievement_counts(
    hass, xbox_config_entry, oauth_session, xuid, title_id, recent_limit=0
):
    """Authoritative earned/total achievement counts (plus, when
    recent_limit > 0, a bounded newest-first recent-unlocks list) for one
    specific, already-known title_id -- unlike fetch_xbox_achievements,
    this never resolves "currently playing" (the title may not be running
    at all). Used by library_scan.py both as a fallback for titles where
    the title-history endpoint's own totalAchievements field is
    live-confirmed unreliable (observed 0 for titles with nonzero
    currentAchievements, recent_limit=0 there -- unchanged), and as the
    per-title detail call for delta-detected/backfilled achievement
    history (recent_limit=RECENT_UNLOCKS_LIMIT there). Never raises;
    returns None on any failure."""
    if not xbox_config_entry or not oauth_session or not xuid or not title_id:
        return None
    try:
        from . import xbox_client

        client = xbox_client.get_xbox_client(hass, xbox_config_entry, oauth_session)
        return await xbox_client.async_get_achievements(
            client, xuid, title_id, recent_limit=recent_limit
        )
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Xbox per-title achievement fetch failed for xuid %s title %s: %s",
            xuid,
            title_id,
            e,
        )
        return None


async def fetch_steam_owned_games(hass, api_key, steamid64):
    """Full-library-scan source for Steam -- every game the account owns.
    Never raises; returns (games, error) -- error is None on success, or a
    human-readable description of what went wrong (rate limited, bad key,
    the account's "Game details" privacy toggle, network error) so a
    genuine API failure can be told apart from an account that legitimately
    owns zero games."""
    if not api_key or not steamid64:
        return [], None
    try:
        client = _get_steam_client(hass, api_key)
        return await client.async_get_owned_games(steamid64), None
    except ApiError as e:
        _LOGGER.debug(
            "[Gaming Status] Steam owned-games fetch failed for %s: %s", steamid64, e
        )
        return [], str(e)
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Steam owned-games fetch failed for %s: %s", steamid64, e
        )
        return [], f"{type(e).__name__}: {e}"


async def fetch_xbox_title_history(hass, xbox_config_entry, oauth_session, xuid):
    """Full-library-scan source for Xbox -- one non-paginated call already
    returns every title's achievement/gamerscore summary (see
    xbox_client.async_get_title_history). Never raises; returns (titles,
    error) -- see fetch_steam_owned_games for why error matters."""
    if not xbox_config_entry or not oauth_session or not xuid:
        return [], None
    try:
        from . import xbox_client

        client = xbox_client.get_xbox_client(hass, xbox_config_entry, oauth_session)
        return await xbox_client.async_get_title_history(client, xuid), None
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] Xbox title history fetch failed for %s: %s", xuid, e
        )
        return [], f"{type(e).__name__}: {e}"


async def fetch_psn_full_library(hass, npsso, account_id):
    """Full-library-scan source for PlayStation -- every title with a
    trophy list, tier counts included. Never raises; returns (titles,
    error) -- see fetch_steam_owned_games for why error matters."""
    if not npsso or not account_id:
        return [], None
    try:
        client = _get_psn_client(hass, npsso)
        return await client.async_get_trophy_titles(account_id), None
    except ApiError as e:
        _LOGGER.debug(
            "[Gaming Status] PSN full trophy-titles fetch failed for %s: %s",
            account_id,
            e,
        )
        return [], str(e)
    except Exception as e:
        _LOGGER.debug(
            "[Gaming Status] PSN full trophy-titles fetch failed for %s: %s",
            account_id,
            e,
        )
        return [], f"{type(e).__name__}: {e}"


async def fetch_and_cache_image(hass, remote_url, file_name):
    """Generic helper to cache any remote image locally."""
    from homeassistant.helpers.network import NoURLAvailableError, get_url

    try:
        base_url = get_url(hass, prefer_external=True)
    except NoURLAvailableError:
        base_url = ""

    cache_dir = Path(hass.config.path("www/gaming_status_cache"))

    # 1. Safely wrap the mkdir command to avoid kwarg TypeErrors
    def _ensure_dir():
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)

    await hass.async_add_executor_job(_ensure_dir)

    # The allowed charset has no path separators, so the only way this
    # could still escape cache_dir as a single path segment is "." or
    # ".." verbatim -- checked directly (no filesystem I/O needed) rather
    # than resolving the path, which would be a blocking call here.
    safe_file_name = re.sub(r"[^a-zA-Z0-9._]", "_", file_name)
    if safe_file_name in (".", ".."):
        _LOGGER.error("Refusing to cache avatar to an unsafe path: %s", file_name)
        return remote_url
    file_path = cache_dir / safe_file_name

    # 2. Return immediately if already cached
    if await hass.async_add_executor_job(file_path.exists):
        return f"{base_url}/local/gaming_status_cache/{safe_file_name}"

    # 3. Download and save
    try:
        if not await is_public_url(hass, remote_url):
            _LOGGER.warning(
                "Refusing to fetch avatar: URL does not resolve to a public host"
            )
            return remote_url

        session = async_get_clientsession(hass)
        async with session.get(remote_url, timeout=10) as resp:
            if resp.status == 200 and await is_public_url(hass, str(resp.url)):
                img_bytes = await _read_capped(resp)
                if img_bytes is not None:
                    # Safely wrap the file writing command
                    def _write_img():
                        file_path.write_bytes(img_bytes)

                    await hass.async_add_executor_job(_write_img)
                    return f"{base_url}/local/gaming_status_cache/{safe_file_name}"
    except Exception as e:
        _LOGGER.error("Failed to cache avatar %s: %s", remote_url, e)

    return remote_url  # Fallback to remote if download fails


def get_base_game_name(full_name):
    if not full_name:
        return full_name
    full_name_str = str(full_name)
    if " - Playing" in full_name_str:
        full_name_str = full_name_str.split(" - Playing", maxsplit=1)[0]
    elif " – Playing" in full_name_str:
        full_name_str = full_name_str.split(" – Playing")[0]
    elif " Playing " in full_name_str:
        full_name_str = full_name_str.split(" Playing ")[0]
    elif " - In The Menus" in full_name_str:
        full_name_str = full_name_str.split(" - In The Menus")[0]
    return full_name_str.strip()


def _get_gamertag_from_entity(source_entity_id, platform):
    try:
        object_id = source_entity_id.split(".")[1]
        if platform == "steam" and object_id.startswith("steam_"):
            return object_id[6:]
        if platform == "xbox" and "_status" in object_id:
            return object_id.split("_status")[0]
        if platform == "playstation":
            if object_id.endswith("_now_playing"):
                return object_id[: -len("_now_playing")]
            if "_online_status" in object_id:
                return object_id.split("_online_status")[0]
            if "_onlinestatus" in object_id:
                return object_id.split("_onlinestatus")[0]
    except Exception:
        pass
    try:
        return source_entity_id.split(".")[1]
    except Exception:
        return "unknown"


def _format_time(seconds):
    if not seconds or seconds < 0:
        return "0m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def top_n_games(breakdown, n=10):
    """Sort a {game: seconds} breakdown descending and return the top n as
    [{"game": ..., "hours": ...}, ...]. Shared by the platform and master
    sensors so their all-time rankings can't independently drift."""
    if not breakdown:
        return []
    ranked = sorted(breakdown.items(), key=lambda item: item[1], reverse=True)
    return [
        {"game": game, "hours": round(seconds / 3600, 1)}
        for game, seconds in ranked[:n]
    ]


_TRADEMARK_SYMBOLS_RE = re.compile(r"[™®©]")
# Includes both the plain ASCII apostrophe and the curly/smart quote
# variants -- live-confirmed that different Xbox title_ids for what's
# conceptually the same franchise (e.g. separate SKUs/editions) can report
# an otherwise-identical name with different apostrophe characters (e.g.
# "Tom Clancy's" vs "Tom Clancy's"), which would otherwise silently defeat
# Title Overrides and every other lookup keyed on this normalization.
_NORMALIZE_GAME_NAME_RE = re.compile(r"[,:\-™®©'‘’]")


def _format_game_name_for_display(game_name):
    if not game_name:
        return game_name
    clean_name = " ".join(str(game_name).split())

    # Strip a " - Subtitle" suffix and trademark symbols BEFORE the Title
    # Overrides lookup, not after -- a raw name that differs from the user's
    # configured override only by an edition/bundle suffix (e.g. "Tom
    # Clancy's The Division 2 - Warlords of New York Edition", which some
    # platforms track as a distinct title from the base game) would
    # otherwise normalize to a longer string that never matches a plain
    # "Tom Clancy's The Division 2" override, silently leaving it
    # unrenamed even though this same cleanup would have stripped the exact
    # suffix a moment later.
    if " - " in clean_name:
        clean_name = clean_name.split(" - ")[0].strip()
    clean_name = _TRADEMARK_SYMBOLS_RE.sub("", clean_name).strip()

    clean_name = GAME_TITLE_OVERRIDES.get(_normalize_game_name(clean_name), clean_name)

    for pattern in COMPILED_TITLE_CLEANUPS:
        clean_name = pattern.sub("", clean_name).strip()

    clean_name = " ".join(clean_name.split())
    return clean_name


def _normalize_game_name(game_name):
    """
    Canonical matching key: lowercased, with commas/colons/dashes/trademark
    symbols removed and whitespace collapsed. Used everywhere a game title
    needs to be compared or looked up (overrides, artwork maps, ratings,
    cross-platform same-game detection, cache/filename keys) so punctuation
    differences never cause a mismatch.
    """
    if not game_name:
        return ""
    clean = _NORMALIZE_GAME_NAME_RE.sub("", str(game_name).lower())
    return " ".join(clean.split())


def _is_same_base_game(name_a, name_b, prefix_words):
    if not prefix_words or prefix_words <= 0:
        return False
    words_a = _normalize_game_name(name_a).split()
    words_b = _normalize_game_name(name_b).split()
    if not words_a or not words_b:
        return False
    return words_a[:prefix_words] == words_b[:prefix_words]


def _safe_parse_datetime(value):
    if not value:
        return None
    try:
        dt_obj = value if isinstance(value, datetime) else parser.isoparse(str(value))
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=UTC)
        else:
            dt_obj = dt_obj.astimezone(UTC)
        return dt_obj
    except Exception:
        return None


def _parse_relative_time_from_status(status_text):
    if not status_text or isinstance(status_text, datetime):
        return None
    text = str(status_text).lower()
    if "last seen" not in text and "last online" not in text:
        return None
    try:
        now = dt_util.now()
        parts = text.split(" ")
        for i, part in enumerate(parts):
            if part.isdigit() and i + 1 < len(parts):
                val, unit, delta = int(part), parts[i + 1], None
                if "m" in unit:
                    delta = timedelta(minutes=val)
                elif "h" in unit:
                    delta = timedelta(hours=val)
                elif "d" in unit:
                    delta = timedelta(days=val)
                elif "s" in unit:
                    delta = timedelta(seconds=val)
                if delta:
                    return (now - delta).isoformat()
            if part[-1] in ["d", "h", "m", "s"] and part[:-1].isdigit():
                val, unit, delta = int(part[:-1]), part[-1], None
                if unit == "d":
                    delta = timedelta(days=val)
                elif unit == "h":
                    delta = timedelta(hours=val)
                elif unit == "m":
                    delta = timedelta(minutes=val)
                elif unit == "s":
                    delta = timedelta(seconds=val)
                if delta:
                    return (now - delta).isoformat()
    except Exception:
        return None
    return None


def _calculate_time_ago_v2(timestamp_val):
    if not timestamp_val:
        return None, "No TS"
    try:
        ts = _safe_parse_datetime(timestamp_val)
        if not ts:
            return None, "Parse Fail"
        now = dt_util.now()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=now.tzinfo)
        else:
            ts = ts.astimezone(now.tzinfo)

        seconds = int((now - ts).total_seconds())
        debug = f"Now:{int(now.timestamp())} - TS:{int(ts.timestamp())} = {seconds}s"

        if seconds < 0:
            return ("just now" if seconds > -60 else "in future"), debug
        if seconds < 60:
            return "just now", debug
        if seconds < 3600:
            return f"{seconds // 60}m ago", debug
        if seconds < 86400:
            return f"{seconds // 3600}h ago", debug
        return f"{seconds // 86400}d ago", debug
    except Exception as e:
        return None, f"Err: {e}"


def safe_url(url):
    if isinstance(url, str) and (url.startswith("http") or url.startswith("/")):
        return url
    return None


def url_host_matches(url, domain):
    """Check whether url's hostname is domain or a subdomain of it (not just a substring match)."""
    if not isinstance(url, str):
        return False
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return bool(host) and (host == domain or host.endswith("." + domain))


_SAFE_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "ico",
    "gif",
    "bmp",
    "tiff",
    "tif",
}

MAX_IMAGE_DOWNLOAD_BYTES = (
    25 * 1024 * 1024
)  # generous for any real cover/hero/logo/icon


async def _read_capped(resp, max_bytes=MAX_IMAGE_DOWNLOAD_BYTES):
    """Read a response body in chunks, aborting early (returning None) if
    it exceeds max_bytes -- avoids ever buffering a hostile/oversized
    response fully into memory, unlike checking the size after resp.read()."""
    content_length = resp.content_length
    if content_length is not None and content_length > max_bytes:
        return None
    chunks = []
    total = 0
    async for chunk in resp.content.iter_chunked(65536):
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def safe_image_ext(url, default="png"):
    """Extract a safe file extension from a URL, rejecting anything that isn't a known image type."""
    try:
        raw = urlparse(url).path.rsplit(".", 1)[-1]
    except ValueError:
        return default
    raw = raw.lower()
    return raw if raw in _SAFE_IMAGE_EXTENSIONS else default


async def is_public_url(hass, url):
    """Reject non-http(s) URLs and URLs whose host resolves to a private/loopback/link-local address, to prevent SSRF."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        hostname = parsed.hostname

        def _resolve():
            return {info[4][0] for info in socket.getaddrinfo(hostname, None)}

        addrs = await hass.async_add_executor_job(_resolve)
        if not addrs:
            return False
        for addr in addrs:
            ip = ipaddress.ip_address(addr)
            # is_global is a strict superset of the six checks this
            # replaced for every case EXCEPT multicast, which Python's
            # ipaddress module counts as "global" -- verified empirically
            # (e.g. 224.0.0.1, ff0e::1) -- so it needs its own check.
            if not ip.is_global or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


async def check_steam_url_validity(hass, url):
    return True


async def get_steam_game_cover(hass, game_name, game_id=None):
    return await get_steamgriddb_game_cover(hass, game_name)


def extract_vibrant_color(image_path):
    """Extracts the most dominant vibrant color from an image, with a safe fallback."""
    try:
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        img = img.resize((50, 50))
        pixels = img.getdata()

        color_counts = {}
        fallback_r, fallback_g, fallback_b = 0, 0, 0
        total_pixels = 0

        for r, g, b in pixels:
            # Keep a running total for the fallback average
            fallback_r += r
            fallback_g += g
            fallback_b += b
            total_pixels += 1

            # Masking: Ignore pixels that are too dark, white, or grayscale
            max_val, min_val = max(r, g, b), min(r, g, b)
            saturation = max_val - min_val

            # Require minimum brightness and color saturation to be considered "vibrant"
            if max_val > 50 and min_val < 200 and saturation > 20:
                color = (
                    min(round(r / 15) * 15, 255),
                    min(round(g / 15) * 15, 255),
                    min(round(b / 15) * 15, 255),
                )
                color_counts[color] = color_counts.get(color, 0) + 1

        if not color_counts:
            # Fallback: If all pixels were filtered out, calculate the true average
            if total_pixels > 0:
                avg_r = int(fallback_r / total_pixels)
                avg_g = int(fallback_g / total_pixels)
                avg_b = int(fallback_b / total_pixels)
                return f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}"
            return "#333333"  # Absolute fallback for completely broken images

        dominant_rgb = max(color_counts, key=color_counts.get)
        r, g, b = [min(c, 255) for c in dominant_rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to extract vibrant color from %s: %s", image_path, e
        )
        return None


def get_cached_remote_url(game_name, asset_type="grid", *, require_remote_host=True):
    """
    Retrieve the cached asset URL for a game.

    By default (require_remote_host=True), only returns a URL if it still
    points at the remote SteamGridDB CDN, bypassing any locally-cached
    copy -- useful for cloud webhooks like Discord when Home Assistant
    lacks an external domain and local-to-external URL construction has
    already failed once (see GamingNotifier._make_external_url's own
    fallback use of this function).

    With require_remote_host=False, returns whatever is cached regardless
    of host (a local-cache-baked-in URL, a bare "/local/..." path, or a
    remote CDN URL) -- callers must run the result through
    GamingNotifier._make_external_url() themselves before handing it to a
    webhook, exactly as session start/stop notifications already do.
    """
    if not game_name:
        return None

    # ASSET_URL_CACHE is keyed by the normalized name (see fetch_game_assets),
    # not the raw display name this is usually called with.
    cache_entry = ASSET_URL_CACHE.get(_normalize_game_name(game_name))
    if not cache_entry:
        return None

    url = cache_entry.get(asset_type)
    if not url:
        return None
    if require_remote_host and not url_host_matches(url, "steamgriddb.com"):
        return None

    return url
