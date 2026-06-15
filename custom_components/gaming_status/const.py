"""Constants for Gaming Status."""

DOMAIN = "gaming_status"
CONF_STEAMGRIDDB_API_KEY = "steamgriddb_api_key"

# --- Default values ---
DEFAULT_RESET_HISTORY = False
DEFAULT_GRACE_PERIOD_SECONDS = 300
DEFAULT_AWAY_GRACE_PERIOD_SECONDS = 600
DEFAULT_GAME_TRANSITION_GRACE_SECONDS = 120
DEFAULT_MIN_SESSION_DURATION = 300

# ZOMBIE ATTRIBUTE CLEANUP
ZOMBIE_ATTRIBUTES = ["grace_period_active", "xbox_last_seen_game", "debug_sync"]

PLATFORM_CONFIG = {
    "custom": {
        "icon": "mdi:controller", 
        "name_suffix": "Custom", 
        "group": "PC"
    },
    "steam": {
        "icon": "mdi:steam", 
        "name_suffix": "Steam", 
        "group": "PC"
    },
    "xbox": {
        "icon": "mdi:microsoft-xbox",
        "name_suffix": "Xbox",
        "idle_states": ["Home", "Xbox App", "Online", "Microsoft Store"],
        "group": "Xbox"
    },
    "playstation": {
        "icon": "mdi:sony-playstation", 
        "name_suffix": "PlayStation", 
        "group": "PlayStation"
    },
    "discord": {
        "icon": "mdi:controller", 
        "name_suffix": "Discord", 
        "group": "PC"
    },
}

PLATFORM_PRIORITY = ["discord", "custom", "steam", "playstation", "xbox"]
PLAYER_PLATFORMS = ["steam", "xbox", "playstation", "custom", "discord"]

# ---------------------------------------------------------------------------
# Menu option identifiers
# ---------------------------------------------------------------------------
MENU_GLOBAL_SETTINGS = "global_settings"
MENU_MANAGE_PLAYERS = "manage_players"
MENU_NOTIFICATIONS = "notifications"
MENU_PARENTAL = "parental_controls"
MENU_CUSTOM_ARTWORK = "custom_artwork"
MENU_ADVANCED = "advanced"

# ---------------------------------------------------------------------------
# Option keys
# ---------------------------------------------------------------------------
OPT_RESET_HISTORY = "reset_history"
OPT_GRACE_PERIOD = "grace_period_seconds"
OPT_AWAY_GRACE_PERIOD = "away_grace_period_seconds"
OPT_TRANSITION_GRACE = "game_transition_grace_seconds"
OPT_MIN_SESSION = "min_session_duration"

OPT_PLAYERS = "players"

OPT_ENDPOINTS = "notification_endpoints"
OPT_WEEKLY_REPORT = "weekly_report"
OPT_NOTIFY_ARTWORK = "notify_artwork"

OPT_PARENTAL = "parental_controls"

OPT_TITLE_OVERRIDES = "game_title_overrides"
OPT_TITLE_CLEANUPS = "title_cleanups"
OPT_GLOBAL_EXCLUSIONS = "global_exclusions"
OPT_USE_CACHE = "use_local_cache"
DEFAULT_USE_CACHE = True
OPT_EXTRACT_COLOR = "extract_colors"
DEFAULT_EXTRACT_COLOR = True
OPT_CACHE_MAX_FILES = "cache_max_files"
DEFAULT_CACHE_MAX_FILES = 200
OPT_CACHE_MAX_DAYS = "cache_max_days"
DEFAULT_CACHE_MAX_DAYS = 30

OPT_CUSTOM_GRID = "custom_grid"
OPT_CUSTOM_HERO = "custom_hero"
OPT_CUSTOM_LOGO = "custom_logo"
OPT_CUSTOM_ICON = "custom_icon"
OPT_CUSTOM_COLORS = "custom_colors"

OPT_DISCORD_COLORS = "discord_colors"

DISCORD_COLOR_DEFAULT = "default"
DISCORD_COLOR_PLATFORM = "platform"
DISCORD_COLOR_GAME = "game"
DISCORD_COLOR_CUSTOM = "custom"