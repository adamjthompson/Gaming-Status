"""
Utilities for Gaming Status
"""
import logging
import re
import aiohttp
from datetime import datetime, timezone, timedelta
from dateutil import parser

from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Initialize logger FIRST so error handling can use it
_LOGGER = logging.getLogger(__name__)

# Safely pull from profiles.py without crashing if missing
try:
    from . import profiles
    GAME_TITLE_OVERRIDES = getattr(profiles, 'GAME_TITLE_OVERRIDES', {})
    TITLE_CLEANUPS = getattr(profiles, 'TITLE_CLEANUPS', [])
    STEAMGRIDDB_API_KEY = getattr(profiles, 'STEAMGRIDDB_API_KEY', None)
except ImportError:
    _LOGGER.error("Could not import profiles.py into utils.py")
    GAME_TITLE_OVERRIDES = {}
    TITLE_CLEANUPS = []
    STEAMGRIDDB_API_KEY = None

# Cache to prevent hitting the API on every sensor update
COVER_URL_CACHE = {}

async def get_steamgriddb_game_cover(hass, game_name):
    """
    Fetch game cover art (Hero style) from SteamGridDB.
    Priority: Cache -> Official Art -> Fan Art -> None.
    """
    if not game_name:
        return None

    # 1. Check In-Memory Cache
    if game_name in COVER_URL_CACHE:
        return COVER_URL_CACHE[game_name]

    if not STEAMGRIDDB_API_KEY:
        if "missing_key_warned" not in COVER_URL_CACHE:
            _LOGGER.warning("SteamGridDB API Key is missing in profiles.py")
            COVER_URL_CACHE["missing_key_warned"] = True
        return None

    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {STEAMGRIDDB_API_KEY}"}

    try:
        # 2. Search for Game ID
        search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{game_name}"
        async with session.get(search_url, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.debug(f"SteamGridDB Search failed ({resp.status}) for {game_name}")
                return None
            search_data = await resp.json()

        if not search_data.get("success") or not search_data.get("data"):
            COVER_URL_CACHE[game_name] = None # Cache the failure
            return None

        game_id = search_data["data"][0]["id"]

        # 3. Fetch Hero Images
        img_url = f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}?formats=png,webp,jpg"
        
        async with session.get(img_url, headers=headers) as resp:
            if resp.status != 200:
                return None
            img_data = await resp.json()

        if not img_data.get("success") or not img_data.get("data"):
            COVER_URL_CACHE[game_name] = None
            return None

        # 4. Smart Filtering (Official > Any)
        images = img_data["data"]
        final_image = None
        
        official = next((img for img in images if img.get("style") == "official"), None)
        
        if official:
            final_image = official["url"] 
        elif images:
            final_image = images[0]["url"]

        if final_image:
            COVER_URL_CACHE[game_name] = final_image
            return final_image

    except Exception as e:
        _LOGGER.error(f"Error fetching SteamGridDB art for {game_name}: {e}")
    
    return None

# --- HELPERS ---

def _get_gamertag_from_entity(source_entity_id, platform):
    try:
        object_id = source_entity_id.split('.')[1]
        if platform == "steam" and object_id.startswith("steam_"): return object_id[6:]
        if platform == "xbox" and object_id.endswith("_status"): return object_id[:-7]
        if platform == "playstation" and object_id.endswith("_online_status"): return object_id[:-14]
    except Exception: pass
    try:
        return source_entity_id.split('.')[1]
    except Exception:
        return "unknown"

def _format_time(seconds):
    if not seconds or seconds < 0: return "0m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def _format_game_name_for_display(game_name):
    if not game_name: return game_name
    
    # [V160] SQUASH MULTIPLE SPACES
    # " ".join(string.split()) efficiently removes all duplicate spaces, tabs, and newlines
    clean_name = " ".join(str(game_name).split())
    
    clean_name = GAME_TITLE_OVERRIDES.get(clean_name, clean_name)
    
    # 1. Remove Symbols
    if " - " in clean_name: clean_name = clean_name.split(" - ")[0].strip()
    clean_name = re.sub(r'[™®©]', '', clean_name).strip()
    
    # 2. Case-Insensitive Phrase Removal
    for phrase in TITLE_CLEANUPS:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        clean_name = pattern.sub("", clean_name).strip()
        
    # Final squash just in case phrase removal left double spaces
    clean_name = " ".join(clean_name.split())
        
    return clean_name

def _normalize_game_name(game_name):
    if not game_name: return ""
    # [V160] SQUASH MULTIPLE SPACES
    return " ".join(str(game_name).lower().split())

def _safe_parse_datetime(value):
    if not value: return None
    try:
        dt_obj = None
        if isinstance(value, datetime):
            dt_obj = value
        else:
            dt_obj = parser.isoparse(str(value))
        
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=timezone.utc)
        else:
            dt_obj = dt_obj.astimezone(timezone.utc)
            
        return dt_obj
    except Exception:
        return None

def _parse_relative_time_from_status(status_text):
    if not status_text: return None
    if isinstance(status_text, datetime): return None
    
    text = str(status_text).lower()
    if "last seen" not in text and "last online" not in text: return None
    try:
        now = dt_util.now()
        parts = text.split(" ")
        for i, part in enumerate(parts):
            if part.isdigit() and i + 1 < len(parts):
                val = int(part)
                unit = parts[i+1]
                delta = None
                if "m" in unit: delta = timedelta(minutes=val)
                elif "h" in unit: delta = timedelta(hours=val)
                elif "d" in unit: delta = timedelta(days=val)
                elif "s" in unit: delta = timedelta(seconds=val)
                if delta: return (now - delta).isoformat()
            if part[-1] in ['d', 'h', 'm', 's'] and part[:-1].isdigit():
                val = int(part[-1])
                unit = part[-1]
                delta = None
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
        
        diff = (now - ts).total_seconds()
        seconds = int(diff)
        
        debug = f"Now:{int(now.timestamp())} - TS:{int(ts.timestamp())} = {seconds}s"
        
        if seconds < 0:
            if seconds > -60: return "just now", debug
            return "in future", debug
            
        if seconds < 60: 
            return "just now", debug
        elif seconds < 3600: 
            return f"{seconds // 60}m ago", debug
        elif seconds < 86400: 
            return f"{seconds // 3600}h ago", debug
        else: 
            return f"{seconds // 86400}d ago", debug
        
    except Exception as e:
        return None, f"Err: {e}"

# Compatibility wrappers
async def check_steam_url_validity(hass, url):
    return True

async def get_steam_game_cover(hass, game_name, game_id=None):
    return await get_steamgriddb_game_cover(hass, game_name)