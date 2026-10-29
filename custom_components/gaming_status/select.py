"""Select setting entities: each player's reminder intervals and maximum
age rating, offering exactly the choices Configure's dropdowns do."""

from typing import ClassVar

from homeassistant.components.select import SelectEntity

from .const import RATING_THRESHOLD_OPTIONS
from .device import ensure_player_device
from .settings import (
    REPEAT_CHOICES,
    PlayerParentalMixin,
    async_sync_parental_entities,
    players,
    ratings_available,
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_sync_parental_entities(hass, config_entry)
    entities = []
    for name in players(config_entry):
        device_info = ensure_player_device(hass, config_entry, name)
        entities += [
            ReminderInterval(config_entry, name, device_info, "screen_time"),
            ReminderInterval(config_entry, name, device_info, "curfew"),
        ]
        if ratings_available(config_entry):
            entities.append(MaxAgeRating(config_entry, name, device_info))
    async_add_entities(entities)


class _ParentalSelect(PlayerParentalMixin, SelectEntity):
    _key: str
    _choices: ClassVar[dict[int, str]]

    @property
    def current_option(self) -> str | None:
        try:
            return self._choices.get(int(self._rule_value(self._key)))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        value = next(v for v, label in self._choices.items() if label == option)
        self._set_rule_value(self._key, value)


class ReminderInterval(_ParentalSelect):
    _key = "repeat"
    _choices: ClassVar[dict[int, str]] = REPEAT_CHOICES

    def __init__(self, entry, player_name, device_info, rule):
        self._rule = rule
        self._suffix = f"parental_{rule}_repeat"
        self._attr_options = list(REPEAT_CHOICES.values())
        label = "Screen time" if rule == "screen_time" else "Curfew"
        self._setup_parental(
            entry,
            player_name,
            f"{label} reminder interval",
            device_info,
            "mdi:bell-ring",
        )


class MaxAgeRating(_ParentalSelect):
    _rule = "ratings"
    _key = "max_age_floor"
    _suffix = "parental_max_age"
    _choices: ClassVar[dict[int, str]] = dict(RATING_THRESHOLD_OPTIONS)

    def __init__(self, entry, player_name, device_info):
        self._attr_options = list(self._choices.values())
        self._setup_parental(
            entry,
            player_name,
            "Maximum age rating",
            device_info,
            "mdi:shield-account",
        )
