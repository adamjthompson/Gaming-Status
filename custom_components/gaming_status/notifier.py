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

from .const import (
    OPT_PLAYERS,
    OPT_ENDPOINTS,
    OPT_WEEKLY_REPORT,
    OPT_PARENTAL,
    OPT_GLOBAL_EXCLUSIONS,
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

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _opts(self) -> dict:
        return self._entry.options

    def _players(self) -> dict:
        return _load_json(self._opts.get(OPT_PLAYERS), {})

    def _endpoints(self) -> dict:
        return _load_json(self._opts.get(OPT_ENDPOINTS), {})

    def _weekly_report(self) -> dict:
        return _load_json(self._opts.get(OPT_WEEKLY_REPORT), {})

    def _parental(self) -> dict:
        return _load_json(self._opts.get(OPT_PARENTAL), {})

    def _global_exclusions(self) -> list:
        return _load_json(self._opts.get(OPT_GLOBAL_EXCLUSIONS), [])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        self._startup_time = datetime.now()

        # OPTIMIZATION: Only listen to the specific master sensors, not the global event bus
        players = self._players()
        master_entities = [
            f"sensor.{p.lower().replace(' ', '_')}_gaming_status" for p in players
        ]
        
        if master_entities:
            self._unsub_listener = async_track_state_change_event(
                self.hass, master_entities, self._handle_state_change
            )

        report = self._weekly_report()
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
        if self._unsub_listener:
            self._unsub_listener()
        if self._unsub_weekly:
            self._unsub_weekly()
        if self._unsub_parental:
            self._unsub_parental()

    # ------------------------------------------------------------------
    # Generic Notification Helpers
    # ------------------------------------------------------------------
    
    def _format_duration(self, minutes: int) -> str:
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        hours = minutes // 60
        mins = minutes % 60
        hour_str = f"1 hour" if hours == 1 else f"{hours} hours"
        if mins == 0:
            return hour_str
        return f"{hour_str} and {mins} minute{'s' if mins != 1 else ''}"
    
    async def _send_to_endpoint(
        self, 
        ep_id: str, 
        message: str, 
        image_url: str = None, 
        game_title: str = None, 
        duration_str: str = None, 
        event_type: str = "info"
    ) -> None:
        """Helper to dispatch a message, formatting specifically for Discord Embeds vs Mobile App."""
        endpoints = self._endpoints()
        dest = endpoints.get(ep_id)
        
        if not dest:
            return
            
        service_str = dest.get("notifier", "")
        if not service_str or "." not in service_str:
            return
            
        domain, service = service_str.split(".", 1)
        
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Gaming Status: notification skipped, service %s.%s not found", domain, service)
            return

        target_id = dest.get("target_id", "").strip()
        ep_type = dest.get("type", "Mobile App")
        
        service_data = {}

        if target_id and target_id.lower() != "n/a":
            if ep_type == "Discord":
                service_data["target"] = [target_id]
            else:
                service_data["target"] = target_id

        if ep_type == "Discord":
            service_data["message"] = message
            
            if event_type == "start":
                color = 65280
            elif event_type == "stop":
                color = 16711680
            else:
                color = 3447003 

            embed = {"color": color}
            if game_title:
                embed["title"] = game_title
            if duration_str:
                embed["description"] = f"Duration: {duration_str}"
            if image_url:
                embed["image"] = {"url": image_url}
                
            service_data["data"] = {"embed": embed}
            
        else:
            final_message = message
            if duration_str:
                final_message += f"\nDuration: {duration_str}"
            
            service_data["message"] = final_message
            if image_url and ep_type != "SMS":
                service_data["data"] = {"image": image_url}
            
        try:
            await self.hass.services.async_call(domain, service, service_data)
        except Exception as exc:
            _LOGGER.warning("Gaming Status: notification failed for endpoint '%s': %s", ep_id, exc)

    # ------------------------------------------------------------------
    # State change handler — session start/stop notifications
    # ------------------------------------------------------------------

    async def _handle_state_change(self, event) -> None:
        if self._startup_time and datetime.now() - self._startup_time < timedelta(seconds=30):
            return

        entity_id = event.data.get("entity_id", "")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        
        if not old_state or not new_state:
            return

        for s in (old_state.state, new_state.state):
            if s in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return

        old_game = " ".join(str(old_state.state).split())
        new_game = " ".join(str(new_state.state).split())

        exclusions = [x.strip().lower() for x in self._global_exclusions()]
        ignored = [STATE_UNAVAILABLE, STATE_UNKNOWN, "offline"]

        players = self._players()
        target_player = None
        for player_name, player_data in players.items():
            safe = player_name.lower().replace(" ", "_")
            if entity_id == f"sensor.{safe}_gaming_status":
                target_player = player_name
                break

        if target_player is None:
            return

        user_config = players.get(target_player, {})

        old_clean = old_game.lower().strip()
        new_clean = new_game.lower().strip()
        old_off = old_clean in (["offline"] + ignored) or old_clean in exclusions
        new_off = new_clean in (["offline"] + ignored) or new_clean in exclusions

        is_start = old_off and not new_off
        is_switch = not old_off and not new_off and old_game != new_game
        is_end = not old_off and new_off

        if not (is_start or is_switch or is_end):
            return

        start_dests = user_config.get("notify_start_destinations", [])
        end_dests = user_config.get("notify_end_destinations", [])

        if is_start or is_switch:
            image_url = None
            old_url = old_state.attributes.get("game_cover_art") if old_state else None
            
            for _ in range(8):
                await asyncio.sleep(2)
                current_state = self.hass.states.get(entity_id)
                if current_state:
                    current_url = current_state.attributes.get("game_cover_art") or current_state.attributes.get("cached_game_cover")
                    
                    if current_url:
                        if is_switch and current_url == old_url:
                            continue
                        image_url = current_url
                        break

            event_verb = "started playing" if is_start else "switched to"
            for ep_id in start_dests:
                await self._send_to_endpoint(
                    ep_id, 
                    message=f"{target_player} {event_verb} {new_game}", 
                    image_url=image_url,
                    game_title=new_game,
                    event_type="start"
                )
                
        elif is_end:
            image_url = old_state.attributes.get("game_cover_art") or old_state.attributes.get("cached_game_cover")
            
            duration_str = None
            start_time_str = old_state.attributes.get("play_start_time")
            if start_time_str:
                try:
                    start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    now_dt = datetime.now(start_dt.tzinfo) if start_dt.tzinfo else datetime.now()
                    diff = now_dt - start_dt
                    total_minutes = int(diff.total_seconds() / 60)
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    
                    if hours > 0:
                        duration_str = f"{hours}h {minutes}m"
                    else:
                        duration_str = f"{minutes}m"
                except Exception:
                    pass

            for ep_id in end_dests:
                await self._send_to_endpoint(
                    ep_id, 
                    message=f"{target_player} finished playing {old_game}", 
                    image_url=image_url,
                    game_title=old_game,
                    duration_str=duration_str,
                    event_type="stop"
                )

    # ------------------------------------------------------------------
    # Parental controls
    # ------------------------------------------------------------------

    async def _check_parental_controls(self, now) -> None:
        parental = self._parental()
        players = self._players()
        if not parental:
            return

        now_dt = datetime.now()
        is_weekend = now_dt.weekday() >= 5

        for player_name, rules in parental.items():
            safe_player = player_name.lower().replace(" ", "_")
            master_entity = f"sensor.{safe_player}_gaming_status"
            master_state = self.hass.states.get(master_entity)
            
            if not master_state:
                continue

            # ---- Screen time ----
            st_rule = rules.get("screen_time", {})
            if st_rule.get("enabled"):
                limit_minutes = (
                    st_rule.get("weekend_minutes", 180)
                    if is_weekend
                    else st_rule.get("weekday_minutes", 120)
                )
                st_key = f"{safe_player}_screen_time"
                st_repeat = int(st_rule.get("repeat", 0))
                
                today_hours = float(master_state.attributes.get("total_daily_hours", 0))
                today_minutes = int(today_hours * 60)
                
                if today_minutes >= limit_minutes:
                    is_playing = master_state.state.lower() not in ("offline", "unavailable", "unknown")
                    last_fired = self._triggered_parental_events.get(st_key)
                    
                    if is_playing:
                        should_fire = False
                        if last_fired is None:
                            should_fire = True
                        elif st_repeat > 0 and (now_dt - last_fired).total_seconds() >= (st_repeat * 60):
                            should_fire = True
                            
                        if should_fire:
                            self._triggered_parental_events[st_key] = now_dt
                            action = st_rule.get("action", "")
                            overage = today_minutes - limit_minutes
                            
                            if overage <= 1:
                                message = f"{player_name} has reached the {limit_minutes}-minute screen time limit."
                            else:
                                message = f"{player_name} has exceeded the {limit_minutes}-minute screen time limit by {overage} minutes ({today_minutes} minutes total)."
                                
                            await self._fire_parental_action(player_name, action, message)
                elif today_minutes < limit_minutes:
                    self._triggered_parental_events.pop(st_key, None)

            # ---- Curfew ----
            cf_rule = rules.get("curfew", {})
            if cf_rule.get("enabled"):
                cf_key = f"{safe_player}_curfew"
                curfew_time = (
                    cf_rule.get("weekend", "23:00")
                    if is_weekend
                    else cf_rule.get("weekday", "22:00")
                )
                cf_repeat = int(cf_rule.get("repeat", 0))
                
                try:
                    c_hour, c_min = map(int, curfew_time.split(":"))
                    curfew_dt = now_dt.replace(hour=c_hour, minute=c_min, second=0, microsecond=0)
                    
                    if now_dt >= curfew_dt:
                        is_playing = master_state.state.lower() not in ("offline", "unavailable", "unknown")
                        last_fired = self._triggered_parental_events.get(cf_key)
                        
                        if is_playing:
                            should_fire = False
                            if last_fired is None:
                                should_fire = True
                            elif cf_repeat > 0 and (now_dt - last_fired).total_seconds() >= (cf_repeat * 60):
                                list_fire = True
                                
                            if should_fire:
                                self._triggered_parental_events[cf_key] = now_dt
                                pretty = datetime.strptime(curfew_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                                action = cf_rule.get("action", "")
                                
                                overage_minutes = int((now_dt - curfew_dt).total_seconds() / 60)
                                
                                if overage_minutes <= 1:
                                    message = f"{player_name} has reached the {pretty} curfew."
                                else:
                                    overage_text = self._format_duration(overage_minutes)
                                    message = f"{player_name} has exceeded the {pretty} curfew by {overage_text}."
                                    
                                await self._fire_parental_action(player_name, action, message)
                    elif now_dt < curfew_dt:
                        self._triggered_parental_events.pop(cf_key, None)
                except (ValueError, AttributeError):
                    pass

    async def _fire_parental_action(
        self, player_name: str, action_service: str, message: str
    ) -> None:
        if not action_service or action_service == "none":
            return
            
        _LOGGER.info("Gaming Status parental control triggered for %s: %s", player_name, message)
        
        if action_service.startswith("endpoint_"):
            ep_id = action_service.replace("endpoint_", "", 1)
            await self._send_to_endpoint(ep_id, message, event_type="info")
            
        elif "." in action_service:
            domain, service = action_service.split(".", 1)
            if self.hass.services.has_service(domain, service):
                try:
                    await self.hass.services.async_call(domain, service, {"message": message})
                except Exception as exc:
                    _LOGGER.warning("Gaming Status: parental action failed: %s", exc)
            else:
                _LOGGER.warning("Gaming Status: parental action skipped, service %s.%s not found", domain, service)

    # ------------------------------------------------------------------
    # Weekly report
    # ------------------------------------------------------------------

    async def _trigger_weekly_report(self, now) -> None:
        if now.weekday() != self._run_day:
            return

        report_config = self._weekly_report()
        if not report_config.get("enabled"):
            return

        players = self._players()
        assigned = report_config.get("destinations", [])

        lines = [f"**Weekly Gaming Report** — {datetime.now().strftime('%B %d, %Y')}"]

        for player_name in players:
            safe = player_name.lower().replace(" ", "_")
            master_entity = f"sensor.{safe}_gaming_status"
            state = self.hass.states.get(master_entity)
            
            if not state:
                continue
                
            attrs = state.attributes
            weekly_hours = attrs.get("total_weekly_hours_last_week", attrs.get("total_weekly_hours", 0))
            last_game = attrs.get("last_played_game", "Unknown")
            
            lines.append(f"\n**{player_name}**: {weekly_hours}h total — Last game: {last_game}")

        message = "\n".join(lines)

        for ep_id in assigned:
            await self._send_to_endpoint(ep_id, message, event_type="info")