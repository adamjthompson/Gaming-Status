"""Generic token-bucket rate limiter, shared across the native platform
enrichment clients.

One instance per platform lives in hass.data[DOMAIN]["rate_limiters"][platform]
(see utils.py), so every tracked player's enrichment fetch draws from the
same shared budget for that platform -- credentials are shared (reused from
the official steam_online/playstation_network integration, or one manual
override), not per-player, so the budget has to be too.
"""
from __future__ import annotations

import asyncio
import time

from .platform_exceptions import RateLimitedError


class RateLimiterTimeout(RateLimitedError):
    """Raised when acquire() times out waiting for budget to free up -- a
    RateLimitedError (not a bare Exception) so every existing
    `except (RateLimitedError, ..., ApiError)`/`except ApiError` handler
    already catches this correctly, with no changes needed there."""


class RateLimiter:
    def __init__(self, capacity: float, refill_rate_per_sec: float, *, name: str = "") -> None:
        self._capacity = capacity
        self._rate = refill_rate_per_sec
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()
        self._name = name
        # Set by notify_rate_limited() when the server itself says to back
        # off until an authoritative deadline, overriding our own math.
        self._blocked_until: float | None = None

    async def async_acquire(self, cost: float = 1.0, *, timeout: float | None = None) -> None:
        """Blocks (without holding the lock across the wait) until `cost`
        tokens are available, then deducts them."""
        start = time.monotonic()
        while True:
            async with self._lock:
                now = time.monotonic()
                if self._blocked_until is not None and now < self._blocked_until:
                    wait = self._blocked_until - now
                else:
                    if self._blocked_until is not None:
                        # The server-imposed block just lapsed -- resume
                        # normal refill accounting from this point on.
                        self._last = self._blocked_until
                        self._blocked_until = None
                    self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                    self._last = now
                    if self._tokens >= cost:
                        self._tokens -= cost
                        return
                    wait = (cost - self._tokens) / self._rate

            if timeout is not None and (time.monotonic() - start + wait) > timeout:
                raise RateLimiterTimeout(f"Rate limit budget exhausted for {self._name}")
            await asyncio.sleep(min(wait, 5.0))

    def notify_rate_limited(self, retry_after: float | None) -> None:
        """Called when the server itself returns a rate-limited response
        despite our local bucket thinking there was room -- zero out the
        local budget, and if the server gave us an authoritative retry-after,
        block every acquire() until then rather than trusting our own math."""
        self._tokens = 0.0
        if retry_after:
            self._blocked_until = time.monotonic() + retry_after
