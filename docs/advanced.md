# 🛠️ Advanced Setup

Below are additional setup options as well as descriptions of what each parameter in the gaming_profiles.json file does. See 'example.profiles.json' for and example of the proper formatting.

## User Profiles (GAMER_PROFILES)
This section maps a friendly display name to the underlying Home Assistant sensors tracking that person. It also holds user-specific rules.

**Platform Keys:** Add the entity IDs for Steam, Xbox, PlayStation, or custom sensors. You can include as few or as many as a user owns. *Note the following default platform integration sensor naming conventions:*

- **Steam:** sensor.steam_playername
- **Xbox:** sensor.playername_status
- **PlayStation:** sensor.playername_online_status

**Ghosted_by:** A list of master sensor IDs. If the current user is playing the exact same game as someone in this list, the current user's sensor will remain offline. This is useful for shared consoles or PCs to prevent duplicate tracking.

**Exclude_games:** A user-specific list of games or apps that should be completely ignored (case-insensitive).

*Editing Notes: Replace "Player One" etc. with whatever you want the players to be named and "_player_one" with whatever the actual gamertags should be. "Custom" is only needed if you will be creating your own status sensors, for example, using HASS Agent on a PC to provide an on/off status for a game. Remove any lines that you do not need, and make sure that you do not have any trailing commas after the last entries.*

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

## Global Exclusions (GLOBAL_EXCLUSIONS)
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

## Title Cleanups (TITLE_CLEANUPS)
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

## Game Title Overrides (GAME_TITLE_OVERRIDES)
This acts as a strict dictionary. If the integration detects an exact match with the key (the name on the left), it will permanently replace it with the value (the name on the right) before doing any API lookups or dashboard updates. This is perfect for shortening obnoxiously long official titles or for when cover art lookup fails due to a name mismatch.

```yaml
"GAME_TITLE_OVERRIDES": {
    "Grand Theft Auto V": "GTA V",
    "The Elder Scrolls V: Skyrim Special Edition": "Skyrim",
    "Call of Duty®: Modern Warfare® II": "Modern Warfare II"
  }
```

## Custom Cover Art (CUSTOM_COVER_MAP)
This allows you to bypass the SteamGridDB API entirely. If a game title matches a key in this list, the integration will immediately use the provided URL for the artwork. This is great for obscure games, custom emulators, or simply when you prefer a specific piece of fan art over the official hero artwork.

*Note: URLs must point directly to an image file (e.g., .png, .jpg).*

```yaml
"CUSTOM_COVER_MAP": {
    "Marvel Rivals": "https://cdn2.steamgriddb.com/hero/a31d2779e08530d0b5fdbed368c735b4.png",
    "Super Smash Bros. Melee": "/local/gaming_status/melee_cover.jpg"
  }
```
---

## Tracking Standalone PC Games (HASS.Agent Setup)
While the **Gaming Status** integration handles most of the heavy lifting automatically, you can use Home Assistant's native Template Sensors to unlock even more advanced tracking and home automation capabilities. 

Below is a guide on how to track standalone PC games, along with two highly recommended templates you can add to your `configuration.yaml` (or `templates.yaml`) file.

Steam and Xbox tell Home Assistant exactly what game you are playing. However, standalone PC games (like Epic Games, Genshin Impact, or Minecraft) don't have native integrations. 

To track these, the easiest method is using **HASS.Agent** (a free Windows companion app for Home Assistant) to monitor the game's background process.

### Install HASS.Agent
1. Install the HASS.Agent software on your gaming PC and the Home Assistant Integration by following the installation steps [here](https://www.hass-agent.io/2.2/getting-started/installation/#installing-hassagent).
2. Open the HASS.Agent configuration app on the gaming PC.
3. Go to **Local Sensors** and click **Add New**.
4. In the "Type" dropdown, select **Process**.
5. In the "Process Name" box, type the exact name of the game's executable file without the `.exe` extension (e.g., type `Wonderlands` instead of `Wonderlands.exe`).
6. Set the Update Interval to something responsive (e.g., `10` seconds).
7. Click **Store** and then **Store and Activate** to push the new sensor to Home Assistant.
8. In Home Assistant, this will create an entity like `sensor.yourpcname_wonderlands`.

*Note: The "Process" sensor counts how many instances of that game are running (often returning a `1` or `2`). The Funnel Sensor below is specifically designed to translate these numbers into a clean game title!*

### The "PC Funnel" Sensor

**The Problem:** Once you create the HASS.Agent sensors above, it can be messy to track all of them individually. You need a way to combine them into one output for the Gaming Status integration.

**The Solution:** Build a "Funnel Sensor." This template watches your list of PC executables. If any of them report a running process, it automatically forwards the clean, formatted game name directly to your Gaming Status profile!

**How to use it:**
1. Paste this into your template configuration.
2. Edit the `pc_games` list with your specific PC sensors and desired display names.
3. Open your `gaming_profiles.json` file and point that user's `"custom"` slot directly to this new sensor (`sensor.username_active_pc_game`).

```yaml
- sensor:
    - name: "Username Active PC Game"
      unique_id: username_active_pc_game
      icon: mdi:desktop-tower
      state: >
        {# 1. DEFINE YOUR LIST OF GAMES HERE #}
        {# Format -> "your_pc_sensor_entity_id": "Clean Dashboard Name" #}
        {% set pc_games = {
          "sensor.pc_name_genshin_impact_status": "Genshin Impact",
          "sensor.pc_name_wonderlands_status": "Tiny Tina's Wonderlands"
        } %}

        {# 2. THE AUTO-GENERATOR (Do not touch below this line) #}
        {% set active = namespace(game='Offline') %}
        {% for entity_id, game_name in pc_games.items() %}
          {% set state_val = states(entity_id) %}
          {# Trigger if it says 'on', 'true', OR is a process count greater than 0 #}
          {% if state_val in ['on', 'true'] or (state_val | int(default=-1)) > 0 %}
            {% set active.game = game_name %}
          {% endif %}
        {% endfor %}

        {{ active.game }}
```

---

## The "Is Anyone Gaming?" Binary Sensor (For Automations)

**The Problem:** You want to trigger a Home Assistant automation (like changing the living room lights to a specific color, or silencing TTS announcements) whenever *anyone* in the house starts gaming, but you don't want to write a messy automation trigger that manually lists every single person's Xbox, Steam, and PlayStation sensor.

**The Solution:** This dynamic binary sensor automatically searches your entire Home Assistant system for any Master Gaming Sensors (`_gaming_status`) created by this integration. If *any* of them are currently playing a game, this switch turns `on`. When the last person stops playing, it turns `off`.

**How to use it:** Paste this into your template configuration. You can now use `binary_sensor.anyone_gaming` as a single, simple trigger or condition in your Node-RED flows or Home Assistant Automations!

```yaml
- binary_sensor:
    - name: "Anyone Gaming"
      unique_id: anyone_gaming
      icon: >
        {{ 'mdi:controller' if this.state == 'on' else 'mdi:controller-off' }}
      state: >
        {{ states.sensor 
          | selectattr('entity_id', 'match', '^sensor\..*_gaming_status$') 
          | rejectattr('state', 'eq', 'Offline') 
          | rejectattr('state', 'in', ['unavailable', 'unknown']) 
          | list 
          | count > 0 }}
```