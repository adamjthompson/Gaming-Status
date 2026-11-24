"""GOG public-profile client, covering both the currently-tracked game (a GOG
game reported by Gaming Status Agent) and the full-library scan
(library_scan.py).

No sign-in: everything here reads the same unauthenticated pages gog.com uses
to render a public player profile, so it only works for players whose profile
and games are set to public. The endpoints are undocumented and could change
without notice; every caller treats a failure as "no GOG data" rather than an
error worth surfacing loudly.

Three reads cover everything:
  * the profile's games list (JSON, paged) -- every owned game, with
    playtime, last session and achievement percentage for played ones;
  * a game's profile page (HTML) -- its full achievement list with this
    player's unlock state and unlock times, embedded in the page as JSON;
  * the profile's own page (HTML) -- the numeric user id and the privacy
    settings, used to validate a newly entered username.

GOG only records playtime and achievements for games run through GOG Galaxy,
so a game launched straight from its own shortcut has no stats at all.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import GOG_PROFILE_BASE, RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS
from .platform_exceptions import (
    GogProfilePrivateError,
    MalformedResponseError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
)
from .rate_limiter import RateLimiter, parse_retry_after

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Profile pages are ~0.5 MB; anything far past that isn't a profile page.
_MAX_PAGE_BYTES = 4 * 1024 * 1024
# The games list is 50 per page; 40 pages is a 2,000-game library.
_MAX_LIBRARY_PAGES = 40

# GOG usernames are letters, digits, "_" and "-" ("." is tolerated, but not
# first, so "." / ".." can never become a path segment). Validated before a
# name is ever put in a URL.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{1,63}$")

_PRIVATE_STATUSES = (401, 403)

_DECODER = json.JSONDecoder()
_MISSING = object()


def valid_username(username: str) -> bool:
    return bool(username) and bool(USERNAME_RE.match(username))


def _profile_url(username: str, *path: str) -> str:
    if not valid_username(username):
        raise NotFoundError("Not a valid GOG username")
    parts = [GOG_PROFILE_BASE, quote(username, safe="")]
    parts.extend(quote(str(p), safe="") for p in path)
    return "/".join(parts)


def _page_value(html: str, name: str) -> Any:
    """The JSON value a profile page assigns to window.profilesData.<name>,
    or _MISSING when the page doesn't assign it. Parsed as JSON only (never
    evaluated), starting right after the "=" and stopping where the value
    ends."""
    match = re.search(r"window\.profilesData\." + re.escape(name) + r"\s*=\s*", html)
    if not match:
        return _MISSING
    try:
        value, _end = _DECODER.raw_decode(html, match.end())
    except ValueError as err:
        raise MalformedResponseError(
            f"GOG profile page's {name} data isn't valid JSON"
        ) from err
    return value


def _own_stats(stats: Any, user_id: str | None) -> dict | None:
    """A game's or achievement's "stats" field is keyed by user id, and is
    an empty list (not an object) when there's nothing recorded."""
    if not isinstance(stats, dict) or not stats:
        return None
    value = stats.get(user_id) if user_id and user_id in stats else None
    if value is None:
        value = next(iter(stats.values()))
    return value if isinstance(value, dict) and value else None


def _epoch_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class GogClient:
    def __init__(
        self, session: aiohttp.ClientSession, rate_limiter: RateLimiter
    ) -> None:
        self._session = session
        self._rate_limiter = rate_limiter
        # lowercased username -> (fetched_monotonic, library)
        self._library_cache: dict[str, tuple[float, dict]] = {}

    async def _get(self, url: str, what: str, *, as_json: bool) -> Any:
        """GET one profile URL; return decoded JSON or the page's text."""
        await self._rate_limiter.async_acquire(
            timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS
        )
        try:
            async with self._session.get(
                url,
                timeout=_TIMEOUT,
                headers={"Accept": "application/json" if as_json else "text/html"},
            ) as resp:
                if resp.status == 429:
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    self._rate_limiter.notify_rate_limited(retry_after)
                    raise RateLimitedError(
                        f"GOG rate-limited {what}", retry_after=retry_after
                    )
                if resp.status == 404:
                    raise NotFoundError(f"GOG profile not found ({what})")
                if resp.status in _PRIVATE_STATUSES:
                    raise GogProfilePrivateError(f"GOG profile is private ({what})")
                if resp.status != 200:
                    raise NetworkError(f"GOG returned HTTP {resp.status} for {what}")
                # Read to the end, but stop early on anything far bigger
                # than a profile page.
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > _MAX_PAGE_BYTES:
                        raise MalformedResponseError(
                            f"GOG {what} response is unexpectedly large"
                        )
                    chunks.append(chunk)
        except aiohttp.ClientError as err:
            raise NetworkError(f"Error communicating with GOG ({what}): {err}") from err
        except TimeoutError as err:
            raise NetworkError(f"Timed out talking to GOG ({what})") from err
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if not as_json:
            return text
        try:
            return json.loads(text)
        except ValueError as err:
            raise MalformedResponseError(
                f"Unexpected response body from GOG {what}"
            ) from err

    async def async_get_profile(self, username: str) -> dict:
        """Return {"username", "user_id", "games_public"} for a profile.

        games_public is False when the player has hidden their games list,
        which is what everything else here reads."""
        html = await self._get(_profile_url(username), "profile", as_json=False)
        user = _page_value(html, "profileUser")
        if not isinstance(user, dict) or not user.get("userId"):
            raise MalformedResponseError("GOG profile page shape changed")
        prefs = _page_value(html, "profileUserPreferences")
        privacy = (prefs or {}).get("privacy") if isinstance(prefs, dict) else None
        games_privacy = (
            (privacy or {}).get("games") if isinstance(privacy, dict) else None
        )
        return {
            "username": str(user.get("username") or username),
            "user_id": str(user["userId"]),
            # Unknown counts as public: the games list itself is the real test.
            "games_public": games_privacy in (None, "public"),
        }

    async def async_get_library(
        self, username: str, max_age: float | None = None
    ) -> dict:
        """Return {"owned_count", "games": [...]} -- every owned game.

        Each game: {"id", "title", "image", "achievement_support", "played",
        "percent", "playtime_minutes", "last_session"}. played is False (and
        the stats fields None) for games GOG has no play record for.

        max_age reuses a read at most that many seconds old, for the
        current-game lookup; the library scan always reads afresh.
        """
        key = username.lower()
        if max_age is not None:
            cached = self._library_cache.get(key)
            if cached and time.monotonic() - cached[0] < max_age:
                return cached[1]

        games: list[dict] = []
        owned_count = None
        page, pages = 1, 1
        while page <= pages and page <= _MAX_LIBRARY_PAGES:
            body = await self._get(
                _profile_url(username, "games", "stats")
                + f"?sort=recent_playtime&order=desc&page={page}",
                "games list",
                as_json=True,
            )
            if not isinstance(body, dict):
                raise MalformedResponseError("GOG games list returned a non-object")
            items = (body.get("_embedded") or {}).get("items")
            if not isinstance(items, list):
                raise MalformedResponseError("GOG games list shape changed")
            if page == 1:
                pages = _int_or_none(body.get("pages")) or 1
                owned_count = _int_or_none(body.get("total"))
            for item in items:
                game = (item or {}).get("game") if isinstance(item, dict) else None
                if not isinstance(game, dict) or not game.get("id"):
                    continue
                stats = _own_stats(item.get("stats"), None)
                games.append(
                    {
                        "id": str(game["id"]),
                        "title": str(game.get("title") or ""),
                        "image": game.get("image"),
                        "achievement_support": bool(game.get("achievementSupport")),
                        "played": stats is not None,
                        "percent": _int_or_none(
                            (stats or {}).get("achievementsPercentage")
                        ),
                        "playtime_minutes": _int_or_none((stats or {}).get("playtime")),
                        "last_session": (stats or {}).get("lastSession"),
                    }
                )
            page += 1

        result = {
            "owned_count": owned_count if owned_count is not None else len(games),
            "games": games,
        }
        self._library_cache[key] = (time.monotonic(), result)
        return result

    async def async_get_game_achievements(self, username: str, game_id: str) -> dict:
        """Return one game's achievements for this player:
        {"earned", "total", "achievements": [{"name", "description",
        "icon_url", "unlocked", "unlocked_at"}]}. unlocked_at is a UTC ISO
        string or None. A game without GOG achievements has total 0."""
        html = await self._get(
            _profile_url(username, "game", str(game_id)),
            "game achievements",
            as_json=False,
        )
        user = _page_value(html, "profileUser")
        user_id = (
            str(user["userId"])
            if isinstance(user, dict) and user.get("userId")
            else None
        )
        raw = _page_value(html, "achievements")
        if raw is _MISSING or not isinstance(raw, list):
            raise MalformedResponseError("GOG game page shape changed")
        achievements = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            ach = item.get("achievement")
            if not isinstance(ach, dict) or not ach.get("name"):
                continue
            stats = _own_stats(item.get("stats"), user_id) or {}
            unlocked = bool(stats.get("isUnlocked"))
            achievements.append(
                {
                    "name": str(ach["name"]),
                    "description": str(ach.get("description") or "") or None,
                    "icon_url": ach.get("imageUrlUnlocked")
                    or ach.get("imageUrlLocked"),
                    "unlocked": unlocked,
                    "unlocked_at": _epoch_iso(stats.get("unlockDate"))
                    if unlocked
                    else None,
                }
            )
        return {
            "earned": sum(1 for a in achievements if a["unlocked"]),
            "total": len(achievements),
            "achievements": achievements,
        }
