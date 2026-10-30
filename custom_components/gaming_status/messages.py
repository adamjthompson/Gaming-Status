"""All of Gaming Status's short user-facing wording in one place:

* describe_event / event_message -- plain-language descriptions of the bus
  events, shared by logbook.py (Logbook entries) and the `message` field
  every event carries (e.g. "Erik stopped playing Borderlands 4 (3h 4m)"),
  so the two always match.
* notify_* -- the text of Gaming Status's own notifications (notifier.py).

The weekly report's layout stays in notifier.py; it's formatting, not
short sentences."""

from __future__ import annotations

from .const import (
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


def describe_event(event_type: str, data: dict) -> tuple[str, str]:
    """(message without the player's name, icon) for one of this
    integration's events -- e.g. ("stopped playing Borderlands 4 (3h 4m)",
    "mdi:controller-off")."""
    game = data.get("game") or "a game"
    if event_type == EVENT_SESSION_STARTED:
        message = f"started playing {game}{_on(data.get('platform'))}"
        icon = "mdi:controller"
    elif event_type == EVENT_SESSION_SWITCHED:
        message = (
            f"switched from {data.get('previous_game') or 'a game'}"
            f"{_duration(data.get('duration_minutes'))} to {game}"
            f"{_on(data.get('platform'))}"
        )
        icon = "mdi:swap-horizontal"
    elif event_type == EVENT_SESSION_ENDED:
        message = f"stopped playing {game}{_duration(data.get('duration_minutes'))}"
        icon = "mdi:controller-off"
    elif event_type == EVENT_SCREEN_TIME_WARNING:
        left = data.get("minutes_left")
        message = f"has {left} minute{'s' if left != 1 else ''} of screen time left"
        icon = "mdi:timer-sand"
    elif event_type == EVENT_SCREEN_TIME_EXCEEDED:
        over = data.get("minutes_over") or 0
        message = (
            f"reached the {data.get('limit_minutes')}-minute screen time limit"
            + (f" ({over} minutes over)" if over else "")
        )
        icon = "mdi:timer-alert-outline"
    elif event_type == EVENT_CURFEW_EXCEEDED:
        over = data.get("minutes_over") or 0
        message = f"is still playing {game} after the {data.get('curfew')} curfew" + (
            f" ({over} minutes past)" if over else ""
        )
        icon = "mdi:weather-night"
    elif event_type == EVENT_RATING_EXCEEDED:
        age = data.get("age_floor")
        message = f"started {game}" + (f", rated for ages {age}+" if age else "")
        icon = "mdi:shield-alert-outline"
    else:
        tier = f" {data['tier']} trophy" if data.get("tier") else ""
        message = f'unlocked{tier} "{data.get("achievement")}" in {game}'
        icon = "mdi:trophy"
    return message, icon


def event_message(event_type: str, data: dict) -> str:
    """The full sentence an automation can send as-is, e.g. "Erik stopped
    playing Borderlands 4 (3h 4m)"."""
    message, _icon = describe_event(event_type, data)
    return f"{data.get('player') or 'Someone'} {message}"


# ---------------------------------------------------------------------------
# Notification wording (notifier.py). Shaped for notifications that carry
# the game separately (title / Discord embed / cover image), so these are
# intentionally not the same sentences as event_message's.
# ---------------------------------------------------------------------------


def notify_started(player: str) -> str:
    return f"{player} started playing"


def notify_switched(player: str, duration: str | None) -> str:
    return (
        f"{player} switched games after {duration}"
        if duration
        else f"{player} switched games"
    )


def notify_switch_title(old_game: str, new_game: str) -> str:
    return f"{old_game} > {new_game}"


def notify_ended(player: str, duration: str | None) -> str:
    return (
        f"{player} played for {duration}" if duration else f"{player} finished playing"
    )


def notify_screen_time(player: str, limit: int, overage: int, total: int) -> str:
    if overage > 0:
        return (
            f"❗️ {player} has exceeded the {limit}-minute screen time limit by "
            f"{overage} minutes ({total} minutes total)."
        )
    return f"❗️ {player} has reached the {limit}-minute screen time limit."


def notify_curfew(player: str, curfew_time: str, overage_minutes: int) -> str:
    if overage_minutes > 1:
        return (
            f"❗️ {player} has exceeded the {curfew_time} curfew by "
            f"{overage_minutes} minutes."
        )
    return f"❗️ {player} has reached the {curfew_time} curfew."


def notify_rating(player: str, game: str, age_floor) -> str:
    return f"❗️ {player} is playing {game}, rated for ages {age_floor}+."
