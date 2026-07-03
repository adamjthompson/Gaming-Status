"""Gaming Status notifier — session alerts, weekly report, parental controls."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change, async_track_time_interval
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from .const import (
    OPT_PLAYERS,
    OPT_ENDPOINTS,
    OPT_WEEKLY_REPORT,
    OPT_PARENTAL,
    OPT_GLOBAL_EXCLUSIONS,
    OPT_NOTIFY_ARTWORK,
    OPT_DISCORD_COLORS,
)

_LOGGER = logging.getLogger(__name__)


def _load_json(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class GamingNotifier:
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = config_entry
        self._unsub_listener = None
        self._unsub_weekly = None
        self._unsub_parental = None
        self._startup_time: datetime | None = None
        self._triggered_parental_events: dict = {}

        # Instant O(1) lookup map (entity_id -> player_name)
        # Built once at startup since entities require a full system reload to change
        initial_players = _load_json(self._entry.options.get(OPT_PLAYERS), {})
        self._entity_player_map = {
            f"sensor.gaming_status_{p.lower().replace(' ', '_')}_master": p
            for p in initial_players
        }

    # ------------------------------------------------------------------
    # Dynamic Properties (The Ultimate Gatekeeper)
    # ------------------------------------------------------------------

    @property
    def _enable_notifications(self) -> bool:
        from .const import OPT_ENABLE_NOTIFICATIONS
        return self._entry.options.get(OPT_ENABLE_NOTIFICATIONS, False)

    @property
    def _enable_parental(self) -> bool:
        from .const import OPT_ENABLE_PARENTAL
        return self._entry.options.get(OPT_ENABLE_PARENTAL, False)

    @property
    def _cached_players(self) -> dict: return _load_json(self._entry.options.get(OPT_PLAYERS), {})
    @property
    def _cached_endpoints(self) -> dict: return _load_json(self._entry.options.get(OPT_ENDPOINTS), {})
    @property
    def _cached_weekly(self) -> dict: return _load_json(self._entry.options.get(OPT_WEEKLY_REPORT), {})
    @property
    def _cached_parental(self) -> dict: return _load_json(self._entry.options.get(OPT_PARENTAL), {})
    @property
    def _cached_discord_colors(self) -> dict: return _load_json(self._entry.options.get(OPT_DISCORD_COLORS), {})
    @property
    def _cached_notify_artwork(self) -> str: return self._entry.options.get(OPT_NOTIFY_ARTWORK, "game_cover_art")
    @property
    def _cached_exclusions(self) -> list: return [x.strip().lower() for x in _load_json(self._entry.options.get(OPT_GLOBAL_EXCLUSIONS), [])]
    
    # Keep legacy getters functioning to support older function calls
    def _players(self) -> dict: return self._cached_players
    def _endpoints(self) -> dict: return self._cached_endpoints
    def _weekly_report(self) -> dict: return self._cached_weekly
    def _parental(self) -> dict: return self._cached_parental
    def _global_exclusions(self) -> list: return self._cached_exclusions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        self._startup_time = dt_util.now()

        master_entities = list(self._entity_player_map.keys())

        if master_entities:
            self._unsub_listener = async_track_state_change_event(
                self.hass, master_entities, self._handle_state_change
            )

        report = self._cached_weekly
        run_day = int(report.get("day", 0))
        run_time_str = report.get("time", "09:00")
        try:
            target_hour, target_minute = map(int, run_time_str.split(":"))
        except ValueError:
            target_hour, target_minute = 9, 0

        self._run_day = run_day
        self._unsub_weekly = async_track_time_change(
            self.hass,
            self._trigger_weekly_report,
            hour=target_hour,
            minute=target_minute,
            second=0,
        )

        self._unsub_parental = async_track_time_interval(
            self.hass, self._check_parental_controls, timedelta(minutes=1)
        )

    async def async_stop(self) -> None:
        if self._unsub_listener: self._unsub_listener()
        if self._unsub_weekly: self._unsub_weekly()
        if self._unsub_parental: self._unsub_parental()

    # ------------------------------------------------------------------
    # Generic Notification Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hex_to_int(hex_color: str, fallback: int) -> int:
        """Convert a hex string like '#FF5500' or 'FF5500' to a Discord integer."""
        try:
            return int(hex_color.lstrip("#"), 16)
        except (ValueError, AttributeError):
            return fallback

    def _resolve_discord_color(
        self,
        event_type: str,
        state_obj,
    ) -> int:
        """Return the Discord embed color integer for this endpoint and event type."""
        DEFAULT_START = 65280      # green
        DEFAULT_STOP  = 16711680   # red
        DEFAULT_INFO  = 3447003    # blue

        PLATFORM_COLORS = {
            "steam":       175599,   # rgb(2, 173, 239)
            "xbox":        752656,   # rgb(11, 124, 16)
            "playstation": 12423,    # rgb(0, 48, 135)
            "custom":      6566500,  # rgb(100, 50, 100)
        }

        default_for_type = (
            DEFAULT_START if event_type == "start"
            else DEFAULT_STOP if event_type == "stop"
            else DEFAULT_INFO
        )

        colors_config = self._cached_discord_colors
        mode = colors_config.get("mode", "default")

        if mode == "platform" and state_obj:
            active_platform = state_obj.attributes.get("active_platform", "").lower()
            for key in PLATFORM_COLORS:
                if key in active_platform:
                    return PLATFORM_COLORS[key]
            return default_for_type

        if mode == "game" and state_obj:
            hex_color = state_obj.attributes.get("game_dominant_color", "")
            if hex_color:
                return self._hex_to_int(hex_color, default_for_type)

        if mode == "custom":
            if event_type == "start":
                return self._hex_to_int(colors_config.get("color_start", ""), DEFAULT_START)
            elif event_type == "stop":
                return self._hex_to_int(colors_config.get("color_end", ""), DEFAULT_STOP)
            else:
                return self._hex_to_int(colors_config.get("color_parental", ""), DEFAULT_INFO)

        return default_for_type

    def _format_duration(self, minutes: int) -> str:
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        hours = minutes // 60
        mins = minutes % 60
        hour_str = f"1 hour" if hours == 1 else f"{hours} hours"
        if mins == 0: return hour_str
        return f"{hour_str} and {mins} minute{'s' if mins != 1 else ''}"

    async def _send_to_endpoint(
        self,
        ep_id: str,
        message: str,
        image_url: str = None,
        game_title: str = None,
        event_type: str = "info",
        state_obj = None,
    ) -> bool:
        """Dispatch a notification to a configured endpoint."""
        dest = self._cached_endpoints.get(ep_id)
        if not dest:
            _LOGGER.warning("Gaming Status: endpoint '%s' not found in config", ep_id)
            return False

        service_str = dest.get("service", "")
        if not service_str or "." not in service_str:
            service_str = dest.get("notifier", "")
            if not service_str or "." not in service_str:
                _LOGGER.warning("Gaming Status: endpoint '%s' has no valid service configured", ep_id)
                return False

        domain, service = service_str.split(".", 1)

        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Gaming Status: notification skipped, service %s.%s not found", domain, service)
            return False

        target_id = dest.get("target_id", "").strip()
        ep_type = dest.get("type", "Mobile App")

        service_data = {}

        if target_id and target_id.lower() != "n/a":
            if ep_type == "Discord": service_data["target"] = [target_id]
            else: service_data["target"] = target_id

        if ep_type == "Discord":
            # Discord API strictly rejects relative local paths. If domain appending failed, strip the image to save the notification!
            if image_url and not image_url.startswith("http"):
                image_url = None
                
            color = self._resolve_discord_color(event_type, state_obj)
            embed = {"color": color}
            
            # Placing text in "description" puts it INSIDE the colored bar
            if event_type == "info":
                embed["title"] = "Gaming Status"
                embed["description"] = message
            else:
                if game_title: embed["title"] = game_title
                embed["description"] = message
                
            if image_url: embed["image"] = {"url": image_url}
            
            service_data["message"] = "" 
            service_data["data"] = {"embed": embed}
                
        else: # Standard Mobile App / SMS
            service_data["message"] = message
            
            if event_type == "start":
                service_data["title"] = game_title if game_title else "Gaming Status"
            elif event_type == "stop":
                service_data["title"] = f"Finished {game_title}" if game_title else "Gaming Session Ended"
            elif event_type == "parental":
                service_data["title"] = game_title if game_title else "Parental Controls"
            else:
                service_data["title"] = "Gaming Status"
                
            if image_url and ep_type != "SMS": 
                service_data["data"] = {"image": image_url}

        try:
            await self.hass.services.async_call(domain, service, service_data)
            return True
        except Exception as exc:
            _LOGGER.warning("Gaming Status: notification failed for endpoint '%s': %s", ep_id, exc)
            return False

    # ------------------------------------------------------------------
    # Cover art resolution
    # ------------------------------------------------------------------

    async def _make_external_url(self, image_url: str | None, game_name: str) -> str | None:
        """Convert a local HA path into a public URL, or fallback to the remote SteamGridDB cache."""
        if not image_url or not image_url.startswith("/"):
            return image_url

        try:
            from urllib.parse import urlparse
            import ipaddress
            import socket
            
            base_url = get_url(self.hass, prefer_external=True)
            host = urlparse(base_url).hostname or ""
            
            try:
                resolved_ip = socket.gethostbyname(host)
                is_local = ipaddress.ip_address(resolved_ip).is_private
            except Exception:
                is_local = host.endswith((".local", ".lan", ".internal"))
                
            if not base_url.startswith("https://") or is_local:
                raise ValueError("No external domain available")
                
            return f"{base_url.rstrip('/')}{image_url}"
            
        except Exception:
            try:
                from .utils import get_cached_remote_url
                target_type = "hero" if "hero" in self._cached_notify_artwork else "logo" if "logo" in self._cached_notify_artwork else "grid"
                remote_url = await self.hass.async_add_executor_job(get_cached_remote_url, game_name, target_type)
                return remote_url or image_url
            except Exception as e:
                return image_url

    async def _resolve_cover_art(
        self,
        player_name: str,
        user_config: dict,
        old_state,
        is_switch: bool,
    ) -> str | None:
        """Wait for the user's preferred artwork to be available on the active platform sensor."""
        if self._cached_notify_artwork == "none":
            return None

        safe = player_name.lower().replace(" ", "_")
        old_url = old_state.attributes.get(self._cached_notify_artwork) if old_state else None

        # Check Master, Sub-Master, and all active platforms (including Discord) for artwork
        platform_entity_ids = [f"sensor.gaming_status_{safe}_master", f"sensor.gaming_status_{safe}_pc"]
        for platform in ("steam", "xbox", "playstation", "custom", "discord"):
            if user_config.get(platform):
                platform_entity_ids.append(f"sensor.gaming_status_{safe}_{platform}")

        def _read_cover() -> str | None:
            """Return the preferred artwork URL found across platform sensors."""
            for pid in platform_entity_ids:
                pstate = self.hass.states.get(pid)
                if not pstate:
                    continue
                
                if str(pstate.state).lower() in ("offline", "unavailable", "unknown", "idle"):
                    continue

                # Grab the preferred art style
                url = pstate.attributes.get(self._cached_notify_artwork)
                
                # Resilient fallback if the preferred art is missing for this specific game
                if not url:
                    url = pstate.attributes.get("game_cover_art") or pstate.attributes.get("cached_game_cover")
                
                if not url:
                    continue

                if is_switch and url == old_url:
                    continue
                return url
            return None

        url = _read_cover()
        if url:
            return url

        _LOGGER.debug(
            "Gaming Status: artwork not yet ready for %s, waiting up to 30s",
            player_name,
        )
        for _ in range(15):
            await asyncio.sleep(2)
            url = _read_cover()
            if url:
                return url

        _LOGGER.debug(
            "Gaming Status: artwork did not arrive in time for %s, sending without image",
            player_name,
        )
        return None

    # ------------------------------------------------------------------
    # State change handler
    # ------------------------------------------------------------------

    async def _handle_state_change(self, event) -> None:
        if not self._enable_notifications:
            return
            
        if self._startup_time and dt_util.now() - self._startup_time < timedelta(seconds=30):
            return

        entity_id = event.data.get("entity_id", "")
        target_player = self._entity_player_map.get(entity_id)
        if not target_player: return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state: return

        for s in (old_state.state, new_state.state):
            if s in (STATE_UNAVAILABLE, STATE_UNKNOWN): return

        old_game = " ".join(str(old_state.state).split())
        new_game = " ".join(str(new_state.state).split())

        ignored = [STATE_UNAVAILABLE, STATE_UNKNOWN, "offline"]
        user_config = self._cached_players.get(target_player, {})

        old_clean = old_game.lower().strip()
        new_clean = new_game.lower().strip()
        old_off = old_clean in (["offline"] + ignored) or old_clean in self._cached_exclusions
        new_off = new_clean in (["offline"] + ignored) or new_clean in self._cached_exclusions

        is_start = old_off and not new_off
        # Use old_clean != new_clean to prevent case-sensitivity triggers
        is_switch = not old_off and not new_off and old_clean != new_clean
        is_end = not old_off and new_off

        now = dt_util.now()
        if not hasattr(self, "_last_start_time"): self._last_start_time = {}

        if is_start:
            last_start = self._last_start_time.get(target_player)
            # COOLDOWN: If a session just started less than 90 seconds ago, block the duplicate bounce!
            if last_start and (now - last_start).total_seconds() < 90:
                return
                
            self._last_start_time[target_player] = now
            
            # SMART POLLING DELAY: Give the API up to 15 seconds to fetch artwork, checking every 3 seconds
            refreshed_state = None
            for _ in range(5):
                await asyncio.sleep(3)
                temp_state = self.hass.states.get(entity_id)
                if temp_state and temp_state.state.lower() not in (["offline", "unknown", "unavailable"] + self._cached_exclusions):
                    refreshed_state = temp_state
                    
                    # If the API successfully populated custom art (not just the fallback Akamai link), stop waiting!
                    art_check = refreshed_state.attributes.get("game_hero_art") or refreshed_state.attributes.get("game_cover_art")
                    color_check = refreshed_state.attributes.get("game_dominant_color")
                    
                    from .utils import url_host_matches
                    if art_check and not url_host_matches(art_check, "akamaihd.net"):
                        # If Discord is set to Game Color, wait an extra tick for the extraction algorithm to finish!
                        if self._cached_discord_colors.get("mode") == "game" and not color_check:
                            continue
                        break
                else:
                    return # The game was closed instantly, abort notification
            
            if refreshed_state:
                new_state = refreshed_state
                new_game = " ".join(str(new_state.state).split())
                
        elif is_switch:
            last_start = self._last_start_time.get(target_player)
            # If a game launcher transitioned to the real game within 90 seconds, suppress the switch alert
            if last_start and (now - last_start).total_seconds() < 90:
                return

        if not (is_start or is_switch or is_end): return

        start_dests = user_config.get("notify_start_destinations", [])
        end_dests = user_config.get("notify_end_destinations", [])

        duration_str = None
        if is_switch or is_end:
            start_time_str = old_state.attributes.get("play_start_time")
            if start_time_str:
                try:
                    start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    
                    # TRUE END TIME CALCULATION: Default to now, but override if a session cleanly ended
                    end_dt = datetime.now(start_dt.tzinfo) if start_dt.tzinfo else datetime.now()
                    
                    if is_end:
                        last_online_str = old_state.attributes.get("last_online_valid_timestamp")
                        if last_online_str:
                            temp_end = datetime.fromisoformat(last_online_str.replace("Z", "+00:00"))
                            if not temp_end.tzinfo:
                                temp_end = temp_end.replace(tzinfo=start_dt.tzinfo)
                            # Only apply if it doesn't result in a negative time glitch
                            if temp_end > start_dt:
                                end_dt = temp_end

                    diff = end_dt - start_dt
                    total_minutes = int(diff.total_seconds() / 60)
                    if total_minutes > 0:
                        hours, minutes = total_minutes // 60, total_minutes % 60
                        duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                except Exception: pass

        if is_start or is_switch:
            if is_switch:
                msg = f"{target_player} switched games after {duration_str}" if duration_str else f"{target_player} switched games"
                display_title = f"{old_game} > {new_game}"
            else:
                msg = f"{target_player} started playing"
                display_title = new_game

            raw_url = await self._resolve_cover_art(
                target_player, user_config, old_state, is_switch
            )
            image_url = await self._make_external_url(raw_url, new_game)

            for ep_id in start_dests:
                await self._send_to_endpoint(ep_id, message=msg, image_url=image_url, game_title=display_title, event_type="start", state_obj=new_state)

        elif is_end:
            msg = f"{target_player} played for {duration_str}" if duration_str else f"{target_player} finished playing"
            
            if self._cached_notify_artwork == "none":
                raw_url = None
            else:
                raw_url = old_state.attributes.get(self._cached_notify_artwork)
                if not raw_url:
                    raw_url = old_state.attributes.get("game_cover_art") or old_state.attributes.get("cached_game_cover")

            image_url = await self._make_external_url(raw_url, old_game)
            
            for ep_id in end_dests:
                # Pass the OLD state so the color is fully preserved!
                await self._send_to_endpoint(ep_id, message=msg, image_url=image_url, game_title=old_game, event_type="stop", state_obj=old_state)

    # ------------------------------------------------------------------
    # Parental controls
    # ------------------------------------------------------------------

    async def _check_parental_controls(self, now) -> None:
        if not self._enable_parental or not self._cached_parental: return

        now_dt = dt_util.now()
        is_weekend = now_dt.weekday() >= 5

        for player_name, rules in self._cached_parental.items():
            safe_player = player_name.lower().replace(" ", "_")
            master_entity = f"sensor.gaming_status_{safe_player}_master"
            master_state = self.hass.states.get(master_entity)
            if not master_state: continue
            
            user_config = self._cached_players.get(player_name, {})
            fallback_dests = list(set(user_config.get("notify_start_destinations", []) + user_config.get("notify_end_destinations", [])))

            is_playing = master_state.state.lower() not in ("offline", "unavailable", "unknown")

            # --- SCREEN TIME LIMIT ---
            st_rule = rules.get("screen_time", {})
            if st_rule.get("enabled"):
                st_key = f"{safe_player}_screen_time"
                st_repeat = int(st_rule.get("repeat", 0))

                limit = int(
                    st_rule.get("weekend_minutes", 180)
                    if is_weekend
                    else st_rule.get("weekday_minutes", 120)
                )
                
                try:
                    raw_hours = master_state.attributes.get("total_daily_hours", 0)
                    if raw_hours is None: raw_hours = 0
                    today_minutes = int(float(raw_hours) * 60)
                except (ValueError, TypeError):
                    continue

                if today_minutes >= limit:
                    if is_playing:
                        overage = max(0, today_minutes - limit)
                        last_notified_overage = self._triggered_parental_events.get(st_key)

                        should_notify = False
                        if last_notified_overage is None:
                            should_notify = True
                        elif st_repeat > 0 and (overage - last_notified_overage) >= st_repeat:
                            should_notify = True

                        if should_notify:
                            if overage > 0:
                                msg = f"{player_name} has exceeded the {limit}-minute screen time limit by {overage} minutes ({today_minutes} minutes total)."
                            else:
                                msg = f"{player_name} has reached the {limit}-minute screen time limit."

                            current_game = master_state.state if is_playing else None
                            parental_image = None
                            if current_game and self._cached_notify_artwork != "none":
                                raw_url = master_state.attributes.get(self._cached_notify_artwork)
                                if not raw_url:
                                    raw_url = master_state.attributes.get("game_cover_art") or master_state.attributes.get("cached_game_cover")
                                parental_image = await self._make_external_url(raw_url, current_game)

                            action = st_rule.get("action", "none")
                            if not action or action == "none":
                                action = fallback_dests

                            if await self._fire_parental_action(
                                player_name, action, msg,
                                game_title=current_game,
                                image_url=parental_image,
                                state_obj=master_state,
                            ):
                                self._triggered_parental_events[st_key] = overage
                else:
                    self._triggered_parental_events.pop(st_key, None)

            # --- CURFEW ---
            cf_rule = rules.get("curfew", {})
            if cf_rule.get("enabled"):
                cf_key = f"{safe_player}_curfew"
                curfew_time = cf_rule.get("weekend", "23:00") if is_weekend else cf_rule.get("weekday", "22:00")
                cf_repeat = int(cf_rule.get("repeat", 0))
                try:
                    c_hour, c_min = map(int, curfew_time.split(":"))
                    curfew_dt = now_dt.replace(hour=c_hour, minute=c_min, second=0, microsecond=0)

                    if now_dt >= curfew_dt:
                        last_fired = self._triggered_parental_events.get(cf_key)

                        if is_playing and (
                            last_fired is None or
                            (cf_repeat > 0 and (now_dt - last_fired).total_seconds() >= (cf_repeat * 60))
                        ):
                            overage_minutes = int((now_dt - curfew_dt).total_seconds() / 60)
                            pretty_time = datetime.strptime(curfew_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                            if overage_minutes > 1:
                                msg = f"{player_name} has exceeded the {pretty_time} curfew by {overage_minutes} minutes."
                            else:
                                msg = f"{player_name} has reached the {pretty_time} curfew."
                                
                            current_game = master_state.state if is_playing else None
                            parental_image = None
                            if current_game and self._cached_notify_artwork != "none":
                                raw_url = master_state.attributes.get(self._cached_notify_artwork)
                                if not raw_url:
                                    raw_url = master_state.attributes.get("game_cover_art") or master_state.attributes.get("cached_game_cover")
                                parental_image = await self._make_external_url(raw_url, current_game)

                            action = cf_rule.get("action", "none")
                            if not action or action == "none":
                                action = fallback_dests

                            if await self._fire_parental_action(
                                player_name, action, msg,
                                game_title=current_game,
                                image_url=parental_image,
                                state_obj=master_state,
                            ):
                                self._triggered_parental_events[cf_key] = now_dt

                    elif now_dt < curfew_dt:
                        self._triggered_parental_events.pop(cf_key, None)

                except (ValueError, AttributeError): pass

    async def _fire_parental_action(
        self,
        player_name: str,
        action_data,
        message: str,
        game_title: str = None,
        image_url: str = None,
        state_obj=None,
    ) -> bool:
        if not action_data or action_data == "none":
            return False

        targets = action_data if isinstance(action_data, list) else [action_data]
        any_succeeded = False

        for target in targets:
            if not isinstance(target, str): continue
            target = target.strip()
            if not target or target == "none": continue

            if target.startswith("endpoint_"):
                sent = await self._send_to_endpoint(
                    target.replace("endpoint_", "", 1), message,
                    image_url=image_url, game_title=game_title,
                    event_type="parental", state_obj=state_obj,
                )
            elif target in self._cached_endpoints:
                sent = await self._send_to_endpoint(
                    target, message,
                    image_url=image_url, game_title=game_title,
                    event_type="parental", state_obj=state_obj,
                )
            elif "." in target:
                domain, service = target.split(".", 1)
                if self.hass.services.has_service(domain, service):
                    try:
                        await self.hass.services.async_call(domain, service, {"message": message})
                        sent = True
                    except Exception as exc:
                        _LOGGER.warning("Gaming Status: parental action failed: %s", exc)
                        sent = False
                else:
                    _LOGGER.warning("Gaming Status: parental action skipped, service %s.%s not found", domain, service)
                    sent = False
            else:
                sent = False

            if sent:
                any_succeeded = True

        return any_succeeded

    # ------------------------------------------------------------------
    # Weekly report
    # ------------------------------------------------------------------

    async def _trigger_weekly_report(self, now) -> None:
        if not self._enable_notifications or now.weekday() != self._run_day or not self._cached_weekly.get("enabled"): return
        assigned = self._cached_weekly.get("destinations", [])
        lines = [f"**Weekly Gaming Report** — {dt_util.now().strftime('%B %d, %Y')}"]
        for player_name in self._cached_players:
            safe = player_name.lower().replace(" ", "_")
            state = self.hass.states.get(f"sensor.gaming_status_{safe}_master")
            if state:
                attrs = state.attributes
                lines.append(f"\n**{player_name}**: {attrs.get('total_weekly_hours_last_week', attrs.get('total_weekly_hours', 0))}h total — Last game: {attrs.get('last_played_game', 'Unknown')}")
        message = "\n".join(lines)
        for ep_id in assigned: await self._send_to_endpoint(ep_id, message, event_type="info")