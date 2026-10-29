"""Event entity platform -- one event.gaming_status_<player>_session per
player recording their last session start/switch/stop, so the session
events notifier.py fires show up on dashboards, in history, and as a plain
"Event" trigger for anyone not using trigger.py's triggers."""

import json

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, callback

from .const import (
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    EVENT_SESSION_SWITCHED,
    OPT_PLAYERS,
)
from .device import ensure_player_device, safe_owner_slug

_EVENT_TYPES = {
    EVENT_SESSION_STARTED: "started",
    EVENT_SESSION_SWITCHED: "switched",
    EVENT_SESSION_ENDED: "stopped",
}
# Bus event fields carried onto the entity's attributes.
_ATTRIBUTES = (
    "game",
    "platform",
    "previous_game",
    "duration_minutes",
    "game_color",
    "game_cover_art",
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    raw_players = config_entry.options.get(OPT_PLAYERS, "{}")
    try:
        players = json.loads(raw_players) if raw_players else {}
    except (ValueError, TypeError):
        players = {}
    async_add_entities(
        GamingSessionEvent(name, ensure_player_device(hass, config_entry, name))
        for name in players
    )


class GamingSessionEvent(EventEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:controller"

    def __init__(self, player_name, device_info):
        self._attr_event_types = list(_EVENT_TYPES.values())
        safe_owner = safe_owner_slug(player_name)
        self._player_name = player_name
        self.entity_id = f"event.gaming_status_{safe_owner}_session"
        self._attr_unique_id = f"gaming_status_{safe_owner}_session_event"
        self._attr_name = f"{player_name} Gaming Session"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for event_type in _EVENT_TYPES:
            self.async_on_remove(
                self.hass.bus.async_listen(event_type, self._async_session_event)
            )

    @callback
    def _async_session_event(self, event: Event):
        if event.data.get("player") != self._player_name:
            return
        self._trigger_event(
            _EVENT_TYPES[event.event_type],
            {k: event.data.get(k) for k in _ATTRIBUTES if k in event.data},
        )
        self.async_write_ha_state()
