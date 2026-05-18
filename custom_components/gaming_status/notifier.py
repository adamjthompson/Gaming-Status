import logging
import asyncio
import json
import os
from datetime import datetime, timedelta
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval

_LOGGER = logging.getLogger(__name__)

class GamingNotifier:
    def __init__(self, hass):
        self.hass = hass
        self.config_path = hass.config.path("gaming_profiles.json")
        self._unsub_listener = None
        self._unsub_weekly = None
        self._unsub_parental = None
        self._startup_time = None
        
        # State memory to prevent spamming actions every 60 seconds
        self._triggered_parental_events = {}

    async def async_start(self):
        self._startup_time = datetime.now()
        
        self._unsub_listener = self.hass.bus.async_listen(
            "state_changed", self._handle_state_change
        )
        
        config = await self._async_get_config()
        report_config = config.get("WEEKLY_REPORT", {})
        
        run_day = int(report_config.get("day", 0))
        run_time_str = report_config.get("time", "09:00")
        try: target_hour, target_minute = map(int, run_time_str.split(":"))
        except ValueError: target_hour, target_minute = 9, 0

        self._run_day = run_day
        self._unsub_weekly = async_track_time_change(
            self.hass, self._trigger_weekly_report, hour=target_hour, minute=target_minute, second=0
        )

        # PARENTAL CONTROLS ENGINE: Runs every 60 seconds
        self._unsub_parental = async_track_time_interval(
            self.hass, self._check_parental_controls, timedelta(minutes=1)
        )

    async def async_stop(self):
        if self._unsub_listener: self._unsub_listener()
        if self._unsub_weekly: self._unsub_weekly()
        if self._unsub_parental: self._unsub_parental()

    async def _async_get_config(self):
        def read_file():
            if not os.path.exists(self.config_path): return {}
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception: return {}
        return await self.hass.async_add_executor_job(read_file)

    async def _handle_state_change(self, event):
        if self._startup_time and datetime.now() - self._startup_time < timedelta(seconds=30): return

        entity_id = event.data.get("entity_id")
        if not entity_id.endswith("_gaming_status"): return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state: return
            
        if old_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN] or new_state.state in [STATE_UNAVAILABLE, STATE_UNKNOWN]: return

        old_game = " ".join(str(old_state.state).split())
        new_game = " ".join(str(new_state.state).split())
        
        config = await self._async_get_config()
        exclusions = [x.strip().lower() for x in config.get("GLOBAL_EXCLUSIONS", [])]
        ignored = [STATE_UNAVAILABLE, STATE_UNKNOWN, "offline"]
        
        old_is_game = old_game.lower() not in ignored and old_game.lower() not in exclusions
        new_is_game = new_game.lower() not in ignored and new_game.lower() not in exclusions
        safe_owner = entity_id.split('.')[1].replace("_gaming_status", "")

        # When a session completely ends, clear their trigger memory for the next session
        if old_is_game and old_game != new_game:
            if f"{safe_owner}_screentime" in self._triggered_parental_events:
                del self._triggered_parental_events[f"{safe_owner}_screentime"]
                
            # Pass BOTH the old and new attributes to accurately capture the timeline
            await self._process_game_ended(safe_owner, old_game, old_state.attributes, new_state.attributes)

        if new_is_game and old_game != new_game:
            self.hass.async_create_task(self._process_game_started(entity_id, safe_owner, new_game))

    # --- PARENTAL CONTROLS ENGINE ---
    async def _check_parental_controls(self, now):
        config = await self._async_get_config()
        parental_config = config.get("PARENTAL_CONTROLS", {})
        if not parental_config: return
        
        gaming_now = now - timedelta(hours=6)
        today_str = gaming_now.strftime("%Y-%m-%d")
        
        if getattr(self, '_last_parental_reset', None) != today_str:
            self._triggered_parental_events = {}
            self._last_parental_reset = today_str

        st_day_type = "weekend" if gaming_now.weekday() >= 5 else "weekday"
        cf_day_type = "weekend" if gaming_now.weekday() in [4, 5] else "weekday"

        now_hour_adjusted = now.hour if now.hour >= 6 else now.hour + 24
        now_minutes_since_6am = (now_hour_adjusted - 6) * 60 + now.minute

        for player_name, rules in parental_config.items():
            safe_player = player_name.lower().replace(" ", "_")
            sensor_id = f"sensor.{safe_player}_gaming_status"
            state = self.hass.states.get(sensor_id)
            
            if not state or state.state.lower() in ["offline", "unavailable", "unknown"]:
                continue

            # 1. Screen Time Check
            st_rule = rules.get("screen_time", {})
            if st_rule.get("enabled"):
                st_key = f"{safe_player}_screentime"
                daily_hours = float(state.attributes.get("total_daily_hours", 0))
                daily_minutes = int(daily_hours * 60)
                limit = int(st_rule.get(st_day_type, st_rule.get("limit_minutes", 9999)))

                if daily_minutes >= limit and st_key not in self._triggered_parental_events:
                    self._triggered_parental_events[st_key] = True
                    await self._execute_parental_action(config, player_name, st_rule, f"Screen Time Limit ({limit}m)")

            # 2. Curfew Check
            cf_rule = rules.get("curfew", {})
            if cf_rule.get("enabled"):
                cf_key = f"{safe_player}_curfew"
                curfew_time = cf_rule.get(cf_day_type, cf_rule.get("time", "23:59"))
                
                try:
                    c_hour, c_min = map(int, curfew_time.split(":"))
                    c_hour_adjusted = c_hour if c_hour >= 6 else c_hour + 24
                    curfew_minutes_since_6am = (c_hour_adjusted - 6) * 60 + c_min

                    if now_minutes_since_6am >= curfew_minutes_since_6am and cf_key not in self._triggered_parental_events:
                        self._triggered_parental_events[cf_key] = True
                        pretty_time = datetime.strptime(curfew_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                        await self._execute_parental_action(config, player_name, cf_rule, f"Curfew ({pretty_time})")
                except ValueError:
                    pass

    async def _execute_parental_action(self, config, player_name, rule, reason):
        action_type = rule.get("action_type")
        target_id = rule.get("action_target")
        if not target_id: return

        if action_type == "notify":
            dest = config.get("NOTIFICATION_ENDPOINTS", {}).get(target_id)
            if not dest: return
            service_name = dest.get("service", "").replace("notify.", "")
            if not service_name: return
            
            notify_type = dest.get("type", "discord")
            target = dest.get("target")
            
            message = f"🚨 {player_name} has reached their {reason}."
            
            service_data = {"message": message}
            if notify_type == "discord": service_data["target"] = target
            elif notify_type == "sms": service_data["target"] = [target] if target else []
            elif notify_type == "mobile": service_data["title"] = "Gaming Alert"

            try: await self.hass.services.async_call("notify", service_name, service_data)
            except Exception as e: _LOGGER.error(f"[Gaming Notifier] Notification failed: {e}")

        elif action_type == "script":
            try: await self.hass.services.async_call("script", target_id.replace("script.", ""), {})
            except Exception as e: _LOGGER.error(f"[Gaming Notifier] Script failed: {e}")

        elif action_type == "automation":
            try: await self.hass.services.async_call("automation", "trigger", {"entity_id": target_id})
            except Exception as e: _LOGGER.error(f"[Gaming Notifier] Automation failed: {e}")

    # --- SESSION NOTIFICATIONS ---
    async def _process_game_ended(self, safe_owner, game_name, old_attributes, new_attributes):
        config = await self._async_get_config()
        profiles = config.get("GAMER_PROFILES", {})
        user_config = next((data for name, data in profiles.items() if name.lower().replace(" ", "_") == safe_owner), None)
        if not user_config or not user_config.get("notifications", {}).get("notify_end"): return

        duration_str = ""
        # The final calculated play time is written to the NEW state by the sensor
        duration_sec = new_attributes.get("last_session_play_time", 0)
        
        if duration_sec > 0:
            hrs, remainder = divmod(duration_sec, 3600)
            mins, _ = divmod(remainder, 60)
            duration_str = f"{int(hrs)}h {int(mins)}m" if hrs > 0 else f"{int(mins)}m"

        # The cover art and title must be pulled from the OLD state
        friendly_name = old_attributes.get("friendly_name", "Someone").replace(" Gaming Status", "").strip()
        image_url = old_attributes.get("game_cover_art") or old_attributes.get("entity_picture") or ""
        if image_url.startswith("/"): image_url = f"{self.hass.config.api.base_url}{image_url}"

        await self._dispatch_individual_alert(config, user_config, "end", friendly_name, game_name, duration_str, image_url)

    async def _process_game_started(self, entity_id, safe_owner, game_name):
        await asyncio.sleep(15)  
        current_state = self.hass.states.get(entity_id)
        if not current_state: return
        current_game_clean = " ".join(str(current_state.state).split())
        if current_game_clean != game_name: return

        config = await self._async_get_config()
        profiles = config.get("GAMER_PROFILES", {})
        user_config = next((data for name, data in profiles.items() if name.lower().replace(" ", "_") == safe_owner), None)
        if not user_config or not user_config.get("notifications", {}).get("notify_start"): return

        attributes = current_state.attributes
        friendly_name = attributes.get("friendly_name", "Someone").replace(" Gaming Status", "").strip()
        image_url = attributes.get("game_cover_art") or attributes.get("entity_picture") or ""
        if image_url.startswith("/"): image_url = f"{self.hass.config.api.base_url}{image_url}"

        await self._dispatch_individual_alert(config, user_config, "start", friendly_name, game_name, "", image_url)

    async def _dispatch_individual_alert(self, config, user_config, event_type, friendly_name, game, duration, image_url):
        all_endpoints = config.get("NOTIFICATION_ENDPOINTS", {})
        assigned_ids = user_config.get("notifications", {}).get("assigned_endpoints", [])
        
        for endpoint_id in assigned_ids:
            dest = all_endpoints.get(endpoint_id)
            if not dest: continue

            service_name = dest.get("service", "").replace("notify.", "")
            target = dest.get("target")
            notify_type = dest.get("type", "discord")
            if not service_name: continue

            if notify_type == "discord":
                message = f"{friendly_name} started playing {game}" if event_type == "start" else f"{friendly_name} finished playing {game}"
                service_data = {"message": message, "target": target, "data": {"embed": {"title": game, "color": 5763719 if event_type == "start" else 15158332}}}
                if image_url: service_data["data"]["embed"]["image"] = {"url": image_url}
                if duration and event_type == "end": service_data["data"]["embed"]["description"] = f"Duration: {duration}"
            elif notify_type == "sms":
                message = f"🎮 {friendly_name} is now playing {game}." if event_type == "start" else f"🛑 {friendly_name} finished playing {game} (Time: {duration})."
                service_data = {"message": message, "target": [target] if target else []}
            elif notify_type == "mobile":
                message = f"🎮 {friendly_name} is now playing {game}." if event_type == "start" else f"🛑 {friendly_name} finished playing {game}."
                service_data = {"title": "Gaming Status", "message": message}
                if duration and event_type == "end": service_data["message"] += f"\nDuration: {duration}"
                if image_url: service_data["data"] = {"image": image_url, "url": image_url}

            try: await self.hass.services.async_call("notify", service_name, service_data)
            except Exception as e: _LOGGER.error(f"[Gaming Notifier] Failed: {e}")

    async def _trigger_weekly_report(self, now):
        if now.weekday() != getattr(self, '_run_day', 0): return
        config = await self._async_get_config()
        report_config = config.get("WEEKLY_REPORT", {})
        if not report_config.get("enabled", False): return
        included_players = report_config.get("included_players", [])
        if not included_players: return

        end_date = now - timedelta(days=1)
        start_date = now - timedelta(days=7)
        date_range = f"{start_date.strftime('%b %-d')} - {end_date.strftime('%b %-d')}"

        def format_time(decimal_hours):
            if decimal_hours == 0: return "0h"
            hrs = int(decimal_hours)
            mins = round((decimal_hours - hrs) * 60)
            if hrs > 0 and mins > 0: return f"{hrs}h {mins}m"
            if hrs > 0: return f"{hrs}h"
            return f"{mins}m"

        players_data = []
        total_group_hours = 0.0

        for player in included_players:
            safe_player = player.lower().replace(" ", "_")
            state = self.hass.states.get(f"sensor.{safe_player}_gaming_status")
            if state:
                hours = float(state.attributes.get("total_weekly_hours_last_week", 0))
                game = state.attributes.get("last_played_game") or "None"
                if len(game) > 25: game = game[:25] + "..."
                friendly_name = state.attributes.get("friendly_name", player).replace(" Gaming Status", "").strip()
                if hours > 0:
                    players_data.append({"name": friendly_name, "hours": hours, "game": game})
                    total_group_hours += hours

        if not players_data: return
        players_data.sort(key=lambda x: x["hours"], reverse=True)

        report_msg = ""
        for p in players_data:
            report_msg += f"• **{p['name']}**: {format_time(p['hours'])} *(Recent: {p['game']})*\n"

        mobile_msg = f"{len(players_data)} active players ({total_group_hours:.1f}h total).\nTap for details."
        all_endpoints = config.get("NOTIFICATION_ENDPOINTS", {})
        assigned_endpoints = report_config.get("destinations", [])

        for endpoint_id in assigned_endpoints:
            dest = all_endpoints.get(endpoint_id)
            if not dest: continue

            service_name = dest.get("service", "").replace("notify.", "")
            target = dest.get("target")
            notify_type = dest.get("type", "discord")

            if notify_type == "discord":
                service_data = {"message": "Weekly Gaming Report", "target": target, "data": {"embed": {"title": date_range, "description": report_msg, "color": 5025616}}}
            elif notify_type == "mobile":
                service_data = {"title": "Weekly Report Ready", "message": mobile_msg, "data": {"tag": "gaming_report", "color": "#4CAF50"}}
                click_url = report_config.get("click_url", "").strip()
                if click_url:
                    service_data["data"]["clickAction"] = click_url
                    service_data["data"]["url"] = click_url
            elif notify_type == "sms":
                sms_text = f"Weekly Gaming ({date_range}): {total_group_hours:.1f}h total. Top player: {players_data[0]['name']} ({format_time(players_data[0]['hours'])})."
                service_data = {"message": sms_text, "target": [target] if target else []}

            try: await self.hass.services.async_call("notify", service_name, service_data)
            except Exception as e: _LOGGER.error(f"[Gaming Notifier] Failed to send report: {e}")