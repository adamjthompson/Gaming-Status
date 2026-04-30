"""
Constants for Gaming Status
"""

DOMAIN = "gaming_status"
CONF_STEAMGRIDDB_API_KEY = "steamgriddb_api_key"

# --- SETTINGS ---
# These are the safety fallbacks if the user's JSON file is missing the GLOBAL_SETTINGS block
DEFAULT_RESET_HISTORY = False
DEFAULT_GRACE_PERIOD_SECONDS = 300          # 5 Minutes - Standard Offline
DEFAULT_AWAY_GRACE_PERIOD_SECONDS = 600     # 10 Minutes - Steam Away countdown time
DEFAULT_GAME_TRANSITION_GRACE_SECONDS = 120 # 2 Minutes - Bridges small gaps to keep the session active
DEFAULT_MIN_SESSION_DURATION = 300          # 5 Minutes - Sessions shorter than this are not counted

# [V106] ZOMBIE ATTRIBUTE CLEANUP
ZOMBIE_ATTRIBUTES = ["grace_period_active", "xbox_last_seen_game", "debug_sync"]

PLATFORM_CONFIG = {
    "custom": {"icon": "mdi:gamepad-variant", "name_suffix": "PC"},
    "steam": {"icon": "mdi:steam", "name_suffix": "Steam"},
    "xbox": {
        "icon": "mdi:microsoft-xbox",
        "name_suffix": "Xbox",
        "idle_states": ["Home", "Xbox App", "Online", "Microsoft Store"]
    },
    "playstation": {"icon": "mdi:sony-playstation", "name_suffix": "PlayStation"}
}

PLATFORM_PRIORITY = ["custom", "steam", "playstation", "xbox"]
