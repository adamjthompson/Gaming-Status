"""Xbox Live client for native achievement enrichment (current game +
full-library scan) -- reuses HA core's official "xbox" integration's own
OAuth2Session (see utils.resolve_xbox_entry_and_session) instead of a
separate credential like OpenXBL. The AsyncConfigEntryAuth bridge and the
get_friend_by_xuid-based presence lookup below are ported/adapted from
home-assistant/core's own homeassistant/components/xbox/api.py and
coordinator.py (confirmed live against the dev branch) -- the exact adapter
and calls the official integration already makes on itself, just never
exposing title_id/individual achievement detail as sensor attributes.

Requires the `python-xbox` package (import name `pythonxbox`) -- already a
dependency of HA core's own `xbox` integration, so guaranteed present in the
same venv once that integration is installed.
"""
from __future__ import annotations

import logging

from homeassistant.util.dt import utc_from_timestamp

from pythonxbox.authentication.manager import AuthenticationManager
from pythonxbox.authentication.models import OAuth2TokenResponse
from pythonxbox.api.client import XboxLiveClient

_LOGGER = logging.getLogger(__name__)


class AsyncConfigEntryAuth(AuthenticationManager):
    """Bridges HA's own OAuth2Session (already holding a valid, auto-
    refreshing xbox config entry token) into pythonxbox's AuthenticationManager.
    Client id/secret/redirect_uri are left blank -- HA already owns the OAuth
    authorization flow and token refresh, so only the XAU/XSTS hops (which
    don't need them) are ever exercised here."""

    def __init__(self, async_client, oauth_session) -> None:
        super().__init__(async_client, "", "", "")
        self._oauth_session = oauth_session
        self.oauth = self._get_oauth_token()

    def _get_oauth_token(self) -> OAuth2TokenResponse:
        tokens = {**self._oauth_session.token}
        issued = tokens["expires_at"] - tokens["expires_in"]
        del tokens["expires_at"]
        token_response = OAuth2TokenResponse.model_validate(tokens)
        token_response.issued = utc_from_timestamp(issued)
        return token_response

    async def refresh_tokens(self) -> None:
        await self._oauth_session.async_ensure_token_valid()
        self.oauth = self._get_oauth_token()
        await super().refresh_tokens()


def get_xbox_client(hass, entry, oauth_session):
    """XboxLiveClient singleton per xbox config entry -- shared across every
    player tracked against the same Xbox account, same reasoning as the
    existing PSN client singleton (one live auth/token-refresh state, not one
    per tracked player)."""
    from homeassistant.helpers.httpx_client import get_async_client

    clients = hass.data.setdefault("gaming_status_xbox_clients", {})
    if entry.entry_id not in clients:
        auth = AsyncConfigEntryAuth(get_async_client(hass), oauth_session)
        clients[entry.entry_id] = XboxLiveClient(auth)
    return clients[entry.entry_id]


async def async_get_current_title_id(client, xuid):
    """Resolves the numeric Xbox title_id for whatever `xuid` is currently
    playing. `get_friend_by_xuid` works identically for the account owner
    (calling it with their own xuid) or any tracked friend -- the same call
    HA's own xbox coordinator makes for both, just never exposed as a sensor
    attribute. Returns None if nothing is currently
    Active+is_game+is_primary (not playing, or presence not visible). Never
    raises."""
    try:
        response = await client.people.get_friend_by_xuid(xuid)
        person = (response.people or [None])[0]
        if not person:
            return None
        detail = next(
            (d for d in person.presence_details or []
             if d.state == "Active" and d.title_id and d.is_game and d.is_primary),
            None,
        )
        return detail.title_id if detail else None
    except Exception as e:
        _LOGGER.debug("[Gaming Status] Xbox title_id resolution failed for xuid %s: %s", xuid, e)
        return None


async def async_get_achievements(client, xuid, title_id, recent_limit=10):
    """Individual achievement detail for one title -- earned/total counts
    plus a bounded, newest-first recent-unlocks list (name/description/
    unlocked_at). Rate-limited client-side by pythonxbox itself
    (AchievementsProvider.RATE_LIMITS: ~100/15s burst, 300/300s sustained) --
    no separate token-bucket limiter needed here. Never raises; returns None
    on failure."""
    try:
        response = await client.achievements.get_achievements_xboxone_gameprogress(xuid, title_id)
        achievements = response.achievements or []
        earned = [a for a in achievements if a.progression and a.progression.time_unlocked]
        earned.sort(key=lambda a: a.progression.time_unlocked, reverse=True)
        return {
            "earned": len(earned),
            "total": len(achievements),
            "recent_unlocks": [
                {
                    "name": a.name,
                    "description": a.description,
                    "unlocked_at": a.progression.time_unlocked.isoformat(),
                }
                for a in earned[:recent_limit]
            ],
        }
    except Exception as e:
        _LOGGER.debug("[Gaming Status] Xbox achievement fetch failed for xuid %s title %s: %s", xuid, title_id, e)
        return None


async def async_get_title_history(client, xuid, max_items=1000):
    """Full-library-scan source for Xbox -- ONE non-paginated call returns
    every title's name + achievement earned/total + gamerscore already, no
    per-title achievement call needed for the library view (unlike Steam/
    PSN). Returns a list of raw pythonxbox Title objects (each with
    .achievement.{current_achievements,total_achievements,current_gamerscore,
    total_gamerscore}, .name, .title_id, .display_image). Deliberately lets
    failures propagate (unlike this module's other fetch helpers) so
    utils.fetch_xbox_title_history can tell a genuine API failure apart from
    an account with a legitimately empty library."""
    response = await client.titlehub.get_title_history(xuid, max_items=max_items)
    return response.titles or []
