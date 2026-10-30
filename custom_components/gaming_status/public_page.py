"""Public (no-login) status page for Gaming Status.

Renders the SAME custom cards as the Gaming Status Cards repository
(https://github.com/adamjthompson/Gaming-Status-Cards), so the public page
and the user's own dashboards share one display codebase. Two
unauthenticated routes are served from Home Assistant's web server:

  /gaming_status/public              -- a small HTML shell that loads the
                                        cards script and lays the cards out
  /api/gaming_status/public.json     -- the page layout plus a filtered
                                        snapshot of entity states

The cards only ever read ``hass.states``, so the shell hands each card a
read-only stand-in ``hass`` object built from that snapshot. The snapshot
contains ONLY:
  * sensor/binary_sensor entities on the devices of players who have
    "Show on public page" enabled (Player Details), and
  * synthesized hub entities (players online / anyone gaming) counted
    over those public players only, so private players can't be inferred.
Attributes that could expose credentials, account ids, parental-control
data or (optionally) gamertags are stripped, as are image URLs a
logged-out browser can't load anyway.

Page layout mirrors a Lovelace dashboard view chosen in the "Public Page"
options menu, so it's designed with the cards' own visual editors: every
Gaming Status card on that view is shown with its exact config (stacks and
sections stay grouped), other card types are skipped, cards pinned to a
non-public player are dropped, and the Game Management card is never
shown. With no view chosen, a built-in default layout is used, where
per-player cards are expanded once per public player and skipped for
players without the data they need.

aiohttp views can't be unregistered, so they're registered once per HA
run and look up the currently loaded entry on every request, returning
404 whenever the page is disabled or nobody is opted in.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_PUBLIC_PAGE_GENERAL_CARDS,
    DEFAULT_PUBLIC_PAGE_HIDE_ZERO_ACHIEVEMENTS,
    DEFAULT_PUBLIC_PAGE_PLAYER_CARDS,
    DEFAULT_PUBLIC_PAGE_THEME_MODE,
    DEFAULT_PUBLIC_PAGE_TITLE,
    DOMAIN,
    OPT_PLAYERS,
    OPT_PUBLIC_PAGE_CARDS_URL,
    OPT_PUBLIC_PAGE_ENABLED,
    OPT_PUBLIC_PAGE_GENERAL_CARDS,
    OPT_PUBLIC_PAGE_HIDE_ZERO_ACHIEVEMENTS,
    OPT_PUBLIC_PAGE_KEY,
    OPT_PUBLIC_PAGE_PLAYER_CARDS,
    OPT_PUBLIC_PAGE_REQUIRE_KEY,
    OPT_PUBLIC_PAGE_SHOW_GAMERTAGS,
    OPT_PUBLIC_PAGE_SOURCE_VIEW,
    OPT_PUBLIC_PAGE_THEME,
    OPT_PUBLIC_PAGE_THEME_MODE,
    OPT_PUBLIC_PAGE_TITLE,
    PUBLIC_PAGE_DEFAULT_LAYOUT,
    PUBLIC_PAGE_THEME_DEFAULT,
    PUBLIC_PAGE_THEME_MODES,
)
from .device import resolve_master_entity_id, safe_owner_slug

_LOGGER = logging.getLogger(__name__)

PUBLIC_PAGE_URL = "/gaming_status/public"
PUBLIC_JSON_URL = "/api/gaming_status/public.json"
# Under the page's own path, so a proxy/tunnel rule allowing the page
# allows its icon too.
PUBLIC_ICON_URL = f"{PUBLIC_PAGE_URL}/icon.png"
_ICON_PATH = Path(__file__).parent / "brand" / "icon.png"
PLAYER_OPT_PUBLIC = "show_on_public_page"

# Fallback when the cards aren't installed as a Lovelace resource. Tracks
# the latest cards release, which ships alongside each Gaming Status
# release; jsDelivr re-resolves @latest about every 12 hours.
CARDS_CDN_FALLBACK = (
    "https://cdn.jsdelivr.net/gh/adamjthompson/Gaming-Status-Cards@latest/"
    "gaming-status-cards.js"
)
MDI_FONT_CSS = (
    "https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css"
)

_VIEWS_REGISTERED_KEY = f"{DOMAIN}_public_views_registered"
_CACHE_KEY = f"{DOMAIN}_public_cache"
CACHE_SECONDS = 30

# type -> (label for the options menu, per_player, data requirement)
# per_player cards are rendered once per public player via single_entity;
# the requirement lets the server skip players that would show an empty card.
PUBLIC_CARD_CATALOG: dict[str, tuple[str, bool, str | None]] = {
    "gaming-status-card": ("Player list (status)", False, None),
    "gaming-slideshow-card": ("Now playing slideshow", False, None),
    "gaming-status-gamercard-card": ("Gamercard (per player)", True, "library"),
    "gaming-status-stats-card": ("Stats (per player)", True, "library"),
    "gaming-status-weekly-activity-card": ("Weekly activity", False, None),
    "gaming-status-game-chart-card": ("Weekly games chart (per player)", True, None),
    "gaming-status-donut-card": ("Platform split", False, None),
    "gaming-status-leaderboard-card": ("Leaderboard", False, None),
    "gaming-status-recent-sessions-card": ("Recent sessions", False, None),
    "gaming-status-recent-achievements-card": ("Recent achievements", False, None),
    "gaming-status-achievement-icons-card": ("Achievement icons", False, None),
    "gaming-status-library-card": ("Game library (per player)", True, "library"),
    "gaming-status-completion-tracker-card": (
        "100% completed games (per player)",
        True,
        "library_complete",
    ),
    "gaming-status-near-completion-card": (
        "Near completion (per player)",
        True,
        "library_near",
    ),
    "gaming-status-playstation-trophies-card": (
        "PlayStation trophies (per player)",
        True,
        "library_playstation",
    ),
}
# Cards that show a single platform (their "platform" option). The default
# layout adds one copy per platform the player has a library for.
_PER_PLATFORM_CARDS = {"gaming-status-gamercard-card"}
_LIBRARY_PLATFORMS = ("steam", "xbox", "playstation")

# Extra config for general cards on the default layout. The slideshow stays
# visible (showing its idle placeholder) rather than hiding when nobody's
# playing, so the page doesn't shift around.
_GENERAL_CARD_CONFIG: dict[str, dict[str, Any]] = {
    "gaming-slideshow-card": {"auto_hide": False},
    # The card defaults to the top 3.
    "gaming-status-leaderboard-card": {"max_players": "10"},
    # Labels for cards that have no heading of their own by default.
    "gaming-status-donut-card": {"title": "Platform Split"},
    "gaming-status-weekly-activity-card": {"title": "Weekly Stats"},
    "gaming-status-recent-achievements-card": {"title": "Recent Achievements"},
    "gaming-status-recent-sessions-card": {"title": "Recent Sessions"},
}

_LIBRARY_CARD = "gaming-status-library-card"
# Extra config for per-player cards on the default layout (the near
# completion card always shows as a list; its own default is 12 games).
_PLAYER_CARD_CONFIG: dict[str, dict[str, Any]] = {
    "gaming-status-near-completion-card": {"max_games": 5},
}
# Cards that show gamertags when Show gamertags is on (default layout).
_GAMERTAG_CARDS = {"gaming-status-card", "gaming-status-gamercard-card"}

# Never allowed on a public page, even if it's on the mirrored view: it calls services
# that edit/delete history.
_FORBIDDEN_CARD_TYPES = {"gaming-status-game-management-card"}

# Attribute keys never sent publicly. Pattern match catches credential-ish,
# account-id and parental-control fields, including any added later.
# Checked at every nesting level, not just top-level attribute names.
_DENY_ATTR_RE = re.compile(
    r"(token|npsso|oauth|password|secret|api_key|xuid|account|config_entry|"
    r"discord_user|discord_data|user_id|steam_?id|online_id|"
    r"internal|blocked|limit|remaining|parental|snooze|"
    r"rating_exceeded|source_entit|debug|backup)",
    re.IGNORECASE,
)
# Real account names: removed unless Show gamertags is on.
_GAMERTAG_RE = re.compile(r"(gamertag|display_name|persona)", re.IGNORECASE)
# Where the frontend integration keeps installed themes (themes: in
# configuration.yaml), keyed by theme name.
_FRONTEND_THEMES_KEY = "frontend_themes"
_FRONTEND_DEFAULT_THEME_KEY = "frontend_default_theme"
_HUB_ONLINE = "sensor.gaming_status_players_online"
_HUB_ANYONE = "binary_sensor.gaming_status_anyone_gaming"
_OFFLINE_STATES = {"offline", "unknown", "unavailable", "source missing", ""}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@callback
def async_register_public_views(hass: HomeAssistant) -> None:
    if hass.data.get(_VIEWS_REGISTERED_KEY):
        return
    hass.http.register_view(GamingStatusPublicJsonView())
    hass.http.register_view(GamingStatusPublicPageView())
    hass.http.register_view(GamingStatusPublicIconView())
    hass.data[_VIEWS_REGISTERED_KEY] = True


@callback
def async_invalidate_public_cache(hass: HomeAssistant) -> None:
    hass.data.pop(_CACHE_KEY, None)


# ---------------------------------------------------------------------------
# Options helpers
# ---------------------------------------------------------------------------


def _loaded_entry(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


def _players(entry) -> dict:
    raw = entry.options.get(OPT_PLAYERS) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def public_players(entry) -> list[str]:
    return sorted(
        (
            name
            for name, data in _players(entry).items()
            if isinstance(data, dict) and data.get(PLAYER_OPT_PUBLIC)
        ),
        key=str.lower,
    )


def page_enabled(entry) -> bool:
    return bool(entry and entry.options.get(OPT_PUBLIC_PAGE_ENABLED)) and bool(
        public_players(entry)
    )


def page_key(entry) -> str:
    """The private-link key, or "" when the page doesn't require one."""
    if not entry.options.get(OPT_PUBLIC_PAGE_REQUIRE_KEY):
        return ""
    return entry.options.get(OPT_PUBLIC_PAGE_KEY) or ""


def with_key(url: str, entry) -> str:
    key = page_key(entry)
    return f"{url}?key={key}" if key else url


# Per-IP request cap across the public views. The payload itself is cached
# (CACHE_SECONDS), so this only bounds a client hammering the endpoints --
# a visitor's page polls every ~30s, so a shared IP (tunnel, proxy without
# trusted_proxies) still fits dozens of visitors.
_RATE_LIMIT_KEY = "gaming_status_public_rate"
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 120


def _rate_limited(hass: HomeAssistant, request: web.Request) -> bool:
    now = time.monotonic()
    hits = hass.data.setdefault(_RATE_LIMIT_KEY, {})
    if len(hits) > 1000:
        # Drop idle clients so the table can't grow without bound.
        for ip in [ip for ip, (start, _) in hits.items() if now - start > 60]:
            del hits[ip]
    ip = request.remote or ""
    start, count = hits.get(ip, (now, 0))
    if now - start > _RATE_LIMIT_WINDOW_SECONDS:
        start, count = now, 0
    hits[ip] = (start, count + 1)
    return count + 1 > _RATE_LIMIT_MAX_REQUESTS


def _request_allowed(request: web.Request, entry) -> bool:
    """Page enabled and, when a private link is required, the right key.
    Every failure is the same 404 so the page's existence isn't confirmed."""
    if not page_enabled(entry):
        return False
    key = page_key(entry)
    return not key or secrets.compare_digest(request.query.get("key", ""), key)


def theme_mode(entry) -> str:
    mode = entry.options.get(OPT_PUBLIC_PAGE_THEME_MODE) if entry else None
    return mode if mode in PUBLIC_PAGE_THEME_MODES else DEFAULT_PUBLIC_PAGE_THEME_MODE


def installed_theme_names(hass) -> list[str]:
    return sorted(hass.data.get(_FRONTEND_THEMES_KEY) or {}, key=str.lower)


def _theme_vars(hass, name: str | None) -> dict | None:
    """An installed theme as CSS variables: {"base", "light", "dark"},
    each {"--var": value}. Mode sections come from the theme's own
    "modes:" block, applied over the base on top of the page's defaults."""
    if name == "Backend-selected":
        name = hass.data.get(_FRONTEND_DEFAULT_THEME_KEY)
    theme = (hass.data.get(_FRONTEND_THEMES_KEY) or {}).get(name) if name else None
    if not isinstance(theme, dict):
        return None

    def css(values) -> dict:
        if not isinstance(values, dict):
            return {}
        return {
            f"--{key}": str(value)
            for key, value in values.items()
            if key != "modes" and isinstance(value, (str, int, float))
        }

    modes = theme.get("modes") or {}
    return {
        "base": css(theme),
        "light": css(modes.get("light")),
        "dark": css(modes.get("dark")),
    }


def _lovelace_dashboards(hass) -> dict:
    """url_path -> dashboard config object. Lovelace's hass.data shape has
    changed across HA versions (dict, then a LovelaceData dataclass), so
    both are handled; the default Overview dashboard is keyed None."""
    data = hass.data.get("lovelace")
    dashboards = getattr(data, "dashboards", None)
    if dashboards is None and isinstance(data, dict):
        dashboards = data.get("dashboards")
    return dashboards or {}


async def _load_dashboard(dash) -> dict | None:
    try:
        config = await dash.async_load(False)
    except Exception:
        return None
    return config if isinstance(config, dict) else None


async def async_list_dashboard_views(hass) -> list[tuple[str, str]]:
    """(option value, label) for every view of every dashboard."""
    out = []
    for url_path, dash in _lovelace_dashboards(hass).items():
        config = await _load_dashboard(dash)
        if not config:
            continue
        meta = getattr(dash, "config", None) or {}
        dash_title = meta.get("title") or (url_path or "Overview")
        for index, view in enumerate(config.get("views") or []):
            ref = view.get("path") or str(index)
            view_title = view.get("title") or view.get("path") or f"View {index + 1}"
            out.append((f"{url_path or ''}|{ref}", f"{dash_title} › {view_title}"))
    return sorted(out, key=lambda item: item[1].lower())


def _walk_cards(cards):
    """Yield every Gaming Status card, descending into stacks/grids/
    conditional wrappers so nested cards aren't lost."""
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_type = str(card.get("type", "")).removeprefix("custom:")
        if (
            card_type.startswith("gaming-")
            and card_type not in _FORBIDDEN_CARD_TYPES
            and not card_type.endswith("-editor")
        ):
            yield {**card, "type": card_type}
        if isinstance(card.get("cards"), list):
            yield from _walk_cards(card["cards"])
        if isinstance(card.get("card"), dict):
            yield from _walk_cards([card["card"]])


async def _load_view(hass, source: str) -> dict | None:
    """The mirrored view's config, or None if the dashboard or view no
    longer exists."""
    url_path, _, ref = source.partition("|")
    dash = _lovelace_dashboards(hass).get(url_path or None)
    if dash is None:
        return None
    config = await _load_dashboard(dash)
    if not config:
        return None
    views = config.get("views") or []
    return next(
        (v for i, v in enumerate(views) if v.get("path") == ref or str(i) == ref),
        None,
    )


def _view_card_groups(view: dict) -> list[list[dict]]:
    """Card groups from the mirrored view: one group per section (sections
    views) or per top-level card/stack (masonry/panel views)."""
    if view.get("sections"):
        raw_groups = [section.get("cards") or [] for section in view["sections"]]
    else:
        raw_groups = [[card] for card in view.get("cards") or []]
    groups = [list(_walk_cards(group)) for group in raw_groups]
    return [g for g in groups if g]


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------


def _public_url_ok(value: str) -> bool:
    if value.startswith(("https://", "http://", "/local/", "/hacsfiles/")):
        return True
    # Image entities (the Xbox/PSN gamerpics) are served through
    # image_proxy with a short-lived per-entity token in the URL, which is
    # how HA's own frontend loads them without a login header. The token
    # only unlocks that one image and rotates every few minutes; the page
    # refreshes often enough to pick up the new one. Only player-device
    # entities ever reach this filter, so it can't expose other images.
    return value.startswith("/api/image_proxy/") and "token=" in value


_DROP = object()


def _filter_value(value, show_gamertags: bool):
    """Filters nested dicts/lists the same way as top-level attributes, so
    a sensitive field can't slip through inside another one."""
    if isinstance(value, dict):
        return _filter_attrs(value, show_gamertags)
    if isinstance(value, (list, tuple)):
        items = (_filter_value(v, show_gamertags) for v in value)
        return [v for v in items if v is not _DROP]
    if isinstance(value, str) and value.startswith("/") and not _public_url_ok(value):
        # e.g. /api/camera_proxy/... needs a login; drop rather than hand
        # the card a URL that renders as a broken image.
        return _DROP
    return value


def _filter_attrs(attrs: dict, show_gamertags: bool) -> dict:
    out = {}
    for key, value in attrs.items():
        key_str = str(key)
        if _DENY_ATTR_RE.search(key_str):
            continue
        if not show_gamertags and _GAMERTAG_RE.search(key_str):
            continue
        filtered = _filter_value(value, show_gamertags)
        if filtered is not _DROP:
            out[key] = filtered
    return out


def _state_dict(state, attrs) -> dict:
    return {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": attrs,
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }


def _player_entity_ids(hass, name: str) -> list[str]:
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, safe_owner_slug(name))})
    if device is None:
        return [resolve_master_entity_id(hass, name)]
    return [
        e.entity_id
        for e in er.async_entries_for_device(ent_reg, device.id)
        if e.domain in ("sensor", "binary_sensor") and not e.disabled_by
    ]


def _player_has(hass, name: str, requirement: str | None) -> bool:
    if requirement is None:
        return True
    slug = safe_owner_slug(name)
    if requirement in ("library", "library_complete", "library_near"):
        uid, guess = (
            f"gaming_status_{slug}_library_summary",
            f"sensor.gaming_status_{slug}_library_summary",
        )
        attr = "game_count"
    else:  # library_<platform>
        platform = requirement.split("_", 1)[1]
        uid, guess = (
            f"gaming_status_{slug}_library_{platform}",
            f"sensor.gaming_status_{slug}_library_{platform}",
        )
        attr = "game_count"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, uid) or guess
    st = hass.states.get(entity_id)
    if requirement == "library_complete":
        # The completion tracker lists only 100% games, so it'd be empty.
        return bool(st) and any(
            isinstance(g, dict) and (g.get("percent") or 0) >= 100
            for g in st.attributes.get("games") or []
        )
    if requirement == "library_near":
        # Started but unfinished games; without any, the card is empty.
        return bool(st) and any(
            isinstance(g, dict) and 0 < (g.get("percent") or 0) < 100
            for g in st.attributes.get("games") or []
        )
    return bool(st and st.attributes.get(attr))


def _library_earned(hass, name: str, platform: str | None = None) -> bool:
    """Whether the player's library (or one platform's) has any
    achievements earned -- the library sensors' state is that count."""
    slug = safe_owner_slug(name)
    suffix = f"library_{platform}" if platform else "library_summary"
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"gaming_status_{slug}_{suffix}"
    ) or (f"sensor.gaming_status_{slug}_{suffix}")
    st = hass.states.get(entity_id)
    try:
        return float(st.state) > 0
    except (AttributeError, TypeError, ValueError):
        return False


def _platform_flags(hass, name: str) -> dict:
    return {
        f"show_platform_{platform}": _player_has(hass, name, f"library_{platform}")
        for platform in _LIBRARY_PLATFORMS
    }


# Card options that list players (entity ids or player names). On a
# mirrored view these are cut down to public players, so a private
# player's name never appears in the page's data.
_ENTITY_LIST_KEYS = ("selected_entities", "manual_entities", "entities")


def _scrub_entity_lists(config: dict, allowed: set[str]) -> dict:
    out = dict(config)
    for key in _ENTITY_LIST_KEYS:
        value = out.get(key)
        if isinstance(value, str):
            tokens = [t.strip() for t in re.split(r"[,\n]", value) if t.strip()]
            out[key] = ", ".join(t for t in tokens if t.lower() in allowed)
        elif isinstance(value, list):
            out[key] = [
                item
                for item in value
                if str(
                    item.get("entity", "") if isinstance(item, dict) else item
                ).lower()
                in allowed
            ]
    return out


def _build_layout(
    hass,
    entry,
    names: list[str],
    masters: dict,
    mirrored: bool,
    view: dict | None,
    states: dict | None = None,
):
    """Returns (groups, notice). Each group is a list of {type, config}."""
    public_masters = set(masters.values())
    # Lower-cased entity ids, player names and friendly names of public
    # players -- everything a card's player list may legitimately name.
    allowed = {n.lower() for n in names}
    for entity_id, st in (states or {}).items():
        allowed.add(entity_id.lower())
        friendly = (st.get("attributes") or {}).get("friendly_name")
        if friendly:
            allowed.add(str(friendly).lower())

    if mirrored:
        if view is None:
            return [], "The dashboard view this page mirrors can’t be found."
        out = []
        for group in _view_card_groups(view):
            kept = []
            for card in group:
                config = {k: v for k, v in card.items() if k != "type"}
                target = config.get("single_entity") or config.get("entity")
                # Pinned to a specific player: only if that player is public.
                # Unpinned cards are safe as-is -- the snapshot only contains
                # public players, so "all"/"selected"/fallback modes can only
                # ever resolve to them.
                if target and target not in public_masters:
                    continue
                config = _scrub_entity_lists(config, allowed)
                kept.append({"type": card["type"], "config": config})
            if kept:
                out.append(kept)
        return out, None

    # Built-in default layout: the chosen general cards, then one group per
    # public player holding their chosen per-player cards, skipping any they
    # don't have the data for. Both follow the order the cards are listed in
    # the Public Page settings, so that order is the page's order.
    general = entry.options.get(
        OPT_PUBLIC_PAGE_GENERAL_CARDS, DEFAULT_PUBLIC_PAGE_GENERAL_CARDS
    )
    per_player_cards = entry.options.get(
        OPT_PUBLIC_PAGE_PLAYER_CARDS, DEFAULT_PUBLIC_PAGE_PLAYER_CARDS
    )
    hide_zero = entry.options.get(
        OPT_PUBLIC_PAGE_HIDE_ZERO_ACHIEVEMENTS,
        DEFAULT_PUBLIC_PAGE_HIDE_ZERO_ACHIEVEMENTS,
    )
    out = [
        [
            {
                "type": card_type,
                "config": {"mode": "all", **_GENERAL_CARD_CONFIG.get(card_type, {})},
            }
        ]
        for card_type in general
        if card_type in PUBLIC_CARD_CATALOG and not PUBLIC_CARD_CATALOG[card_type][1]
    ]
    for name in names:
        if name not in masters:
            continue
        flags = _platform_flags(hass, name)
        base = {
            # Hide platform tabs this player has no library for, and label
            # each copy.
            **flags,
            "title": name,
            "mode": "single",
            "single_entity": masters[name],
        }
        group = []
        for card_type in per_player_cards:
            _label, per_player, requirement = PUBLIC_CARD_CATALOG.get(
                card_type, ("", False, None)
            )
            if not per_player or not _player_has(hass, name, requirement):
                continue
            if card_type in _PER_PLATFORM_CARDS:
                group.extend(
                    {
                        "type": card_type,
                        # Kept compact: one recent game, and no Steam
                        # playtime or PlayStation trophy-tier row.
                        "config": {
                            **base,
                            "platform": platform,
                            "recent_games_count": 1,
                            "show_total_playtime": False,
                            "show_trophy_breakdown": False,
                        },
                    }
                    for platform in _LIBRARY_PLATFORMS
                    if flags[f"show_platform_{platform}"]
                )
            elif card_type == _LIBRARY_CARD and hide_zero:
                # Leave out games with nothing earned, and skip the card (or
                # a platform's tab) when that would leave it empty.
                if not _library_earned(hass, name):
                    continue
                tabs = {
                    f"show_platform_{p}": flags[f"show_platform_{p}"]
                    and _library_earned(hass, name, p)
                    for p in _LIBRARY_PLATFORMS
                }
                group.append(
                    {
                        "type": card_type,
                        "config": {**base, **tabs, "exclude_zero_completion": True},
                    }
                )
            else:
                group.append(
                    {
                        "type": card_type,
                        "config": {**base, **_PLAYER_CARD_CONFIG.get(card_type, {})},
                    }
                )
        if group:
            out.append(group)
    if entry.options.get(OPT_PUBLIC_PAGE_SHOW_GAMERTAGS, False):
        # The setting only lets gamertags into the snapshot; the cards
        # that can show them each have their own opt-in switch too. Only
        # the player list and Gamercards turn it on -- elsewhere they
        # clutter the card.
        for group in out:
            for item in group:
                if item["type"] in _GAMERTAG_CARDS:
                    item["config"] = {**item["config"], "show_gamertags": True}
    return out, None


async def _cards_script_url(hass, entry) -> str:
    override = (entry.options.get(OPT_PUBLIC_PAGE_CARDS_URL) or "").strip()
    if override:
        return override
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None and isinstance(lovelace, dict):
            resources = lovelace.get("resources")
        if resources is not None:
            if getattr(resources, "loaded", True) is False:
                await resources.async_load()
                resources.loaded = True
            for item in resources.async_items():
                url = item.get("url", "")
                if "gaming-status-cards" in url:
                    return url
    except Exception:
        _LOGGER.debug("[Gaming Status] Couldn't read Lovelace resources", exc_info=True)
    return CARDS_CDN_FALLBACK


async def build_public_payload(hass: HomeAssistant) -> dict | None:
    cached = hass.data.get(_CACHE_KEY)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    entry = _loaded_entry(hass)
    if not page_enabled(entry):
        return None
    names = public_players(entry)
    show_gamertags = entry.options.get(OPT_PUBLIC_PAGE_SHOW_GAMERTAGS, False)

    states: dict[str, dict] = {}
    masters: dict[str, str] = {}
    online = 0
    active_games: list[str] = []
    for name in names:
        master_id = resolve_master_entity_id(hass, name)
        for entity_id in _player_entity_ids(hass, name):
            st = hass.states.get(entity_id)
            if st is None:
                continue
            states[entity_id] = _state_dict(
                st, _filter_attrs(dict(st.attributes), show_gamertags)
            )
            if entity_id == master_id:
                masters[name] = master_id
                if (st.state or "").lower() not in _OFFLINE_STATES:
                    online += 1
                    active_games.append(st.state)

    # Hub entities, recomputed over public players only.
    stamp = dt_util.utcnow().isoformat()
    for entity_id, value, attrs in (
        (
            _HUB_ONLINE,
            str(online),
            {
                "friendly_name": "Players Online",
                "active_games": ", ".join(active_games) if active_games else "None",
            },
        ),
        (
            _HUB_ANYONE,
            "on" if online else "off",
            {"friendly_name": "Anyone Gaming"},
        ),
    ):
        real = hass.states.get(entity_id)
        if real is None:
            continue
        # Only presentation attributes are copied from the real hub entity:
        # anything else on it (e.g. its active_games, which names private
        # players' games) covers every player, not just public ones.
        safe = {
            k: real.attributes[k]
            for k in ("icon", "unit_of_measurement", "device_class")
            if k in real.attributes
        }
        states[entity_id] = {
            "entity_id": entity_id,
            "state": value,
            "attributes": {**safe, **attrs},
            "last_changed": stamp,
            "last_updated": stamp,
        }

    source = entry.options.get(OPT_PUBLIC_PAGE_SOURCE_VIEW) or ""
    mirrored = bool(source) and source != PUBLIC_PAGE_DEFAULT_LAYOUT
    view = await _load_view(hass, source) if mirrored else None
    layout, notice = _build_layout(hass, entry, names, masters, mirrored, view, states)

    theme_name = entry.options.get(OPT_PUBLIC_PAGE_THEME) or PUBLIC_PAGE_THEME_DEFAULT
    if theme_name == PUBLIC_PAGE_THEME_DEFAULT:
        theme_name = (view or {}).get("theme")

    payload = {
        "title": entry.options.get(OPT_PUBLIC_PAGE_TITLE) or DEFAULT_PUBLIC_PAGE_TITLE,
        "theme_mode": theme_mode(entry),
        "theme": _theme_vars(hass, theme_name),
        "cards_url": await _cards_script_url(hass, entry),
        "generated_at": dt_util.now().isoformat(),
        "refresh_seconds": CACHE_SECONDS * 2,
        "layout": layout,
        "notice": notice,
        "states": states,
    }
    hass.data[_CACHE_KEY] = (now, payload)
    return payload


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class GamingStatusPublicJsonView(HomeAssistantView):
    url = PUBLIC_JSON_URL
    name = "api:gaming_status:public"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if _rate_limited(hass, request):
            return web.Response(status=429, text="Too many requests")
        if not _request_allowed(request, _loaded_entry(hass)):
            return web.json_response({"error": "not_enabled"}, status=404)
        payload = await build_public_payload(hass)
        if payload is None:
            return web.json_response({"error": "not_enabled"}, status=404)
        resp = web.json_response(
            payload,
            headers={
                "Cache-Control": f"public, max-age={CACHE_SECONDS}",
                "X-Robots-Tag": "noindex",
            },
        )
        resp.enable_compression()  # library "games" lists can be large
        return resp


# The cards' one inline image-fallback handler (onerror=...), allowed by
# hash so it keeps working under the CSP. If the cards change it, only that
# broken-image fallback stops working; nothing else depends on it.
_CARDS_INLINE_HANDLERS = (
    (
        "this.style.display='none'; "
        "this.previousElementSibling.style.removeProperty('display');"
    ),
)
_HANDLER_HASHES = " ".join(
    f"'sha256-{base64.b64encode(hashlib.sha256(h.encode()).digest()).decode()}'"
    for h in _CARDS_INLINE_HANDLERS
)


def _content_security_policy(nonce: str, cards_url: str) -> str:
    """Scripts only from this page (by nonce), Home Assistant, jsDelivr and
    the cards script's own host. Images may come from anywhere (artwork
    hosts vary); the page itself makes no requests except to HA."""
    script_hosts = {"'self'", "https://cdn.jsdelivr.net"}
    parts = urlsplit(cards_url)
    if parts.scheme in ("http", "https") and parts.netloc:
        script_hosts.add(f"{parts.scheme}://{parts.netloc}")
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'nonce-{nonce}' 'unsafe-hashes' {_HANDLER_HASHES} "
            + " ".join(sorted(script_hosts)),
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "font-src 'self' https://cdn.jsdelivr.net data:",
            "img-src * data: blob:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        ]
    )


class GamingStatusPublicPageView(HomeAssistantView):
    url = PUBLIC_PAGE_URL
    name = "gaming_status:public_page"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if _rate_limited(hass, request):
            return web.Response(status=429, text="Too many requests")
        entry = _loaded_entry(hass)
        if not _request_allowed(request, entry):
            return web.Response(status=404, text="Not found")
        nonce = secrets.token_urlsafe(16)
        csp = _content_security_policy(nonce, await _cards_script_url(hass, entry))
        # Link previews (Discord, iMessage, Slack...) read these server-side,
        # so they're filled in here rather than by the page's script.
        # request.url reflects the proxy's public host once trusted_proxies
        # is set, so the preview image URL is reachable from outside.
        payload = await build_public_payload(hass)
        title = entry.options.get(OPT_PUBLIC_PAGE_TITLE) or DEFAULT_PUBLIC_PAGE_TITLE
        online = int(
            ((payload or {}).get("states", {}).get(_HUB_ONLINE) or {}).get("state") or 0
        )
        origin = str(request.url.origin())
        return web.Response(
            text=PAGE_HTML.replace("__NONCE__", nonce)
            .replace("__PAGE_TITLE__", html.escape(title))
            .replace(
                "__DESCRIPTION__",
                f"{online} Player{'' if online == 1 else 's'} Online",
            )
            .replace(
                "__PAGE_URL__",
                html.escape(with_key(f"{origin}{PUBLIC_PAGE_URL}", entry)),
            )
            .replace(
                "__ICON_ABS_URL__",
                html.escape(with_key(f"{origin}{PUBLIC_ICON_URL}", entry)),
            )
            .replace("__JSON_URL__", html.escape(with_key(PUBLIC_JSON_URL, entry)))
            .replace("__ICON_URL__", html.escape(with_key(PUBLIC_ICON_URL, entry)))
            .replace("__MDI_CSS__", MDI_FONT_CSS)
            .replace("__THEME_MODE__", theme_mode(entry)),
            content_type="text/html",
            headers={
                "Cache-Control": "no-cache",
                "X-Robots-Tag": "noindex",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": csp,
                "X-Content-Type-Options": "nosniff",
            },
        )


class GamingStatusPublicIconView(HomeAssistantView):
    url = PUBLIC_ICON_URL
    name = "gaming_status:public_icon"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        hass = request.app["hass"]
        if _rate_limited(hass, request):
            return web.Response(status=429, text="Too many requests")
        if not _request_allowed(request, _loaded_entry(hass)):
            return web.Response(status=404, text="Not found")
        return web.FileResponse(
            _ICON_PATH, headers={"Cache-Control": "public, max-age=86400"}
        )


# ---------------------------------------------------------------------------
# Page shell: theme variables + <ha-card>/<ha-icon> stand-ins + card host
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!doctype html>
<html lang="en" data-theme="__THEME_MODE__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>__PAGE_TITLE__</title>
<meta name="description" content="__DESCRIPTION__">
<meta property="og:type" content="website">
<meta property="og:title" content="__PAGE_TITLE__">
<meta property="og:description" content="__DESCRIPTION__">
<meta property="og:url" content="__PAGE_URL__">
<meta property="og:image" content="__ICON_ABS_URL__">
<meta name="twitter:card" content="summary">
<link rel="icon" type="image/png" href="__ICON_URL__">
<link rel="stylesheet" href="__MDI_CSS__">
<style>
/* Home Assistant's default theme variables, which the cards read. */
:root{
  --primary-font-family:Roboto,"Segoe UI",system-ui,-apple-system,sans-serif;
  --primary-color:#03a9f4; --text-primary-color:#ffffff;
  --primary-text-color:#212121; --secondary-text-color:#727272;
  --primary-background-color:#fafafa; --secondary-background-color:#e5e5e5;
  --card-background-color:#ffffff; --divider-color:rgba(0,0,0,.12);
  --error-color:#db4437; --success-color:#43a047;
  --ha-card-border-radius:12px; --ha-card-box-shadow:none;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --primary-text-color:#e1e1e1; --secondary-text-color:#9b9b9b;
  --primary-background-color:#111111; --secondary-background-color:#282828;
  --card-background-color:#1c1c1c; --divider-color:rgba(225,225,225,.12);
  color-scheme:dark}}
:root[data-theme="dark"]{
  --primary-text-color:#e1e1e1; --secondary-text-color:#9b9b9b;
  --primary-background-color:#111111; --secondary-background-color:#282828;
  --card-background-color:#1c1c1c; --divider-color:rgba(225,225,225,.12);
  color-scheme:dark}
*{box-sizing:border-box}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--primary-background-color);color:var(--primary-text-color);
  font-family:var(--primary-font-family);
  padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
header{max-width:1280px;margin:0 auto;padding:24px 16px 8px;
  display:flex;align-items:center;gap:12px}
/* Sized to the title + subtitle block beside it: both line heights plus
   the gap between them (kept in sync with h1/#sub below). */
header .icon{flex:none;display:block;object-fit:contain;
  width:calc(28px*1.2 + 4px + 14px*1.2);height:calc(28px*1.2 + 4px + 14px*1.2)}
h1{font-size:28px;line-height:1.2;font-weight:400;margin:0}
#sub{color:var(--secondary-text-color);margin:4px 0 0;font-size:14px;line-height:1.2}
main{max-width:1280px;margin:0 auto;padding:8px 16px 32px;
  columns:3 420px;column-gap:16px}
main>*{break-inside:avoid;display:block;margin-bottom:16px}
.group{display:flex;flex-direction:column;gap:16px}
.group>*{display:block}
/* A card that hides itself (e.g. an auto-hiding slideshow) sets an inline
   display:none; hide its wrapper too so no empty gap is left behind. */
.group:not(:has(>:not([style*="display: none"]))){display:none}
.msg{color:var(--secondary-text-color);column-span:all}
footer{text-align:center;color:var(--secondary-text-color);font-size:12px;padding-bottom:24px}
</style>
</head>
<body>
<header><img class="icon" src="__ICON_URL__" alt="">
<div><h1 id="title">__PAGE_TITLE__</h1><p id="sub">Loading…</p></div></header>
<main id="cards" aria-live="polite"></main>
<footer><span id="updated"></span>Powered by Gaming Status for Home Assistant</footer>
<script nonce="__NONCE__">
const JSON_URL="__JSON_URL__", MDI_CSS="__MDI_CSS__";

// --- Stand-ins for Home Assistant's own frontend elements -----------------
if(!customElements.get("ha-card")){
  customElements.define("ha-card",class extends HTMLElement{
    constructor(){super();this.attachShadow({mode:"open"}).innerHTML=
      `<style>:host{display:block;background:var(--ha-card-background,var(--card-background-color));
       border-radius:var(--ha-card-border-radius,12px);box-shadow:var(--ha-card-box-shadow,none);
       color:var(--primary-text-color);position:relative;overflow:hidden}
       .h{font-size:24px;padding:24px 16px 16px;line-height:1.3}</style><div class="h" hidden></div><slot></slot>`}
    static get observedAttributes(){return["header"]}
    attributeChangedCallback(_,__,v){const h=this.shadowRoot.querySelector(".h");h.hidden=!v;h.textContent=v||""}
    set header(v){v?this.setAttribute("header",v):this.removeAttribute("header")}
  });
}
if(!customElements.get("ha-icon")){
  customElements.define("ha-icon",class extends HTMLElement{
    constructor(){super();this.attachShadow({mode:"open"}).innerHTML=
      `<link rel="stylesheet" href="${MDI_CSS}"><style>:host{display:inline-flex;vertical-align:middle;
       width:var(--mdc-icon-size,24px);height:var(--mdc-icon-size,24px);align-items:center;justify-content:center}
       span{font-size:var(--mdc-icon-size,24px);line-height:1}</style><span class="mdi"></span>`}
    static get observedAttributes(){return["icon"]}
    attributeChangedCallback(_,__,v){this._set(v)}
    set icon(v){this.setAttribute("icon",v||"")} get icon(){return this.getAttribute("icon")}
    _set(v){const s=this.shadowRoot.querySelector("span");const n=(v||"").replace(/^mdi:/,"");
      s.className=n?"mdi mdi-"+n:"mdi"}
  });
}

// --- Theme -----------------------------------------------------------------
const darkQuery=matchMedia("(prefers-color-scheme: dark)");
let theme=null, themeProps=[], lastStates=null;
function isDark(){const m=document.documentElement.dataset.theme;
  return m==="dark"||(m!=="light"&&darkQuery.matches)}
function applyTheme(){
  const root=document.documentElement.style;
  themeProps.forEach(p=>root.removeProperty(p)); themeProps=[];
  if(!theme)return;
  const vars={...theme.base,...(isDark()?theme.dark:theme.light)};
  for(const[k,v]of Object.entries(vars)){root.setProperty(k,v);themeProps.push(k)}
}
darkQuery.addEventListener("change",()=>{applyTheme();
  if(lastStates){const h=makeHass(lastStates);cards.forEach(c=>{c.hass=h})}});

// --- Read-only hass stand-in ----------------------------------------------
function makeHass(states){
  const dark=isDark();
  const lang=navigator.language||"en";
  const nope=()=>Promise.reject(new Error("Read-only public page"));
  return{states,language:lang,locale:{language:lang,number_format:"language",time_format:"language"},
    themes:{darkMode:dark},selectedTheme:null,user:{is_admin:false,name:"Guest"},
    callService:nope,callWS:nope,callApi:nope,connection:null,
    // Tells the cards there's no Home Assistant UI here (e.g. no more-info dialog).
    readOnly:true};
}

let cards=[], layoutKey="", scriptUrl=null;
function loadScript(url){return new Promise((ok,fail)=>{
  if(scriptUrl===url)return ok(); const s=document.createElement("script");
  s.type="module";s.src=url;s.onload=()=>{scriptUrl=url;ok()};s.onerror=()=>{s.remove();fail(new Error("cards script failed to load: "+url))};
  document.head.append(s)})}

function msg(text){document.getElementById("cards").replaceChildren(
  Object.assign(document.createElement("p"),{className:"msg",textContent:text}))}

async function makeCard(item,hass){
  await customElements.whenDefined(item.type);
  const Card=customElements.get(item.type);
  const el=document.createElement(item.type);
  el.setConfig({...(Card.getStubConfig?Card.getStubConfig():{}),...item.config});
  el.hass=hass; return el;
}
async function build(groups,hass){
  const box=document.getElementById("cards"); const blocks=[]; const made=[];
  for(const group of groups){
    const block=document.createElement("div"); block.className="group";
    for(const item of group){
      try{const el=await makeCard(item,hass); block.append(el); made.push(el)}
      catch(e){console.warn("Gaming Status: skipped card",item.type,e)}
    }
    if(block.childElementCount)blocks.push(block);
  }
  box.replaceChildren(...blocks); cards=made;
  if(!made.length)msg("No cards to show yet.");
}

// Each step names what failed, so a visitor (or you, debugging a proxy or
// tunnel) can tell a blocked data request from a blocked cards script.
async function refresh(){
  let next=60, step="data";
  const sub=document.getElementById("sub");
  try{
    let r;
    try{r=await fetch(JSON_URL,{cache:"no-store"})}
    catch(e){step="network";throw e}
    if(r.status===404){msg("This page isn’t enabled right now.");return}
    if(!r.ok){step="status:"+r.status;throw new Error("HTTP "+r.status)}
    let d;
    try{d=await r.json()}catch(e){step="json";throw e}
    next=d.refresh_seconds||60;
    document.title=d.title; document.getElementById("title").textContent=d.title;
    document.documentElement.dataset.theme=d.theme_mode||"auto";
    theme=d.theme||null; applyTheme(); lastStates=d.states;
    const on=Object.values(d.states).filter(s=>s.entity_id.endsWith("_master")&&
      !["offline","unknown","unavailable","source missing"].includes(String(s.state).toLowerCase())).length;
    sub.textContent=`${on} Player${on===1?"":"s"} Online`;
    const at=new Date(d.generated_at);
    document.getElementById("updated").textContent=isNaN(at)?"":
      `Updated ${at.toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})} · `;
    step="script";
    await loadScript(d.cards_url);
    step="render";
    const hass=makeHass(d.states); const key=JSON.stringify(d.layout);
    if(d.notice){layoutKey="";cards=[];msg(d.notice)}
    else if(key!==layoutKey){layoutKey=key;await build(d.layout,hass)}
    else cards.forEach(c=>{c.hass=hass});
  }catch(e){
    const text=step==="network"?"Couldn’t reach the server."
      :step.startsWith("status:")?`The server returned an error (${step.slice(7)}).`
      :step==="json"?"The server sent something other than page data (a proxy or security page?)."
      :step==="script"?"Couldn’t load the cards script."
      :step==="render"?"Couldn’t display the cards."
      :"Couldn’t load page data.";
    sub.textContent=text+" Retrying…";
    console.warn("Gaming Status:",text,e);
  }
  finally{setTimeout(refresh,next*1000)}
}
refresh();
</script>
</body>
</html>
"""
