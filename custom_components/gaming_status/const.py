"""Constants for Gaming Status."""

DOMAIN = "gaming_status"
CONF_STEAMGRIDDB_API_KEY = "steamgriddb_api_key"
CONF_RAWG_API_KEY = "rawg_api_key"
CONF_DISCORD_TOKEN = "discord_bot_token"
CONF_DISCORD_SERVER = "discord_server_id"

# --- Default values ---
DEFAULT_RESET_HISTORY = False
DEFAULT_GRACE_PERIOD_SECONDS = 300
DEFAULT_AWAY_GRACE_PERIOD_SECONDS = 600
DEFAULT_GAME_TRANSITION_GRACE_SECONDS = 120
DEFAULT_MIN_SESSION_DURATION = 300
DEFAULT_MASTER_HANDOFF_GRACE_SECONDS = 300

# Max number of completed sessions retained per sensor for the "recent_sessions" history log
MAX_RECENT_SESSIONS = 20

# ZOMBIE ATTRIBUTE CLEANUP
ZOMBIE_ATTRIBUTES = ["grace_period_active", "xbox_last_seen_game", "debug_sync"]

PLATFORM_CONFIG = {
    "playnite": {
        "icon": "mdi:controller", 
        "name_suffix": "Playnite", 
        "group": "PC"
    },
    "custom": {
        "icon": "mdi:gamepad-square", 
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
        "icon": "mdi:gamepad-variant", 
        "name_suffix": "Discord", 
        "group": "PC"
    },
}

PLATFORM_PRIORITY = ["custom", "steam", "xbox", "playstation", "playnite", "discord"]
PLAYER_PLATFORMS = ["custom", "steam", "xbox", "playstation", "playnite", "discord"]

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
OPT_REMOVE_DISABLED_SENSORS = "remove_disabled_sensors"
DEFAULT_REMOVE_DISABLED_SENSORS = False
OPT_GRACE_PERIOD = "grace_period_seconds"
OPT_AWAY_GRACE_PERIOD = "away_grace_period_seconds"
OPT_TRANSITION_GRACE = "game_transition_grace_seconds"
OPT_MIN_SESSION = "min_session_duration"
OPT_SAME_GAME_PREFIX_WORDS = "same_game_prefix_words"
DEFAULT_SAME_GAME_PREFIX_WORDS = 2
OPT_MASTER_HANDOFF_GRACE = "master_handoff_grace_seconds"

OPT_ENABLED_PLATFORMS = "enabled_platforms"
DEFAULT_ENABLED_PLATFORMS = ["steam", "xbox", "playstation", "discord", "custom", "playnite"]
OPT_ENABLE_NOTIFICATIONS = "enable_notifications"
DEFAULT_ENABLE_NOTIFICATIONS = False
OPT_ENABLE_PARENTAL = "enable_parental"
DEFAULT_ENABLE_PARENTAL = False

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

# ---------------------------------------------------------------------------
# Content/age rating thresholds (parental "ratings" rule)
# ---------------------------------------------------------------------------
# Stored value is a board-agnostic numeric age floor, not an ESRB letter, so
# the same options keep working unchanged if PEGI/other boards are added later.
RATING_THRESHOLD_OPTIONS = [
    (0, "Ages 3+ (Everyone)"),
    (10, "Ages 10+ (Everyone 10+)"),
    (13, "Ages 13+ (Teen)"),
    (17, "Ages 17+ (Mature)"),
    (18, "Ages 18+ (Adults Only)"),
]

# Manual per-game rating overrides (Advanced Settings), for games the rating
# provider has no data for. Free-text entry uses these short ESRB-style codes,
# which get resolved to the same board-agnostic age floor used everywhere else.
OPT_RATING_OVERRIDES = "rating_overrides"
RATING_OVERRIDE_CODES = {"E": 0, "E10": 10, "T": 13, "M": 17, "AO": 18}