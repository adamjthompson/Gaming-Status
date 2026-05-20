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
from homeassistant.util import dt as dt_util

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

        opts = self._entry.options

        # Parse JSON exactly once at startup and cache in memory
        self._cached_players = _load_json(opts.get(OPT_PLAYERS), {})
        self._cached_endpoints = _load_json(opts.get(OPT_ENDPOINTS), {})
        self._cached_weekly = _load_json(opts.get(OPT_WEEKLY_REPORT), {})
        self._cached_parental = _load_json(opts.get(OPT_PARENTAL), {})

        raw_exclusions = _load_json(opts.get(OPT_GLOBAL_EXCLUSIONS), [])
        self._cached_exclusions = [x.strip().lower() for x in raw_exclusions]

        # Instant O(1) lookup map (entity_id -> player_name)
        self._entity_player_map = {
            f"sensor.{p.lower().replace(' ', '_')}_gaming_status": p
            for p in self._cached_players
        }

    # ------------------------------------------------------------------
    # Optimized Getter Methods (Returns Cache Only)
    # ------------------------------------------------------------------

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
        duration_str: str = None,
        event_type: str = "info"
    ) -> bool:
        """Dispatch a notification to a configured endpoint.

        Returns True if the HA service call was made successfully, False in
        every other case (missing endpoint, bad config, service not found,
        call raised). Callers that gate state changes on delivery (e.g.
        parental controls) must check the return value before recording.
        """
        dest = self._cached_endpoints.get(ep_id)
        if not dest:
            _LOGGER.warning("Gaming Status: endpoint '%s' not found in config", ep_id)
            return False

        # The config flow saves the HA service string under the key "service".
        service_str = dest.get("service", "")
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
            service_data["message"] = message
            color = 65280 if event_type == "start" else (16711680 if event_type == "stop" else 3447003)
            embed = {"color": color}
            if game_title: embed["title"] = game_title
            if duration_str: embed["description"] = f"Duration: {duration_str}"
            if image_url: embed["image"] = {"url": image_url}
            service_data["data"] = {"embed": embed}
        else:
            final_message = message
            if duration_str: final_message += f"\nDuration: {duration_str}"
            service_data["message"] = final_message
            if image_url and ep_type != "SMS": service_data["data"] = {"image": image_url}

        try:
            await self.hass.services.async_call(domain, service, service_data)
            return True
        except Exception as exc:
            _LOGGER.warning("Gaming Status: notification failed for endpoint '%s': %s", ep_id, exc)
            return False

    # ------------------------------------------------------------------
    # Cover art resolution
    # ------------------------------------------------------------------

    async def _resolve_cover_art(
        self,
        player_name: str,
        user_config: dict,
        old_state,
        is_switch: bool,
    ) -> str | None:
        """Wait for cover art to be available on the active platform sensor.

        Strategy:
        - Read the platform sensors directly (the source of truth) rather than
          the master sensor, which only updates after a further state-change
          propagation step and was the cause of the original timing race.
        - If a /local/ URL is already present it is a user-defined custom cover
          and is returned immediately — no waiting needed.
        - For SteamGridDB URLs we wait up to 30 seconds (15 x 2s) for the
          async fetch to complete before giving up and sending without art.
        - For a game-switch event we skip any URL that matches the previous
          game's art so we don't accidentally reuse stale cover art.
        """
        safe = player_name.lower().replace(" ", "_")
        old_url = old_state.attributes.get("game_cover_art") if old_state else None

        # Build the ordered list of platform sensor entity IDs for this player.
        platform_entity_ids = [
            f"sensor.{safe}_{platform}"
            for platform in ("steam", "xbox", "playstation", "custom")
            if user_config.get(platform)
        ]

        def _read_cover() -> str | None:
            """Return the first valid cover URL found across platform sensors."""
            for pid in platform_entity_ids:
                pstate = self.hass.states.get(pid)
                if not pstate:
                    continue
                url = (
                    pstate.attributes.get("game_cover_art")
                    or pstate.attributes.get("cached_game_cover")
                )
                if not url:
                    continue
                # For a game switch, ignore the previous game's art which may
                # still be present on the sensor before it has been updated.
                if is_switch and url == old_url:
                    continue
                return url
            return None

        # Check immediately — cached art from a previous session may already
        # be present, or a /local/ custom cover is always ready to use now.
        url = _read_cover()
        if url:
            return url

        # Art not yet available — poll the platform sensors directly, waiting
        # up to 30 seconds for the SteamGridDB fetch to complete.
        _LOGGER.debug(
            "Gaming Status: cover art not yet ready for %s, waiting up to 30s",
            player_name,
        )
        for _ in range(15):
            await asyncio.sleep(2)
            url = _read_cover()
            if url:
                return url

        _LOGGER.debug(
            "Gaming Status: cover art did not arrive in time for %s, sending without image",
            player_name,
        )
        return None

    # ------------------------------------------------------------------
    # State change handler
    # ------------------------------------------------------------------

    async def _handle_state_change(self, event) -> None:
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
        is_switch = not old_off and not new_off and old_game != new_game
        is_end = not old_off and new_off

        if not (is_start or is_switch or is_end): return

        start_dests = user_config.get("notify_start_destinations", [])
        end_dests = user_config.get("notify_end_destinations", [])

        # --- Calculate duration for the previous session ---
        duration_str = None
        if is_switch or is_end:
            start_time_str = old_state.attributes.get("play_start_time")
            if start_time_str:
                try:
                    start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    now_dt = datetime.now(start_dt.tzinfo) if start_dt.tzinfo else datetime.now()
                    diff = now_dt - start_dt
                    total_minutes = int(diff.total_seconds() / 60)
                    if total_minutes > 0:
                        hours, minutes = total_minutes // 60, total_minutes % 60
                        duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                except Exception: pass

        if is_start or is_switch:
            # Build the message now — it doesn't depend on cover art.
            if is_switch and duration_str:
                msg = f"{target_player} switched to {new_game} after {duration_str}"
            elif is_switch:
                msg = f"{target_player} switched to {new_game}"
            else:
                msg = f"{target_player} started playing {new_game}"

            image_url = await self._resolve_cover_art(
                target_player, user_config, old_state, is_switch
            )

            for ep_id in start_dests:
                # Discord can only display publicly reachable URLs.
                # /local/ paths are LAN-only, so strip them for Discord endpoints
                # rather than sending an embed whose image silently fails to load.
                dest = self._cached_endpoints.get(ep_id, {})
                discord_safe_url = image_url if (image_url or "").startswith("https://") else None
                effective_url = discord_safe_url if dest.get("type") == "Discord" else image_url
                await self._send_to_endpoint(ep_id, message=msg, image_url=effective_url, game_title=new_game, event_type="start")

        elif is_end:
            image_url = old_state.attributes.get("game_cover_art") or old_state.attributes.get("cached_game_cover")
            for ep_id in end_dests:
                await self._send_to_endpoint(ep_id, message=f"{target_player} finished playing {old_game}", image_url=image_url, game_title=old_game, duration_str=duration_str, event_type="stop")

    # ------------------------------------------------------------------
    # Parental controls
    # ------------------------------------------------------------------

    async def _check_parental_controls(self, now) -> None:
        if not self._cached_parental: return

        # dt_util.now() is timezone-aware and respects HA's configured timezone,
        # avoiding DST issues that datetime.now() (naive) can produce.
        now_dt = dt_util.now()
        is_weekend = now_dt.weekday() >= 5

        for player_name, rules in self._cached_parental.items():
            safe_player = player_name.lower().replace(" ", "_")
            master_entity = f"sensor.{safe_player}_gaming_status"
            master_state = self.hass.states.get(master_entity)
            if not master_state: continue

            is_playing = master_state.state.lower() not in ("offline", "unavailable", "unknown")

            # --- SCREEN TIME LIMIT ---
            st_rule = rules.get("screen_time", {})
            if st_rule.get("enabled"):
                st_key = f"{safe_player}_screen_time"
                st_repeat = int(st_rule.get("repeat", 0))

                # Calculate entirely from _cached_parental (always current — updated
                # on every integration reload) and total_daily_hours (a recorded
                # attribute, always present on the live state object).
                #
                # We deliberately do NOT read remaining_play_time_minutes or
                # daily_play_limit_minutes from the sensor attributes because:
                # 1. Both are in _unrecorded_attributes so they are None after restart
                #    until the sensor receives a platform state-change event.
                # 2. The sensor only recalculates them when _update_master_state runs,
                #    which only happens on platform state changes — so if the limit is
                #    changed in config but the game state hasn't changed, the sensor
                #    still carries the old limit value until the next state change fires.
                is_weekend = now_dt.weekday() >= 5
                limit = int(
                    st_rule.get("weekend_minutes", 180)
                    if is_weekend
                    else st_rule.get("weekday_minutes", 120)
                )
                today_minutes = int(float(master_state.attributes.get("total_daily_hours", 0)) * 60)

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
                            # Only record as notified if delivery actually succeeded so
                            # that a transient failure retries next tick rather than
                            # permanently suppressing further notifications.
                            if await self._fire_parental_action(player_name, st_rule.get("action", ""), msg):
                                self._triggered_parental_events[st_key] = overage

                else:
                    # today_minutes is below the limit — player is back under their
                    # limit (e.g. after a daily reset). Clear the fired flag so the
                    # next breach triggers a fresh notification.
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
                            if await self._fire_parental_action(player_name, cf_rule.get("action", ""), msg):
                                self._triggered_parental_events[cf_key] = now_dt

                    elif now_dt < curfew_dt:
                        # Past midnight, new day — clear so curfew fires again tonight.
                        self._triggered_parental_events.pop(cf_key, None)

                except (ValueError, AttributeError): pass

    async def _fire_parental_action(self, player_name: str, action_data, message: str) -> bool:
        """Dispatch a parental control action to one or more targets.

        Returns True if every configured target was reached successfully.
        Returns False if action_data is empty, invalid, or any target fails.
        Callers must check the return value before recording the event as fired
        so that a transient delivery failure doesn't permanently suppress retries.
        """
        if not action_data or action_data == "none":
            return False

        targets = action_data if isinstance(action_data, list) else [action_data]
        any_succeeded = False

        for target in targets:
            if not isinstance(target, str): continue
            target = target.strip()
            if not target or target == "none": continue

            if target.startswith("endpoint_"):
                sent = await self._send_to_endpoint(target.replace("endpoint_", "", 1), message, event_type="info")
            elif target in self._cached_endpoints:
                sent = await self._send_to_endpoint(target, message, event_type="info")
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
        if now.weekday() != self._run_day or not self._cached_weekly.get("enabled"): return
        assigned = self._cached_weekly.get("destinations", [])
        lines = [f"**Weekly Gaming Report** — {dt_util.now().strftime('%B %d, %Y')}"]
        for player_name in self._cached_players:
            safe = player_name.lower().replace(" ", "_")
            state = self.hass.states.get(f"sensor.{safe}_gaming_status")
            if state:
                attrs = state.attributes
                lines.append(f"\n**{player_name}**: {attrs.get('total_weekly_hours_last_week', attrs.get('total_weekly_hours', 0))}h total — Last game: {attrs.get('last_played_game', 'Unknown')}")
        message = "\n".join(lines)
        for ep_id in assigned: await self._send_to_endpoint(ep_id, message, event_type="info")