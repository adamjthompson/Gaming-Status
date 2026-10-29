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
        elif event.event_type == EVENT_SESSION_ENDED:
            message = f"stopped playing {game}{_duration(data.get('duration_minutes'))}"
            icon = "mdi:controller-off"
        elif event.event_type == EVENT_SCREEN_TIME_WARNING:
            left = data.get("minutes_left")
            message = f"has {left} minute{'s' if left != 1 else ''} of screen time left"
            icon = "mdi:timer-sand"
        elif event.event_type == EVENT_SCREEN_TIME_EXCEEDED:
            over = data.get("minutes_over") or 0
            message = (
                f"reached the {data.get('limit_minutes')}-minute screen time limit"
                + (f" ({over} minutes over)" if over else "")
            )
            icon = "mdi:timer-alert-outline"
        elif event.event_type == EVENT_CURFEW_EXCEEDED:
            over = data.get("minutes_over") or 0
            message = (
                f"is still playing {game} after the {data.get('curfew')} curfew"
                + (f" ({over} minutes past)" if over else "")
            )
            icon = "mdi:weather-night"
        elif event.event_type == EVENT_RATING_EXCEEDED:
            age = data.get("age_floor")
            message = f"started {game}" + (f", rated for ages {age}+" if age else "")
            icon = "mdi:shield-alert-outline"
        else:
            tier = f" {data['tier']} trophy" if data.get("tier") else ""
            message = f'unlocked{tier} "{data.get("achievement")}" in {game}'
            icon = "mdi:trophy"
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
        EVENT_SCREEN_TIME_WARNING,
        EVENT_SCREEN_TIME_EXCEEDED,
        EVENT_CURFEW_EXCEEDED,
        EVENT_RATING_EXCEEDED,
        EVENT_ACHIEVEMENT_UNLOCKED,
    ):
        async_describe_event(DOMAIN, event_type, _describe)
