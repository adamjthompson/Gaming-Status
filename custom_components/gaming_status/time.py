"""Time setting entities: each player's weekday and weekend curfew."""

from datetime import time

from homeassistant.components.time import TimeEntity

from .device import ensure_player_device
from .settings import PlayerParentalMixin, async_sync_parental_entities, players


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_sync_parental_entities(hass, config_entry)
    entities = []
    for name in players(config_entry):
        device_info = ensure_player_device(hass, config_entry, name)
        entities += [
            CurfewTime(config_entry, name, device_info, "weekday"),
            CurfewTime(config_entry, name, device_info, "weekend"),
        ]
    async_add_entities(entities)


class CurfewTime(PlayerParentalMixin, TimeEntity):
    _rule = "curfew"

    def __init__(self, entry, player_name, device_info, day_type):
        self._key = day_type
        self._suffix = f"parental_curfew_{day_type}"
        self._setup_parental(
            entry,
            player_name,
            f"{day_type.capitalize()} curfew",
            device_info,
            "mdi:weather-night",
        )

    @property
    def native_value(self) -> time | None:
        # Stored as "HH:MM", or "HH:MM:SS" from Configure's time picker.
        try:
            hour, minute = (
                int(p) for p in str(self._rule_value(self._key)).split(":")[:2]
            )
            return time(hour, minute)
        except (TypeError, ValueError):
            return None

    async def async_set_value(self, value: time) -> None:
        self._set_rule_value(self._key, value.strftime("%H:%M"))
