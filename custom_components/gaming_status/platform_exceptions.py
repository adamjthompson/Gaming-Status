"""Shared exception hierarchy for the native platform enrichment clients
(steam_client.py, psn_client.py). Ported/trimmed from the sibling "Trophy
Hub" integration's exceptions.py -- kept only what these current-game-only
clients actually raise; Trophy Hub's own full-library-specific exceptions
(e.g. ProfilePrivateError for a whole-profile privacy toggle) aren't needed
here since Gaming Status never touches a full game list.
"""
from __future__ import annotations


class ApiError(Exception):
    """Base class -- catch-all fallback for anything not more specific."""


class NetworkError(ApiError):
    """Wraps a transport-level failure (connection refused, timeout, etc.)."""


class AuthError(ApiError):
    """The credential itself is invalid/revoked -- distinct from a privacy
    setting blocking access to someone's data."""


class ReauthRequiredError(AuthError):
    """PSN specifically: the stored NPSSO cookie itself has expired or been
    revoked, so no amount of silently re-deriving a session from it can
    recover -- a brand new NPSSO (a fresh browser login) is required."""


class RateLimitedError(ApiError):
    """The platform itself returned a rate-limit response.

    `retry_after`, in seconds, is set only when the platform tells us
    explicitly (e.g. a Retry-After-style header) -- callers should prefer it
    over guessing a fixed cooldown when present.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NotFoundError(ApiError):
    """A submitted identifier (SteamID64, account_id, title_id, ...) doesn't
    resolve to anything."""


class PsnTrophyListPrivateError(ApiError):
    """PSN: the target account's trophy list isn't visible to the account
    that owns the configured NPSSO -- depends on the *pair* of accounts
    (friendship/privacy settings), not a single public/private toggle."""


class MalformedResponseError(ApiError):
    """The response didn't have the shape we expected -- usually signals an
    upstream API change, not a blip; never retried automatically."""
