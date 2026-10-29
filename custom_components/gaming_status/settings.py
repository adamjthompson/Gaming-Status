"""Shared read/write access to Gaming Status's config-entry options for the
setting entities (number.py, select.py, switch.py, time.py).

The options stay the single source of truth -- an entity change is exactly
the same as saving that value in Configure. Two things differ from a
Configure save:

* "Live" settings (limits, curfew times, global toggles, ...) are read by
  the notifier/master sensors/public page on every check, so writing them
  doesn't need the full integration reload every options change normally
  triggers (__init__._async_options_updated). async_update_options sets a
  one-shot skip flag for those, then signals SIGNAL_SETTINGS_UPDATED so
  entities and master sensors pick up the new values.
* Parental-control entities are enabled/disabled in the entity registry to
  follow their rule (async_sync_parental_entities).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DEFAULT_ENABLE_ACHIEVEMENT_TRACKING,
    DEFAULT_ENABLE_LIBRARY_SCAN,
    DEFAULT_ENABLE_NATIVE_RATINGS,
    DOMAIN,
    OPT_ENABLE_ACHIEVEMENT_TRACKING,
    OPT_ENABLE_LIBRARY_SCAN,
    OPT_ENABLE_NATIVE_RATINGS,
    OPT_ENABLE_PARENTAL,
    OPT_PARENTAL,
    OPT_PLAYERS,
    OPT_WEEKLY_REPORT,
)
from .device import safe_owner_slug

SIGNAL_SETTINGS_UPDATED = f"{DOMAIN}_settings_updated"
_SKIP_RELOAD_KEY = "skip_reload"

# Configure's own defaults for a player with no parental entry yet (see
# config_flow.async_step_parental_player / notifier._check_parental_controls).
PARENTAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "screen_time": {
        "enabled": False,
        "weekday_minutes": 120,
        "weekend_minutes": 180,
        "repeat": 0,
        "warning_minutes": 0,
    },
    "curfew": {"enabled": False, "weekday": "22:00", "weekend": "23:00", "repeat": 0},
    "ratings": {"enabled": False, "max_age_floor": 13},
}

LIBRARY_PLATFORMS = ("steam", "xbox", "playstation")

# Configure's "action frequency" choices (config_flow REPEAT_OPTIONS) --
# the reminder-interval select entities offer exactly these, so a value set
# from an entity is always one Configure's dropdown can show.
REPEAT_CHOICES = {
    0: "Once per day",
    15: "Every 15 minutes",
    30: "Every 30 minutes",
    60: "Every 60 minutes",
}

_JSON_OPTIONS = (OPT_PLAYERS, OPT_PARENTAL, OPT_WEEKLY_REPORT)


# ---------------------------------------------------------------------------
# JSON options (players / parental_controls / weekly_report are stored as
# JSON text by config_flow._dump_json)
# ---------------------------------------------------------------------------


def load_json_option(options, key: str) -> dict:
    raw = options.get(key)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _dump_json_like(original, value: dict):
    """Writes back in the format the option was stored in."""
    if isinstance(original, dict):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def get_entry(hass: HomeAssistant):
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


@callback
def async_update_options(
    hass: HomeAssistant,
    entry,
    mutate: Callable[[dict], None],
    *,
    live: bool,
) -> None:
    """Applies `mutate` to a copy of the options (JSON options are handed to
    it already parsed, keyed by option name) and saves.

    `mutate` receives a dict of option -> value, where the JSON options
    (players, parental_controls, weekly_report) are parsed dicts.
    """
    new_options = dict(entry.options)
    parsed: dict[str, Any] = {}
    for key, raw in new_options.items():
        if key in _JSON_OPTIONS:
            parsed[key] = load_json_option(new_options, key)
        else:
            parsed[key] = raw
    for key in _JSON_OPTIONS:
        parsed.setdefault(key, {})
    mutate(parsed)
    for key, value in parsed.items():
        if key in _JSON_OPTIONS:
            if key not in new_options and not value:
                continue
            new_options[key] = _dump_json_like(entry.options.get(key), value)
        else:
            new_options[key] = value
    if new_options == dict(entry.options):
        return
    if live:
        hass.data.setdefault(DOMAIN, {})[_SKIP_RELOAD_KEY] = True
    hass.config_entries.async_update_entry(entry, options=new_options)
    if live:
        async_dispatcher_send(hass, SIGNAL_SETTINGS_UPDATED)


@callback
def consume_skip_reload(hass: HomeAssistant) -> bool:
    """True (once) if the options change being handled was a live entity
    write that shouldn't reload the integration."""
    return bool(hass.data.get(DOMAIN, {}).pop(_SKIP_RELOAD_KEY, False))


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def players(entry) -> dict:
    return load_json_option(entry.options, OPT_PLAYERS)


def parental_rule(entry, player_name: str, rule: str) -> dict:
    """A player's rule merged over Configure's defaults."""
    stored = (load_json_option(entry.options, OPT_PARENTAL).get(player_name) or {}).get(
        rule
    ) or {}
    return {**PARENTAL_DEFAULTS[rule], **stored}


def parental_rules_live(hass: HomeAssistant, player_name: str) -> dict:
    """Current parental rules for a player ({} while parental controls are
    off globally) -- what MasterGamingSensor evaluates against."""
    entry = get_entry(hass)
    if entry is None or not entry.options.get(OPT_ENABLE_PARENTAL, False):
        return {}
    return load_json_option(entry.options, OPT_PARENTAL).get(player_name) or {}


def player_achievement_tracking(entry, player_data: dict, platform: str) -> bool:
    """Per-player, per-platform achievement tracking preference -- same
    fallback chain sensor.py's async_setup_entry applies (platform flag,
    then the player's old flat flag, then the global setting)."""
    global_on = entry.options.get(
        OPT_ENABLE_ACHIEVEMENT_TRACKING, DEFAULT_ENABLE_ACHIEVEMENT_TRACKING
    )
    return bool(
        player_data.get(
            f"enable_achievement_tracking_{platform}",
            player_data.get("enable_achievement_tracking", global_on),
        )
    )


def player_library_scan(entry, player_data: dict, platform: str) -> bool:
    global_on = entry.options.get(OPT_ENABLE_LIBRARY_SCAN, DEFAULT_ENABLE_LIBRARY_SCAN)
    return bool(
        player_data.get(
            f"enable_library_scan_{platform}",
            player_data.get("enable_library_scan", global_on),
        )
    )


# ---------------------------------------------------------------------------
# Parental entity enable/disable
# ---------------------------------------------------------------------------

# Unique-id suffix -> the rule that has to be on for it to be enabled (None
# = only needs parental controls on globally: the rule switches themselves).
PARENTAL_ENTITY_RULES: dict[str, str | None] = {
    "parental_screen_time": None,
    "parental_curfew": None,
    "parental_ratings": None,
    "parental_screen_time_weekday": "screen_time",
    "parental_screen_time_weekend": "screen_time",
    "parental_screen_time_warning": "screen_time",
    "parental_screen_time_repeat": "screen_time",
    "parental_curfew_weekday": "curfew",
    "parental_curfew_weekend": "curfew",
    "parental_curfew_repeat": "curfew",
    "parental_max_age": "ratings",
}


def parental_unique_id(player_name: str, suffix: str) -> str:
    return f"gaming_status_{safe_owner_slug(player_name)}_{suffix}"


def ratings_available(entry) -> bool:
    """The rating rule only exists while native content ratings are on --
    Configure hides it otherwise (no rating data to compare against)."""
    return bool(
        entry.options.get(OPT_ENABLE_NATIVE_RATINGS, DEFAULT_ENABLE_NATIVE_RATINGS)
    )


def parental_entity_should_be_enabled(entry, player_name: str, suffix: str) -> bool:
    if not entry.options.get(OPT_ENABLE_PARENTAL, False):
        return False
    if suffix in ("parental_ratings", "parental_max_age") and not ratings_available(
        entry
    ):
        return False
    rule = PARENTAL_ENTITY_RULES[suffix]
    return rule is None or bool(parental_rule(entry, player_name, rule)["enabled"])


@callback
def async_sync_parental_entities(hass: HomeAssistant, entry) -> None:
    """Enables/disables each parental entity in the registry to follow its
    rule. Only ever re-enables entities this integration disabled itself --
    one the user disabled stays disabled."""
    registry = er.async_get(hass)
    for player_name in players(entry):
        for suffix in PARENTAL_ENTITY_RULES:
            unique_id = parental_unique_id(player_name, suffix)
            for domain in ("switch", "number", "select", "time"):
                entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
                if entity_id:
                    break
            else:
                continue
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None:
                continue
            want = parental_entity_should_be_enabled(entry, player_name, suffix)
            if want and reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                registry.async_update_entity(entity_id, disabled_by=None)
            elif not want and reg_entry.disabled_by is None:
                registry.async_update_entity(
                    entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
                )


# ---------------------------------------------------------------------------
# Base for the setting entities
# ---------------------------------------------------------------------------


class SettingEntityMixin:
    """Shared behavior for number/select/switch/time setting entities: no
    state of their own (every value is read from entry.options when the
    state is written), re-rendered whenever a live setting changes."""

    hass: HomeAssistant
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def _setup_setting(
        self,
        entry,
        unique_id: str,
        entity_id: str,
        name: str,
        device_info,
        icon: str | None = None,
    ) -> None:
        self._entry = entry
        self._attr_unique_id = unique_id
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_device_info = device_info
        if icon:
            self._attr_icon = icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(  # type: ignore[attr-defined]
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SETTINGS_UPDATED,
                self.async_write_ha_state,  # type: ignore[attr-defined]
            )
        )

    def _update(self, mutate: Callable[[dict], None], *, live: bool = True) -> None:
        async_update_options(self.hass, self._entry, mutate, live=live)


class PlayerParentalMixin(SettingEntityMixin):
    """A per-player parental setting: enabled in the registry only while
    its rule is on (see async_sync_parental_entities)."""

    _rule: str
    _suffix: str

    def _setup_parental(
        self, entry, player_name: str, name: str, device_info, icon=None
    ):
        self._player_name = player_name
        slug = safe_owner_slug(player_name)
        domain = type(self).__module__.rsplit(".", 1)[-1]
        self._setup_setting(
            entry,
            parental_unique_id(player_name, self._suffix),
            f"{domain}.gaming_status_{slug}_{self._suffix}",
            f"{player_name} {name}",
            device_info,
            icon,
        )
        self._attr_entity_registry_enabled_default = parental_entity_should_be_enabled(
            entry, player_name, self._suffix
        )

    def _rule_value(self, key: str):
        return parental_rule(self._entry, self._player_name, self._rule)[key]

    def _set_rule_value(self, key: str, value) -> None:
        player, rule = self._player_name, self._rule

        def mutate(opts: dict) -> None:
            rules = opts[OPT_PARENTAL].setdefault(player, {})
            rules[rule] = {**parental_rule(self._entry, player, rule), key: value}

        self._update(mutate)
