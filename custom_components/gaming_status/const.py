"""
Constants for Gaming Status
"""

DOMAIN = "gaming_status"

# --- CONFIGURATION ---
STEAMGRIDDB_API_KEY = "36e7d1db74add2af12840ce754f7f688"

# Manual Overrides: Map specific Game Names to direct Image URLs
# This skips the API lookup entirely for these titles.
STEAMGRIDDB_OVERRIDES = {
    "Dead Cells": "https://cdn2.steamgriddb.com/hero/daa181337f9fb25b5122ef9df114446d.png",
    "Genshin Impact": "https://cdn2.steamgriddb.com/hero/714aeac233808ffb2b01e3910edff2bc.png",
    "Warhammer 40,000: Space Marine II": "https://cdn2.steamgriddb.com/hero/2ac5c763221a1ca796638e111b3951a7.png",
    "Among Us":"https://cdn2.steamgriddb.com/hero/c5136a36b0bdea61cf049154a776ecc2.png",
    "Hades":"https://cdn2.steamgriddb.com/hero/1a23efcb39da8db7ca95ea8085d096a1.png",
    "Marvel Rivals":"https://cdn2.steamgriddb.com/hero/2fae0ac9d905a76bfcac629b32cc3197.png",
}

# --- PROFILE IMPORT LOGIC ---
try:
    from .profiles import (
        GAMER_PROFILES,
        TITLE_CLEANUPS,
        GAME_TITLE_OVERRIDES,
        GLOBAL_EXCLUSIONS,
        CUSTOM_GAME_MAP,
        CUSTOM_COVER_MAP
    )
except ImportError:
    from .profiles import GAMER_PROFILES, TITLE_CLEANUPS, GAME_TITLE_OVERRIDES
    GLOBAL_EXCLUSIONS = ["Home", "Xbox App", "Online", "Microsoft Store"]
    CUSTOM_GAME_MAP = {}
    CUSTOM_COVER_MAP = {}

# --- SETTINGS ---
RESET_HISTORY = False
GRACE_PERIOD_SECONDS = 300          # 5 Minutes - Standard Offline
AWAY_GRACE_PERIOD_SECONDS = 600     # 10 Minutes - Steam Away countdown time
GAME_TRANSITION_GRACE_SECONDS = 120 # 2 Minutes - Bridges small gaps to keep the session active
MIN_SESSION_DURATION = 300          # 5 Minutes - Sessions shorter than this are not counted

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