![Gaming Status Logo](custom_components/gaming_status/brand/logo.png)

# 🎮 Gaming Status for Home Assistant

This is a powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, and custom PC clients into a single, clean dashboard sensor for each person in your household. It's super useful if you want to be able to track who is online, what they're playing, and for how long. 

I started developing this as a way to have persistent sensors since I was annoyed that the Xbox and Steam sensors would regularly flutter between online/offline status, making notifications and my gaming dashboard unreliable. This evolved into making master sensors instead of simply tracking each platform independently. Over time, this has grown into a much more complex integration that now tracks game time, records the last game played, provides cover art, allows for rich notifications through Discord and much, much more.

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
* **Customizable Cover Art:** Automatically fetches gorgeous images from SteamGridDB and passes them to your dashboard via URL and/or caches them locally for fast updates.
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
* [ApexCharts Card](https://github.com/RomRider/apexcharts-card) - For the stats and donut graphs
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
* **Weekly Report:** Send a beautifully formatted summary of everyone's weekly playtime and top games to your selected notification methods on a specific day and time.

#### 3. Parental Controls
Set automated rules based on accumulated playtime or time of day.
* **Screen Time:** Set distinct weekday and weekend daily minute limits.
* **Curfew:** Set distinct weekday and weekend cutoff times (e.g., 22:00).
* **Reminder Frequency:** Set how often to repeat the notification(s).
* **Notifications:** When a limit is reached, you can automatically send a notification using any of your configured methods.

#### 4. Advanced
Fine-tune game names, covers, and exclusions using simple, comma-separated lists.
* **Game Title Overrides:** Clean up messy or lengthy names. Format as `raw name: display name`.
  * *Example:* `Minecraft Launcher = Minecraft, Grand Theft Auto V = GTA V`
* **Custom Cover Map:** Manually assign cover art for custom games or unrecognized titles. Fully supports web URLs and Home Assistant `/local/` paths.
  * *Example:* `Marvel Rivals = /local/covers/marvel.png, Halo = https://...`
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
| sensor.players_online | Sensor | Global sensor that tracks the total number of players currently online |
| binary_sensor.anyone_gaming | Binary Sensor | Useful for showing or hiding cards |

### Attributes for Master Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_gaming_status` sensors provide the following attibutes

**Aggregate Analytics**
| Attribute | Description |
| --- | --- |
| friendly_name | Display name for this player's entity |
| total_daily_hours | Sum of daily hours across all platforms (float) |
| total_weekly_hours | Sum of weekly hours across all platforms (float) |
| rolling_weekly_hours | Sum of rolling hours across all platforms |
| total_weekly_hours_last_week | Sum of last week's hours |
| weekly_breakdown | A copy of 'calendar_weekly_breakdown', for backward-compatibility |
| calendar_weekly_breakdown | Consolidated dictionary of all games played across all platforms for the week, formatted as human-readable strings (e.g., "5h 30m") and resetting each Sunday at midnight |
| rolling_weekly_breakdown | Consolidated dictionary of all games played across all platforms, formatted as human-readable strings (e.g., "5h 30m") as a rolling 7-day total |
| platform_split | A dictionary showing the percentage of total weekly hours spent on each platform (e.g., {"Steam": "55%"}) |
| longest_session | A copy of 'calendar_longest_session', for backward-compatibility |
| calendar_longest_session | A formatted string showing the game title and duration of the longest session across all platforms for the week, resetting each Sunday at midnight |
| rolling_longest_session | A formatted string showing the game title and duration of the longest session across all platforms, tracking only the last seven days |

**Parental/Limit Controls**
| Attribute | Description |
| --- | --- |
| daily_play_limit_minutes | The configured daily limit |
| remaining_play_time_minutes |Calculated remaining time based on current usage |


**Active Status**
| Attribute | Description |
| --- | --- |
| secondary | Current state or the time elapsed and session duration of the last played game |
| active_platform | The name of the platform currently driving the status (e.g., "Steam") |
| entity_picture | URL of the player's avatar fetched from the active platform |
| icon | Dynamic icon to match the active platform |
| game_cover_art | URL of cover art, either local or SteamGridDB |
| game_hero_art | URL of hero art, either local or SteamGridDB |
| game_logo_art | URL of logo art, either local or SteamGridDB |
| game_icon_art | URL of icon art, either local or SteamGridDB |
| current_game | Inherited from the most active underlying platform tracker |
| play_start_time | Inherited from the most active underlying platform tracker |
| last_played_game | Title of the most recent game detected across all tracked platforms |
| last_online_valid_timestamp | Timestamp of the last time detected online |

### Attributes for Platform Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_steam`, `*_xbox`, and `*_playstation` sensors provide the following attibutes

**Core State Attributes**
| Attribute | Description |
| --- | --- |
| friendly_name | Display name for this player's entity |
| secondary | Current state or the time elapsed and session duration of the last played game |
| current_game | Title of the game actively being played right now, or null if the user is offline |
| entity_picture | The local path to the cached avatar image |
| last_online_valid_timestamp | Timestamp of the last time detected online |
| play_start_time | Timestamp when the current, active gaming session began |
| timer_status | Current state of the internal playtime stopwatch (Running, Paused, or Stopped) |
| cached_game_cover | Backup URL for the last known game cover art, used during grace periods or offline states |
| game_cover_art | URL of cover art, either local or SteamGridDB |
| game_hero_art | URL of hero art, either local or SteamGridDB |
| game_logo_art | URL of logo art, either local or SteamGridDB |
| game_icon_art | URL of icon art, either local or SteamGridDB |

**Rich Tracking & Analytics**
| Attribute | Description |
| --- | --- |
| icon | Icon to match the current platform |
| daily_play_time | Total seconds played today |
| daily_play_time_formatted | Human-readable daily time (e.g., "1h 13m") |
| weekly_play_time | Total seconds played this week |
| weekly_play_time_formatted | Human-readable weekly time |
| weekly_play_time_last_week | Total seconds played in the previous week |
| last_played_game | Title of the most recently closed game detected on this specific platform |
| weekly_game_breakdown | A dictionary mapping game names to their playtime durations |
| longest_session_details | A copy of 'calendar_longest session', for backward compatibility |
| calendar_longest_session | A dictionary containing the game title and duration (in seconds) of the longest session recorded during the calendar week (resets on Sunday at midnight) |
| rolling_longest_session | A dictionary containing the game title and duration (in seconds) of the longest session recorded over the last seven day period |

### Attributes for Players Online Sensor
| Attribute | Description |
| --- | --- |
| active_games | A comma-separated string of the games currently being played by online players |

### Note
Several of these attributes (e.g., the artwork URLs, weekly_breakdown, longest_session_details) are explicitly added to `_unrecorded_attributes` in the classes. This is a deliberate performance optimization to prevent Home Assistant from saving these frequently changing values into the long-term database (recorder), which keeps your `home-assistant_v2.db` file from growing excessively large.

## ❓ What Next?
Once everything is up and running (with sensors showing up from the integration), try playing a game for at least 5 minutes to make sure the online status is reflected in the master "_gaming_status" sensors. *Note that, by default, sessions shorter than 300 seconds (5 minutes) are discarded and do not count toward the total playtime hours.* If the sensors are working correctly, try some of the following! If not, see the [troubleshooting](docs/troubleshooting.md) documentation.

- Add some sweet displays to your [dashboard](https://github.com/adamjthompson/Gaming-Status-Cards#1-gaming-status---list), showing who's online and what they're playing
- Set up Discord, SMS, and/or Mobile [notifications](docs/notifications.md) for when users start and stop playing games
- Add a [slideshow](https://github.com/adamjthompson/Gaming-Status-Cards#2-gaming-status---slideshow) to your dashboard or wallpanel display to see what's being played
- Add a [graph](https://github.com/adamjthompson/Gaming-Status-Cards#3-gaming-status---chart) to chart weekly game time
- Add [custom sensors](docs/advanced.md#tracking-standalone-pc-games-hassagent-setup) to track PC games not logged by Steam or Xbox
- Use a [sensor](docs/advanced.md#the-is-anyone-gaming-binary-sensor-for-automations) to track whether or not anyone is gaming (useful for automations or contextual card display)
- Check out other [advanced setup options](docs/advanced.md) for features like preventing tracking of games by the wrong players and per-user game exclusions
