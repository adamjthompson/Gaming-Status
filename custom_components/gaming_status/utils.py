"""
Utilities for Gaming Status
"""
import logging
import re
import os
import time
from urllib.parse import quote
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
TITLE_CLEANUPS = []
COMPILED_TITLE_CLEANUPS = []
STEAMGRIDDB_API_KEY = None

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
        
    # 0. Check Memory Cache BEFORE touching the disk or creating sessions!
    if game_name in ASSET_URL_CACHE:
        ASSET_URL_CACHE.move_to_end(game_name)
        return ASSET_URL_CACHE[game_name]

    # --- THE MEMORY LOCK ---
    # Prevent race conditions by making simultaneous requests wait
    if "gaming_status_locks" not in hass.data:
        hass.data["gaming_status_locks"] = {}
        
    if game_name in hass.data["gaming_status_locks"]:
        # Another sensor is currently downloading this game! Wait for it to finish.
        await hass.data["gaming_status_locks"][game_name].wait()
        # The first downloader should have populated the cache, grab it and return!
        if game_name in ASSET_URL_CACHE:
            ASSET_URL_CACHE.move_to_end(game_name)
            return ASSET_URL_CACHE[game_name]
        return assets
        
    # Lock the game while we download it
    lock = asyncio.Event()
    hass.data["gaming_status_locks"][game_name] = lock

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
        await hass.async_add_executor_job(_ensure_dir)
            
        # 2. Check Custom UI Overrides & Ensure Local Cache
        search_name = game_name.strip().lower()
        override_maps = {
            "grid": CUSTOM_GRID_MAP, "hero": CUSTOM_HERO_MAP,
            "logo": CUSTOM_LOGO_MAP, "icon": CUSTOM_ICON_MAP
        }
        
        safe_file_prefix = re.sub(r'[^a-z0-9]', '_', str(game_name).lower())
        safe_file_prefix = re.sub(r'_+', '_', safe_file_prefix).strip('_')

        for asset_type, map_dict in override_maps.items():
            # Safety net: double-check the keys are lowercase just in case old data exists in the dictionary
            safe_map = {k.lower(): v for k, v in map_dict.items()}
            
            if search_name in safe_map:
                remote_url = safe_url(safe_map[search_name])
                if not remote_url: continue
                
                # If the user provided a raw local path, map it directly without downloading!
                if not remote_url.startswith("http"):
                    assets[asset_type] = remote_url
                    continue
                
                # Determine extension for external HTTP links
                ext = remote_url.split('.')[-1].split('?')[0]
                if len(ext) > 4: ext = "png"
                file_name = f"{safe_file_prefix}_{asset_type}.{ext}"
                file_path = cache_dir / file_name
                
                # ALWAYS download overrides to ensure the user's latest choice overwrites the old SteamGridDB file!
                try:
                    async with session.get(remote_url, timeout=15) as img_resp:
                        if img_resp.status == 200:
                            img_bytes = await img_resp.read()
                            await hass.async_add_executor_job(lambda: file_path.write_bytes(img_bytes))
                except Exception as e:
                    _LOGGER.error("Failed to cache override for %s (%s): %s", game_name, asset_type, e)
                
                assets[asset_type] = f"/local/gaming_status_cache/{file_name}"

        def _update_cache(name, data_dict):
            final_dict = {k: assets[k] or data_dict.get(k) for k in assets}
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
            return _update_cache(game_name, assets)

        if not STEAMGRIDDB_API_KEY:
            if not _MISSING_KEY_WARNED:
                _LOGGER.warning("[Gaming Status] SteamGridDB API key is not configured.")
                _MISSING_KEY_WARNED = True
            return _update_cache(game_name, assets)

        # 4. Fetch from SteamGridDB
        fetched_assets = {"grid": None, "hero": None, "logo": None, "icon": None}
        headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}

        try:
            safe_title = quote(game_name, safe='')
            search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{safe_title}"
            
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return _update_cache(game_name, fetched_assets)
                search_data = await resp.json()
                
            if not search_data.get("data"): return _update_cache(game_name, fetched_assets)
                
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
                            
                            ext = remote_url.split('.')[-1].split('?')[0]
                            if len(ext) > 4: ext = "png"
                            
                            file_name = f"{safe_file_prefix}_{asset_type}.{ext}"
                            file_path = cache_dir / file_name
                            
                            if not file_path.exists():
                                async with session.get(remote_url, timeout=15) as img_resp:
                                    if img_resp.status == 200:
                                        img_bytes = await img_resp.read()
                                        await hass.async_add_executor_job(lambda: file_path.write_bytes(img_bytes))
                                    
                            fetched_assets[asset_type] = f"{base_url}/local/gaming_status_cache/{file_name}"
                                    
        except Exception as e:
            _LOGGER.error("Failed to fetch assets for %s: %s", game_name, e)

        return _update_cache(game_name, fetched_assets)

    finally:
        # ALWAYS release the lock, even if the API throws an unexpected error
        lock.set()
        hass.data["gaming_status_locks"].pop(game_name, None)

async def get_steamgriddb_game_cover(hass, game_name):
    """Backward compatibility wrapper."""
    if not game_name:
        return None
    assets = await fetch_game_assets(hass, game_name)
    return assets.get("hero") or assets.get("grid")

async def fetch_and_cache_image(hass, remote_url, file_name):
    """Generic helper to cache any remote image locally."""
    cache_dir = Path(hass.config.path("www/gaming_status_cache"))
    
    # 1. Safely wrap the mkdir command to avoid kwarg TypeErrors
    def _ensure_dir():
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            
    await hass.async_add_executor_job(_ensure_dir)
    
    file_path = cache_dir / file_name
    
    # 2. Return immediately if already cached
    if file_path.exists():
        return f"/local/gaming_status_cache/{file_name}"
        
    # 3. Download and save
    try:
        session = async_get_clientsession(hass)
        async with session.get(remote_url, timeout=10) as resp:
            if resp.status == 200:
                img_bytes = await resp.read()
                
                # Safely wrap the file writing command
                def _write_img():
                    file_path.write_bytes(img_bytes)
                    
                await hass.async_add_executor_job(_write_img)
                return f"/local/gaming_status_cache/{file_name}"
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
        if platform == "xbox" and object_id.endswith("_status"): return object_id[:-7]
        if platform == "playstation" and object_id.endswith("_online_status"): return object_id[:-14]
    except Exception: pass
    try: return source_entity_id.split('.')[1]
    except Exception: return "unknown"

def _format_time(seconds):
    if not seconds or seconds < 0: return "0m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def _format_game_name_for_display(game_name):
    if not game_name: return game_name
    clean_name = " ".join(str(game_name).split())
    clean_name = GAME_TITLE_OVERRIDES.get(clean_name, clean_name)
    
    if " - " in clean_name: clean_name = clean_name.split(" - ")[0].strip()
    clean_name = re.sub(r'[™®©]', '', clean_name).strip()
    
    for pattern in COMPILED_TITLE_CLEANUPS:
        clean_name = pattern.sub("", clean_name).strip()
        
    clean_name = " ".join(clean_name.split())
    return clean_name

def _normalize_game_name(game_name):
    if not game_name: return ""
    return " ".join(str(game_name).lower().split())

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
        logging.getLogger(__name__).warning(f"Failed to extract vibrant color from {image_path}: {e}")
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
    if url and "steamgriddb.com" in url:
        return url
        
    return None