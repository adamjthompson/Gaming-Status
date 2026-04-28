![Gaming Status Logo](custom_components/gaming_status/brand/logo.png)

# 🎮 Gaming Status for Home Assistant

This is a powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, and custom PC clients into a single, clean dashboard sensor for each person in your household. I am NOT a programmer, but I AM a gamer, and I hope that other gamers can make good use of this project!

## ✨ Features
* **Unified Master Sensor:** Combines Xbox, PlayStation, Steam, and Custom PC clients into one clean "Master Status" sensor per person.
* **Custom PC Game Support:** Track non-platform games (like Epic Games, Minecraft, or Genshin Impact) using template funnels or binary sensors.
* **Smart Ghosting Protection:** Automatically prevents echo sessions (e.g., when the Windows Xbox app incorrectly broadcasts a Steam game).
* **Drop-Out Protection:** Built-in grace periods prevent a gamer from appearing "Offline" if their game crashes, they switch titles, or their internet briefly blips, keeping play sessions perfectly intact.
* **Playtime Analytics:** Automatically calculates session time, daily hours, and a rolling 7-day total for easy dashboard charting.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.
* **Advanced Exclusion Filtering:** Prevent media apps (Netflix, YouTube, Spotify) or background processes from triggering gaming statuses using the global exclusions list.
* **Zero-Bloat Cover Art:** Automatically fetches gorgeous, clean game covers from SteamGridDB and passes them to your dashboard via URL, ensuring your local HA storage never gets bloated with downloaded images.
* **"Last Seen" Memory:** When gamers go offline, the sensor retains their last played game and calculates exactly how long ago they were active (e.g., *Last seen 3h ago: Genshin Impact*).
* **Custom Avatars:** Automatically pulls live gamer pictures from platform APIs, with the option to easily override missing or incorrect images with your own local images.

## ⚠️ Prerequisites
This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have any necessary base integrations installed and working in Home Assistant:
* Official PlayStation Network Integration
* Official Steam Integration
* Official Xbox Integration

## 📥 Installation

### HACS (Recommended)
1. Open HACS in Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the URL to this repository and select **Integration** as the category.
4. Click **Download**.
5. **Restart Home Assistant.**

### Manual Installation
If you prefer not to use HACS, you can install the integration manually:

1. Download the latest release from the [GitHub Releases page](https://github.com/adamjthompson/Gaming-Status/releases). *(Note: Download the Source Code ZIP, not the main repository branch).*
2. Extract the downloaded ZIP file.
3. Locate the `custom_components/gaming_status/` folder inside the extracted files.
4. Copy that entire `gaming_status` folder into your Home Assistant `config/custom_components/` directory. *(If the `custom_components` folder does not exist, create it).*
5. **Restart Home Assistant.**
6. Follow the Configuration steps below to set up your `gaming_profiles.json` file.

## ⚙️ Configuration (Crucial Step)
Because every home setup is unique, this integration requires a manual configuration file to map your entities to the right gamer. All settings will be configured inside of `config/gaming_profiles.json`. It is recommended to rename the provided `example.profiles.json` and to **`gaming_profiles.json`** for use as a starting point.

### Obtaining a SteamGridDB API Key
To display beautiful, high-resolution game covers on your dashboard, this integration requires a free API key from SteamGridDB. If you skip this step, the integration will simply fall back to displaying the gamer's profile picture.

1. Go to [SteamGridDB.com](https://www.steamgriddb.com/).
2. Click **Login** in the top right corner and authenticate using your Steam account.
3. Click your profile picture in the top right and select **Preferences**.
4. Navigate to the **API** tab on the left menu.
5. Click **Generate API Key**.
6. Copy the string of letters and numbers generated. 

### API Key (STEAMGRIDDB_API_KEY)

Input your **SteamGridDB API Key** where indicated.

### User Profiles (GAMER_PROFILES)
This section maps a friendly display name to the underlying Home Assistant sensors tracking that person. It also holds user-specific rules.

**Platform Keys:** Add the entity IDs for Steam, Xbox, PlayStation, or custom sensors. You can include as few or as many as a user owns. *Note the following default platform integration sensor naming conventions:*

- **Steam:** sensor.steam_playername
- **Xbox:** sensor.playername_status
- **PlayStation:** sensor.playername_online_status

**Ghosted_by:** A list of master sensor IDs. If the current user is playing the exact same game as someone in this list, the current user's sensor will remain offline. This is useful for shared consoles or PCs to prevent duplicate tracking.

**Exclude_games:** A user-specific list of games or apps that should be completely ignored (case-insensitive).

```yaml
"GAMER_PROFILES": {
    "Player One": {
        "steam": "sensor.steam_player_one",
        "xbox": "sensor.player_one_status",
        "playstation": "sensor.player_one_online_status",
        "custom": "sensor.player_one_active_pc_game",
        "ghosted_by": ["sensor.player_two_steam"],
        "exclude_games": ["Genshin Impact", "Minecraft"]
    },
    "Player Two": {
        "xbox": "sensor.player_two_status",
        "steam": "sensor.player_two_steam"
    }
  }
```

### Game Title Overrides (GAME_TITLE_OVERRIDES)
This acts as a strict dictionary. If the integration detects an exact match with the key (the name on the left), it will permanently replace it with the value (the name on the right) before doing any API lookups or dashboard updates. This is perfect for shortening obnoxiously long official titles or for when cover art lookup fails due to a name mismatch.

```yaml
"GAME_TITLE_OVERRIDES": {
    "Grand Theft Auto V": "GTA V",
    "The Elder Scrolls V: Skyrim Special Edition": "Skyrim",
    "Call of Duty®: Modern Warfare® II": "Modern Warfare II"
  }
```

### Custom Covers (CUSTOM_COVER_MAP)
This allows you to bypass the SteamGridDB API entirely. If a game title matches a key in this list, the integration will immediately use the provided URL for the artwork. This is great for obscure games, custom emulators, or simply when you prefer a specific piece of fan art over the official cover.

*Note: URLs must point directly to an image file (e.g., .png, .jpg).*

```yaml
"CUSTOM_COVER_MAP": {
    "Marvel Rivals": "https://cdn2.steamgriddb.com/hero/a31d2779e08530d0b5fdbed368c735b4.png",
    "Super Smash Bros. Melee": "https://your-home-assistant-url.com/local/gaming_status/melee_cover.jpg"
  }
```

### Title Cleanups (TITLE_CLEANUPS)
This is a universal "scrubber." It takes a list of phrases and automatically deletes them from any game title it encounters. This is evaluated case-insensitively. It is the best way to handle dynamic "Rich Presence" statuses that console integrations append to games or to remove unnecessary word from game titles.

```yaml
"TITLE_CLEANUPS": [
    "Tom Clancy's",
    "Sid Meier's",
    "Marvel's",
    "Director's Cut",
    "Steam Edition",
    "Java Edition",
    "Open Network Test"
  ]
```

### Global Exclusions (GLOBAL_EXCLUSIONS)
This is a universal "ignore list." While **exclude_games** inside a user profile only applies to that specific person, GLOBAL_EXCLUSIONS applies to every single gamer on your Home Assistant instance. If any console or PC reports playing an app on this list, the integration will immediately force the sensor to report as "Offline." This is incredibly useful for preventing streaming apps, music players, or dashboard menus from padding out your gaming hours or sending false "Online" triggers.

*Note: This list is completely case-insensitive.*

```yaml
"GLOBAL_EXCLUSIONS": [
    "Home",
    "Netflix",
    "YouTube",
    "Hulu",
    "Amazon Prime Video",
    "Spotify",
    "Twitch"
  ]
```

##  Activating the Integration
Once your `gaming_profiles.json` file is configured and saved:
1. Go to **Settings** ➔ **Devices & Services** in Home Assistant.
2. Click **+ Add Integration**.
3. Search for **Gaming Status** and select it.
4. The integration will instantly read your `gaming_profiles.json` file and generate the master tracking sensors for your dashboard.
5. Look for the new master sensors named `sensor.XXXXXXXX.gaming_status`. Additionally, individual platform sensors will be created ending in `_playstation`, `_steam`, `_xbox`, and `_custom`, where applicable.

## What to Try Next!?
Once evrything is up and running, with sensors showing up from the integration, try loading up a game to make sure the online status is reflected in the master "_gaming_status" sensors. If they are working correctly, try some of the following!

- Add some sweet displays to your [dashboard](docs/dashboards.md#1-the-currently-playing-card), showing who's online and what they're playing
- Set up Discord or SMS [notifications](docs/notifications.md) for when users start and stop playing games
- Add a [graph](docs/dashboards.md#3-the-playtime-stats-chart) to chart weekly game time
- Add a [slideshow](docs/dashboards.md#2-cinematic-slideshow-with-player-avatars) to your wallpanel display to see what's being played
- Add [custom sensors](docs/templates.md) to track PC games not logged by Steam or Xbox
- Add a [sensor](docs/templates.md#3-the-is-anyone-gaming-binary-sensor-for-automations) to track whether or not anyone is gaming (useful for automations or contextual card display)

## 🛠️ Troubleshooting & FAQ
**The integration loads, but no sensors are created**
One possibility is a simple JSON formatting error in your `gaming_profiles.json` file. JSON is extremely strict and doesn't allow 'trailing commas' after the last item in a list. See example below:

**Bad JSON**
```yaml
"exclude_games": [
  "Genshin Impact",
  "Minecraft",  <-- THIS TRAILING COMMA BREAKS EVERYTHING
]
```

**Good JSON**
```yaml
"exclude_games": [
  "Genshin Impact",
  "Minecraft"
]
```

**My Master Sensor always says "Offline" even when I'm playing!**
99% of the time, this is a typo in your `gaming_profiles.json` file. 
1. Go to **Developer Tools ➔ States** and find your base platform sensor (e.g., `sensor.steam_765611...`).
2. Make sure the entity ID in your `gaming_profiles.json` matches that exact string letter-for-letter. 
3. Ensure the base official integrations (Steam, Xbox, PlayStation) are actually signed in and working. If the official Xbox sensor is broken, this integration has no data to read!
*(Note: Remember to restart Home Assistant after making any changes to `gaming_profiles.json`!)*

**The game cover art is missing or incorrect.**
The integration searches SteamGridDB for high-quality cover art based on the name of the game. If a publisher names a game weirdly on Xbox (like *"Call of Duty®: HQ - Cross-Gen Bundle"*), the search will fail.
- **The Fix:** Open your `gaming_profiles.json` and use the `GAME_TITLE_OVERRIDES` dictionary to map that messy title to a clean one (e.g., `"Call of Duty®: HQ - Cross-Gen Bundle": "Call of Duty"`). The integration will instantly find the right art!

**My Gamerpic / Avatar is blank or broken.**
Sometimes, official APIs (especially PlayStation) fail to pass the avatar image URL to Home Assistant. 
- **The Fix (Local Override):** You can force your own profile picture! Create a folder inside your Home Assistant `www` directory called `gaming_status`. Drop a `.png` or `.jpg` image in there using the format: `[platform]_[profile_name]_avatar.jpg`. 
- *Example:* If your profile name is "Player One" and you want an Xbox avatar, name the file `xbox_player_one_avatar.jpg` and the integration will automatically use it!

**My Custom PC Game (or Epic Game) isn't triggering.**
If you are using a PC companion app like HASS.Agent to track a running `.exe` file, it often reports the *number of running processes* rather than a simple "on/off" state. 
- **The Fix:** Ensure your Template Funnel Sensor checks for a number greater than zero, rather than just checking if the state is exactly '1'. *(See the [Templates Documentation](docs/templates.md) for the exact code to fix this).*

**I changed a game's title, but the old name is still showing.**
The Master Sensor caches history and states to prevent drop-outs. If things look stuck, simply restart Home Assistant to flush the cache and force it to rebuild from the current live data.