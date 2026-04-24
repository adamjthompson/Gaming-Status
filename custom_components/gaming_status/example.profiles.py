"""
Gaming Status Profiles Configuration
Rename this file to profiles.py and fill in your entity IDs.
"""

# ---------------------------------------------------------
# 1. GAMER PROFILES
# ---------------------------------------------------------
# Define the players you want to track and their gaming entity IDs.
# You can simply exclude any platforms a user does not have.
# The "custom" slot can be pointed to a PC sensor or Template Sensor.
# To exclude game tracking of certain games for a user - "exclude_games": ["Game Title", "Game Title", ...]

GAMER_PROFILES = {
    "Player One": {
        "steam": "sensor.steam_player_one",
        "xbox": "sensor.player_one_status",
        "playstation": "sensor.player_one_online_status",
        "custom": "sensor.player_one_active_pc_game", # This can be a Template Sensor that checks multiple binary sensors for PC games and outputs the active one, or "None"
        "ghosted_by": [], # E.g., ["sensor.player_two_gaming_status"] to prevent duplicate tracking. Useful for when a computer is shared and both users have a sensor that would track the same game.
        "exclude_games": ["Genshin Impact", "Minecraft"] # List of games to ignore for this user (case-insensitive)
    },
    "Player Two": {
        "xbox": "sensor.player_two_status"
    }
}

# ---------------------------------------------------------
# 2. GAME TITLE OVERRIDES
# ---------------------------------------------------------
# Rename messy source titles into clean dashboard titles or replace titles
# with alternatives for better consistency across various platforms.

GAME_TITLE_OVERRIDES = {
    "Minecraft Launcher": "Minecraft",
    "Minecraft Preview for Windows": "Minecraft",
    "Minecraft: Java Edition": "Minecraft",
    "Minecraft for Windows": "Minecraft",
    "Ghost Recon Breakpoint": "Ghost Recon: Breakpoint",
    "Ghost Recon Wildlands": "Ghost Recon: Wildlands",
    "RaceTheSun": "Race The Sun",
    "Call of Duty": "Call of Duty: Black Ops 7",
    "Army of TWO: TFD": "Army of Two: The 40th Day"
}

# ---------------------------------------------------------
# 3. GLOBAL EXCLUSIONS
# ---------------------------------------------------------
# A list of raw states or game names that should be completely 
# ignored by the tracker (case-insensitive). These are applied to all users and platforms.
# Useful for filtering out common false positives or generic states that aren't actual games.

GLOBAL_EXCLUSIONS = [
    "Home",
    "Online",
    "Xbox App",
    "YouTube",
    "Netflix",
    "Spotify",
    "Microsoft Store",
    "Home",
    "Store",
    "Xbox 360 Dashboard",
    "Setting up...",
    "Wallpaper Engine"
]

# ---------------------------------------------------------
# 4. CUSTOM GAME MAP (Legacy/Optional)
# ---------------------------------------------------------
# If you are using simple binary sensors (on/off) for PC games, 
# map the entity ID to the game name here. 

CUSTOM_GAME_MAP = {
    "sensor.pc_genshin_impact": "Genshin Impact"
}

# ---------------------------------------------------------
# 5. TITLE CLEANUP
# ---------------------------------------------------------
# Phrases to completely remove from game titles (case-insensitive)

TITLE_CLEANUPS = [
    "Tom Clancy's",
    "Sid Meier's",
    "Marvel's",
    "Director's Cut",
    "Steam Edition",
    "Java Edition",
    "Open Network Test"
]

# ---------------------------------------------------------
# 6. STEAMGRIDDB IMAGE OVERRIDES
# ---------------------------------------------------------
# Manually define image URLs for games that fail the API lookup or where a different image is preferred

STEAMGRIDDB_OVERRIDES = {
    "Marvel Rivals": "https://cdn2.steamgriddb.com/hero/a31d2779e08530d0b5fdbed368c735b4.png",
    "Sky: Children of the Light": "https://cdn2.steamgriddb.com/hero/a00448af12a60bcbff48b1a698280558.jpg",
    "Race The Sun": "https://cdn2.steamgriddb.com/hero/cfa0860e83a4c3a763a7e62d825349f7.png",
    "The Division 2": "https://cdn2.steamgriddb.com/hero/7b41bfa5085806dfa24b8c9de0ce567f.jpg",
    "Elite Dangerous": "https://cdn2.steamgriddb.com/hero/9909794d52985cbc5d95c26e31125d1a.png",
    "Warhammer 40,000: Space Marine II": "https://cdn2.steamgriddb.com/hero/2c9cd37eba5104fc855083c41e534298.png",
    "Wheelchair Wizards": "https://cdn2.steamgriddb.com/hero/3bf274e5539bdfda17d4221de3429954.png",
    "Keeper": "https://cdn2.steamgriddb.com/hero/69cd452b668474280029279566cf3de4.jpg",
    "Borderlands 3": "https://cdn2.steamgriddb.com/hero/8f14e45fceea167a5a36dedd4bea2543.png",
    "Tiny Tina's Wonderlands": "https://cdn2.steamgriddb.com/hero/610ed4481667e4e4bc31f7c55757a052.jpg"
}