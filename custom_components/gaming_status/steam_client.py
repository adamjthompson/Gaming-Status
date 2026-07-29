"""Steam Web API client for native achievement + rating enrichment, covering
both the currently-tracked game (achievement counts, store rating) and the
full owned-games library scan (GetOwnedGames, used by library_scan.py).

Known, inherent limitation (documented, not hidden): Steam's achievement
endpoint frequently rejects requests for any SteamID64 that isn't the API
key's own account, regardless of privacy settings -- a long-documented Steam
Web API restriction. Resolving the API key per the owning steam_online
config entry (see utils.py) is the best available mitigation, not a full fix.
"""
from __future__ import annotations

import logging

import aiohttp

from .const import RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS, STEAM_API_BASE, STEAM_STORE_API_BASE
from .platform_exceptions import AuthError, GameDetailsPrivateError, MalformedResponseError, NetworkError, RateLimitedError
from .rate_limiter import RateLimiter

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)


class SteamClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, rate_limiter: RateLimiter) -> None:
        self._session = session
        self._api_key = api_key
        self._rate_limiter = rate_limiter

    async def _get(self, path: str, params: dict) -> dict:
        await self._rate_limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
        url = f"{STEAM_API_BASE}/{path}"
        query = {"key": self._api_key, **params}
        try:
            async with self._session.get(url, params=query, timeout=_TIMEOUT) as resp:
                if resp.status == 429:
                    raise RateLimitedError(f"Steam rate-limited {path}")
                if resp.status in (401, 403):
                    raise AuthError(f"Steam rejected the API key calling {path}")
                if resp.status != 200:
                    raise NetworkError(f"Steam returned HTTP {resp.status} for {path}")
                try:
                    return await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise MalformedResponseError(f"Unexpected response body from Steam {path}: {err}") from err
        except aiohttp.ClientError as err:
            raise NetworkError(f"Error communicating with Steam ({path}): {err}") from err
        except TimeoutError as err:
            raise NetworkError(f"Timed out reaching Steam ({path}): {err}") from err

    async def async_get_owned_games(self, steamid64: str) -> list[dict]:
        """Full-library-scan source for Steam -- returns raw {"appid", "name",
        "playtime_forever", "playtime_2weeks", "rtime_last_played", ...}
        dicts for every game the account owns. Raises
        GameDetailsPrivateError if the response has no `games` key at all --
        Steam's separate "Game details" privacy toggle, independent of
        overall profile visibility."""
        data = await self._get(
            "IPlayerService/GetOwnedGames/v1/",
            {"steamid": steamid64, "include_appinfo": 1, "include_played_free_games": 1},
        )
        response = (data or {}).get("response")
        if not response or "games" not in response:
            raise GameDetailsPrivateError(f"Steam account {steamid64}'s game details are private")
        return response.get("games") or []

    async def async_get_schema_for_game(self, appid: int) -> dict:
        """Returns {"total_achievements": int, "display_names": {apiname: str}}.
        total_achievements is 0 (not an error) for a game with no
        achievements at all. Only total_achievements is used by Gaming
        Status today (achievement names/icons are out of scope -- earned/
        total counts only)."""
        data = await self._get("ISteamUserStats/GetSchemaForGame/v2/", {"appid": appid})
        game = (data or {}).get("game") or {}
        achievements = ((game.get("availableGameStats") or {}).get("achievements")) or []
        display_names = {entry["name"]: entry.get("displayName") or entry["name"] for entry in achievements if entry.get("name")}
        return {"total_achievements": len(achievements), "display_names": display_names}

    async def async_get_player_achievements(self, steamid64: str, appid: int) -> list[dict]:
        """Returns [{"apiname", "achieved": bool, "unlocktime": int}, ...].
        Games with no achievements at all return an empty list (not an
        error).

        This endpoint returns HTTP 401/403 for a specific steamid+appid
        combination even when the same API key just succeeded calling
        GetSchemaForGame for that same appid -- a real, long-documented
        Steam Web API limitation where achievement data is only reliably
        available for the API key's own account, regardless of privacy
        settings. Caught here (not left to bubble up as AuthError, whose
        message implies a broken key) and treated as "no data available for
        this account" instead.
        """
        try:
            data = await self._get("ISteamUserStats/GetPlayerAchievements/v1/", {"steamid": steamid64, "appid": appid})
        except AuthError:
            _LOGGER.debug(
                "Steam returned 401/403 for GetPlayerAchievements (steamid %s, appid %s) -- "
                "likely Steam's per-account achievement-data restriction (only reliably "
                "available for the API key's own account), not an invalid key.",
                steamid64, appid,
            )
            return []
        except NetworkError:
            # Steam returns a non-200 for "this game has no achievements
            # schema" as well as real failures -- treat as "no achievements"
            # rather than propagating a spurious error for the common case
            # of a game with no achievement support at all.
            return []

        playerstats = (data or {}).get("playerstats") or {}
        if not playerstats.get("success"):
            _LOGGER.debug(
                "Steam GetPlayerAchievements returned success=false for steamid %s appid %s: %s",
                steamid64, appid, playerstats.get("error") or "no error message given",
            )
            return []
        return playerstats.get("achievements") or []

    async def async_get_appdetails(self, appid: int) -> dict | None:
        """Steam's public, unauthenticated Store endpoint -- no API key
        needed, works for any appid regardless of whose key is configured
        (unlike the achievement endpoint above). Returns the rating-relevant
        slice of the response: {"ratings": {...}, "content_descriptors": {...},
        "required_age": int}, or None if the app has no store page / the
        lookup otherwise fails. Never raises -- a rating lookup failing
        should never be louder than "no native rating available"."""
        try:
            await self._rate_limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
            async with self._session.get(
                f"{STEAM_STORE_API_BASE}/appdetails", params={"appids": appid}, timeout=_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError, RateLimitedError):
            return None

        entry = (data or {}).get(str(appid)) or {}
        if not entry.get("success"):
            return None
        app_data = entry.get("data") or {}
        return {
            "ratings": app_data.get("ratings") or {},
            "content_descriptors": app_data.get("content_descriptors") or {},
            "required_age": app_data.get("required_age"),
        }
