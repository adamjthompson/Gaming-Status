"""Gaming Status notifier — session alerts, weekly report, parental controls."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval

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
    # Properties — always reads latest options so live reloads work
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

        self._unsub_listener = self.hass.bus.async_listen(
            "state_changed", self._handle_state_change
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
    # State change handler — session start/stop notifications
    # ------------------------------------------------------------------

    async def _handle_state_change(self, event) -> None:
        if self._startup_time and datetime.now() - self._startup_time < timedelta(seconds=30):
            return

        entity_id = event.data.get("entity_id", "")
        if not entity_id.endswith("_gaming_status"):
            return

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

        # Find which player this sensor belongs to
        players = self._players()
        target_player = None
        target_id = None
        for player_name, player_data in players.items():
            for platform in ("steam", "xbox", "playstation", "custom"):
                if player_data.get(platform) == entity_id:
                    # This is the master sensor entity, not the platform sensor
                    pass
            # Check master sensor naming convention
            safe = player_name.lower().replace(" ", "_")
            if entity_id == f"sensor.{safe}_gaming_status":
                target_player = player_name
                target_id = player_name
                break

        if target_player is None:
            return

        user_config = players.get(target_player, {})

        old_clean = old_game.lower().strip()
        new_clean = new_game.lower().strip()
        old_off = old_clean in (["offline"] + ignored) or old_clean in exclusions
        new_off = new_clean in (["offline"] + ignored) or new_clean in exclusions

        # Session started
        if old_off and not new_off:
            await self._send_notification(
                target_id,
                user_config,
                f"🎮 {target_player} started playing {new_game}",
                event_type="session_start",
            )
        # Game switched
        elif not old_off and not new_off and old_game != new_game:
            await self._send_notification(
                target_id,
                user_config,
                f"🔄 {target_player} switched to {new_game}",
                event_type="game_switch",
            )
        # Session ended
        elif not old_off and new_off:
            await self._send_notification(
                target_id,
                user_config,
                f"⏹ {target_player} stopped playing {old_game}",
                event_type="session_end",
            )

    async def _send_notification(
        self, player_id: str, user_config: dict, message: str, event_type: str
    ) -> None:
        endpoints = self._endpoints()
        assigned_ids = user_config.get("notifications", {}).get("assigned_endpoints", [])

        for ep_id in assigned_ids:
            dest = endpoints.get(ep_id)
            if not dest:
                continue
            service_str = dest.get("service", "")
            if not service_str or "." not in service_str:
                continue
            domain, service = service_str.split(".", 1)
            service_data = dict(dest.get("data", {}))
            service_data.setdefault("message", message)
            try:
                await self.hass.services.async_call(domain, service, service_data)
            except Exception as exc:
                _LOGGER.warning("Gaming Status: notification failed (%s): %s", ep_id, exc)

    # ------------------------------------------------------------------
    # Parental controls
    # ------------------------------------------------------------------

    async def _check_parental_controls(self, now) -> None:
        parental = self._parental()
        players = self._players()
        endpoints = self._endpoints()
        if not parental:
            return

        now_dt = datetime.now()
        is_weekend = now_dt.weekday() >= 5

        for player_name, rules in parental.items():
            safe_player = player_name.lower().replace(" ", "_")
            player_data = players.get(player_name, {})

            # ---- Screen time ----
            st_rule = rules.get("screen_time", {})
            if st_rule.get("enabled"):
                limit_minutes = (
                    st_rule.get("weekend_minutes", 180)
                    if is_weekend
                    else st_rule.get("weekday_minutes", 120)
                )
                st_key = f"{safe_player}_screen_time"
                # Read today's session total from the history sensor attribute
                history_entity = f"sensor.{safe_player}_gaming_history"
                hist_state = self.hass.states.get(history_entity)
                if hist_state:
                    today_minutes = float(hist_state.attributes.get("today_playtime_minutes", 0))
                    if today_minutes >= limit_minutes and st_key not in self._triggered_parental_events:
                        self._triggered_parental_events[st_key] = True
                        action = st_rule.get("action", "")
                        await self._fire_parental_action(player_name, action, endpoints, f"⏰ {player_name} has reached their {limit_minutes}-minute screen time limit.")
                elif st_key in self._triggered_parental_events:
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
                try:
                    c_hour, c_min = map(int, curfew_time.split(":"))
                    curfew_dt = now_dt.replace(hour=c_hour, minute=c_min, second=0, microsecond=0)
                    if now_dt >= curfew_dt and cf_key not in self._triggered_parental_events:
                        # Only fire if player is actively gaming
                        master_entity = f"sensor.{safe_player}_gaming_status"
                        master_state = self.hass.states.get(master_entity)
                        if master_state and master_state.state.lower() not in ("offline", "unavailable", "unknown"):
                            self._triggered_parental_events[cf_key] = True
                            pretty = datetime.strptime(curfew_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                            action = cf_rule.get("action", "")
                            await self._fire_parental_action(player_name, action, endpoints, f"🌙 {player_name}'s curfew time of {pretty} has been reached.")
                    elif now_dt < curfew_dt:
                        self._triggered_parental_events.pop(cf_key, None)
                except (ValueError, AttributeError):
                    pass

    async def _fire_parental_action(
        self, player_name: str, action_service: str, endpoints: dict, message: str
    ) -> None:
        _LOGGER.info("Gaming Status parental control triggered for %s: %s", player_name, message)
        if action_service and "." in action_service:
            domain, service = action_service.split(".", 1)
            try:
                await self.hass.services.async_call(domain, service, {"message": message})
            except Exception as exc:
                _LOGGER.warning("Gaming Status: parental action failed: %s", exc)

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
        endpoints = self._endpoints()
        assigned = report_config.get("destinations", [])

        lines = [f"📊 **Weekly Gaming Report** — {datetime.now().strftime('%B %d, %Y')}"]

        for player_name in players:
            safe = player_name.lower().replace(" ", "_")
            history_entity = f"sensor.{safe}_gaming_history"
            state = self.hass.states.get(history_entity)
            if not state:
                continue
            attrs = state.attributes
            weekly_hours = attrs.get("weekly_playtime_hours", 0)
            top_game = attrs.get("top_game_this_week", "Unknown")
            lines.append(f"\n🎮 **{player_name}**: {weekly_hours}h total — top game: {top_game}")

        message = "\n".join(lines)

        for ep_id in assigned:
            dest = endpoints.get(ep_id)
            if not dest or "." not in dest.get("service", ""):
                continue
            domain, service = dest["service"].split(".", 1)
            service_data = dict(dest.get("data", {}))
            service_data.setdefault("message", message)
            try:
                await self.hass.services.async_call(domain, service, service_data)
            except Exception as exc:
                _LOGGER.warning("Gaming Status: weekly report send failed (%s): %s", ep_id, exc)