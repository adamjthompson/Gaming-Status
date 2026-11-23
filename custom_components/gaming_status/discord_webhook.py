"""Direct Discord webhook client for the "Discord Webhook" notification type.

Posts (and, for the opt-in edit mode, edits) messages straight to a Discord
webhook URL, so that notification type needs no Discord bot and no HA
Discord integration. Only HTTP and URL handling lives here; the notifier
decides what to send.

A webhook URL carries its own secret token, so nothing here ever logs a URL
or a raw aiohttp exception (whose text can include the request URL).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS,
    WEBHOOK_MAX_RETRY_WAIT_SECONDS,
    WEBHOOK_TIMEOUT_SECONDS,
)
from .rate_limiter import RateLimiter, RateLimiterTimeout, parse_retry_after

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS)

REDACTED = "**REDACTED**"

# Only Discord's own webhook endpoint (incl. the PTB/Canary client hosts and
# the legacy discordapp.com domain) -- never an arbitrary host, so a stored
# URL can't be used to make Home Assistant call anything else.
WEBHOOK_URL_RE = re.compile(
    r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/(?:v\d+/)?"
    r"webhooks/(\d+)/([\w-]+)/?$"
)
# The same URL anywhere inside text, keeping everything but the token.
_WEBHOOK_TOKEN_IN_TEXT_RE = re.compile(
    r"(discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/)[\w-]+"
)

# Discord rejects webhook usernames containing these, or longer than 80.
_FORBIDDEN_USERNAME_PARTS = ("discord", "clyde")
MAX_USERNAME_LENGTH = 80


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of one webhook request.

    status is the HTTP status, or None when no response arrived (network
    error, timeout, or the local rate limiter's wait timing out)."""

    ok: bool
    status: int | None = None
    message_id: str | None = None

    @property
    def not_found(self) -> bool:
        return self.status == 404


def escape_masked_links(text: Any) -> Any:
    """Discord renders [label](https://...) in an embed's description as a
    link that shows only the label. Game and achievement names come from
    players, developers and the Agent, so a "game" named like that would
    turn a notification into a disguised link. Escaping the brackets (which
    Gaming Status's own text never uses) shows them exactly as before, just
    never as a link."""
    if not isinstance(text, str):
        return text
    return text.replace("[", "\\[").replace("]", "\\]")


def is_valid_webhook_url(url: Any) -> bool:
    return isinstance(url, str) and WEBHOOK_URL_RE.match(url.strip()) is not None


def webhook_id(url: str) -> str | None:
    """The webhook's numeric ID (not secret), for keying rate limiters."""
    match = WEBHOOK_URL_RE.match(url.strip()) if isinstance(url, str) else None
    return match.group(1) if match else None


def redact_webhook_url(text: str) -> str:
    """Replace the token part of any webhook URL inside text."""
    return _WEBHOOK_TOKEN_IN_TEXT_RE.sub(r"\1" + REDACTED, text)


def username_error(username: str) -> str | None:
    """Translation error key if Discord would reject this username."""
    if not username:
        return None
    lowered = username.lower()
    if len(username) > MAX_USERNAME_LENGTH or any(
        part in lowered for part in _FORBIDDEN_USERNAME_PARTS
    ):
        return "invalid_webhook_username"
    return None


def build_payload(
    embed: dict, username: str | None = None, avatar_url: str | None = None
) -> dict:
    """A webhook message carrying one embed.

    allowed_mentions with an empty parse list means nothing in a game title
    or player name can ever ping @everyone, a role or a user."""
    payload: dict[str, Any] = {
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if username:
        payload["username"] = username
    if avatar_url:
        payload["avatar_url"] = avatar_url
    return payload


async def _retry_after_seconds(resp: aiohttp.ClientResponse) -> float | None:
    """Discord gives retry_after in the JSON body; Retry-After as fallback."""
    try:
        body = await resp.json(content_type=None)
        value = body.get("retry_after") if isinstance(body, dict) else None
        if value is not None:
            return max(0.0, float(value))
    except (ValueError, TypeError, aiohttp.ClientError):
        pass
    return parse_retry_after(resp.headers.get("Retry-After"))


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None,
    payload: dict | None = None,
    params: dict | None = None,
    read_json: bool = False,
) -> tuple[int | None, Any]:
    """One request, retried once after a short 429. Returns (status, body);
    status is None when no response arrived."""
    for attempt in range(2):
        if limiter is not None:
            try:
                await limiter.async_acquire(timeout=RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS)
            except RateLimiterTimeout:
                _LOGGER.warning(
                    "Gaming Status: Discord webhook request skipped, "
                    "rate limit budget exhausted"
                )
                return None, None
        try:
            async with session.request(
                method,
                url,
                json=payload,
                params=params,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                if resp.status == 429:
                    retry_after = await _retry_after_seconds(resp)
                    if limiter is not None:
                        limiter.notify_rate_limited(retry_after)
                    if (
                        attempt == 0
                        and retry_after is not None
                        and retry_after <= WEBHOOK_MAX_RETRY_WAIT_SECONDS
                    ):
                        # With a limiter, the next acquire() already waits
                        # out Discord's deadline.
                        if limiter is None:
                            await asyncio.sleep(retry_after)
                        continue
                    return resp.status, None
                body = None
                if read_json and 200 <= resp.status < 300:
                    try:
                        body = await resp.json(content_type=None)
                    except (ValueError, aiohttp.ClientError):
                        body = None
                return resp.status, body
        except (OSError, TimeoutError, aiohttp.ClientError) as err:
            # Only the exception type: its text can contain the webhook URL.
            _LOGGER.warning(
                "Gaming Status: Discord webhook request failed (%s)",
                type(err).__name__,
            )
            return None, None
    return 429, None


async def post_message(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict,
    *,
    wait: bool = False,
    limiter: RateLimiter | None = None,
) -> WebhookResult:
    """Post a new message. With wait=True Discord returns the created
    message, so the result carries its ID (needed to edit it later)."""
    status, body = await _request(
        session,
        "POST",
        url.strip(),
        limiter=limiter,
        payload=payload,
        params={"wait": "true"} if wait else None,
        read_json=wait,
    )
    if status is None or not 200 <= status < 300:
        return WebhookResult(False, status)
    message_id = None
    if wait and isinstance(body, dict) and body.get("id"):
        message_id = str(body["id"])
    return WebhookResult(True, status, message_id)


async def edit_message(
    session: aiohttp.ClientSession,
    url: str,
    message_id: str,
    payload: dict,
    *,
    limiter: RateLimiter | None = None,
) -> WebhookResult:
    """Edit a message this webhook posted earlier. A 404 (not_found) means
    it was deleted in Discord (or the webhook itself was)."""
    message_id = str(message_id)
    if not message_id.isdigit():
        return WebhookResult(False, 404)
    # The sender name and avatar are fixed when a message is posted; edits
    # only carry the content.
    edit_payload = {
        key: value
        for key, value in payload.items()
        if key not in ("username", "avatar_url")
    }
    status, _ = await _request(
        session,
        "PATCH",
        f"{url.strip().rstrip('/')}/messages/{message_id}",
        limiter=limiter,
        payload=edit_payload,
    )
    if status is None or not 200 <= status < 300:
        return WebhookResult(False, status)
    return WebhookResult(True, status, message_id)


async def validate_webhook(session: aiohttp.ClientSession, url: str) -> str | None:
    """Check a webhook URL works before it's saved. Returns a translation
    error key, or None when Discord accepts it."""
    if not is_valid_webhook_url(url):
        return "invalid_webhook_url"
    status, _ = await _request(session, "GET", url.strip(), limiter=None)
    if status is not None and 200 <= status < 300:
        return None
    if status in (401, 403, 404):
        return "webhook_not_found"
    return "cannot_connect"
