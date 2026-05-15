import logging
import asyncio
import json
import os
from datetime import datetime, timedelta
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.event import async_track_time_change

_LOGGER = logging.getLogger(__name__)

class GamingNotifier:
    def __init__(self, hass):
        self.hass = hass
        self.config_path = hass.config.path("gaming_profiles.json")
        self._unsub_listener = None
        self._unsub_weekly = None

    async def async_start(self):
        """Start listening to all state changes and schedule reports."""
        self._unsub_listener = self.hass.bus.async_listen(
            "state_changed", self._handle_state_change
        )
        
        # Pull config to schedule the weekly report
        config = self._get_config()
        report_config = config.get("WEEKLY_REPORT", {})
        
        # Schedule based on user config, defaulting to Monday (0) at 09:00
        run_day = int(report_config.get("day", 0))
        run_time_str = report_config.get("time", "09:00")
        
        try:
            target_hour, target_minute = map(int, run_time_str.split(":"))
        except ValueError:
            target_hour, target_minute = 9, 0

        self._run_day = run_day

        self._unsub_weekly = async_track_time_change(
            self.hass, self._trigger_weekly_report, hour=target_hour, minute=target_minute, second=0
        )

    async def async_stop(self):
        """Stop listening when the integration is unloaded."""
        if self._unsub_listener:
            self._unsub_listener()
        if self._unsub_weekly:
            self._unsub_weekly()

    def _get_config(self):
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _LOGGER.error(f"[Gaming Notifier] Error reading config: {e}")
            return {}

    async def _handle_state_change(self, event):
        entity_id = event.data.get("entity_id")
        
        if not entity_id.endswith("_gaming_status"):
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if not old_state or not new_state:
            return

        # Crush any double-spaces, tabs, or weird unicode spaces into a single normal space
        old_game = " ".join(str(old_state.state).split())
        new_game = " ".join(str(new_state.state).split())
        
        config = self._get_config()
        
        # Grab global exclusions and make them lowercase for bulletproof comparison
        exclusions = [x.strip().lower() for x in config.get("GLOBAL_EXCLUSIONS", [])]
        
        ignored = [STATE_UNAVAILABLE, STATE_UNKNOWN, "offline"]
        
        old_is_game = old_game.lower() not in ignored and old_game.lower() not in exclusions
        new_is_game = new_game.lower() not in ignored and new_game.lower() not in exclusions

        safe_owner = entity_id.split('.')[1].replace("_gaming_status", "")

        # 1. GAME ENDED
        if old_is_game and old_game != new_game:
            await self._process_game_ended(safe_owner, old_game, old_state.attributes)

        # 2. GAME STARTED
        if new_is_game and old_game != new_game:
            self.hass.async_create_task(self._process_game_started(entity_id, safe_owner, new_game))

    async def _process_game_ended(self, safe_owner, game_name, attributes):
        config = self._get_config()
        profiles = config.get("GAMER_PROFILES", {})
        
        user_config = next((data for name, data in profiles.items() if name.lower().replace(" ", "_") == safe_owner), None)
        if not user_config or not user_config.get("notifications", {}).get("notify_end"):
            return

        duration_str = ""
        duration_sec = attributes.get("last_session_play_time", 0)
        if duration_sec > 0:
            hrs, remainder = divmod(duration_sec, 3600)
            mins, _ = divmod(remainder, 60)
            duration_str = f"{int(hrs)}h {int(mins)}m" if hrs > 0 else f"{int(mins)}m"

        friendly_name = attributes.get("friendly_name", "Someone").replace(" Gaming Status", "").strip()
        image_url = attributes.get("game_cover_art") or attributes.get("entity_picture") or ""
        if image_url.startswith("/"):
            image_url = f"{self.hass.config.api.base_url}{image_url}"

        await self._dispatch_individual_alert(config, user_config, "end", friendly_name, game_name, duration_str, image_url)

    async def _process_game_started(self, entity_id, safe_owner, game_name):
        await asyncio.sleep(15)  

        current_state = self.hass.states.get(entity_id)
        if not current_state:
            return
            
        # Reclean the string after the sleep delay to ensure it matches
        current_game_clean = " ".join(str(current_state.state).split())

        if current_game_clean != game_name:
            return

        config = self._get_config()
        profiles = config.get("GAMER_PROFILES", {})
        user_config = next((data for name, data in profiles.items() if name.lower().replace(" ", "_") == safe_owner), None)
        
        if not user_config or not user_config.get("notifications", {}).get("notify_start"):
            return

        attributes = current_state.attributes
        friendly_name = attributes.get("friendly_name", "Someone").replace(" Gaming Status", "").strip()
        image_url = attributes.get("game_cover_art") or attributes.get("entity_picture") or ""
        if image_url.startswith("/"):
            image_url = f"{self.hass.config.api.base_url}{image_url}"

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
                service_data = {
                    "message": message,
                    "target": target,
                    "data": {
                        "embed": {
                            "title": game,
                            "color": 5763719 if event_type == "start" else 15158332,
                        }
                    }
                }
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

            try:
                await self.hass.services.async_call("notify", service_name, service_data)
            except Exception as e:
                _LOGGER.error(f"[Gaming Notifier] Failed to send {notify_type} to {service_name}: {e}")

    # --- WEEKLY REPORT ENGINE ---
    
    async def _trigger_weekly_report(self, now):
        """Fires at the configured time. Checks if today is the correct day."""
        if now.weekday() != getattr(self, '_run_day', 0): 
            return # Failsafe: Only run on the configured day
        
        config = self._get_config()
        report_config = config.get("WEEKLY_REPORT", {})
        
        if not report_config.get("enabled", False):
            return
            
        included_players = report_config.get("included_players", [])
        if not included_players:
            return

        # Date Math: Last 7 days
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
            sensor_id = f"sensor.{safe_player}_gaming_status"
            state = self.hass.states.get(sensor_id)
            
            if state:
                attrs = state.attributes
                hours = float(attrs.get("total_weekly_hours_last_week", 0))
                game = attrs.get("last_played_game") or "None"
                if len(game) > 25: game = game[:25] + "..."
                
                friendly_name = attrs.get("friendly_name", player).replace(" Gaming Status", "").strip()
                
                if hours > 0:
                    players_data.append({"name": friendly_name, "hours": hours, "game": game})
                    total_group_hours += hours

        if not players_data:
            return

        players_data.sort(key=lambda x: x["hours"], reverse=True)

        # Build clean list format (Discord does not support Markdown tables)
        report_msg = ""
        for p in players_data:
            time_str = format_time(p["hours"])
            report_msg += f"• **{p['name']}**: {time_str} *(Recent: {p['game']})*\n"

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
                service_data = {
                    "message": "Weekly Gaming Report",
                    "target": target,
                    "data": {"embed": {"title": date_range, "description": report_msg, "color": 5025616}}
                }
            elif notify_type == "mobile":
                service_data = {
                    "title": "Weekly Report Ready",
                    "message": mobile_msg,
                    "data": {
                        "tag": "gaming_report",
                        "color": "#4CAF50"
                    }
                }
                # Dynamically append click action if configured
                click_url = report_config.get("click_url", "").strip()
                if click_url:
                    service_data["data"]["clickAction"] = click_url
                    service_data["data"]["url"] = click_url

            elif notify_type == "sms":
                sms_text = f"Weekly Gaming ({date_range}): {total_group_hours:.1f}h total. Top player: {players_data[0]['name']} ({format_time(players_data[0]['hours'])})."
                service_data = {"message": sms_text, "target": [target] if target else []}

            try:
                await self.hass.services.async_call("notify", service_name, service_data)
            except Exception as e:
                _LOGGER.error(f"[Gaming Notifier] Failed to send report to {service_name}: {e}")