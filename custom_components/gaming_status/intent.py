"""Assist intents: "Who's playing games?", "What is <player> playing?" and
"How long has <player> played?", answered from the master sensors.

AI-backed assistants get these as tools via llm.py. Home Assistant's
built-in Assist only matches sentences from the user's own
custom_sentences folder, so the README ships a ready-made sentence file.
"""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, intent

from .const import DOMAIN, OPT_PLAYERS
from .device import resolve_master_entity_id

INTENT_WHO_IS_PLAYING = "GamingStatusWhoIsPlaying"
INTENT_WHAT_IS_PLAYING = "GamingStatusWhatIsPlaying"
INTENT_PLAY_TIME = "GamingStatusPlayTime"
INTENTS = (INTENT_WHO_IS_PLAYING, INTENT_WHAT_IS_PLAYING, INTENT_PLAY_TIME)

_OFFLINE = {"offline", "unknown", "unavailable", "source missing", ""}


async def async_setup_intents(hass: HomeAssistant) -> None:
    intent.async_register(hass, WhoIsPlayingIntent())
    intent.async_register(hass, WhatIsPlayingIntent())
    intent.async_register(hass, PlayTimeIntent())


def player_names(hass: HomeAssistant) -> list[str]:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return []
    raw = entries[0].options.get(OPT_PLAYERS) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except ValueError:
            return []
    return list(raw) if isinstance(raw, dict) else []


def master_entity_ids(hass: HomeAssistant) -> list[str]:
    return [resolve_master_entity_id(hass, name) for name in player_names(hass)]


def _master_state(hass: HomeAssistant, name: str):
    return hass.states.get(resolve_master_entity_id(hass, name))


def _is_playing(state) -> bool:
    return bool(state) and str(state.state).lower() not in _OFFLINE


def _now_playing(name: str, state) -> str:
    platform = state.attributes.get("active_platform")
    return f"{name} is playing {state.state}" + (f" on {platform}" if platform else "")


def _hours(value) -> str:
    try:
        total_minutes = round(float(value) * 60)
    except (TypeError, ValueError):
        total_minutes = 0
    hours, minutes = divmod(total_minutes, 60)
    h = f"{hours} hour{'s' if hours != 1 else ''}"
    m = f"{minutes} minute{'s' if minutes != 1 else ''}"
    if hours and minutes:
        return f"{h} and {m}"
    return h if hours else m


class _PlayerIntent(intent.IntentHandler):
    """Base for intents that take one player name."""

    @property
    def slot_schema(self) -> dict | None:
        return {"player": cv.string}

    def _resolve_player(self, intent_obj: intent.Intent) -> str | None:
        spoken = str(intent_obj.slots["player"]["value"]).strip().casefold()
        for name in player_names(intent_obj.hass):
            if name.casefold() == spoken:
                return name
        return None

    def _respond(self, intent_obj: intent.Intent, speech: str) -> intent.IntentResponse:
        response = intent_obj.create_response()
        response.async_set_speech(speech)
        return response


class WhoIsPlayingIntent(_PlayerIntent):
    intent_type = INTENT_WHO_IS_PLAYING
    description = (
        "Lists which Gaming Status players are playing a game right now, and what"
    )

    @property
    def slot_schema(self) -> dict | None:
        return None

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        playing = [
            _now_playing(name, state)
            for name in player_names(hass)
            if _is_playing(state := _master_state(hass, name))
        ]
        if not playing:
            speech = "Nobody is playing games right now."
        elif len(playing) == 1:
            speech = f"{playing[0]}."
        else:
            speech = f"{', '.join(playing[:-1])}, and {playing[-1]}."
        return self._respond(intent_obj, speech)


class WhatIsPlayingIntent(_PlayerIntent):
    intent_type = INTENT_WHAT_IS_PLAYING
    description = "Tells what game a Gaming Status player is playing right now"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        name = self._resolve_player(intent_obj)
        if name is None:
            return self._respond(intent_obj, "I don't know that player.")
        state = _master_state(intent_obj.hass, name)
        if _is_playing(state):
            return self._respond(intent_obj, f"{_now_playing(name, state)}.")
        last = state.attributes.get("last_played_game") if state else None
        speech = f"{name} isn't playing anything right now." + (
            f" The last game was {last}." if last else ""
        )
        return self._respond(intent_obj, speech)


class PlayTimeIntent(_PlayerIntent):
    intent_type = INTENT_PLAY_TIME
    description = "Tells how long a Gaming Status player has played today and this week"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        name = self._resolve_player(intent_obj)
        if name is None:
            return self._respond(intent_obj, "I don't know that player.")
        state = _master_state(intent_obj.hass, name)
        attrs = state.attributes if state else {}
        return self._respond(
            intent_obj,
            f"{name} has played {_hours(attrs.get('total_daily_hours'))} today "
            f"and {_hours(attrs.get('total_weekly_hours'))} this week.",
        )
