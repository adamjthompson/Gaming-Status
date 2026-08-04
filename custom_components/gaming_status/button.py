"""Button platform for Gaming Status -- one manual "scan library now" button
per player with the full-library-scan feature active (friendlier for
non-technical use than HA's built-in homeassistant.update_entity service).

Deliberately does NOT read hass.data[DOMAIN]["library_coordinators"] at
*setup* time to decide what to create -- confirmed live against HA core's
own config_entries.py that async_forward_entry_setups() runs every
platform's async_setup_entry CONCURRENTLY (asyncio.gather over
create_eager_task), not sequentially in PLATFORMS list order. sensor.py's
async_setup_entry populates that dict while looping over players, with real
await points in between each player -- so this platform's setup could
easily run (and read an empty/partial dict) while sensor.py's is still
mid-loop. Instead, this recomputes the same cheap eligibility check
sensor.py itself uses (does this player have >=1 steam/xbox/playstation
platform configured, with achievement tracking + library scan both on)
directly from config_entry.options, and only looks up the actual
coordinator *lazily inside async_press()* -- by the time a user can press a
button in the UI, every platform has long since finished loading, so the
dict is guaranteed to be complete there.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import (
    DEFAULT_ENABLE_ACHIEVEMENT_TRACKING,
    DEFAULT_ENABLE_LIBRARY_SCAN,
    DEFAULT_ENABLED_PLATFORMS,
    DOMAIN,
    OPT_ENABLE_ACHIEVEMENT_TRACKING,
    OPT_ENABLE_LIBRARY_SCAN,
    OPT_ENABLED_PLATFORMS,
    OPT_PLAYERS,
)
from .device import player_device_info, safe_owner_slug
from .sensor import _load_opt_json


async def async_setup_entry(hass, config_entry, async_add_entities):
    opts = config_entry.options
    if not (
        opts.get(OPT_ENABLE_ACHIEVEMENT_TRACKING, DEFAULT_ENABLE_ACHIEVEMENT_TRACKING)
        and opts.get(OPT_ENABLE_LIBRARY_SCAN, DEFAULT_ENABLE_LIBRARY_SCAN)
    ):
        return

    players = _load_opt_json(opts, OPT_PLAYERS, {})
    enabled_platforms = opts.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)

    entities = []
    for player_name, player_data in players.items():
        has_library_platform = any(
            player_data.get(platform)
            for platform in ("steam", "xbox", "playstation")
            if platform in enabled_platforms
        )
        if not has_library_platform:
            continue
        safe_owner = safe_owner_slug(player_name)
        platforms = [p for p in enabled_platforms if player_data.get(p)]
        device_info = player_device_info(player_name, safe_owner, platforms)
        entities.append(LibraryScanRefreshButton(hass, safe_owner, player_name, device_info))

    async_add_entities(entities)


class LibraryScanRefreshButton(ButtonEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:refresh"

    def __init__(self, hass, safe_owner, owner_name, device_info=None):
        self.hass = hass
        self._safe_owner = safe_owner
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_refresh"
        self.entity_id = f"button.gaming_status_{safe_owner}_library_refresh"
        self._attr_name = f"{owner_name} Game Library Refresh"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        # Looked up lazily (not held as a reference from setup time) --
        # see the module docstring for why. A deliberate manual request
        # always forces a real scan -- unlike the automatic post-reload
        # path in sensor.py (async_schedule_or_refresh), which skips
        # rescanning if the data is still fresh. Debounced (not
        # async_refresh()) so an accidental double-press doesn't fire two
        # scans back to back.
        coordinator = self.hass.data.get(DOMAIN, {}).get("library_coordinators", {}).get(self._safe_owner)
        if coordinator:
            await coordinator.async_request_refresh()
