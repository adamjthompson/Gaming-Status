"""Constants for Gaming Status."""

DOMAIN = "gaming_status"
CONF_STEAMGRIDDB_API_KEY = "steamgriddb_api_key"
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
MENU_OVERRIDES = "overrides_exclusions"
MENU_ACHIEVEMENTS_RATINGS = "achievements_ratings"
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

OPT_ENABLE_PS3_TRACKING = "enable_ps3_tracking"
DEFAULT_ENABLE_PS3_TRACKING = False
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

# ---------------------------------------------------------------------------
# Native platform achievement/trophy/rating enrichment (current game only,
# never a full library -- see steam_client.py/psn_client.py). Opt-in, off by
# default: existing installs must not start any new API traffic (especially
# a brand-new PSN OAuth session) just from updating Gaming Status.
# ---------------------------------------------------------------------------
OPT_ENABLE_PLATFORM_ENRICHMENT = "enable_platform_enrichment"
DEFAULT_ENABLE_PLATFORM_ENRICHMENT = False

# Manual override credentials -- only used when the matching official
# integration (steam_online/playstation_network) isn't installed/configured,
# or the user explicitly wants a different credential than the one already
# in use there. Stored in entry.data like the other API keys.
CONF_STEAM_ACHIEVEMENTS_API_KEY_OVERRIDE = "steam_achievements_api_key_override"
CONF_PSN_NPSSO_OVERRIDE = "psn_npsso_override"

OPT_ACHIEVEMENT_RECHECK_SECONDS = "achievement_recheck_seconds"
DEFAULT_ACHIEVEMENT_RECHECK_SECONDS = 900
MIN_ACHIEVEMENT_RECHECK_SECONDS = 300

# Full-library scan (every game ever played, not just the current one) --
# a much heavier, separately opt-in feature nested under platform
# enrichment above, since it reuses the same resolved credentials but adds
# its own scheduled scan + new sensors. Off by default, same reasoning.
OPT_ENABLE_LIBRARY_SCAN = "enable_library_scan"
DEFAULT_ENABLE_LIBRARY_SCAN = False
OPT_LIBRARY_SCAN_INTERVAL_HOURS = "library_scan_interval_hours"
DEFAULT_LIBRARY_SCAN_INTERVAL_HOURS = 12
MIN_LIBRARY_SCAN_INTERVAL_HOURS = 1
MAX_LIBRARY_SCAN_INTERVAL_HOURS = 24

# HA core's official steam_online integration stores the raw Steam Web API
# key at entry.data[homeassistant.const.CONF_API_KEY] -- safe to import
# directly since CONF_API_KEY is a generic core constant. Its own domain,
# though, is only ever referenced as a literal string (never imported),
# since "import homeassistant.components.steam_online" would fail hard if
# that integration isn't installed at all.
HA_STEAM_ONLINE_DOMAIN = "steam_online"

# HA core's official playstation_network integration stores the NPSSO at
# entry.data["npsso"] -- confirmed live against HA core's dev branch source
# (homeassistant/components/playstation_network/const.py: CONF_NPSSO =
# "npsso"). Kept as a literal (not imported) for the same reason as above --
# this domain may not be installed at all.
HA_PLAYSTATION_NETWORK_DOMAIN = "playstation_network"
HA_PSN_NPSSO_KEY = "npsso"

# HA core's official xbox integration is OAuth2-based (config_entry_oauth2_flow),
# with the token stored at entry.data["token"] (standard shape). Reused via the
# public config_entry_oauth2_flow.async_get_config_entry_implementation() +
# OAuth2Session() helpers -- the same ones the xbox component calls on itself --
# never entry.runtime_data. Kept as a literal for the same "may not be
# installed" reason as the other HA_*_DOMAIN constants above.
HA_XBOX_DOMAIN = "xbox"

# Steam Web API -- official, static key, no OAuth.
STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_API_BASE = "https://store.steampowered.com/api"

# PSN Trophy/Catalog API -- unofficial but stable, community-documented
# (https://andshrew.github.io/PlayStation-Trophies/#/APIv2). Base URLs
# confirmed live against psnawp_api's own endpoints.py (the library HA
# core's official playstation_network integration itself depends on).
PSN_AUTH_BASE = "https://ca.account.sony.com/api/authz/v3/oauth"
PSN_TROPHY_API_BASE = "https://m.np.playstation.com/api/trophy/v1"
PSN_CATALOG_API_BASE = "https://m.np.playstation.com/api/catalog/v2/titles"
PSN_PRESENCE_API_BASE = "https://m.np.playstation.com/api/userProfile/v2/internal/users"
PSN_PROFILE_BASE = "https://m.np.playstation.com/api/userProfile/v1/internal/users"
PSN_LEGACY_PROFILE_BASE = "https://us-prof.np.community.playstation.net/userProfile/v1/users"
PSN_OAUTH_CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
PSN_OAUTH_BASIC_AUTH_HEADER = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
PSN_OAUTH_SCOPE = "psn:mobile.v2.core psn:clientapp"
PSN_OAUTH_REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"

RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS = 20
STEAM_RATE_LIMIT_CAPACITY = 20
STEAM_RATE_LIMIT_PER_SECOND = 4
PSN_RATE_LIMIT_CAPACITY = 15
PSN_RATE_LIMIT_PER_SECOND = 250 / 900  # ~250/15min effective ceiling

# SteamGridDB pacing for the full-library scan's remote-URL-only art lookup
# (utils.fetch_game_grid_urls_remote) -- matches Trophy Hub's own already-
# proven pacing for the same API, since a library scan can mean hundreds of
# lookups in one pass.
STEAMGRIDDB_RATE_LIMIT_CAPACITY = 5
STEAMGRIDDB_RATE_LIMIT_PER_SECOND = 2