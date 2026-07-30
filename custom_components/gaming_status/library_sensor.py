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

    # A full games list (every tracked game, with achievement counts + up to
    # 4 artwork URLs each) can easily exceed the recorder's 16KB per-state
    # attributes limit for a large library -- exclude it the same way every
    # other bulky/volatile attribute in this integration already is (see
    # PersistentStatusSensor/MasterGamingSensor's own _unrecorded_attributes).
    _unrecorded_attributes = frozenset({"games"})

    def __init__(self, coordinator, owner_name, safe_owner, device_info=None):
        super().__init__(coordinator)
        self._owner_name = owner_name
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_summary"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_library_summary"
        self._attr_name = f"{owner_name} Game Library"
        self._attr_device_info = device_info

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("total_achievements_earned")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        # Achievement backfill (Xbox/PlayStation only -- Steam's per-game
        # recent_unlocks arrive for free on every regular scan, no backfill
        # needed) has no other user-visible progress indicator, since
        # _backfill_done is otherwise a private coordinator attribute.
        backfill_done = getattr(self.coordinator, "_backfill_done", {}) or {}
        platforms_data = data.get("platforms", {}) or {}
        backfill_total = 0
        backfill_pending = 0
        for platform in ("xbox", "playstation"):
            games = (platforms_data.get(platform) or {}).get("games", [])
            done = backfill_done.get(platform, {})
            backfill_total += len(games)
            backfill_pending += sum(1 for g in games if str(g.get("id")) not in done)
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
            "achievement_backfill_pending": backfill_pending,
            "achievement_backfill_total": backfill_total,
            "achievement_backfill_complete": backfill_pending == 0,
            "games": data.get("games"),
        }


class TrophyLibraryPlatformSensor(CoordinatorEntity, SensorEntity):
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"games"})

    def __init__(self, coordinator, owner_name, safe_owner, platform, device_info=None):
        super().__init__(coordinator)
        self._owner_name = owner_name
        self._platform = platform
        config = PLATFORM_CONFIG[platform]
        self._attr_icon = config["icon"]
        self._attr_unique_id = f"gaming_status_{safe_owner}_library_{platform}"
        self.entity_id = f"sensor.gaming_status_{safe_owner}_library_{platform}"
        self._attr_name = f"{owner_name} {config['name_suffix']} Library"
        self._attr_device_info = device_info

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
        if self._platform == "steam":
            attrs["playtime_hours"] = pdata.get("playtime_hours")
        elif self._platform == "xbox":
            attrs["gamerscore_earned"] = pdata.get("gamerscore_earned")
            attrs["gamerscore_total"] = pdata.get("gamerscore_total")
        elif self._platform == "playstation":
            trophies_earned = pdata.get("trophies_earned") or {}
            trophies_total = pdata.get("trophies_total") or {}
            for tier in ("bronze", "silver", "gold", "platinum"):
                attrs[f"trophies_{tier}"] = trophies_earned.get(tier)
                attrs[f"trophies_{tier}_total"] = trophies_total.get(tier)
        return attrs
