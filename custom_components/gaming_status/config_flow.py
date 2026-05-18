"""Config flow for Gaming Status."""
from __future__ import annotations

import json
import logging
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_STEAMGRIDDB_API_KEY,
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
    OPT_CUSTOM_COVERS,
    OPT_TITLE_CLEANUPS,
    OPT_GLOBAL_EXCLUSIONS,
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

def _players(options: dict) -> dict:
    return _load_json(options.get(OPT_PLAYERS, ""), {})

def _endpoints(options: dict) -> dict:
    return _load_json(options.get(OPT_ENDPOINTS, ""), {})

def _weekly_report(options: dict) -> dict:
    return _load_json(options.get(OPT_WEEKLY_REPORT, ""), {})

def _parental(options: dict) -> dict:
    return _load_json(options.get(OPT_PARENTAL, ""), {})

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

        if user_input is not None:
            api_key = user_input.get(CONF_STEAMGRIDDB_API_KEY, "").strip()
            return self.async_create_entry(
                title="Gaming Status",
                data={CONF_STEAMGRIDDB_API_KEY: api_key},
                options={},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Optional(CONF_STEAMGRIDDB_API_KEY, default=""): str}
            ),
            description_placeholders={"api_url": "https://www.steamgriddb.com/profile/api"},
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

    async def _update_and_return(self):
        """Save data and route user back to the main menu."""
        self.hass.config_entries.async_update_entry(self._config_entry, options=self._options)
        return await self.async_step_init()

    # -----------------------------------------------------------------------
    # Top-level menu
    # -----------------------------------------------------------------------

    async def async_step_init(self, user_input=None):
        """Main menu — choose a section to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                MENU_MANAGE_PLAYERS,
                MENU_NOTIFICATIONS,
                MENU_PARENTAL,
                MENU_ADVANCED,
                MENU_GLOBAL_SETTINGS,
            ],
        )

    # -----------------------------------------------------------------------
    # Global settings
    # -----------------------------------------------------------------------

    async def async_step_global_settings(self, user_input=None):
        opts = self._options

        if user_input is not None:
            opts[OPT_RESET_HISTORY] = user_input[OPT_RESET_HISTORY]
            opts[OPT_GRACE_PERIOD] = user_input[OPT_GRACE_PERIOD]
            opts[OPT_AWAY_GRACE_PERIOD] = user_input[OPT_AWAY_GRACE_PERIOD]
            opts[OPT_TRANSITION_GRACE] = user_input[OPT_TRANSITION_GRACE]
            opts[OPT_MIN_SESSION] = user_input[OPT_MIN_SESSION]
            self._options = opts
            return await self._update_and_return()

        return self.async_show_form(
            step_id=MENU_GLOBAL_SETTINGS,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPT_GRACE_PERIOD,
                        default=opts.get(OPT_GRACE_PERIOD, DEFAULT_GRACE_PERIOD_SECONDS),
                    ): vol.All(int, vol.Range(min=0)),
                    vol.Optional(
                        OPT_AWAY_GRACE_PERIOD,
                        default=opts.get(OPT_AWAY_GRACE_PERIOD, DEFAULT_AWAY_GRACE_PERIOD_SECONDS),
                    ): vol.All(int, vol.Range(min=0)),
                    vol.Optional(
                        OPT_TRANSITION_GRACE,
                        default=opts.get(OPT_TRANSITION_GRACE, DEFAULT_GAME_TRANSITION_GRACE_SECONDS),
                    ): vol.All(int, vol.Range(min=0)),
                    vol.Optional(
                        OPT_MIN_SESSION,
                        default=opts.get(OPT_MIN_SESSION, DEFAULT_MIN_SESSION_DURATION),
                    ): vol.All(int, vol.Range(min=0)),
                    vol.Optional(
                        OPT_RESET_HISTORY,
                        default=opts.get(OPT_RESET_HISTORY, DEFAULT_RESET_HISTORY),
                    ): bool,
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
        choices.append(selector.SelectOptionDict(value="__add_new__", label="➕ Add new player"))

        return self.async_show_form(
            step_id=MENU_MANAGE_PLAYERS,
            data_schema=vol.Schema(
                {
                    # Added the default attribute to jump straight to Add new player
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
                players.pop(name, None)
                self._options[OPT_PLAYERS] = _dump_json(players)
                
                parental = _parental(self._options)
                parental.pop(name, None)
                self._options[OPT_PARENTAL] = _dump_json(parental)
                return await self._update_and_return()
            else:
                updated_platforms = self._player_data_from_input(user_input)
                for p in PLAYER_PLATFORMS:
                    if p in updated_platforms:
                        existing[p] = updated_platforms[p]
                    elif p in existing:
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
        players = _players(self._options)
        name = self._editing_player or ""
        existing = players.get(name, {})

        endpoints = _endpoints(self._options)
        endpoint_options = [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in endpoints.items()
        ]

        if user_input is not None:
            ghosted_raw = user_input.get("ghosted_by", "")
            exclude_raw = user_input.get("exclude_games", "")
            
            existing["ghosted_by"] = [
                e.strip() for e in ghosted_raw.split(",") if e.strip()
            ]
            existing["exclude_games"] = [
                e.strip() for e in exclude_raw.split(",") if e.strip()
            ]
            
            existing["notify_start_destinations"] = user_input.get("notify_start_destinations", [])
            existing["notify_end_destinations"] = user_input.get("notify_end_destinations", [])
            
            players[name] = existing
            self._options[OPT_PLAYERS] = _dump_json(players)
            return await self._update_and_return()

        ghosted_default = ", ".join(existing.get("ghosted_by", []))
        exclude_default = ", ".join(existing.get("exclude_games", []))

        schema_dict = {}

        if endpoint_options:
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
        endpoints = _endpoints(self._options)
        
        if user_input is not None:
            selection = user_input.get("endpoint_choice")
            if selection == "__add_new__":
                self._editing_endpoint = None
                return await self.async_step_add_endpoint()
            elif selection == "__weekly_report__":
                return await self.async_step_weekly_report()
            elif selection in endpoints:
                self._editing_endpoint = selection
                return await self.async_step_edit_endpoint()
            return await self.async_step_init()

        choices = [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in endpoints.items()
        ]
        
        # Renamed label to Add new notification
        choices.append(selector.SelectOptionDict(value="__add_new__", label="➕ Add new notification"))
        choices.append(selector.SelectOptionDict(value="__weekly_report__", label="📅 Weekly report settings"))

        return self.async_show_form(
            step_id=MENU_NOTIFICATIONS,
            data_schema=vol.Schema(
                {
                    # Added the default attribute to jump straight to Add new notification
                    vol.Required("endpoint_choice", default="__add_new__"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
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
        name = self._editing_player or ""
        parental = _parental(self._options)
        rules = parental.get(name, {})
        st = rules.get("screen_time", {})
        cf = rules.get("curfew", {})

        if user_input is not None:
            rules["screen_time"] = {
                "enabled": user_input.get("st_enabled", False),
                "weekday_minutes": user_input.get("st_weekday_minutes", 120),
                "weekend_minutes": user_input.get("st_weekend_minutes", 180),
                "action": user_input.get("st_action_target", "none"),
            }
            rules["curfew"] = {
                "enabled": user_input.get("cf_enabled", False),
                "weekday": user_input.get("cf_weekday", "22:00"),
                "weekend": user_input.get("cf_weekend", "23:00"),
                "action": user_input.get("cf_action_target", "none"),
            }
            parental[name] = rules
            self._options[OPT_PARENTAL] = _dump_json(parental)
            return await self._update_and_return()

        return self.async_show_form(
            step_id="parental_player",
            data_schema=vol.Schema(
                {
                    vol.Optional("st_enabled", default=st.get("enabled", False)): bool,
                    vol.Optional("st_weekday_minutes", default=st.get("weekday_minutes", 120)): vol.All(int, vol.Range(min=0)),
                    vol.Optional("st_weekend_minutes", default=st.get("weekend_minutes", 180)): vol.All(int, vol.Range(min=0)),
                    vol.Optional("st_action_target", default=st.get("action", "none")): self._get_action_targets(),
                    vol.Optional("cf_enabled", default=cf.get("enabled", False)): bool,
                    vol.Optional("cf_weekday", default=cf.get("weekday", "22:00")): str,
                    vol.Optional("cf_weekend", default=cf.get("weekend", "23:00")): str,
                    vol.Optional("cf_action_target", default=cf.get("action", "none")): self._get_action_targets(),
                }
            ),
            description_placeholders={"player_name": name},
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
                (OPT_CUSTOM_COVERS, "custom_covers"),
            ]:
                raw = user_input.get(field, "")
                parsed_dict = {}
                for item in raw.split(','):
                    if ':' in item:
                        k, v = item.split(':', 1)
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
            new_data = dict(self._config_entry.data)
            new_data[CONF_STEAMGRIDDB_API_KEY] = api_key
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
                return ", ".join([f"{k}: {v}" for k, v in parsed.items()])
            return ", ".join([f"{k}: {v}" for k, v in fallback.items()])

        return self.async_show_form(
            step_id=MENU_ADVANCED,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_STEAMGRIDDB_API_KEY,
                        default=self._config_entry.data.get(CONF_STEAMGRIDDB_API_KEY, ""),
                    ): str,
                    vol.Optional(
                        "title_overrides",
                        default=_get_dict_default(OPT_TITLE_OVERRIDES, {}),
                    ): str,
                    vol.Optional(
                        "custom_covers",
                        default=_get_dict_default(OPT_CUSTOM_COVERS, {}),
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
                }
            ),
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------------

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
                return vol.Optional(platform, default=current)
            return vol.Optional(platform)

        schema[_field("steam")] = _get_filtered_selector("steam_online", None, existing.get("steam", ""))
        schema[_field("xbox")] = _get_filtered_selector("xbox", "_status", existing.get("xbox", ""))
        schema[_field("playstation")] = _get_filtered_selector("playstation_network", "_online_status", existing.get("playstation", ""))
        schema[_field("custom")] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        if not is_new:
            schema[vol.Optional("delete_player", default=False)] = bool

        return vol.Schema(schema)

    def _player_data_from_input(self, user_input: dict) -> dict:
        data: dict = {}
        for platform in PLAYER_PLATFORMS:
            val = user_input.get(platform, "").strip()
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

    def _get_action_targets(self):
        options = [selector.SelectOptionDict(value="none", label="None")]
        
        for k, v in _endpoints(self._options).items():
            options.append(selector.SelectOptionDict(value=f"endpoint_{k}", label=f"Notify: {v['name']}"))
            
        for state in self.hass.states.async_all(["automation", "script"]):
            friendly = state.attributes.get("friendly_name", state.entity_id)
            label = f"Run: {friendly}"
            options.append(selector.SelectOptionDict(value=state.entity_id, label=label))
            
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options, 
                mode=selector.SelectSelectorMode.DROPDOWN, 
                custom_value=True
            )
        )

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