"""Constants for Gaming Status."""

DOMAIN = "gaming_status"
CONF_STEAMGRIDDB_API_KEY = "steamgriddb_api_key"

# --- Options keys (stored in config_entry.options) ---
# Global settings
OPT_RESET_HISTORY = "reset_history"
OPT_GRACE_PERIOD = "grace_period_seconds"
OPT_AWAY_GRACE_PERIOD = "away_grace_period_seconds"
OPT_TRANSITION_GRACE = "game_transition_grace_seconds"
OPT_MIN_SESSION = "min_session_duration"

# Players  (stored as a JSON string of a dict keyed by player name)
OPT_PLAYERS = "players"

# Notifications
OPT_ENDPOINTS = "notification_endpoints"   # JSON string: {id: {name, service, data}}
OPT_WEEKLY_REPORT = "weekly_report"        # JSON string: {enabled, day, time, destinations}

# Parental controls  (JSON string: {player_name: {screen_time: {...}, curfew: {...}}})
OPT_PARENTAL = "parental_controls"

# Advanced
OPT_TITLE_OVERRIDES = "game_title_overrides"   # JSON string: {raw: display}
OPT_CUSTOM_COVERS = "custom_cover_map"          # JSON string: {game_name: url}
OPT_TITLE_CLEANUPS = "title_cleanups"           # JSON string: ["Tom Clancy's", ...]
OPT_GLOBAL_EXCLUSIONS = "global_exclusions"     # JSON string: ["Home", "YouTube", ...]

# --- Default values ---
DEFAULT_RESET_HISTORY = False
DEFAULT_GRACE_PERIOD_SECONDS = 300
DEFAULT_AWAY_GRACE_PERIOD_SECONDS = 600
DEFAULT_GAME_TRANSITION_GRACE_SECONDS = 120
DEFAULT_MIN_SESSION_DURATION = 300

# [V106] ZOMBIE ATTRIBUTE CLEANUP
ZOMBIE_ATTRIBUTES = ["grace_period_active", "xbox_last_seen_game", "debug_sync"]

PLATFORM_CONFIG = {
    "custom": {"icon": "mdi:gamepad-variant", "name_suffix": "PC"},
    "steam": {"icon": "mdi:steam", "name_suffix": "Steam"},
    "xbox": {
        "icon": "mdi:microsoft-xbox",
        "name_suffix": "Xbox",
        "idle_states": ["Home", "Xbox App", "Online", "Microsoft Store"],
    },
    "playstation": {"icon": "mdi:sony-playstation", "name_suffix": "PlayStation"},
}

PLATFORM_PRIORITY = ["custom", "steam", "playstation", "xbox"]
PLAYER_PLATFORMS = ["steam", "xbox", "playstation", "custom"]

# Menu option identifiers
MENU_GLOBAL_SETTINGS = "global_settings"
MENU_MANAGE_PLAYERS = "manage_players"
MENU_NOTIFICATIONS = "notifications"
MENU_PARENTAL = "parental_controls"
MENU_ADVANCED = "advanced"