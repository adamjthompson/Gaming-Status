"""Gaming Status notifier — session alerts, weekly report, parental controls."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    OPT_DISCORD_COLORS,
    OPT_ENDPOINTS,
    OPT_GLOBAL_EXCLUSIONS,
    OPT_NOTIFY_ARTWORK,
    OPT_PARENTAL,
    OPT_PLAYERS,
    OPT_WEEKLY_REPORT,
)
from .device import (
    resolve_master_entity_id,
    resolve_pc_entity_id,
    resolve_platform_entity_id,
    safe_owner_slug,
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
            resolve_master_entity_id(self.hass, p): p for p in initial_players
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

    def _memoized_json_option(self, cache_attr: str, option_key: str, fallback):
        """Parse an options-stored JSON blob once per distinct raw value,
        instead of on every property access -- an options change already
        forces a full integration reload, so a cached value can never go
        stale between reloads."""
        raw = self._entry.options.get(option_key)
        cached = getattr(self, cache_attr, None)
        if cached is not None and cached[0] == raw:
            return cached[1]
        value = _load_json(raw, fallback)
        setattr(self, cache_attr, (raw, value))
        return value

    @property
    def _cached_players(self) -> dict:
        return self._memoized_json_option("_players_cache", OPT_PLAYERS, {})

    @property
    def _cached_endpoints(self) -> dict:
        return self._memoized_json_option("_endpoints_cache", OPT_ENDPOINTS, {})

    @property
    def _cached_weekly(self) -> dict:
        return self._memoized_json_option("_weekly_cache", OPT_WEEKLY_REPORT, {})

    @property
    def _cached_parental(self) -> dict:
        return self._memoized_json_option("_parental_cache", OPT_PARENTAL, {})

    @property
    def _cached_discord_colors(self) -> dict:
        return self._memoized_json_option(
            "_discord_colors_cache", OPT_DISCORD_COLORS, {}
        )

    @property
    def _cached_notify_artwork(self) -> str:
        return self._entry.options.get(OPT_NOTIFY_ARTWORK, "game_cover_art")

    @property
    def _cached_exclusions(self) -> list:
        raw = self._entry.options.get(OPT_GLOBAL_EXCLUSIONS)
        cached = getattr(self, "_exclusions_cache", None)
        if cached is not None and cached[0] == raw:
            return cached[1]
        value = [x.strip().lower() for x in _load_json(raw, [])]
        self._exclusions_cache = (raw, value)
        return value

    # Keep legacy getters functioning to support older function calls
    def _players(self) -> dict:
        return self._cached_players

    def _endpoints(self) -> dict:
        return self._cached_endpoints

    def _weekly_report(self) -> dict:
        return self._cached_weekly

    def _parental(self) -> dict:
        return self._cached_parental

    def _global_exclusions(self) -> list:
        return self._cached_exclusions

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
            # [:2] tolerates both "HH:MM" (legacy free-text entries) and
            # "HH:MM:SS" (Home Assistant's TimeSelector widget).
            target_hour, target_minute = (int(p) for p in run_time_str.split(":")[:2])
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
        game_color_override: str = None,
    ) -> int:
        """Return the Discord embed color integer for this endpoint and event type."""
        DEFAULT_START = 65280  # green
        DEFAULT_STOP = 16711680  # red
        DEFAULT_INFO = 3447003  # blue
        DEFAULT_WEEKLY = 15844367  # gold

        PLATFORM_COLORS = {
            "steam": 175599,  # rgb(2, 173, 239)
            "xbox": 752656,  # rgb(11, 124, 16)
            "playstation": 12423,  # rgb(0, 48, 135)
            "custom": 6566500,  # rgb(100, 50, 100)
        }

        default_for_type = (
            DEFAULT_START
            if event_type == "start"
            else DEFAULT_STOP
            if event_type == "stop"
            else DEFAULT_WEEKLY
            if event_type == "weekly"
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

        if mode == "game":
            # For event types with no single natural state_obj (e.g. the
            # weekly report, which aggregates multiple players) callers can
            # pass an already-resolved color directly instead.
            hex_color = game_color_override or (
                state_obj.attributes.get("game_dominant_color", "") if state_obj else ""
            )
            if hex_color:
                return self._hex_to_int(hex_color, default_for_type)

        if mode == "custom":
            if event_type == "start":
                return self._hex_to_int(
                    colors_config.get("color_start", ""), DEFAULT_START
                )
            if event_type == "stop":
                return self._hex_to_int(
                    colors_config.get("color_end", ""), DEFAULT_STOP
                )
            if event_type == "weekly":
                return self._hex_to_int(
                    colors_config.get("color_weekly", ""), DEFAULT_WEEKLY
                )
            return self._hex_to_int(
                colors_config.get("color_parental", ""), DEFAULT_INFO
            )

        return default_for_type

    def _format_duration(self, minutes: int) -> str:
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        hours = minutes // 60
        mins = minutes % 60
        hour_str = "1 hour" if hours == 1 else f"{hours} hours"
        if mins == 0:
            return hour_str
        return f"{hour_str} and {mins} minute{'s' if mins != 1 else ''}"

    async def _send_to_endpoint(
        self,
        ep_id: str,
        message: str,
        image_url: str = None,
        game_title: str = None,
        event_type: str = "info",
        state_obj=None,
        game_color: str = None,
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
                _LOGGER.warning(
                    "Gaming Status: endpoint '%s' has no valid service configured",
                    ep_id,
                )
                return False

        domain, service = service_str.split(".", 1)

        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning(
                "Gaming Status: notification skipped, service %s.%s not found",
                domain,
                service,
            )
            return False

        target_id = dest.get("target_id", "").strip()
        ep_type = dest.get("type", "Mobile App")

        service_data = {}

        if target_id and target_id.lower() != "n/a":
            if ep_type == "Discord":
                service_data["target"] = [target_id]
            else:
                service_data["target"] = target_id

        if ep_type == "Discord":
            # Discord API strictly rejects relative local paths. If domain appending failed, strip the image to save the notification!
            if image_url and not image_url.startswith("http"):
                image_url = None

            color = self._resolve_discord_color(
                event_type, state_obj, game_color_override=game_color
            )
            embed = {"color": color}

            # Placing text in "description" puts it INSIDE the colored bar
            if event_type == "info":
                embed["title"] = "Gaming Status"
                embed["description"] = message
            else:
                if game_title:
                    embed["title"] = game_title
                embed["description"] = message

            if image_url:
                embed["image"] = {"url": image_url}

            service_data["message"] = ""
            service_data["data"] = {"embed": embed}

        else:  # Standard Mobile App / SMS
            service_data["message"] = message

            if event_type == "start":
                service_data["title"] = game_title or "Gaming Status"
            elif event_type == "stop":
                service_data["title"] = (
                    f"Finished {game_title}" if game_title else "Gaming Session Ended"
                )
            elif event_type == "parental":
                service_data["title"] = game_title or "Parental Controls"
            elif event_type == "weekly":
                service_data["title"] = game_title or "Weekly Gaming Report"
            else:
                service_data["title"] = "Gaming Status"

            if image_url and ep_type != "SMS":
                service_data["data"] = {"image": image_url}

        try:
            await self.hass.services.async_call(domain, service, service_data)
            return True
        except Exception as exc:
            _LOGGER.warning(
                "Gaming Status: notification failed for endpoint '%s': %s", ep_id, exc
            )
            return False

    # ------------------------------------------------------------------
    # Cover art resolution
    # ------------------------------------------------------------------

    async def _make_external_url(
        self, image_url: str | None, game_name: str
    ) -> str | None:
        """Convert a local HA path into a public URL, or fallback to the remote SteamGridDB cache."""
        if not image_url or not image_url.startswith("/"):
            return image_url

        try:
            import ipaddress
            import socket
            from urllib.parse import urlparse

            base_url = get_url(self.hass, prefer_external=True)
            host = urlparse(base_url).hostname or ""

            try:
                resolved_ip = await self.hass.async_add_executor_job(
                    socket.gethostbyname, host
                )
                is_local = ipaddress.ip_address(resolved_ip).is_private
            except Exception:
                is_local = host.endswith((".local", ".lan", ".internal"))

            if not base_url.startswith("https://") or is_local:
                raise ValueError("No external domain available")

            return f"{base_url.rstrip('/')}{image_url}"

        except Exception:
            try:
                from .utils import get_cached_remote_url

                target_type = (
                    "hero"
                    if "hero" in self._cached_notify_artwork
                    else "logo"
                    if "logo" in self._cached_notify_artwork
                    else "grid"
                )
                remote_url = get_cached_remote_url(game_name, target_type)
                return remote_url or image_url
            except Exception:
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

        old_url = (
            old_state.attributes.get(self._cached_notify_artwork) if old_state else None
        )

        # Check Master, Sub-Master, and all active platforms (including Discord) for artwork
        platform_entity_ids = [
            resolve_master_entity_id(self.hass, player_name),
            resolve_pc_entity_id(self.hass, player_name),
        ]
        for platform in ("steam", "xbox", "playstation", "custom", "discord"):
            source_entity_id = user_config.get(platform)
            if source_entity_id:
                platform_entity_ids.append(
                    resolve_platform_entity_id(
                        self.hass, player_name, platform, source_entity_id
                    )
                )

        def _read_cover() -> str | None:
            """Return the preferred artwork URL found across platform sensors."""
            for pid in platform_entity_ids:
                pstate = self.hass.states.get(pid)
                if not pstate:
                    continue

                if str(pstate.state).lower() in (
                    "offline",
                    "unavailable",
                    "unknown",
                    "idle",
                ):
                    continue

                # Grab the preferred art style
                url = pstate.attributes.get(self._cached_notify_artwork)

                # Resilient fallback if the preferred art is missing for this specific game
                if not url:
                    url = pstate.attributes.get(
                        "game_cover_art"
                    ) or pstate.attributes.get("cached_game_cover")

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

    async def _wait_for_enriched_state(self, entity_id, expected_game):
        """Poll up to 15s (every 3s) for artwork/color to populate after a
        transition, so a notification doesn't fire off the early,
        pre-enrichment state write a sensor publishes immediately on
        detecting a new game (before the slower artwork/color pipeline
        runs). Returns the refreshed state once real artwork (and color,
        if Discord's color mode is "game") has appeared, or None if the
        game closed/went offline/excluded/changed to a different game
        during the wait, in which case the caller should abort the
        notification."""
        refreshed_state = None
        expected_clean = expected_game.lower().strip()
        for _ in range(5):
            await asyncio.sleep(3)
            temp_state = self.hass.states.get(entity_id)
            if temp_state and temp_state.state.lower() not in (
                ["offline", "unknown", "unavailable"] + self._cached_exclusions
            ):
                if (
                    " ".join(str(temp_state.state).split()).lower().strip()
                    != expected_clean
                ):
                    return None  # Player switched to a different game during the wait, abort notification

                refreshed_state = temp_state

                # If the API successfully populated custom art (not just the fallback Akamai link), stop waiting!
                art_check = refreshed_state.attributes.get(
                    "game_hero_art"
                ) or refreshed_state.attributes.get("game_cover_art")
                color_check = refreshed_state.attributes.get("game_dominant_color")

                from .utils import url_host_matches

                if art_check and not url_host_matches(art_check, "akamaihd.net"):
                    # If Discord is set to Game Color, wait an extra tick for the extraction algorithm to finish!
                    if (
                        self._cached_discord_colors.get("mode") == "game"
                        and not color_check
                    ):
                        continue
                    break
            else:
                return None  # The game was closed instantly, abort notification
        return refreshed_state

    async def _handle_state_change(self, event) -> None:
        if not self._enable_notifications:
            return

        if self._startup_time and dt_util.now() - self._startup_time < timedelta(
            seconds=30
        ):
            return

        entity_id = event.data.get("entity_id", "")
        target_player = self._entity_player_map.get(entity_id)
        if not target_player:
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

        ignored = [STATE_UNAVAILABLE, STATE_UNKNOWN, "offline"]
        user_config = self._cached_players.get(target_player, {})

        old_clean = old_game.lower().strip()
        new_clean = new_game.lower().strip()
        old_off = (
            old_clean in (["offline"] + ignored) or old_clean in self._cached_exclusions
        )
        new_off = (
            new_clean in (["offline"] + ignored) or new_clean in self._cached_exclusions
        )

        is_start = old_off and not new_off
        # Use old_clean != new_clean to prevent case-sensitivity triggers
        is_switch = not old_off and not new_off and old_clean != new_clean
        is_end = not old_off and new_off

        now = dt_util.now()
        if not hasattr(self, "_last_start_time"):
            self._last_start_time = {}

        if is_start:
            last_start = self._last_start_time.get(target_player)
            # COOLDOWN: If a session just started less than 90 seconds ago, block the duplicate bounce!
            if last_start and (now - last_start).total_seconds() < 90:
                return

            self._last_start_time[target_player] = now

            # SMART POLLING DELAY: Give the API up to 15 seconds to fetch artwork, checking every 3 seconds
            refreshed_state = await self._wait_for_enriched_state(entity_id, new_game)
            if refreshed_state is None:
                return  # The game was closed instantly, abort notification
            new_state = refreshed_state
            new_game = " ".join(str(new_state.state).split())

        elif is_switch:
            last_start = self._last_start_time.get(target_player)
            # If a game launcher transitioned to the real game within 90 seconds, suppress the switch alert
            if last_start and (now - last_start).total_seconds() < 90:
                return

            # Same wait as is_start: the new game's own state write happens
            # immediately on detection, before artwork/color extraction runs,
            # so acting on it right away would always fall back to the
            # default embed color instead of the real game's color.
            refreshed_state = await self._wait_for_enriched_state(entity_id, new_game)
            if refreshed_state is None:
                return
            new_state = refreshed_state
            new_game = " ".join(str(new_state.state).split())

        if not (is_start or is_switch or is_end):
            return

        start_dests = user_config.get("notify_start_destinations", [])
        end_dests = user_config.get("notify_end_destinations", [])

        duration_str = None
        if is_switch or is_end:
            start_time_str = old_state.attributes.get("play_start_time")
            if start_time_str:
                try:
                    start_dt = datetime.fromisoformat(
                        start_time_str.replace("Z", "+00:00")
                    )

                    # TRUE END TIME CALCULATION: Default to now, but override if a session cleanly ended
                    end_dt = (
                        datetime.now(start_dt.tzinfo)
                        if start_dt.tzinfo
                        else datetime.now()
                    )

                    if is_end:
                        last_online_str = old_state.attributes.get(
                            "last_online_valid_timestamp"
                        )
                        if last_online_str:
                            temp_end = datetime.fromisoformat(
                                last_online_str.replace("Z", "+00:00")
                            )
                            if not temp_end.tzinfo:
                                temp_end = temp_end.replace(tzinfo=start_dt.tzinfo)
                            # Only apply if it doesn't result in a negative time glitch
                            if temp_end > start_dt:
                                end_dt = temp_end

                    diff = end_dt - start_dt
                    total_minutes = int(diff.total_seconds() / 60)
                    if total_minutes > 0:
                        hours, minutes = total_minutes // 60, total_minutes % 60
                        duration_str = (
                            f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                        )
                except Exception:
                    pass

        if is_start or is_switch:
            if is_switch:
                msg = (
                    f"{target_player} switched games after {duration_str}"
                    if duration_str
                    else f"{target_player} switched games"
                )
                display_title = f"{old_game} > {new_game}"
            else:
                msg = f"{target_player} started playing"
                display_title = new_game

            raw_url = await self._resolve_cover_art(
                target_player, user_config, old_state, is_switch
            )
            image_url = await self._make_external_url(raw_url, new_game)

            await asyncio.gather(
                *(
                    self._send_to_endpoint(
                        ep_id,
                        message=msg,
                        image_url=image_url,
                        game_title=display_title,
                        event_type="start",
                        state_obj=new_state,
                    )
                    for ep_id in start_dests
                )
            )

        elif is_end:
            msg = (
                f"{target_player} played for {duration_str}"
                if duration_str
                else f"{target_player} finished playing"
            )

            if self._cached_notify_artwork == "none":
                raw_url = None
            else:
                raw_url = old_state.attributes.get(self._cached_notify_artwork)
                if not raw_url:
                    raw_url = old_state.attributes.get(
                        "game_cover_art"
                    ) or old_state.attributes.get("cached_game_cover")

            image_url = await self._make_external_url(raw_url, old_game)

            # Pass the OLD state so the color is fully preserved!
            await asyncio.gather(
                *(
                    self._send_to_endpoint(
                        ep_id,
                        message=msg,
                        image_url=image_url,
                        game_title=old_game,
                        event_type="stop",
                        state_obj=old_state,
                    )
                    for ep_id in end_dests
                )
            )

    # ------------------------------------------------------------------
    # Parental controls
    # ------------------------------------------------------------------

    async def _check_parental_controls(self, now) -> None:
        if not self._enable_parental or not self._cached_parental:
            return

        now_dt = dt_util.now()
        is_weekend = now_dt.weekday() >= 5

        for player_name, rules in self._cached_parental.items():
            master_entity = resolve_master_entity_id(self.hass, player_name)
            master_state = self.hass.states.get(master_entity)
            if not master_state:
                continue

            safe_player = safe_owner_slug(player_name)

            user_config = self._cached_players.get(player_name, {})
            fallback_dests = list(
                set(
                    user_config.get("notify_start_destinations", [])
                    + user_config.get("notify_end_destinations", [])
                )
            )

            is_playing = master_state.state.lower() not in (
                "offline",
                "unavailable",
                "unknown",
            )

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
                    if raw_hours is None:
                        raw_hours = 0
                    today_minutes = int(float(raw_hours) * 60)
                except (ValueError, TypeError):
                    continue

                if today_minutes >= limit:
                    if is_playing:
                        overage = max(0, today_minutes - limit)
                        last_notified_overage = self._triggered_parental_events.get(
                            st_key
                        )

                        should_notify = False
                        if last_notified_overage is None or (
                            st_repeat > 0
                            and (overage - last_notified_overage) >= st_repeat
                        ):
                            should_notify = True

                        if should_notify:
                            if overage > 0:
                                msg = f"❗️ {player_name} has exceeded the {limit}-minute screen time limit by {overage} minutes ({today_minutes} minutes total)."
                            else:
                                msg = f"❗️ {player_name} has reached the {limit}-minute screen time limit."

                            current_game = master_state.state if is_playing else None
                            parental_image = None
                            if current_game and self._cached_notify_artwork != "none":
                                raw_url = master_state.attributes.get(
                                    self._cached_notify_artwork
                                )
                                if not raw_url:
                                    raw_url = master_state.attributes.get(
                                        "game_cover_art"
                                    ) or master_state.attributes.get(
                                        "cached_game_cover"
                                    )
                                parental_image = await self._make_external_url(
                                    raw_url, current_game
                                )

                            action = st_rule.get("action", "none")
                            if not action or action == "none":
                                action = fallback_dests

                            if await self._fire_parental_action(
                                player_name,
                                action,
                                msg,
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
                curfew_time = (
                    cf_rule.get("weekend", "23:00")
                    if is_weekend
                    else cf_rule.get("weekday", "22:00")
                )
                cf_repeat = int(cf_rule.get("repeat", 0))
                try:
                    # [:2] tolerates both "HH:MM" (legacy free-text entries)
                    # and "HH:MM:SS" (Home Assistant's TimeSelector widget).
                    c_hour, c_min = (int(p) for p in curfew_time.split(":")[:2])
                    curfew_dt = now_dt.replace(
                        hour=c_hour, minute=c_min, second=0, microsecond=0
                    )

                    if now_dt >= curfew_dt:
                        last_fired = self._triggered_parental_events.get(cf_key)

                        if is_playing and (
                            last_fired is None
                            or (
                                cf_repeat > 0
                                and (now_dt - last_fired).total_seconds()
                                >= (cf_repeat * 60)
                            )
                        ):
                            overage_minutes = int(
                                (now_dt - curfew_dt).total_seconds() / 60
                            )
                            # Reuse the already-parsed curfew_dt rather than
                            # re-parsing curfew_time with a strict format
                            # that "HH:MM:SS" input wouldn't match.
                            pretty_time = curfew_dt.strftime("%I:%M %p").lstrip("0")
                            if overage_minutes > 1:
                                msg = f"❗️ {player_name} has exceeded the {pretty_time} curfew by {overage_minutes} minutes."
                            else:
                                msg = f"❗️ {player_name} has reached the {pretty_time} curfew."

                            current_game = master_state.state if is_playing else None
                            parental_image = None
                            if current_game and self._cached_notify_artwork != "none":
                                raw_url = master_state.attributes.get(
                                    self._cached_notify_artwork
                                )
                                if not raw_url:
                                    raw_url = master_state.attributes.get(
                                        "game_cover_art"
                                    ) or master_state.attributes.get(
                                        "cached_game_cover"
                                    )
                                parental_image = await self._make_external_url(
                                    raw_url, current_game
                                )

                            action = cf_rule.get("action", "none")
                            if not action or action == "none":
                                action = fallback_dests

                            if await self._fire_parental_action(
                                player_name,
                                action,
                                msg,
                                game_title=current_game,
                                image_url=parental_image,
                                state_obj=master_state,
                            ):
                                self._triggered_parental_events[cf_key] = now_dt

                    elif now_dt < curfew_dt:
                        self._triggered_parental_events.pop(cf_key, None)

                except (ValueError, AttributeError):
                    pass

            # --- CONTENT RATING ---
            # Deduped per-game (not time-based like screen_time/curfew) so that
            # switching directly from one over-the-limit game to another always
            # notifies - a repeat cooldown could otherwise mask a newly started
            # violating game if a prior one already fired today.
            rt_rule = rules.get("ratings", {})
            if rt_rule.get("enabled"):
                rt_key = f"{safe_player}_rating"
                rating_exceeded = master_state.attributes.get("rating_exceeded", False)

                if is_playing and rating_exceeded:
                    current_game = master_state.state
                    last_notified_game = self._triggered_parental_events.get(rt_key)

                    if last_notified_game != current_game:
                        age_floor = (
                            master_state.attributes.get("game_content_rating") or {}
                        ).get("age_floor")
                        msg = f"❗️ {player_name} is playing {current_game}, rated for ages {age_floor}+."

                        parental_image = None
                        if self._cached_notify_artwork != "none":
                            raw_url = master_state.attributes.get(
                                self._cached_notify_artwork
                            )
                            if not raw_url:
                                raw_url = master_state.attributes.get(
                                    "game_cover_art"
                                ) or master_state.attributes.get("cached_game_cover")
                            parental_image = await self._make_external_url(
                                raw_url, current_game
                            )

                        action = rt_rule.get("action", "none")
                        if not action or action == "none":
                            action = fallback_dests

                        if await self._fire_parental_action(
                            player_name,
                            action,
                            msg,
                            game_title=current_game,
                            image_url=parental_image,
                            state_obj=master_state,
                        ):
                            self._triggered_parental_events[rt_key] = current_game
                else:
                    self._triggered_parental_events.pop(rt_key, None)

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
            if not isinstance(target, str):
                continue
            target = target.strip()
            if not target or target == "none":
                continue

            if target.startswith("endpoint_"):
                sent = await self._send_to_endpoint(
                    target.replace("endpoint_", "", 1),
                    message,
                    image_url=image_url,
                    game_title=game_title,
                    event_type="parental",
                    state_obj=state_obj,
                )
            elif target in self._cached_endpoints:
                sent = await self._send_to_endpoint(
                    target,
                    message,
                    image_url=image_url,
                    game_title=game_title,
                    event_type="parental",
                    state_obj=state_obj,
                )
            elif "." in target:
                domain, service = target.split(".", 1)
                if self.hass.services.has_service(domain, service):
                    try:
                        await self.hass.services.async_call(
                            domain, service, {"message": message}
                        )
                        sent = True
                    except Exception as exc:
                        _LOGGER.warning(
                            "Gaming Status: parental action failed: %s", exc
                        )
                        sent = False
                else:
                    _LOGGER.warning(
                        "Gaming Status: parental action skipped, service %s.%s not found",
                        domain,
                        service,
                    )
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
        if (
            not self._enable_notifications
            or now.weekday() != self._run_day
            or not self._cached_weekly.get("enabled")
        ):
            return
        assigned = self._cached_weekly.get("destinations", [])

        if self._cached_weekly.get("style") == "rich":
            await self._trigger_weekly_report_rich(assigned)
            return

        lines = [f"**Weekly Gaming Report** — {dt_util.now().strftime('%B %d, %Y')}"]
        for player_name in self._cached_players:
            state = self.hass.states.get(
                resolve_master_entity_id(self.hass, player_name)
            )
            if state:
                attrs = state.attributes
                lines.append(
                    f"\n**{player_name}**: {attrs.get('total_weekly_hours_last_week', attrs.get('total_weekly_hours', 0))}h total — Last game: {attrs.get('last_played_game', 'Unknown')}"
                )
        message = "\n".join(lines)
        for ep_id in assigned:
            await self._send_to_endpoint(ep_id, message, event_type="info")

    async def _trigger_weekly_report_rich(self, assigned) -> None:
        """Leaderboard-style weekly report: players ranked by last week's
        hours, each shown with their top game -- a real Discord embed (with
        artwork) for Discord destinations, and a sorted plain-text version
        for everything else. Opt-in via style: rich (see
        async_step_weekly_report) -- the default 'simple' style above is
        completely untouched by this method."""
        from .utils import _format_time, fetch_game_assets

        title = "🏆 Weekly Gaming Report"
        selected_players = self._cached_weekly.get("players", [])
        include_zero_hours = self._cached_weekly.get("include_zero_hours", False)
        include_images = self._cached_weekly.get("include_images", True)
        show_top_game = self._cached_weekly.get("show_top_game", True)
        show_rank_numbers = self._cached_weekly.get("show_rank_numbers", True)
        show_total_summary = self._cached_weekly.get("show_total_summary", True)

        players_stats = []
        for player_name in self._cached_players:
            if selected_players and player_name not in selected_players:
                continue
            state = self.hass.states.get(
                resolve_master_entity_id(self.hass, player_name)
            )
            if not state:
                continue
            attrs = state.attributes
            hours = float(
                attrs.get(
                    "total_weekly_hours_last_week", attrs.get("total_weekly_hours", 0)
                )
                or 0
            )
            if hours <= 0 and not include_zero_hours:
                continue
            # raw_rolling_breakdown is the trailing 7-day window, already
            # sorted descending by hours -- its first key is a close (not
            # exact) proxy for "top game last week," the same known
            # rolling-vs-calendar-week tradeoff already accepted elsewhere
            # (see the Library card's "Recently Played" sort).
            rolling = attrs.get("raw_rolling_breakdown") or {}
            top_game = (
                next(iter(rolling), None) or attrs.get("last_played_game") or "Unknown"
            )
            players_stats.append(
                {"name": player_name, "hours": hours, "top_game": top_game}
            )

        players_stats.sort(key=lambda p: p["hours"], reverse=True)

        if not players_stats:
            message = "No gaming activity this week."
            for ep_id in assigned:
                await self._send_to_endpoint(
                    ep_id, message, game_title=title, event_type="weekly"
                )
            return

        # Shared per-player line builder so the Discord and plain-text
        # formats stay consistent by construction -- same fields shown or
        # hidden in both, only the markdown/punctuation differs.
        def _player_line(i, p, bold_name, dash):
            name_part = f"{i + 1}. {p['name']}" if show_rank_numbers else p["name"]
            if bold_name:
                name_part = f"**{name_part}**"
            parts = [name_part, f" {dash} {_format_time(p['hours'] * 3600)}"]
            if show_top_game:
                parts.append(f" {dash} {p['top_game']}")
            return "".join(parts)

        discord_message = "\n".join(
            _player_line(i, p, bold_name=True, dash="—")
            for i, p in enumerate(players_stats)
        )
        plain_message = "\n".join(
            _player_line(i, p, bold_name=False, dash="-")
            for i, p in enumerate(players_stats)
        )

        if show_total_summary:
            total_hours = sum(p["hours"] for p in players_stats)
            player_word = "player" if len(players_stats) == 1 else "players"
            total_line = (
                f"Total: {_format_time(total_hours * 3600)} across "
                f"{len(players_stats)} {player_word}"
            )
            discord_message += f"\n\n{total_line}"
            plain_message += f"\n{total_line}"

        top_game = players_stats[0]["top_game"]

        image_url = None
        if include_images:
            # Actively fetch rather than passively reading the cache -- the
            # week's top game isn't necessarily the player's currently
            # active game, so there's no guarantee its artwork was ever
            # fetched via the normal real-time pipeline. fetch_game_assets
            # already checks the cache first, so this costs nothing extra
            # when art is already cached.
            assets = await fetch_game_assets(self.hass, top_game)
            raw_image_url = assets.get("hero") or assets.get("grid")
            _LOGGER.debug(
                "Gaming Status: weekly report image lookup for top game '%s' -- "
                "assets=%s, raw_image_url=%r",
                top_game,
                assets,
                raw_image_url,
            )
            if raw_image_url:
                image_url = await self._make_external_url(raw_image_url, top_game)
                _LOGGER.debug(
                    "Gaming Status: weekly report image resolved to %r", image_url
                )

        # Only meaningful when Discord Notification Colors mode is "Game
        # Color" -- _resolve_discord_color falls back to the default gold
        # otherwise. No explicit color-extraction-enabled check needed:
        # _color_history_cache is only ever populated when extraction is
        # on, so this naturally resolves to None (and the default color)
        # when it's off, for anyone, with no separate gate required.
        from .library_scan import _dominant_color_for

        game_color = None
        top_player_name = players_stats[0]["name"]
        platform_sensors = self.hass.data.get(DOMAIN, {}).get("platform_sensors", {})
        for sensor_obj in platform_sensors.values():
            if getattr(sensor_obj, "_owner_name", None) == top_player_name:
                color = _dominant_color_for(sensor_obj, top_game)
                if color:
                    game_color = color
                    break
        _LOGGER.debug(
            "Gaming Status: weekly report color lookup for top game '%s' "
            "(player '%s') -- resolved to %r",
            top_game,
            players_stats[0]["name"],
            game_color,
        )

        for ep_id in assigned:
            ep_type = self._cached_endpoints.get(ep_id, {}).get("type")
            message = discord_message if ep_type == "Discord" else plain_message
            await self._send_to_endpoint(
                ep_id,
                message,
                image_url=image_url,
                game_title=title,
                event_type="weekly",
                game_color=game_color,
            )
