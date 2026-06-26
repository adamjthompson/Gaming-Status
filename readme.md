![Gaming Status Logo](custom_components/gaming_status/brand/logo.png)

# 🎮 Gaming Status for Home Assistant

This is a powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, Discord, Playnite, and custom PC clients into a single, clean dashboard sensor for each person in your household / friend group. It's super useful if you want to be able to track who is online, what they're playing, and for how long. 

I started developing this as a way to have persistent sensors since I was annoyed that the Xbox and Steam sensors would regularly flutter between online/offline status, making notifications and my gaming dashboard unreliable. This evolved into making master sensors instead of simply tracking each platform independently. Over time, this has grown into a much more complex integration that now tracks game time, records the last game played, provides cover art, allows for rich notifications through Discord and much, much more.

Some of the key features are listed below.

## ✨ Features
* **Unified Master Sensor:** Combines Xbox, PlayStation, Steam, Discord, Playnite, and Custom PC clients into one clean "Master Status" sensor per person.
* **Online/Offline Notifications:** Receive Discord, SMS, and/or Mobile notifications when users start or finish playing a game.
* **Parental Controls:** Track daily playtime and recieve notifications or trigger an automation or script when a limit or curfew is reached.
* **Discord Rich Presence:** Track hundreds of standalone games, emulators, and Epic/EA/Ubisoft launchers automatically by hooking into Discord's Rich Presence status. Automatically ignores custom text statuses to prevent false positives.
* **Custom PC Game Support:** Track non-platform games (like Epic Games, Minecraft, or Genshin Impact) using template funnels or binary sensors.
* **PC Sub-Master Sensor:** Automatically aggregates Custom, Steam, and Discord tracking into a single, unified "PC" status. It features smart platform yielding (e.g., Discord quietly steps aside if Steam is tracking the same game) to completely eliminate double-counting in your playtime analytics.
* **Smart Ghosting Protection:** Automatically prevents echo sessions (e.g., when the Windows Xbox app incorrectly broadcasts a Steam game).
* **Drop-Out Protection:** Built-in grace periods prevent a gamer from appearing "Offline" if their game crashes, they switch titles, or their internet briefly blips, keeping play sessions perfectly intact.
* **Playtime Analytics:** Automatically calculates session time, daily hours, and a rolling 7-day total for easy dashboard charting.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.
* **Advanced Exclusion Filtering:** Prevent media apps (Netflix, YouTube, Spotify) or background processes from triggering gaming statuses using a global exclusions list.
* **Customizable Cover Art:** Automatically fetches gorgeous images from SteamGridDB and passes them to your dashboard via URL and/or caches them locally for fast updates.
* **"Last Seen" Memory:** When gamers go offline, the sensor retains their last played game and calculates exactly how long ago they were active (e.g., *Last seen 3h ago: Genshin Impact (1h 37m)*).
* **Custom Avatars:** Automatically pulls live gamer pictures from platform APIs, with the option to easily override missing or incorrect images with your own local images.

## ⚠️ Prerequisites
This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have any necessary base integrations installed and working in Home Assistant. Not all are required, but you'll need at least ONE official integration installed:
* [Official PlayStation Network Integration](https://www.home-assistant.io/integrations/playstation_network)
* [Official Steam Integration](https://www.home-assistant.io/integrations/steam_online)
* [Official Xbox Integration](https://www.home-assistant.io/integrations/xbox)

## Recommended
While not required for functionality, I recommend installing the following HACS for the most robust dashboard cards:
* [SteamGridDB API Key](https://www.steamgriddb.com/) - Provides artwork for games. *This is not REQUIRED, but it is HIGHLY recommended!*
* [Gaming Status Cards](https://github.com/adamjthompson/Gaming-Status-Cards) - Easy to use companion dashboard cards, so you don't have to make your own.
* [ApexCharts Card](https://github.com/RomRider/apexcharts-card) - For the stats and donut graph cards.
* [Official Discord Integration](https://www.home-assistant.io/integrations/discord) - Requires setting up a Discord Bot. *REQUIRED if you want to use Discord for notifications.*
* [Mosquitto Broker](https://github.com/home-assistant/addons/tree/master/mosquitto) - Required if you plan to use Playnite for tracking games. You will also need an MQTT add-on installed in Playnite (such as [Playnite MQTT Client](https://playnite.link/addons.html#MQTTClient_90c44048-4f8f-43f7-a0c1-f8164bf1d7ef)) to broadcast your status to Home Assistant.
* [HASS.Agent](https://www.hass-agent.io/2.2/getting-started/installation/#installing-hassagent) - Allows you to create custom sensors for otherwise untrackable games. Install both the PC app and the integration for Custom PC sensors. *Try using Discord tracking first, if possible, since HASS Agent sensors have to be created for each individually-tracked game.*

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
Installation is easiest via the [Home Assistant Community Store (HACS)](https://hacs.xyz/). Gaming Status is a default repository in HACS, so you do not need to add any custom links! 

Simply click the button below (requires My Home Assistant configured) to open the download page directly, or search for "Gaming Status" in HACS.

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
To configure your players, notifications, and rules, click the **Configure** button (gear icon) on the Gaming Status integration card. This opens the main configuration hub, which is divided into six menus based on your enabled features:

#### 1. Manage Players
Add, edit, or delete the gamers in your household.
* **Platform Sensors:** When adding a player, you simply select their respective integration sensors from the dropdowns. The integration will automatically filter your entities to show the correct Steam (`sensor.steam_*`), Xbox (`sensor.*_status`), PlayStation (`sensor.*_online_status`), and Playnite (MQTT `binary_sensor.*_playnite_playing_game`) entities. *(Note: To remove a previously assigned sensor, simply click the 'X' to clear the entity dropdown and click Submit. The integration will safely save the empty state and stop tracking that platform).*
* **Player Details:** After adding a player, you can configure:
  * **Session Notifications:** Select notification methods for when this specific player starts or stops gaming. *Note: These must be configured under Notifications.*
  * **Ghosted-by:** Enter comma-separated entity IDs (e.g., `sensor.gaming_status_player_two_steam`) for sensors that should take priority over this user's Xbox sensor.
  * **Exclude Games:** Comma-separated list of games to ignore for this specific player.

#### 2. Notifications (Requires Global Toggle)
Manage where your [gaming alerts and weekly reports](docs/notifications.md) are sent.
* **Notify Artwork Style:** Choose which fetched artwork type (Cover, Hero, Logo, Icon) attaches to your notifications.
* **Add Notification:** Map a friendly name (e.g., "Dad's Phone") to an existing Home Assistant `notify.` service. It fully supports standard mobile app notifications, Discord, and SMS.
* **Discord Notification Colors:** Customize the embed colors for Discord alerts (Default, Platform Colors, Game Color, or Custom Hex).
* **Weekly Report:** Send a beautifully formatted summary of everyone's weekly playtime and top games to your selected notification methods on a specific day and time.

#### 3. Parental Controls (Requires Global Toggle)
Set automated rules based on accumulated playtime or time of day.
* **Screen Time:** Set distinct weekday and weekend daily minute limits.
* **Curfew:** Set distinct weekday and weekend cutoff times (e.g., 22:00).
* **Reminder Frequency:** Set how often to repeat the notification(s).
* **Notifications:** When a limit is reached, you can automatically send a notification using any of your configured methods.

#### 4. Custom Artwork
Manually assign artwork or colors to specific games using simple `Game = Value` lists.
* **Custom Asset Maps (Grid, Hero, Logo, Icon):** Assign artwork for custom games or unrecognized titles. Fully supports web URLs and Home Assistant `/local/` paths. *(Example: `Marvel Rivals = /local/covers/marvel.png`)*
* **Custom Colors:** Override the automatic dominant color extractor by assigning specific hex codes to games. *(Example: `Cyberpunk 2077 = #fcee0a`)*

#### 5. Advanced
Update your API keys and fine-tune text processing rules.
* **API Keys & Tokens:** Update your SteamGridDB API key, or your Discord Bot Token and Server ID.
* **Game Title Overrides:** Clean up messy or lengthy names. Format as `raw name = display name`. *(Example: `Minecraft Launcher = Minecraft`)*
* **Title Cleanups:** A list of strings to automatically strip from game names. *(Example: `Tom Clancy's, Sid Meier's`)*
* **Global Exclusions:** Games or apps that should be universally ignored by the tracker. *(Example: `Home, YouTube, Netflix, Xbox App`)*

#### 6. Global Settings
These variables control how the integration handles platforms, caching, and network drops across all players.
* **Enabled Platforms:** Select which gaming platforms to track (Steam, Xbox, PlayStation, Discord, Playnite, and Custom).
* **Master Toggles:** Enable or disable the Notifications and Parental Controls configuration hubs.
* **Cache Settings:** Toggle local image caching, automatic vibrant color extraction, and configure background cleanup limits (Max Files & Max Days).
* **Grace Periods (Network / Away / Transition):** Configure exactly how long the integration waits during network drops, idle statuses, or game switches before ending a session.
* **Minimum Session Duration:** Prevents quickly opening and closing a game (like launching a launcher) from cluttering your play history.
* **Reset History:** A toggle to clear all accumulated daily/weekly session history upon the next Home Assistant restart. *Use with caution!*
* **Remove Disabled Sensors:** Automatically deletes orphaned sensors from the registry if their platform is un-checked from the Enabled Platforms list.

#### 7. PC Tracking & Discord Setup
To provide the most accurate PC tracking possible, this integration can monitor Steam, Discord, Playnite, and Custom clients simultaneously and output the result as one "PC" sensor. 

**Setting up Discord Tracking:**
Because Home Assistant does not natively track Rich Presence, this integration features a built-in Discord tracker. To use it, you must create a bot in the Developer Portal to read your server's statuses.
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **New Application** in the top right corner and give your bot a name (e.g., "Home Assistant Tracker").
3. On the left menu, click **Bot**. Scroll down to the **Privileged Gateway Intents** section.
4. **CRITICAL:** You must turn on **Presence Intent** and **Server Members Intent**. Without these, the integration cannot see what games you are playing. Save your changes.
5. Scroll up and click **Reset Token**. Copy this newly generated Bot Token to a safe place. 
6. On the left menu, click **OAuth2**, then **URL Generator**. Under Scopes, check the **bot** box. 
7. Copy the generated URL at the bottom, paste it into your browser, and authorize the bot to join your personal Discord server. 
8. Open your Discord app, right-click your server icon, and click **Copy Server ID** *(Note: This requires Developer Mode to be enabled in Discord's advanced settings).*
9. Finally, in Home Assistant, add the **Gaming Status** integration (or go to the Advanced menu if already installed) and make sure **Discord** is checked in the platforms list. Paste your Bot Token and Server ID. The integration will automatically fetch a list of your server members so you can easily assign them to your players from a dropdown menu!
10. *Smart Application ID Filtering:* Once set up, the integration automatically ignores custom text statuses (like 'Eating dinner' or listening to Spotify). It strictly requires a verified Discord `application_id` to trigger, guaranteeing only legitimate gaming sessions are captured.

**Setting up Playnite Tracking:**
Playnite is an incredible open-source library manager that can track games across almost any PC launcher or emulator. To link it to Gaming Status, it utilizes MQTT.
1. Ensure you have an MQTT Broker (like Mosquitto) installed and configured in Home Assistant.
2. Open Playnite on your PC, navigate to the Add-ons menu, and install an MQTT broadcasting add-on (e.g., *MQTT Client*).
3. Configure the add-on in Playnite with your Home Assistant MQTT broker IP address, username, and password.
5. In Home Assistant, ensure the MQTT integration detects and creates this binary sensor.
6. Go to the Gaming Status configuration, select Playnite as an Enabled Platform, and assign that new `binary_sensor` to your player! *By default, it may be named `binary_sensor.desktop_playnite_playnite_playing_game`.*

**The PC Sub-Master Priority Logic:**
If a player launches a game, it is very common for multiple trackers (like Discord, Playnite, and Steam) to detect it simultaneously. To prevent double-counting your playtime hours and sending duplicate push notifications, the `sensor.gaming_status_XXXXX_pc` sensor uses strict **Smart Platform Yielding**. 

Platforms are prioritized in this exact order: **Custom > Steam > Playnite > Discord**.
* *Example:* If a player launches a Steam game, Discord will likely detect it first and claim the dashboard. Seconds later, when Steam wakes up and detects the same game, Discord will instantly pause its timer and yield control to Steam. 
* *Result:* You get the lightning-fast notifications of Discord, but the pristine, deduplicated analytics of Steam!

### Entities

Upon restart, the integration will instantly read your settings and generate the master tracking sensors for your dashboard. All entities generated by the integration are strictly standardized under the `gaming_status_` namespace.

| Entity | Type | Description |
| ---| --- | --- |
| sensor.gaming_status_XXXXX_steam | Sensor | Steam sensor for each added profile |
| sensor.gaming_status_XXXXX_xbox | Sensor | Xbox sensor for each added profile |
| sensor.gaming_status_XXXXX_playstation | Sensor | PlayStation sensor for each added profile |
| sensor.gaming_status_XXXXX_discord | Sensor | Discord sensor for each added profile |
| sensor.gaming_status_XXXXX_playnite | Sensor | Playnite sensor for each added profile |
| sensor.gaming_status_XXXXX_custom | Sensor | Custom sensor for each added profile |
| sensor.gaming_status_XXXXX_pc | Sensor | Sub-master sensor that automatically aggregates Steam, Discord, Playnite, and Custom PC clients into a single unified PC state |
| sensor.gaming_status_XXXXX_master | Sensor | Master sensor for each added profile that combines all added platforms into one "Online/Offline" status |
| sensor.gaming_status_XXXXX_chart | Sensor | Daily game time mapped to the Long-Term Statistics database (Total Increasing) |
| sensor.gaming_status_players_online | Sensor | Global sensor that tracks the total number of players currently online |
| binary_sensor.gaming_status_anyone_gaming | Binary Sensor | Useful for showing or hiding cards |

### Attributes for Master Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_master` sensors provide the following attibutes:

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
| game_dominant_color | The automatically extracted vibrant hex color from the game's artwork, or a manually assigned color |
| current_game | Inherited from the most active underlying platform tracker |
| play_start_time | Inherited from the most active underlying platform tracker |
| last_played_game | Title of the most recent game detected across all tracked platforms |
| last_online_valid_timestamp | Timestamp of the last time detected online |

### Attributes for Platform Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The individual `*_steam`, `*_xbox`, `*_discord`, and `*_playstation` sensors provide the following attibutes:

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
| game_dominant_color | The automatically extracted vibrant hex color from the game's artwork, or a manually assigned color |

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
Once everything is up and running (with sensors showing up from the integration), try playing a game for at least 5 minutes to make sure the online status is reflected in the `_master` sensors. *Note that, by default, sessions shorter than 300 seconds (5 minutes) are discarded and do not count toward the total playtime hours.* If the sensors are working correctly, try some of the following! If not, see the [troubleshooting](docs/troubleshooting.md) documentation.

- Add some sweet displays to your [dashboard](https://github.com/adamjthompson/Gaming-Status-Cards#1-gaming-status---list), showing who's online and what they're playing
- Set up Discord, SMS, and/or Mobile [notifications](docs/notifications.md) for when users start, stop, and switch games
- Add a [slideshow](https://github.com/adamjthompson/Gaming-Status-Cards#2-gaming-status---slideshow) to your dashboard or wallpanel display to see what's being played
- Add a [graph](https://github.com/adamjthompson/Gaming-Status-Cards#3-gaming-status---chart) to chart weekly game time
- Add [custom sensors](docs/advanced.md#tracking-standalone-pc-games-hassagent-setup) to track PC games not logged by Steam or Xbox sensors
- Use a [sensor](docs/advanced.md#the-is-anyone-gaming-binary-sensor-for-automations) to track whether or not anyone is gaming (useful for automations or contextual card display)
- Check out other [advanced setup options](docs/advanced.md) for features like preventing tracking of games by the wrong players and per-user game exclusions
