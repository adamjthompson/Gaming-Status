"""
Utilities for Gaming Status
"""
import logging
import re
import os
import time
import socket
import ipaddress
from urllib.parse import quote, urlparse
from datetime import datetime, timezone, timedelta
from dateutil import parser
from collections import OrderedDict
from pathlib import Path
from PIL import Image

from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url, NoURLAvailableError

_LOGGER = logging.getLogger(__name__)

# Initialize empty globals (Populated securely by setup)
GAME_TITLE_OVERRIDES = {}
GAME_COLOR_OVERRIDES = {}
RATING_OVERRIDES = {}
TITLE_CLEANUPS = []
COMPILED_TITLE_CLEANUPS = []
STEAMGRIDDB_API_KEY = None
RAWG_API_KEY = None

# Native platform achievement/trophy/rating enrichment (current game only --
# see steam_client.py/psn_client.py). Opt-in, off by default.
ENABLE_PLATFORM_ENRICHMENT = False
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
PSN_TITLE_ID_CACHE = OrderedDict()  # normalized_game_name -> title_id, rare-fallback-path only
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

# Content-rating cache: a confirmed rating is immutable and cached forever
# (just the LRU size cap below). An "unrated" result is NOT trusted forever,
# since it may reflect a transient lookup failure or data RAWG hasn't added
# yet, so it gets re-checked after RATING_RECHECK_SECONDS instead of sticking
# until the next HA restart.
RATING_CACHE = OrderedDict()
MAX_RATING_CACHE_SIZE = 500
RATING_RECHECK_SECONDS = 86400  # 24 hours
_RATINGS_MISSING_KEY_WARNED = False

# Maps RAWG's esrb_rating.slug values to a board-agnostic numeric age floor.
ESRB_AGE_FLOOR = {
    "everyone": 0,
    "everyone-10-plus": 10,
    "teen": 13,
    "mature": 17,
    "adults-only": 18,
    "rating-pending": None,
}

# Display label for a manually-overridden age floor (RATING_OVERRIDES).
AGE_FLOOR_LABELS = {0: "Everyone", 10: "Everyone 10+", 13: "Teen", 17: "Mature", 18: "Adults Only"}

def compile_title_cleanups():
    """Pre-compile regex patterns for performance."""
    global COMPILED_TITLE_CLEANUPS
    COMPILED_TITLE_CLEANUPS = [re.compile(re.escape(p), re.IGNORECASE) for p in TITLE_CLEANUPS]

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
                _LOGGER.error("Gaming Status failed to delete aged image %s: %s", f.name, e)

    # 2. Prune by File Count (if feature is enabled)
    if CACHE_MAX_FILES > 0 and len(files) > CACHE_MAX_FILES:
        # Sort files by modification time, oldest first
        files.sort(key=lambda x: x.stat().st_mtime)
        files_to_delete = files[:-CACHE_MAX_FILES]
        
        for f in files_to_delete:
            try:
                f.unlink()
            except OSError as e:
                _LOGGER.error("Gaming Status failed to delete excess image %s: %s", f.name, e)

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
            "grid": CUSTOM_GRID_MAP, "hero": CUSTOM_HERO_MAP,
            "logo": CUSTOM_LOGO_MAP, "icon": CUSTOM_ICON_MAP
        }

        safe_file_prefix = re.sub(r'[^a-z0-9]', '_', cache_key)
        safe_file_prefix = re.sub(r'_+', '_', safe_file_prefix).strip('_')

        for asset_type, map_dict in override_maps.items():
            # Safety net: re-normalize keys in case older un-migrated data exists in the dictionary
            safe_map = {_normalize_game_name(k): v for k, v in map_dict.items()}

            if search_name in safe_map:
                remote_url = safe_url(safe_map[search_name])
                if not remote_url: continue
                
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
                        _LOGGER.warning("Refusing to fetch override art for %s (%s): URL does not resolve to a public host", game_name, asset_type)
                    else:
                        async with session.get(remote_url, timeout=15) as img_resp:
                            if img_resp.status == 200:
                                img_bytes = await img_resp.read()
                                await hass.async_add_executor_job(lambda: file_path.write_bytes(img_bytes))
                except Exception as e:
                    _LOGGER.error("Failed to cache override for %s (%s): %s", game_name, asset_type, e)

                try:
                    mt = int(await hass.async_add_executor_job(os.path.getmtime, file_path))
                except Exception:
                    mt = int(time.time())
                assets[asset_type] = f"{base_url}/local/gaming_status_cache/{file_name}?v={mt}"

        def _update_cache(name, data_dict):
            final_dict = {k: assets[k] or data_dict.get(k) for k in assets}
            
            # ONLY cache to RAM if we successfully retrieved at least one image
            if any(final_dict.values()):
                ASSET_URL_CACHE[name] = final_dict
                ASSET_URL_CACHE.move_to_end(name)
                if len(ASSET_URL_CACHE) > MAX_CACHE_SIZE:
                    ASSET_URL_CACHE.popitem(last=False)
                
                # Fire off non-blocking cache cleanup whenever a NEW game enters RAM
                if USE_LOCAL_CACHE:
                    async def _run_cleanup():
                        await hass.async_add_executor_job(_clean_image_cache, cache_dir)
                    hass.async_create_task(_run_cleanup())
                
            return final_dict

        # If the user provided ALL 4 custom images manually, skip the API entirely!
        if all(assets.values()):
            return _update_cache(cache_key, assets)

        if not STEAMGRIDDB_API_KEY:
            if not _MISSING_KEY_WARNED:
                _LOGGER.warning("[Gaming Status] SteamGridDB API key is not configured.")
                _MISSING_KEY_WARNED = True
            return _update_cache(cache_key, assets)

        # 4. Fetch from SteamGridDB
        fetched_assets = {"grid": None, "hero": None, "logo": None, "icon": None}
        headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}

        try:
            safe_title = quote(game_name, safe='')
            search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{safe_title}"
            
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return _update_cache(cache_key, fetched_assets)
                search_data = await resp.json()

            if not search_data.get("data"): return _update_cache(cache_key, fetched_assets)
                
            game_id = search_data["data"][0]["id"]
            endpoints = {
                "grid": f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}",
                "hero": f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}",
                "logo": f"https://www.steamgriddb.com/api/v2/logos/game/{game_id}",
                "icon": f"https://www.steamgriddb.com/api/v2/icons/game/{game_id}"
            }
            
            for asset_type, endpoint in endpoints.items():
                if assets[asset_type]: continue # Already filled by override
                    
                async with session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        asset_data = await resp.json()
                        if asset_data.get("data"):
                            # Scoring Algorithm
                            def get_score(img):
                                score = 0
                                if img.get("style") == "official": score += 10
                                if img.get("mime") == "image/png": score += 5
                                return score
                                
                            best_art = sorted(asset_data["data"], key=get_score, reverse=True)[0]
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

                            if not file_path.exists():
                                if not await is_public_url(hass, remote_url):
                                    _LOGGER.warning("Refusing to fetch %s art for %s: URL does not resolve to a public host", asset_type, game_name)
                                else:
                                    async with session.get(remote_url, timeout=15) as img_resp:
                                        if img_resp.status == 200:
                                            img_bytes = await img_resp.read()
                                            await hass.async_add_executor_job(lambda: file_path.write_bytes(img_bytes))
                            
                            try:
                                mt = int(await hass.async_add_executor_job(os.path.getmtime, file_path))
                            except Exception:
                                mt = int(time.time())
                            fetched_assets[asset_type] = f"{base_url}/local/gaming_status_cache/{file_name}?v={mt}"
                                    
        except Exception as e:
            _LOGGER.error("Failed to fetch assets for %s: %s", game_name, e)

        return _update_cache(cache_key, fetched_assets)

    finally:
        # ALWAYS release the lock, even if the API throws an unexpected error
        lock.set()
        hass.data["gaming_status_locks"].pop(cache_key, None)

async def get_steamgriddb_game_cover(hass, game_name):
    """Backward compatibility wrapper."""
    if not game_name:
        return None
    assets = await fetch_game_assets(hass, game_name)
    return assets.get("hero") or assets.get("grid")

async def fetch_game_rating(hass, game_name, platform=None, platform_context=None):
    """
    Fetch content/age-rating metadata for a game.
    A manual entry in RATING_OVERRIDES always takes priority and skips the
    cache/API entirely. Otherwise, if platform enrichment is enabled and the
    caller passed enough context, tries a platform-native rating first (see
    _fetch_native_rating) -- only falling through to RAWG.io if that's
    unavailable/fails. A confirmed rating (from either source) is cached
    forever (ratings don't change). An "unrated" result (no match, no ESRB
    data, or a failed lookup) is only cached for RATING_RECHECK_SECONDS, then
    retried, since it may reflect a transient failure or data not yet added.

    `platform`/`platform_context` are optional and only used for the native
    lookup: platform="xbox" -> {"min_age": <already-known value, no HTTP
    call needed>}; platform="steam" -> {"appid": int}; platform="psn" ->
    {"npsso": str, "title_id": str}.

    Returns a dict like {"esrb": "M", "pegi": None, "age_floor": 17,
    "descriptors": [...], "unrated": False, "source": "rawg"}, or None if no
    rating source (native or RAWG) is available.
    """
    import asyncio
    global _RATINGS_MISSING_KEY_WARNED

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
        is_stale = cached.get("unrated") and (time.time() - cached.get("checked_at", 0)) > RATING_RECHECK_SECONDS
        if not is_stale:
            RATING_CACHE.move_to_end(cache_key)
            return cached

    if ENABLE_PLATFORM_ENRICHMENT and platform and platform_context:
        native = await _fetch_native_rating(hass, platform, platform_context)
        if native is not None:
            native["checked_at"] = time.time()
            RATING_CACHE[cache_key] = native
            RATING_CACHE.move_to_end(cache_key)
            if len(RATING_CACHE) > MAX_RATING_CACHE_SIZE:
                RATING_CACHE.popitem(last=False)
            return native

    if "gaming_status_rating_locks" not in hass.data:
        hass.data["gaming_status_rating_locks"] = {}

    if cache_key in hass.data["gaming_status_rating_locks"]:
        await hass.data["gaming_status_rating_locks"][cache_key].wait()
        if cache_key in RATING_CACHE:
            RATING_CACHE.move_to_end(cache_key)
            return RATING_CACHE[cache_key]
        return None

    lock = asyncio.Event()
    hass.data["gaming_status_rating_locks"][cache_key] = lock

    try:
        if not RAWG_API_KEY:
            if not _RATINGS_MISSING_KEY_WARNED:
                _LOGGER.warning("[Gaming Status] RAWG API key is not configured.")
                _RATINGS_MISSING_KEY_WARNED = True
            return None

        def _cache_and_return(data):
            data = dict(data)
            data["checked_at"] = time.time()
            RATING_CACHE[cache_key] = data
            RATING_CACHE.move_to_end(cache_key)
            if len(RATING_CACHE) > MAX_RATING_CACHE_SIZE:
                RATING_CACHE.popitem(last=False)
            return data

        unrated = {
            "esrb": None, "pegi": None, "age_floor": None,
            "descriptors": [], "unrated": True, "source": "rawg",
        }

        session = async_get_clientsession(hass)
        try:
            search_url = "https://api.rawg.io/api/games"
            params = {"key": RAWG_API_KEY, "search": game_name, "page_size": 1}
            async with session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "[Gaming Status] RAWG rating lookup for '%s' failed: HTTP %s - %s",
                        game_name, resp.status, body[:200],
                    )
                    return _cache_and_return(unrated)
                data = await resp.json()
        except Exception as e:
            _LOGGER.error("Failed to fetch rating for %s: %s", game_name, e)
            return _cache_and_return(unrated)

        results = data.get("results") or []
        if not results:
            _LOGGER.debug("[Gaming Status] RAWG returned no search results for '%s'", game_name)
            return _cache_and_return(unrated)

        matched_name = results[0].get("name")
        esrb = results[0].get("esrb_rating") or {}
        slug = esrb.get("slug")
        age_floor = ESRB_AGE_FLOOR.get(slug)

        if age_floor is None:
            _LOGGER.debug(
                "[Gaming Status] RAWG matched '%s' to '%s' but has no ESRB rating on file (slug=%s)",
                game_name, matched_name, slug,
            )

        rating = {
            "esrb": esrb.get("name"),
            "pegi": None,
            "age_floor": age_floor,
            "descriptors": [],
            "unrated": age_floor is None,
            "source": "rawg",
        }
        return _cache_and_return(rating)

    finally:
        lock.set()
        hass.data["gaming_status_rating_locks"].pop(cache_key, None)


# ---------------------------------------------------------------------------
# Native platform achievement/trophy/rating enrichment (current game only --
# see steam_client.py/psn_client.py for the actual HTTP clients this section
# orchestrates). Opt-in (ENABLE_PLATFORM_ENRICHMENT), off by default.
# ---------------------------------------------------------------------------

def _get_rate_limiter(hass, platform: str):
    """One shared token-bucket limiter per platform, since credentials here
    are shared (reused from the official steam_online/playstation_network
    integration, or one manual override) across every tracked player, not
    per-player -- the budget has to be shared too."""
    from .const import PSN_RATE_LIMIT_CAPACITY, PSN_RATE_LIMIT_PER_SECOND, STEAM_RATE_LIMIT_CAPACITY, STEAM_RATE_LIMIT_PER_SECOND
    from .rate_limiter import RateLimiter

    limiters = hass.data.setdefault("gaming_status_rate_limiters", {})
    if platform not in limiters:
        if platform == "steam":
            limiters[platform] = RateLimiter(STEAM_RATE_LIMIT_CAPACITY, STEAM_RATE_LIMIT_PER_SECOND, name="steam")
        elif platform == "psn":
            limiters[platform] = RateLimiter(PSN_RATE_LIMIT_CAPACITY, PSN_RATE_LIMIT_PER_SECOND, name="psn")
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
        clients[api_key] = SteamClient(async_get_clientsession(hass), api_key, _get_rate_limiter(hass, "steam"))
    return clients[api_key]


def _get_psn_client(hass, npsso: str):
    """PSN client singleton per NPSSO -- holds live OAuth token state, so
    reusing one instance across every player sharing the same NPSSO (the
    common case: one playstation_network entry, many tracked friends) avoids
    each of them independently re-deriving a separate session."""
    from .psn_client import PsnClient

    clients = hass.data.setdefault("gaming_status_psn_clients", {})
    if npsso not in clients:
        clients[npsso] = PsnClient(async_get_clientsession(hass), npsso, _get_rate_limiter(hass, "psn"))
    return clients[npsso]


_STEAM_ESRB_AGE_FLOOR = {"e": 0, "e10": 10, "e10+": 10, "t": 13, "m": 17, "ao": 18}


async def _fetch_native_rating(hass, platform, platform_context):
    """Tries a platform-native rating source. Returns a rating dict in the
    same shape fetch_game_rating returns (and caches), or None if
    unavailable/the lookup fails -- callers fall through to RAWG in that
    case. Never raises."""
    try:
        if platform == "xbox":
            min_age = platform_context.get("min_age")
            if min_age is None:
                return None
            # Xbox's min_age is a numeric age floor Microsoft synthesizes
            # from whatever regional rating board applies to that title --
            # not guaranteed to sit on the exact same 0/10/13/17/18 buckets
            # Steam/RAWG use, but age_floor is already a "board-agnostic
            # numeric" field meant to be compared with >=, not exact-matched
            # against those 5 labels, so a raw numeric value from a
            # different board is still meaningful here.
            return {
                "esrb": None, "pegi": None, "age_floor": int(min_age),
                "descriptors": [], "unrated": False, "source": "xbox_native",
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
            descriptors = [d.strip() for d in str(esrb.get("descriptors") or "").split("\n") if d.strip()]
            return {
                "esrb": rating_code.upper() or None, "pegi": None, "age_floor": age_floor,
                "descriptors": descriptors, "unrated": False, "source": "steam_native",
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
            description = content_rating.get("description") or content_rating.get("name")
            return {
                "esrb": description if authority == "ESRB" else None,
                "pegi": description if authority == "PEGI" else None,
                "age_floor": int(min_age), "descriptors": [], "unrated": False, "source": "psn_native",
            }
    except Exception as e:
        _LOGGER.debug("[Gaming Status] Native rating lookup failed for platform=%s: %s", platform, e)
        return None
    return None


async def fetch_steam_achievements(hass, steamid64, api_key, appid):
    """Earned/total achievement counts for one game on one Steam account.
    Never raises -- returns None on any failure (missing key, network
    error, or Steam's own per-account achievement-data restriction, see
    steam_client.py). The schema call (total achievements) is cached
    forever per appid, since it's static; the earned count is always
    fetched fresh -- the caller (sensor.py) controls how often via its own
    recheck-interval guard, so caching it here would just serve stale data.
    """
    if not steamid64 or not api_key or not appid:
        return None
    try:
        client = _get_steam_client(hass, api_key)

        if appid in STEAM_SCHEMA_CACHE:
            STEAM_SCHEMA_CACHE.move_to_end(appid)
            total = STEAM_SCHEMA_CACHE[appid]
        else:
            schema = await client.async_get_schema_for_game(appid)
            total = schema.get("total_achievements", 0)
            STEAM_SCHEMA_CACHE[appid] = total
            STEAM_SCHEMA_CACHE.move_to_end(appid)
            if len(STEAM_SCHEMA_CACHE) > MAX_ENRICHMENT_CACHE_SIZE:
                STEAM_SCHEMA_CACHE.popitem(last=False)

        if not total:
            return {"earned": 0, "total": 0}

        achievements = await client.async_get_player_achievements(steamid64, appid)
        earned = sum(1 for a in achievements if a.get("achieved"))
        return {"earned": earned, "total": total}
    except Exception as e:
        _LOGGER.debug("[Gaming Status] Steam achievement fetch failed for appid %s: %s", appid, e)
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
    except Exception as e:
        _LOGGER.debug("[Gaming Status] PSN presence/title_id resolution failed: %s", e)
        return None


async def fetch_psn_trophies(hass, npsso, account_id, game_name, title_id=None):
    """Earned/total trophy counts (by tier) for one game on one PSN account.
    Never raises -- returns None on any failure. Resolution order:

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
    """
    if not npsso or not account_id:
        return None
    try:
        client = _get_psn_client(hass, npsso)
        cache_key = _normalize_game_name(game_name) if game_name else None

        entry = None
        if title_id:
            entry = await client.async_get_trophy_summary_for_title(account_id, title_id)
        elif cache_key and cache_key in PSN_TITLE_ID_CACHE:
            cached_title_id = PSN_TITLE_ID_CACHE[cache_key]
            PSN_TITLE_ID_CACHE.move_to_end(cache_key)
            entry = await client.async_get_trophy_summary_for_title(account_id, cached_title_id)

        if entry is None and cache_key:
            # Rare fallback -- full list scan, name-matched. Only reached
            # when presence-based title_id resolution isn't available.
            titles = await client.async_get_trophy_titles(account_id)
            for candidate in titles:
                if _normalize_game_name(candidate.get("trophyTitleName") or "") == cache_key:
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
        return {
            "earned": {k: int(earned.get(k, 0)) for k in ("bronze", "silver", "gold", "platinum")},
            "total": {k: int(defined.get(k, 0)) for k in ("bronze", "silver", "gold", "platinum")},
        }
    except Exception as e:
        _LOGGER.debug("[Gaming Status] PSN trophy fetch failed for %s: %s", game_name, e)
        return None


async def fetch_and_cache_image(hass, remote_url, file_name):
    """Generic helper to cache any remote image locally."""
    from homeassistant.helpers.network import get_url, NoURLAvailableError
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
    
    file_path = cache_dir / file_name
    
    # 2. Return immediately if already cached
    if file_path.exists():
        return f"{base_url}/local/gaming_status_cache/{file_name}"
        
    # 3. Download and save
    try:
        if not await is_public_url(hass, remote_url):
            _LOGGER.warning("Refusing to fetch avatar: URL does not resolve to a public host")
            return remote_url

        session = async_get_clientsession(hass)
        async with session.get(remote_url, timeout=10) as resp:
            if resp.status == 200:
                img_bytes = await resp.read()

                # Safely wrap the file writing command
                def _write_img():
                    file_path.write_bytes(img_bytes)

                await hass.async_add_executor_job(_write_img)
                return f"{base_url}/local/gaming_status_cache/{file_name}"
    except Exception as e:
        _LOGGER.error("Failed to cache avatar %s: %s", remote_url, e)
        
    return remote_url # Fallback to remote if download fails

def get_base_game_name(full_name):
    if not full_name: return full_name
    full_name_str = str(full_name)
    if " - Playing" in full_name_str: full_name_str = full_name_str.split(" - Playing")[0]
    elif " – Playing" in full_name_str: full_name_str = full_name_str.split(" – Playing")[0]
    elif " Playing " in full_name_str: full_name_str = full_name_str.split(" Playing ")[0]
    elif " - In The Menus" in full_name_str: full_name_str = full_name_str.split(" - In The Menus")[0]
    return full_name_str.strip()

def _get_gamertag_from_entity(source_entity_id, platform):
    try:
        object_id = source_entity_id.split('.')[1]
        if platform == "steam" and object_id.startswith("steam_"): return object_id[6:]
        if platform == "xbox" and "_status" in object_id: return object_id.split("_status")[0]
        if platform == "playstation":
            if object_id.endswith("_now_playing"): return object_id[:-len("_now_playing")]
            if "_online_status" in object_id: return object_id.split("_online_status")[0]
            if "_onlinestatus" in object_id: return object_id.split("_onlinestatus")[0]
    except Exception: pass
    try: return source_entity_id.split('.')[1]
    except Exception: return "unknown"

def _format_time(seconds):
    if not seconds or seconds < 0: return "0m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def top_n_games(breakdown, n=10):
    """Sort a {game: seconds} breakdown descending and return the top n as
    [{"game": ..., "hours": ...}, ...]. Shared by the platform and master
    sensors so their all-time rankings can't independently drift."""
    if not breakdown:
        return []
    ranked = sorted(breakdown.items(), key=lambda item: item[1], reverse=True)
    return [{"game": game, "hours": round(seconds / 3600, 1)} for game, seconds in ranked[:n]]

def _format_game_name_for_display(game_name):
    if not game_name: return game_name
    clean_name = " ".join(str(game_name).split())
    clean_name = GAME_TITLE_OVERRIDES.get(_normalize_game_name(clean_name), clean_name)
    
    if " - " in clean_name: clean_name = clean_name.split(" - ")[0].strip()
    clean_name = re.sub(r'[™®©]', '', clean_name).strip()
    
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
    if not game_name: return ""
    clean = re.sub(r'[,:\-™®©]', '', str(game_name).lower())
    return " ".join(clean.split())

def _is_same_base_game(name_a, name_b, prefix_words):
    if not prefix_words or prefix_words <= 0: return False
    words_a = _normalize_game_name(name_a).split()
    words_b = _normalize_game_name(name_b).split()
    if not words_a or not words_b: return False
    return words_a[:prefix_words] == words_b[:prefix_words]

def _safe_parse_datetime(value):
    if not value: return None
    try:
        dt_obj = value if isinstance(value, datetime) else parser.isoparse(str(value))
        if dt_obj.tzinfo is None: dt_obj = dt_obj.replace(tzinfo=timezone.utc)
        else: dt_obj = dt_obj.astimezone(timezone.utc)
        return dt_obj
    except Exception: return None

def _parse_relative_time_from_status(status_text):
    if not status_text or isinstance(status_text, datetime): return None
    text = str(status_text).lower()
    if "last seen" not in text and "last online" not in text: return None
    try:
        now = dt_util.now()
        parts = text.split(" ")
        for i, part in enumerate(parts):
            if part.isdigit() and i + 1 < len(parts):
                val, unit, delta = int(part), parts[i+1], None
                if "m" in unit: delta = timedelta(minutes=val)
                elif "h" in unit: delta = timedelta(hours=val)
                elif "d" in unit: delta = timedelta(days=val)
                elif "s" in unit: delta = timedelta(seconds=val)
                if delta: return (now - delta).isoformat()
            if part[-1] in ['d', 'h', 'm', 's'] and part[:-1].isdigit():
                val, unit, delta = int(part[:-1]), part[-1], None
                if unit == 'd': delta = timedelta(days=val)
                elif unit == 'h': delta = timedelta(hours=val)
                elif unit == 'm': delta = timedelta(minutes=val)
                elif unit == 's': delta = timedelta(seconds=val)
                if delta: return (now - delta).isoformat()
    except Exception: return None
    return None

def _calculate_time_ago_v2(timestamp_val):
    if not timestamp_val: return None, "No TS"
    try:
        ts = _safe_parse_datetime(timestamp_val)
        if not ts: return None, "Parse Fail"
        now = dt_util.now()
        if ts.tzinfo is None: ts = ts.replace(tzinfo=now.tzinfo)
        else: ts = ts.astimezone(now.tzinfo)
        
        seconds = int((now - ts).total_seconds())
        debug = f"Now:{int(now.timestamp())} - TS:{int(ts.timestamp())} = {seconds}s"
        
        if seconds < 0: return ("just now" if seconds > -60 else "in future"), debug
        if seconds < 60: return "just now", debug
        elif seconds < 3600: return f"{seconds // 60}m ago", debug
        elif seconds < 86400: return f"{seconds // 3600}h ago", debug
        else: return f"{seconds // 86400}d ago", debug
    except Exception as e: return None, f"Err: {e}"

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

def safe_image_ext(url, default="png"):
    """Extract a safe file extension from a URL, rejecting anything that isn't a short alnum token."""
    try:
        raw = urlparse(url).path.rsplit(".", 1)[-1]
    except ValueError:
        return default
    return raw.lower() if re.fullmatch(r"[a-z0-9]{1,4}", raw.lower()) else default

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
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except Exception:
        return False

async def check_steam_url_validity(hass, url): return True
async def get_steam_game_cover(hass, game_name, game_id=None): return await get_steamgriddb_game_cover(hass, game_name)

def extract_vibrant_color(image_path):
    """Extracts the most dominant vibrant color from an image, with a safe fallback."""
    try:
        from PIL import Image
        img = Image.open(image_path).convert('RGB')
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
                color = (min(round(r/15)*15, 255), min(round(g/15)*15, 255), min(round(b/15)*15, 255))
                color_counts[color] = color_counts.get(color, 0) + 1
                
        if not color_counts:
            # Fallback: If all pixels were filtered out, calculate the true average
            if total_pixels > 0:
                avg_r = int(fallback_r / total_pixels)
                avg_g = int(fallback_g / total_pixels)
                avg_b = int(fallback_b / total_pixels)
                return f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}"
            return "#333333" # Absolute fallback for completely broken images
            
        dominant_rgb = max(color_counts, key=color_counts.get)
        r, g, b = [min(c, 255) for c in dominant_rgb]
        return f"#{r:02x}{g:02x}{b:02x}"
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to extract vibrant color from %s: %s", image_path, e)
        return None

def get_cached_remote_url(game_name, asset_type="grid"):
    """
    Retrieve the original remote SteamGridDB URL from the cache,
    bypassing the local file path. Useful for cloud webhooks like Discord
    when Home Assistant lacks an external domain.
    """
    if not game_name:
        return None
        
    cache_entry = ASSET_URL_CACHE.get(game_name)
    if not cache_entry:
        return None
        
    url = cache_entry.get(asset_type)
    if url and url_host_matches(url, "steamgriddb.com"):
        return url
        
    return None