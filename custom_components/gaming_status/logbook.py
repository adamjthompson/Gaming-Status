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
    EVENT_ACHIEVEMENT_UNLOCKED,
    EVENT_CURFEW_EXCEEDED,
    EVENT_RATING_EXCEEDED,
    EVENT_SCREEN_TIME_EXCEEDED,
    EVENT_SCREEN_TIME_WARNING,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    EVENT_SESSION_SWITCHED,
)
from .messages import describe_event


@callback
def async_describe_events(
    hass: HomeAssistant,  # noqa: ARG001 -- required by the logbook platform API
    async_describe_event: Callable[
        [str, str, Callable[[Event], dict[str, str | None]]], None
    ],
) -> None:
    @callback
    def _describe(event: Event) -> dict[str, str | None]:
        message, icon = describe_event(event.event_type, event.data)
        return {
            LOGBOOK_ENTRY_NAME: event.data.get("player"),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: event.data.get("entity_id"),
            LOGBOOK_ENTRY_ICON: icon,
        }

    for event_type in (
        EVENT_SESSION_STARTED,
        EVENT_SESSION_SWITCHED,
        EVENT_SESSION_ENDED,
        EVENT_SCREEN_TIME_WARNING,
        EVENT_SCREEN_TIME_EXCEEDED,
        EVENT_CURFEW_EXCEEDED,
        EVENT_RATING_EXCEEDED,
        EVENT_ACHIEVEMENT_UNLOCKED,
    ):
        async_describe_event(DOMAIN, event_type, _describe)
