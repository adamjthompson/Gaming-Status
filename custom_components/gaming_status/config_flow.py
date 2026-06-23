"""Config flow for Gaming Status."""
from __future__ import annotations

import json
import logging
import re
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import get_url, NoURLAvailableError

from .const import (
    DOMAIN,
    CONF_STEAMGRIDDB_API_KEY,
    CONF_DISCORD_TOKEN,
    CONF_DISCORD_SERVER,
    OPT_RESET_HISTORY,
    OPT_GRACE_PERIOD,
    OPT_AWAY_GRACE_PERIOD,
    OPT_TRANSITION_GRACE,
    OPT_MIN_SESSION,
    OPT_PLAYERS,
    OPT_ENDPOINTS,
    OPT_WEEKLY_REPORT,
    OPT_PARENTAL,
    OPT_TITLE_OVERRIDES,
    OPT_CUSTOM_GRID,
    OPT_CUSTOM_HERO,
    OPT_CUSTOM_LOGO,
    OPT_CUSTOM_ICON,
    MENU_CUSTOM_ARTWORK,
    OPT_NOTIFY_ARTWORK,
    OPT_TITLE_CLEANUPS,
    OPT_GLOBAL_EXCLUSIONS,
    OPT_CUSTOM_COLORS,
    OPT_DISCORD_COLORS,
    DEFAULT_RESET_HISTORY,
    DEFAULT_GRACE_PERIOD_SECONDS,
    DEFAULT_AWAY_GRACE_PERIOD_SECONDS,
    DEFAULT_GAME_TRANSITION_GRACE_SECONDS,
    DEFAULT_MIN_SESSION_DURATION,
    MENU_GLOBAL_SETTINGS,
    MENU_MANAGE_PLAYERS,
    MENU_NOTIFICATIONS,
    MENU_PARENTAL,
    MENU_ADVANCED,
    PLAYER_PLATFORMS,
    OPT_USE_CACHE,
    DEFAULT_USE_CACHE,
    OPT_EXTRACT_COLOR,
    DEFAULT_EXTRACT_COLOR,
    OPT_CACHE_MAX_FILES,
    DEFAULT_CACHE_MAX_FILES,
    OPT_CACHE_MAX_DAYS,
    DEFAULT_CACHE_MAX_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(raw: str, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback

def _dump_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)

async def _fetch_discord_members(token: str, server_id: str) -> list:
    if not token or not server_id: return []
    url = f"https://discord.com/api/v10/guilds/{server_id}/members?limit=1000"
    headers = {"Authorization": f"Bot {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    members = []
                    for m in data:
                        user = m.get("user", {})
                        if not user.get("bot"):
                            display = m.get("nick") or user.get("global_name") or user.get("username")
                            members.append((user.get("id"), display))
                    return sorted(members, key=lambda x: x[1].lower())
    except Exception as e:
        _LOGGER.error(f"Error fetching Discord members: {e}")
    return []

def _players(options: dict) -> dict:
    data = _load_json(options.get(OPT_PLAYERS, ""), {})
    # Sort players alphabetically by their name (the dictionary key)
    return dict(sorted(data.items(), key=lambda item: str(item[0]).lower()))

def _endpoints(options: dict) -> dict:
    data = _load_json(options.get(OPT_ENDPOINTS, ""), {})
    # Sort endpoints alphabetically by their displayed "name" value
    return dict(sorted(data.items(), key=lambda item: str(item[1].get("name", "")).lower()))

def _weekly_report(options: dict) -> dict:
    return _load_json(options.get(OPT_WEEKLY_REPORT, ""), {})

def _parental(options: dict) -> dict:
    data = _load_json(options.get(OPT_PARENTAL, ""), {})
    # Sort parental rules alphabetically by the player's name
    return dict(sorted(data.items(), key=lambda item: str(item[0]).lower()))

def _safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())

# ---------------------------------------------------------------------------
# Initial config flow
# ---------------------------------------------------------------------------

class GamingStatusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        # --- SMART DEFAULT LOGIC ---
        smart_cache_default = DEFAULT_USE_CACHE
        try:
            public_url = get_url(self.hass, prefer_external=True, allow_internal=False)
            if public_url and public_url.startswith("https"):
                smart_cache_default = True
            else:
                smart_cache_default = False
        except NoURLAvailableError:
            smart_cache_default = False

        # Start with an empty list so Discord, Custom, and Playnite are unchecked by default
        smart_platforms = []
        if self.hass.config_entries.async_entries("steam_online"): smart_platforms.append("steam")
        if self.hass.config_entries.async_entries("xbox"): smart_platforms.append("xbox")
        if self.hass.config_entries.async_entries("playstation_network"): smart_platforms.append("playstation")

        if user_input is not None:
            self._temp_user_input = user_input
            
            # If they checked Discord, route them to the dedicated Discord Setup Screen
            from .const import OPT_ENABLED_PLATFORMS
            if "discord" in user_input.get(OPT_ENABLED_PLATFORMS, []):
                return await self.async_step_discord_setup()
                
            # If no Discord, finish setup immediately
            return self._create_entry_from_temp()

        from .const import OPT_ENABLED_PLATFORMS, OPT_ENABLE_NOTIFICATIONS, OPT_ENABLE_PARENTAL
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(OPT_ENABLED_PLATFORMS, default=smart_platforms): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="steam", label="Steam"),
                            selector.SelectOptionDict(value="xbox", label="Xbox"),
                            selector.SelectOptionDict(value="playstation", label="PlayStation"),
                            selector.SelectOptionDict(value="discord", label="Discord"),
                            selector.SelectOptionDict(value="custom", label="Custom Tracker"),
                            selector.SelectOptionDict(value="playnite", label="Playnite"),
                            ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST
                    )
                ),
                vol.Optional(OPT_ENABLE_NOTIFICATIONS, default=False): bool,
                vol.Optional(OPT_ENABLE_PARENTAL, default=False): bool,
                vol.Optional(CONF_STEAMGRIDDB_API_KEY, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(OPT_USE_CACHE, default=smart_cache_default): bool,
            }),
            description_placeholders={"api_url": "https://www.steamgriddb.com/profile/api"},
        )

    async def async_step_discord_setup(self, user_input=None):
        if user_input is not None:
            self._temp_user_input.update(user_input)
            return self._create_entry_from_temp()

        return self.async_show_form(
            step_id="discord_setup",
            data_schema=vol.Schema({
                vol.Optional(CONF_DISCORD_TOKEN, default=""): str,
                vol.Optional(CONF_DISCORD_SERVER, default=""): str,
            }),
        )

    def _create_entry_from_temp(self):
        user_input = getattr(self, "_temp_user_input", {})
        
        api_key = user_input.get(CONF_STEAMGRIDDB_API_KEY, "").strip()
        dc_token = user_input.get(CONF_DISCORD_TOKEN, "").strip()
        dc_server = user_input.get(CONF_DISCORD_SERVER, "").strip()
        
        from .const import OPT_ENABLED_PLATFORMS, OPT_ENABLE_NOTIFICATIONS, OPT_ENABLE_PARENTAL
        use_cache = user_input.get(OPT_USE_CACHE, DEFAULT_USE_CACHE)
        enabled_platforms = user_input.get(OPT_ENABLED_PLATFORMS, [])
        enable_notifications = user_input.get(OPT_ENABLE_NOTIFICATIONS, False)
        enable_parental = user_input.get(OPT_ENABLE_PARENTAL, False)
        
        return self.async_create_entry(
            title="Gaming Status",
            data={
                CONF_STEAMGRIDDB_API_KEY: api_key,
                CONF_DISCORD_TOKEN: dc_token,
                CONF_DISCORD_SERVER: dc_server,
            },
            options={
                OPT_USE_CACHE: use_cache,
                OPT_ENABLED_PLATFORMS: enabled_platforms,
                OPT_ENABLE_NOTIFICATIONS: enable_notifications,
                OPT_ENABLE_PARENTAL: enable_parental
            },
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GamingStatusOptionsFlow(config_entry)

# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class GamingStatusOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._options: dict = dict(config_entry.options)
        self._editing_player: str | None = None
        self._editing_endpoint: str | None = None
        self._discord_members = []

    async def async_step_init(self, user_input=None):
        token = self._config_entry.data.get(CONF_DISCORD_TOKEN)
        server_id = self._config_entry.data.get(CONF_DISCORD_SERVER)
        if token and server_id and not self._discord_members:
            self._discord_members = await _fetch_discord_members(token, server_id)
            
        from .const import OPT_ENABLE_NOTIFICATIONS, OPT_ENABLE_PARENTAL
        
        menu_options = [MENU_MANAGE_PLAYERS]
        
        if self._options.get(OPT_ENABLE_NOTIFICATIONS, False):
            menu_options.append(MENU_NOTIFICATIONS)
            
        if self._options.get(OPT_ENABLE_PARENTAL, False):
            menu_options.append(MENU_PARENTAL)
            
        menu_options.extend([
            MENU_CUSTOM_ARTWORK,
            MENU_ADVANCED,
            MENU_GLOBAL_SETTINGS,
        ])
            
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    # -----------------------------------------------------------------------
    # Global settings
    # -----------------------------------------------------------------------

    async def async_step_global_settings(self, user_input=None):
        from .const import (
            OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS,
            OPT_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS,
            OPT_ENABLE_PARENTAL, DEFAULT_ENABLE_PARENTAL,
            OPT_USE_CACHE, DEFAULT_USE_CACHE,
            OPT_EXTRACT_COLOR, DEFAULT_EXTRACT_COLOR,
            OPT_CACHE_MAX_FILES, DEFAULT_CACHE_MAX_FILES,
            OPT_CACHE_MAX_DAYS, DEFAULT_CACHE_MAX_DAYS,
            OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS,
            OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS,
            OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS,
            OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION,
            OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY,
            OPT_REMOVE_DISABLED_SENSORS, DEFAULT_REMOVE_DISABLED_SENSORS
        )

        opts = self._options

        if user_input is not None:
            opts[OPT_ENABLED_PLATFORMS] = user_input.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)
            opts[OPT_ENABLE_NOTIFICATIONS] = user_input.get(OPT_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS)
            opts[OPT_ENABLE_PARENTAL] = user_input.get(OPT_ENABLE_PARENTAL, DEFAULT_ENABLE_PARENTAL)
            opts[OPT_USE_CACHE] = user_input.get(OPT_USE_CACHE, DEFAULT_USE_CACHE)
            opts[OPT_EXTRACT_COLOR] = user_input.get(OPT_EXTRACT_COLOR, DEFAULT_EXTRACT_COLOR) if user_input.get(OPT_USE_CACHE, DEFAULT_USE_CACHE) else False
            opts[OPT_CACHE_MAX_FILES] = user_input.get(OPT_CACHE_MAX_FILES, DEFAULT_CACHE_MAX_FILES)
            opts[OPT_CACHE_MAX_DAYS] = user_input.get(OPT_CACHE_MAX_DAYS, DEFAULT_CACHE_MAX_DAYS)
            opts[OPT_GRACE_PERIOD] = user_input.get(OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS)
            opts[OPT_AWAY_GRACE_PERIOD] = user_input.get(OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS)
            opts[OPT_TRANSITION_GRACE] = user_input.get(OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS)
            opts[OPT_MIN_SESSION] = user_input.get(OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION)
            opts[OPT_RESET_HISTORY] = user_input.get(OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY)
            opts[OPT_REMOVE_DISABLED_SENSORS] = user_input.get(OPT_REMOVE_DISABLED_SENSORS, DEFAULT_REMOVE_DISABLED_SENSORS)
            
            self._options = opts
            return await self._update_and_return()

        return self.async_show_form(
            step_id=MENU_GLOBAL_SETTINGS,
            data_schema=vol.Schema(
                {
                    vol.Optional(OPT_ENABLED_PLATFORMS, default=opts.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="steam", label="Steam"),
                                selector.SelectOptionDict(value="xbox", label="Xbox"),
                                selector.SelectOptionDict(value="playstation", label="PlayStation"),
                                selector.SelectOptionDict(value="discord", label="Discord"),
                                selector.SelectOptionDict(value="custom", label="Custom Tracker"),
                                selector.SelectOptionDict(value="playnite", label="Playnite"),
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST
                        )
                    ),
                    vol.Optional(OPT_ENABLE_NOTIFICATIONS, default=opts.get(OPT_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS)): bool,
                    vol.Optional(OPT_ENABLE_PARENTAL, default=opts.get(OPT_ENABLE_PARENTAL, DEFAULT_ENABLE_PARENTAL)): bool,
                    vol.Optional(OPT_USE_CACHE, default=opts.get(OPT_USE_CACHE, DEFAULT_USE_CACHE)): bool,
                    vol.Optional(OPT_EXTRACT_COLOR, default=opts.get(OPT_EXTRACT_COLOR, DEFAULT_EXTRACT_COLOR)): bool,
                    vol.Optional(OPT_CACHE_MAX_FILES, default=opts.get(OPT_CACHE_MAX_FILES, DEFAULT_CACHE_MAX_FILES)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_CACHE_MAX_DAYS, default=opts.get(OPT_CACHE_MAX_DAYS, DEFAULT_CACHE_MAX_DAYS)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_GRACE_PERIOD, default=opts.get(OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_AWAY_GRACE_PERIOD, default=opts.get(OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_TRANSITION_GRACE, default=opts.get(OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_MIN_SESSION, default=opts.get(OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION)): vol.All(int, vol.Range(min=0)),
                    vol.Optional(OPT_RESET_HISTORY, default=opts.get(OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY)): bool,
                    vol.Optional(OPT_REMOVE_DISABLED_SENSORS, default=opts.get(OPT_REMOVE_DISABLED_SENSORS, DEFAULT_REMOVE_DISABLED_SENSORS)): bool,
                }
            ),
        )

    # -----------------------------------------------------------------------
    # Player management
    # -----------------------------------------------------------------------

    async def async_step_manage_players(self, user_input=None):
        players = _players(self._options)
        player_names = list(players.keys())

        if user_input is not None:
            selection = user_input.get("player_choice")
            if selection == "__add_new__":
                self._editing_player = None
                return await self.async_step_add_player()
            elif selection in players:
                self._editing_player = selection
                return await self.async_step_edit_player()
            return await self.async_step_init()

        choices = [
            selector.SelectOptionDict(value=p, label=p) for p in player_names
        ]
        choices.insert(0, selector.SelectOptionDict(value="__add_new__", label="➕ Add New Player"))

        return self.async_show_form(
            step_id=MENU_MANAGE_PLAYERS,
            data_schema=vol.Schema(
                {
                    vol.Required("player_choice", default="__add_new__"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
            description_placeholders={
                "player_count_text": f"You have {len(player_names)} player(s) configured." if player_names else "You currently have 0 players configured.",
            },
        )

    async def async_step_add_player(self, user_input=None):
        errors = {}

        if user_input is not None:
            name = user_input.get("player_name", "").strip()
            if not name:
                errors["player_name"] = "name_required"
            else:
                players = _players(self._options)
                if name in players:
                    errors["player_name"] = "name_exists"
                else:
                    players[name] = self._player_data_from_input(user_input)
                    self._options[OPT_PLAYERS] = _dump_json(players)
                    self._editing_player = name
                    return await self.async_step_player_details()

        return self.async_show_form(
            step_id="add_player",
            data_schema=self._player_schema(is_new=True),
            errors=errors,
        )

    async def async_step_edit_player(self, user_input=None):
        players = _players(self._options)
        name = self._editing_player or ""
        existing = players.get(name, {})
        errors = {}

        if user_input is not None:
            if user_input.get("delete_player"):
                # Trigger the purge BEFORE deleting the player data
                await self._cleanup_player_entities(name)
                
                players.pop(name, None)
                self._options[OPT_PLAYERS] = _dump_json(players)
                
                parental = _parental(self._options)
                parental.pop(name, None)
                self._options[OPT_PARENTAL] = _dump_json(parental)
                return await self._update_and_return()
            else:
                from .const import OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS
                updated_platforms = self._player_data_from_input(user_input)
                enabled_platforms = self._options.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)
                
                for p in enabled_platforms:
                    if p in updated_platforms:
                        existing[p] = updated_platforms[p]
                    elif p in existing:
                        # Platform was removed! Purge it from the registry.
                        await self._cleanup_player_entities(name, [p])
                        del existing[p]
                
                players[name] = existing
                self._options[OPT_PLAYERS] = _dump_json(players)
                return await self.async_step_player_details()

        return self.async_show_form(
            step_id="edit_player",
            data_schema=self._player_schema(existing, is_new=False),
            errors=errors,
            description_placeholders={"player_name": name},
        )

    async def async_step_player_details(self, user_input=None):
        from .const import OPT_ENABLE_NOTIFICATIONS
        
        players = _players(self._options)
        name = self._editing_player or ""
        existing = players.get(name, {})

        endpoints = _endpoints(self._options)
        endpoint_options = [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in endpoints.items()
        ]
        
        notifications_enabled = self._options.get(OPT_ENABLE_NOTIFICATIONS, False)

        if user_input is not None:
            ghosted_raw = user_input.get("ghosted_by", "")
            exclude_raw = user_input.get("exclude_games", "")
            
            existing["ghosted_by"] = [
                e.strip() for e in ghosted_raw.split(",") if e.strip()
            ]
            existing["exclude_games"] = [
                e.strip() for e in exclude_raw.split(",") if e.strip()
            ]
            
            # Only update destinations if the UI actually displayed them
            if notifications_enabled:
                existing["notify_start_destinations"] = user_input.get("notify_start_destinations", [])
                existing["notify_end_destinations"] = user_input.get("notify_end_destinations", [])
            
            players[name] = existing
            self._options[OPT_PLAYERS] = _dump_json(players)
            return await self._update_and_return()

        ghosted_default = ", ".join(existing.get("ghosted_by", []))
        exclude_default = ", ".join(existing.get("exclude_games", []))

        schema_dict = {}

        if endpoint_options and notifications_enabled:
            schema_dict[vol.Optional("notify_start_destinations", default=existing.get("notify_start_destinations", []))] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=endpoint_options, 
                        multiple=True, 
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            )
            schema_dict[vol.Optional("notify_end_destinations", default=existing.get("notify_end_destinations", []))] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=endpoint_options, 
                        multiple=True, 
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            )

        schema_dict.update({
            vol.Optional("ghosted_by", default=ghosted_default): str,
            vol.Optional("exclude_games", default=exclude_default): str,
        })

        return self.async_show_form(
            step_id="player_details",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"player_name": name},
        )

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    async def async_step_notifications(self, user_input=None):
        opts = self._options
        endpoints = _endpoints(opts)
        
        if user_input is not None:
            # 1. Save the new artwork selection FIRST
            opts[OPT_NOTIFY_ARTWORK] = user_input.get(OPT_NOTIFY_ARTWORK, "game_cover_art")
            self._options = opts

            # 2. Then handle the routing
            selection = user_input.get("endpoint_choice")
            if selection == "__save_settings__":
                return await self._update_and_return()
            elif selection == "__add_new__":
                self._editing_endpoint = None
                return await self.async_step_add_endpoint()
            elif selection == "__discord_colors__":
                return await self.async_step_discord_colors()
            elif selection == "__weekly_report__":
                return await self.async_step_weekly_report()
            elif selection in endpoints:
                self._editing_endpoint = selection
                return await self.async_step_edit_endpoint()
            
            return await self._update_and_return()

        # Build the new dropdown choices with a dedicated Save option
        choices = [
            selector.SelectOptionDict(value="__save_settings__", label="Save Artwork Setting"),
            selector.SelectOptionDict(value="__add_new__", label="➕ Add New Notification"),
            selector.SelectOptionDict(value="__discord_colors__", label="Discord Notification Colors"),
            selector.SelectOptionDict(value="__weekly_report__", label="Weekly Report Settings"),
        ]
        
        for k, v in endpoints.items():
            choices.append(selector.SelectOptionDict(value=k, label=f"Edit: {v['name']}"))

        return self.async_show_form(
            step_id=MENU_NOTIFICATIONS,
            data_schema=vol.Schema(
                {
                    vol.Required("endpoint_choice", default="__save_settings__"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Optional(
                        OPT_NOTIFY_ARTWORK, 
                        default=opts.get(OPT_NOTIFY_ARTWORK, "game_cover_art")
                    ): vol.In({
                        "game_cover_art": "Cover/Grid (Vertical)",
                        "game_hero_art": "Hero (Horizontal)",
                        "game_logo_art": "Logo (Transparent Title)",
                        "game_icon_art": "Icon (Small Square)",
                        "none": "No Artwork"
                    }),
                }
            ),
        )

    async def async_step_add_endpoint(self, user_input=None):
        errors = {}

        if user_input is not None:
            ep_name = user_input.get("endpoint_name", "").strip()
            if not ep_name:
                errors["endpoint_name"] = "name_required"
            else:
                endpoints = _endpoints(self._options)
                ep_id = _safe_id(ep_name)
                if ep_id in endpoints:
                    ep_id = ep_id + "_2"
                endpoints[ep_id] = {
                    "name": ep_name,
                    "type": user_input.get("notification_type", "Mobile App"),
                    "notifier": user_input.get("notifier", "").strip(),
                    "target_id": user_input.get("target_id", "").strip(),
                }
                self._options[OPT_ENDPOINTS] = _dump_json(endpoints)
                return await self._update_and_return()

        return self.async_show_form(
            step_id="add_endpoint",
            data_schema=self._endpoint_schema(),
            errors=errors,
        )

    async def async_step_edit_endpoint(self, user_input=None):
        endpoints = _endpoints(self._options)
        ep_id = self._editing_endpoint or ""
        existing = endpoints.get(ep_id, {})
        errors = {}

        if user_input is not None:
            if user_input.get("delete_endpoint"):
                endpoints.pop(ep_id, None)
                self._cleanup_endpoint_refs(ep_id)
                self._options[OPT_ENDPOINTS] = _dump_json(endpoints)
                return await self._update_and_return()

            if not errors:
                existing["name"] = user_input.get("endpoint_name", existing.get("name")).strip()
                existing["type"] = user_input.get("notification_type", "Mobile App")
                existing["notifier"] = user_input.get("notifier", "").strip()
                existing["target_id"] = user_input.get("target_id", "").strip()
                endpoints[ep_id] = existing
                self._options[OPT_ENDPOINTS] = _dump_json(endpoints)
                return await self._update_and_return()

        return self.async_show_form(
            step_id="edit_endpoint",
            data_schema=self._endpoint_schema(existing, include_delete=True),
            errors=errors,
            description_placeholders={"endpoint_name": existing.get("name", ep_id)},
        )

    async def async_step_discord_colors(self, user_input=None):
        colors = _load_json(self._options.get(OPT_DISCORD_COLORS, ""), {})
        errors = {}

        _HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')

        if user_input is not None:
            for field in ("color_start", "color_end", "color_parental"):
                val = user_input.get(field, "").strip()
                if not _HEX_RE.match(val):
                    errors[field] = "invalid_hex_color"

            if not errors:
                self._options[OPT_DISCORD_COLORS] = _dump_json({
                    "mode": user_input.get("discord_color_mode", "default"),
                    "color_start": user_input.get("color_start", "#00FF00").strip(),
                    "color_end": user_input.get("color_end", "#FF0000").strip(),
                    "color_parental": user_input.get("color_parental", "#0000FF").strip(),
                })
                return await self._update_and_return()

        return self.async_show_form(
            step_id="discord_colors",
            errors=errors,
            data_schema=vol.Schema({
                vol.Optional("discord_color_mode", default=colors.get("mode", "default")): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="default", label="Default (Green / Red / Blue)"),
                            selector.SelectOptionDict(value="platform", label="Platform Colors"),
                            selector.SelectOptionDict(value="game", label="Game Color"),
                            selector.SelectOptionDict(value="custom", label="Custom Colors"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional("color_start", default=colors.get("color_start", "#00FF00")): str,
                vol.Optional("color_end", default=colors.get("color_end", "#FF0000")): str,
                vol.Optional("color_parental", default=colors.get("color_parental", "#0000FF")): str,
            }),
        )

    async def async_step_weekly_report(self, user_input=None):
        report = _weekly_report(self._options)
        endpoints = _endpoints(self._options)
        
        endpoint_options = [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in endpoints.items()
        ]

        if user_input is not None:
            report["enabled"] = user_input.get("enabled", False)
            report["day"] = user_input.get("day", 0)
            report["time"] = user_input.get("time", "09:00")
            report["destinations"] = user_input.get("destinations", [])
            self._options[OPT_WEEKLY_REPORT] = _dump_json(report)
            return await self._update_and_return()

        day_options = {
            0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
            4: "Friday", 5: "Saturday", 6: "Sunday",
        }

        schema_dict = {
            vol.Optional("enabled", default=report.get("enabled", False)): bool,
            vol.Optional("day", default=report.get("day", 0)): vol.In(day_options),
            vol.Optional("time", default=report.get("time", "09:00")): str,
        }

        if endpoint_options:
            schema_dict[vol.Optional("destinations", default=report.get("destinations", []))] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=endpoint_options, 
                        multiple=True, 
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            )

        return self.async_show_form(
            step_id="weekly_report",
            data_schema=vol.Schema(schema_dict),
        )

    # -----------------------------------------------------------------------
    # Parental controls
    # -----------------------------------------------------------------------

    async def async_step_parental_controls(self, user_input=None):
        players = _players(self._options)
        player_names = list(players.keys())

        if not player_names:
            return self.async_show_form(
                step_id=MENU_PARENTAL,
                data_schema=vol.Schema({}),
                description_placeholders={"info": "No players configured yet. Add players first."},
            )

        if user_input is not None:
            self._editing_player = user_input.get("player_choice")
            return await self.async_step_parental_player()

        return self.async_show_form(
            step_id=MENU_PARENTAL,
            data_schema=vol.Schema(
                {vol.Required("player_choice"): vol.In(player_names)}
            ),
        )

    async def async_step_parental_player(self, user_input=None):
        from .const import OPT_ENABLE_NOTIFICATIONS
        notifications_enabled = self._options.get(OPT_ENABLE_NOTIFICATIONS, False)
        
        name = self._editing_player or ""
        parental = _parental(self._options)
        rules = parental.get(name, {})
        st = rules.get("screen_time", {})
        cf = rules.get("curfew", {})

        REPEAT_OPTIONS = [
            selector.SelectOptionDict(value="0", label="Once per day"),
            selector.SelectOptionDict(value="15", label="Every 15 minutes"),
            selector.SelectOptionDict(value="30", label="Every 30 minutes"),
            selector.SelectOptionDict(value="60", label="Every 60 minutes"),
        ]

        endpoints = _endpoints(self._options)
        endpoint_options = [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in endpoints.items()
        ]

        if user_input is not None:
            # Preserve existing actions if the UI hides them, otherwise grab new user input
            st_action_final = user_input.get("st_action_targets", st.get("action", [])) if notifications_enabled else st.get("action", [])
            cf_action_final = user_input.get("cf_action_targets", cf.get("action", [])) if notifications_enabled else cf.get("action", [])
            
            rules["screen_time"] = {
                "enabled": user_input.get("st_enabled", False),
                "weekday_minutes": user_input.get("st_weekday_minutes", 120),
                "weekend_minutes": user_input.get("st_weekend_minutes", 180),
                "repeat": int(user_input.get("st_repeat", "0")),
                "action": st_action_final,
            }
            rules["curfew"] = {
                "enabled": user_input.get("cf_enabled", False),
                "weekday": user_input.get("cf_weekday", "22:00"),
                "weekend": user_input.get("cf_weekend", "23:00"),
                "repeat": int(user_input.get("cf_repeat", "0")),
                "action": cf_action_final,
            }
            parental[name] = rules
            self._options[OPT_PARENTAL] = _dump_json(parental)
            return await self._update_and_return()

        # --- Migration handling for older string configs ---
        st_action = st.get("action", [])
        if isinstance(st_action, str):
            if st_action and st_action.lower() != "none":
                st_action = [st_action.replace("endpoint_", "", 1)] if st_action.startswith("endpoint_") else [st_action]
            else:
                st_action = []

        cf_action = cf.get("action", [])
        if isinstance(cf_action, str):
            if cf_action and cf_action.lower() != "none":
                cf_action = [cf_action.replace("endpoint_", "", 1)] if cf_action.startswith("endpoint_") else [cf_action]
            else:
                cf_action = []

        schema_dict = {
            vol.Optional("st_enabled", default=st.get("enabled", False)): bool,
            vol.Optional("st_weekday_minutes", default=st.get("weekday_minutes", 120)): vol.All(int, vol.Range(min=0)),
            vol.Optional("st_weekend_minutes", default=st.get("weekend_minutes", 180)): vol.All(int, vol.Range(min=0)),
            vol.Optional("st_repeat", default=str(st.get("repeat", 0))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=REPEAT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        }

        if endpoint_options and notifications_enabled:
            schema_dict[vol.Optional("st_action_targets", default=st_action)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=endpoint_options, 
                    multiple=True, 
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            )

        schema_dict.update({
            vol.Optional("cf_enabled", default=cf.get("enabled", False)): bool,
            vol.Optional("cf_weekday", default=cf.get("weekday", "22:00")): str,
            vol.Optional("cf_weekend", default=cf.get("weekend", "23:00")): str,
            vol.Optional("cf_repeat", default=str(cf.get("repeat", 0))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=REPEAT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        })

        if endpoint_options and notifications_enabled:
            schema_dict[vol.Optional("cf_action_targets", default=cf_action)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=endpoint_options, 
                    multiple=True, 
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            )

        return self.async_show_form(
            step_id="parental_player",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"player_name": name},
        )

    # -----------------------------------------------------------------------
    # Custom Artwork
    # -----------------------------------------------------------------------

    async def async_step_custom_artwork(self, user_input=None):
        opts = self._options
        errors = {}

        if user_input is not None:
            for key, field in [
                (OPT_CUSTOM_GRID, "custom_grid"),
                (OPT_CUSTOM_HERO, "custom_hero"),
                (OPT_CUSTOM_LOGO, "custom_logo"),
                (OPT_CUSTOM_ICON, "custom_icon"),
                (OPT_CUSTOM_COLORS, "custom_colors"),
            ]:
                raw = user_input.get(field, "")
                parsed_dict = {}
                for item in raw.split(','):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        parsed_dict[k.strip()] = v.strip()
                opts[key] = _dump_json(parsed_dict)

            if not errors:
                self._options = opts
                return await self._update_and_return()

        def _get_dict_default(key, fallback):
            raw = opts.get(key)
            if raw:
                parsed = _load_json(raw, fallback)
                return ", ".join([f"{k} = {v}" for k, v in parsed.items()])
            return ", ".join([f"{k} = {v}" for k, v in fallback.items()])

        return self.async_show_form(
            step_id=MENU_CUSTOM_ARTWORK,
            data_schema=vol.Schema(
                {
                    vol.Optional("custom_grid", default=_get_dict_default(OPT_CUSTOM_GRID, {})): str,
                    vol.Optional("custom_hero", default=_get_dict_default(OPT_CUSTOM_HERO, {})): str,
                    vol.Optional("custom_logo", default=_get_dict_default(OPT_CUSTOM_LOGO, {})): str,
                    vol.Optional("custom_icon", default=_get_dict_default(OPT_CUSTOM_ICON, {})): str,
                    vol.Optional("custom_colors", default=_get_dict_default(OPT_CUSTOM_COLORS, {})): str,
                }
            ),
            description_placeholders={"example_url": "https://link-to-image.png"},
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # Advanced
    # -----------------------------------------------------------------------
    
    async def async_step_advanced(self, user_input=None):
        opts = self._options
        errors = {}

        if user_input is not None:
            for key, field in [
                (OPT_TITLE_OVERRIDES, "title_overrides"),
            ]:
                raw = user_input.get(field, "")
                parsed_dict = {}
                for item in raw.split(','):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        parsed_dict[k.strip()] = v.strip()
                opts[key] = _dump_json(parsed_dict)

            for key, field in [
                (OPT_TITLE_CLEANUPS, "title_cleanups"),
                (OPT_GLOBAL_EXCLUSIONS, "global_exclusions"),
            ]:
                raw = user_input.get(field, "")
                parsed_list = [x.strip() for x in raw.split(',') if x.strip()]
                opts[key] = _dump_json(parsed_list)

            api_key = user_input.get(CONF_STEAMGRIDDB_API_KEY, "").strip()
            dc_token = user_input.get(CONF_DISCORD_TOKEN, "").strip()
            dc_server = user_input.get(CONF_DISCORD_SERVER, "").strip()
            new_data = dict(self._config_entry.data)
            new_data[CONF_STEAMGRIDDB_API_KEY] = api_key
            new_data[CONF_DISCORD_TOKEN] = dc_token
            new_data[CONF_DISCORD_SERVER] = dc_server
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)

            if not errors:
                self._options = opts
                return await self._update_and_return()

        def _get_list_default(key, fallback):
            raw = opts.get(key)
            if raw:
                parsed = _load_json(raw, fallback)
                return ", ".join(parsed)
            return ", ".join(fallback)

        def _get_dict_default(key, fallback):
            raw = opts.get(key)
            if raw:
                parsed = _load_json(raw, fallback)
                # Universally reconstruct using '=' for the UI with clean spacing
                return ", ".join([f"{k} = {v}" for k, v in parsed.items()])
            return ", ".join([f"{k} = {v}" for k, v in fallback.items()])

        from .const import OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS
        enabled_platforms = opts.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)

        schema_dict = {
            vol.Optional(
                CONF_STEAMGRIDDB_API_KEY,
                default=self._config_entry.data.get(CONF_STEAMGRIDDB_API_KEY, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }

        if "discord" in enabled_platforms:
            schema_dict.update({
                vol.Optional(
                    CONF_DISCORD_TOKEN,
                    default=self._config_entry.data.get(CONF_DISCORD_TOKEN, ""),
                ): str,
                vol.Optional(
                    CONF_DISCORD_SERVER,
                    default=self._config_entry.data.get(CONF_DISCORD_SERVER, ""),
                ): str,
            })

        schema_dict.update({
            vol.Optional(
                "title_overrides",
                default=_get_dict_default(OPT_TITLE_OVERRIDES, {}),
            ): str,
            vol.Optional(
                "title_cleanups",
                default=_get_list_default(OPT_TITLE_CLEANUPS, []),
            ): str,
            vol.Optional(
                "global_exclusions",
                default=_get_list_default(OPT_GLOBAL_EXCLUSIONS, [
                    "Home", "Online", "Xbox App", "YouTube", "Netflix",
                    "Hulu", "Amazon Prime Video", "Spotify",
                    "Microsoft Store", "Store", "Xbox 360 Dashboard",
                    "Setting up...", "Wallpaper Engine",
                ]),
            ): str,
        })

        return self.async_show_form(
            step_id=MENU_ADVANCED,
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    # ---------------------------------------------------------------------------
    # Shared Helpers
    # ---------------------------------------------------------------------------

    def _player_schema(self, existing: dict | None = None, is_new: bool = False) -> vol.Schema:
        existing = existing or {}
        schema = {}
        
        if is_new:
            schema[vol.Required("player_name")] = str

        def _get_filtered_selector(integration: str, suffix: str | None = None, current_val: str = ""):
            options = []
            
            try:
                registry = er.async_get(self.hass)
                for entry in registry.entities.values():
                    if entry.domain == "sensor" and entry.platform == integration:
                        if suffix and not entry.entity_id.endswith(suffix):
                            continue
                        options.append(entry.entity_id)
            except Exception:
                pass

            if current_val and current_val not in options and current_val.lower() != "none":
                options.append(current_val)
                
            options = sorted(list(set(options)))
            options.insert(0, "none")

            if len(options) > 1:
                return selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=True
                    )
                )
            
            return selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", integration=integration)
            )

        def _field(platform: str):
            current = existing.get(platform, "")
            if current:
                return vol.Optional(platform, description={"suggested_value": current})
            return vol.Optional(platform)

        schema[_field("steam")] = _get_filtered_selector("steam_online", None, existing.get("steam", ""))
        schema[_field("xbox")] = _get_filtered_selector("xbox", "_status", existing.get("xbox", ""))
        schema[_field("playstation")] = _get_filtered_selector("playstation_network", "_online_status", existing.get("playstation", ""))
        if self._discord_members:
            dc_options = [selector.SelectOptionDict(value=m[0], label=f"{m[1]} ({m[0]})") for m in self._discord_members]
            dc_options.insert(0, selector.SelectOptionDict(value="none", label="None"))
            current_dc = existing.get("discord", "")
            if current_dc and current_dc != "none" and not any(o["value"] == current_dc for o in dc_options):
                dc_options.append(selector.SelectOptionDict(value=current_dc, label=f"Unknown ID ({current_dc})"))
            schema[_field("discord")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=dc_options, mode=selector.SelectSelectorMode.DROPDOWN)
            )
        else:
            schema[_field("discord")] = str
            
        schema[_field("custom")] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        schema[_field("playnite")] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor", integration="mqtt")
        )

        if not is_new:
            schema[vol.Optional("delete_player", default=False)] = bool

        return vol.Schema(schema)

    def _player_data_from_input(self, user_input: dict) -> dict:
        data: dict = {}
        for platform in PLAYER_PLATFORMS:
            val = user_input.get(platform)
            if val is not None:
                val = str(val).strip()
                if val and val.lower() != "none":
                    data[platform] = val
        return data

    def _get_notify_services(self) -> list[str]:
        services = []
        notify_services = self.hass.services.async_services().get("notify", {})
        for service in notify_services:
            services.append(f"notify.{service}")
        services.sort()
        return services

    def _endpoint_schema(self, existing: dict | None = None, include_delete: bool = False) -> vol.Schema:
        existing = existing or {}
        schema = {}
        
        if not include_delete:
            schema[vol.Required("endpoint_name")] = str
        else:
            schema[vol.Required("endpoint_name", default=existing.get("name", ""))] = str

        schema[vol.Optional("notification_type", default=existing.get("type", "Mobile App"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["Mobile App", "Discord", "SMS"], 
                mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
        
        services = self._get_notify_services()
        current_notifier = existing.get("notifier", "")
        if current_notifier and current_notifier not in services:
            services.insert(0, current_notifier)
            
        schema[vol.Optional("notifier", default=current_notifier)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=services,
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True
            )
        )
        
        schema[vol.Optional("target_id", default=existing.get("target_id", ""))] = str

        if include_delete:
            schema[vol.Optional("delete_endpoint", default=False)] = bool

        return vol.Schema(schema)

    def _cleanup_endpoint_refs(self, ep_id: str) -> None:
        players = _players(self._options)
        changed = False
        for pdata in players.values():
            for dest_key in ["notify_start_destinations", "notify_end_destinations"]:
                destinations = pdata.get(dest_key, [])
                if ep_id in destinations:
                    destinations.remove(ep_id)
                    pdata[dest_key] = destinations
                    changed = True
        if changed:
            self._options[OPT_PLAYERS] = _dump_json(players)

        report = _weekly_report(self._options)
        dests = report.get("destinations", [])
        if ep_id in dests:
            dests.remove(ep_id)
            report["destinations"] = dests
            self._options[OPT_WEEKLY_REPORT] = _dump_json(report)

    async def _cleanup_player_entities(self, player_name: str, platforms: list | None = None):
        """Forcefully remove entities from the registry when a player or platform is removed."""
        registry = er.async_get(self.hass)
        safe_owner = player_name.lower().replace(" ", "_")
        
        # If no specific platforms provided, remove ALL sensors for this player
        platforms_to_clean = platforms if platforms else ["steam", "xbox", "playstation", "discord", "custom", "master", "chart", "pc"]
        
        for p in platforms_to_clean:
            entity_id = f"sensor.gaming_status_{safe_owner}_{p}"
            
            if registry.async_get(entity_id):
                try:
                    registry.async_remove(entity_id)
                except Exception as e:
                    _LOGGER.warning(f"Could not remove {entity_id}: {e}")
    
    async def _update_and_return(self):
        """Save the updated options to Home Assistant and return to the main menu."""
        self.hass.config_entries.async_update_entry(self._config_entry, options=self._options)
        return await self.async_step_init()