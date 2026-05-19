"""
Gaming Status Sensor Platform
"""
import logging
import asyncio
import os
import re
import json
from datetime import datetime, timezone, timedelta
from dateutil import parser

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store 

_LOGGER = logging.getLogger(__name__)

from .const import (
    DOMAIN, ZOMBIE_ATTRIBUTES, PLATFORM_CONFIG, PLATFORM_PRIORITY,
    DEFAULT_RESET_HISTORY, DEFAULT_GRACE_PERIOD_SECONDS,
    DEFAULT_AWAY_GRACE_PERIOD_SECONDS, DEFAULT_GAME_TRANSITION_GRACE_SECONDS,
    DEFAULT_MIN_SESSION_DURATION, OPT_TITLE_CLEANUPS,
    CONF_STEAMGRIDDB_API_KEY, OPT_PLAYERS, OPT_GRACE_PERIOD,
    OPT_AWAY_GRACE_PERIOD, OPT_TRANSITION_GRACE, OPT_MIN_SESSION,
    OPT_RESET_HISTORY, OPT_TITLE_OVERRIDES, OPT_CUSTOM_COVERS,
    OPT_GLOBAL_EXCLUSIONS, OPT_PARENTAL, PLAYER_PLATFORMS
)

from . import utils
from .utils import (
    _get_gamertag_from_entity, _format_time, _format_game_name_for_display,
    _normalize_game_name, _safe_parse_datetime, _parse_relative_time_from_status,
    _calculate_time_ago_v2, get_steamgriddb_game_cover, get_base_game_name,
    safe_url
)

XBOX_IDLE_STATES = frozenset(s.lower() for s in PLATFORM_CONFIG["xbox"]["idle_states"])

# ------------------------------------------------------------------
# 1. PLATFORM SENSOR CLASS
# ------------------------------------------------------------------

class PersistentStatusSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    
    _unrecorded_attributes = frozenset({
        "secondary", "daily_play_time_formatted", "weekly_play_time_formatted",
        "game_cover_art", "entity_picture", "cached_game_cover", 
        "last_online_valid_timestamp", "current_game", "timer_status"
    })

    def __init__(self, hass, source_entity_id, gaming_type, owner_name, ghosted_by=None, exclude_games=None, active_settings=None, global_exclusions=None, available_avatars=None):
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._gaming_type = gaming_type
        self._owner_name = owner_name
        self._ghosted_by = ghosted_by or []
        self._available_avatars = available_avatars or []
        
        self._exclude_games = {g.lower() for g in (exclude_games or [])}
        self._global_exclusions_lower = {x.lower() for x in (global_exclusions or [])}
        
        self._active_settings = active_settings or {
            "RESET_HISTORY": DEFAULT_RESET_HISTORY,
            "GRACE_PERIOD_SECONDS": DEFAULT_GRACE_PERIOD_SECONDS,
            "AWAY_GRACE_PERIOD_SECONDS": DEFAULT_AWAY_GRACE_PERIOD_SECONDS,
            "GAME_TRANSITION_GRACE_SECONDS": DEFAULT_GAME_TRANSITION_GRACE_SECONDS,
            "MIN_SESSION_DURATION": DEFAULT_MIN_SESSION_DURATION
        }
        
        self._attr_native_value = "Offline"
        self._attr_extra_state_attributes = {
            "secondary": "Offline", "daily_play_time": 0, "weekly_play_time": 0, "weekly_play_time_last_week": 0
        }
        self._attr_entity_picture = None
        self._previous_state = None
        self._previous_state_online = None
        self._temp_game_lost_time = None
        self._ha_offline_timestamp = None
        self._last_online_valid_timestamp = None
        self._last_game_stopped_timestamp = None
        self._cached_game_cover = None
        self._current_game = None
        self._play_start_time = None
        self._last_played_game = None
        
        self._daily_play_time = 0
        self._weekly_play_time = 0
        self._weekly_play_time_last_week = 0
        self._play_history = {} 
        self._cached_history_seconds = 0
        self._local_avatar_path = None
        self._cover_fetch_attempted = False 
        
        self._away_start_timestamp = None
        self._away_timeout_deducted = False

        self._last_state_change_ts = None
        self._last_update_dt = None
        
        self._backup_last_session_time = 0 
        self._backup_last_online_timestamp = None
        self._backup_last_played_game = None
        self._backup_last_game_stopped_timestamp = None
        self._temp_offline_start = None
        self._daily_play_time_yesterday = 0
        self._last_reset_date = None
        self._last_weekly_reset = None
        self._last_session_play_time = 0
        
        config = PLATFORM_CONFIG[gaming_type]
        self._attr_icon = config["icon"]
        safe_owner = self._owner_name.lower().replace(" ", "_")
        
        self._store = Store(hass, 1, f"gaming_status.{safe_owner}_{gaming_type}_history")
        
        self._desired_entity_id = f"sensor.{safe_owner}_{gaming_type}"
        self._attr_unique_id = f"{source_entity_id}_tracker_v5"
        self._attr_name = f"{self._owner_name} {config['name_suffix']}"

    @property
    def native_value(self): return self._attr_native_value
    @property
    def available(self): return True

    @callback
    def _get_store_data(self):
        return {
            "history": self._play_history,
            "backups": {
                "backup_last_session_time": self._backup_last_session_time,
                "backup_last_online_timestamp": self._backup_last_online_timestamp,
                "backup_last_played_game": self._backup_last_played_game,
                "backup_last_game_stopped_timestamp": getattr(self, "_backup_last_game_stopped_timestamp", None)
            },
            "internal_state": {
                "temp_offline_start": self._temp_offline_start.isoformat() if self._temp_offline_start else None,
                "daily_play_time_yesterday": self._daily_play_time_yesterday,
                "last_reset_date": self._last_reset_date,
                "last_weekly_reset": self._last_weekly_reset,
                "last_session_play_time": self._last_session_play_time
            }
        }

    def _check_daily_reset(self):
        now = dt_util.now()
        current_date_str = now.strftime("%Y-%m-%d")
        current_week_str = now.strftime("%Y-%U") 
        
        history_changed = False
        
        if self._last_reset_date != current_date_str:
            if self._last_reset_date and self._daily_play_time > 0:
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

        if history_changed:
            self._cached_history_seconds = sum(self._play_history.values())
            self._store.async_delay_save(self._get_store_data, 5.0)

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
        if not current_game or self._gaming_type != "xbox": return False
        try:
            normalized = _normalize_game_name(current_game)
            steam_sensor_id = self._desired_entity_id.replace("_xbox", "_steam")
            steam_state = self.hass.states.get(steam_sensor_id)
            if steam_state and steam_state.state not in ["Offline", "offline", STATE_UNKNOWN, STATE_UNAVAILABLE]:
                steam_game = steam_state.attributes.get("current_game") or steam_state.state
                if _normalize_game_name(steam_game) == normalized:
                    return True
        except Exception: pass
        return False

    def _apply_title_override(self, game_name):
        if not game_name: return game_name
        return utils.GAME_TITLE_OVERRIDES.get(str(game_name).strip().lower(), game_name)

    def _get_platform_data(self, state, attrs):
        data = { "is_online": False, "current_game": None, "game_cover_url": None, "last_online_timestamp": None, "gamertag": None, "avatar_url": None, "game_id": None, "offline_reason": "standard" }
        state_clean = str(state).lower().strip()
        if state_clean in ["none", ""]: return None
        normalized_state = state_clean.lower()
        
        if self._gaming_type == "custom":
            if normalized_state in ["0", "off", "offline", "false", "unavailable", "unknown", "0.0", "none", ""]:
                data["is_online"] = False
            elif self._source_entity_id in utils.CUSTOM_COVER_MAP:
                if normalized_state in ["1", "on", "playing", "true", "1.0"]:
                    data["is_online"] = True
                    data["current_game"] = utils.CUSTOM_COVER_MAP.get(self._source_entity_id)
            else:
                data["is_online"] = True
                if normalized_state in ["1", "on", "playing", "true", "1.0"]:
                    data["current_game"] = "Unknown Custom Game"
                else:
                    data["current_game"] = state 

        data["avatar_url"] = attrs.get("entity_picture")
        if self._gaming_type == "custom":
            data["avatar_url"] = None

        is_globally_excluded = False
        if normalized_state in self._global_exclusions_lower: is_globally_excluded = True
        is_user_excluded = normalized_state in self._exclude_games
        is_basic_offline = state_clean in ["offline", "off", "disconnected", "0", "unavailable", "unknown", "0.0"]

        if self._gaming_type == "steam":
            if is_basic_offline: data["is_online"] = False
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
                    elif state.lower() == "playing": data["is_online"] = True 
                    else: data["is_online"] = False
                
                cover = attrs.get("game_image_main") or attrs.get("game_image_header") or attrs.get("header_image")
                if cover: data["game_cover_url"] = cover
                else:
                    app_id = attrs.get("app_id") or attrs.get("game_id")
                    if app_id: data["game_cover_url"] = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_hero.jpg"
            
            if attrs.get("last_logoff"): data["last_online_timestamp"] = attrs.get("last_logoff")
            elif attrs.get("last_online"): data["last_online_timestamp"] = attrs.get("last_online")

        elif self._gaming_type == "xbox":
            if is_globally_excluded or is_user_excluded: data["is_online"] = False
            elif state_clean.startswith("last seen"):
                data["is_online"] = False
                if ": " in state:
                    parts = state.split(": ", 1)
                    if len(parts) > 1:
                        g_name = parts[1]
                        if "(" in g_name and g_name.endswith(")"): g_name = g_name.rsplit(" (", 1)[0]
                        data["xbox_last_seen_game"] = self._apply_title_override(g_name)
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
            if data["gamertag"]:
                safe_tag = data["gamertag"].lower().replace(" ", "_")
                xbox_img = self.hass.states.get(f"image.{safe_tag}_gamerpic")
                if xbox_img and xbox_img.attributes.get("entity_picture"):
                    data["avatar_url"] = xbox_img.attributes.get("entity_picture")

        elif self._gaming_type == "playstation":
            try:
                object_id = self._source_entity_id.split('.')[1]
                if object_id.endswith("_online_status"):
                    gamertag = object_id[:-14]
                    image_state = self.hass.states.get(f"image.{gamertag}_avatar")
                    if image_state and image_state.attributes.get("entity_picture"):
                        data["avatar_url"] = image_state.attributes.get("entity_picture")
            except Exception: pass

            if is_globally_excluded or is_user_excluded: data["is_online"] = False
            elif state_clean.startswith("last seen") or state_clean.startswith("last online"): data["is_online"] = False
            elif is_basic_offline: data["is_online"] = False
            else:
                if attrs.get("title_name"):
                    data["current_game"] = attrs.get("title_name")
                    data["game_cover_url"] = attrs.get("title_image")
                    data["is_online"] = True
                elif state.lower() == "playing": data["is_online"] = False
                else:
                    found_sibling = False
                    if "_online_status" in self._source_entity_id:
                        sibling_id = self._source_entity_id.replace("_online_status", "_now_playing")
                        sibling_state = self.hass.states.get(sibling_id)
                        if sibling_state and sibling_state.state.lower() not in ["unknown", "unavailable", "unknown game", "none", ""]:
                            sibling_val = sibling_state.state
                            is_excluded_sib = False
                            if sibling_val.lower() in self._global_exclusions_lower: is_excluded_sib = True
                            if not is_excluded_sib:
                                data["current_game"] = sibling_val
                                data["is_online"] = True
                                data["game_cover_url"] = sibling_state.attributes.get("entity_picture")
                                found_sibling = True
                    if not found_sibling: data["is_online"] = False

        if data.get("is_online") and data.get("current_game"):
            data["current_game"] = self._apply_title_override(get_base_game_name(data["current_game"]))
            if _format_game_name_for_display(data["current_game"]).lower().strip() in (self._global_exclusions_lower | self._exclude_games): data["is_online"], data["current_game"] = False, None
        return data

    def _handle_game_transition(self, new_game_name, explicit_end_time=None):
        now = dt_util.now()
        actual_end_time = explicit_end_time if explicit_end_time else now
        discarded_session = False
        if self._play_start_time:
            start_dt = _safe_parse_datetime(self._play_start_time)
            if start_dt:
                if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=actual_end_time.tzinfo)
                else: start_dt = start_dt.astimezone(actual_end_time.tzinfo)
                session_seconds = (actual_end_time - start_dt).total_seconds()
                if session_seconds <= self._active_settings["MIN_SESSION_DURATION"]:
                    discarded_session = True
                    self._daily_play_time = max(0, int((self._daily_play_time or 0) - session_seconds))
                    self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - session_seconds))
                    if getattr(self, "_backup_last_session_time", None) is not None and self._backup_last_session_time > 0:
                        self._last_session_play_time = self._backup_last_session_time
                    if getattr(self, "_backup_last_online_timestamp", None) is not None:
                        self._last_online_valid_timestamp = self._backup_last_online_timestamp
                    if getattr(self, "_backup_last_played_game", None) is not None:
                        self._last_played_game = self._backup_last_played_game
                elif session_seconds > 0: self._last_session_play_time = int(session_seconds)
        if not new_game_name:
            if not discarded_session: self._last_game_stopped_timestamp = actual_end_time.isoformat()
            elif getattr(self, "_backup_last_game_stopped_timestamp", None) is not None: self._last_game_stopped_timestamp = self._backup_last_game_stopped_timestamp
            self._current_game = None
            self._play_start_time = None
        else:
            self._current_game = new_game_name
            self._play_start_time = now.isoformat()
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
                        if diff < 300: can_resurrect = True
            if can_resurrect:
                if self._last_session_play_time > 0:
                    resumed_start = now - timedelta(seconds=self._last_session_play_time)
                    self._play_start_time = resumed_start.isoformat()
            self._backup_last_session_time = self._last_session_play_time
            self._backup_last_online_timestamp = self._last_online_valid_timestamp
            self._backup_last_played_game = self._last_played_game
            self._backup_last_game_stopped_timestamp = getattr(self, "_last_game_stopped_timestamp", None)
            self._last_session_play_time = 0
            self._cover_fetch_attempted = False
            self._store.async_delay_save(self._get_store_data, 5.0)

    def _get_session_info(self):
        if not self._play_start_time: return 0, None
        start_dt = _safe_parse_datetime(self._play_start_time)
        if not start_dt: return 0, None
        now = dt_util.now()
        if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=now.tzinfo)
        else: start_dt = start_dt.astimezone(now.tzinfo)
        seconds = (now - start_dt).total_seconds()
        if seconds < 60: return seconds, "just now"
        minutes = int(seconds // 60)
        hours = int(minutes // 60)
        if hours > 0: display = f"{hours}h {minutes % 60}m"
        else: display = f"{minutes}m"
        return seconds, display

    def _clean_restored_game_name(self, game_name):
        if not game_name: return None
        clean = str(game_name).strip()
        if "last seen" in clean.lower():
            if ": " in clean:
                parts = clean.split(": ")
                clean = parts[-1].strip()
                if "(" in clean and clean.endswith(")"): clean = clean.rsplit(" (", 1)[0].strip()
        return self._apply_title_override(clean)

    def _write_common_attributes(self, secondary="", timer_status=None, game_cover=None):
        if timer_status: self._attr_extra_state_attributes["timer_status"] = timer_status
        self._attr_extra_state_attributes["current_game"] = self._current_game
        self._attr_extra_state_attributes["game_cover_art"] = game_cover or self._cached_game_cover
        self._attr_extra_state_attributes["daily_play_time"] = self._daily_play_time
        self._attr_extra_state_attributes["daily_play_time_formatted"] = _format_time(self._daily_play_time)
        self._attr_extra_state_attributes["weekly_play_time"] = self._weekly_play_time
        self._attr_extra_state_attributes["weekly_play_time_formatted"] = _format_time(self._weekly_play_time)
        self._attr_extra_state_attributes["weekly_play_time_last_week"] = self._weekly_play_time_last_week
        
        live_avatar = self._local_avatar_path
        if not live_avatar:
            if self._gaming_type == "xbox":
                gamertag = _get_gamertag_from_entity(self._source_entity_id, "xbox")
                if gamertag:
                    safe_tag = gamertag.lower().replace(" ", "_")
                    xbox_img = self.hass.states.get(f"image.{safe_tag}_gamerpic")
                    if xbox_img and xbox_img.attributes.get("entity_picture"):
                        live_avatar = xbox_img.attributes.get("entity_picture")
            elif self._gaming_type == "playstation":
                try:
                    object_id = self._source_entity_id.split('.')[1]
                    if object_id.endswith("_online_status"):
                        gamertag = object_id[:-14]
                        ps_img = self.hass.states.get(f"image.{gamertag}_avatar")
                        if ps_img and ps_img.attributes.get("entity_picture"):
                            live_avatar = ps_img.attributes.get("entity_picture")
                except Exception: pass
        if live_avatar: self._attr_entity_picture = live_avatar
        self._attr_extra_state_attributes["entity_picture"] = self._attr_entity_picture
        if self._last_online_valid_timestamp: self._attr_extra_state_attributes["last_online_valid_timestamp"] = str(self._last_online_valid_timestamp)
        now = dt_util.now()
        self._last_update_dt = now
        total_rolling = self._cached_history_seconds + self._daily_play_time
        self._attr_extra_state_attributes["rolling_weekly_hours"] = round(total_rolling / 3600, 2)
        self._attr_extra_state_attributes["last_played_game"] = self._last_played_game
        self._attr_extra_state_attributes["play_start_time"] = self._play_start_time
        self._attr_extra_state_attributes["cached_game_cover"] = self._cached_game_cover
        self._attr_extra_state_attributes["secondary"] = secondary

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        stored_data = await self._store.async_load()
        if stored_data:
            self._play_history = stored_data.get("history", {})
            backups = stored_data.get("backups", {})
            self._backup_last_session_time = backups.get("backup_last_session_time", 0)
            self._backup_last_online_timestamp = backups.get("backup_last_online_timestamp")
            self._backup_last_played_game = backups.get("backup_last_played_game")
            self._backup_last_game_stopped_timestamp = backups.get("backup_last_game_stopped_timestamp")
            internal = stored_data.get("internal_state", {})
            temp_off = internal.get("temp_offline_start")
            self._temp_offline_start = _safe_parse_datetime(temp_off) if temp_off else None
            self._daily_play_time_yesterday = int(internal.get("daily_play_time_yesterday", 0))
            self._last_reset_date = internal.get("last_reset_date")
            self._last_weekly_reset = internal.get("last_weekly_reset")
            self._last_session_play_time = int(internal.get("last_session_play_time", 0))
        else:
            self._play_history = {}
            self._backup_last_session_time = 0
            self._backup_last_online_timestamp = None
            self._backup_last_played_game = None
            self._backup_last_game_stopped_timestamp = None
            self._temp_offline_start = None
            self._daily_play_time_yesterday = 0
            self._last_reset_date = None
            self._last_weekly_reset = None
            self._last_session_play_time = 0
        self._cached_history_seconds = sum(self._play_history.values())

        def _check_local_avatar():
            safe_name = re.sub(r'[^a-z0-9_]', '', self._owner_name.lower().replace(" ", "_"))
            for ext in ['png', 'jpg']:
                file_name = f"{self._gaming_type}_{safe_name}_avatar.{ext}"
                if file_name in self._available_avatars: return f"/local/gaming_status/{file_name}"
            return None
        self._local_avatar_path = _check_local_avatar()

        last_state = await self.async_get_last_state()
        if self._active_settings["RESET_HISTORY"]:
            self._attr_native_value = "Offline"
            self._attr_extra_state_attributes = {}
        elif last_state:
            self._attr_native_value = last_state.state
            attrs = last_state.attributes
            self._last_online_valid_timestamp = attrs.get("last_online_valid_timestamp")
            self._last_game_stopped_timestamp = attrs.get("last_game_stopped_timestamp")
            self._last_state_change_ts = last_state.last_changed
            restored_last_game = attrs.get("last_played_game")
            self._last_played_game = self._clean_restored_game_name(restored_last_game)
            self._cached_game_cover = attrs.get("cached_game_cover")
            if not stored_data or "internal_state" not in stored_data:
                self._temp_offline_start = _safe_parse_datetime(attrs.get("temp_offline_start"))
                self._last_session_play_time = int(attrs.get("last_session_play_time") or 0)
                self._daily_play_time_yesterday = int(float(attrs.get("daily_play_time_yesterday") or 0))
                self._last_reset_date = attrs.get("last_reset_date")
                self._last_weekly_reset = attrs.get("last_weekly_reset")
            try:
                self._daily_play_time = int(float(attrs.get("daily_play_time") or 0))
                self._weekly_play_time = int(float(attrs.get("weekly_play_time") or 0))
                self._weekly_play_time_last_week = int(float(attrs.get("weekly_play_time_last_week") or 0))
            except Exception:
                self._daily_play_time = 0
                self._weekly_play_time = 0
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
            if self._last_game_stopped_timestamp: self._last_online_valid_timestamp = self._last_game_stopped_timestamp
        
        for zombie in ZOMBIE_ATTRIBUTES:
            if zombie in self._attr_extra_state_attributes: del self._attr_extra_state_attributes[zombie]
        for legacy_debug in ["debug_raw_source_state", "debug_time_ago", "debug_sync", "source_entity", "play_history", "code_version", "last_update_timestamp", "backup_last_session_time", "backup_last_online_timestamp", "backup_last_played_game", "backup_last_game_stopped_timestamp", "temp_offline_start", "daily_play_time_yesterday", "last_reset_date", "last_weekly_reset", "last_session_play_time", "daily_play_limit_minutes", "remaining_play_time_minutes"]:
            if legacy_debug in self._attr_extra_state_attributes: del self._attr_extra_state_attributes[legacy_debug]

        source_state = self.hass.states.get(self._source_entity_id)
        if source_state: await self._try_force_sync(source_state)
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
        elif self._gaming_type == "xbox":
            try:
                gamertag = _get_gamertag_from_entity(self._source_entity_id, "xbox")
                if gamertag:
                    safe_tag = gamertag.lower().replace(" ", "_")
                    image_entity = f"image.{safe_tag}_gamerpic"
                    entities_to_watch.append(image_entity)
            except Exception: pass

        self.async_on_remove(async_track_state_change_event(self.hass, entities_to_watch, self._async_state_changed))
        self.async_on_remove(async_track_time_interval(self.hass, self._update_play_time, timedelta(seconds=30)))
        await self._trigger_source_update(force=True)

    async def _trigger_source_update(self, force=False):
        source = self.hass.states.get(self._source_entity_id)
        if source: await self._unified_update(source.state, source.attributes, force_update=force)

    async def _try_force_sync(self, source_state):
        if self._last_online_valid_timestamp: return
        s_ts = None
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
            except Exception: pass
        if not self._attr_extra_state_attributes: self._attr_extra_state_attributes = {}

    @callback
    def _async_state_changed(self, event): self.hass.async_create_task(self._trigger_source_update())

    @callback
    def _update_play_time(self, now=None):
        try:
            was_offline = (self._attr_native_value.lower() == "offline")
            old_daily = self._daily_play_time
            old_secondary = self._attr_extra_state_attributes.get("secondary")
            self._check_daily_reset()
            if self._last_played_game and str(self._last_played_game).lower() == "offline": self._last_played_game = None
            now_dt = dt_util.now()
            delta_seconds = 30
            if self._last_update_dt:
                delta = (now_dt - self._last_update_dt).total_seconds()
                if 0 < delta < 120: delta_seconds = int(delta)
                else: delta_seconds = 30
            if not self._attr_extra_state_attributes: self._attr_extra_state_attributes = {}
            if self._temp_offline_start:
                time_in_limbo = (now_dt - self._temp_offline_start).total_seconds()
                if time_in_limbo > self._active_settings["GRACE_PERIOD_SECONDS"]: self.hass.async_create_task(self._trigger_source_update())
            if self._attr_native_value.lower() != "offline" and not self._current_game:
                self._current_game = self._attr_native_value
                if not self._play_start_time: 
                    if self._last_state_change_ts: self._play_start_time = self._last_state_change_ts.isoformat()
                    else: self._play_start_time = now_dt.isoformat()
            if self._current_game and not self._play_start_time: self._play_start_time = now_dt.isoformat()
            timer_status = "Inactive"
            secondary = ""
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
                else: timer_status = f"Paused ({block_reason})"
                session_seconds, play_time_text = self._get_session_info()
                if play_time_text: secondary = f"({play_time_text})"
                else: secondary = "Playing now"
                if session_seconds > self._active_settings["MIN_SESSION_DURATION"] and not is_blocked: self._last_played_game = self._current_game
            else:
                timer_status = "Stopped (Offline)"
                if self._attr_native_value.lower() != "offline": self._attr_native_value = "Offline"
                if not self._last_online_valid_timestamp and self._last_game_stopped_timestamp: self._last_online_valid_timestamp = self._last_game_stopped_timestamp
                if self._last_online_valid_timestamp:
                    time_ago, debug_info = _calculate_time_ago_v2(self._last_online_valid_timestamp)
                    if time_ago:
                        if self._last_played_game:
                            secondary = f"Last seen {time_ago}: {self._last_played_game}"
                            if self._last_session_play_time and self._last_session_play_time >= 60:
                                st_str = _format_time(self._last_session_play_time)
                                secondary = f"{secondary} ({st_str})"
                        else: secondary = f"Last seen {time_ago}"
                    else: secondary = "Offline"
                else: secondary = "Offline"
            self._write_common_attributes(secondary, timer_status=timer_status)
            if was_offline and timer_status == "Stopped (Offline)" and self._daily_play_time == old_daily and secondary == old_secondary: return
            self.async_write_ha_state()
        except Exception as e: _LOGGER.error("Error in _update_play_time for %s: %s", self.entity_id, e)

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
                            deduction = min(int(time_away), current_daily)
                            self._daily_play_time = max(0, current_daily - deduction)
                            self._weekly_play_time = max(0, current_weekly - deduction)
                            self._away_timeout_deducted = True 
                else:
                    self._away_start_timestamp = None
                    self._away_timeout_deducted = False
            except Exception: pass
            is_offline_now = not platform_data.get("is_online")
            in_grace_period = False
            current_grace_limit = self._active_settings["GRACE_PERIOD_SECONDS"]
            if platform_data.get("offline_reason") == "away": current_grace_limit = self._active_settings["AWAY_GRACE_PERIOD_SECONDS"]
            if self._current_game and is_offline_now:
                if self._temp_offline_start is None: 
                    can_start_grace = True
                    if self._last_online_valid_timestamp:
                        last_ts = _safe_parse_datetime(self._last_online_valid_timestamp)
                        if last_ts:
                            time_since_valid = (now_dt - last_ts).total_seconds()
                            if time_since_valid > current_grace_limit: can_start_grace = False
                    if can_start_grace:
                        self._temp_offline_start = now_dt
                        self._store.async_delay_save(self._get_store_data, 5.0)
                if self._temp_offline_start:
                    offline_duration = (now_dt - self._temp_offline_start).total_seconds()
                    if offline_duration <= current_grace_limit: in_grace_period = True
            elif not is_offline_now: 
                if self._temp_offline_start:
                    missed_seconds = (now_dt - self._temp_offline_start).total_seconds()
                    if missed_seconds > 0:
                        self._daily_play_time = int((self._daily_play_time or 0) + missed_seconds)
                        self._weekly_play_time = int((self._weekly_play_time or 0) + missed_seconds)
                if self._temp_offline_start is not None:
                    self._temp_offline_start = None
                    self._store.async_delay_save(self._get_store_data, 5.0)
            display_state = "Offline"
            game_cover = None
            secondary = ""
            if platform_data.get("current_game"):
                raw_game_name = platform_data["current_game"]
                raw_game_name = self._apply_title_override(raw_game_name)
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
                        if _normalize_game_name(self._current_game) == normalized_new and time_since_lost <= self._active_settings["GAME_TRANSITION_GRACE_SECONDS"]: self._current_game = game_name_display
                        else: self._handle_game_transition(game_name_display)
                    else: self._handle_game_transition(game_name_display)
                    self._temp_game_lost_time = None
                else:
                    self._current_game = game_name_display
                    self._temp_game_lost_time = None
                display_state = game_name_display
                if normalized_new and not self._cover_fetch_attempted:
                    fetched = await get_steamgriddb_game_cover(self.hass, game_name_display)
                    self._cover_fetch_attempted = True
                    if fetched: self._cached_game_cover = fetched
                    else: self._cached_game_cover = platform_data.get("game_cover_url")
                elif not self._cached_game_cover and platform_data.get("game_cover_url"): self._cached_game_cover = platform_data.get("game_cover_url")
                game_cover = self._cached_game_cover
                session_seconds, play_time_text = self._get_session_info()
                if session_seconds > self._active_settings["MIN_SESSION_DURATION"]:
                    if not self._is_game_active_elsewhere(game_name_display) and not self._is_ghost_session(game_name_display): self._last_played_game = game_name_display
                if play_time_text: secondary = f"({play_time_text})"
                else: secondary = "Playing now"
            elif self._current_game and in_grace_period:
                display_state = self._current_game
                session_seconds, play_time_text = self._get_session_info()
                if play_time_text: secondary = f"({play_time_text})"
                else: secondary = "Playing now"
                game_cover = platform_data.get("game_cover_url") or self._cached_game_cover
                if not self._temp_game_lost_time: self._temp_game_lost_time = now_dt
            else:
                display_state = "Offline"
                game_cover = None
                if self._current_game:
                    if self._temp_offline_start:
                        limit_to_check = self._active_settings["GRACE_PERIOD_SECONDS"]
                        if platform_data.get("offline_reason") == "away": limit_to_check = self._active_settings["AWAY_GRACE_PERIOD_SECONDS"]
                        if (now_dt - self._temp_offline_start).total_seconds() > limit_to_check:
                            self._handle_game_transition(None, explicit_end_time=self._temp_offline_start)
                            self._temp_offline_start = None 
                            self._temp_game_lost_time = None
                            self._store.async_delay_save(self._get_store_data, 5.0)
                    elif self._temp_game_lost_time:
                        if (now_dt - self._temp_game_lost_time).total_seconds() > self._active_settings["GAME_TRANSITION_GRACE_SECONDS"]:
                            self._handle_game_transition(None)
                            self._temp_game_lost_time = None
                    else: self._handle_game_transition(None)
                self._temp_offline_start = None
                if is_offline_now:
                    if self._gaming_type == "xbox" and platform_data.get("xbox_last_seen_game"):
                        new_xbox_game = _format_game_name_for_display(self._clean_restored_game_name(platform_data["xbox_last_seen_game"]))
                        idle_list = XBOX_IDLE_STATES
                        is_ghost = self._is_ghost_session(new_xbox_game)
                        if new_xbox_game.lower() not in idle_list and not is_ghost: self._last_played_game = new_xbox_game
                time_ago, debug_info = _calculate_time_ago_v2(self._last_online_valid_timestamp)
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
            entity_pic = self._local_avatar_path
            if not entity_pic and platform_data.get("avatar_url"): entity_pic = platform_data.get("avatar_url")
            self._attr_entity_picture = entity_pic
            self._write_common_attributes(secondary, game_cover=game_cover)
            self.async_write_ha_state()
        except Exception as e: _LOGGER.error("Error in _unified_update for %s: %s", self.entity_id, e)

# ------------------------------------------------------------------
# 2. MASTER SENSOR CLASS
# ------------------------------------------------------------------

class MasterGamingSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"secondary", "game_cover_art", "entity_picture", "last_online_valid_timestamp", "current_game", "daily_play_limit_minutes", "remaining_play_time_minutes"})
    
    def __init__(self, hass, name, profiles, parental_rules=None):
        self.hass = hass
        self._profiles = profiles
        self._parental_rules = parental_rules or {}
        safe_owner = name.lower().replace(" ", "_")
        self._attr_name = f"{name} Gaming Status"
        self._attr_unique_id = f"{safe_owner}_master_v5"
        self.entity_id = f"sensor.{safe_owner}_gaming_status"
        self._attr_native_value = "Offline"
        self._attr_icon = "mdi:controller"
        self._attr_entity_picture = None
        self._attr_extra_state_attributes = {}
        self._platform_sensors = {}
        for platform in PLATFORM_PRIORITY:
            if profiles.get(platform): self._platform_sensors[f"sensor.{safe_owner}_{platform}"] = platform
    
    @property
    def available(self): return True

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._attr_native_value = last_state.state
            self._attr_extra_state_attributes = dict(last_state.attributes)
            self._attr_entity_picture = last_state.attributes.get("entity_picture")
            lp = self._attr_extra_state_attributes.get("last_played_game")
            if lp and str(lp).lower() == "offline": self._attr_extra_state_attributes["last_played_game"] = None
        if self._platform_sensors: self.async_on_remove(async_track_state_change_event(self.hass, list(self._platform_sensors.keys()), self._async_platform_changed))
        await self._update_master_state()
    
    @callback
    def _async_platform_changed(self, event): self.hass.async_create_task(self._update_master_state())
    
    async def _update_master_state(self):
        active_sensor_id = None
        active_state = None
        total_daily_seconds = 0
        total_weekly_seconds = 0
        total_rolling_weekly_hours = 0.0 
        total_weekly_seconds_last_week = 0
        most_recent_ts = None
        most_recent_sensor = None
        most_recent_key = None
        
        for platform_sensor_id, p_key in self._platform_sensors.items():
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
            ts_str = platform_state.attributes.get("last_online_valid_timestamp")
            if ts_str:
                try:
                    ts = parser.isoparse(ts_str)
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                    if most_recent_ts is None or ts > most_recent_ts:
                        most_recent_ts = ts
                        most_recent_sensor = platform_state
                        most_recent_key = p_key
                except Exception: pass
            if active_state: continue 
            state_value = platform_state.state
            if state_value.lower() not in ["offline", "source missing", "unavailable", "unknown"]:
                active_sensor_id = platform_sensor_id
                active_state = platform_state
        
        # --- Parental Calculation Logic ---
        daily_play_limit_minutes = 0
        remaining_play_time_minutes = 0
        st_rule = self._parental_rules.get("screen_time", {})
        if st_rule.get("enabled"):
            is_weekend = dt_util.now().weekday() >= 5
            daily_play_limit_minutes = st_rule.get("weekend_minutes", 180) if is_weekend else st_rule.get("weekday_minutes", 120)
            spent = int(total_daily_seconds / 60)
            remaining_play_time_minutes = max(0, daily_play_limit_minutes - spent)

        total_daily_hours = round(total_daily_seconds / 3600, 2)
        total_weekly_hours = round(total_weekly_seconds / 3600, 2)
        total_rolling_weekly_hours = round(total_rolling_weekly_hours, 2)
        total_weekly_hours_last_week = round(total_weekly_seconds_last_week / 3600, 2)

        new_state_value = "Offline"
        new_icon = "mdi:controller"
        new_entity_picture = None
        new_attrs = {}

        if active_state:
            new_state_value = active_state.state
            new_entity_picture = active_state.attributes.get("entity_picture")
            platform_key = self._platform_sensors.get(active_sensor_id, "gaming")
            pretty_platform_name = PLATFORM_CONFIG.get(platform_key, {}).get("name_suffix", platform_key.title())
            new_attrs = {
                "secondary": active_state.attributes.get("secondary", ""),
                "active_platform": pretty_platform_name, 
                "game_cover_art": active_state.attributes.get("game_cover_art"),
                "current_game": active_state.attributes.get("current_game"),
                "play_start_time": active_state.attributes.get("play_start_time"),
                "last_online_valid_timestamp": active_state.attributes.get("last_online_valid_timestamp"),
                "total_daily_hours": total_daily_hours,
                "total_weekly_hours": total_weekly_hours,
                "rolling_weekly_hours": total_rolling_weekly_hours, 
                "total_weekly_hours_last_week": total_weekly_hours_last_week,
                "last_played_game": active_state.attributes.get("last_played_game"),
                "entity_picture": new_entity_picture,
                "daily_play_limit_minutes": daily_play_limit_minutes,
                "remaining_play_time_minutes": remaining_play_time_minutes
            }
            if platform_key in PLATFORM_CONFIG: new_icon = PLATFORM_CONFIG[platform_key]["icon"]
        else:
            if most_recent_sensor:
                pretty_name = PLATFORM_CONFIG.get(most_recent_key, {}).get("name_suffix", "Gaming")
                new_entity_picture = most_recent_sensor.attributes.get("entity_picture")
                new_attrs = {
                    "secondary": most_recent_sensor.attributes.get("secondary", "Offline"),
                    "active_platform": pretty_name,
                    "game_cover_art": most_recent_sensor.attributes.get("game_cover_art"),
                    "last_played_game": most_recent_sensor.attributes.get("last_played_game"),
                    "last_online_valid_timestamp": most_recent_sensor.attributes.get("last_online_valid_timestamp"),
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week,
                    "entity_picture": new_entity_picture,
                    "daily_play_limit_minutes": daily_play_limit_minutes,
                    "remaining_play_time_minutes": remaining_play_time_minutes
                }
                lp = new_attrs.get("last_played_game")
                if lp and str(lp).lower() == "offline": new_attrs["last_played_game"] = None
                if most_recent_key in PLATFORM_CONFIG: new_icon = PLATFORM_CONFIG[most_recent_key]["icon"]
            else:
                new_attrs = {
                    "secondary": "Offline",
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week,
                    "entity_picture": None,
                    "daily_play_limit_minutes": daily_play_limit_minutes,
                    "remaining_play_time_minutes": remaining_play_time_minutes
                }

        if (self._attr_native_value == new_state_value and 
            self._attr_icon == new_icon and 
            self._attr_entity_picture == new_entity_picture and
            self._attr_extra_state_attributes == new_attrs): return

        self._attr_native_value = new_state_value
        self._attr_icon = new_icon
        self._attr_entity_picture = new_entity_picture
        self._attr_extra_state_attributes = new_attrs
        self.async_write_ha_state()

# ------------------------------------------------------------------
# 3. HISTORY CHART & SETUP
# ------------------------------------------------------------------

class HistoryChartSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"
    def __init__(self, hass, name):
        self.hass = hass
        safe_owner = name.lower().replace(" ", "_")
        self._attr_name = f"{name} Chart"
        self._attr_unique_id = f"{safe_owner}_chart_v161"
        self.entity_id = f"sensor.{safe_owner}_daily_gaming_hours_chart"
        self._master_sensor_id = f"sensor.{safe_owner}_gaming_status"
        self._attr_native_value = 0.0

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            try: self._attr_native_value = float(last_state.state)
            except ValueError: self._attr_native_value = 0.0
        self.async_on_remove(async_track_state_change_event(self.hass, [self._master_sensor_id], self._async_master_changed))
        master_state = self.hass.states.get(self._master_sensor_id)
        if master_state:
            daily_hours = master_state.attributes.get("total_daily_hours", 0.0)
            try:
                daily_hours_float = float(daily_hours)
                if self._attr_native_value != daily_hours_float:
                    self._attr_native_value = daily_hours_float
                    self.async_write_ha_state()
            except (ValueError, TypeError): pass

    @callback
    def _async_master_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state:
            daily_hours = new_state.attributes.get("total_daily_hours", 0.0)
            try:
                daily_hours_float = float(daily_hours)
                if self._attr_native_value != daily_hours_float:
                    self._attr_native_value = daily_hours_float
                    self.async_write_ha_state()
            except (ValueError, TypeError): pass

# ------------------------------------------------------------------
# 4. GLOBAL ONLINE COUNT SENSOR
# ------------------------------------------------------------------

class GlobalOnlineCountSensor(SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    def __init__(self, hass, players):
        self.hass = hass
        self._attr_name = "Players Online"
        self._attr_unique_id = "global_players_online_count_v1"
        self.entity_id = "sensor.players_online"
        self._attr_icon = "mdi:account-group"
        self._attr_native_value = 0
        self._master_sensors = []
        for player_name in players:
            safe_owner = player_name.lower().replace(" ", "_")
            self._master_sensors.append(f"sensor.{safe_owner}_gaming_status")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_track_state_change_event(self.hass, self._master_sensors, self._async_master_changed))
        self._update_count()

    @callback
    def _async_master_changed(self, event): self._update_count()

    def _update_count(self):
        count = 0
        active_games = []
        for entity_id in self._master_sensors:
            state = self.hass.states.get(entity_id)
            if state and state.state.lower() not in ["offline", "unavailable", "unknown", "source missing"]:
                count += 1
                active_games.append(state.state)
        new_games_str = ", ".join(active_games) if active_games else "None"
        current_games_str = getattr(self, "_attr_extra_state_attributes", {}).get("active_games", "None")
        if self._attr_native_value == count and current_games_str == new_games_str: return
        self._attr_native_value = count
        self._attr_extra_state_attributes = {"active_games": new_games_str}
        self.async_write_ha_state()
        
async def async_setup_entry(hass, config_entry, async_add_entities):
    opts = config_entry.options
    active_settings = {
        "RESET_HISTORY": opts.get(OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY),
        "GRACE_PERIOD_SECONDS": opts.get(OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS),
        "AWAY_GRACE_PERIOD_SECONDS": opts.get(OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS),
        "GAME_TRANSITION_GRACE_SECONDS": opts.get(OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS),
        "MIN_SESSION_DURATION": opts.get(OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION),
    }
    raw_overrides = _load_opt_json(opts, OPT_TITLE_OVERRIDES, {})
    utils.GAME_TITLE_OVERRIDES = {k.strip().lower(): v for k, v in raw_overrides.items()}
    raw_covers = _load_opt_json(opts, OPT_CUSTOM_COVERS, {})
    utils.CUSTOM_COVER_MAP = {k: safe_url(v) for k, v in raw_covers.items() if safe_url(v)}
    raw_cleanups = _load_opt_json(opts, OPT_TITLE_CLEANUPS, [])
    utils.TITLE_CLEANUPS = raw_cleanups
    utils.compile_title_cleanups()
    utils.STEAMGRIDDB_API_KEY = config_entry.data.get(CONF_STEAMGRIDDB_API_KEY, "")
    global_exclusions = _load_opt_json(opts, OPT_GLOBAL_EXCLUSIONS, [])
    players = _load_opt_json(opts, OPT_PLAYERS, {})
    parental_rules = _load_opt_json(opts, OPT_PARENTAL, {})
    avatar_dir = hass.config.path("www/gaming_status")
    try: available_avatars = await hass.async_add_executor_job(os.listdir, avatar_dir)
    except FileNotFoundError: available_avatars = []

    ents = []
    for player_name, player_data in players.items():
        ghosted_by = player_data.get("ghosted_by", [])
        exclude_games = player_data.get("exclude_games", [])
        rules = parental_rules.get(player_name, {})

        for platform in PLAYER_PLATFORMS:
            entity_id = player_data.get(platform)
            if entity_id:
                ents.append(PersistentStatusSensor(hass, entity_id, platform, player_name, ghosted_by, exclude_games, active_settings, global_exclusions, available_avatars))

        ents.extend([
            MasterGamingSensor(hass, player_name, player_data, rules),
            HistoryChartSensor(hass, player_name),
        ])

    ents.append(GlobalOnlineCountSensor(hass, players))
    async_add_entities(ents)

def _load_opt_json(options, key, fallback):
    raw = options.get(key)
    if not raw: return fallback
    try: return json.loads(raw)
    except (TypeError, ValueError): return fallback