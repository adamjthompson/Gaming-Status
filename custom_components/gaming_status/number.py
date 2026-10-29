"""Number setting entities: each player's screen time limits and warning
(see settings.py for how these read/write the integration's options)."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime

from .device import ensure_player_device
from .settings import PlayerParentalMixin, async_sync_parental_entities, players


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_sync_parental_entities(hass, config_entry)
    entities = []
    for name in players(config_entry):
        device_info = ensure_player_device(hass, config_entry, name)
        entities += [
            ScreenTimeMinutes(config_entry, name, device_info, "weekday"),
            ScreenTimeMinutes(config_entry, name, device_info, "weekend"),
            ScreenTimeWarning(config_entry, name, device_info),
        ]
    async_add_entities(entities)


class _ParentalNumber(PlayerParentalMixin, NumberEntity):
    _rule = "screen_time"
    _key: str
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float | None:
        return float(self._rule_value(self._key) or 0)

    async def async_set_native_value(self, value: float) -> None:
        self._set_rule_value(self._key, int(value))


class ScreenTimeMinutes(_ParentalNumber):
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, player_name, device_info, day_type):
        self._key = f"{day_type}_minutes"
        self._suffix = f"parental_screen_time_{day_type}"
        self._setup_parental(
            entry,
            player_name,
            f"{day_type.capitalize()} screen time",
            device_info,
            "mdi:timer-outline",
        )


class ScreenTimeWarning(_ParentalNumber):
    _key = "warning_minutes"
    _suffix = "parental_screen_time_warning"
    _attr_native_min_value = 0
    _attr_native_max_value = 120
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry, player_name, device_info):
        self._setup_parental(
            entry,
            player_name,
            "Screen time warning",
            device_info,
            "mdi:timer-sand",
        )
