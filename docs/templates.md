# 🛠️ Advanced Template Sensors & PC Tracking

While the **Gaming Status** integration handles most of the heavy lifting automatically, you can use Home Assistant's native Template Sensors to unlock even more advanced tracking and home automation capabilities. 

Below is a guide on how to track standalone PC games, along with two highly recommended templates you can add to your `configuration.yaml` (or `templates.yaml`) file.

---

## 1. Tracking Standalone PC Games (HASS.Agent Setup)

Steam and Xbox tell Home Assistant exactly what game you are playing. However, standalone PC games (like Epic Games, Genshin Impact, or Minecraft) don't have native integrations. 

To track these, the easiest method is using **HASS.Agent** (a free Windows companion app for Home Assistant) to monitor the game's background process.

**How to set up a HASS.Agent Game Sensor:**
1. Open the HASS.Agent configuration app on the gaming PC.
2. Go to **Local Sensors** and click **Add New**.
3. In the "Type" dropdown, select **Process**.
4. In the "Process Name" box, type the exact name of the game's executable file without the `.exe` extension (e.g., type `Wonderlands` instead of `Wonderlands.exe`).
5. Set the Update Interval to something responsive (e.g., `10` seconds).
6. Click **Store** and then **Store and Activate** to push the new sensor to Home Assistant.
7. In Home Assistant, this will create an entity like `sensor.yourpcname_wonderlands`.

*Note: The "Process" sensor counts how many instances of that game are running (often returning a `1` or `2`). The Funnel Sensor below is specifically designed to translate these numbers into a clean game title!*

---

## 2. The "PC Funnel" Sensor

**The Problem:** Once you create the HASS.Agent sensors above, it can be messy to track all of them individually. You need a way to combine them into one output for the Gaming Status integration.

**The Solution:** Build a "Funnel Sensor." This template watches your list of PC executables. If any of them report a running process, it automatically forwards the clean, formatted game name directly to your Gaming Status profile!

**How to use it:** 1. Paste this into your template configuration.
2. Edit the `pc_games` list with your specific PC sensors and desired display names.
3. Open your `profiles.py` file and point that user's `"custom"` slot directly to this new sensor (`sensor.username_active_pc_game`).

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

## 3. The "Is Anyone Gaming?" Binary Sensor (For Automations)

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