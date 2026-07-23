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

from homeassistant.components.sensor import RestoreSensor, SensorEntity, SensorStateClass
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
    DEFAULT_MIN_SESSION_DURATION, MAX_RECENT_SESSIONS, OPT_TITLE_CLEANUPS,
    CONF_STEAMGRIDDB_API_KEY, CONF_RAWG_API_KEY, OPT_RATING_OVERRIDES, OPT_PLAYERS, OPT_GRACE_PERIOD,
    OPT_AWAY_GRACE_PERIOD, OPT_TRANSITION_GRACE, OPT_MIN_SESSION,
    OPT_SAME_GAME_PREFIX_WORDS, DEFAULT_SAME_GAME_PREFIX_WORDS,
    OPT_MASTER_HANDOFF_GRACE, DEFAULT_MASTER_HANDOFF_GRACE_SECONDS,
    OPT_RESET_HISTORY, OPT_TITLE_OVERRIDES, OPT_CUSTOM_GRID,
    OPT_CUSTOM_HERO, OPT_CUSTOM_LOGO, OPT_CUSTOM_ICON,
    OPT_CUSTOM_COLORS, OPT_GLOBAL_EXCLUSIONS, OPT_PARENTAL, 
    PLAYER_PLATFORMS, OPT_USE_CACHE, DEFAULT_USE_CACHE, 
    OPT_EXTRACT_COLOR, DEFAULT_EXTRACT_COLOR, OPT_CACHE_MAX_FILES,
    DEFAULT_CACHE_MAX_FILES, OPT_CACHE_MAX_DAYS, DEFAULT_CACHE_MAX_DAYS,
)

from . import utils
from .utils import (
    _get_gamertag_from_entity, _format_time, _format_game_name_for_display,
    _normalize_game_name, _safe_parse_datetime, _parse_relative_time_from_status,
    _calculate_time_ago_v2, get_base_game_name, safe_url, url_host_matches,
    _is_same_base_game, top_n_games
)

XBOX_IDLE_STATES = frozenset(s.lower() for s in PLATFORM_CONFIG["xbox"]["idle_states"])

# ------------------------------------------------------------------
# 1. PLATFORM SENSOR CLASS
# ------------------------------------------------------------------

class PersistentStatusSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    
    _unrecorded_attributes = frozenset({
        "secondary",
        "game_cover_art", "game_hero_art", "game_logo_art", "game_icon_art",
        "entity_picture", "cached_game_cover",
        "last_online_valid_timestamp", "current_game", "timer_status",
        "weekly_game_breakdown", "longest_session_details",
        "rolling_weekly_breakdown", "rolling_longest_session_details",
        "calendar_weekly_breakdown", "calendar_longest_session_details",
        "last_played_game", "daily_play_time", "weekly_play_time", "weekly_play_time_last_week",
        "play_history", "game_content_rating", "recent_sessions",
        "all_time_total_hours", "all_time_session_count", "all_time_top_games",
    })

    def __init__(self, hass, source_entity_id, gaming_type, owner_name, ghosted_by=None, exclude_games=None, active_settings=None, global_exclusions=None, available_avatars=None, ps3_entity_id=None):
        
        # --- SILENT AUTO-CORRECTION FOR CONSOLES ---
        # Migrate legacy _online_status / _onlinestatus sources to _now_playing.
        # Use the device registry to find the translated "now_playing" entity so
        # non-English locales (e.g. _spielt_gerade) resolve correctly.
        if gaming_type == "playstation":
            for old_suffix in ["_online_status", "_onlinestatus"]:
                if source_entity_id.endswith(old_suffix):
                    try:
                        reg = er.async_get(hass)
                        old_entry = reg.async_get(source_entity_id)
                        found = False
                        if old_entry and old_entry.device_id:
                            for d in er.async_entries_for_device(reg, old_entry.device_id):
                                if d.domain == "sensor" and getattr(d, "translation_key", None) == "now_playing":
                                    source_entity_id = d.entity_id
                                    found = True
                                    break
                        if not found:
                            source_entity_id = source_entity_id[:-len(old_suffix)] + "_now_playing"
                    except Exception:
                        source_entity_id = source_entity_id[:-len(old_suffix)] + "_now_playing"
                    break
        elif gaming_type == "xbox":
            for wrong_suffix in ["_now_playing", "_last_online"]:
                if wrong_suffix in source_entity_id:
                    try:
                        reg = er.async_get(hass)
                        wrong_entry = reg.async_get(source_entity_id)
                        found = False
                        if wrong_entry and wrong_entry.device_id:
                            for d in er.async_entries_for_device(reg, wrong_entry.device_id):
                                if d.domain == "sensor" and getattr(d, "translation_key", None) == "status":
                                    source_entity_id = d.entity_id
                                    found = True
                                    break
                        if not found:
                            source_entity_id = source_entity_id.replace(wrong_suffix, "_status")
                    except Exception:
                        source_entity_id = source_entity_id.replace(wrong_suffix, "_status")
                    break
                
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._gaming_type = gaming_type
        self._owner_name = owner_name
        self._ghosted_by = ghosted_by or []
        self._ghost_missing_warned = set()
        self._init_time = dt_util.now()
        self._available_avatars = available_avatars or []
        
        self._avatar_entity_id = None
        self._xbox_now_playing_entity_id = None
        self._ps3_entity_id = ps3_entity_id

        self._exclude_games = {_normalize_game_name(g) for g in (exclude_games or [])}
        self._global_exclusions_lower = {_normalize_game_name(x) for x in (global_exclusions or [])}
        
        self._active_settings = active_settings or {
            "RESET_HISTORY": DEFAULT_RESET_HISTORY,
            "GRACE_PERIOD_SECONDS": DEFAULT_GRACE_PERIOD_SECONDS,
            "AWAY_GRACE_PERIOD_SECONDS": DEFAULT_AWAY_GRACE_PERIOD_SECONDS,
            "GAME_TRANSITION_GRACE_SECONDS": DEFAULT_GAME_TRANSITION_GRACE_SECONDS,
            "MIN_SESSION_DURATION": DEFAULT_MIN_SESSION_DURATION,
            "SAME_GAME_PREFIX_WORDS": DEFAULT_SAME_GAME_PREFIX_WORDS
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
        
        # Artwork Caches
        self._cached_game_cover = None
        self._cached_game_hero = None
        self._cached_game_logo = None
        self._cached_game_icon = None
        self._cached_game_color = None
        self._color_history_cache = {}

        # Content-rating cache
        self._cached_game_rating = None
        self._rating_fetch_attempted = False

        self._current_game = None
        self._play_start_time = None
        self._last_played_game = None
        
        # New Rich Tracking Data
        self._weekly_game_breakdown = {}
        self._longest_session_details = {"game": None, "duration": 0}

        # All-time (lifetime) tracking data. Never pruned, unlike
        # _weekly_game_breakdown/_play_history -- lives only in the JSON
        # store and a small bounded attribute summary, never the recorder.
        self._all_time_game_seconds = {}
        self._all_time_session_count = 0
        self._all_time_seeded = False
        
        self._daily_play_time = 0
        self._weekly_play_time = 0
        self._weekly_play_time_last_week = 0
        self._play_history = {}
        self._recent_sessions = []
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
        self._session_ticks_persistent = {}
        self._active_elsewhere_blocked_seconds = {}

        config = PLATFORM_CONFIG[gaming_type]
        self._attr_icon = config["icon"]
        safe_owner = re.sub(r'[^a-z0-9_]', '_', self._owner_name.lower().replace(" ", "_"))
        
        self._store = Store(hass, 1, f"gaming_status.{safe_owner}_{gaming_type}_history")
        
        self._desired_entity_id = f"sensor.gaming_status_{safe_owner}_{gaming_type}"
        self.entity_id = self._desired_entity_id
        self._attr_unique_id = f"gaming_status_{safe_owner}_{source_entity_id}_tracker_v6"
        self._attr_name = f"{self._owner_name} {config['name_suffix']}"

    @property
    def native_value(self): return self._attr_native_value
    @property
    def available(self): return True

    def _bump_playtime(self, game, delta):
        """Credit `delta` seconds to `game` in both the weekly (pruned) and
        all-time (never pruned) breakdowns together, so they can't drift."""
        if not game or delta <= 0:
            return
        self._weekly_game_breakdown[game] = self._weekly_game_breakdown.get(game, 0) + int(delta)
        self._all_time_game_seconds[game] = self._all_time_game_seconds.get(game, 0) + int(delta)

    def _unbump_playtime(self, game, delta):
        """Roll back `delta` seconds from both breakdowns together -- used
        when a session turns out to be too short and gets discarded, so a
        junk launch doesn't permanently inflate the lifetime total either."""
        if not game or delta <= 0:
            return
        if game in self._weekly_game_breakdown:
            self._weekly_game_breakdown[game] = max(0, self._weekly_game_breakdown[game] - delta)
            if self._weekly_game_breakdown[game] == 0:
                del self._weekly_game_breakdown[game]
        if game in self._all_time_game_seconds:
            self._all_time_game_seconds[game] = max(0, self._all_time_game_seconds[game] - delta)
            if self._all_time_game_seconds[game] == 0:
                del self._all_time_game_seconds[game]

    @callback
    def _get_store_data(self):
        return {
            "history": self._play_history,
            "recent_sessions": getattr(self, "_recent_sessions", []),
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
                "last_session_play_time": self._last_session_play_time,
                "weekly_game_breakdown": self._weekly_game_breakdown,
                "longest_session_details": self._longest_session_details,
                "session_ticks": self._session_ticks_persistent,
                "blocked_seconds": self._active_elsewhere_blocked_seconds,
                "daily_play_time": getattr(self, "_daily_play_time", 0),
                "weekly_play_time": getattr(self, "_weekly_play_time", 0),
                "weekly_play_time_last_week": getattr(self, "_weekly_play_time_last_week", 0)
            },
            "cached_game_cover": getattr(self, "_cached_game_cover", None),
            "game_hero_art": getattr(self, "_cached_game_hero", None),
            "game_logo_art": getattr(self, "_cached_game_logo", None),
            "game_icon_art": getattr(self, "_cached_game_icon", None),
            "game_dominant_color": getattr(self, "_cached_game_color", None),
            "color_history_cache": getattr(self, "_color_history_cache", {}),
            "cached_game_rating": getattr(self, "_cached_game_rating", None),
            "all_time": {
                "game_seconds": getattr(self, "_all_time_game_seconds", {}),
                "session_count": getattr(self, "_all_time_session_count", 0),
                "seeded": getattr(self, "_all_time_seeded", False),
            },
        }

    def _check_daily_reset(self):
        now = dt_util.now()
        local_now = dt_util.as_local(now)
        current_date_str = local_now.strftime("%Y-%m-%d")
        current_week_str = local_now.strftime("%Y-%U") 
        
        history_changed = False
        
        if self._last_reset_date != current_date_str:
            if self._last_reset_date and self._daily_play_time > 0:
                self._play_history[self._last_reset_date] = {
                    "total_seconds": self._daily_play_time,
                    "game_breakdown": dict(self._weekly_game_breakdown),
                    "longest_session": dict(self._longest_session_details),
                    "week_str": current_week_str
                }
                history_changed = True
            
            cutoff_date = (local_now - timedelta(days=8)).date()
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
            
            self._weekly_game_breakdown = {}
            self._longest_session_details = {"game": None, "duration": 0}
            
            self._last_reset_date = current_date_str
            
        if self._last_weekly_reset != current_week_str:
            self._weekly_play_time_last_week = self._weekly_play_time
            self._weekly_play_time = 0
            self._last_weekly_reset = current_week_str

        if history_changed:
            self._cached_history_seconds = sum(
                day.get("total_seconds", day) if isinstance(day, dict) else day 
                for day in self._play_history.values()
            )
            self._store.async_delay_save(self._get_store_data, 5.0)

    def _is_ghost_session(self, game_name):
        if self._gaming_type != "xbox": return False
        if not game_name: return False
        if not self._ghosted_by: return False

        clean_target = _format_game_name_for_display(get_base_game_name(game_name))

        for ghost_entity_id in self._ghosted_by:
            state = self.hass.states.get(ghost_entity_id)
            if state is None:
                # Give referenced entities a brief window to finish being added
                # to hass after a restart/reload -- asyncio scheduling order
                # across many simultaneously-initializing entities isn't
                # guaranteed, so a miss in the first moments is expected, not
                # a misconfiguration. Don't mark it as warned during this
                # window, so a genuine miss still gets flagged once it ends.
                if dt_util.now() - self._init_time < timedelta(seconds=30):
                    continue
                if ghost_entity_id not in self._ghost_missing_warned:
                    self._ghost_missing_warned.add(ghost_entity_id)
                    _LOGGER.warning(
                        "Gaming Status: %s references %s (via suppresses_xbox_sensors) which no longer "
                        "exists -- this suppression rule will never trigger until it's corrected.",
                        self.entity_id, ghost_entity_id,
                    )
                continue
            # The entity exists again -- clear any earlier "missing" warning so a
            # genuine future disappearance (not just a one-time startup race,
            # e.g. this sensor updating before the referenced one finished being
            # added to hass) is still reported instead of staying silenced forever.
            self._ghost_missing_warned.discard(ghost_entity_id)
            other_game = state.attributes.get("current_game") or state.state
            clean_other = _format_game_name_for_display(get_base_game_name(other_game))
            # Exact match always counts, independent of the same-game-prefix
            # setting below, so disabling that heuristic never accidentally
            # disables ghosting for identically-named games.
            is_match = (
                _normalize_game_name(clean_target) == _normalize_game_name(clean_other)
                or _is_same_base_game(clean_target, clean_other, self._active_settings["SAME_GAME_PREFIX_WORDS"])
            )
            _LOGGER.debug(
                "Gaming Status: %s ghost-check target=%r vs %s (state=%r, current_game=%r, normalized_other=%r) -> match=%s",
                self.entity_id, clean_target, ghost_entity_id, state.state, state.attributes.get("current_game"), clean_other, is_match,
            )
            if state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, "Offline", "offline"] and is_match:
                return True
        return False

    def _is_game_active_elsewhere(self, current_game):
        if not current_game: return False

        try:
            # Clean the same way regardless of caller, so platforms that pass
            # raw/uncleaned presence text (Steam, PlayStation, Playnite, Discord)
            # compare on equal footing with Xbox, which already cleans its own
            # game name before calling this. Without this, e.g. Discord reporting
            # "DOOM Eternal In the menu" while Xbox reports "DOOM Eternal" would
            # never match, and both platforms would tick playtime independently
            # for the same real session (double-counting).
            clean_current = _format_game_name_for_display(get_base_game_name(current_game))
            my_priority = PLATFORM_PRIORITY.index(self._gaming_type) if self._gaming_type in PLATFORM_PRIORITY else 99

            for other_platform in PLATFORM_PRIORITY:
                if other_platform == self._gaming_type:
                    continue

                other_priority = PLATFORM_PRIORITY.index(other_platform)
                if other_priority > my_priority:
                    continue

                other_sensor_id = self._desired_entity_id.replace(f"_{self._gaming_type}", f"_{other_platform}")
                other_state = self.hass.states.get(other_sensor_id)

                if other_state and str(other_state.state).lower() not in ["offline", "unavailable", "unknown", "source missing"]:
                    other_game = other_state.attributes.get("current_game") or other_state.state
                    clean_other = _format_game_name_for_display(get_base_game_name(other_game))
                    # Exact match always counts (see comment in _is_ghost_session);
                    # the same-game-prefix heuristic is an additional, configurable
                    # layer for phase-text variants that survive the cleanup above.
                    if _normalize_game_name(clean_current) == _normalize_game_name(clean_other):
                        return True
                    if _is_same_base_game(clean_current, clean_other, self._active_settings["SAME_GAME_PREFIX_WORDS"]):
                        return True
        except Exception:
            pass

        return False

    def _apply_title_override(self, game_name):
        if not game_name: return game_name
        return utils.GAME_TITLE_OVERRIDES.get(_normalize_game_name(game_name), game_name)

    def _sanitize_game_title(self, title: str) -> str:
        """
        Strips trademark symbols and extra spaces from game titles.
        """
        if not title: return title
        # 1. Replace registered/trademark symbols with a space
        clean_title = re.sub(r'[™®©]', ' ', str(title))
        # 2. Replace multiple spaces with a single space and strip trailing whitespace
        clean_title = re.sub(r'\s+', ' ', clean_title).strip()
        return clean_title

    def _get_platform_data(self, state, attrs):
        data = { "is_online": False, "current_game": None, "game_cover_url": None, "gamertag": None, "avatar_url": None, "offline_reason": "standard", "xbox_suppressed": False }
        state_clean = str(state).lower().strip()
        if state_clean in ["none", ""]: return None
        normalized_state = state_clean.lower()
        
        if self._gaming_type == "custom":
            if normalized_state in ["0", "off", "offline", "false", "unavailable", "unknown", "0.0", "none", ""]:
                data["is_online"] = False
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
        norm_state = _normalize_game_name(state)
        if norm_state in self._global_exclusions_lower: is_globally_excluded = True
        is_user_excluded = norm_state in self._exclude_games
        is_basic_offline = state_clean in ["offline", "off", "disconnected", "0", "unavailable", "unknown", "0.0"]

        if self._gaming_type == "steam":
            if is_basic_offline: data["is_online"] = False
            elif normalized_state == "snooze":
                data["is_online"] = False
                data["offline_reason"] = "snooze"
            else:
                steam_game = attrs.get("game")
                if steam_game:
                    if self._is_game_active_elsewhere(steam_game):
                        data["is_online"] = False
                    else:
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
                    app_id = str(attrs.get("app_id") or attrs.get("game_id") or "")
                    if app_id and app_id.isdigit(): data["game_cover_url"] = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_hero.jpg"
            
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
            else:
                found_sibling = False
                sibling_id = self._xbox_now_playing_entity_id or (
                    self._source_entity_id.replace("_status", "_now_playing") if "_status" in self._source_entity_id else None
                )

                potential_game = state
                
                # Check the sibling FIRST. A valid game overrides an offline base state.
                if sibling_id:
                    sibling_state = self.hass.states.get(sibling_id)
                    if sibling_state and sibling_state.state.lower() not in ["unknown", "unavailable", "none", "", "offline"]:
                        potential_game = sibling_state.state
                        if sibling_state.attributes.get("entity_picture"):
                            data["game_cover_url"] = sibling_state.attributes.get("entity_picture")
                        found_sibling = True

                if attrs.get("game_queue_games") and not found_sibling: 
                    potential_game = attrs.get("game_queue_games")[0]
                    
                potential_game = get_base_game_name(potential_game)
                
                # If they are just online/home, AND we found no game in the sibling/queue, they are offline
                if not found_sibling and (is_basic_offline or state_clean in ["online", "home"]):
                    data["is_online"] = False
                elif self._is_ghost_session(potential_game):
                    data["is_online"] = False
                    data["xbox_suppressed"] = True
                elif self._is_game_active_elsewhere(potential_game):
                    data["is_online"] = False
                else:
                    data["is_online"] = True
                    data["current_game"] = potential_game
            
            data["gamertag"] = _get_gamertag_from_entity(self._source_entity_id, "xbox")
            
            # 1. Use Dynamic Registry Avatar
            if self._avatar_entity_id:
                xbox_img = self.hass.states.get(self._avatar_entity_id)
                if xbox_img and xbox_img.attributes.get("entity_picture"):
                    data["avatar_url"] = xbox_img.attributes.get("entity_picture")
            # 2. Fallback to String Guessing
            elif data["gamertag"]:
                safe_tag = data["gamertag"].lower().replace(" ", "_")
                xbox_img = self.hass.states.get(f"image.{safe_tag}_gamerpic")
                if xbox_img and xbox_img.attributes.get("entity_picture"):
                    data["avatar_url"] = xbox_img.attributes.get("entity_picture")

        elif self._gaming_type == "playstation":
            # 1. Use Dynamic Registry Avatar
            if self._avatar_entity_id:
                image_state = self.hass.states.get(self._avatar_entity_id)
                if image_state and image_state.attributes.get("entity_picture"):
                    data["avatar_url"] = image_state.attributes.get("entity_picture")
            # 2. Fallback to String Guessing — use translation_key to handle any locale's suffix
            else:
                try:
                    reg_entry = er.async_get(self.hass).async_get(self._source_entity_id)
                    tk = getattr(reg_entry, "translation_key", None) if reg_entry else None
                    object_id = self._source_entity_id.split('.')[1]
                    suffix = f"_{tk}" if tk else "_now_playing"
                    gamertag = None
                    if object_id.endswith(suffix):
                        gamertag = object_id[:-len(suffix)]
                    elif object_id.endswith("_now_playing"):
                        gamertag = object_id[:-len("_now_playing")]
                    if gamertag:
                        image_state = self.hass.states.get(f"image.{gamertag}_avatar")
                        if image_state and image_state.attributes.get("entity_picture"):
                            data["avatar_url"] = image_state.attributes.get("entity_picture")
                except Exception: pass

            # State IS the game title by default; unknown/unavailable means not
            # gaming -- unless a PS3 media_player sibling is configured and is
            # itself reporting "playing", in which case IT takes priority (PS3
            # predates the modern PSN status API, so it has no now_playing
            # sensor of its own -- it feeds into this same sensor instead).
            potential_game = state
            found_ps3 = False
            ps3_state = None
            if self._ps3_entity_id:
                ps3_state = self.hass.states.get(self._ps3_entity_id)
                if ps3_state and ps3_state.state.lower() == "playing":
                    ps3_title = ps3_state.attributes.get("media_title")
                    if ps3_title:
                        potential_game = ps3_title
                        found_ps3 = True

            # Recompute exclusion against whichever source actually applies --
            # is_globally_excluded/is_user_excluded above were derived from the
            # primary source's raw state, which is irrelevant once the PS3
            # sibling is the one actually supplying the game title.
            if _normalize_game_name(potential_game) in (self._global_exclusions_lower | self._exclude_games):
                data["is_online"] = False
            elif found_ps3 or state_clean not in ["unknown", "unavailable", "unknown game", "none", "", "offline"]:
                if self._is_game_active_elsewhere(potential_game):
                    data["is_online"] = False
                else:
                    data["is_online"] = True
                    data["current_game"] = potential_game
                    if found_ps3:
                        if ps3_state.attributes.get("entity_picture"):
                            data["game_cover_url"] = ps3_state.attributes.get("entity_picture")
                    elif attrs.get("entity_picture"):
                        data["game_cover_url"] = attrs.get("entity_picture")

        elif self._gaming_type == "playnite":
            # Apply the persistent Playnite logo as a base default if no custom image exists
            if not data.get("avatar_url"):
                data["avatar_url"] = "https://playnite.link/applogo.png"

            if is_globally_excluded or is_user_excluded or is_basic_offline:
                data["is_online"] = False
            else:
                # Adapter handles both direct MQTT JSON and custom HA Template states
                if state_clean == "on":
                    playnite_game = attrs.get("Name") or attrs.get("name") or "Unknown Playnite Game"
                else:
                    playnite_game = state
                if self._is_game_active_elsewhere(playnite_game):
                    data["is_online"] = False
                else:
                    data["is_online"] = True
                    data["current_game"] = playnite_game

        elif self._gaming_type == "discord":
            # Allow Discord to track games if an application_id is present
            app_id = str(attrs.get("application_id", ""))
            
            # If we have an app_id, we treat the state as the game name
            if app_id and state_clean not in ["offline", "online", "idle"]:
                # Suppress Discord if a console is currently active — Discord surfaces stale
                # console-game data (e.g. the last PS5 game) via its platform integrations,
                # and that data is unreliable when a console session is live.
                console_active = False
                for _cp in ["playstation", "xbox"]:
                    _cid = self._desired_entity_id.replace(f"_{self._gaming_type}", f"_{_cp}")
                    _cs = self.hass.states.get(_cid)
                    if _cs and str(_cs.state).lower() not in ["offline", "unavailable", "unknown", "source missing", "none", ""]:
                        console_active = True
                        break
                if self._is_game_active_elsewhere(state) or console_active:
                    data["is_online"] = False
                else:
                    data["is_online"] = True
                    data["current_game"] = state
            elif is_globally_excluded or is_user_excluded: 
                data["is_online"] = False
            else:
                data["is_online"] = False
            
            discord_data = attrs.get("discord_data", {})
            discord_user = discord_data.get("discord_user", {})
            avatar_hash = discord_user.get("avatar")
            user_id = discord_user.get("id")
            if avatar_hash and user_id:
                data["avatar_url"] = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"

        if data.get("is_online") and data.get("current_game"):
            # STEP 1 & 2: Get base name and apply manual overrides
            current_title = self._apply_title_override(get_base_game_name(data["current_game"]))
            
            # STEP 3: Check Exclusions BEFORE sanitizing (so exact text matches still work)
            if _normalize_game_name(current_title) in (self._global_exclusions_lower | self._exclude_games): 
                data["is_online"], data["current_game"] = False, None
            else:
                # STEP 4: Sanitize the final string to unify punctuation across platforms
                data["current_game"] = self._sanitize_game_title(current_title)
                
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
                session_ticks = self._session_ticks_persistent.get(self._current_game, 0)
                # Wall-clock time this segment spent blocked by "Active Elsewhere"
                # doesn't belong to this sensor's own totals at all -- exclude it
                # up front so it's neither discarded-vs-kept based on raw wall-clock
                # nor topped back up by the tick-reconciliation gap below (which
                # would otherwise silently re-credit exactly the time blocking was
                # meant to exclude).
                blocked_seconds = self._active_elsewhere_blocked_seconds.pop(self._current_game, 0)
                effective_seconds = max(0, session_seconds - blocked_seconds)
                if effective_seconds <= self._active_settings["MIN_SESSION_DURATION"]:
                    discarded_session = True
                    # Remove only what was actually accumulated via ticks for this segment.
                    self._daily_play_time = max(0, int((self._daily_play_time or 0) - session_ticks))
                    self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - session_ticks))
                    self._unbump_playtime(self._current_game, session_ticks)
                    if getattr(self, "_backup_last_session_time", None) is not None and self._backup_last_session_time > 0:
                        self._last_session_play_time = self._backup_last_session_time
                    if getattr(self, "_backup_last_online_timestamp", None) is not None:
                        self._last_online_valid_timestamp = self._backup_last_online_timestamp
                    if getattr(self, "_backup_last_played_game", None) is not None:
                        self._last_played_game = self._backup_last_played_game
                elif effective_seconds > 0:
                    self._last_session_play_time = int(effective_seconds)
                    # Reconcile wall-clock vs accumulated ticks. Any gap (HA restarts,
                    # event loop delays) is added to the breakdown so the chart matches
                    # the session time shown on the card.
                    gap = int(effective_seconds) - session_ticks
                    if gap != 0:
                        _LOGGER.debug(
                            "Gaming Status: %s session-close reconciliation for %r -- session_ticks=%s, blocked_seconds=%s, effective_seconds=%s, gap=%s",
                            self.entity_id, self._current_game, session_ticks, blocked_seconds, effective_seconds, gap,
                        )
                    if gap > 0:
                        self._bump_playtime(self._current_game, gap)
                        self._daily_play_time = int((self._daily_play_time or 0) + gap)
                        self._weekly_play_time = int((self._weekly_play_time or 0) + gap)
                    elif gap < 0:
                        # Ticks over-counted this segment (e.g. a game transition
                        # landed mid-tick-interval, crediting part of another
                        # game's time to this one) -- correct back down so the
                        # total credited here always matches this session's
                        # recorded duration_seconds exactly. Without this,
                        # deleting every recorded session for a game could still
                        # leave a nonzero residual in the running totals.
                        self._unbump_playtime(self._current_game, -gap)
                        self._daily_play_time = max(0, int((self._daily_play_time or 0) + gap))
                        self._weekly_play_time = max(0, int((self._weekly_play_time or 0) + gap))

                    # Log this completed session for the "recent_sessions" history.
                    # Gated only on effective_seconds > 0 (guaranteed by the
                    # enclosing branch, and already above MIN_SESSION_DURATION) --
                    # NOT on session_ticks, which can legitimately stay at 0 even
                    # for a genuinely-credited segment whenever ticks fall behind
                    # wall-clock time (blocking, connectivity blips, timing delays)
                    # and get reconciled via the gap top-up above. Gating on ticks
                    # used to assume "ticks==0 always means a fully-blocked
                    # duplicate," but that's not true once a partial/near-total gap
                    # gets topped up -- it just silently dropped the session row
                    # while still crediting the aggregate totals.
                    # Note: a brief network blip after 5+ minutes of play can end a
                    # session here and have it "resurrect" as a new one below, which
                    # will show as two rows instead of one continuous session - a
                    # known, accepted edge case (same tradeoff the aggregate totals
                    # above already make in favor of not losing playtime).
                    if self._current_game:
                        self._all_time_session_count = getattr(self, "_all_time_session_count", 0) + 1
                        local_start = dt_util.as_local(start_dt)
                        local_end = dt_util.as_local(actual_end_time)
                        self._recent_sessions.insert(0, {
                            "game": self._current_game,
                            "platform": PLATFORM_CONFIG.get(self._gaming_type, {}).get("name_suffix", self._gaming_type.title()),
                            "duration_seconds": self._last_session_play_time,
                            "date": local_start.strftime("%Y-%m-%d"),
                            "start_time": local_start.isoformat(),
                            "end_time": local_end.isoformat(),
                            "hero_art_url": self._cached_game_hero,
                            "game_dominant_color": self._cached_game_color,
                        })
                        if len(self._recent_sessions) > MAX_RECENT_SESSIONS:
                            del self._recent_sessions[MAX_RECENT_SESSIONS:]
                        self._store.async_delay_save(self._get_store_data, 5.0)
        if not new_game_name:
            if not discarded_session: self._last_game_stopped_timestamp = actual_end_time.isoformat()
            elif getattr(self, "_backup_last_game_stopped_timestamp", None) is not None: self._last_game_stopped_timestamp = self._backup_last_game_stopped_timestamp
            self._current_game = None
            self._play_start_time = None
            self._session_ticks_persistent = {}
            self._active_elsewhere_blocked_seconds = {}
        else:
            self._current_game = new_game_name
            self._play_start_time = now.isoformat()
            self._session_ticks_persistent = {}
            self._active_elsewhere_blocked_seconds = {}
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
                    # Seed session ticks with the previous segment's time so the
                    # reconciliation at merged session end doesn't re-add already-stored time.
                    self._session_ticks_persistent[new_game_name] = self._last_session_play_time
            self._backup_last_session_time = self._last_session_play_time
            self._backup_last_online_timestamp = self._last_online_valid_timestamp
            self._backup_last_played_game = self._last_played_game
            self._backup_last_game_stopped_timestamp = getattr(self, "_last_game_stopped_timestamp", None)
            self._last_session_play_time = 0
            self._cover_fetch_attempted = False
            
            self._cached_game_cover = None
            self._cached_game_hero = None
            self._cached_game_logo = None
            self._cached_game_icon = None
            self._cached_game_color = None
            self._rating_fetch_attempted = False
            self._cached_game_rating = None
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
                
        # Apply overrides first, then sanitize the result
        return self._sanitize_game_title(self._apply_title_override(clean))

    def _write_common_attributes(self, secondary="", timer_status=None, game_cover=None, xbox_suppressed=None):
        
        # --- IMPROVED SELF HEALING: Recover true start time across HA reboots ---
        if self._current_game:
            if not getattr(self, "_play_start_time", None):
                recovered_str = self._attr_extra_state_attributes.get("play_start_time")
                
                # If not in the current attributes, ask the HA state machine for the last known state
                if not recovered_str and getattr(self, "entity_id", None):
                    old_state = self.hass.states.get(self.entity_id)
                    if old_state:
                        recovered_str = old_state.attributes.get("play_start_time")
                        
                if recovered_str:
                    try:
                        # We successfully recovered the text string from the database
                        self._play_start_time = recovered_str
                    except Exception:
                        pass
                        
                # Absolute fallback if the game was genuinely started during the HA downtime.
                # Only apply if the last valid online timestamp is recent (within the grace period), so stale offline players don't get a fresh start time on restart.
                if not getattr(self, "_play_start_time", None) and timer_status in ("Running", "Paused (Grace Period)"):
                    is_recent = False
                    if getattr(self, "_last_online_valid_timestamp", None):
                        last_ts = _safe_parse_datetime(self._last_online_valid_timestamp)
                        if last_ts:
                            age = (dt_util.now() - last_ts).total_seconds()
                            if age < self._active_settings.get("GRACE_PERIOD_SECONDS", 180):
                                is_recent = True
                    # If there is no previous timestamp at all, assume it is a brand new session
                    elif not getattr(self, "_last_online_valid_timestamp", None):
                        is_recent = True
                        
                    if is_recent:
                        self._play_start_time = dt_util.now()
                    
            # PREVENT CRASHES: Ensure we only format datetime objects, not strings!
            if getattr(self, "_play_start_time", None):
                if isinstance(self._play_start_time, datetime):
                    self._attr_extra_state_attributes["play_start_time"] = self._play_start_time.isoformat()
                else:
                    self._attr_extra_state_attributes["play_start_time"] = str(self._play_start_time)
            
        if timer_status: self._attr_extra_state_attributes["timer_status"] = timer_status
        if xbox_suppressed is not None: self._attr_extra_state_attributes["xbox_suppressed"] = xbox_suppressed
        self._attr_extra_state_attributes["current_game"] = self._current_game
        
        if secondary:
            self._attr_extra_state_attributes["secondary"] = secondary

        # Write artwork and color directly from RAM to the state machine. 
        self._attr_extra_state_attributes["game_cover_art"] = game_cover or self._cached_game_cover
        self._attr_extra_state_attributes["game_hero_art"] = self._cached_game_hero
        self._attr_extra_state_attributes["game_logo_art"] = self._cached_game_logo
        self._attr_extra_state_attributes["game_icon_art"] = self._cached_game_icon
        self._attr_extra_state_attributes["game_dominant_color"] = self._cached_game_color
        self._attr_extra_state_attributes["cached_game_cover"] = self._cached_game_cover
        self._attr_extra_state_attributes["game_content_rating"] = self._cached_game_rating
        # Global integration setting, exposed so cards can hide dynamic-color
        # options entirely when color extraction is disabled instead of
        # showing a mode that can never produce a color.
        self._attr_extra_state_attributes["color_extraction_enabled"] = utils.ENABLE_VIBRANT_COLOR

        # --- CRITICAL FIX: Flush internal RAM variables to HA attributes ---
        self._attr_extra_state_attributes["last_online_valid_timestamp"] = getattr(self, "_last_online_valid_timestamp", None)
        self._attr_extra_state_attributes["last_played_game"] = getattr(self, "_last_played_game", None)
        self._attr_extra_state_attributes["daily_play_time"] = getattr(self, "_daily_play_time", 0)
        self._attr_extra_state_attributes["weekly_play_time"] = getattr(self, "_weekly_play_time", 0)
        self._attr_extra_state_attributes["weekly_play_time_last_week"] = getattr(self, "_weekly_play_time_last_week", 0)

        # Build rolling 7-day and calendar-week breakdowns from history + today
        rolling_breakdown = dict(getattr(self, "_weekly_game_breakdown", {}))
        local_today = dt_util.as_local(dt_util.now()).date()
        week_start = local_today - timedelta(days=local_today.weekday())
        calendar_breakdown = dict(getattr(self, "_weekly_game_breakdown", {}))

        today_longest = getattr(self, "_longest_session_details", {"game": None, "duration": 0})
        rolling_longest = dict(today_longest)
        calendar_longest = dict(today_longest)

        if hasattr(self, "_play_history"):
            for date_str, day_data in self._play_history.items():
                if not isinstance(day_data, dict):
                    continue
                in_cal_week = False
                try:
                    in_cal_week = parser.parse(date_str).date() >= week_start
                except Exception:
                    pass
                if "game_breakdown" in day_data:
                    for game, secs in day_data["game_breakdown"].items():
                        rolling_breakdown[game] = rolling_breakdown.get(game, 0) + secs
                        if in_cal_week:
                            calendar_breakdown[game] = calendar_breakdown.get(game, 0) + secs
                hist_longest = day_data.get("longest_session", {})
                if isinstance(hist_longest, dict):
                    if hist_longest.get("duration", 0) > rolling_longest.get("duration", 0):
                        rolling_longest = hist_longest
                    if in_cal_week and hist_longest.get("duration", 0) > calendar_longest.get("duration", 0):
                        calendar_longest = hist_longest

        self._attr_extra_state_attributes["weekly_game_breakdown"] = rolling_breakdown
        self._attr_extra_state_attributes["rolling_weekly_breakdown"] = rolling_breakdown
        self._attr_extra_state_attributes["calendar_weekly_breakdown"] = calendar_breakdown
        self._attr_extra_state_attributes["longest_session_details"] = today_longest
        self._attr_extra_state_attributes["rolling_longest_session_details"] = rolling_longest
        self._attr_extra_state_attributes["calendar_longest_session_details"] = calendar_longest
        
        # Expose per-day game breakdown for history cards
        today_str = dt_util.as_local(dt_util.now()).strftime("%Y-%m-%d")
        history_attr = {}
        if hasattr(self, "_play_history"):
            for date_str, day_data in self._play_history.items():
                if isinstance(day_data, dict):
                    history_attr[date_str] = day_data.get("game_breakdown", {})
        history_attr[today_str] = dict(getattr(self, "_weekly_game_breakdown", {}))
        self._attr_extra_state_attributes["play_history"] = dict(sorted(history_attr.items()))
        self._attr_extra_state_attributes["recent_sessions"] = list(getattr(self, "_recent_sessions", []))

        # All-time (lifetime) summary. Only a small, bounded summary is ever
        # exposed here -- the full per-game breakdown lives only in the JSON
        # store, never as a live attribute.
        all_time_seconds = getattr(self, "_all_time_game_seconds", {})
        self._attr_extra_state_attributes["all_time_total_hours"] = round(sum(all_time_seconds.values()) / 3600, 1)
        self._attr_extra_state_attributes["all_time_session_count"] = getattr(self, "_all_time_session_count", 0)
        self._attr_extra_state_attributes["all_time_top_games"] = top_n_games(all_time_seconds, 20)

    def _game_name_matches(self, stored_name, clean_target):
        """True if stored_name refers to the same game as clean_target (an
        already get_base_game_name/_format_game_name_for_display-cleaned
        name). Mirrors _is_game_active_elsewhere's exact-plus-prefix-heuristic
        matching, since two platforms can record genuinely different raw
        spellings for the identical game (e.g. a colon variant, trademark
        symbol, or dash suffix) even after each platform's own cleanup -- a
        name picked from one platform's spelling must still match another
        platform's differently-formatted entry for the same game."""
        clean_stored = _format_game_name_for_display(get_base_game_name(stored_name))
        if _normalize_game_name(clean_stored) == _normalize_game_name(clean_target):
            return True
        return _is_same_base_game(clean_stored, clean_target, self._active_settings["SAME_GAME_PREFIX_WORDS"])

    async def async_rename_game(self, old_name, new_name):
        """Rename a game across stored history (merging into new_name if it already exists). Does not touch the live/in-progress session."""
        clean_target = _format_game_name_for_display(get_base_game_name(old_name))
        clean_new = _format_game_name_for_display(get_base_game_name(new_name))
        renamed = False

        for entry in self._recent_sessions:
            if self._game_name_matches(entry.get("game"), clean_target):
                entry["game"] = new_name
                renamed = True

        def _merge_rename(d):
            nonlocal renamed
            match_key = next((k for k in d if self._game_name_matches(k, clean_target)), None)
            if match_key is None:
                return
            seconds = d.pop(match_key)
            # Merge into whichever key already represents new_name (matched the
            # same punctuation/case-insensitive way as old_name above), so a
            # rename onto an existing entry actually merges instead of creating
            # a second, differently-spelled duplicate of the same game.
            existing_key = next((k for k in d if self._game_name_matches(k, clean_new)), None)
            target_key = existing_key if existing_key is not None else new_name
            d[target_key] = d.get(target_key, 0) + seconds
            renamed = True

        _merge_rename(self._weekly_game_breakdown)
        _merge_rename(self._all_time_game_seconds)
        for day_data in self._play_history.values():
            if isinstance(day_data, dict) and isinstance(day_data.get("game_breakdown"), dict):
                _merge_rename(day_data["game_breakdown"])
                hist_longest = day_data.get("longest_session")
                if isinstance(hist_longest, dict) and self._game_name_matches(hist_longest.get("game"), clean_target):
                    hist_longest["game"] = new_name
                    renamed = True

        if self._game_name_matches(self._longest_session_details.get("game"), clean_target):
            self._longest_session_details["game"] = new_name
            renamed = True
        if self._game_name_matches(self._last_played_game, clean_target):
            self._last_played_game = new_name
            renamed = True

        if not renamed:
            _LOGGER.warning("Gaming Status: rename_game found no match for %r on %s", old_name, self.entity_id)
            return

        self._write_common_attributes()
        await self._store.async_save(self._get_store_data())
        self.async_write_ha_state()

    async def async_delete_game(self, game):
        """Permanently purge every trace of a named game from stored history."""
        clean_target = _format_game_name_for_display(get_base_game_name(game))
        purged = False

        # 1. Visible session log. recent_sessions is capped at MAX_RECENT_SESSIONS
        #    across all games, so a heavily-played game's true total may exceed
        #    what's visible here -- steps 2-3 correct the game-keyed totals
        #    directly rather than relying on summing this list.
        before = len(self._recent_sessions)
        self._recent_sessions = [s for s in self._recent_sessions if not self._game_name_matches(s.get("game"), clean_target)]
        purged = purged or (len(self._recent_sessions) != before)

        # 2. Today's live per-game breakdown + daily/weekly totals
        match_key = next((k for k in self._weekly_game_breakdown if self._game_name_matches(k, clean_target)), None)
        if match_key is not None:
            today_secs = self._weekly_game_breakdown.pop(match_key)
            self._daily_play_time = max(0, int((self._daily_play_time or 0) - today_secs))
            self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - today_secs))
            purged = True

        # 2b. All-time (lifetime) total -- never pruned like weekly/history, so
        #     it must be purged explicitly here too, or "permanently purge
        #     every trace" above wouldn't actually be true for this game.
        match_key = next((k for k in self._all_time_game_seconds if self._game_name_matches(k, clean_target)), None)
        if match_key is not None:
            self._all_time_game_seconds.pop(match_key)
            purged = True

        # 3. Archived days
        for date_str, day_data in list(self._play_history.items()):
            if not isinstance(day_data, dict):
                continue
            gb = day_data.get("game_breakdown", {})
            match_key = next((k for k in gb if self._game_name_matches(k, clean_target)), None)
            if match_key is None:
                continue
            secs = gb.pop(match_key)
            day_data["total_seconds"] = max(0, day_data.get("total_seconds", 0) - secs)
            # weekly_play_time accumulates across the whole current week
            # independent of the daily reset, so an archived-but-still-this-week
            # day must also correct it.
            if day_data.get("week_str") == self._last_weekly_reset:
                self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - secs))
            purged = True
            if not gb and day_data.get("total_seconds", 0) == 0:
                del self._play_history[date_str]

        # 4. Longest-session records that referenced this game
        if self._game_name_matches(self._longest_session_details.get("game"), clean_target):
            remaining_today = [s for s in self._recent_sessions if s.get("date") == self._last_reset_date]
            if remaining_today:
                best = max(remaining_today, key=lambda s: s.get("duration_seconds", 0))
                self._longest_session_details = {"game": best["game"], "duration": best["duration_seconds"]}
            else:
                self._longest_session_details = {"game": None, "duration": 0}
        for day_data in self._play_history.values():
            hist_longest = day_data.get("longest_session") if isinstance(day_data, dict) else None
            if isinstance(hist_longest, dict) and self._game_name_matches(hist_longest.get("game"), clean_target):
                day_data["longest_session"] = {"game": None, "duration": 0}

        # 5. last_played_game
        if self._game_name_matches(self._last_played_game, clean_target):
            self._last_played_game = None
            purged = True

        if not purged:
            _LOGGER.warning("Gaming Status: delete_game found no match for %r on %s", game, self.entity_id)
            return

        self._write_common_attributes()
        await self._store.async_save(self._get_store_data())
        self.async_write_ha_state()

    def get_session_entry(self, game, start_time):
        """Read-only lookup of one specific recorded session in this sensor's
        own recent_sessions, by (fuzzy-matched) game name + exact start_time.
        Shared by async_delete_session and the reassign_session service
        handler (which needs to find the source entry without removing it
        until the destination add has already succeeded)."""
        clean_target = _format_game_name_for_display(get_base_game_name(game))
        return next(
            (s for s in self._recent_sessions
             if self._game_name_matches(s.get("game"), clean_target) and s.get("start_time") == start_time),
            None
        )

    async def async_delete_session(self, game, start_time, quiet_if_missing=False):
        """Permanently remove one specific recorded session, correcting only
        that session's contribution to daily/weekly/all-time totals -- unlike
        async_delete_game, this leaves every other session of the same game
        untouched. `start_time` is matched exactly (verbatim string), since
        within a single sensor's own recent_sessions list it's guaranteed
        unique -- _handle_game_transition only ever appends one entry per
        completed segment, sequentially, so two sessions can't collide.

        `quiet_if_missing` is set when this call is part of an "All Platforms"
        fan-out (no platform explicitly chosen) -- a session can only ever
        live on one platform's own history, so a miss on every other platform
        is guaranteed and not worth a warning. An explicit, single-platform
        miss is still logged, since that's a genuine anomaly."""
        clean_target = _format_game_name_for_display(get_base_game_name(game))

        target_entry = self.get_session_entry(game, start_time)
        if target_entry is None:
            if quiet_if_missing:
                _LOGGER.debug("Gaming Status: delete_session found no match for %r at %r on %s", game, start_time, self.entity_id)
            else:
                _LOGGER.warning("Gaming Status: delete_session found no match for %r at %r on %s", game, start_time, self.entity_id)
            return

        self._recent_sessions.remove(target_entry)
        secs = target_entry.get("duration_seconds", 0)
        date_str = target_entry.get("date")

        # All-time (lifetime) total is never pruned, so it must be corrected
        # regardless of which day this session falls on.
        match_key = next((k for k in self._all_time_game_seconds if self._game_name_matches(k, clean_target)), None)
        if match_key is not None:
            self._all_time_game_seconds[match_key] = max(0, self._all_time_game_seconds[match_key] - secs)
            if self._all_time_game_seconds[match_key] == 0:
                del self._all_time_game_seconds[match_key]

        if date_str == self._last_reset_date:
            # Today's still-live per-game breakdown + daily/weekly totals.
            match_key = next((k for k in self._weekly_game_breakdown if self._game_name_matches(k, clean_target)), None)
            if match_key is not None:
                self._weekly_game_breakdown[match_key] = max(0, self._weekly_game_breakdown[match_key] - secs)
                if self._weekly_game_breakdown[match_key] == 0:
                    del self._weekly_game_breakdown[match_key]
            self._daily_play_time = max(0, int((self._daily_play_time or 0) - secs))
            self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - secs))
        elif date_str in self._play_history:
            day_data = self._play_history[date_str]
            if isinstance(day_data, dict):
                gb = day_data.get("game_breakdown", {})
                match_key = next((k for k in gb if self._game_name_matches(k, clean_target)), None)
                if match_key is not None:
                    gb[match_key] = max(0, gb[match_key] - secs)
                    if gb[match_key] == 0:
                        del gb[match_key]
                    day_data["total_seconds"] = max(0, day_data.get("total_seconds", 0) - secs)
                    # weekly_play_time accumulates across the whole current week
                    # independent of the daily reset, so an archived-but-still-
                    # this-week day must also correct it.
                    if day_data.get("week_str") == self._last_weekly_reset:
                        self._weekly_play_time = max(0, int((self._weekly_play_time or 0) - secs))
                    if not gb and day_data.get("total_seconds", 0) == 0:
                        del self._play_history[date_str]
        # else: this session had already aged past both "today" and the
        # 8-day play_history window (recent_sessions has its own, separate
        # 20-slot cap) -- only the all-time correction above applies.

        # Longest-session records that referenced exactly this session.
        # Matched by exact (game, duration) equality against the entry we
        # already have in hand, not the fuzzy _game_name_matches used above,
        # since precision matters more here than title-variant tolerance.
        # Accepted, documented limitation (same class async_delete_game
        # already has): on an archived day there's no per-session log left
        # to recompute from, so two same-duration sessions of the same game
        # on the same day could occasionally blank a still-valid record.
        if (self._longest_session_details.get("game") == target_entry.get("game")
                and self._longest_session_details.get("duration") == secs):
            remaining_today = [s for s in self._recent_sessions if s.get("date") == self._last_reset_date]
            if remaining_today:
                best = max(remaining_today, key=lambda s: s.get("duration_seconds", 0))
                self._longest_session_details = {"game": best["game"], "duration": best["duration_seconds"]}
            else:
                self._longest_session_details = {"game": None, "duration": 0}
        if date_str in self._play_history:
            day_data = self._play_history[date_str]
            hist_longest = day_data.get("longest_session") if isinstance(day_data, dict) else None
            if (isinstance(hist_longest, dict)
                    and hist_longest.get("game") == target_entry.get("game")
                    and hist_longest.get("duration") == secs):
                day_data["longest_session"] = {"game": None, "duration": 0}

        # last_played_game is refreshed continuously while a game is actively
        # being played (not just at session end), so it almost always
        # mirrors whichever session is most likely to be the one just
        # deleted. Unlike async_delete_game (which can clear it
        # unconditionally since it purges everything at once), only clear it
        # here if this game genuinely has zero traces left anywhere.
        if self._game_name_matches(self._last_played_game, clean_target):
            still_exists = (
                any(self._game_name_matches(s.get("game"), clean_target) for s in self._recent_sessions)
                or any(self._game_name_matches(k, clean_target) for k in self._weekly_game_breakdown)
                or any(self._game_name_matches(k, clean_target) for k in self._all_time_game_seconds)
                or any(
                    isinstance(d, dict) and any(self._game_name_matches(k, clean_target) for k in d.get("game_breakdown", {}))
                    for d in self._play_history.values()
                )
            )
            if not still_exists:
                self._last_played_game = None

        self._write_common_attributes()
        await self._store.async_save(self._get_store_data())
        self.async_write_ha_state()

    async def async_add_session(self, game, start_time, end_time, hero_art_url=None, game_dominant_color=None):
        """Manually insert a completed session into this sensor's history --
        the mirror image of async_delete_session. Credits the exact same
        buckets that method debits: recent_sessions, weekly/all-time totals,
        play_history (today's live buckets, an existing archived day, or a
        freshly-synthesized one), and longest-session records. If the caller
        doesn't already have art to pass through (e.g. reassign_session,
        which carries over the original session's real art), a best-effort
        SteamGridDB lookup is attempted here since utils.fetch_game_assets is
        self-contained and safe to call outside the live tracking flow --
        unlike dominant-color extraction, which needs a locally-cached image
        file and isn't worth reproducing for a one-off manual entry, so
        game_dominant_color stays None unless explicitly passed in."""
        clean_title = _format_game_name_for_display(get_base_game_name(game))
        start_dt = _safe_parse_datetime(start_time)
        end_dt = _safe_parse_datetime(end_time)
        if not start_dt or not end_dt or end_dt <= start_dt:
            _LOGGER.warning("Gaming Status: add_session got an invalid time range (%r -> %r) for %r on %s", start_time, end_time, game, self.entity_id)
            return

        if hero_art_url is None:
            try:
                fetched = await utils.fetch_game_assets(self.hass, clean_title)
                # Matches organic sessions exactly: hero_art_url only ever
                # holds the "hero" (wide) asset, never "grid" (cover-style,
                # different aspect ratio) as a substitute.
                hero_art_url = fetched.get("hero")
            except Exception as e:
                _LOGGER.error("Gaming Status: artwork fetch failed for manually-added session %r: %s", clean_title, e)

        local_start = dt_util.as_local(start_dt)
        local_end = dt_util.as_local(end_dt)
        secs = int((local_end - local_start).total_seconds())
        date_str = local_start.strftime("%Y-%m-%d")

        self._recent_sessions.insert(0, {
            "game": clean_title,
            "platform": PLATFORM_CONFIG.get(self._gaming_type, {}).get("name_suffix", self._gaming_type.title()),
            "duration_seconds": secs,
            "date": date_str,
            "start_time": local_start.isoformat(),
            "end_time": local_end.isoformat(),
            "hero_art_url": hero_art_url,
            "game_dominant_color": game_dominant_color,
        })
        if len(self._recent_sessions) > MAX_RECENT_SESSIONS:
            del self._recent_sessions[MAX_RECENT_SESSIONS:]

        self._all_time_session_count = getattr(self, "_all_time_session_count", 0) + 1

        # All-time total is never pruned, so it's credited regardless of date --
        # find an existing (possibly differently-cased/spelled) bucket to merge
        # into, same fuzzy lookup async_delete_session uses, falling back to
        # this normalized title as a brand-new key if none matches.
        match_key = next((k for k in self._all_time_game_seconds if self._game_name_matches(k, clean_title)), clean_title)
        self._all_time_game_seconds[match_key] = self._all_time_game_seconds.get(match_key, 0) + secs

        if date_str == self._last_reset_date:
            # Today's still-live per-game breakdown + daily/weekly totals.
            match_key = next((k for k in self._weekly_game_breakdown if self._game_name_matches(k, clean_title)), clean_title)
            self._weekly_game_breakdown[match_key] = self._weekly_game_breakdown.get(match_key, 0) + secs
            self._daily_play_time = int((self._daily_play_time or 0) + secs)
            self._weekly_play_time = int((self._weekly_play_time or 0) + secs)
            if secs > self._longest_session_details.get("duration", 0):
                self._longest_session_details = {"game": clean_title, "duration": secs}
        elif date_str in self._play_history and isinstance(self._play_history[date_str], dict):
            day_data = self._play_history[date_str]
            gb = day_data.setdefault("game_breakdown", {})
            match_key = next((k for k in gb if self._game_name_matches(k, clean_title)), clean_title)
            gb[match_key] = gb.get(match_key, 0) + secs
            day_data["total_seconds"] = day_data.get("total_seconds", 0) + secs
            # weekly_play_time accumulates across the whole current week
            # independent of the daily reset, so an archived-but-still-
            # this-week day must also be credited here.
            if day_data.get("week_str") == self._last_weekly_reset:
                self._weekly_play_time = int((self._weekly_play_time or 0) + secs)
            hist_longest = day_data.get("longest_session") or {}
            if secs > hist_longest.get("duration", 0):
                day_data["longest_session"] = {"game": clean_title, "duration": secs}
        else:
            # No existing archived entry for this date at all -- synthesize a
            # fresh one in the same shape organic daily-reset archiving uses.
            week_str = local_start.strftime("%Y-%U")
            self._play_history[date_str] = {
                "total_seconds": secs,
                "game_breakdown": {clean_title: secs},
                "longest_session": {"game": clean_title, "duration": secs},
                "week_str": week_str,
            }
            if week_str == self._last_weekly_reset:
                self._weekly_play_time = int((self._weekly_play_time or 0) + secs)

        # Only advance last_played_game/last_online_valid_timestamp if this
        # session is chronologically the most recent thing tracked -- a
        # backfilled OLDER session shouldn't override what's already
        # correctly showing as "last played."
        current_last_ts = _safe_parse_datetime(self._last_online_valid_timestamp) if self._last_online_valid_timestamp else None
        if not current_last_ts or local_end > current_last_ts:
            self._last_played_game = clean_title
            self._last_online_valid_timestamp = local_end.isoformat()
            self._last_session_play_time = secs

        self._write_common_attributes()
        await self._store.async_save(self._get_store_data())
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        
        # --- DYNAMIC DEVICE REGISTRY DISCOVERY ---
        registry = er.async_get(self.hass)
        entry = registry.async_get(self._source_entity_id)
        if entry and entry.device_id:
            device_entries = er.async_entries_for_device(registry, entry.device_id)
            for e in device_entries:
                if e.domain == "image":
                    # Explicitly hunt for the correct image type based on the platform!
                    if self._gaming_type == "xbox" and "gamerpic" in e.entity_id:
                        self._avatar_entity_id = e.entity_id
                    elif self._gaming_type == "playstation" and "avatar" in e.entity_id:
                        self._avatar_entity_id = e.entity_id
                elif e.domain == "sensor" and self._gaming_type == "xbox" and getattr(e, "translation_key", None) == "now_playing":
                    self._xbox_now_playing_entity_id = e.entity_id
        stored_data = await self._store.async_load()
        if stored_data:
            self._play_history = stored_data.get("history", {})
            self._recent_sessions = stored_data.get("recent_sessions", [])
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
            self._weekly_game_breakdown = internal.get("weekly_game_breakdown", {})
            self._longest_session_details = internal.get("longest_session_details", {"game": None, "duration": 0})
            self._session_ticks_persistent = internal.get("session_ticks", {})
            self._active_elsewhere_blocked_seconds = internal.get("blocked_seconds", {})

            all_time = stored_data.get("all_time", {})
            self._all_time_game_seconds = all_time.get("game_seconds", {})
            if not isinstance(self._all_time_game_seconds, dict):
                self._all_time_game_seconds = {}
            self._all_time_session_count = int(all_time.get("session_count", 0) or 0)
            self._all_time_seeded = bool(all_time.get("seeded", False))

            # Restore live running tallies from JSON backup if RAM wipe occurs
            self._daily_play_time = int(internal.get("daily_play_time", 0))
            self._weekly_play_time = int(internal.get("weekly_play_time", 0))
            self._weekly_play_time_last_week = int(internal.get("weekly_play_time_last_week", 0))
            
            self._cached_game_cover = stored_data.get("cached_game_cover")
            self._cached_game_hero = stored_data.get("game_hero_art")
            self._cached_game_logo = stored_data.get("game_logo_art")
            self._cached_game_icon = stored_data.get("game_icon_art")
            self._cached_game_rating = stored_data.get("cached_game_rating")
            _raw_color = stored_data.get("game_dominant_color")
            if _raw_color and not re.match(r'^#[0-9A-Fa-f]{6}$', str(_raw_color)):
                _raw_color = None
            self._cached_game_color = _raw_color
            raw_color_history = stored_data.get("color_history_cache", {})
            self._color_history_cache = {}
            for k, v in raw_color_history.items():
                if isinstance(v, dict) and "color" in v and re.match(r'^#[0-9A-Fa-f]{6}$', str(v["color"])):
                    self._color_history_cache[k] = v
                elif isinstance(v, str) and re.match(r'^#[0-9A-Fa-f]{6}$', str(v)):
                    # Backwards compatibility for the old string-based cache
                    self._color_history_cache[k] = {"color": v, "timestamp": 0}
        else:
            self._play_history = {}
            self._recent_sessions = []
            self._color_history_cache = {}
            self._backup_last_session_time = 0
            self._backup_last_online_timestamp = None
            self._backup_last_played_game = None
            self._backup_last_game_stopped_timestamp = None
            self._temp_offline_start = None
            self._daily_play_time_yesterday = 0
            self._last_reset_date = None
            self._last_weekly_reset = None
            self._last_session_play_time = 0
            self._weekly_game_breakdown = {}
            self._longest_session_details = {"game": None, "duration": 0}
            self._all_time_game_seconds = {}
            self._all_time_session_count = 0
            self._all_time_seeded = False

        if not getattr(self, "_all_time_seeded", False):
            # One-time backfill for installs upgrading from before this feature
            # existed. play_history only ever holds ~8 days, so this is a
            # best-effort starting point, not a true reconstruction of
            # playtime that already aged out before now.
            for day_data in self._play_history.values():
                if isinstance(day_data, dict):
                    for game, secs in day_data.get("game_breakdown", {}).items():
                        if isinstance(secs, (int, float)):
                            self._all_time_game_seconds[game] = self._all_time_game_seconds.get(game, 0) + int(secs)
            for game, secs in self._weekly_game_breakdown.items():
                if isinstance(secs, (int, float)):
                    self._all_time_game_seconds[game] = self._all_time_game_seconds.get(game, 0) + int(secs)
            if not self._all_time_session_count:
                self._all_time_session_count = len(self._recent_sessions)
            self._all_time_seeded = True
            self._store.async_delay_save(self._get_store_data, 5.0)

        self._cached_history_seconds = sum(
            day.get("total_seconds", day) if isinstance(day, dict) else day 
            for day in self._play_history.values()
        )

        def _check_local_avatar():
            from homeassistant.helpers.network import get_url
            try:
                base_url = get_url(self.hass, prefer_external=True)
            except Exception:
                base_url = ""
            safe_name = re.sub(r'[^a-z0-9_]', '', self._owner_name.lower().replace(" ", "_"))
            for ext in ['png', 'jpg']:
                file_name = f"{self._gaming_type}_{safe_name}_avatar.{ext}"
                if file_name in self._available_avatars: return f"{base_url}/local/gaming_status/{file_name}"
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
            
            if not getattr(self, "_cached_game_cover", None): self._cached_game_cover = attrs.get("cached_game_cover")
            if not getattr(self, "_cached_game_hero", None): self._cached_game_hero = attrs.get("game_hero_art")
            if not getattr(self, "_cached_game_logo", None): self._cached_game_logo = attrs.get("game_logo_art")
            if not getattr(self, "_cached_game_icon", None): self._cached_game_icon = attrs.get("game_icon_art")
            if not getattr(self, "_cached_game_rating", None): self._cached_game_rating = attrs.get("game_content_rating")

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
                is_stale = False
                last_ts = None
                if self._last_online_valid_timestamp:
                    last_ts = _safe_parse_datetime(self._last_online_valid_timestamp)
                    if last_ts:
                        now_dt = dt_util.now()
                        # Safeguard 1: Force identical timezone awareness
                        if last_ts.tzinfo is None:
                            last_ts = last_ts.replace(tzinfo=now_dt.tzinfo)
                        else:
                            last_ts = last_ts.astimezone(now_dt.tzinfo)

                        # Safeguard 2: Catch Raspberry Pi negative boot-clock drift
                        delta_seconds = (now_dt - last_ts).total_seconds()
                        if delta_seconds < 0:
                            _LOGGER.warning("Negative time delta detected for %s. System clock may be out of sync.", self.entity_id)
                            is_stale = False
                        elif delta_seconds > self._active_settings["GRACE_PERIOD_SECONDS"]:
                            is_stale = True

                if (self._gaming_type == "xbox" and "last seen" in self._attr_native_value.lower()) or is_stale:
                    self._attr_native_value = "Offline"
                    if self._current_game and self._play_start_time:
                        # Properly close out whatever session was in progress before
                        # this gap/restart instead of silently discarding it. Uses the
                        # last confirmed-online timestamp (not "now") as the end time,
                        # so the outage itself never gets counted as playtime -- this
                        # also sets _current_game/_play_start_time back to None.
                        self._handle_game_transition(None, explicit_end_time=last_ts)
                    else:
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
            if self._current_game and _normalize_game_name(self._current_game) in (self._global_exclusions_lower | self._exclude_games):
                # This game was added to the global or this player's own
                # exclusion list since the session started -- end it
                # immediately (crediting whatever time genuinely accrued
                # before the exclusion was added), instead of silently
                # continuing to track an app that should never count as a
                # game, until the player naturally switches away.
                self._handle_game_transition(None)
                self._attr_native_value = "Offline"
                self._attr_extra_state_attributes["current_game"] = None
            if self._last_game_stopped_timestamp: self._last_online_valid_timestamp = self._last_game_stopped_timestamp
        
        for zombie in ZOMBIE_ATTRIBUTES:
            if zombie in self._attr_extra_state_attributes: del self._attr_extra_state_attributes[zombie]
        for legacy_debug in ["debug_raw_source_state", "debug_time_ago", "debug_sync", "source_entity", "play_history", "code_version", "last_update_timestamp", "backup_last_session_time", "backup_last_online_timestamp", "backup_last_played_game", "backup_last_game_stopped_timestamp", "temp_offline_start", "daily_play_time_yesterday", "last_reset_date", "last_weekly_reset", "last_session_play_time", "daily_play_limit_minutes", "remaining_play_time_minutes"]:
            if legacy_debug in self._attr_extra_state_attributes: del self._attr_extra_state_attributes[legacy_debug]

        self.async_on_remove(async_track_time_interval(self.hass, self._update_play_time, timedelta(seconds=30)))

        if self._gaming_type == "discord":
            self.async_on_remove(
                self.hass.bus.async_listen(f"gaming_status_discord_{self._source_entity_id}", self._async_discord_update)
            )
        else:
            source_state = self.hass.states.get(self._source_entity_id)
            if source_state: await self._try_force_sync(source_state)
            
            entities_to_watch = [self._source_entity_id]
            if self._avatar_entity_id: entities_to_watch.append(self._avatar_entity_id)
            if self._gaming_type == "playstation" and self._ps3_entity_id:
                # Unlike the Xbox now_playing sibling (same device, tends to
                # update in tandem with its primary source), a PS3 media_player
                # is a fully independent entity -- watch it explicitly so its
                # own state changes trigger an update promptly, rather than
                # only being read reactively whenever the primary source
                # happens to fire.
                entities_to_watch.append(self._ps3_entity_id)

            # Fallback: track avatar image entity if device lookup didn't find it.
            # Use translation_key to strip the (possibly translated) suffix from the
            # source entity ID so the gamertag prefix is extracted correctly in any locale.
            if self._gaming_type == "playstation" and not self._avatar_entity_id:
                try:
                    reg_entry = er.async_get(self.hass).async_get(self._source_entity_id)
                    tk = getattr(reg_entry, "translation_key", None) if reg_entry else None
                    object_id = self._source_entity_id.split('.')[1]
                    suffix = f"_{tk}" if tk else "_now_playing"
                    if object_id.endswith(suffix):
                        gamertag = object_id[:-len(suffix)]
                        entities_to_watch.append(f"image.{gamertag}_avatar")
                    elif object_id.endswith("_now_playing"):
                        gamertag = object_id[:-len("_now_playing")]
                        entities_to_watch.append(f"image.{gamertag}_avatar")
                except Exception: pass
            elif self._gaming_type == "xbox" and not self._avatar_entity_id:
                try:
                    gamertag = _get_gamertag_from_entity(self._source_entity_id, "xbox")
                    if gamertag:
                        safe_tag = gamertag.lower().replace(" ", "_")
                        image_entity = f"image.{safe_tag}_gamerpic"
                        entities_to_watch.append(image_entity)
                except Exception: pass

            self.async_on_remove(async_track_state_change_event(self.hass, entities_to_watch, self._async_state_changed))
            await self._trigger_source_update(force=True)

    async def _trigger_source_update(self, force=False):
        if self._gaming_type == "discord": return
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
    def _async_discord_update(self, event):
        data = event.data
        state = data["state"]
        attrs = {
            "application_id": data["app_id"],
            "discord_data": {
                "discord_user": {"id": data["user_id"], "avatar": None}
            },
            "entity_picture": data["avatar_url"]
        }
        self.hass.async_create_task(self._unified_update(state, attrs))

    @callback
    def _update_play_time(self, now=None):
        try:
            # --- PREVENT CRASHES: Ensure restored JSON cache objects are dicts, not NoneTypes ---
            if not isinstance(self._weekly_game_breakdown, dict): self._weekly_game_breakdown = {}
            if not isinstance(self._longest_session_details, dict): self._longest_session_details = {"game": None, "duration": 0}
            if not isinstance(self._play_history, dict): self._play_history = {}
            if not isinstance(getattr(self, "_all_time_game_seconds", None), dict): self._all_time_game_seconds = {}
            
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
                    self._play_start_time = now_dt.isoformat()
            if self._current_game and not self._play_start_time: self._play_start_time = now_dt.isoformat()
            timer_status = "Inactive"
            secondary = ""
            if self._play_start_time and self._current_game:
                is_blocked = False
                block_reason = ""
                if self._is_game_active_elsewhere(self._current_game):
                    is_blocked = True
                    block_reason = "Active Elsewhere"
                    self._active_elsewhere_blocked_seconds[self._current_game] = (
                        self._active_elsewhere_blocked_seconds.get(self._current_game, 0) + delta_seconds
                    )
                elif self._temp_offline_start is not None:
                    is_blocked = True
                    block_reason = "Grace Period"
                if not is_blocked:
                    self._last_online_valid_timestamp = now_dt.isoformat()
                    self._daily_play_time = int((self._daily_play_time or 0) + delta_seconds)
                    self._weekly_play_time = int((self._weekly_play_time or 0) + delta_seconds)
                    
                    self._bump_playtime(self._current_game, delta_seconds)
                    self._session_ticks_persistent[self._current_game] = self._session_ticks_persistent.get(self._current_game, 0) + int(delta_seconds)

                    timer_status = "Running"
                else: timer_status = f"Paused ({block_reason})"
                session_seconds, play_time_text = self._get_session_info()
                
                if session_seconds > self._longest_session_details.get("duration", 0) and not is_blocked:
                    self._longest_session_details = {"game": self._current_game, "duration": int(session_seconds)}
                    
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
            self._last_update_dt = now_dt
            if was_offline and timer_status == "Stopped (Offline)" and self._daily_play_time == old_daily and secondary == old_secondary: return
            self.async_write_ha_state()
        except Exception as e: _LOGGER.error("Error in _update_play_time for %s: %s", self.entity_id, e)

    async def _unified_update(self, current_state, attrs, force_update=False):
        try:
            platform_data = self._get_platform_data(current_state, attrs)
            if not platform_data: return
            self._check_daily_reset()
            now_dt = dt_util.now()
            
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
                        self._bump_playtime(self._current_game, missed_seconds)
                        self._session_ticks_persistent[self._current_game] = self._session_ticks_persistent.get(self._current_game, 0) + int(missed_seconds)
                if self._temp_offline_start is not None:
                    self._temp_offline_start = None
                    self._store.async_delay_save(self._get_store_data, 5.0)
            display_state = "Offline"
            game_cover = None
            secondary = ""
            if platform_data.get("current_game"):
                raw_game_name = platform_data["current_game"]
                raw_game_name = self._apply_title_override(raw_game_name)
                
                # Sanitize the final display name as a last line of defense
                game_name_display = self._sanitize_game_title(_format_game_name_for_display(raw_game_name))
                normalized_new = _normalize_game_name(game_name_display)
                normalized_current = _normalize_game_name(self._current_game) if self._current_game else None
                new_transition = False
                if normalized_new != normalized_current:
                    if self._current_game and _is_same_base_game(self._current_game, game_name_display, self._active_settings["SAME_GAME_PREFIX_WORDS"]):
                        # Same underlying game (menu/lobby/match state text churn) — keep the
                        # session and canonical name locked to what was already playing.
                        game_name_display = self._current_game
                    elif self._temp_game_lost_time and self._current_game:
                        time_since_lost = (now_dt - self._temp_game_lost_time).total_seconds()
                        if _normalize_game_name(self._current_game) == normalized_new and time_since_lost <= self._active_settings["GAME_TRANSITION_GRACE_SECONDS"]: self._current_game = game_name_display
                        else:
                            self._handle_game_transition(game_name_display)
                            new_transition = True
                    else:
                        self._handle_game_transition(game_name_display)
                        new_transition = True
                    self._temp_game_lost_time = None
                else:
                    self._current_game = game_name_display
                    self._temp_game_lost_time = None
                display_state = game_name_display

                if new_transition:
                    # Publish now, before the artwork/rating pipeline below (live
                    # SteamGridDB + RAWG API calls, sequential) -- a sibling sensor's
                    # ghost/"Active Elsewhere" check reads this sensor's PUBLISHED
                    # current_game attribute, not this internal variable, so waiting
                    # until the full pipeline finished left a multi-second window
                    # where that check could run first and miss entirely.
                    self._attr_native_value = display_state
                    self._write_common_attributes("Playing now", game_cover=self._cached_game_cover)
                    self.async_write_ha_state()

                if normalized_new and not self._cover_fetch_attempted:
                    self._cover_fetch_attempted = True
                    try:
                        fetched = await utils.fetch_game_assets(self.hass, game_name_display)
                        if fetched and any(fetched.values()):
                            self._cached_game_cover = fetched.get("grid") or platform_data.get("game_cover_url")
                            self._cached_game_hero = fetched.get("hero")
                            self._cached_game_logo = fetched.get("logo")
                            self._cached_game_icon = fetched.get("icon")
                        else: 
                            self._cached_game_cover = platform_data.get("game_cover_url")
                    except Exception as e:
                        _LOGGER.error("Artwork fetch failed for %s: %s", game_name_display, e)
                        self._cached_game_cover = platform_data.get("game_cover_url")

                if normalized_new and not self._rating_fetch_attempted:
                    self._rating_fetch_attempted = True
                    try:
                        self._cached_game_rating = await utils.fetch_game_rating(self.hass, game_name_display)
                    except Exception as e:
                        _LOGGER.error("Rating fetch failed for %s: %s", game_name_display, e)
                        self._cached_game_rating = None

                # --- NEW BACKGROUND DISK SCAN (Runs ONLY once per game transition) ---
                def _scan_local_disk():
                    from homeassistant.helpers.network import get_url
                    try:
                        base_url = get_url(self.hass, prefer_external=True)
                    except Exception:
                        base_url = ""
                    res = {}
                    s_name = re.sub(r'[^a-z0-9]', '_', str(game_name_display).lower())
                    s_name = re.sub(r'_+', '_', s_name).strip('_')
                    for sfx in ["grid", "hero", "logo", "icon"]:
                        for e in ["png", "jpg", "jpeg", "webp", "ico", "gif"]:
                            f_path = self.hass.config.path("www", "gaming_status_cache", f"{s_name}_{sfx}.{e}")
                            if os.path.exists(f_path):
                                mt = os.path.getmtime(f_path)
                                res[sfx] = f"{base_url}/local/gaming_status_cache/{s_name}_{sfx}.{e}?v={mt}"
                                if sfx == "hero" or (sfx == "grid" and "color_path" not in res):
                                    res["color_path"] = f_path
                                    res["color_mtime"] = mt
                                break
                    return res
                    
                local_scan = await self.hass.async_add_executor_job(_scan_local_disk)
                
                # Apply local fallbacks to RAM if the API didn't provide them
                if not self._cached_game_cover or url_host_matches(self._cached_game_cover, "akamaihd.net"):
                    self._cached_game_cover = local_scan.get("grid") or self._cached_game_cover
                if not self._cached_game_hero: self._cached_game_hero = local_scan.get("hero")
                if not self._cached_game_logo: self._cached_game_logo = local_scan.get("logo")
                if not self._cached_game_icon: self._cached_game_icon = local_scan.get("icon")
                        
                # 1. ALWAYS Check for Manual Override FIRST (Costs zero CPU/Disk I/O)
                override = getattr(utils, "GAME_COLOR_OVERRIDES", {}).get(_normalize_game_name(game_name_display))
                if override:
                    self._cached_game_color = override
                
                # 2. Only run the heavy background extraction if the feature is enabled
                elif utils.ENABLE_VIBRANT_COLOR:
                    try:
                        local_path = local_scan.get("color_path")
                        current_mtime = local_scan.get("color_mtime", 0)

                        # 3. Check the Internal Cache SECOND (with Timestamp Awareness)
                        if not self._cached_game_color and game_name_display in self._color_history_cache:
                            cached_data = self._color_history_cache[game_name_display]
                            
                            if isinstance(cached_data, dict):
                                cached_color = cached_data.get("color")
                                cached_time = cached_data.get("timestamp", 0)
                            else:
                                cached_color = cached_data
                                cached_time = 0
                                
                            # If the physical file is newer than our cache, bypass to force re-extraction
                            if current_mtime > 0 and current_mtime > cached_time:
                                pass 
                            else:
                                self._cached_game_color = cached_color
                        
                        # 3. ONLY extract if both are empty
                        if not self._cached_game_color and local_path:
                            self._cached_game_color = await self.hass.async_add_executor_job(
                                utils.extract_vibrant_color, local_path
                            )
                            
                            if self._cached_game_color:
                                self._color_history_cache[game_name_display] = {
                                    "color": self._cached_game_color,
                                    "timestamp": current_mtime
                                }
                                if len(self._color_history_cache) > 50:
                                    oldest_game = next(iter(self._color_history_cache))
                                    del self._color_history_cache[oldest_game]
                                self._store.async_delay_save(self._get_store_data, 5.0)
                    except Exception as e:
                        _LOGGER.error("Color extraction failed for %s: %s", game_name_display, e)
                        
                if not self._cached_game_cover and platform_data.get("game_cover_url"): 
                    self._cached_game_cover = platform_data.get("game_cover_url")
                
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
                        is_excluded = _normalize_game_name(new_xbox_game) in (self._global_exclusions_lower | self._exclude_games)
                        if new_xbox_game.lower() not in idle_list and not is_ghost and not is_excluded: self._last_played_game = new_xbox_game
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
            self._write_common_attributes(secondary, game_cover=game_cover, xbox_suppressed=platform_data.get("xbox_suppressed", False))
            self.async_write_ha_state()
        except Exception as e: _LOGGER.error("Error in _unified_update for %s: %s", self.entity_id, e)

    async def _process_avatar_cache(self, url, filename):
        await utils.fetch_and_cache_image(self.hass, url, filename)

# ------------------------------------------------------------------
# 2. MASTER SENSOR CLASS
# ------------------------------------------------------------------

class MasterGamingSensor(RestoreSensor):
    _attr_should_poll = False
    
    # Exclude from database to completely prevent bloat
    _unrecorded_attributes = frozenset({
        "secondary", 
        "game_cover_art", 
        "game_hero_art", 
        "game_logo_art", 
        "game_icon_art", 
        "entity_picture", 
        "last_online_valid_timestamp", 
        "current_game", 
        "daily_play_limit_minutes",
        "remaining_play_time_minutes",
        "weekly_breakdown", "platform_split", "longest_session",
        "rolling_weekly_breakdown", "calendar_weekly_breakdown",
        "rolling_longest_session", "calendar_longest_session",
        "raw_rolling_breakdown", "raw_calendar_breakdown",
        "play_history", "game_content_rating", "rating_exceeded", "recent_sessions",
        "all_time_total_hours", "all_time_session_count", "all_time_top_games",
    })

    def __init__(self, hass, name, profiles, parental_rules=None, same_game_prefix_words=DEFAULT_SAME_GAME_PREFIX_WORDS, handoff_grace_seconds=DEFAULT_MASTER_HANDOFF_GRACE_SECONDS):
        self.hass = hass
        self._profiles = profiles
        self._parental_rules = parental_rules or {}
        self._same_game_prefix_words = same_game_prefix_words
        self._handoff_grace_seconds = handoff_grace_seconds
        safe_owner = re.sub(r'[^a-z0-9_]', '_', name.lower().replace(" ", "_"))
        self._attr_name = f"{name} Gaming Status"
        self._attr_unique_id = f"gaming_status_{safe_owner}_master_v6"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_master"
        self._attr_native_value = "Offline"
        self._attr_icon = "mdi:controller"
        self._attr_entity_picture = None
        self._attr_extra_state_attributes = {}
        self._platform_sensors = {}
        for platform in PLATFORM_PRIORITY:
            if profiles.get(platform): self._platform_sensors[f"sensor.gaming_status_{safe_owner}_{platform}"] = platform

    @property
    def available(self): return True

    async def async_added_to_hass(self):
        """Run when the entity is added to Home Assistant to restore state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        
        if last_state and last_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._attr_native_value = last_state.state
            self._attr_extra_state_attributes = dict(last_state.attributes)
            self._attr_entity_picture = last_state.attributes.get("entity_picture")
            lp = self._attr_extra_state_attributes.get("last_played_game")
            if lp and str(lp).lower() == "offline": 
                self._attr_extra_state_attributes["last_played_game"] = None
                
        if self._platform_sensors: 
            self.async_on_remove(async_track_state_change_event(self.hass, list(self._platform_sensors.keys()), self._async_platform_changed))
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
        most_recent_ts = None
        most_recent_sensor = None
        most_recent_key = None
        
        # Trackers for the new Dual-Window Rich Data attributes
        master_rolling_breakdown = {}
        master_calendar_breakdown = {}
        master_history = {}
        master_recent_sessions = []
        master_all_time_hours = 0.0
        master_all_time_seconds_by_game = {}
        master_all_time_sessions = 0
        platform_totals = {}
        max_rolling_duration = 0
        max_rolling_game = None
        max_calendar_duration = 0
        max_calendar_game = None

        for platform_sensor_id, p_key in self._platform_sensors.items():
            platform_state = self.hass.states.get(platform_sensor_id)
            if not platform_state: continue
            
            d_time = platform_state.attributes.get("daily_play_time")
            w_time = platform_state.attributes.get("weekly_play_time")
            r_time = platform_state.attributes.get("rolling_weekly_hours")
            wl_time = platform_state.attributes.get("weekly_play_time_last_week") 
            
            if d_time: total_daily_seconds += int(d_time)
            if r_time: total_rolling_weekly_hours += float(r_time)
            if wl_time: total_weekly_seconds_last_week += int(wl_time)
            
            # Platform Total Tracking
            if w_time: 
                total_weekly_seconds += int(w_time)
                platform_totals[p_key] = platform_totals.get(p_key, 0) + int(w_time)
                
            # Aggregate Rolling Breakdowns
            r_breakdown = platform_state.attributes.get("rolling_weekly_breakdown", {})
            for game, duration in r_breakdown.items():
                master_rolling_breakdown[game] = master_rolling_breakdown.get(game, 0) + duration
                
            # Aggregate Calendar Breakdowns
            c_breakdown = platform_state.attributes.get("calendar_weekly_breakdown", {})
            for game, duration in c_breakdown.items():
                master_calendar_breakdown[game] = master_calendar_breakdown.get(game, 0) + duration

            # Aggregate per-day game history
            for date_str, game_breakdown in platform_state.attributes.get("play_history", {}).items():
                if isinstance(game_breakdown, dict):
                    day = master_history.setdefault(date_str, {})
                    for game, seconds in game_breakdown.items():
                        day[game] = day.get(game, 0) + int(seconds)

            # Aggregate recent sessions
            master_recent_sessions.extend(platform_state.attributes.get("recent_sessions", []))

            # Aggregate all-time (lifetime) stats. Total hours/session count are
            # summed directly from each platform's exact numbers; only the
            # top-games ranking is derived from (necessarily bounded) candidate
            # lists, so a game split across platforms can rarely be
            # under-ranked here even though the total is always exact.
            master_all_time_hours += platform_state.attributes.get("all_time_total_hours", 0) or 0
            master_all_time_sessions += platform_state.attributes.get("all_time_session_count", 0) or 0
            for game_entry in platform_state.attributes.get("all_time_top_games", []):
                game_name = game_entry.get("game")
                if game_name:
                    master_all_time_seconds_by_game[game_name] = master_all_time_seconds_by_game.get(game_name, 0) + game_entry.get("hours", 0) * 3600

            # Find Longest Sessions
            r_longest = platform_state.attributes.get("rolling_longest_session_details", {})
            r_dur = r_longest.get("duration", 0)
            if r_dur > max_rolling_duration:
                max_rolling_duration = r_dur
                max_rolling_game = r_longest.get("game")
                
            c_longest = platform_state.attributes.get("calendar_longest_session_details", {})
            c_dur = c_longest.get("duration", 0)
            if c_dur > max_calendar_duration:
                max_calendar_duration = c_dur
                max_calendar_game = c_longest.get("game")
            
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
                
            state_value = platform_state.state
            idle_states = PLATFORM_CONFIG.get(p_key, {}).get("idle_states", [])
            
            # Only process if NOT offline/unknown AND NOT in the idle list
            if (state_value.lower() not in ["offline", "source missing", "unavailable", "unknown"] and 
                state_value not in idle_states):
                
                t_status = platform_state.attributes.get("timer_status", "")
                
                # Ignore sensors that have explicitly yielded to consoles
                if "Active Elsewhere" not in t_status:
                    if not active_state:
                        active_sensor_id = platform_sensor_id
                        active_state = platform_state
                    else:
                        active_t_status = active_state.attributes.get("timer_status", "")
                        same_game = _is_same_base_game(
                            active_state.attributes.get("current_game"),
                            platform_state.attributes.get("current_game"),
                            self._same_game_prefix_words,
                        )

                        # Rule 1 (glitch correction): a "Running" game ALWAYS beats a
                        # "Paused/Grace Period" one, even for the same base game — this
                        # recovers from a platform-specific hiccup regardless of priority
                        # order or which platform is currently active.
                        if "Running" in t_status and "Paused" in active_t_status:
                            active_sensor_id = platform_sensor_id
                            active_state = platform_state

                        # Rule 2 (tiebreak): only decides between two genuinely different
                        # games, and only once the incumbent has been genuinely stale for
                        # a sustained period (not just this one tick) -- otherwise a
                        # platform with a brief connectivity blip can repeatedly steal and
                        # relinquish control from an incumbent that's continuously,
                        # legitimately still active (two truly simultaneous sessions on
                        # different platforms should settle on one and stop flapping, not
                        # re-litigate the "winner" on every tick).
                        elif not same_game and (("Running" in t_status and "Running" in active_t_status) or ("Paused" in t_status and "Paused" in active_t_status)):
                            new_start = _safe_parse_datetime(platform_state.attributes.get("play_start_time"))
                            curr_start = _safe_parse_datetime(active_state.attributes.get("play_start_time"))
                            incumbent_last_valid = _safe_parse_datetime(active_state.attributes.get("last_online_valid_timestamp"))
                            incumbent_stale = (
                                incumbent_last_valid is None
                                or (dt_util.now() - incumbent_last_valid).total_seconds() >= self._handoff_grace_seconds
                            )
                            if new_start and curr_start and new_start > curr_start and incumbent_stale:
                                active_sensor_id = platform_sensor_id
                                active_state = platform_state
        
        # Compute rolling_weekly_hours directly from master_history (play_history aggregated
        # from all platform sensors, including today's live _weekly_game_breakdown).
        # Platform sensors do not expose a rolling_weekly_hours attribute, so reading it
        # from them always returns 0 — this is the authoritative calculation instead.
        rolling_cutoff = (dt_util.as_local(dt_util.now()) - timedelta(days=7)).date()
        rolling_secs = 0
        for _date_str, _day_data in master_history.items():
            try:
                if parser.parse(_date_str).date() >= rolling_cutoff:
                    rolling_secs += sum(int(v) for v in _day_data.values())
            except Exception:
                pass
        total_rolling_weekly_hours = round(rolling_secs / 3600, 2)

        # --- Generate Formatted Rich Data Attributes ---
        # 1. Top Games Breakdowns
        sort_rolling = dict(sorted(master_rolling_breakdown.items(), key=lambda item: item[1], reverse=True))
        fmt_rolling_breakdown = {k: utils._format_time(v) for k, v in sort_rolling.items() if v >= 60}
        raw_rolling_breakdown = {k: round(v / 3600, 2) for k, v in sort_rolling.items() if v >= 60} # For Charting
        
        sort_calendar = dict(sorted(master_calendar_breakdown.items(), key=lambda item: item[1], reverse=True))
        fmt_calendar_breakdown = {k: utils._format_time(v) for k, v in sort_calendar.items() if v >= 60}
        raw_calendar_breakdown = {k: round(v / 3600, 2) for k, v in sort_calendar.items() if v >= 60} # For Charting
        
        # 2. Platform Split (Percentages)
        platform_split = {}
        if total_weekly_seconds > 0:
            grouped_totals = {}
            # Step 1: Combine the raw seconds by their shared analytic group
            for plat, plat_secs in platform_totals.items():
                if plat_secs > 0:
                    config = PLATFORM_CONFIG.get(plat, {})
                    # Pull the analytic group, gracefully falling back to the suffix if missing
                    group_name = config.get("group", config.get("name_suffix", plat.title()))
                    grouped_totals[group_name] = grouped_totals.get(group_name, 0) + plat_secs
            
            # Step 2: Calculate the percentages from the grouped totals
            for group_name, combined_secs in grouped_totals.items():
                pct = round((combined_secs / total_weekly_seconds) * 100)
                platform_split[group_name] = f"{pct}%"
                    
        # 3. Longest Session Outputs
        rolling_longest_text = "None"
        if max_rolling_game and max_rolling_duration >= 60:
            rolling_longest_text = f"{max_rolling_game} ({utils._format_time(max_rolling_duration)})"
            
        calendar_longest_text = "None"
        if max_calendar_game and max_calendar_duration >= 60:
            calendar_longest_text = f"{max_calendar_game} ({utils._format_time(max_calendar_duration)})"
        
        # --- Parental Calculation Logic ---
        daily_play_limit_minutes = 0
        remaining_play_time_minutes = 0
        st_rule = self._parental_rules.get("screen_time", {})
        if st_rule.get("enabled"):
            is_weekend = dt_util.now().weekday() >= 5
            daily_play_limit_minutes = st_rule.get("weekend_minutes", 180) if is_weekend else st_rule.get("weekday_minutes", 120)
            spent = int(total_daily_seconds / 60)
            remaining_play_time_minutes = max(0, daily_play_limit_minutes - spent)

        rating_exceeded = False
        current_game_rating = None
        rt_rule = self._parental_rules.get("ratings", {})
        if rt_rule.get("enabled") and active_state:
            rating_info = active_state.attributes.get("game_content_rating") or {}
            age_floor = rating_info.get("age_floor")
            max_age_floor = rt_rule.get("max_age_floor")
            current_game_rating = rating_info.get("esrb")
            if age_floor is not None and max_age_floor is not None and age_floor > int(max_age_floor):
                rating_exceeded = True

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
                "game_hero_art": active_state.attributes.get("game_hero_art"),
                "game_logo_art": active_state.attributes.get("game_logo_art"),
                "game_icon_art": active_state.attributes.get("game_icon_art"),
                "game_dominant_color": active_state.attributes.get("game_dominant_color"),
                "game_content_rating": active_state.attributes.get("game_content_rating"),
                "current_game_rating": current_game_rating,
                "rating_exceeded": rating_exceeded,
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
                "remaining_play_time_minutes": remaining_play_time_minutes,
                "weekly_breakdown": fmt_rolling_breakdown,
                "rolling_weekly_breakdown": fmt_rolling_breakdown,
                "calendar_weekly_breakdown": fmt_calendar_breakdown,
                "raw_rolling_breakdown": raw_rolling_breakdown,
                "raw_calendar_breakdown": raw_calendar_breakdown,
                "platform_split": platform_split,
                "longest_session": rolling_longest_text,
                "rolling_longest_session": rolling_longest_text,
                "calendar_longest_session": calendar_longest_text
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
                    "game_hero_art": most_recent_sensor.attributes.get("game_hero_art"),
                    "game_logo_art": most_recent_sensor.attributes.get("game_logo_art"),
                    "game_icon_art": most_recent_sensor.attributes.get("game_icon_art"),
                    "game_dominant_color": most_recent_sensor.attributes.get("game_dominant_color"),
                    "game_content_rating": most_recent_sensor.attributes.get("game_content_rating"),
                    "current_game_rating": None,
                    "rating_exceeded": False,
                    "last_played_game": most_recent_sensor.attributes.get("last_played_game"),
                    "last_online_valid_timestamp": most_recent_sensor.attributes.get("last_online_valid_timestamp"),
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week,
                    "entity_picture": new_entity_picture,
                    "daily_play_limit_minutes": daily_play_limit_minutes,
                    "remaining_play_time_minutes": remaining_play_time_minutes,
                    "weekly_breakdown": fmt_rolling_breakdown,
                    "rolling_weekly_breakdown": fmt_rolling_breakdown,
                    "calendar_weekly_breakdown": fmt_calendar_breakdown,
                    "raw_rolling_breakdown": raw_rolling_breakdown,
                    "raw_calendar_breakdown": raw_calendar_breakdown,
                    "platform_split": platform_split,
                    "longest_session": rolling_longest_text,
                    "rolling_longest_session": rolling_longest_text,
                    "calendar_longest_session": calendar_longest_text
                }
                lp = new_attrs.get("last_played_game")
                if lp and str(lp).lower() == "offline": new_attrs["last_played_game"] = None
                if most_recent_key in PLATFORM_CONFIG: new_icon = PLATFORM_CONFIG[most_recent_key]["icon"]
            else:
                new_attrs = {
                    "secondary": "Offline",
                    "game_cover_art": self._attr_extra_state_attributes.get("game_cover_art"),
                    "game_hero_art": self._attr_extra_state_attributes.get("game_hero_art"),
                    "game_logo_art": self._attr_extra_state_attributes.get("game_logo_art"),
                    "game_icon_art": self._attr_extra_state_attributes.get("game_icon_art"),
                    "game_dominant_color": self._attr_extra_state_attributes.get("game_dominant_color"),
                    "game_content_rating": self._attr_extra_state_attributes.get("game_content_rating"),
                    "current_game_rating": None,
                    "rating_exceeded": False,
                    "last_played_game": self._attr_extra_state_attributes.get("last_played_game"),
                    "last_online_valid_timestamp": self._attr_extra_state_attributes.get("last_online_valid_timestamp"),
                    "total_daily_hours": total_daily_hours,
                    "total_weekly_hours": total_weekly_hours,
                    "rolling_weekly_hours": total_rolling_weekly_hours,
                    "total_weekly_hours_last_week": total_weekly_hours_last_week,
                    "entity_picture": self._attr_extra_state_attributes.get("entity_picture"),
                    "daily_play_limit_minutes": daily_play_limit_minutes,
                    "remaining_play_time_minutes": remaining_play_time_minutes,
                    "weekly_breakdown": fmt_rolling_breakdown,
                    "rolling_weekly_breakdown": fmt_rolling_breakdown,
                    "calendar_weekly_breakdown": fmt_calendar_breakdown,
                    "raw_rolling_breakdown": raw_rolling_breakdown,
                    "raw_calendar_breakdown": raw_calendar_breakdown,
                    "platform_split": platform_split,
                    "longest_session": rolling_longest_text,
                    "rolling_longest_session": rolling_longest_text,
                    "calendar_longest_session": calendar_longest_text
                }

        new_attrs["play_history"] = dict(sorted(master_history.items()))
        new_attrs["recent_sessions"] = sorted(
            master_recent_sessions, key=lambda r: r.get("start_time") or "", reverse=True
        )[:MAX_RECENT_SESSIONS]
        new_attrs["all_time_total_hours"] = round(master_all_time_hours, 1)
        new_attrs["all_time_session_count"] = master_all_time_sessions
        new_attrs["all_time_top_games"] = top_n_games(master_all_time_seconds_by_game, 10)
        new_attrs["color_extraction_enabled"] = utils.ENABLE_VIBRANT_COLOR

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
# 3. GLOBAL ONLINE COUNT SENSOR
# ------------------------------------------------------------------

class GlobalOnlineCountSensor(SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    def __init__(self, hass, players):
        self.hass = hass
        self._attr_name = "Players Online"
        self._attr_unique_id = "gaming_status_players_online_count_v2"
        self.entity_id = "sensor.gaming_status_players_online"
        self._attr_icon = "mdi:account-group"
        self._attr_native_value = 0
        self._master_sensors = []
        for player_name in players:
            safe_owner = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))
            self._master_sensors.append(f"sensor.gaming_status_{safe_owner}_master")

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

# ------------------------------------------------------------------
# 4. PC SUB-MASTER SENSOR
# ------------------------------------------------------------------

class PCGamingSensor(RestoreSensor):
    _attr_should_poll = False
    _attr_icon = "mdi:monitor"

    # Exclude bulky/volatile attributes from the recorder database (same
    # rationale as PersistentStatusSensor/MasterGamingSensor's equivalents).
    _unrecorded_attributes = frozenset({
        "secondary", "game_cover_art", "game_hero_art", "game_logo_art",
        "game_icon_art", "entity_picture", "last_online_valid_timestamp",
        "current_game", "game_dominant_color", "game_content_rating",
        "rolling_weekly_breakdown", "calendar_weekly_breakdown",
        "play_history", "recent_sessions",
    })

    def __init__(self, hass, name, pc_entities, same_game_prefix_words=DEFAULT_SAME_GAME_PREFIX_WORDS, handoff_grace_seconds=DEFAULT_MASTER_HANDOFF_GRACE_SECONDS):
        self.hass = hass
        self._pc_entities = pc_entities
        self._same_game_prefix_words = same_game_prefix_words
        self._handoff_grace_seconds = handoff_grace_seconds
        safe_owner = re.sub(r'[^a-z0-9_]', '_', name.lower().replace(" ", "_"))
        self._attr_name = f"{name} PC"
        self._attr_unique_id = f"gaming_status_{safe_owner}_pc_v2"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_pc"
        self._attr_native_value = "Offline"
        self._attr_extra_state_attributes = {}
        self._attr_entity_picture = None

    async def async_added_to_hass(self):
        """Run when the entity is added to Home Assistant to restore state."""
        await super().async_added_to_hass()

        # Pull the last known state from the Home Assistant database
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._attr_native_value = last_state.state
            self._attr_extra_state_attributes = dict(last_state.attributes)
            self._attr_entity_picture = last_state.attributes.get("entity_picture")

        # Start listening for live changes
        self.async_on_remove(async_track_state_change_event(self.hass, self._pc_entities, self._async_pc_changed))
        # Periodic poll as a safety net — catches any state change events that were missed
        # during startup, reload, or edge cases (custom/playnite sensors can be slow to fire)
        self.async_on_remove(async_track_time_interval(self.hass, self._async_pc_poll, timedelta(seconds=30)))
        await self._update_pc_state()

        # FORCE UPDATE: Wait for HA to finish booting, pause 5 seconds, then check platforms again
        from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
        async def _force_delayed_update(event=None):
            await asyncio.sleep(5)
            await self._update_pc_state()

        # OPTIMIZATION: Allow Hot-Reloads to work without full system reboots
        if self.hass.is_running:
            self.hass.async_create_task(_force_delayed_update())
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _force_delayed_update)

    @callback
    def _async_pc_changed(self, event):
        self.hass.async_create_task(self._update_pc_state())

    @callback
    def _async_pc_poll(self, now=None):
        self.hass.async_create_task(self._update_pc_state())

    async def _update_pc_state(self):
        active_state = None
        most_recent_state = None
        most_recent_ts = None

        # Entities are passed in strict priority order (custom -> steam -> discord)
        for entity_id in self._pc_entities:
            state = self.hass.states.get(entity_id)
            if not state: continue

            # Track most recent for offline fallback
            # NEW: Only treat as an offline fallback if the integration isn't actively tracking a live or paused game
            t_status = state.attributes.get("timer_status", "") or ""
            if "Running" not in t_status and "Paused" not in t_status and "Active Elsewhere" not in t_status:
                ts_str = state.attributes.get("last_online_valid_timestamp")
                if ts_str:
                    try:
                        ts = parser.isoparse(ts_str)
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                        if most_recent_ts is None or ts > most_recent_ts:
                            most_recent_ts = ts
                            most_recent_state = state
                    except Exception: pass

            # Set active state (smart conflict resolution)
            if state.state.lower() not in ["offline", "unavailable", "unknown", "source missing"]:
                t_status = state.attributes.get("timer_status", "")
                if "Active Elsewhere" not in t_status:
                    if not active_state:
                        active_state = state
                    else:
                        active_t_status = active_state.attributes.get("timer_status", "")
                        same_game = _is_same_base_game(
                            active_state.attributes.get("current_game"),
                            state.attributes.get("current_game"),
                            self._same_game_prefix_words,
                        )

                        # Rule 1 (glitch correction): a "Running" game ALWAYS beats a
                        # "Paused/Grace Period" one, even for the same base game.
                        if "Running" in t_status and "Paused" in active_t_status:
                            active_state = state

                        # Rule 2 (tiebreak): only decides between two genuinely
                        # different games, and only once the incumbent has been
                        # genuinely stale for a sustained period (not just this one
                        # tick) -- otherwise a platform with a brief connectivity
                        # blip can repeatedly steal and relinquish control from an
                        # incumbent that's continuously, legitimately still active.
                        elif not same_game and (("Running" in t_status and "Running" in active_t_status) or ("Paused" in t_status and "Paused" in active_t_status)):
                            new_start = _safe_parse_datetime(state.attributes.get("play_start_time"))
                            curr_start = _safe_parse_datetime(active_state.attributes.get("play_start_time"))
                            incumbent_last_valid = _safe_parse_datetime(active_state.attributes.get("last_online_valid_timestamp"))
                            incumbent_stale = (
                                incumbent_last_valid is None
                                or (dt_util.now() - incumbent_last_valid).total_seconds() >= self._handoff_grace_seconds
                            )
                            if new_start and curr_start and new_start > curr_start and incumbent_stale:
                                active_state = state

        if active_state:
            self._attr_native_value = active_state.state
            
            # Dynamically grab the icon and name from the winning platform
            winning_platform = active_state.entity_id.split("_")[-1]
            self._attr_icon = PLATFORM_CONFIG.get(winning_platform, {}).get("icon", "mdi:monitor")
            pretty_platform_name = PLATFORM_CONFIG.get(winning_platform, {}).get("name_suffix", winning_platform.title())
            
            self._attr_extra_state_attributes = {
                "secondary": active_state.attributes.get("secondary", ""),
                "active_platform": pretty_platform_name,
                "game_cover_art": active_state.attributes.get("game_cover_art"),
                "game_hero_art": active_state.attributes.get("game_hero_art"),
                "game_logo_art": active_state.attributes.get("game_logo_art"),
                "game_icon_art": active_state.attributes.get("game_icon_art"),
                "game_dominant_color": active_state.attributes.get("game_dominant_color"),
                "game_content_rating": active_state.attributes.get("game_content_rating"),
                "current_game": active_state.attributes.get("current_game"),
                "last_played_game": active_state.attributes.get("last_played_game"),
                "play_start_time": active_state.attributes.get("play_start_time")
            }
            self._attr_entity_picture = active_state.attributes.get("entity_picture")
        else:
            self._attr_native_value = "Offline"
            self._attr_icon = "mdi:monitor"
            
            # If everything is offline, inherit the 'Last seen...' data from the most recently active PC platform
            if most_recent_state:
                winning_platform = most_recent_state.entity_id.split("_")[-1]
                pretty_platform_name = PLATFORM_CONFIG.get(winning_platform, {}).get("name_suffix", winning_platform.title())
                self._attr_icon = PLATFORM_CONFIG.get(winning_platform, {}).get("icon", "mdi:monitor")
                
                self._attr_extra_state_attributes = {
                    "secondary": most_recent_state.attributes.get("secondary", "Offline"),
                    "active_platform": pretty_platform_name,
                    "game_cover_art": most_recent_state.attributes.get("game_cover_art"),
                    "game_hero_art": most_recent_state.attributes.get("game_hero_art"),
                    "game_logo_art": most_recent_state.attributes.get("game_logo_art"),
                    "game_icon_art": most_recent_state.attributes.get("game_icon_art"),
                    "game_dominant_color": most_recent_state.attributes.get("game_dominant_color"),
                    "game_content_rating": most_recent_state.attributes.get("game_content_rating"),
                    "last_online_valid_timestamp": most_recent_state.attributes.get("last_online_valid_timestamp"),
                    "last_played_game": most_recent_state.attributes.get("last_played_game"),
                    "play_start_time": None
                }
                self._attr_entity_picture = most_recent_state.attributes.get("entity_picture")
            else:
                self._attr_extra_state_attributes = {"secondary": "Offline", "play_start_time": None}
                
                # Fallback to Steam avatar if completely offline and blank, otherwise grab the first available avatar
                fallback_pic = None
                for entity_id in self._pc_entities:
                    state = self.hass.states.get(entity_id)
                    if state and state.attributes.get("entity_picture"):
                        fallback_pic = state.attributes.get("entity_picture")
                        if "steam" in entity_id:
                            break
                self._attr_entity_picture = fallback_pic

        # Aggregate play time and history from all PC platform sensors
        total_daily = 0
        total_weekly = 0
        total_weekly_last = 0
        merged_rolling = {}
        merged_calendar = {}
        merged_history = {}
        merged_sessions = []

        for entity_id in self._pc_entities:
            state = self.hass.states.get(entity_id)
            if not state:
                continue
            attrs = state.attributes
            try:
                total_daily += int(attrs.get("daily_play_time") or 0)
                total_weekly += int(attrs.get("weekly_play_time") or 0)
                total_weekly_last += int(attrs.get("weekly_play_time_last_week") or 0)
            except (ValueError, TypeError):
                pass
            for game, secs in attrs.get("rolling_weekly_breakdown", {}).items():
                merged_rolling[game] = merged_rolling.get(game, 0) + secs
            for game, secs in attrs.get("calendar_weekly_breakdown", {}).items():
                merged_calendar[game] = merged_calendar.get(game, 0) + secs
            for date_str, day_breakdown in attrs.get("play_history", {}).items():
                if not isinstance(day_breakdown, dict):
                    continue
                if date_str not in merged_history:
                    merged_history[date_str] = {}
                for game, secs in day_breakdown.items():
                    merged_history[date_str][game] = merged_history[date_str].get(game, 0) + secs
            merged_sessions.extend(attrs.get("recent_sessions", []))

        self._attr_extra_state_attributes["daily_play_time"] = total_daily
        self._attr_extra_state_attributes["weekly_play_time"] = total_weekly
        self._attr_extra_state_attributes["weekly_play_time_last_week"] = total_weekly_last
        self._attr_extra_state_attributes["rolling_weekly_breakdown"] = merged_rolling
        self._attr_extra_state_attributes["calendar_weekly_breakdown"] = merged_calendar
        self._attr_extra_state_attributes["play_history"] = dict(sorted(merged_history.items()))
        self._attr_extra_state_attributes["recent_sessions"] = sorted(
            merged_sessions, key=lambda r: r.get("start_time") or "", reverse=True
        )[:MAX_RECENT_SESSIONS]
        self._attr_extra_state_attributes["color_extraction_enabled"] = utils.ENABLE_VIBRANT_COLOR

        self.async_write_ha_state()

async def async_setup_entry(hass, config_entry, async_add_entities):
    opts = config_entry.options
    active_settings = {
        "RESET_HISTORY": opts.get(OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY),
        "GRACE_PERIOD_SECONDS": opts.get(OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS),
        "AWAY_GRACE_PERIOD_SECONDS": opts.get(OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS),
        "GAME_TRANSITION_GRACE_SECONDS": opts.get(OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS),
        "MIN_SESSION_DURATION": opts.get(OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION),
        "SAME_GAME_PREFIX_WORDS": opts.get(OPT_SAME_GAME_PREFIX_WORDS, DEFAULT_SAME_GAME_PREFIX_WORDS),
        "MASTER_HANDOFF_GRACE_SECONDS": opts.get(OPT_MASTER_HANDOFF_GRACE, DEFAULT_MASTER_HANDOFF_GRACE_SECONDS),
    }
    raw_overrides = _load_opt_json(opts, OPT_TITLE_OVERRIDES, {})
    utils.GAME_TITLE_OVERRIDES = {_normalize_game_name(k): v for k, v in raw_overrides.items()}
    raw_rating_overrides = _load_opt_json(opts, OPT_RATING_OVERRIDES, {})
    utils.RATING_OVERRIDES = {_normalize_game_name(k): v for k, v in raw_rating_overrides.items()}
    raw_grid = _load_opt_json(opts, OPT_CUSTOM_GRID, {})
    utils.CUSTOM_GRID_MAP = {_normalize_game_name(k): safe_url(v) for k, v in raw_grid.items() if safe_url(v)}
    raw_hero = _load_opt_json(opts, OPT_CUSTOM_HERO, {})
    utils.CUSTOM_HERO_MAP = {_normalize_game_name(k): safe_url(v) for k, v in raw_hero.items() if safe_url(v)}
    raw_logo = _load_opt_json(opts, OPT_CUSTOM_LOGO, {})
    utils.CUSTOM_LOGO_MAP = {_normalize_game_name(k): safe_url(v) for k, v in raw_logo.items() if safe_url(v)}
    raw_icon = _load_opt_json(opts, OPT_CUSTOM_ICON, {})
    utils.CUSTOM_ICON_MAP = {_normalize_game_name(k): safe_url(v) for k, v in raw_icon.items() if safe_url(v)}
    raw_colors = _load_opt_json(opts, OPT_CUSTOM_COLORS, {})
    utils.GAME_COLOR_OVERRIDES = {_normalize_game_name(k): v.strip() for k, v in raw_colors.items()}
    raw_cleanups = _load_opt_json(opts, OPT_TITLE_CLEANUPS, [])
    utils.TITLE_CLEANUPS = raw_cleanups
    utils.compile_title_cleanups()
    utils.STEAMGRIDDB_API_KEY = config_entry.data.get(CONF_STEAMGRIDDB_API_KEY, "")
    utils.RAWG_API_KEY = config_entry.data.get(CONF_RAWG_API_KEY, "")
    utils.USE_LOCAL_CACHE = opts.get(OPT_USE_CACHE, DEFAULT_USE_CACHE)
    
    # --- HARDWARE SAFETY NET ---
    # Detect Raspberry Pi hardware to prevent SD card I/O lockups during color extraction
    def _check_is_pi():
        try:
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                return "Raspberry Pi" in f.read()
        except Exception:
            return False
            
    is_pi = await hass.async_add_executor_job(_check_is_pi)
        
    dynamic_color_default = False if is_pi else DEFAULT_EXTRACT_COLOR
    utils.ENABLE_VIBRANT_COLOR = opts.get(OPT_EXTRACT_COLOR, dynamic_color_default)
    
    utils.CACHE_MAX_FILES = opts.get(OPT_CACHE_MAX_FILES, DEFAULT_CACHE_MAX_FILES)
    utils.CACHE_MAX_DAYS = opts.get(OPT_CACHE_MAX_DAYS, DEFAULT_CACHE_MAX_DAYS)
    global_exclusions = _load_opt_json(opts, OPT_GLOBAL_EXCLUSIONS, [])
    players = _load_opt_json(opts, OPT_PLAYERS, {})
    
    from .const import OPT_ENABLE_PARENTAL
    if opts.get(OPT_ENABLE_PARENTAL, False):
        parental_rules = _load_opt_json(opts, OPT_PARENTAL, {})
    else:
        parental_rules = {}
        
    avatar_dir = hass.config.path("www/gaming_status")
    try: available_avatars = await hass.async_add_executor_job(os.listdir, avatar_dir)
    except FileNotFoundError: available_avatars = []

    ents = []
    registry = er.async_get(hass)
    
    # --- UNIQUE ID MIGRATION (Self-Healing Duplicates Fix) ---
    for player_name, player_data in players.items():
        safe_owner = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))
        for platform in PLAYER_PLATFORMS:
            source_entity_id = player_data.get(platform)
            if source_entity_id:
                old_unique_id = f"gaming_status_{source_entity_id}_tracker_v6"
                new_unique_id = f"gaming_status_{safe_owner}_{source_entity_id}_tracker_v6"
                
                old_ent_id = registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
                new_ent_id = registry.async_get_entity_id("sensor", DOMAIN, new_unique_id)
                
                if old_ent_id:
                    # If HA created a "_2" ghost during the bad boot, kill it
                    if new_ent_id and old_ent_id != new_ent_id:
                        registry.async_remove(new_ent_id)
                    # Safely migrate the original entity to the new unique ID format
                    try:
                        registry.async_update_entity(old_ent_id, new_unique_id=new_unique_id)
                    except Exception:
                        pass

    # --- PC SENSOR UNIQUE ID SANITIZATION MIGRATION ---
    # PCGamingSensor formerly used an unsanitized safe_owner (only spaces replaced),
    # so player names with &, -, etc. produced invalid unique IDs. Migrate them now.
    for player_name in players.keys():
        old_safe = player_name.lower().replace(" ", "_")
        new_safe = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))
        if old_safe == new_safe:
            continue
        old_uid = f"gaming_status_{old_safe}_pc_v2"
        new_uid = f"gaming_status_{new_safe}_pc_v2"
        old_eid = registry.async_get_entity_id("sensor", DOMAIN, old_uid)
        if old_eid:
            ghost = registry.async_get_entity_id("sensor", DOMAIN, new_uid)
            if ghost and ghost != old_eid:
                registry.async_remove(ghost)
            try:
                registry.async_update_entity(
                    old_eid,
                    new_unique_id=new_uid,
                    new_entity_id=f"sensor.gaming_status_{new_safe}_pc"
                )
            except Exception:
                pass

    # --- AUTOMATIC LEGACY SENSOR PURGE (DATABASE & RAM GHOSTS) ---

    # Step 1: Registry Purge by Legacy Unique ID
    legacy_tags = ("_tracker_v5", "_master_v5", "_chart_v161", "_pc_status_v1", "global_players_online_count_v1", "_chart_v6", "_chart_v7")
    for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entity.unique_id.endswith(legacy_tags) or entity.unique_id in legacy_tags:
            try: registry.async_remove(entity.entity_id)
            except Exception: pass

    # Step 2: Hard RAM & Registry Purge by Exact Legacy Name
    legacy_entity_ids = ["sensor.players_online", "binary_sensor.anyone_gaming"]
    for player_name in players.keys():
        safe_owner = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))
        legacy_entity_ids.extend([
            f"sensor.{safe_owner}_gaming_status",
            f"sensor.{safe_owner}_daily_gaming_hours_chart",
            f"sensor.{safe_owner}_pc_status",
            f"sensor.{safe_owner}_steam",
            f"sensor.{safe_owner}_xbox",
            f"sensor.{safe_owner}_playstation",
            f"sensor.{safe_owner}_discord",
            f"sensor.{safe_owner}_custom",
            f"sensor.gaming_status_{safe_owner}_chart",
            f"sensor.gaming_status_{safe_owner}_chart_2"
        ])

    for entity_id in legacy_entity_ids:
        # Kill the Database Ghost
        if registry.async_get(entity_id):
            try: registry.async_remove(entity_id)
            except Exception: pass
            
        # Kill the RAM Ghost (Prevents writing back to core.restore_state)
        if hass.states.get(entity_id):
            try: 
                hass.states.async_remove(entity_id)
                _LOGGER.warning("Permanently flushed legacy RAM ghost: %s", entity_id)
            except Exception: 
                pass

    # --- BACKGROUND ENTITY MIGRATION ---
    for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entity.domain == "sensor" and not entity.entity_id.startswith("sensor.gaming_status_"):
            new_id = entity.entity_id.replace("sensor.", "sensor.gaming_status_")
            
            # Map legacy suffix exceptions to the new clean standard using precise slicing
            if entity.entity_id.endswith("_gaming_status"):
                new_id = new_id[:-14] + "_master"  # -14 removes exactly "_gaming_status"
            elif entity.entity_id == "sensor.players_online":
                new_id = "sensor.gaming_status_players_online"
                
            if not registry.async_get(new_id):
                try: registry.async_update_entity(entity.entity_id, new_entity_id=new_id)
                except ValueError: pass
    
    from .const import OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS, OPT_REMOVE_DISABLED_SENSORS, DEFAULT_REMOVE_DISABLED_SENSORS
    enabled_platforms = opts.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)
    remove_disabled = opts.get(OPT_REMOVE_DISABLED_SENSORS, DEFAULT_REMOVE_DISABLED_SENSORS)

    # --- PRE-FLIGHT REGISTRY RECONCILIATION ---
    # Prevents _N suffix duplicates by ensuring the registry matches what we're about to create.
    # This happens when a unique_id drifts (source entity renamed, player name edited, etc.):
    # HA can't give the new entity the old entity_id, so it appends _2.
    def _apply_source_correction(source_id, platform):
        if platform == "playstation":
            for s in ["_online_status", "_onlinestatus"]:
                if source_id.endswith(s):
                    try:
                        old_entry = registry.async_get(source_id)
                        if old_entry and old_entry.device_id:
                            for d in er.async_entries_for_device(registry, old_entry.device_id):
                                if d.domain == "sensor" and getattr(d, "translation_key", None) == "now_playing":
                                    return d.entity_id
                    except Exception:
                        pass
                    return source_id[:-len(s)] + "_now_playing"
        elif platform == "xbox":
            for s in ["_now_playing", "_last_online"]:
                if s in source_id:
                    try:
                        wrong_entry = registry.async_get(source_id)
                        if wrong_entry and wrong_entry.device_id:
                            for d in er.async_entries_for_device(registry, wrong_entry.device_id):
                                if d.domain == "sensor" and getattr(d, "translation_key", None) == "status":
                                    return d.entity_id
                    except Exception:
                        pass
                    return source_id.replace(s, "_status")
        return source_id

    desired_uid_map = {}
    for player_name, player_data in players.items():
        safe_owner = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))
        for platform in enabled_platforms:
            raw_source = player_data.get(platform)
            if raw_source:
                corrected = _apply_source_correction(raw_source, platform)
                desired_uid_map[f"sensor.gaming_status_{safe_owner}_{platform}"] = \
                    f"gaming_status_{safe_owner}_{corrected}_tracker_v6"
        desired_uid_map[f"sensor.gaming_status_{safe_owner}_master"] = f"gaming_status_{safe_owner}_master_v6"
        desired_uid_map[f"sensor.gaming_status_{safe_owner}_pc"] = f"gaming_status_{safe_owner}_pc_v2"
    desired_uid_map["sensor.gaming_status_players_online"] = "gaming_status_players_online_count_v2"

    for desired_eid, expected_uid in desired_uid_map.items():
        base_entry = registry.async_get(desired_eid)
        uid_entry_eid = registry.async_get_entity_id("sensor", DOMAIN, expected_uid)

        if uid_entry_eid and uid_entry_eid != desired_eid:
            # Correct unique_id is stranded on a _N entity_id (the ghost from the bad boot).
            # Remove the stale base entry first, then rename the ghost to the desired name.
            if base_entry:
                try:
                    registry.async_remove(desired_eid)
                    base_entry = None
                except Exception:
                    pass
            if not base_entry:
                try:
                    registry.async_update_entity(uid_entry_eid, new_entity_id=desired_eid)
                    _LOGGER.warning("Gaming Status: Renamed ghost sensor %s → %s", uid_entry_eid, desired_eid)
                except Exception:
                    pass
        elif base_entry and base_entry.unique_id != expected_uid:
            # Base entity_id exists but holds the wrong/old unique_id; update it in-place.
            try:
                registry.async_update_entity(desired_eid, new_unique_id=expected_uid)
            except Exception:
                try:
                    registry.async_remove(desired_eid)
                except Exception:
                    pass

    xbox_ghost_sources = {}
    for p_name, p_data in players.items():
        _LOGGER.info(
            "Gaming Status: [ghost-debug] player=%r suppresses_xbox_sensors=%r platforms=%r",
            p_name, p_data.get("suppresses_xbox_sensors"),
            {plat: p_data.get(plat) for plat in PLAYER_PLATFORMS},
        )
        p_safe_owner = re.sub(r'[^a-z0-9_]', '_', p_name.lower().replace(" ", "_"))
        for xbox_entity_id in p_data.get("suppresses_xbox_sensors", []):
            sources = xbox_ghost_sources.setdefault(xbox_entity_id, [])
            for plat in PLAYER_PLATFORMS:
                if plat == "xbox" or plat not in enabled_platforms: continue
                # The ghost check needs Gaming Status's OWN derived sensor (which
                # publishes a sanitized `current_game` attribute), not the raw
                # source entity configured here -- raw platform integrations
                # (e.g. the official Steam sensor) have no such attribute and
                # completely different state semantics, so comparing against
                # them can never match.
                if p_data.get(plat): sources.append(f"sensor.gaming_status_{p_safe_owner}_{plat}")
    _LOGGER.info("Gaming Status: [ghost-debug] xbox_ghost_sources = %r", xbox_ghost_sources)

    for player_name, player_data in players.items():
        exclude_games = player_data.get("exclude_games", [])
        rules = parental_rules.get(player_name, {})
        safe_owner = re.sub(r'[^a-z0-9_]', '_', player_name.lower().replace(" ", "_"))

        # --- ORPHANED SENSOR GARBAGE COLLECTION ---
        if remove_disabled:
            for platform in PLAYER_PLATFORMS:
                if platform not in enabled_platforms:
                    target_id = f"sensor.gaming_status_{safe_owner}_{platform}"
                    if registry.async_get(target_id):
                        registry.async_remove(target_id)

        pc_platforms_present = []

        for platform in enabled_platforms:
            entity_id = player_data.get(platform)
            if entity_id:
                ghosted_by = xbox_ghost_sources.get(entity_id, []) if platform == "xbox" else []
                if platform == "xbox":
                    _LOGGER.info("Gaming Status: [ghost-debug] %s ghosted_by=%r", entity_id, ghosted_by)
                ps3_entity_id = player_data.get("ps3") if platform == "playstation" else None
                sensor_entity = PersistentStatusSensor(hass, entity_id, platform, player_name, ghosted_by, exclude_games, active_settings, global_exclusions, available_avatars, ps3_entity_id=ps3_entity_id)
                ents.append(sensor_entity)
                hass.data.setdefault(DOMAIN, {}).setdefault("platform_sensors", {})[sensor_entity.entity_id] = sensor_entity

                # Register PC platforms in strict hierarchy order for the Sub-Master
                if platform in ["playnite", "custom", "steam", "discord"]:
                    pc_platforms_present.append(f"sensor.gaming_status_{safe_owner}_{platform}")

        # Spawn PC Sub-Master if any PC platforms exist
        if pc_platforms_present:
            # Sort the entities to ensure strict Double-Dip Priority: Playnite -> Custom -> Steam -> Discord
            priority_order = {"playnite": 0, "custom": 1, "steam": 2, "discord": 3}
            pc_platforms_present.sort(key=lambda x: priority_order.get(x.split("_")[-1], 99))
            ents.append(PCGamingSensor(hass, player_name, pc_platforms_present, active_settings["SAME_GAME_PREFIX_WORDS"], active_settings["MASTER_HANDOFF_GRACE_SECONDS"]))
        else:
            # Garbage Collection: Destroy orphaned PC sensor if all PC platforms are removed
            target_id = f"sensor.gaming_status_{safe_owner}_pc_status"
            if registry.async_get(target_id):
                registry.async_remove(target_id)

        master_sensor = MasterGamingSensor(hass, player_name, player_data, rules, active_settings["SAME_GAME_PREFIX_WORDS"], active_settings["MASTER_HANDOFF_GRACE_SECONDS"])
        ents.append(master_sensor)
        hass.data.setdefault(DOMAIN, {}).setdefault("master_sensors", {})[master_sensor.entity_id] = master_sensor

    ents.append(GlobalOnlineCountSensor(hass, players))
    async_add_entities(ents)

def _load_opt_json(options, key, fallback):
    raw = options.get(key)
    if not raw: return fallback
    try: return json.loads(raw)
    except (TypeError, ValueError): return fallback