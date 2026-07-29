"""Full-library-scan sensor entities -- one summary sensor + one sensor per
tracked platform, per player. Fed entirely by library_scan.py's
LibraryScanCoordinator; these classes are thin CoordinatorEntity wrappers
with no game-list scanning logic of their own. Only created (see
sensor.py's async_setup_entry) when OPT_ENABLE_LIBRARY_SCAN is on and the
player already has a matching platform PersistentStatusSensor -- no
per-game entities, to avoid entity explosion for large libraries.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import PLATFORM_CONFIG


class TrophyLibrarySummarySensor(CoordinatorEntity, SensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:trophy"

    def __init__(self, coordinator, owner_name, safe_owner):
        super().__init__(coordinator)
        self._owner_name = owner_name
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_summary"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_library_summary"
        self._attr_name = f"{owner_name} Game Library"

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("total_achievements_earned")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "total_achievements_possible": data.get("total_achievements_possible"),
            "total_gamerscore": data.get("total_gamerscore"),
            "total_platinum_trophies": data.get("total_platinum_trophies"),
            "average_completion_percent": data.get("average_completion_percent"),
            "game_count": data.get("game_count"),
            "tracked_platforms": data.get("tracked_platforms"),
            "last_sync_success": data.get("last_sync_success"),
            "platform_errors": data.get("platform_errors"),
            "last_synced": data.get("last_synced"),
            "games": data.get("games"),
        }


class TrophyLibraryPlatformSensor(CoordinatorEntity, SensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator, owner_name, safe_owner, platform):
        super().__init__(coordinator)
        self._owner_name = owner_name
        self._platform = platform
        config = PLATFORM_CONFIG[platform]
        self._attr_icon = config["icon"]
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_{platform}"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_library_{platform}"
        self._attr_name = f"{owner_name} {config['name_suffix']} Library"

    @property
    def _platform_data(self):
        data = self.coordinator.data or {}
        return (data.get("platforms") or {}).get(self._platform, {})

    @property
    def native_value(self):
        return self._platform_data.get("achievements_earned")

    @property
    def extra_state_attributes(self):
        pdata = self._platform_data
        attrs = {
            "achievements_total": pdata.get("achievements_total"),
            "game_count": pdata.get("game_count"),
            "games": pdata.get("games"),
        }
        if self._platform == "xbox":
            attrs["gamerscore_earned"] = pdata.get("gamerscore_earned")
            attrs["gamerscore_total"] = pdata.get("gamerscore_total")
        elif self._platform == "playstation":
            trophies_earned = pdata.get("trophies_earned") or {}
            trophies_total = pdata.get("trophies_total") or {}
            for tier in ("bronze", "silver", "gold", "platinum"):
                attrs[f"trophies_{tier}"] = trophies_earned.get(tier)
                attrs[f"trophies_{tier}_total"] = trophies_total.get(tier)
        return attrs
