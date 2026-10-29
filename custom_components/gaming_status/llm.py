"""Offers intent.py's intents as tools to AI-backed Assist conversation
agents, while any player's master sensor is exposed to that assistant.

The llm tools platform this builds on is newer than 2026.4; HA preloads
this module on every version, so on older ones the imports below fail
quietly and nothing is offered (older HA exposed registered intents to AI
agents on its own).
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .intent import INTENTS, master_entity_ids

try:
    from homeassistant.components.homeassistant import async_should_expose
    from homeassistant.components.llm import LLMTools
    from homeassistant.helpers import intent
    from homeassistant.helpers.llm import LLM_API_ASSIST, IntentTool
except ImportError:  # HA without the llm tools platform
    LLMTools = None  # type: ignore[assignment,misc]

_TITLES = {
    "GamingStatusWhoIsPlaying": "Who is playing games",
    "GamingStatusWhatIsPlaying": "What a player is playing",
    "GamingStatusPlayTime": "How long a player has played",
}


@callback
def async_get_tools(hass: HomeAssistant, llm_context: Any, api_id: str) -> Any:
    if LLMTools is None or api_id != LLM_API_ASSIST or not llm_context.assistant:
        return None
    if not any(
        async_should_expose(hass, llm_context.assistant, entity_id)
        for entity_id in master_entity_ids(hass)
    ):
        return None
    tools = [
        IntentTool(
            f"{DOMAIN}__{handler.intent_type}",
            handler,
            title=_TITLES[handler.intent_type],
            integration=DOMAIN,
        )
        for handler in intent.async_get(hass)
        if handler.intent_type in INTENTS
    ]
    return LLMTools(tools=tools) if tools else None
