# Events
Gaming Status fires these events on the Home Assistant event bus, for use as automation triggers. They use the same filtering as notifications, so a launcher handing off to the real game, or a game moving from one platform to another, doesn't fire extra events. They fire whether or not notifications are turned on.

| Event | When | Data |
| --- | --- | --- |
| `gaming_status_session_started` | A player starts a new gaming session | `player`, `game`, `platform`, `entity_id` (the master sensor), `device_id`, `game_cover_art`, `game_color` |
| `gaming_status_session_switched` | A player switches from one game to another | The same fields for the new game, plus `previous_game` and `duration_minutes` (how long the previous game was played) |
| `gaming_status_session_ended` | A player stops playing | The same fields for the game that ended, plus `duration_minutes` |
| `gaming_status_screen_time_warning` | A player has played to within their **Screen time warning** minutes of the limit (once per day; set the minutes in Parental Controls or with the player's Screen time warning entity) | `player`, `entity_id`, `device_id`, `game`, `limit_minutes`, `minutes_played`, `minutes_left` |
| `gaming_status_screen_time_exceeded` | A player reaches their daily screen time limit while playing. Repeats on the rule's **Repeat** interval while they keep playing | `player`, `entity_id`, `device_id`, `game`, `limit_minutes`, `minutes_played`, `minutes_over` |
| `gaming_status_curfew_exceeded` | A player is still playing at curfew. Repeats on the rule's **Repeat** interval while they keep playing | `player`, `entity_id`, `device_id`, `game`, `curfew` (e.g. `22:00`), `minutes_over` |
| `gaming_status_rating_exceeded` | A player starts a game rated above their limit (once per game) | `player`, `entity_id`, `device_id`, `game`, `age_floor`, `esrb`, `pegi`, `max_age_floor` (the player's limit), `game_cover_art` |
| `gaming_status_achievement_unlocked` | A player earns an achievement or trophy | `player`, `entity_id`, `device_id`, `game`, `platform`, `console`, `achievement`, `description`, `tier` (PlayStation trophy tier), `icon_url`, `unlocked_at`, `game_color` |

* **`message`:** Every event also includes a ready-made sentence starting with the player's name, such as "Erik stopped playing Borderlands 4 (3h 4m)", "Liv has 15 minutes of screen time left" or "Adam unlocked platinum trophy "Platinum" in Astro Bot". Use it as-is in a notification: `{{ trigger.message }}` with a Gaming Status trigger, or `{{ trigger.event.data.message }}` with an event trigger.
* **`game_color`:** The game's dominant color as a hex code (e.g. `#3c3c5a`), handy for setting light colors. Only filled in when **Enable Game Color Extraction** is turned on in Global Settings; otherwise it's empty. Gaming Status waits up to about 15 seconds after a game starts for its artwork and color, so they're almost always included.
* **Parental events** only fire for rules that are turned on under **Parental Controls**, but they fire whether or not the rule has an action or notification destination set, so you can build your own response in an automation.
* **Achievement events** arrive when Gaming Status next checks for unlocks (every 15 minutes by default, set by **Achievement/Trophy Recheck Interval**), since Steam, Xbox and PlayStation don't announce unlocks as they happen. Only unlocks from the last hour fire an event, so turning on achievement tracking or a library scan filling in older history doesn't set off a flood of events. Requires **Enable Achievement/Trophy Tracking**.
* A switch within 90 seconds of starting a game (e.g. a launcher opening the real game) fires nothing; the earlier **Started playing** stands.

Each event is also written to Home Assistant's **Logbook** in plain language, for example "Adam started playing ELDEN RING on Steam", "Adam switched from Halo (1h 35m) to DOOM Eternal on Xbox", "Liv is still playing Roblox after the 22:00 curfew (10 minutes past)" or "Adam unlocked "Arena Grande" in Borderlands 4". These entries appear in the Logbook and on each player's device and master sensor pages.

## Triggers
In the automation editor's **Add trigger** dialog, the events are available as **Started playing**, **Switched games**, **Stopped playing**, **Screen time warning**, **Screen time exceeded**, **Curfew exceeded**, **Rating exceeded** and **Achievement unlocked**. Find them under **By type → Gaming Status**, or under **By target** by picking a player (their device is under **Unassigned → Devices** unless you've given it an area). Each player's device also has matching classic device triggers.

The target picks **which player(s)** the trigger watches, not which sensor. Any of a player's Gaming Status sensors works: picking their Library or Steam sensor triggers exactly the same as picking their master sensor, because the trigger looks up the player that sensor belongs to. The master sensor (*Name* Gaming Status) is the clearest choice. You can also target a player's device (`device_id:`), or an area or label (`area_id:` / `label_id:`) to cover several players at once.

**Home Assistant version:** These triggers need Home Assistant 2025.12 or newer, and they appear in the **Add trigger** dialog from 2026.2. On older versions they aren't available, but the events above and the classic device triggers still work, so use an event trigger (see the second example below) instead.

With these triggers, the event data is directly under `trigger.` (e.g. `trigger.game_color`). With a raw event trigger, it's under `trigger.event.data.`.

## Example: light matches the game's color
Sets a light to the game's color whenever Adam starts playing or switches games:

```yaml
alias: Game color lighting
triggers:
  - trigger: gaming_status.session_started
    target:
      entity_id: sensor.gaming_status_adam_master
    options: {}
  - trigger: gaming_status.session_switched
    target:
      entity_id: sensor.gaming_status_adam_master
    options: {}
conditions:
  # Skip games with no extracted color (or Enable Game Color Extraction off).
  - condition: template
    value_template: "{{ trigger.game_color is string }}"
actions:
  - action: light.turn_on
    target:
      entity_id: light.game_room
    data:
      brightness_pct: 80
      rgb_color: >
        {% set c = trigger.game_color.lstrip('#') %}
        {{ [c[0:2] | int(base=16), c[2:4] | int(base=16), c[4:6] | int(base=16)] }}
mode: restart
```

## Example: the same thing with an event trigger
Useful in YAML packages or when you'd rather match on event data, such as a specific player or game:

```yaml
alias: Game color lighting (event)
triggers:
  - trigger: event
    event_type: gaming_status_session_started
    event_data:
      player: Adam
  - trigger: event
    event_type: gaming_status_session_switched
    event_data:
      player: Adam
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.game_color is string }}"
actions:
  - action: light.turn_on
    target:
      entity_id: light.game_room
    data:
      brightness_pct: 80
      rgb_color: >
        {% set c = trigger.event.data.game_color.lstrip('#') %}
        {{ [c[0:2] | int(base=16), c[2:4] | int(base=16), c[4:6] | int(base=16)] }}
mode: restart
```

Remove the `event_data` lines to react to every player.

## Example: a whole gaming setup
Turns a gaming setup on when Adam starts playing, keeps the light matched to whatever game he's playing, and turns everything off when he stops:

```yaml
alias: Gaming setup
triggers:
  - trigger: gaming_status.session_started
    target:
      entity_id: sensor.gaming_status_adam_master
    options: {}
    id: started
  - trigger: gaming_status.session_switched
    target:
      entity_id: sensor.gaming_status_adam_master
    options: {}
    id: switched
  - trigger: gaming_status.session_ended
    target:
      entity_id: sensor.gaming_status_adam_master
    options: {}
    id: ended
variables:
  # The game's color as [r, g, b], or white if it has none.
  rgb: >
    {% set c = (trigger.game_color or '') | replace('#', '') %}
    {% if c | length == 6 %}
      {{ [c[0:2] | int(base=16), c[2:4] | int(base=16), c[4:6] | int(base=16)] }}
    {% else %}
      {{ [255, 255, 255] }}
    {% endif %}
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: started
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.gaming_pc_power
          - action: light.turn_on
            target:
              entity_id: light.game_room
            data:
              brightness_pct: 80
              rgb_color: "{{ rgb }}"
      - conditions:
          - condition: trigger
            id: switched
        sequence:
          - action: light.turn_on
            target:
              entity_id: light.game_room
            data:
              rgb_color: "{{ rgb }}"
      - conditions:
          - condition: trigger
            id: ended
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.gaming_pc_power
          - action: light.turn_off
            target:
              entity_id: light.game_room
mode: single
```
