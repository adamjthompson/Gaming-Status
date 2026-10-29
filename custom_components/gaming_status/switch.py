"""Switch setting entities -- per player: parental rule on/off, achievement
tracking and library scan per platform, show on public page; on the hub:
public page, notifications, parental controls and weekly report.

All read/write the integration's options through settings.py. Tracking and
library-scan switches add or remove entities, so they reload the
integration; everything else applies live.
"""

from typing import Any, ClassVar

from homeassistant.components.switch import SwitchEntity

from .const import (
    DEFAULT_ENABLE_ACHIEVEMENT_TRACKING,
    DEFAULT_ENABLE_LIBRARY_SCAN,
    OPT_ENABLE_ACHIEVEMENT_TRACKING,
    OPT_ENABLE_LIBRARY_SCAN,
    OPT_ENABLE_NOTIFICATIONS,
    OPT_ENABLE_PARENTAL,
    OPT_PLAYERS,
    OPT_PUBLIC_PAGE_ENABLED,
    OPT_WEEKLY_REPORT,
)
from .device import ensure_player_device, hub_device_info, safe_owner_slug
from .public_page import PLAYER_OPT_PUBLIC, async_invalidate_public_cache
from .settings import (
    LIBRARY_PLATFORMS,
    PlayerParentalMixin,
    SettingEntityMixin,
    async_sync_parental_entities,
    load_json_option,
    player_achievement_tracking,
    player_library_scan,
    players,
    ratings_available,
)

_PLATFORM_LABELS = {"steam": "Steam", "xbox": "Xbox", "playstation": "PlayStation"}


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_sync_parental_entities(hass, config_entry)
    opts = config_entry.options
    tracking_on = opts.get(
        OPT_ENABLE_ACHIEVEMENT_TRACKING, DEFAULT_ENABLE_ACHIEVEMENT_TRACKING
    )
    library_on = tracking_on and opts.get(
        OPT_ENABLE_LIBRARY_SCAN, DEFAULT_ENABLE_LIBRARY_SCAN
    )

    hub = hub_device_info()
    entities: list[SwitchEntity] = [
        GlobalSwitch(
            config_entry, hub, OPT_PUBLIC_PAGE_ENABLED, "Public page", "mdi:web"
        ),
        GlobalSwitch(
            config_entry, hub, OPT_ENABLE_NOTIFICATIONS, "Notifications", "mdi:bell"
        ),
        GlobalSwitch(
            config_entry,
            hub,
            OPT_ENABLE_PARENTAL,
            "Parental controls",
            "mdi:account-child",
        ),
        WeeklyReportSwitch(config_entry, hub),
    ]
    for name, player_data in players(config_entry).items():
        device_info = ensure_player_device(hass, config_entry, name)
        entities += [
            ParentalRuleSwitch(config_entry, name, device_info, "screen_time"),
            ParentalRuleSwitch(config_entry, name, device_info, "curfew"),
            PublicPageSwitch(config_entry, name, device_info),
        ]
        if ratings_available(config_entry):
            entities.append(
                ParentalRuleSwitch(config_entry, name, device_info, "ratings")
            )
        for platform in LIBRARY_PLATFORMS:
            if not player_data.get(platform):
                continue
            if tracking_on:
                entities.append(
                    TrackingSwitch(
                        config_entry, name, device_info, platform, "achievement"
                    )
                )
            if library_on:
                entities.append(
                    TrackingSwitch(config_entry, name, device_info, platform, "library")
                )
    async_add_entities(entities)


class GlobalSwitch(SettingEntityMixin, SwitchEntity):
    """An integration-wide on/off option, on the Gaming Status hub device."""

    def __init__(self, entry, device_info, option, name, icon):
        self._option = option
        self._setup_setting(
            entry,
            f"gaming_status_setting_{option}",
            f"switch.gaming_status_{option}",
            name,
            device_info,
            icon,
        )

    @property
    def is_on(self) -> bool:
        return bool(self._entry.options.get(self._option, False))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        option = self._option

        def mutate(opts: dict) -> None:
            opts[option] = value

        self._update(mutate)
        if option == OPT_ENABLE_PARENTAL:
            async_sync_parental_entities(self.hass, self._entry)
        elif option == OPT_PUBLIC_PAGE_ENABLED:
            async_invalidate_public_cache(self.hass)


class WeeklyReportSwitch(SettingEntityMixin, SwitchEntity):
    def __init__(self, entry, device_info):
        self._setup_setting(
            entry,
            "gaming_status_setting_weekly_report",
            "switch.gaming_status_weekly_report",
            "Weekly report",
            device_info,
            "mdi:calendar-week",
        )

    @property
    def is_on(self) -> bool:
        return bool(
            load_json_option(self._entry.options, OPT_WEEKLY_REPORT).get("enabled")
        )

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        def mutate(opts: dict) -> None:
            opts[OPT_WEEKLY_REPORT]["enabled"] = value

        self._update(mutate)


class ParentalRuleSwitch(PlayerParentalMixin, SwitchEntity):
    """Screen time limit / Curfew / Rating limit on or off for a player.
    Turning one on or off also enables/disables that rule's detail
    entities (Home Assistant adds or removes them about 30 seconds later)."""

    _NAMES: ClassVar[dict[str, tuple[str, str]]] = {
        "screen_time": ("Screen time limit", "mdi:timer-lock-outline"),
        "curfew": ("Curfew", "mdi:weather-night"),
        "ratings": ("Rating limit", "mdi:shield-alert-outline"),
    }

    def __init__(self, entry, player_name, device_info, rule):
        self._rule = rule
        self._suffix = f"parental_{rule}"
        name, icon = self._NAMES[rule]
        self._setup_parental(entry, player_name, name, device_info, icon)

    @property
    def is_on(self) -> bool:
        return bool(self._rule_value("enabled"))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        self._set_rule_value("enabled", value)
        async_sync_parental_entities(self.hass, self._entry)


class _PlayerSwitch(SettingEntityMixin, SwitchEntity):
    def _player_data(self) -> dict:
        return players(self._entry).get(self._player_name) or {}

    def _set_player_value(self, key: str, value, *, live: bool) -> None:
        player = self._player_name

        def mutate(opts: dict) -> None:
            opts[OPT_PLAYERS].setdefault(player, {})[key] = value

        self._update(mutate, live=live)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        raise NotImplementedError


class PublicPageSwitch(_PlayerSwitch):
    def __init__(self, entry, player_name, device_info):
        self._player_name = player_name
        slug = safe_owner_slug(player_name)
        self._setup_setting(
            entry,
            f"gaming_status_{slug}_show_on_public_page",
            f"switch.gaming_status_{slug}_show_on_public_page",
            f"{player_name} Show on public page",
            device_info,
            "mdi:web",
        )

    @property
    def is_on(self) -> bool:
        return bool(self._player_data().get(PLAYER_OPT_PUBLIC))

    def _set(self, value: bool) -> None:
        self._set_player_value(PLAYER_OPT_PUBLIC, value, live=True)
        async_invalidate_public_cache(self.hass)


class TrackingSwitch(_PlayerSwitch):
    """Per-platform achievement tracking or library scan. These add or
    remove sensors, so changing one reloads the integration."""

    def __init__(self, entry, player_name, device_info, platform, kind):
        self._player_name = player_name
        self._platform = platform
        self._kind = kind
        slug = safe_owner_slug(player_name)
        label = _PLATFORM_LABELS[platform]
        if kind == "achievement":
            self._key = f"enable_achievement_tracking_{platform}"
            what = (
                "trophy tracking"
                if platform == "playstation"
                else "achievement tracking"
            )
            icon = "mdi:trophy-outline"
        else:
            self._key = f"enable_library_scan_{platform}"
            what = "library scan"
            icon = "mdi:bookshelf"
        self._setup_setting(
            entry,
            f"gaming_status_{slug}_{self._key}",
            f"switch.gaming_status_{slug}_{self._key}",
            f"{player_name} {label} {what}",
            device_info,
            icon,
        )

    @property
    def is_on(self) -> bool:
        resolve = (
            player_achievement_tracking
            if self._kind == "achievement"
            else player_library_scan
        )
        return resolve(self._entry, self._player_data(), self._platform)

    def _set(self, value: bool) -> None:
        self._set_player_value(self._key, value, live=False)
