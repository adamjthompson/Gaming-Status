"""
Gaming Status Sensor Platform
"""
import logging
import asyncio
import os
import json
import time
from datetime import datetime, timezone, timedelta
from dateutil import parser

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store  # <-- Added Store Helper

_LOGGER = logging.getLogger(__name__)

from .const import (
    DOMAIN, RESET_HISTORY, GRACE_PERIOD_SECONDS, AWAY_GRACE_PERIOD_SECONDS,
    GAME_TRANSITION_GRACE_SECONDS, MIN_SESSION_DURATION, ZOMBIE_ATTRIBUTES,
    PLATFORM_CONFIG, PLATFORM_PRIORITY
)

# Initialize empty globals (Populated safely during async_setup_entry)
GAMER_PROFILES = {}
GLOBAL_EXCLUSIONS = []

from . import utils
from .utils import (
    _get_gamertag_from_entity, _format_time, _format_game_name_for_display,
    _normalize_game_name, _safe_parse_datetime, _parse_relative_time_from_status,
    _calculate_time_ago_v2, get_steamgriddb_game_cover, get_base_game_name
)

# ------------------------------------------------------------------
# 1. CLASS DEFINITIONS
# ------------------------------------------------------------------

class PersistentStatusSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    
    # Instructs the HA Recorder to ignore these specific attributes to prevent DB bloat
    _unrecorded_attributes = frozenset({
        "secondary",
        "daily_play_time_formatted",
        "weekly_play_time_formatted",
        "game_cover_art",
        "cached_game_cover",
        "entity_picture",
        "code_version",
        "source_entity",
        "temp_offline_start",
        "debug_time_ago"
    })

    def __init__(self, hass, source_entity_id, gaming_type, owner_name, ghosted_by=None, exclude_games=None):
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._gaming_type = gaming_type
        self._owner_name = owner_name
        self._ghosted_by = ghosted_by or []
        self._exclude_games = [g.lower() for g in (exclude_games or [])]
        
        self._attr_native_value = "Offline"
        self._attr_extra_state_attributes = {
            "secondary": "Offline", "daily_play_time": 0, "weekly_play_time": 0, "weekly_play_time_last_week": 0
        }
        self._attr_entity_picture = None
        self._previous_state = None
        self._previous_state_online = None
        self._temp_offline_start = None
        self._temp_game_lost_time = None
        self._ha_offline_timestamp = None
        self._last_online_valid_timestamp = None
        self._last_game_stopped_timestamp = None
        self._cached_entity_picture = None
        self._cached_game_cover = None
        self._current_game = None
        self._play_start_time = None
        self._accumulated_play_time = 0
        self._last_played_game = None
        self._last_session_play_time = 0
        self._daily_play_time = 0
        self._weekly_play_time = 0
        self._daily_play_time_yesterday = 0
        self._weekly_play_time_last_week = 0
        self._last_reset_date = None
        self._last_weekly_reset = None
        self._last_update_timestamp = None
        self._play_history = {} 
        self._cover_fetch_attempted = False 
        
        self._away_start_timestamp = None
        self._away_timeout_deducted = False

        self._backup_last_session_time = 0 
        self._backup_last_online_timestamp = None
        self._backup_last_played_game = None
        self._last_state_change_ts = None
        
        config = PLATFORM_CONFIG[gaming_type]
        self._attr_icon = config["icon"]
        safe_owner = self._owner_name.lower().replace(" ", "_")
        
        # Initialize the hidden storage helper for this specific sensor instance
        self._store = Store(hass, 1, f"gaming_status.{safe_owner}_{gaming_type}_history")
        
        self._desired_entity_id = f"sensor.{safe_owner}_{gaming_type}"
        self._attr_unique_id = f"{source_entity_id}_tracker_v5"
        self._attr_name = f"{self._owner_name} {config['name_suffix']}"

    @property
    def native_value(self): return self._attr_native_value
    @property
    def available(self): return True
    @property
    def entity_picture(self):
        if self._cached_entity_picture: return self._cached_entity_picture
        if self._gaming_type == "xbox": return self._attr_extra_state_attributes.get("entity_picture", "/local/gaming_status/default.png")
        return self._attr_entity_picture

    def _check_daily_reset(self):
        now = dt_util.now()
        current_date_str = now.strftime("%Y-%m-%d")
        current_week_str = now.strftime("%Y-%U") 
        
        history_changed = False
        
        if self._last_reset_date != current_date_str:
            if self._last_reset_date:
                self._play_history[self._last_reset_date] = self._daily_play_time
                history_changed = True
            
            cutoff_date = (now - timedelta(days=8)).date()
            keys_to_remove = []
            for date_str in self._play_history:
                try:
                    d = parser.parse(date_str).date()
                    if d < cutoff_date: keys_to_remove.append(date_str)
                except: pass
            for k in keys_to_remove: 
                del self._play_history[k]
                history_changed = True

            self._daily_play_time_yesterday = self._daily_play_time
            self._daily_play_time = 0
            self._last_reset_date = current_date_str
            
        if self._last_weekly_reset != current_week_str:
            self._weekly_play_time_last_week = self._weekly_play_time
            self._weekly_play_time = 0
            self._last_weekly_reset = current_week_str

        # If we modified the internal history dictionary during a reset, safely save it to disk
        if history_changed:
            self._store.async_delay_save(lambda: {"history": self._play_history}, 5.0)

    def _is_steam_echo(self, xbox_game):
        if self._gaming_type != "xbox" or not xbox_game: return False
        steam_entity_id = self._desired_entity_id.replace("_xbox", "_steam")
        steam_state = self.hass.states.get(steam_entity_id)
        if not steam_state: return False
        
        clean_xbox = _format_game_name_for_display(xbox_game)
        norm_xbox = _normalize_game_name(clean_xbox)
        
        if steam_state.state.lower() not in ["offline", "unknown", "unavailable"]:
            steam_game = steam_state.attributes.get("game") or steam_state.state
            if _normalize_game_name(steam_game) == norm_xbox: return True
        last_played = steam_state.attributes.get("last_played_game")
        if last_played and _normalize_game_name(last_played) == norm_xbox:
            last_changed = steam_state.last_changed
            now = dt_util.now()
            if (now - last_changed).total_seconds() < 900: return True
        return False

    def _is_ghost_session(self, game_name):
        if self._gaming_type != "xbox": return False
        if not game_name: return False
        if not self._ghosted_by: return False 
        
        clean_target = _format_game_name_for_display(game_name)
        normalized_target = _normalize_game_name(clean_target)
        
        for ghost_entity_id in self._ghosted_by:
            state = self.hass.states.get(ghost_entity_id)
            if state and state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, "Offline", "offline"]:
                other_game = state.attributes.get("current_game") or state.state
                norm_other = _normalize_game_name(other_game)
                if (norm_other in normalized_target) or (normalized_target in norm_other):
                    return True
        return False

    def _is_game_active_elsewhere(self, current_game):
        if not current_game: return False
        try:
            normalized = _normalize_game_name(current_game)
            if self._gaming_type == "xbox":
                steam_sensor_id = self._desired_entity_id.replace("_xbox", "_steam")
                steam_state = self.hass.states.get(steam_sensor_id)
                if steam_state and steam_state.state not in ["Offline", "offline", STATE_UNKNOWN, STATE_UNAVAILABLE]:
                    return True
        except Exception: pass
        return False

    def _get_platform_data(self, state, attrs):
        data = { 
            "is_online": False, 
            "current_game": None, 
            "game_cover_url": None, 
            "last_online_timestamp": None, 
            "gamertag": None, 
            "avatar_url": None, 
            "game_id": None,
            "offline_reason": "standard" 
        }
        state_clean = str(state).lower().strip()
        if state_clean in ["none", ""]: return None
        normalized_state = state_clean.lower()
        
        # Log the raw state securely instead of exposing it to the UI
        _LOGGER.debug(f"Raw source state for {self.entity_id}: {state_clean}")
        
        # --- CUSTOM SENSOR LOGIC [V161] ---
        if self._gaming_type == "custom":
            if normalized_state in ["0", "off", "offline", "false", "unavailable", "unknown", "0.0", "none", ""]:
                data["is_online"] = False
                data["avatar_url"] = attrs.get("entity_picture")
            elif self._source_entity_id in utils.CUSTOM_COVER_MAP:
                # Legacy behavior: Mapped binary sensor
                if normalized_state in ["1", "on", "playing", "true", "1.0"]:
                    data["is_online"] = True
                    data["current_game"] = utils.CUSTOM_COVER_MAP.get(self._source_entity_id)
                    data["avatar_url"] = attrs.get("entity_picture")
            else:
                # Dynamic behavior: The state IS the game name
                data["is_online"] = True
                data["avatar_url"] = attrs.get("entity_picture")
                if normalized_state in ["1", "on", "playing", "true", "1.0"]:
                    data["current_game"] = "Unknown Custom Game"
                else:
                    data["current_game"] = state 

        is_globally_excluded = False
        global_lower = [x.lower() for x in GLOBAL_EXCLUSIONS]
        
        if normalized_state in global_lower: is_globally_excluded = True
        is_user_excluded = normalized_state in self._exclude_games
        
        data["avatar_url"] = attrs.get("entity_picture")
        is_basic_offline = state_clean in ["offline", "off", "disconnected", "0", "unavailable", "unknown", "0.0"]

        if self._gaming_type == "steam":
            if is_basic_offline: 
                data["is_online"] = False
            elif normalized_state == "snooze":
                data["is_online"] = False
                data["offline_reason"] = "snooze"
            else:
                steam_game = attrs.get("game")
                if steam_game:
                    data["current_game"] = steam_game
                    data["is_online"] = True
                elif normalized_state == "away":
                    data["is_online"] = False
                    data["offline_reason"] = "away"
                else:
                    if is_globally_excluded or is_user_excluded:
                        data["is_online"] = False
                    elif state.lower() == "playing": 
                        data["is_online"] = True 
                    else:
                        data["is_online"] = False
                
                cover = attrs.get("game_image_main") or attrs.get("game_image_header") or attrs.get("header_image")
                if cover: 
                    data["game_cover_url"] = cover
                else:
                    app_id = attrs.get("app_id") or attrs.get("game_id")
                    if app_id:
                        data["game_cover_url"] = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_hero.jpg"
            
            if attrs.get("last_logoff"): data["last_online_timestamp"] = attrs.get("last_logoff")
            elif attrs.get("last_online"): data["last_online_timestamp"] = attrs.get("last_online")

        elif self._gaming_type == "xbox":
            if is_globally_excluded or is_user_excluded:
                data["is_online"] = False
            elif state_clean.startswith("last seen"):
                data["is_online"] = False
                if ": " in state:
                    parts = state.split(": ", 1)
                    if len(parts) > 1:
                        g_name = parts[1]
                        if "(" in g_name and g_name.endswith(")"): g_name = g_name.rsplit(" (", 1)[0]
                        data["xbox_last_seen_game"] = g_name
            elif is_basic_offline: data["is_online"] = False
            else:
                potential_game = state
                if attrs.get("game_queue_games"): potential_game = attrs.get("game_queue_games")[0]
                
                potential_game = get_base_game_name(potential_game)
                
                if self._is_ghost_session(potential_game) or self._is_game_active_elsewhere(potential_game):
                    data["is_online"] = False 
                else:
                    data["is_online"] = True
                    data["current_game"] = potential_game
            data["gamertag"] = _get_gamertag_from_entity(self._source_entity_id, "xbox")

        elif self._gaming_type == "playstation":
            try:
                object_id = self._source_entity_id.split('.')[1]
                if object_id.endswith("_online_status"):
                    gamertag = object_id[:-14]
                    image_state = self.hass.states.get(f"image.{gamertag}_avatar")
                    if image_state and image_state.attributes.get("entity_picture"):
                        data["avatar_url"] = image_state.attributes.get("entity_picture")
            except Exception: pass

            if is_globally_excluded or is_user_excluded:
                data["is_online"] = False
            elif state_clean.startswith("last seen") or state_clean.startswith("last online"):
                data["is_online"] = False
            elif is_basic_offline: data["is_online"] = False
            else:
                if attrs.get("title_name"):
                    data["current_game"] = attrs.get("title_name")
                    data["game_cover_url"] = attrs.get("title_image")
                    data["is_online"] = True
                elif state.lower() == "playing": 
                    data["current_game"] = "Unknown Game" 
                    data["is_online"] = True
                else:
                    found_sibling = False
                    if "_online_status" in self._source_entity_id:
                        sibling_id = self._source_entity_id.replace("_online_status", "_now_playing")
                        sibling_state = self.hass.states.get(sibling_id)
                        if sibling_state and sibling_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown", "unavailable"]:
                            sibling_val = sibling_state.state
                            is_excluded_sib = False
                            for ex in GLOBAL_EXCLUSIONS:
                                if ex.lower() == sibling_val.lower(): is_excluded_sib = True
                            if not is_excluded_sib:
                                data["current_game"] = sibling_val
                                data["is_online"] = True
                                data["game_cover_url"] = sibling_state.attributes.get("entity_picture")
                                found_sibling = True
                    if not found_sibling:
                        data["is_online"] = False

        if data.get("is_online") and data.get("current_game"):
            data["current_game"] = get_base_game_name(data["current_game"])
            cleaned_name = _format_game_name_for_display(data["current_game"])
            cg_lower = str(cleaned_name).lower().strip()
            
            if cg_lower in global_lower or cg_lower in self._exclude_games:
                data["is_online"] = False
                data["current_game"] = None

        return data

    def _handle_game_transition(self, new_game_name, explicit_end_time=None):
        now = dt_util.now()
        actual_end_time = explicit_end_time if explicit_end_time else now

        if self._play_start_time:
            start_dt = _safe_parse_datetime(self._play_start_time)
            if start_dt:
                if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=actual_end_time.tzinfo)
                else: start_dt = start_dt.astimezone(actual_end_time.tzinfo)
                session_seconds = (actual_end_time - start_dt).total_seconds()
                
                if session_seconds <= MIN_SESSION_DURATION:
                    _LOGGER.info(f"Ignoring short session ({session_seconds}s) for {self.entity_id}")
                    self._daily_play_time = max(0, int((self._daily_play_time or 0) - session_seconds))
                    self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - session_seconds))
                    
                    if self._backup_last_session_time and self._backup_last_session_time > 0:
                        self._last_session_play_time = self._backup_last_session_time
                    if self._backup_last_online_timestamp:
                        self._last_online_valid_timestamp = self._backup_last_online_timestamp
                    if self._backup_last_played_game:
                        self._last_played_game = self._backup_last_played_game
                        
                elif session_seconds > 0:
                    self._accumulated_play_time = (self._accumulated_play_time or 0) + session_seconds
                    self._last_session_play_time = int(session_seconds)

        if not new_game_name:
            self._last_game_stopped_timestamp = actual_end_time.isoformat()
            self._current_game = None
            self._play_start_time = None
        else:
            self._current_game = new_game_name
            self._play_start_time = now.isoformat()
            self._accumulated_play_time = 0
            
            prev_game = self._last_played_game
            prev_stop = self._last_game_stopped_timestamp
            can_resurrect = False
            
            if prev_game and prev_stop:
                norm_new = _normalize_game_name(new_game_name)
                norm_prev = _normalize_game_name(prev_game)
                
                if norm_new == norm_prev:
                    stop_dt = _safe_parse_datetime(prev_stop)
                    if stop_dt:
                        diff = (now.timestamp() - stop_dt.timestamp())
                        if diff < 300: 
                            can_resurrect = True
            
            if can_resurrect:
                _LOGGER.info(f"[V123] Resurrecting zombie session for {new_game_name}. Gap was small.")
                if self._last_session_play_time > 0:
                    resumed_start = now - timedelta(seconds=self._last_session_play_time)
                    self._play_start_time = resumed_start.isoformat()
            
            self._backup_last_session_time = self._last_session_play_time
            self._backup_last_online_timestamp = self._last_online_valid_timestamp
            self._backup_last_played_game = self._last_played_game
            self._last_session_play_time = 0
            self._cover_fetch_attempted = False

    def _get_play_time_display(self):
        if not self._play_start_time: return None
        start_dt = _safe_parse_datetime(self._play_start_time)
        if not start_dt: return None
        
        now = dt_util.now()
        if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=now.tzinfo)
        else: start_dt = start_dt.astimezone(now.tzinfo)
        seconds = (now - start_dt).total_seconds()
        if seconds < 60: return "just now"
        minutes = int(seconds // 60)
        hours = int(minutes // 60)
        if hours > 0: return f"{hours}h {minutes % 60}m"
        return f"{minutes}m"

    def _get_total_session_seconds(self):
        if not self._play_start_time: return 0
        start_dt = _safe_parse_datetime(self._play_start_time)
        if not start_dt: return 0
        
        now = dt_util.now()
        if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=now.tzinfo)
        else: start_dt = start_dt.astimezone(now.tzinfo)
        return (now - start_dt).total_seconds()

    def _get_dynamic_game_cover_art(self):
        if self._attr_extra_state_attributes: return self._attr_extra_state_attributes.get("game_cover_art")
        return None

    def _clean_restored_game_name(self, game_name):
        if not game_name: return None
        clean = str(game_name).strip()
        if "last seen" in clean.lower():
            if ": " in clean:
                parts = clean.split(": ")
                clean = parts[-1].strip()
                if "(" in clean and clean.endswith(")"): clean = clean.rsplit(" (", 1)[0].strip()
        
        clean = utils.GAME_TITLE_OVERRIDES.get(clean, clean)
        return clean

    async def _async_fetch_missing_cover(self):
        if not self._last_played_game: return
        normalized = _format_game_name_for_display(self._last_played_game)
        if normalized:
            fetched = await get_steamgriddb_game_cover(self.hass, normalized)
            if fetched:
                self._cached_game_cover = fetched
                self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        
        # --- LOAD SECURE HISTORY ---
        # Read the private dict from the hard drive, protecting users' exact timestamps
        stored_data = await self._store.async_load()
        if stored_data and "history" in stored_data:
            self._play_history = stored_data["history"]
        else:
            self._play_history = {} 

        last_state = await self.async_get_last_state()
        
        if RESET_HISTORY:
            _LOGGER.warning(f"V70 NUCLEAR RESET: Discarding saved history for {self.entity_id} to fix corruption.")
            self._attr_native_value = "Offline"
            self._attr_extra_state_attributes = {}
        
        elif last_state:
            self._attr_native_value = last_state.state
            attrs = last_state.attributes
            self._last_online_valid_timestamp = attrs.get("last_online_valid_timestamp")
            self._last_game_stopped_timestamp = attrs.get("last_game_stopped_timestamp")
            self._last_state_change_ts = last_state.last_changed
            
            self._temp_offline_start = _safe_parse_datetime(attrs.get("temp_offline_start"))
            
            restored_last_game = attrs.get("last_played_game")
            self._last_played_game = self._clean_restored_game_name(restored_last_game)
            self._cached_entity_picture = attrs.get("cached_entity_picture")
            self._cached_game_cover = attrs.get("cached_game_cover")
            self._last_session_play_time = int(attrs.get("last_session_play_time") or 0)
            try:
                self._daily_play_time = int(float(attrs.get("daily_play_time") or 0))
                self._weekly_play_time = int(float(attrs.get("weekly_play_time") or 0))
                self._daily_play_time_yesterday = int(float(attrs.get("daily_play_time_yesterday") or 0))
                self._weekly_play_time_last_week = int(float(attrs.get("weekly_play_time_last_week") or 0))
                self._accumulated_play_time = int(float(attrs.get("accumulated_play_time") or 0))
                # Note: self._play_history is no longer loaded from public attrs to prevent memory override!
            except Exception:
                self._daily_play_time = 0
                self._weekly_play_time = 0
            self._last_reset_date = attrs.get("last_reset_date")
            self._last_weekly_reset = attrs.get("last_weekly_reset")
            if attrs.get("current_game") and attrs.get("play_start_time"):
                self._current_game = self._clean_restored_game_name(attrs.get("current_game"))
                self._play_start_time = attrs.get("play_start_time")
            if self._attr_native_value and self._attr_native_value.lower() != "offline":
                if "last seen" in self._attr_native_value.lower():
                    self._attr_native_value = "Offline"
                    self._current_game = None
                    self._play_start_time = None
                else: self._previous_state_online = True
            self._attr_extra_state_attributes = dict(attrs)
            self._attr_entity_picture = attrs.get("entity_picture")
            if self._last_played_game and str(self._last_played_game).lower() == "offline": self._last_played_game = None
            if self._current_game and str(self._current_game).lower() == "offline":
                self._current_game = None
                self._play_start_time = None
                self._attr_native_value = "Offline"
            
            if self._last_game_stopped_timestamp:
                self._last_online_valid_timestamp = self._last_game_stopped_timestamp
        
        for zombie in ZOMBIE_ATTRIBUTES:
            if zombie in self._attr_extra_state_attributes:
                del self._attr_extra_state_attributes[zombie]

        # Secure Cleanup: Purge legacy debug attributes from history database
        for legacy_debug in ["debug_raw_source_state", "debug_time_ago", "debug_sync", "source_entity", "play_history"]:
            if legacy_debug in self._attr_extra_state_attributes:
                del self._attr_extra_state_attributes[legacy_debug]

        source_state = self.hass.states.get(self._source_entity_id)
        if source_state:
            await self._try_force_sync(source_state)
        
        entities_to_watch = [self._source_entity_id]
        if self._gaming_type == "playstation":
            try:
                if "_online_status" in self._source_entity_id:
                    sibling_id = self._source_entity_id.replace("_online_status", "_now_playing")
                    entities_to_watch.append(sibling_id)
                object_id = self._source_entity_id.split('.')[1]
                if object_id.endswith("_online_status"):
                    gamertag = object_id[:-14]
                    image_entity = f"image.{gamertag}_avatar"
                    entities_to_watch.append(image_entity)
            except Exception: pass

        self.async_on_remove(async_track_state_change_event(self.hass, entities_to_watch, self._async_state_changed))
        self.async_on_remove(async_track_time_interval(self.hass, self._update_play_time, timedelta(seconds=30)))
        
        await self._trigger_source_update(force=True)

    async def _trigger_source_update(self, force=False):
        source = self.hass.states.get(self._source_entity_id)
        if source: await self._unified_update(source.state, source.attributes, force_update=force)
        else: _LOGGER.debug(f"Source entity {self._source_entity_id} unavailable (None).")

    async def _try_force_sync(self, source_state):
        if self._last_online_valid_timestamp:
            _LOGGER.debug(f"Sync SKIPPED for {self.entity_id}: Trusting Local History")
            return

        s_ts = None
        debug_msg = "No Source TS"
        if self._gaming_type == "steam":
            s_ts = source_state.attributes.get("last_logoff") or source_state.attributes.get("last_online")
        elif self._gaming_type == "xbox":
            s_ts = source_state.attributes.get("last_seen") or source_state.attributes.get("timestamp")
            if not s_ts: s_ts = _parse_relative_time_from_status(source_state.state)
        
        if s_ts:
            try:
                s_dt = _safe_parse_datetime(s_ts)
                if not s_dt: raise ValueError("Failed to parse")
                self._last_online_valid_timestamp = s_dt.isoformat()
                self._last_game_stopped_timestamp = s_dt.isoformat()
                debug_msg = f"INIT: Imported from Source ({s_dt})"
            except Exception as e:
                debug_msg = f"ERROR: {e}"
        
        if not self._attr_extra_state_attributes: self._attr_extra_state_attributes = {}
        _LOGGER.debug(f"Sync result for {self.entity_id}: {debug_msg}")

    @callback
    def _async_state_changed(self, event): self.hass.async_create_task(self._trigger_source_update())

    @callback
    def _update_play_time(self, now=None):
        try:
            self._check_daily_reset()
            if self._last_played_game and str(self._last_played_game).lower() == "offline": self._last_played_game = None
            now_dt = dt_util.now()
            delta_seconds = 30
            if getattr(self, '_last_update_timestamp', None):
                try:
                    last = _safe_parse_datetime(self._last_update_timestamp)
                    if last:
                        delta = (now_dt - last).total_seconds()
                        if 0 < delta < 120: delta_seconds = int(delta)
                        else: delta_seconds = 30
                except Exception: pass

            if not self._attr_extra_state_attributes: self._attr_extra_state_attributes = {}
            if self._temp_offline_start:
                time_in_limbo = (now_dt - self._temp_offline_start).total_seconds()
                if time_in_limbo > GRACE_PERIOD_SECONDS:
                    self.hass.async_create_task(self._trigger_source_update())

            if self._attr_native_value.lower() != "offline" and not self._current_game:
                self._current_game = self._attr_native_value
                if not self._play_start_time: 
                    if self._last_state_change_ts:
                        self._play_start_time = self._last_state_change_ts.isoformat()
                    else:
                        self._play_start_time = now_dt.isoformat()

            if self._current_game and not self._play_start_time:
                self._play_start_time = now_dt.isoformat()
                self._accumulated_play_time = 0

            timer_status = "Inactive"

            if self._play_start_time and self._current_game:
                is_blocked = False
                block_reason = ""
                
                if self._is_game_active_elsewhere(self._current_game):
                    is_blocked = True
                    block_reason = "Active Elsewhere"
                elif self._temp_offline_start is not None:
                    is_blocked = True
                    block_reason = "Grace Period"
                
                if not is_blocked:
                    self._last_online_valid_timestamp = now_dt.isoformat()
                    self._daily_play_time = int((self._daily_play_time or 0) + delta_seconds)
                    self._weekly_play_time = int((self._weekly_play_time or 0) + delta_seconds)
                    timer_status = "Running"
                else:
                    timer_status = f"Paused ({block_reason})"
                
                play_time_text = self._get_play_time_display()
                if play_time_text: self._attr_extra_state_attributes["secondary"] = f"({play_time_text})"
                else: self._attr_extra_state_attributes["secondary"] = "Playing now"
                
                if self._get_total_session_seconds() > MIN_SESSION_DURATION and not is_blocked: 
                    self._last_played_game = self._current_game
            else:
                timer_status = "Stopped (Offline)"
                if self._attr_native_value.lower() != "offline": self._attr_native_value = "Offline"
                
                if not self._last_online_valid_timestamp and self._last_game_stopped_timestamp:
                    self._last_online_valid_timestamp = self._last_game_stopped_timestamp

                if self._last_online_valid_timestamp:
                    time_ago, debug_info = _calculate_time_ago_v2(self._last_online_valid_timestamp)
                    _LOGGER.debug(f"Time ago calculation for {self.entity_id}: {debug_info}")
                    if time_ago:
                        if self._last_played_game:
                            secondary = f"Last seen {time_ago}: {self._last_played_game}"
                            if self._last_session_play_time and self._last_session_play_time >= 60:
                                st_str = _format_time(self._last_session_play_time)
                                secondary = f"{secondary} ({st_str})"
                    else: secondary = f"Last seen {time_ago}"
                else: secondary = "Offline"

            self._attr_extra_state_attributes["timer_status"] = timer_status
            self._attr_extra_state_attributes["daily_play_time"] = self._daily_play_time
            self._attr_extra_state_attributes["daily_play_time_formatted"] = _format_time(self._daily_play_time)
            self._attr_extra_state_attributes["daily_play_time_yesterday"] = self._daily_play_time_yesterday
            self._attr_extra_state_attributes["weekly_play_time"] = self._weekly_play_time
            self._attr_extra_state_attributes["weekly_play_time_formatted"] = _format_time(self._weekly_play_time)
            self._attr_extra_state_attributes["weekly_play_time_last_week"] = self._weekly_play_time_last_week
            self._attr_extra_state_attributes["last_reset_date"] = self._last_reset_date
            self._attr_extra_state_attributes["last_weekly_reset"] = self._last_weekly_reset
            self._attr_extra_state_attributes["code_version"] = "v161"
            
            if self._last_online_valid_timestamp:
                if isinstance(self._last_online_valid_timestamp, datetime):
                    ts_str = self._last_online_valid_timestamp.isoformat()
                else:
                    ts_str = str(self._last_online_valid_timestamp)
                self._attr_extra_state_attributes["last_online_valid_timestamp"] = ts_str
            self._last_update_timestamp = now_dt.isoformat()
            
            # --- PRIVACY FIX ---
            # self._attr_extra_state_attributes["play_history"] = self._play_history 
            # -------------------
            
            history_seconds = sum(self._play_history.values())
            total_rolling = history_seconds + self._daily_play_time
            self._attr_extra_state_attributes["rolling_weekly_hours"] = round(total_rolling / 3600, 2)

            self._attr_extra_state_attributes["last_played_game"] = self._last_played_game
            self._attr_extra_state_attributes["last_session_play_time"] = self._last_session_play_time
            self._attr_extra_state_attributes["play_start_time"] = self._play_start_time
            
            if self._temp_offline_start:
                self._attr_extra_state_attributes["temp_offline_start"] = self._temp_offline_start.isoformat()
            else:
                self._attr_extra_state_attributes["temp_offline_start"] = None
            
            if 'secondary' in locals():
                self._attr_extra_state_attributes["secondary"] = secondary
            
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("Error in _unified_update for %s: %s", self.entity_id, e)


    async def _unified_update(self, current_state, attrs, force_update=False):
        try:
            platform_data = self._get_platform_data(current_state, attrs)
            if not platform_data: return
            self._check_daily_reset()
            now_dt = dt_util.now()
            
            try:
                if self._gaming_type == "steam" and platform_data.get("is_online") and str(current_state).lower() == "away":
                    if self._away_start_timestamp is None:
                        self._away_start_timestamp = now_dt
                        self._away_timeout_deducted = False 
                    
                    time_away = (now_dt - self._away_start_timestamp).total_seconds()
                    
                    if time_away > 1800:
                        platform_data["is_online"] = False
                        platform_data["offline_reason"] = "away_timeout"
                        
                        if not self._away_timeout_deducted:
                            current_daily = int(self._daily_play_time or 0)
                            current_weekly = int(self._weekly_play_time or 0)
                            self._daily_play_time = max(0, current_daily - 1800)
                            self._weekly_play_time = max(0, current_weekly - 1800)
                            self._away_timeout_deducted = True 
                else:
                    self._away_start_timestamp = None
                    self._away_timeout_deducted = False
            except Exception as e:
                _LOGGER.error(f"[V161 SAFETY] Error in Away Timeout Logic for {self.entity_id}: {e}")

            is_offline_now = not platform_data.get("is_online")
            
            if force_update: pass
            
            in_grace_period = False
            current_grace_limit = GRACE_PERIOD_SECONDS
            if platform_data.get("offline_reason") == "away":
                current_grace_limit = AWAY_GRACE_PERIOD_SECONDS

            if self._current_game and is_offline_now:
                if self._temp_offline_start is None: 
                    can_start_grace = True
                    if self._last_online_valid_timestamp:
                        last_ts = _safe_parse_datetime(self._last_online_valid_timestamp)
                        if last_ts:
                            time_since_valid = (now_dt - last_ts).total_seconds()
                            if time_since_valid > current_grace_limit:
                                can_start_grace = False
                    if can_start_grace:
                        self._temp_offline_start = now_dt
                if self._temp_offline_start:
                    offline_duration = (now_dt - self._temp_offline_start).total_seconds()
                    if offline_duration <= current_grace_limit: in_grace_period = True
                
            elif not is_offline_now: 
                if self._temp_offline_start:
                    missed_seconds = (now_dt - self._temp_offline_start).total_seconds()
                    if missed_seconds > 0:
                        self._daily_play_time = int((self._daily_play_time or 0) + missed_seconds)
                        self._weekly_play_time = int((self._weekly_play_time or 0) + missed_seconds)
                self._temp_offline_start = None

            display_state = "Offline"
            game_cover = None
            secondary = ""
            
            if platform_data.get("current_game"):
                raw_game_name = platform_data["current_game"]
                raw_game_name = utils.GAME_TITLE_OVERRIDES.get(raw_game_name, raw_game_name)
                game_name_display = _format_game_name_for_display(raw_game_name)
                normalized_new = _normalize_game_name(game_name_display)
                normalized_current = _normalize_game_name(self._current_game) if self._current_game else None
                
                if normalized_new == normalized_current and self._play_start_time:
                    last_online_str = platform_data.get("last_online_timestamp")
                    if last_online_str:
                        last_online_dt = _safe_parse_datetime(last_online_str)
                        start_time_dt = _safe_parse_datetime(self._play_start_time)
                        if last_online_dt and start_time_dt:
                            if last_online_dt > (start_time_dt + timedelta(minutes=15)):
                                self._handle_game_transition(game_name_display)
                                self._current_game = game_name_display
                                self._play_start_time = now_dt.isoformat()

                if normalized_new != normalized_current:
                    if self._temp_game_lost_time and self._current_game:
                        time_since_lost = (now_dt - self._temp_game_lost_time).total_seconds()
                        if _normalize_game_name(self._current_game) == normalized_new and time_since_lost <= GAME_TRANSITION_GRACE_SECONDS:
                            self._current_game = game_name_display
                        else: self._handle_game_transition(game_name_display)
                    else: self._handle_game_transition(game_name_display)
                    self._temp_game_lost_time = None
                else:
                    self._current_game = game_name_display
                    self._temp_game_lost_time = None
                
                display_state = game_name_display
                
                if normalized_new:
                    fetched = await get_steamgriddb_game_cover(self.hass, game_name_display)
                    if fetched:
                        platform_data["game_cover_url"] = fetched
                
                game_cover = platform_data.get("game_cover_url")
                if game_cover: self._cached_game_cover = game_cover
                
                if self._get_total_session_seconds() > MIN_SESSION_DURATION:
                    if not self._is_game_active_elsewhere(game_name_display) and not self._is_ghost_session(game_name_display):
                        self._last_played_game = game_name_display
                
                play_time = self._get_play_time_display()
                secondary = f"({play_time})" if play_time else "Playing now"

            elif self._current_game and in_grace_period:
                display_state = self._current_game
                play_time = self._get_play_time_display()
                secondary = f"({play_time})" if play_time else "Playing now"
                game_cover = platform_data.get("game_cover_url") or self._cached_game_cover
                if not self._temp_game_lost_time: self._temp_game_lost_time = now_dt

            else:
                display_state = "Offline"
                game_cover = None
                if self._current_game:
                    if self._temp_offline_start:
                        limit_to_check = GRACE_PERIOD_SECONDS
                        if platform_data.get("offline_reason") == "away":
                            limit_to_check = AWAY_GRACE_PERIOD_SECONDS
                        if (now_dt - self._temp_offline_start).total_seconds() > limit_to_check:
                            self._handle_game_transition(None, explicit_end_time=self._temp_offline_start)
                            self._temp_offline_start = None 
                            self._temp_game_lost_time = None
                    elif self._temp_game_lost_time:
                        if (now_dt - self._temp_game_lost_time).total_seconds() > GAME_TRANSITION_GRACE_SECONDS:
                            self._handle_game_transition(None)
                            self._temp_game_lost_time = None
                    else: self._handle_game_transition(None)
                
                self._temp_offline_start = None

                if is_offline_now:
                    if self._gaming_type == "xbox" and platform_data.get("xbox_last_seen_game"):
                        new_xbox_game = _format_game_name_for_display(
                            self._clean_restored_game_name(platform_data["xbox_last_seen_game"])
                        )
                        idle_list = [s.lower() for s in PLATFORM_CONFIG["xbox"]["idle_states"]]
                        is_ghost = self._is_ghost_session(new_xbox_game)
                        if new_xbox_game.lower() not in idle_list and not is_ghost:
                            self._last_played_game = new_xbox_game

                time_ago, debug_info = _calculate_time_ago_v2(self._last_online_valid_timestamp)
                _LOGGER.debug(f"Time ago calculation for {self.entity_id}: {debug_info}")
                
                if time_ago:
                    if self._last_played_game:
                        secondary = f"Last seen {time_ago}: {self._last_played_game}"
                        if self._last_session_play_time and self._last_session_play_time >= 60:
                            st_str = _format_time(self._last_session_play_time)
                            secondary = f"{secondary} ({st_str})"
                    else: secondary = f"Last seen {time_ago}"
                else: secondary = "Offline"

            self._attr_native_value = display_state
            
            if display_state == "Offline":
                self._current_game = None
                self._play_start_time = None
            
            if self._gaming_type in ["xbox", "playstation", "custom"]:
                entity_pic = None
                safe_name = self._owner_name.lower().replace(" ", "_")
                for ext in ['png', 'jpg']:
                    local_filename = f"{self._gaming_type}_{safe_name}_avatar.{ext}"
                    local_path = self.hass.config.path(f"www/gaming_status/{local_filename}")
                    if os.path.exists(local_path): 
                        entity_pic = f"/local/gaming_status/{local_filename}"
                        break
                if not entity_pic:
                    if platform_data.get("avatar_url"): entity_pic = platform_data.get("avatar_url")
                
                self._attr_extra_state_attributes["entity_picture"] = entity_pic
                self._attr_entity_picture = entity_pic
            else:
                if platform_data.get("avatar_url"): self._attr_entity_picture = platform_data.get("avatar_url")
            
            self._attr_extra_state_attributes["current_game"] = self._current_game
            self._attr_extra_state_attributes["game_cover_art"] = game_cover
            self._attr_extra_state_attributes["cached_game_cover"] = self._cached_game_cover
            self._attr_extra_state_attributes["secondary"] = secondary
            self._attr_extra_state_attributes["daily_play_time"] = self._daily_play_time
            self._attr_extra_state_attributes["daily_play_time_formatted"] = _format_time(self._daily_play_time)
            self._attr_extra_state_attributes["daily_play_time_yesterday"] = self._daily_play_time_yesterday
            self._attr_extra_state_attributes["weekly_play_time"] = self._weekly_play_time
            self._attr_extra_state_attributes["weekly_play_time_formatted"] = _format_time(self._weekly_play_time)
            self._attr_extra_state_attributes["weekly_play_time_last_week"] = self._weekly_play_time_last_week
            self._attr_extra_state_attributes["last_reset_date"] = self._last_reset_date
            self._attr_extra_state_attributes["last_weekly_reset"] = self._last_weekly_reset
            self._attr_extra_state_attributes["code_version"] = "v161"
            
            if self._last_online_valid_timestamp:
                if isinstance(self._last_online_valid_timestamp, datetime):
                    ts_str = self._last_online_valid_timestamp.isoformat()
                else:
                    ts_str = str(self._last_online_valid_timestamp)
                self._attr_extra_state_attributes["last_online_valid_timestamp"] = ts_str
            self._last_update_timestamp = now_dt.isoformat()
            
            # --- PRIVACY FIX ---
            # self._attr_extra_state_attributes["play_history"] = self._play_history 
            # -------------------
            
            history_seconds = sum(self._play_history.values())
            total_rolling = history_seconds + self._daily_play_time
            self._attr_extra_state_attributes["rolling_weekly_hours"] = round(total_rolling / 3600, 2)

            self._attr_extra_state_attributes["last_played_game"] = self._last_played_game
            self._attr_extra_state_attributes["last_session_play_time"] = self._last_session_play_time
            self._attr_extra_state_attributes["play_start_time"] = self._play_start_time
            
            if self._temp_offline_start:
                self._attr_extra_state_attributes["temp_offline_start"] = self._temp_offline_start.isoformat()
            else:
                self._attr_extra_state_attributes["temp_offline_start"] = None
            
            if 'secondary' in locals():
                self._attr_extra_state_attributes["secondary"] = secondary
            
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("Error in _unified_update for %s: %s", self.entity_id, e)

# ------------------------------------------------------------------
# 2. ADDITIONAL CLASSES
# ------------------------------------------------------------------

class MasterGamingSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    
    _unrecorded_attributes = frozenset({
        "secondary",
        "game_cover_art",
        "entity_picture"
    })
    
    def __init__(self, hass, name, profiles):
        self.hass = hass
        self._profiles = profiles
        safe_owner = name.lower().replace(" ", "_")
        self._attr_name = f"{name} Gaming Status"
        self._attr_unique_id = f"{safe_owner}_master_v5"
        self.entity_id = f"sensor.{safe_owner}_gaming_status"
        self._attr_native_value = "Offline"
        self._attr_icon = "mdi:controller"
        self._attr_entity_picture = None
        self._attr_extra_state_attributes = {}
        
        self._platform_sensors = []
        for platform in PLATFORM_PRIORITY:
            if profiles.get(platform):
                self._platform_sensors.append(f"sensor.{safe_owner}_{platform}")
    
    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._attr_native_value = last_state.state
            self._attr_extra_state_attributes = dict(last_state.attributes)
            self._attr_entity_picture = last_state.attributes.get("entity_picture")
            lp = self._attr_extra_state_attributes.get("last_played_game")
            if lp and str(lp).lower() == "offline": self._attr_extra_state_attributes["last_played_game"] = None
            if self._attr_native_value and self._attr_native_value.lower() == "offline": self._attr_native_value = "Offline"

        if self._platform_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, self._platform_sensors, self._async_platform_changed)
            )
        
        await asyncio.sleep(1)
        await self._update_master_state()
    
    @callback
    def _async_platform_changed(self, event):
        self.hass.async_create_task(self._update_master_state())
    
    async def _update_master_state(self):
        active_sensor_id = None
        active_state = None
        total_daily_seconds = 0
        total_weekly_seconds = 0
        total_rolling_weekly_hours = 0.0 
        total_weekly_seconds_last_week = 0
        
        for platform_sensor_id in self._platform_sensors:
            platform_state = self.hass.states.get(platform_sensor_id)
            if not platform_state: continue
            
            d_time = platform_state.attributes.get("daily_play_time")
            w_time = platform_state.attributes.get("weekly_play_time")
            r_time = platform_state.attributes.get("rolling_weekly_hours")
            wl_time = platform_state.attributes.get("weekly_play_time_last_week") 
            
            if d_time: total_daily_seconds += int(d_time)
            if w_time: total_weekly_seconds += int(w_time)
            if r_time: total_rolling_weekly_hours += float(r_time)
            if wl_time: total_weekly_seconds_last_week += int(wl_time)

            if active_state: continue 
            state_value = platform_state.state
            if state_value.lower() not in ["offline", "source missing", "unavailable", "unknown"]:
                active_sensor_id = platform_sensor_id
                active_state = platform_state
        
        total_daily_hours = round(total_daily_seconds / 3600, 2)
        total_weekly_hours = round(total_weekly_seconds / 3600, 2)
        total_rolling_weekly_hours = round(total_rolling_weekly_hours, 2)
        total_weekly_hours_last_week = round(total_weekly_seconds_last_week / 3600, 2)

        if active_state:
            self._attr_native_value = active_state.state
            self._attr_entity_picture = active_state.attributes.get("entity_picture")
            platform_key = active_sensor_id.split("_")[-1]
            pretty_platform_name = PLATFORM_CONFIG.get(platform_key, {}).get("name_suffix", platform_key.title())
            self._attr_extra_state_attributes = {
                "secondary": active_state.attributes.get("secondary", ""),
                "active_platform": pretty_platform_name, 
                "game_cover_art": active_state.attributes.get("game_cover_art"),
                "current_game": active_state.attributes.get("current_game"),
                "play_start_time": active_state.attributes.get("play_start_time"),
                "last_online_valid_timestamp": datetime.now(timezone.utc).isoformat(),
                "total_daily_hours": total_daily_hours,
                "total_weekly_hours": total_weekly_hours,
                "rolling_weekly_hours": total_rolling_weekly_hours, 
                "total_weekly_hours_last_week": total_weekly_hours_last_week,
                "last_played_game": active_state.attributes.get("last_played_game"),
            }
            if platform_key in PLATFORM_CONFIG: self._attr_icon = PLATFORM_CONFIG[platform_key]["icon"]
        else:
            self._attr_native_value = "Offline"
            self._attr_icon = "mdi:controller"
            most_recent_ts = None
            most_recent_sensor = None
            most_recent_key = None
            for sensor_id in self._platform_sensors:
                state_obj = self.hass.states.get(sensor_id)
                if state_obj and state_obj.attributes.get("last_online_valid_timestamp"):
                    ts_str = state_obj.attributes.get("last_online_valid_timestamp")
                    try:
                        ts = parser.isoparse(ts_str)
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                        if most_recent_ts is None or ts > most_recent_ts:
                            most_recent_ts = ts
                            most_recent_sensor = state_obj
                            most_recent_key = sensor_id.split("_")[-1]
                    except Exception: pass
            if most_recent_sensor:
                pretty_name = PLATFORM_CONFIG.get(most_recent_key, {}).get("name_suffix", "Gaming")
                self._attr_extra_state_attributes = {
                    "secondary": most_recent_sensor.attributes.get("secondary", "Offline"),
                    "active_platform": pretty_name,
                    "game_cover_art": most_recent_sensor.attributes.get("game_cover_art"),
                    "last_played_game": most_recent_sensor.attributes.get("last_played_game"),
                    "last_online_valid_timestamp": most_recent_sensor.attributes.get("last_online_valid_timestamp"),
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week
                }
                lp = self._attr_extra_state_attributes.get("last_played_game")
                if lp and str(lp).lower() == "offline": self._attr_extra_state_attributes["last_played_game"] = None
                if most_recent_sensor.attributes.get("entity_picture"):
                    self._attr_entity_picture = most_recent_sensor.attributes.get("entity_picture")
                if most_recent_key in PLATFORM_CONFIG:
                    self._attr_icon = PLATFORM_CONFIG[most_recent_key]["icon"]
            else:
                self._attr_extra_state_attributes = {
                    "secondary": "Offline",
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week
                }
                self._attr_entity_picture = None
        self.async_write_ha_state()

class HistoryChartSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"
    def __init__(self, hass, name):
        safe_owner = name.lower().replace(" ", "_")
        self._attr_name = f"{name} Chart"
        self._attr_unique_id = f"{safe_owner}_chart_v161"
        self.entity_id = f"sensor.{safe_owner}_daily_gaming_hours_chart"
        self._attr_native_value = 0.0
    async def async_added_to_hass(self):
        last_state = await self.async_get_last_state()
        if last_state:
            try: self._attr_native_value = float(last_state.state)
            except ValueError: self._attr_native_value = 0.0

# ------------------------------------------------------------------
# 3. SETUP ENTRY (Defined LAST)
# ------------------------------------------------------------------

from .const import CONF_STEAMGRIDDB_API_KEY

async def async_setup_entry(hass, config_entry, async_add_entities):
    _LOGGER.info("[Gaming Status] Starting Setup...")
    
    # 1. Safely load gaming_profiles.json with robust error handling
    def load_json_config():
        config_path = hass.config.path("gaming_profiles.json")
        
        if not os.path.exists(config_path):
            _LOGGER.error("[Gaming Status] CRITICAL: 'gaming_profiles.json' was not found in your /config folder. Please create it to track players.")
            return {}
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            _LOGGER.error(f"[Gaming Status] SYNTAX ERROR: Your gaming_profiles.json file has a typo at Line {e.lineno}, Column {e.colno}. ({e.msg})")
            return {}
        except Exception as e:
            _LOGGER.error(f"[Gaming Status] FATAL: Could not read gaming_profiles.json. Error: {e}")
            return {}

    profiles_data = await hass.async_add_executor_job(load_json_config)

    # 2. GRACEFUL MIGRATION: Move API Key from JSON to UI Database
    api_key = config_entry.data.get(CONF_STEAMGRIDDB_API_KEY)
    
    # If the UI database doesn't have a key, but the JSON file does:
    if not api_key and profiles_data.get('STEAMGRIDDB_API_KEY'):
        api_key = profiles_data.get('STEAMGRIDDB_API_KEY')
        _LOGGER.warning("[Gaming Status] Migrating API Key from JSON to secure UI storage.")
        
        # Save it to the secure config entry
        new_data = {**config_entry.data, CONF_STEAMGRIDDB_API_KEY: api_key}
        hass.config_entries.async_update_entry(config_entry, data=new_data)
        
        # (Optional) You can leave the key in the JSON, it will just be ignored from now on.

    # 3. Populate globals securely
    global GAMER_PROFILES, GLOBAL_EXCLUSIONS
    GAMER_PROFILES = profiles_data.get('GAMER_PROFILES', {})
    GLOBAL_EXCLUSIONS = profiles_data.get('GLOBAL_EXCLUSIONS', [])
    
    utils.GAME_TITLE_OVERRIDES = profiles_data.get('GAME_TITLE_OVERRIDES', {})
    utils.TITLE_CLEANUPS = profiles_data.get('TITLE_CLEANUPS', [])
    # Secure Validation: Only allow HTTPS URLs or safe local HA paths
    raw_cover_map = profiles_data.get('CUSTOM_COVER_MAP', {})
    utils.CUSTOM_COVER_MAP = {
        k: v for k, v in raw_cover_map.items()
        if isinstance(v, str) and (v.startswith("https://") or v.startswith("/"))
    }
    
    # Give utils the newly secured API key
    utils.STEAMGRIDDB_API_KEY = api_key

    entities = []
    created_wrappers = set()
    for friendly_name, platform_data in GAMER_PROFILES.items():
        _LOGGER.debug(f"[Gaming Status] Found profile: {friendly_name}")
        steam_source = platform_data.get("steam")
        xbox_source = platform_data.get("xbox")
        ps_source = platform_data.get("playstation")
        custom_source = platform_data.get("custom")
        
        ghost_list = platform_data.get("ghosted_by", [])
        exclude_list = platform_data.get("exclude_games", [])

        if steam_source and steam_source not in created_wrappers:
            entities.append(PersistentStatusSensor(hass, steam_source, "steam", friendly_name, ghost_list, exclude_list))
            created_wrappers.add(steam_source)
        if xbox_source and xbox_source not in created_wrappers:
            entities.append(PersistentStatusSensor(hass, xbox_source, "xbox", friendly_name, ghost_list, exclude_list))
            created_wrappers.add(xbox_source)
        if ps_source and ps_source not in created_wrappers:
            entities.append(PersistentStatusSensor(hass, ps_source, "playstation", friendly_name, ghost_list, exclude_list))
            created_wrappers.add(ps_source)
        if custom_source and custom_source not in created_wrappers:
            entities.append(PersistentStatusSensor(hass, custom_source, "custom", friendly_name, ghost_list, exclude_list))
            created_wrappers.add(custom_source)
        
        entities.append(MasterGamingSensor(hass, friendly_name, platform_data))
        
        entities.append(HistoryChartSensor(hass, friendly_name))

    if entities:
        async_add_entities(entities)
        _LOGGER.info(f"[Gaming Status] Added {len(entities)} entities.")

    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    current_unique_ids = {e.unique_id for e in entities}
    for entry in entries:
        if entry.unique_id not in current_unique_ids:
            _LOGGER.info("Removing orphaned entity: %s", entry.entity_id)
            entity_registry.async_remove(entry.entity_id)