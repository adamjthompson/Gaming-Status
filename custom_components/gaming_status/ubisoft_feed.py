"""Ubisoft achievements and library, pushed by Gaming Status Agent over MQTT.

Ubisoft keeps achievement data only on the gaming PC, so the Agent reads it
from Ubisoft Connect's local cache and publishes it to two retained topics
under a base it advertises in its sensor's "Ubisoft Topic" attribute:

  <base>/library                       JSON: every game with achievements
  <base>/icon/<game id>/<achievement>  raw PNG: icons of recent unlocks

This module subscribes once per base (shared by every sensor/coordinator that
needs it), validates what arrives -- the broker is local, but anything that
can publish to it could send junk -- keeps the latest library in memory,
writes icons into the integration's local image cache, and fires a
dispatcher signal so the Agent sensor and the library scan update at once.
There is no polling: the Agent republishes whenever an achievement unlocks.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SIGNAL_UBISOFT_UPDATED = f"{DOMAIN}_ubisoft_updated"

# The Agent's own topic shape; anything else (e.g. a "#" wildcard) is refused
# so a bad attribute can't subscribe the integration to arbitrary topics.
_BASE_RE = re.compile(r"^gaming_status_agent/[a-z0-9_]{1,64}/ubisoft$")
_ICON_TOPIC_RE = re.compile(r"/icon/(\d{1,10})/(\d{1,12})$")

MAX_LIBRARY_BYTES = 512 * 1024
MAX_ICON_BYTES = 256 * 1024
# The whole icon store (shared by every Agent). Once it holds this many
# files or bytes, new icons are refused, so a flood of icon messages can't
# fill the disk. Far above any real library (icons are a few KB each);
# icons already stored are never deleted and can still be updated.
MAX_ICON_STORE_FILES = 50_000
MAX_ICON_STORE_BYTES = 512 * 1024 * 1024
_ICON_USAGE_KEY = "gaming_status_ubisoft_icon_usage"
MAX_GAMES = 2000
MAX_RECENT = 20
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

ICON_SUBDIR = "ubisoft"
_FEEDS_KEY = "ubisoft_feeds"


def signal_for(base: str) -> str:
    return f"{SIGNAL_UBISOFT_UPDATED}_{base}"


def valid_base(base: Any) -> bool:
    return isinstance(base, str) and bool(_BASE_RE.match(base))


def icon_url(game_id: int, ach_id: int) -> str:
    """Same relative /local URL scheme as cached cover art (see
    utils.fetch_and_cache_image), so the public page and cards treat it the
    same way."""
    return f"/local/gaming_status_cache/{ICON_SUBDIR}/{int(game_id)}/{int(ach_id)}.png"


def _int(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("bool is not an int")
    number = int(value)
    if number < 0 or number > 10**12:
        raise ValueError("out of range")
    return number


def _opt_int(value: Any) -> int | None:
    return None if value is None else _int(value)


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def parse_library(payload: Any) -> dict | None:
    """Validate and normalise a library payload. Returns None if it isn't
    usable; individual malformed games or unlocks are dropped, not fatal."""
    if isinstance(payload, bytes | bytearray):
        if len(payload) > MAX_LIBRARY_BYTES:
            return None
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        if len(payload) > MAX_LIBRARY_BYTES:
            return None
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
        return None

    games = []
    for raw in payload["games"][:MAX_GAMES]:
        if not isinstance(raw, dict):
            continue
        try:
            game_id = _int(raw.get("id"))
            variants = sorted(
                {_int(v) for v in (raw.get("variants") or [])[:50]} | {game_id}
            )
            earned = _int(raw.get("achievements_earned") or 0)
            total = _opt_int(raw.get("achievements_total"))
        except (TypeError, ValueError):
            continue
        recent = []
        for unlock in (raw.get("recent") or [])[:MAX_RECENT]:
            if not isinstance(unlock, dict):
                continue
            try:
                ach_id = _int(unlock.get("id"))
            except (TypeError, ValueError):
                continue
            recent.append(
                {
                    "id": ach_id,
                    "name": _text(unlock.get("name"), 255) or f"Achievement {ach_id}",
                    "description": _text(unlock.get("description"), 500) or None,
                    "unlocked_at": _text(unlock.get("unlocked_at"), 40) or None,
                    "icon_url": icon_url(game_id, ach_id),
                }
            )
        games.append(
            {
                "id": game_id,
                "title": _text(raw.get("title"), 255) or f"Ubisoft game {game_id}",
                "variants": variants,
                "owned": bool(raw.get("owned")),
                "achievements_earned": earned,
                "achievements_total": total,
                "last_unlock": _text(raw.get("last_unlock"), 40) or None,
                "recent": recent,
            }
        )
    try:
        owned_count = _int(payload.get("owned_game_count") or 0)
    except (TypeError, ValueError):
        owned_count = 0
    return {"owned_game_count": owned_count, "games": games}


def recent_unlocks_for(game: dict) -> list[dict]:
    """A library game's recent unlocks in the shared recent-unlocks shape the
    other platforms use ({name, description, unlocked_at, icon_url})."""
    return [
        {
            "name": u["name"],
            "description": u["description"],
            "unlocked_at": u["unlocked_at"],
            "icon_url": u["icon_url"],
        }
        for u in game.get("recent") or []
    ]


def find_game(library: dict | None, game_id: Any) -> dict | None:
    """The library game a running Ubisoft game ID belongs to, matching any of
    its store copies."""
    if not library or game_id in (None, ""):
        return None
    try:
        wanted = int(str(game_id).strip())
    except ValueError:
        return None
    for game in library.get("games") or []:
        if wanted in game.get("variants", ()):
            return game
    return None


class UbisoftFeed:
    """Subscriptions and latest data for one Agent's topic base."""

    def __init__(self, hass: HomeAssistant, base: str) -> None:
        self.hass = hass
        self.base = base
        self.library: dict | None = None
        self.updated_at = None
        self.icons_written = 0
        self.rejected = 0
        self._warned_store_full = False
        self._unsubs: list = []
        self._icon_dir = Path(
            hass.config.path("www", "gaming_status_cache", ICON_SUBDIR)
        )

    async def async_start(self) -> bool:
        try:
            from homeassistant.components import mqtt
        except ImportError:
            return False
        try:
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                _LOGGER.debug("Gaming Status: MQTT isn't set up; no Ubisoft data")
                return False
            self._unsubs.append(
                await mqtt.async_subscribe(
                    self.hass, f"{self.base}/library", self._on_library, qos=1
                )
            )
            self._unsubs.append(
                await mqtt.async_subscribe(
                    self.hass,
                    f"{self.base}/icon/+/+",
                    self._on_icon,
                    qos=1,
                    encoding=None,
                )
            )
        except Exception as err:  # MQTT problems must not break setup
            _LOGGER.warning(
                "Gaming Status: couldn't subscribe to Ubisoft data at %s: %s",
                self.base,
                err,
            )
            return False
        return True

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._unsubs.clear()

    @callback
    def _on_library(self, msg) -> None:
        library = parse_library(msg.payload)
        if library is None:
            self.rejected += 1
            _LOGGER.debug("Gaming Status: ignored an invalid Ubisoft library message")
            return
        self.library = library
        self.updated_at = dt_util.utcnow()
        async_dispatcher_send(self.hass, signal_for(self.base))

    @callback
    def _on_icon(self, msg) -> None:
        match = _ICON_TOPIC_RE.search(msg.topic or "")
        data = msg.payload
        if (
            not match
            or not isinstance(data, bytes | bytearray)
            or len(data) > MAX_ICON_BYTES
            or not bytes(data).startswith(_PNG_SIGNATURE)
        ):
            self.rejected += 1
            return
        game_id, ach_id = int(match.group(1)), int(match.group(2))
        self.hass.async_create_background_task(
            self._async_write_icon(game_id, ach_id, bytes(data)),
            f"{DOMAIN}_ubisoft_icon_{game_id}_{ach_id}",
        )

    async def _async_icon_usage(self) -> list[int]:
        """[file count, bytes] of the whole icon store: counted from disk
        once, then kept up to date as icons are written."""
        usage = self.hass.data.get(_ICON_USAGE_KEY)
        if usage is None:
            icon_dir = self._icon_dir

            def _count() -> list[int]:
                files = size = 0
                if icon_dir.is_dir():
                    for icon in icon_dir.rglob("*.png"):
                        with contextlib.suppress(OSError):
                            size += icon.stat().st_size
                            files += 1
                return [files, size]

            counted = await self.hass.async_add_executor_job(_count)
            usage = self.hass.data.setdefault(_ICON_USAGE_KEY, counted)
        return usage

    async def _async_write_icon(self, game_id: int, ach_id: int, data: bytes) -> None:
        # Both parts are plain integers, so the path can't leave the cache.
        path = self._icon_dir / str(game_id) / f"{ach_id}.png"
        usage = await self._async_icon_usage()
        files_now, bytes_now = usage

        def _write() -> tuple[int, int] | None:
            """(files added, bytes added) -- (0, 0) if the icon was already
            there -- or None if it's a new icon and the store is full."""
            old_size = path.stat().st_size if path.exists() else None
            if old_size == len(data):
                return 0, 0
            if old_size is None and (
                files_now >= MAX_ICON_STORE_FILES
                or bytes_now + len(data) > MAX_ICON_STORE_BYTES
            ):
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return (0 if old_size is not None else 1), len(data) - (old_size or 0)

        try:
            added = await self.hass.async_add_executor_job(_write)
        except OSError as err:
            _LOGGER.debug("Gaming Status: couldn't cache Ubisoft icon: %s", err)
            return
        if added is None:
            self.rejected += 1
            if not self._warned_store_full:
                self._warned_store_full = True
                _LOGGER.warning(
                    "Gaming Status: the Ubisoft achievement icon store is full "
                    "(%s files, %s MB); new icons won't be saved",
                    files_now,
                    bytes_now // (1024 * 1024),
                )
            return
        if added != (0, 0):
            usage[0] += added[0]
            usage[1] += added[1]
            self.icons_written += 1

    def status(self) -> dict:
        """For diagnostics -- no account details."""
        return {
            "subscribed": bool(self._unsubs),
            "last_update": self.updated_at.isoformat() if self.updated_at else None,
            "game_count": len((self.library or {}).get("games") or []),
            "icons_written_this_run": self.icons_written,
            "rejected_messages": self.rejected,
        }


def get_feed(hass: HomeAssistant, base: Any) -> UbisoftFeed | None:
    if not valid_base(base):
        return None
    return hass.data.get(DOMAIN, {}).get(_FEEDS_KEY, {}).get(base)


async def async_ensure(hass: HomeAssistant, base: Any) -> UbisoftFeed | None:
    """The feed for a topic base, subscribing on first use. None for an
    invalid base or when MQTT isn't available."""
    if not valid_base(base):
        return None
    feeds = hass.data.setdefault(DOMAIN, {}).setdefault(_FEEDS_KEY, {})
    feed = feeds.get(base)
    if feed is not None:
        return feed
    feed = UbisoftFeed(hass, base)
    # Registered before subscribing, so two sensors asking at once share it.
    feeds[base] = feed
    if not await feed.async_start():
        feeds.pop(base, None)
        return None
    return feed


@callback
def async_stop_all(hass: HomeAssistant) -> None:
    """Unsubscribe every feed (config entry unload)."""
    for feed in hass.data.get(DOMAIN, {}).pop(_FEEDS_KEY, {}).values():
        feed.async_stop()


def all_status(hass: HomeAssistant) -> dict:
    return {
        base: feed.status()
        for base, feed in hass.data.get(DOMAIN, {}).get(_FEEDS_KEY, {}).items()
    }
