![Gaming Status Logo](custom_components/gaming_status/brand/logo.png)

# 🎮 Gaming Status for Home Assistant

This is a powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, and custom PC clients into a single, clean dashboard sensor for each person in your household. It's super useful if you want to be able to track who is online, what they're playing, and for how long. 

I started developing this as a way to have persistent sensors since I was annoyed that the Xbox and Steam sesnors would regularly flutter between online/offline status, making notifications and my gaming dashboard unreliable. This evolved into making master sensors instead of simply tracking each platform independently. Over time, this has grown into a much more complex integration that now tracks game time, last game played, provides cover art, allows for rich notifications through Discord and much, much more.

Some of the key features are listed below.

## ✨ Features
* **Unified Master Sensor:** Combines Xbox, PlayStation, Steam, and Custom PC clients into one clean "Master Status" sensor per person.
* **Online/Offline Notifications** Receive Discord, SMS, and/or Mobile notifications when users start or finish playing a game.
* **Parental Controls:** Track daily playtime and recieve notifications or trigger an automation or script when a limit or curfew is reached.
* **Custom PC Game Support:** Track non-platform games (like Epic Games, Minecraft, or Genshin Impact) using template funnels or binary sensors.
* **Smart Ghosting Protection:** Automatically prevents echo sessions (e.g., when the Windows Xbox app incorrectly broadcasts a Steam game).
* **Drop-Out Protection:** Built-in grace periods prevent a gamer from appearing "Offline" if their game crashes, they switch titles, or their internet briefly blips, keeping play sessions perfectly intact.
* **Playtime Analytics:** Automatically calculates session time, daily hours, and a rolling 7-day total for easy dashboard charting.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.
* **Advanced Exclusion Filtering:** Prevent media apps (Netflix, YouTube, Spotify) or background processes from triggering gaming statuses using a global exclusions list.
* **Zero-Bloat Cover Art:** Automatically fetches gorgeous, clean game hero images from SteamGridDB and passes them to your dashboard via URL, ensuring your local HA storage never gets bloated with downloaded images.
* **"Last Seen" Memory:** When gamers go offline, the sensor retains their last played game and calculates exactly how long ago they were active (e.g., *Last seen 3h ago: Genshin Impact (1h 37m)*).
* **Custom Avatars:** Automatically pulls live gamer pictures from platform APIs, with the option to easily override missing or incorrect images with your own local images.

## ⚠️ Prerequisites
This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have any necessary base integrations installed and working in Home Assistant. Not all are required, but you'll need at least ONE installed:
* [Official PlayStation Network Integration](https://www.home-assistant.io/integrations/playstation_network)
* [Official Steam Integration](https://www.home-assistant.io/integrations/steam_online)
* [Official Xbox Integration](https://www.home-assistant.io/integrations/xbox)
* [SteamGridDB API Key](https://www.steamgriddb.com/) (for cover art) - *This is not 100% REQUIRED, but it is HIGHLY recommended!*

## Recommended
While not required for functionality, I recommend installing the following HACS integrations for the most robust dashboard cards:
* [Gaming Status Cards](https://github.com/adamjthompson/Gaming-Status-Cards) - Companion dashboard cards, so you don't have to make your own
* [ApexCharts Card](https://github.com/RomRider/apexcharts-card) - For the stats graph
* [HASS.Agent](https://www.hass-agent.io/2.2/getting-started/installation/#installing-hassagent) - Install both the PC app and the integration for Custom PC sensors

### Obtaining a SteamGridDB API Key
To display beautiful, high-resolution game covers on your dashboard, this integration requires a free API key from SteamGridDB.

1. Go to [SteamGridDB.com](https://www.steamgriddb.com/).
2. Click **Login** in the top right corner and authenticate using your Steam account.
3. Click your profile picture in the top right and select **Preferences**.
4. Navigate to the **API** tab on the left menu.
5. Click **Generate API Key**.
6. Copy the string of letters and numbers generated. 

## 📥 Installation

**Method 1: HACS (Recommended)**
Installation is easiest via the [Home Assistant Community Store (HACS)](https://hacs.xyz/), which is the best place to get third-party integrations for Home Assistant. Once you have HACS set up, simply click the button below (requires My Homeassistant configured) or follow the [instructions for adding a custom repository](https://hacs.xyz/docs/faq/custom_repositories) and then the integration will be available to install like any other.

[![Open HACS Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=adamjthompson&repository=Gaming-Status&category=integration)

**Method 2: Manual**
Copy the `gaming_status` folder into your `custom_components` folder and restart Home Assistant.

## ⚙️ Configuration

Go to Settings / Devices & Services and press the Add Integration button, or click the shortcut button below (requires My Homeassistant configured):

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=gaming_status)

Gaming Status is configured entirely through the Home Assistant UI. **There is no YAML or JSON configuration required.** *(Note: If you are upgrading from an older version, your `gaming_profiles.json` file will be automatically migrated to the new database and can be safely deleted).*

### Initial Setup
1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **+ Add Integration** and search for **Gaming Status**.
3. You will be prompted to enter a **SteamGridDB API Key** (Optional). This is highly recommended to automatically fetch beautiful hero art for your games. You can get a free key [here](https://www.steamgriddb.com/profile/api).
4. Click Submit. 

### Options & Features
To configure your players, notifications, and rules, click the **Configure** button (gear icon) on the Gaming Status integration card. This opens the main configuration hub, which is divided into five sections:

#### 1. Manage Players
Add, edit, or delete the gamers in your household.
* **Platform Sensors:** When adding a player, you simply select their respective integration sensors from the dropdowns. The integration will automatically filter your entities to show the correct Steam, Xbox (`_status`), and PlayStation (`_online_status`) sensors.
* **Player Details:** After adding a player, you can configure:
  * **Session Notifications:** Select notification methods for when this specific player starts or stops gaming. *Note: These must be configured under Notifications.*
  * **Ghosted-by:** Enter comma-separated entity IDs (e.g., `sensor.player_two_steam`) to hide this player's status from someone else's view.
  * **Exclude Games:** Comma-separated list of games to ignore for this specific player.

#### 2. Notifications
Manage where your [gaming alerts and weekly reports](docs/notifications.md) are sent.
* **Add Notification:** Map a friendly name (e.g., "Dad's Phone") to an existing Home Assistant `notify.` service. It fully supports standard mobile app notifications, Discord, and SMS.
* **Weekly Report:** Send a beautifully formatted summary of everyone's weekly playtime and top games to your selected endpoints on a specific day and time.

#### 3. Parental Controls
Set automated rules based on accumulated playtime or time of day.
* **Screen Time:** Set distinct weekday and weekend daily minute limits.
* **Curfew:** Set distinct weekday and weekend cutoff times (e.g., 22:00).
* **Reminder Frequency:** Set how often to repeat the action.
* **Actions:** When a limit is reached, you can automatically trigger a notification, run a Home Assistant script, or fire an automation (like turning off a smart plug or TV).

#### 4. Advanced
Fine-tune game names, covers, and exclusions using simple, comma-separated lists.
* **Game Title Overrides:** Clean up messy or lengthy names. Format as `raw name: display name`.
  * *Example:* `Minecraft Launcher: Minecraft, Grand Theft Auto V: GTA V`
* **Custom Cover Map:** Manually assign cover art for custom games or unrecognized titles. Fully supports web URLs and Home Assistant `/local/` paths.
  * *Example:* `Marvel Rivals: /local/covers/marvel.png, Halo: https://...`
* **Title Cleanups:** A list of strings to automatically strip from game names.
  * *Example:* `Tom Clancy's, Sid Meier's`
* **Global Exclusions:** Games or apps that should be universally ignored by the tracker. 
  * *Example:* `Home, YouTube, Netflix, Xbox App`

#### 5. Global Settings
These variables control how the integration handles network drops, game crashes, and short sessions across all players.
* **Grace Periods:** Bridges the gap between game launches or brief network drops so your session doesn't falsely show as "Offline". 
* **Minimum Session Duration:** Prevents quickly opening and closing a game (like launching a launcher) from cluttering your play history.
* **Reset History:** A toggle to clear all accumulated daily/weekly session history upon the next Home Assistant restart. *Use with caution!*

### Entities

Upon restart, the integration will instantly read your settings and generate the master tracking sensors for your dashboard. Look for the new master sensors named `sensor.XXXXX.gaming_status`. Additionally, individual platform sensors will be created ending in `_playstation`, `_steam`, `_xbox`, and `_custom`, where applicable.

| Entity | Type | Description |
| ---| --- | --- |
| sensor.XXXXX_steam | Sensor | Steam sensor for each added profile |
| sensor.XXXXX_xbox | Sensor | Xbox sensor for each added profile |
| sensor.XXXXX_playstation | Sensor | PlayStation sensor for each added profile |
| sensor.XXXXX_gaming_status | Sensor | Master sensor for each added profile that combines all added platforms into one "Online/Offline" status |
| sensor.XXXXX_daily_gaming_hours_chart | Sensor | Daily game time, tracked in 0.0 h format |
| binary_sensor.anyone_gaming | Binary Sensor | Useful for showing or hiding cards |

### Attributes for Master Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_gaming_status` sensors provide the following attibutes
| Attribute | Example | Description |
| --- | --- | --- |
| secondary | Last seen 12h ago: Marvel Rivals (58m) | Current state or the time elapsed and session duration of the last played game |
| active_platform | Steam | Platform that is currently active or most recently used
| game_cover_art |   | Hero image URL for currently active game |
| last_played_game | Marvel Rivals | Title of the most recent game detected across all tracked platforms | 
| last_online_valid_timestamp |   | ISO 8601 timestamp of the last time detected online |
| total_daily_hours | 1.0 | Hours for the current calendar day |
| total_weekly_hours | 8.54 | Hours for the current calendar week | 
| rolling_weekly_hours | 3.69 | Hours over a dynamic, trailing 7-day window | 
| total_weekly_hours_last_week | 16.94 | Hours recorded during the previous calendar week | 
| entity_picture |   | URL of the player's avatar fetched from the active platform |
| icon | mdi:steam | Dynamic icon to match the active platform |
| friendly_name | Adam Gaming Status | Display name for this player's entity |

### Attributes for Platform Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_steam`, `*_xbox`, and `*_playstation` sensors provide the following attibutes
| Attribute | Example | Description |
| --- | --- | --- |
| secondary | Last seen 5h ago: Halo 3 (58m) | Current state or the time elapsed and session duration of the last played game
| daily_play_time | 8345 | Playtime for the current calendar day (raw seconds) |
| daily_play_time_formatted | 2h19m  | Current day's total playtime (human-readable string) |
| daily_play_time_yesterday| 12673 | Playtime during the previous calendar day (raw seconds) |
| weekly_play_time | 22040 | Playtime across the current calendar week (raw seconds) |
| weekly_play_time_formatted | 6h 7m | Current week's total playtime (human-readable string) |
| weekly_play_time_last_week | 21310 | Playtime during the previous calendar week (raw seconds) |
| last_reset_date | 2026-04-30 | Calendar date string indicating the last time the daily playtime trackers were reset to zero |
| last_weekly_reset | 2026-17 | Calendar week string (Year-Week) indicating the last time the weekly playtime trackers were reset to zero
| last_played_game | Halo 3 | Title of the most recently closed game detected on this specific platform
| current_game | Genshin Impact | Title of the game actively being played right now, or null if the user is offline
| game_cover_art |   | URL of the hero image for the actively played game, used for UI display
| cached_game_cover |   | Internal backup URL for the last known game cover art, used during grace periods or offline states
| entity_picture |   | URL of the player's official profile avatar fetched from the platform's network |
| icon | mdi:microsoft-xbox | Icon to match the current platform
| friendly_name | Adam Xbox | Display name for this player's entity
| play_start_time |   | The exact timestamp when the current, active gaming session began |
| timer_status | Stopped (Offline) | Current state of the internal playtime stopwatch (Running, Paused, or Stopped) |
| last_online_valid_timestamp |    | ISO 8601 timestamp of the last time detected online
| rolling_weekly_hours | 2.49 | Accumulated playtime in hours calculated over a dynamic, trailing 7-day window |
| last_session_play_time | 3533 | The total duration in seconds of the most recently completed gaming session (raw seconds) |
| temp_offline_start |   | The exact timestamp when a background grace period was triggered after temporarily losing the game state

## ❓ What Next?
Once everything is up and running (with sensors showing up from the integration), try playing a game for at least 5 minutes to make sure the online status is reflected in the master "_gaming_status" sensors. *Note that, by default, sessions shorter than 300 seconds (5 minutes) are discarded and do not count toward the total playtime hours.* If the sensors are working correctly, try some of the following! If not, see the [troubleshooting](docs/troubleshooting.md) documentation.

- Add some sweet displays to your [dashboard](https://github.com/adamjthompson/Gaming-Status-Cards#1-gaming-status---list), showing who's online and what they're playing
- Set up Discord, SMS, and/or Mobile [notifications](docs/notifications.md) for when users start and stop playing games
- Add a [slideshow](https://github.com/adamjthompson/Gaming-Status-Cards#2-gaming-status---slideshow) to your dashboard or wallpanel display to see what's being played
- Add a [graph](https://github.com/adamjthompson/Gaming-Status-Cards#3-gaming-status---chart) to chart weekly game time
- Add [custom sensors](docs/advanced.md#tracking-standalone-pc-games-hassagent-setup) to track PC games not logged by Steam or Xbox
- Use a [sensor](docs/advanced.md#the-is-anyone-gaming-binary-sensor-for-automations) to track whether or not anyone is gaming (useful for automations or contextual card display)
- Check out other [advanced setup options](docs/advanced.md) for features like preventing tracking of games by the wrong players and per-user game exclusions
