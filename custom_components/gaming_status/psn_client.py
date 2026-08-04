"""PlayStation Network client for native achievement/trophy + rating
enrichment, covering both the currently-tracked game and the full-library
scan (library_scan.py). Includes a self-healing NPSSO->OAuth token
lifecycle, presence/title-concepts lookups, and the full trophyTitles
listing used as the library scan's primary source.

Auth is fundamentally different from Steam's static API key: the user
provides an NPSSO cookie (derived from a one-time browser login to their PSN
account, or reused from the official playstation_network integration's own
config entry -- see utils.py), which this client exchanges for a short-lived
access token (~60 min) and a refresh token confirmed (via live testing) to
last only ~10 days -- not the ~2 months some community docs claim. Calling
the refresh grant does NOT rotate to a new refresh token or extend its
lifetime -- it returns the exact same refresh_token value, with
refresh_token_expires_in just counting down from the original exchange.

`_async_ensure_session()` treats a failed refresh as recoverable, not fatal
-- it automatically re-derives a whole new session from the still-held NPSSO
(no user interaction needed) before giving up. Only when that also fails
(the NPSSO cookie itself has expired) does this raise ReauthRequiredError.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import parse_qsl, urlparse

import aiohttp

from .const import (
    PSN_AUTH_BASE,
    PSN_CATALOG_API_BASE,
    PSN_LEGACY_PROFILE_BASE,
    PSN_OAUTH_BASIC_AUTH_HEADER,
    PSN_OAUTH_CLIENT_ID,
    PSN_OAUTH_REDIRECT_URI,
    PSN_OAUTH_SCOPE,
    PSN_PRESENCE_API_BASE,
    PSN_PROFILE_BASE,
    PSN_TROPHY_API_BASE,
    RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS,
)
from .platform_exceptions import (
    AuthError,
    MalformedResponseError,
    NetworkError,
    NotFoundError,
    PsnTrophyListPrivateError,
    RateLimitedError,
    ReauthRequiredError,
)
from .rate_limiter import RateLimiter

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)

# A fresh NPSSO login always produces a redirect whose Location query string
# includes this specific error_code when the NPSSO itself is invalid/expired.
_NPSSO_EXPIRED_ERROR_CODE = "4165"

# Refresh proactively a little before the server-reported expiry rather than
# waiting for an actual 401.
_EXPIRY_SAFETY_MARGIN_SECONDS = 30

# Hard circuit breaker on the rare full trophyTitles fallback scan's
# pagination loop -- purely defensive, the loop's own termination logic
# should always fire first.
_MAX_TROPHY_TITLE_PAGES = 20


class PsnClient:
    def __init__(
        self, session: aiohttp.ClientSession, npsso: str, rate_limiter: RateLimiter
    ) -> None:
        self._session = session
        self._npsso = npsso
        self._rate_limiter = rate_limiter
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self._refresh_token: str | None = None
        self._refresh_token_expires_at: float = 0.0

    async def _request(self, method: str, url: str, **kwargs) -> tuple[int, dict]:
        await self._rate_limiter.async_acquire(
            timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS
        )
        try:
            async with self._session.request(
                method, url, timeout=_TIMEOUT, **kwargs
            ) as resp:
                if resp.status == 429:
                    retry_after_header = resp.headers.get("Retry-After")
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                    self._rate_limiter.notify_rate_limited(retry_after)
                    raise RateLimitedError(
                        f"PSN rate-limited {url}", retry_after=retry_after
                    )
                if resp.status >= 500:
                    raise NetworkError(f"PSN returned HTTP {resp.status} for {url}")
                try:
                    body = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}
                return resp.status, (body or {})
        except aiohttp.ClientError as err:
            raise NetworkError(f"Error communicating with PSN ({url}): {err}") from err
        except TimeoutError as err:
            raise NetworkError(f"Timed out reaching PSN ({url}): {err}") from err

    # ---- Auth / token lifecycle ----------------------------------------

    async def _async_get_authorization_code(self) -> str:
        params = {
            "access_type": "offline",
            "client_id": PSN_OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": PSN_OAUTH_SCOPE,
            "redirect_uri": PSN_OAUTH_REDIRECT_URI,
        }
        headers = {"Cookie": f"npsso={self._npsso}"}
        await self._rate_limiter.async_acquire(
            timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS
        )
        try:
            async with self._session.get(
                f"{PSN_AUTH_BASE}/authorize",
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                location = resp.headers.get("Location", "")
        except aiohttp.ClientError as err:
            raise NetworkError(
                f"Error communicating with PSN (authorize): {err}"
            ) from err
        except TimeoutError as err:
            raise NetworkError(f"Timed out reaching PSN (authorize): {err}") from err

        if not location:
            raise ReauthRequiredError(
                "PSN did not return a redirect -- the NPSSO cookie is likely invalid or expired"
            )

        query_dict = dict(parse_qsl(urlparse(location).query))
        if "error" in query_dict:
            if _NPSSO_EXPIRED_ERROR_CODE in (query_dict.get("error_code") or ""):
                raise ReauthRequiredError(
                    "PSN NPSSO cookie has expired or is invalid -- a fresh one is required"
                )
            raise AuthError(
                f"PSN rejected the authorization request: {query_dict.get('error')}"
            )

        code = query_dict.get("code")
        if not code:
            raise MalformedResponseError(f"No 'code' in PSN's redirect: {location}")
        return code

    async def _async_exchange_code_for_tokens(self, code: str) -> None:
        headers = {
            "Authorization": PSN_OAUTH_BASIC_AUTH_HEADER,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "code": code,
            "redirect_uri": PSN_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
            "token_format": "jwt",
        }
        status, body = await self._request(
            "POST", f"{PSN_AUTH_BASE}/token", headers=headers, data=data
        )
        if status != 200:
            raise AuthError(
                f"PSN rejected the authorization code exchange (HTTP {status})"
            )
        self._store_token_response(body)

    async def _async_refresh_access_token(self) -> None:
        headers = {
            "Authorization": PSN_OAUTH_BASIC_AUTH_HEADER,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
            "scope": PSN_OAUTH_SCOPE,
            "token_format": "jwt",
        }
        status, body = await self._request(
            "POST", f"{PSN_AUTH_BASE}/token", headers=headers, data=data
        )
        if status != 200:
            raise AuthError(f"PSN rejected the refresh_token grant (HTTP {status})")
        self._store_token_response(body)

    def _store_token_response(self, body: dict) -> None:
        now = time.time()
        access_token = body.get("access_token")
        refresh_token = body.get("refresh_token")
        if not access_token or not refresh_token:
            raise MalformedResponseError(
                f"Unexpected PSN token response shape: {body!r}"
            )
        self._access_token = access_token
        self._access_token_expires_at = now + float(body.get("expires_in", 0))
        self._refresh_token = refresh_token
        self._refresh_token_expires_at = now + float(
            body.get("refresh_token_expires_in", 0)
        )

    async def _async_ensure_session(self) -> None:
        now = time.time()
        if (
            self._access_token
            and now < self._access_token_expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS
        ):
            return

        if (
            self._refresh_token
            and now < self._refresh_token_expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS
        ):
            try:
                await self._async_refresh_access_token()
                return
            except AuthError:
                _LOGGER.debug(
                    "PSN refresh_token grant failed -- falling back to a fresh NPSSO-based session"
                )

        code = await self._async_get_authorization_code()
        await self._async_exchange_code_for_tokens(code)

    async def _authenticated_request(
        self, method: str, url: str, **kwargs
    ) -> tuple[int, dict]:
        await self._async_ensure_session()
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self._access_token}"
        return await self._request(method, url, headers=headers, **kwargs)

    # ---- Public API ------------------------------------------------------

    async def async_get_presence(self, account_id: str) -> dict | None:
        """Currently-playing lookup -- a single, lightweight, non-paginated
        call, the same one Sony's own official playstation_network
        integration makes every ~30s for the account owner *and* every
        tracked friend. Returns the raw presence dict (callers read
        presence["basicPresence"]["gameTitleInfoList"][0]["npTitleId"]/
        ["titleName"]) or None if the account's presence isn't visible to
        the configured NPSSO's account (privacy settings) or the call
        otherwise fails -- never raises, since a failed presence lookup
        should just mean "no title info available this cycle", not an
        enrichment-breaking error."""
        try:
            status, body = await self._authenticated_request(
                "GET",
                f"{PSN_PRESENCE_API_BASE}/{account_id}/basicPresences",
                params={
                    "type": "primary",
                    "platforms": "PS4,PS5,MOBILE_APP,PSPC",
                    "withOwnGameTitleInfo": "true",
                },
            )
        except (NetworkError, RateLimitedError, ReauthRequiredError, AuthError):
            return None
        if status != 200:
            return None
        return body or None

    async def async_get_trophy_summary_for_title(
        self, account_id: str, title_id: str
    ) -> dict | None:
        """Targeted, non-paginated single-title trophy lookup -- confirmed
        live (psnawp_api's own trophy_titles.py, the library HA core's
        official integration depends on) to be a single request, not a
        full-library scan. Returns the matched trophyTitle dict (with
        npCommunicationId/earnedTrophies/definedTrophies) or None if the
        title isn't found for this account (e.g. never played) or the call
        fails."""
        try:
            status, body = await self._authenticated_request(
                "GET",
                f"{PSN_TROPHY_API_BASE}/users/{account_id}/titles/trophyTitles",
                params={"npTitleIds": f"{title_id},"},
            )
        except (NetworkError, RateLimitedError, ReauthRequiredError, AuthError):
            return None
        if status != 200:
            return None
        titles = (body.get("titles") or [{}])[0].get("trophyTitles") or []
        return titles[0] if titles else None

    async def async_get_title_concepts(self, title_id: str) -> dict | None:
        """Native rating source -- confirmed live via both a real recorded
        PSN API response (psnawp_api's own test fixtures) and a live test in
        this integration's own development to include a `contentRating`
        object ({"authority": "ESRB", "description": ...}) and a top-level
        `minimumAge`. Same authenticated session as everything else here.
        Returns None on any failure -- a rating lookup failing should never
        be louder than "no native rating".

        `country`/`language`/`age` must be either ALL present or ALL absent
        (confirmed live via a real HTTP 400 otherwise) -- but omitting all
        three isn't a real fix either: confirmed live that it returns an
        unlocalized, multi-region shape (`localizedMinimumAge.metadata`,
        no flat `contentRating` at all) instead of the single-region flat
        fields this method needs. `country="US"`/`language="en-US,en;q=0.9"`
        match psnawp_api's own real recorded request exactly; `age=99` is
        not the viewer's actual age -- it's just large enough to satisfy the
        age-gate so PSN returns the full (not age-restricted-branching)
        rating detail. Hardcoding US/English is consistent with the rest of
        this integration's board-agnostic-but-US-centric rating approach
        (Steam/Xbox's native ratings are similarly unlocalized)."""
        try:
            status, body = await self._authenticated_request(
                "GET",
                f"{PSN_CATALOG_API_BASE}/{title_id}/concepts",
                params={"age": 99, "country": "US", "language": "en-US,en;q=0.9"},
            )
        except (NetworkError, RateLimitedError, ReauthRequiredError, AuthError):
            return None
        if status != 200 or not isinstance(body, list) or not body:
            return None
        entry = body[0]

        # Confirmed live: the response has a `minimumAge` at BOTH the
        # top (concept) level and nested under `defaultProduct` -- and
        # they can genuinely disagree (a real "Everyone 10+" title showed
        # top-level minimumAge=0 but defaultProduct.minimumAge=10, the
        # correct one). The top-level field appears to not reliably track
        # the actual ESRB floor; defaultProduct's does, so prefer it.
        default_product = entry.get("defaultProduct") or {}
        content_rating = (
            default_product.get("contentRating") or entry.get("contentRating") or {}
        )
        minimum_age = default_product.get("minimumAge")
        if minimum_age is None:
            minimum_age = entry.get("minimumAge")
        return {"contentRating": content_rating, "minimumAge": minimum_age}

    async def async_resolve_online_id(
        self, online_id_or_account_id: str
    ) -> tuple[str, str]:
        """Returns (account_id, canonical_online_id) -- only used for the
        manual-NPSSO-override config path, where a per-player identifier
        might be typed as a username rather than the numeric account_id HA's
        own registry already resolves in the common (reused-credential)
        case."""
        candidate = online_id_or_account_id.strip()
        if candidate.isdigit():
            status, body = await self._authenticated_request(
                "GET", f"{PSN_PROFILE_BASE}/{candidate}/profiles"
            )
            if status in (400, 404):
                raise NotFoundError(
                    f"No PSN account found for account ID '{candidate}'"
                )
            if status != 200:
                raise AuthError(
                    f"PSN rejected the profile lookup for account ID '{candidate}' (HTTP {status})"
                )
            online_id = body.get("onlineId")
            if not online_id:
                raise NotFoundError(
                    f"No PSN account found for account ID '{candidate}'"
                )
            return candidate, online_id

        status, body = await self._authenticated_request(
            "GET",
            f"{PSN_LEGACY_PROFILE_BASE}/{candidate}/profile2",
            params={"fields": "accountId,onlineId,currentOnlineId"},
        )
        if status == 404:
            raise NotFoundError(f"No PSN account found for online ID '{candidate}'")
        if status != 200:
            raise AuthError(
                f"PSN rejected the profile lookup for '{candidate}' (HTTP {status})"
            )
        profile = body.get("profile") or {}
        account_id = profile.get("accountId")
        canonical_online_id = (
            profile.get("currentOnlineId") or profile.get("onlineId") or candidate
        )
        if not account_id:
            raise NotFoundError(f"No PSN account found for online ID '{candidate}'")
        return str(account_id), canonical_online_id

    async def async_get_trophy_titles(self, account_id: str) -> list[dict]:
        """The full trophyTitles list for an account -- the primary data
        source for the full-library scan (library_scan.py), and also a rare
        fallback for current-game enrichment when async_get_presence()
        doesn't yield a usable title_id even though a game was already
        detected by name via Gaming Status's existing platform tracking.
        Paginates defensively (max page size 800). Raises
        PsnTrophyListPrivateError on 403 -- PSN's visibility depends on the
        relationship between the NPSSO's own account and the target account
        (friendship/privacy settings), not a single flag."""
        titles: list[dict] = []
        offset = 0
        for _ in range(_MAX_TROPHY_TITLE_PAGES):
            status, body = await self._authenticated_request(
                "GET",
                f"{PSN_TROPHY_API_BASE}/users/{account_id}/trophyTitles",
                params={"limit": 800, "offset": offset},
            )
            if status == 403:
                raise PsnTrophyListPrivateError(
                    f"PSN account {account_id}'s trophy list isn't visible to the configured NPSSO account"
                )
            if status == 404:
                raise NotFoundError(
                    f"No PSN account found for account ID '{account_id}'"
                )
            if status != 200:
                raise AuthError(
                    f"PSN rejected the trophyTitles request (HTTP {status})"
                )

            page = body.get("trophyTitles") or []
            titles.extend(page)
            next_offset = body.get("nextOffset")
            if next_offset is None or next_offset <= offset or len(page) == 0:
                break
            offset = next_offset
        else:
            _LOGGER.warning(
                "PSN trophyTitles pagination hit the %s-page safety cap for account %s -- "
                "stopping early with %s titles collected so far.",
                _MAX_TROPHY_TITLE_PAGES,
                account_id,
                len(titles),
            )
        return titles

    async def async_get_title_trophies_with_progress(
        self, account_id: str, np_communication_id: str
    ) -> list[dict]:
        """Individual trophy detail (name/description/earned/earnedDateTime)
        for one title -- feeds the recent-unlocks list, distinct from
        async_get_trophy_summary_for_title's tier-count-only summary.
        `np_communication_id` comes from that summary call's own response --
        no extra ID-resolution request needed.

        Confirmed live (psnawp_api's own trophy.py + test fixtures) that this
        genuinely requires TWO requests, not one -- metadata (name/
        description) and earned/progress are separate endpoints, zipped
        client-side by trophyId. `trophyGroupId=all` is a valid pseudo-group
        returning every trophy across all groups in one page each, so this
        stays a small, bounded pair of calls per game, not a per-group scan.
        Returns [] on any failure (private profile, never-played title,
        etc.) -- never raises."""
        try:
            status, meta_body = await self._authenticated_request(
                "GET",
                f"{PSN_TROPHY_API_BASE}/npCommunicationIds/{np_communication_id}/trophyGroups/all/trophies",
                params={"npServiceName": "trophy"},
            )
            if status != 200:
                return []
            status, progress_body = await self._authenticated_request(
                "GET",
                f"{PSN_TROPHY_API_BASE}/users/{account_id}/npCommunicationIds/{np_communication_id}/trophyGroups/all/trophies",
                params={"npServiceName": "trophy"},
            )
            if status != 200:
                return []

            meta_by_id = {
                t.get("trophyId"): t for t in (meta_body.get("trophies") or [])
            }
            progress_by_id = {
                t.get("trophyId"): t for t in (progress_body.get("trophies") or [])
            }

            results = []
            for trophy_id, meta in meta_by_id.items():
                progress = progress_by_id.get(trophy_id) or {}
                results.append(
                    {
                        "trophy_id": trophy_id,
                        "name": meta.get("trophyName"),
                        "description": meta.get("trophyDetail"),
                        "type": meta.get("trophyType"),
                        "icon_url": meta.get("trophyIconUrl"),
                        "earned": bool(progress.get("earned")),
                        "earned_at": progress.get("earnedDateTime"),
                    }
                )
            return results
        except (NetworkError, RateLimitedError, ReauthRequiredError, AuthError):
            return []
