"""Gaming Status integration — setup and teardown."""
from __future__ import annotations

import os
import json
import logging
import re
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    OPT_RESET_HISTORY,
    OPT_GRACE_PERIOD,
    OPT_AWAY_GRACE_PERIOD,
    OPT_TRANSITION_GRACE,
    OPT_MIN_SESSION,
    OPT_PLAYERS,
    OPT_ENDPOINTS,
    OPT_WEEKLY_REPORT,
    OPT_PARENTAL,
    OPT_TITLE_OVERRIDES,
    OPT_TITLE_CLEANUPS,
    OPT_GLOBAL_EXCLUSIONS,
    CONF_DISCORD_TOKEN,
    OPT_ENABLED_PLATFORMS,
    DEFAULT_ENABLED_PLATFORMS,
    PLAYER_PLATFORMS,
)
from .notifier import GamingNotifier

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor"]

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Silently migrate old JSON data to the new config options database."""
    _LOGGER.debug("Migrating Gaming Status from version %s", config_entry.version)

    if config_entry.version == 1:
        new_options = {**config_entry.options}
        file_path = hass.config.path("gaming_profiles.json")

        if os.path.exists(file_path):
            def read_legacy_file():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return {}

            old_data = await hass.async_add_executor_job(read_legacy_file)

            # 1. Map Global Settings (Handling both old uppercase and newer lowercase formats)
            global_settings = old_data.get("global_settings", old_data.get("GLOBAL_SETTINGS", {}))
            
            def _map_setting(old_keys, new_opt):
                for k in old_keys:
                    if k in global_settings:
                        new_options[new_opt] = global_settings[k]
                        break

            _map_setting(["grace_period_seconds", "GRACE_PERIOD_SECONDS"], OPT_GRACE_PERIOD)
            _map_setting(["away_grace_period_seconds", "AWAY_GRACE_PERIOD_SECONDS"], OPT_AWAY_GRACE_PERIOD)
            _map_setting(["game_transition_grace_seconds", "GAME_TRANSITION_GRACE_SECONDS"], OPT_TRANSITION_GRACE)
            _map_setting(["min_session_duration", "MIN_SESSION_DURATION"], OPT_MIN_SESSION)
            _map_setting(["reset_history", "RESET_HISTORY"], OPT_RESET_HISTORY)

            # 2. Map Complex Arrays (Dumping them to JSON strings for the new architecture)
            def _migrate_complex(old_key, opt_key):
                if old_key in old_data:
                    new_options[opt_key] = json.dumps(old_data[old_key], ensure_ascii=False)

            _migrate_complex("players", OPT_PLAYERS)
            _migrate_complex("notification_endpoints", OPT_ENDPOINTS)
            _migrate_complex("weekly_report", OPT_WEEKLY_REPORT)
            _migrate_complex("parental_controls", OPT_PARENTAL)
            _migrate_complex("game_title_overrides", OPT_TITLE_OVERRIDES)
            _migrate_complex("title_cleanups", OPT_TITLE_CLEANUPS)
            _migrate_complex("global_exclusions", OPT_GLOBAL_EXCLUSIONS)

            # Optional: Delete the old file so it doesn't clutter the user's config folder
            try:
                await hass.async_add_executor_job(os.remove, file_path)
            except OSError:
                pass

        # Update the entry to Version 2
        hass.config_entries.async_update_entry(config_entry, options=new_options, version=2)

    _LOGGER.info("Gaming Status migration to version %s successful", config_entry.version)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gaming Status from a UI config entry."""
    hass.data.setdefault(DOMAIN, {})

    # --- Stale Entity Registry Cleanup ---
    from homeassistant.helpers import entity_registry as er
    entity_reg = er.async_get(hass)
    
    stale_entities = [
        entry_id
        for entry_id, reg_entry in entity_reg.entities.items()
        if reg_entry.platform == DOMAIN
        and reg_entry.config_entry_id != entry.entry_id
    ]
    
    for entity_entry_id in stale_entities:
        _LOGGER.debug("Gaming Status: Removing stale entity registry entry %s", entity_entry_id)
        entity_reg.async_remove(entity_entry_id)

    notifier = GamingNotifier(hass, entry)
    await notifier.async_start()
    hass.data[DOMAIN]["notifier"] = notifier

    # --- DISCORD WEBSOCKET MANAGER ---
    discord_token = entry.data.get(CONF_DISCORD_TOKEN)
    enabled_platforms = entry.options.get(OPT_ENABLED_PLATFORMS, DEFAULT_ENABLED_PLATFORMS)
    
    if discord_token and "discord" in enabled_platforms:
        try:
            import nextcord
            intents = nextcord.Intents.default()
            intents.members = True
            intents.presences = True
            
            bot = nextcord.Client(intents=intents)
            
            def _dispatch(member):
                activity_name = None
                app_id = None
                for activity in member.activities:
                    if activity.type == nextcord.ActivityType.playing:
                        activity_name = activity.name
                        app_id = str(activity.application_id) if getattr(activity, "application_id", None) else None
                        break
                
                data = {
                    "user_id": str(member.id),
                    "state": activity_name if activity_name else ("Online" if str(member.status) != "offline" else "Offline"),
                    "app_id": app_id,
                    "avatar_url": str(member.display_avatar.with_size(1024).url) if member.display_avatar else None
                }
                hass.bus.async_fire(f"gaming_status_discord_{member.id}", data)
                
            @bot.event
            async def on_presence_update(before, after):
                _dispatch(after)
                
            @bot.event
            async def on_member_update(before, after):
                _dispatch(after)
                
            @bot.event
            async def on_ready():
                _LOGGER.info("Gaming Status Discord Bot Connected!")
                for guild in bot.guilds:
                    for member in guild.members:
                        _dispatch(member)
                        
            hass.loop.create_task(bot.start(discord_token))
            hass.data[DOMAIN]["discord_bot"] = bot
        except Exception as e:
            _LOGGER.error("Failed to setup Discord Bot: %s", e)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def _resolve_targets(player, platform):
        safe_owner = re.sub(r'[^a-z0-9_]', '_', player.lower().replace(" ", "_"))
        platforms = [platform] if platform else PLAYER_PLATFORMS
        sensors = hass.data[DOMAIN].get("platform_sensors", {})
        return [sensors[f"sensor.gaming_status_{safe_owner}_{p}"]
                for p in platforms
                if f"sensor.gaming_status_{safe_owner}_{p}" in sensors]

    async def _handle_rename(call):
        targets = _resolve_targets(call.data["player"], call.data.get("platform"))
        for target in targets:
            await target.async_rename_game(call.data["old_name"], call.data["new_name"])

    async def _handle_delete(call):
        targets = _resolve_targets(call.data["player"], call.data.get("platform"))
        for target in targets:
            await target.async_delete_game(call.data["game"])

    async def _handle_delete_session(call):
        platform = call.data.get("platform")
        targets = _resolve_targets(call.data["player"], platform)
        for target in targets:
            await target.async_delete_session(call.data["game"], call.data["start_time"], quiet_if_missing=platform is None)

    async def _handle_add_session(call):
        targets = _resolve_targets(call.data["player"], call.data["platform"])
        if not targets:
            _LOGGER.warning("Gaming Status: add_session couldn't resolve a sensor for player=%r platform=%r", call.data["player"], call.data["platform"])
            return
        await targets[0].async_add_session(call.data["game"], call.data["start_time"], call.data["end_time"])

    async def _handle_reassign_session(call):
        from_targets = _resolve_targets(call.data["from_player"], call.data.get("from_platform"))
        to_targets = _resolve_targets(call.data["to_player"], call.data["to_platform"])
        if not to_targets:
            _LOGGER.warning("Gaming Status: reassign_session couldn't resolve destination sensor for player=%r platform=%r", call.data["to_player"], call.data["to_platform"])
            return
        game, start_time = call.data["game"], call.data["start_time"]
        for source in from_targets:
            entry = source.get_session_entry(game, start_time)
            if entry is not None:
                # Add to the destination FIRST -- if this fails, the source is
                # untouched. Deleting first would risk losing the session
                # entirely if the destination add then failed.
                await to_targets[0].async_add_session(
                    entry["game"], entry["start_time"], entry["end_time"],
                    hero_art_url=entry.get("hero_art_url"), game_dominant_color=entry.get("game_dominant_color"),
                )
                await source.async_delete_session(game, start_time)
                return
        _LOGGER.warning("Gaming Status: reassign_session found no match for %r at %r for player=%r", game, start_time, call.data["from_player"])

    if not hass.services.has_service(DOMAIN, "rename_game"):
        hass.services.async_register(DOMAIN, "rename_game", _handle_rename, schema=vol.Schema({
            vol.Required("player"): str, vol.Optional("platform"): str,
            vol.Required("old_name"): str, vol.Required("new_name"): str,
        }))
    if not hass.services.has_service(DOMAIN, "delete_game"):
        hass.services.async_register(DOMAIN, "delete_game", _handle_delete, schema=vol.Schema({
            vol.Required("player"): str, vol.Optional("platform"): str,
            vol.Required("game"): str,
        }))
    if not hass.services.has_service(DOMAIN, "delete_session"):
        hass.services.async_register(DOMAIN, "delete_session", _handle_delete_session, schema=vol.Schema({
            vol.Required("player"): str, vol.Optional("platform"): str,
            vol.Required("game"): str, vol.Required("start_time"): str,
        }))
    if not hass.services.has_service(DOMAIN, "add_session"):
        hass.services.async_register(DOMAIN, "add_session", _handle_add_session, schema=vol.Schema({
            vol.Required("player"): str, vol.Required("platform"): str,
            vol.Required("game"): str, vol.Required("start_time"): str, vol.Required("end_time"): str,
        }))
    if not hass.services.has_service(DOMAIN, "reassign_session"):
        hass.services.async_register(DOMAIN, "reassign_session", _handle_reassign_session, schema=vol.Schema({
            vol.Required("from_player"): str, vol.Optional("from_platform"): str,
            vol.Required("game"): str, vol.Required("start_time"): str,
            vol.Required("to_player"): str, vol.Required("to_platform"): str,
        }))

    return True

async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change so entities rebuild."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if "notifier" in hass.data.get(DOMAIN, {}):
        try:
            await hass.data[DOMAIN]["notifier"].async_stop()
        except Exception as e:
            _LOGGER.error("Gaming Status failed to stop notifier cleanly: %s", e)
        
    if "discord_bot" in hass.data.get(DOMAIN, {}):
        try:
            await hass.data[DOMAIN]["discord_bot"].close()
        except Exception as e:
            _LOGGER.error("Gaming Status failed to close Discord bot cleanly: %s", e)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)