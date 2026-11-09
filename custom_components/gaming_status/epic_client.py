"""Epic Games public-profile achievement client, covering both the
currently-tracked game (an Epic game reported by Gaming Status Agent) and the
full-library scan (library_scan.py).

No sign-in: everything here reads the same unauthenticated GraphQL the Epic
Games Store website uses to render a public player profile, so it only works
for players whose profile (and achievements) are set to public. The endpoint
is undocumented and could change without notice; every caller treats a
failure as "no Epic data" rather than an error worth surfacing loudly.

Three queries cover everything:
  * the profile's achievement summaries -- every game with progress, with
    unlocked/total counts and XP (one request, used for matching the running
    game and for the whole library scan);
  * a game's achievement definitions by sandboxId -- display names,
    descriptions, icons, plus the productId the per-player query needs;
  * the player's per-achievement unlock state for one productId.

Only games that use Epic's own achievement system have any data; many Epic
titles (e.g. Marvel's Guardians of the Galaxy) have none at all.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from .const import (
    EPIC_DEFINITIONS_CACHE_SECONDS,
    EPIC_GRAPHQL_URL,
    EPIC_LOCALE,
    RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS,
)
from .platform_exceptions import (
    EpicProfilePrivateError,
    MalformedResponseError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
)
from .rate_limiter import RateLimiter, parse_retry_after

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)

_Q_PROFILE = """query GamingStatusEpicProfile($id: String!, $locale: String!) {
  PlayerProfile { playerProfile(epicAccountId: $id) {
    epicAccountId
    displayName
    achievementsSummaries {
      ... on PlayerAchievementResponseSuccess { data {
        sandboxId totalUnlocked totalXP
        product(locale: $locale) { name slug }
        productAchievements(locale: $locale) { totalAchievements totalProductXP }
      } }
      ... on ServiceError { status message }
    }
  } } }"""

_Q_DEFINITIONS = """query GamingStatusEpicDefinitions($sandboxId: String!, $locale: String!) {
  Achievement { productAchievementsRecordBySandbox(sandboxId: $sandboxId, locale: $locale) {
    productId sandboxId totalAchievements totalProductXP
    achievements { achievement {
      name hidden XP unlockedDisplayName unlockedDescription unlockedIconLink
    } }
  } } }"""

_Q_PLAYER_UNLOCKS = """query GamingStatusEpicUnlocks($id: String!, $productId: String!) {
  PlayerProfile { playerProfile(epicAccountId: $id) {
    productAchievements(productId: $productId) {
      ... on PlayerProductAchievementsResponseSuccess { data {
        sandboxId totalUnlocked totalXP
        playerAchievements { playerAchievement {
          achievementName unlocked unlockDate
        } }
      } }
      ... on ServiceError { status message }
    }
  } } }"""

# Status codes Epic's profile service uses for "not visible to an anonymous
# viewer" vs "no such account".
_PRIVATE_STATUSES = (401, 403)
_NOT_FOUND_STATUSES = (400, 404)


def _status_of(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class EpicClient:
    def __init__(
        self, session: aiohttp.ClientSession, rate_limiter: RateLimiter
    ) -> None:
        self._session = session
        self._rate_limiter = rate_limiter
        # sandboxId -> (fetched_monotonic, definitions-or-None). None is
        # cached too: "this game has no Epic achievements" is just as stable.
        self._definitions_cache: dict[str, tuple[float, dict | None]] = {}

    async def _post(self, query: str, variables: dict, what: str) -> dict:
        """POST one GraphQL query; return the full decoded body.

        GraphQL-level errors are returned to the caller (they carry the
        per-field status codes it needs), only transport/HTTP problems raise.
        """
        await self._rate_limiter.async_acquire(
            timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS
        )
        try:
            async with self._session.post(
                EPIC_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 429:
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    self._rate_limiter.notify_rate_limited(retry_after)
                    raise RateLimitedError(
                        f"Epic rate-limited {what}", retry_after=retry_after
                    )
                if resp.status != 200:
                    raise NetworkError(f"Epic returned HTTP {resp.status} for {what}")
                try:
                    body = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise MalformedResponseError(
                        f"Unexpected response body from Epic {what}: {err}"
                    ) from err
        except aiohttp.ClientError as err:
            raise NetworkError(
                f"Error communicating with Epic ({what}): {err}"
            ) from err
        except TimeoutError as err:
            raise NetworkError(f"Timed out talking to Epic ({what})") from err
        if not isinstance(body, dict):
            raise MalformedResponseError(f"Epic {what} returned a non-object body")
        return body

    @staticmethod
    def _raise_for_profile_errors(body: dict, what: str) -> None:
        """Map top-level GraphQL errors on the playerProfile field to our
        exception types. Unknown/other errors are malformed responses."""
        for err in body.get("errors") or []:
            if not isinstance(err, dict):
                continue
            status = _status_of(err.get("status"))
            if status in _NOT_FOUND_STATUSES:
                raise NotFoundError(f"Epic account not found ({what})")
            if status in _PRIVATE_STATUSES:
                raise EpicProfilePrivateError(f"Epic profile is private ({what})")
            if status == 429:
                raise RateLimitedError(f"Epic rate-limited {what}")
            raise MalformedResponseError(
                f"Epic {what} error: {str(err.get('message'))[:200]}"
            )

    async def async_get_profile(self, account_id: str) -> dict:
        """Return {"display_name", "games": [...]} for a public profile.

        Each game: {"sandbox_id", "title", "slug", "earned", "total",
        "xp_earned", "xp_total"}. Only games with Epic achievement progress
        are listed -- a game whose first achievement hasn't been unlocked
        yet isn't on the profile at all.
        """
        body = await self._post(
            _Q_PROFILE, {"id": account_id, "locale": EPIC_LOCALE}, "profile"
        )
        self._raise_for_profile_errors(body, "profile")
        try:
            profile = body["data"]["PlayerProfile"]["playerProfile"]
        except (KeyError, TypeError) as err:
            raise MalformedResponseError("Epic profile response shape changed") from err
        if not isinstance(profile, dict):
            raise NotFoundError("Epic account not found (profile)")
        summaries = profile.get("achievementsSummaries") or {}
        if "data" not in summaries:
            status = _status_of(summaries.get("status"))
            if status in _NOT_FOUND_STATUSES:
                # No achievement records at all yet -- a valid, empty profile.
                return {"display_name": profile.get("displayName"), "games": []}
            if status in _PRIVATE_STATUSES or status is None:
                raise EpicProfilePrivateError("Epic profile achievements are private")
            raise MalformedResponseError(
                f"Epic achievement summaries error: status {status}"
            )
        games = []
        for item in summaries.get("data") or []:
            if not isinstance(item, dict) or not item.get("sandboxId"):
                continue
            product = item.get("product") or {}
            totals = item.get("productAchievements") or {}
            games.append(
                {
                    "sandbox_id": item["sandboxId"],
                    "title": product.get("name") or "",
                    "slug": product.get("slug"),
                    "earned": item.get("totalUnlocked"),
                    "total": totals.get("totalAchievements"),
                    "xp_earned": item.get("totalXP"),
                    "xp_total": totals.get("totalProductXP"),
                }
            )
        return {"display_name": profile.get("displayName"), "games": games}

    async def async_get_definitions(self, sandbox_id: str) -> dict | None:
        """Return a game's achievement definitions, or None when the game has
        no Epic achievements.

        {"product_id", "sandbox_id", "total", "achievements": {name: {
            "title", "description", "icon_url", "xp", "hidden"}}}
        """
        cached = self._definitions_cache.get(sandbox_id)
        if cached and time.monotonic() - cached[0] < EPIC_DEFINITIONS_CACHE_SECONDS:
            return cached[1]
        body = await self._post(
            _Q_DEFINITIONS,
            {"sandboxId": sandbox_id, "locale": EPIC_LOCALE},
            "achievement definitions",
        )
        try:
            record = body["data"]["Achievement"]["productAchievementsRecordBySandbox"]
        except (KeyError, TypeError) as err:
            if body.get("errors"):
                raise MalformedResponseError(
                    "Epic achievement definitions query failed"
                ) from err
            raise MalformedResponseError(
                "Epic definitions response shape changed"
            ) from err
        result: dict | None = None
        if isinstance(record, dict) and record.get("productId"):
            achievements = {}
            for wrapper in record.get("achievements") or []:
                ach = (wrapper or {}).get("achievement") or {}
                name = ach.get("name")
                if not name:
                    continue
                achievements[name] = {
                    "title": ach.get("unlockedDisplayName") or name,
                    "description": ach.get("unlockedDescription") or "",
                    "icon_url": ach.get("unlockedIconLink"),
                    "xp": ach.get("XP"),
                    "hidden": bool(ach.get("hidden")),
                }
            result = {
                "product_id": record["productId"],
                "sandbox_id": record.get("sandboxId") or sandbox_id,
                "total": record.get("totalAchievements") or len(achievements),
                "xp_total": record.get("totalProductXP"),
                "achievements": achievements,
            }
        self._definitions_cache[sandbox_id] = (time.monotonic(), result)
        return result

    async def async_get_player_unlocks(
        self, account_id: str, product_id: str
    ) -> list[dict]:
        """Return the player's unlocked achievements for one product:
        [{"name", "unlocked_at"}] (unlocked_at is Epic's ISO string). An
        empty list when the player has no progress in that game."""
        body = await self._post(
            _Q_PLAYER_UNLOCKS,
            {"id": account_id, "productId": product_id},
            "player achievements",
        )
        self._raise_for_profile_errors(body, "player achievements")
        try:
            node = body["data"]["PlayerProfile"]["playerProfile"]["productAchievements"]
        except (KeyError, TypeError) as err:
            raise MalformedResponseError(
                "Epic player achievements response shape changed"
            ) from err
        if not isinstance(node, dict):
            return []
        if "data" not in node:
            status = _status_of(node.get("status"))
            if status in _NOT_FOUND_STATUSES:
                return []
            if status in _PRIVATE_STATUSES:
                raise EpicProfilePrivateError("Epic profile achievements are private")
            raise MalformedResponseError(
                f"Epic player achievements error: status {status}"
            )
        unlocks = []
        for wrapper in (node.get("data") or {}).get("playerAchievements") or []:
            ach = (wrapper or {}).get("playerAchievement") or {}
            if ach.get("unlocked") and ach.get("achievementName"):
                unlocks.append(
                    {
                        "name": ach["achievementName"],
                        "unlocked_at": ach.get("unlockDate"),
                    }
                )
        return unlocks
