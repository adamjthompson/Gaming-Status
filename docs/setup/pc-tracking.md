# PC Tracking & Discord Setup
To provide the most accurate PC tracking possible, this integration can monitor Steam, Discord, Playnite, and Gaming Status Agent simultaneously and output the result as one "PC" sensor. 

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
9. Finally, in Home Assistant, add the **Gaming Status** integration and check **Discord** in the platforms list — first-time setup then shows a dedicated Discord step for your Bot Token and Server ID. (If already installed, enable Discord under Global Settings and enter them under Advanced.) The integration will automatically fetch a list of your server members so you can easily assign them to your players from a dropdown menu!
10. *Smart Application ID Filtering:* Once set up, the integration automatically ignores custom text statuses (like 'Eating dinner' or listening to Spotify). It strictly requires a verified Discord `application_id` to trigger, guaranteeing only legitimate gaming sessions are captured. Activities relayed by Discord's own Xbox account connection are treated as stale console relays and ignored when the player also has a native Xbox sensor (which is more timely); without one, the relay is still used.
11. *Automation event:* Every presence update for a tracked member fires a `gaming_status_discord_<member_id>` event with `{user_id, state, app_id, avatar_url, display_name}` (`state` is the game name, `Online`, or `Offline`; `display_name` is the permanent username).

**Setting up Playnite Tracking:**
Playnite is an incredible open-source library manager that can track games across almost any PC launcher or emulator. To link it to Gaming Status, it utilizes MQTT.

1. Ensure you have an MQTT Broker (like Mosquitto) installed and configured in Home Assistant.
2. Open Playnite on your PC, navigate to the Add-ons menu, and install an MQTT broadcasting add-on (e.g., *MQTT Client*).
3. Configure the add-on in Playnite with your Home Assistant MQTT broker IP address, username, and password.
4. In Home Assistant, ensure the MQTT integration detects and creates this binary sensor.
5. Go to the Gaming Status configuration, select Playnite as an Enabled Platform, and assign that new `binary_sensor` to your player! *By default, it may be named `binary_sensor.desktop_playnite_playnite_playing_game`.*

**Setting up Gaming Status Agent Tracking:**
[Gaming Status Agent](https://github.com/adamjthompson/Gaming-Status-Agent) is a companion Windows tray app that detects what you're playing and which launcher it came from, then publishes it to Home Assistant over MQTT. Home Assistant discovers the sensor automatically, so no YAML is needed. It is the way to track standalone PC games that have no native Home Assistant integration (Epic, GOG, Battle.net, Ubisoft, EA, Amazon Games, and so on). Games it can't detect on its own can be matched by executable or window title under its **Custom Games** menu; those report their launcher as `Custom`.

1. Ensure you have an MQTT Broker (like Mosquitto) installed and the MQTT integration configured in Home Assistant.
2. Download and run Gaming Status Agent on the PC. Right-click its tray icon, open **MQTT Settings**, and enter your broker's address and credentials.
3. Under **Platforms**, choose which launchers to track. Under **Gamertags**, optionally enter your account name for each launcher (otherwise your Windows account name is used).
4. In Home Assistant, confirm the MQTT integration has created `sensor.gsa_<profile name>` (e.g. `sensor.gsa_adam`).
5. Go to the Gaming Status configuration, select **Gaming Status Agent** as an Enabled Platform, and pick the `sensor.gsa_*` sensor for your player from the dropdown.

The Agent's sensor reports the following, which Gaming Status reads directly:

| Attribute | Used for |
| --- | --- |
| `Game Title` | The current game, or `Offline` |
| `Launcher` | Launcher Name or None. Shown as the platform (`active_platform`) on the PC and Master sensors and recorded on each session, so a game played through Epic shows as "Epic" rather than a generic PC entry. Steam and Xbox games are attributed to Steam and Xbox (see below) |
| `Profile Name` | Shown as the player's `gamertag` for this sensor |

**Steam and Xbox games from the Agent:** the Agent's Steam and Xbox detection is optional and off by default. It's meant for people who want to track those platforms without installing the Steam or Xbox integrations in Home Assistant. Games it reports on those launchers are treated as Steam or Xbox games, not GSA: they show as Steam or Xbox and are recorded on sessions with `platform_key` `steam` or `xbox`. If the native Steam or Xbox sensor is also set up and reports the same game, the Agent steps aside so that sensor's gamertag, achievements, and ratings are used.

The Agent can also report Playnite games, which the priority rules below keep from being counted twice.

**Custom sensors:** For PCs that can't run Gaming Status Agent (e.g. Linux), enable the **Custom** platform and select any sensor whose state is the game title, such as a HASS.Agent or template sensor. See [Tracking PC Games with a Custom Sensor](#tracking-pc-games-with-a-custom-sensor) below. Custom entries that an earlier update moved into the Gaming Status Agent slot are moved back automatically, along with their history.

**The PC Sub-Master Priority Logic:**
If a player launches a game, it is very common for multiple trackers (like Discord, Playnite, and Steam) to detect it simultaneously. To prevent double-counting your playtime hours and sending duplicate push notifications, the `sensor.gaming_status_XXXXX_pc` sensor uses strict **Smart Platform Yielding**. 

Within the PC sensor, platforms are prioritized in this order: **Playnite > Gaming Status Agent > Custom > Steam > Discord**. The one exception is a Steam or Xbox game reported by Gaming Status Agent, which always yields to the native Steam or Xbox sensor.

* *Example:* If a player launches a Steam game, Discord will likely detect it first and claim the dashboard. Seconds later, when Steam wakes up and detects the same game, Discord will instantly pause its timer and yield control to Steam. 
* *Result:* You get the lightning-fast notifications of Discord, but the pristine, deduplicated analytics of Steam!

Separately, each platform sensor yields its playtime timer to any higher-priority platform tracking the same game, using the global order **Gaming Status Agent > Custom > Steam > Xbox > PlayStation > Playnite > Discord** (the same order the Master sensor checks platforms in). The same GSA Steam/Xbox exception applies.

**Platform hops:** A "finished playing" notification is held for about 45 seconds before sending; if the same game starts on another platform in that window (e.g. one platform's integration briefly drops while another keeps tracking), the end/start notification pair is skipped.

## Tracking PC Games with a Custom Sensor
If Gaming Status Agent isn't an option (for example on a Linux PC), enable the **Custom** platform and point the player's Custom slot at any sensor whose state is the game title. A state of `on`/`1`/`true`/`playing` reports "Unknown Custom Game"; `off`/`0`/`false`/`offline`/`unavailable` reports offline. Custom and Gaming Status Agent can be used together; when both report the same game, Gaming Status Agent wins.

A common setup is a **HASS.Agent** (Windows) or other companion-app "process active" sensor per game, combined into one "PC Funnel" template sensor:

```yaml
- sensor:
    - name: "Username Active PC Game"
      unique_id: username_active_pc_game
      icon: mdi:desktop-tower
      state: >
        {# Format -> "your_pc_sensor_entity_id": "Clean Dashboard Name" #}
        {% set pc_games = {
          "sensor.pc_name_genshin_impact_status": "Genshin Impact",
          "sensor.pc_name_wonderlands_status": "Tiny Tina's Wonderlands"
        } %}
        {% set active = namespace(game='Offline') %}
        {% for entity_id, game_name in pc_games.items() %}
          {% set state_val = states(entity_id) %}
          {% if state_val in ['on', 'true'] or (state_val | int(default=-1)) > 0 %}
            {% set active.game = game_name %}
          {% endif %}
        {% endfor %}
        {{ active.game }}
```

Then select `sensor.username_active_pc_game` as that player's Custom sensor. Custom sensors have no avatar of their own; add `custom_username_avatar.png` as described in [Custom User Avatars](../configuration/manage-players.md#custom-user-avatars).
