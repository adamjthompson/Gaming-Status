"""Logbook descriptions for the session events notifier.py fires, so the
Logbook (and each player's device and master sensor page) reads "Adam
started playing ELDEN RING" instead of a raw event name."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    DOMAIN,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    EVENT_SESSION_SWITCHED,
)


def _duration(minutes) -> str:
    if not minutes:
        return ""
    hours, mins = divmod(int(minutes), 60)
    return f" ({hours}h {mins}m)" if hours else f" ({mins}m)"


def _on(platform) -> str:
    return f" on {platform}" if platform else ""


@callback
def async_describe_events(
    hass: HomeAssistant,  # noqa: ARG001 -- required by the logbook platform API
    async_describe_event: Callable[
        [str, str, Callable[[Event], dict[str, str | None]]], None
    ],
) -> None:
    @callback
    def _describe(event: Event) -> dict[str, str | None]:
        data = event.data
        game = data.get("game") or "a game"
        if event.event_type == EVENT_SESSION_STARTED:
            message = f"started playing {game}{_on(data.get('platform'))}"
            icon = "mdi:controller"
        elif event.event_type == EVENT_SESSION_SWITCHED:
            message = (
                f"switched from {data.get('previous_game') or 'a game'}"
                f"{_duration(data.get('duration_minutes'))} to {game}"
                f"{_on(data.get('platform'))}"
            )
            icon = "mdi:swap-horizontal"
        else:
            message = f"stopped playing {game}{_duration(data.get('duration_minutes'))}"
            icon = "mdi:controller-off"
        return {
            LOGBOOK_ENTRY_NAME: data.get("player"),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
            LOGBOOK_ENTRY_ICON: icon,
        }

    for event_type in (
        EVENT_SESSION_STARTED,
        EVENT_SESSION_SWITCHED,
        EVENT_SESSION_ENDED,
    ):
        async_describe_event(DOMAIN, event_type, _describe)
