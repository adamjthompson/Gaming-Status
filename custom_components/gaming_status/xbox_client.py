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
    per tracked player).

    Uses a dedicated httpx client (create_async_httpx_client), NOT HA's
    shared get_async_client() -- httpx's own default timeout is a hard 5
    seconds (connect/read/write/pool combined, confirmed against httpx's
    own DEFAULT_TIMEOUT_CONFIG), and get_title_history's title-history
    fetch is a single unpaginated request whose payload size scales with
    the account's library size (every title's name + achievement/
    gamerscore sub-object, all in one response). For a large library, that
    fixed 5s budget against a growing payload is a deterministic bottleneck,
    not transient network jitter -- it fails identically on every attempt,
    manual refresh included. Steam/PSN's own clients already set a
    generous explicit timeout on their own dedicated sessions
    (steam_client.py/psn_client.py, `aiohttp.ClientTimeout(total=15)`);
    Xbox never got the equivalent since it borrowed HA's shared client
    instead of building its own. create_async_httpx_client builds a new,
    independent client (unlike get_async_client's cached/shared one) --
    call it only here, once per config entry, and let the existing
    per-entry singleton cache below reuse it."""
    from homeassistant.helpers.httpx_client import create_async_httpx_client

    clients = hass.data.setdefault("gaming_status_xbox_clients", {})
    if entry.entry_id not in clients:
        xbox_http_client = create_async_httpx_client(hass, timeout=30.0)
        auth = AsyncConfigEntryAuth(xbox_http_client, oauth_session)
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
        # progress_state ("Achieved" vs "NotStarted"/"InProgress") is the
        # correct earned/locked signal -- progression.time_unlocked is a
        # non-optional datetime in the underlying model, so Xbox fills it
        # with a placeholder (e.g. 0001-01-01T00:00:00Z) for achievements
        # that were NEVER unlocked. That placeholder still parses to a
        # truthy datetime, so checking time_unlocked's truthiness alone
        # previously counted every achievement as earned.
        earned = [a for a in achievements if (a.progress_state or "").lower() == "achieved"]
        earned.sort(key=lambda a: a.progression.time_unlocked, reverse=True)

        def _icon_url(a):
            for asset in a.media_assets or []:
                if str(getattr(asset, "type", "")).lower() == "icon":
                    return asset.url
            return None

        return {
            "earned": len(earned),
            "total": len(achievements),
            "recent_unlocks": [
                {
                    "name": a.name,
                    "description": a.description,
                    "unlocked_at": a.progression.time_unlocked.isoformat(),
                    "icon_url": _icon_url(a),
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
