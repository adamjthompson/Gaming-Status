"""
Utilities for Gaming Status
"""
import logging
import re
import aiohttp
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from dateutil import parser
from collections import OrderedDict

from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Initialize empty globals (Populated securely by sensor.py during setup)
GAME_TITLE_OVERRIDES = {}
TITLE_CLEANUPS = []
CUSTOM_COVER_MAP = {}
STEAMGRIDDB_API_KEY = None

# Size-capped LRU Cache to prevent unbounded memory growth
COVER_URL_CACHE = OrderedDict()
MAX_CACHE_SIZE = 500
_MISSING_KEY_WARNED = False

async def get_steamgriddb_game_cover(hass, game_name):
    """
    Fetch game cover art (Hero style) from SteamGridDB.
    Priority: Custom Map -> Cache -> Official Art -> Fan Art -> None.
    """
    global _MISSING_KEY_WARNED

    if not game_name:
        return None

    if game_name in CUSTOM_COVER_MAP:
        return CUSTOM_COVER_MAP[game_name]

    if game_name in COVER_URL_CACHE:
        COVER_URL_CACHE.move_to_end(game_name)
        return COVER_URL_CACHE[game_name]

    def _update_cache(name, url):
        COVER_URL_CACHE[name] = url
        COVER_URL_CACHE.move_to_end(name)
        if len(COVER_URL_CACHE) > MAX_CACHE_SIZE:
            COVER_URL_CACHE.popitem(last=False)
        return url

    if not STEAMGRIDDB_API_KEY:
        if not _MISSING_KEY_WARNED:
            _LOGGER.warning("STEAMGRIDDB_API_KEY is missing from profiles.json")
            _MISSING_KEY_WARNED = True
        return None

    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}

    try:
        search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{quote(game_name, safe='')}"
        
        async with session.get(search_url, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.debug(f"SteamGridDB Search failed ({resp.status}) for {game_name}")
                return None
            search_data = await resp.json()

        if not search_data.get("success") or not search_data.get("data"):
            return _update_cache(game_name, None)

        game_id = search_data["data"][0]["id"]
        img_url = f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}?formats=png,webp,jpg"
        
        async with session.get(img_url, headers=headers) as resp:
            if resp.status != 200:
                return None
            img_data = await resp.json()

        if not img_data.get("success") or not img_data.get("data"):
            return _update_cache(game_name, None)

        images = img_data["data"]
        final_image = None
        official = next((img for img in images if img.get("style") == "official"), None)
        
        if official:
            final_image = official["url"] 
        elif images:
            final_image = images[0]["url"]

        # Secure Validation: Ensure API didn't return a malicious payload
        if final_image and final_image.startswith("https://"):
            return _update_cache(game_name, final_image)
        else:
            return _update_cache(game_name, None)

    except Exception as e:
        _LOGGER.error(f"Error fetching SteamGridDB art for {game_name}: {e}")
    
    return None

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
    
    for phrase in TITLE_CLEANUPS:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
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
    """Securely validate URLs to prevent malicious URI injections."""
    if isinstance(url, str) and (url.startswith("https://") or url.startswith("/")):
        return url
    return None

async def check_steam_url_validity(hass, url): return True
async def get_steam_game_cover(hass, game_name, game_id=None): return await get_steamgriddb_game_cover(hass, game_name)