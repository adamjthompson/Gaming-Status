"""Button platform for Gaming Status -- one manual "scan library now" button
per player with the full-library-scan feature active (friendlier for
non-technical use than HA's built-in homeassistant.update_entity service).

Only created for players who already have a LibraryScanCoordinator (see
sensor.py's async_setup_entry, which populates
hass.data[DOMAIN]["library_coordinators"] -- this platform is forwarded
AFTER "sensor" in __init__.py's PLATFORMS list specifically so that dict is
already populated by the time this runs).
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinators = hass.data.get(DOMAIN, {}).get("library_coordinators", {})
    async_add_entities(
        LibraryScanRefreshButton(safe_owner, coordinator)
        for safe_owner, coordinator in coordinators.items()
    )


class LibraryScanRefreshButton(ButtonEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:refresh"

    def __init__(self, safe_owner, coordinator):
        self._coordinator = coordinator
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_refresh"
        self.entity_id = f"button.gaming_status_{safe_owner}_library_refresh"
        self._attr_name = f"{coordinator.owner_name} Game Library Refresh"

    async def async_press(self) -> None:
        # A deliberate manual request always forces a real scan -- unlike
        # the automatic post-reload path in sensor.py (async_schedule_or_refresh),
        # which skips rescanning if the data is still fresh. Debounced
        # (not async_refresh()) so an accidental double-press doesn't fire
        # two scans back to back.
        await self._coordinator.async_request_refresh()
