# 🎨 Dashboard UI Examples

To get the most out of the Gaming Status integration, you can use these advanced Lovelace YAML cards to create a beautiful, dynamic gaming dashboard. 

### ⚠️ Frontend Prerequisites
These dashboard examples rely on a few popular custom frontend cards. You will need to install these via HACS (Frontend section) before the YAML below will work:
* [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom) (For the beautiful base layouts)
* [Card-Mod](https://github.com/thomasloven/lovelace-card-mod) (For the blurred backgrounds and borders)
* [Auto-Entities](https://github.com/thomasloven/lovelace-auto-entities) (To automatically hide offline players)
* [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (For the stats graph)
* [Custom Button-Card](https://github.com/custom-cards/button-card) (For the slideshow)

---

## 1. The "Currently Playing" Card
This card uses `auto-entities` to automatically show anyone who is currently online. It dynamically sets the border color and icon based on the active platform (Steam, Xbox, PlayStation) and uses `card-mod` to pull the game cover art, blur it, and use it as the background! It hides completely if no one is playing.

~~~yaml
type: grid
cards:
  - type: heading
    icon: mdi:controller
    heading: Playing
    heading_style: title
    grid_options:
      columns: 12
      rows: auto
  - type: custom:auto-entities
    card:
      type: vertical-stack
    card_param: cards
    filter:
      include:
        - options:
            type: custom:mushroom-template-card
            primary: >
              {{ state_attr(entity, 'friendly_name') | replace(' Gaming Status', '') }}
            # --- DYNAMIC ICON LOGIC ---
            badge_icon: |
              {% set p = state_attr(entity, 'active_platform') | lower %}
              {% if 'steam' in p %} mdi:steam
              {% elif 'xbox' in p %} mdi:microsoft-xbox
              {% elif 'playstation' in p %} mdi:sony-playstation
              {% else %} mdi:gamepad-variant {% endif %}
            # --- DYNAMIC BADGE COLOR LOGIC ---
            badge_color: |
              {% set p = state_attr(entity, 'active_platform') | lower %}
              {% if 'steam' in p %} rgb(2, 173, 239)
              {% elif 'xbox' in p %} rgb(11, 124, 16)
              {% elif 'playstation' in p %} rgb(0, 48, 135)
              {% else %} rgb(100, 50, 100) {% endif %}
            tap_action:
              action: more-info
            secondary: |
              {{ states(entity) }} {{ state_attr(entity, 'secondary') }}
            picture: "{{ state_attr(entity, 'entity_picture') }}"
            card_mod:
              style: |
                ha-card {
                  position: relative;
                  overflow: hidden;

                  /* --- PLATFORM BORDER COLOR --- */
                  {% set p = state_attr(config.entity, 'active_platform') | lower %}
                  {% if 'steam' in p %} 
                    {% set color = '2, 173, 239' %}
                  {% elif 'xbox' in p %} 
                    {% set color = '11, 124, 16' %}
                  {% elif 'playstation' in p %} 
                    {% set color = '0, 48, 135' %}
                  {% else %} 
                    {% set color = '100, 50, 100' %} 
                  {% endif %}
                  
                  border-right: 8px solid rgb({{color}});
                }
                ha-card::before {
                  content: '';
                  position: absolute;
                  top: -10px;
                  left: -10px;
                  right: -10px;
                  bottom: -10px;
                  /* --- BLURRED BACKGROUND ART --- */
                  /* Uses game cover art, falls back to profile picture if missing */
                  background-image: linear-gradient(-90deg,rgba(0, 0, 0, 0), rgba(0, 0, 0, 1)), url('{{ state_attr(config.entity, 'game_cover_art') or state_attr(config.entity, 'entity_picture') }}');
                  background-size: cover;
                  background-position: center;
                  filter: blur(5px);
                  z-index: 0;
                  pointer-events: none;
                }
                ha-card > * {
                  position: relative;
                  z-index: 1;
                  pointer-events: none;
                }
          # Target all master sensors
          entity_id: sensor.*_gaming_status
          sort:
            reverse: true
            method: last_changed
      # Ignore anyone who is Offline
      exclude:
        - options: {}
          state: Offline
    grid_options:
      columns: 12
      rows: auto
column_span: 1
# Requires the "Anyone Gaming" template sensor (see templates.md)
visibility:
  - condition: state
    entity: binary_sensor.anyone_gaming
    state: "on"
~~~

---

## 2. The "Active Games" Slideshow
This custom button-card uses Javascript to scan your network for active games and generates a beautiful, animated CSS slideshow of the cover art. 

*Note: You must list your specific Master Gaming Sensors in the `triggers_update` section so the card knows when to refresh.*

~~~yaml
type: grid
cards:
  - type: heading
    heading: Active Games
    heading_style: title
    icon: mdi:motion-play-outline
  - type: custom:button-card
    show_name: true
    show_icon: false
    show_state: false
    tap_action:
      action: none
    # UPDATE THESE to match your actual Master Sensors!
    triggers_update:
      - sensor.player_one_gaming_status
      - sensor.player_two_gaming_status
      - sensor.player_three_gaming_status
    styles:
      card:
        - background: none
        - padding: 0px
        - border: none
        - box-shadow: none
        - overflow: hidden
      name:
        - width: 100%
        - padding: 0
        - align-self: start
        - justify-self: start
    name: |
      [[[
        // --- SLIDESHOW CONFIGURATION ---
        var time_per_slide = 5; // Seconds to show each game
        var transition_time = 1; // Seconds to crossfade

        // --- FIND GAMES ---
        var active_games = [];
        var seen_list = [];

        // Scan all sensors in Home Assistant
        Object.keys(states).forEach(key => {
          if (key.endsWith('_gaming_status')) {
            var s = states[key];
            
            // 1. Standard Exclusions
            var is_excluded = ['Offline', 'unavailable', 'unknown', 'idle'].includes(s.state);
            
            // 2. "Last Seen" Filter (Ignore history statuses)
            var is_history = s.state.toLowerCase().includes('last seen') || s.state.toLowerCase().includes('ago');

            if (!is_excluded && !is_history) {
              var game_name = s.attributes.current_game;
              var game_art = s.attributes.game_cover_art;

              // Prevent duplicates if two people are playing the same game
              if (game_name && game_art && !seen_list.includes(game_name)) {
                seen_list.push(game_name);
                active_games.push({name: game_name, art: game_art});
              }
            }
          }
        });

        var total_games = active_games.length;

        // --- RENDER CONTENT ---
        
        // CASE A: No Games Active
        if (total_games === 0) {
           return `<div style="padding: 40px; text-align: center; background: rgba(100,100,100,0.5); color: white;">
                    <ha-icon icon="mdi:gamepad-variant-outline" style="width: 40px; height: 40px; opacity: 0.5;"></ha-icon><br>
                    No active games
                  </div>`;
        }

        // CASE B: One Game (Static image)
        if (total_games === 1) {
          return `<div style="width: 100%; aspect-ratio: 192/112; background-image: url('${active_games[0].art}'); background-size: cover; background-position: center;"></div>`;
        }

        // CASE C: Multiple Games (Animated Slideshow)
        var slideshow_html = `<div style="position: relative; width: 100%; aspect-ratio: 192/112; overflow: hidden;">`;
        
        var loop_duration = total_games * time_per_slide;
        var pct_fade = (transition_time / loop_duration) * 100;
        var pct_visible = ((time_per_slide - transition_time) / loop_duration) * 100;

        var game_ids = active_games.map(g => g.name.replace(/[^a-zA-Z0-9]/g, '')).join('');
        var anim_name = `anim_${game_ids}`;

        var slide_style = `<style>
          @keyframes ${anim_name} {
            0% { opacity: 0; }
            ${pct_fade}% { opacity: 1; }
            ${pct_fade + pct_visible}% { opacity: 1; }
            ${pct_fade + pct_visible + pct_fade}% { opacity: 0; }
            100% { opacity: 0; }
          }
        </style>`;
        
        slideshow_html += slide_style;

        active_games.forEach((g, index) => {
          var delay = index * time_per_slide;
          slideshow_html += `<div style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-image: url('${g.art}'); background-size: cover; background-position: center; opacity: 0; animation: ${anim_name} ${loop_duration}s infinite; animation-delay: ${delay}s;"></div>`;
        });

        slideshow_html += `</div>`;
        return slideshow_html;
      ]]]
visibility:
  - condition: state
    entity: binary_sensor.anyone_gaming
    state: "on"
column_span: 1
~~~

---

## 3. The Playtime Stats Chart
This uses `apexcharts-card` to render a gradient bar chart showing daily play hours for the last 7 days, with rolling weekly totals displayed in the header.

~~~yaml
type: grid
cards:
  - type: heading
    heading: Stats
    heading_style: title
    icon: mdi:signal-cellular-3
    grid_options:
      columns: 24
      rows: 1
  - type: custom:apexcharts-card
    header:
      show: true
      show_states: true
      colorize_states: true
    graph_span: 8d
    span:
      end: day
      offset: +1d
    apex_config:
      fill:
        opacity: 1
        type: gradient
        gradient:
          type: vertical
          shadeIntensity: 0
          opacityFrom: 1
          opacityTo: 0.5
          stops: [0, 95, 100]
      chart:
        height: 350px
        parentHeightOffset: 10
        toolbar: { show: false }
        zoom: { enabled: false }
      grid:
        padding: { left: 0, right: 0 }
      xaxis:
        type: datetime
        labels:
          hideOverlappingLabels: false
          datetimeFormatter:
            year: dd
            month: dd
            day: dd
          trim: false
        tooltip: { enabled: false }
      legend: { show: false }
      tooltip:
        x:
          format: dd
          show: false
    series:
      # --- PLAYER ONE ---
      - entity: sensor.player_one_gaming_status
        attribute: rolling_weekly_hours # Shows in header
        name: Player One
        color: rgb(255, 190, 11)
        show:
          in_header: true
          in_chart: false
      - entity: sensor.player_one_gaming_status
        attribute: total_daily_hours # Shows as bars on chart
        name: Player One
        type: column
        color: rgb(255, 190, 11)
        show:
          in_header: false
          in_chart: true
        group_by:
          func: last
          duration: 1d
          fill: zero
          
      # --- PLAYER TWO ---
      - entity: sensor.player_two_gaming_status
        attribute: rolling_weekly_hours
        name: Player Two
        color: rgb(251, 86, 7)
        show:
          in_header: true
          in_chart: false
      - entity: sensor.player_two_gaming_status
        attribute: total_daily_hours
        name: Player Two
        type: column
        color: rgb(251, 86, 7)
        show:
          in_header: false
          in_chart: true
        group_by:
          func: last
          duration: 1d
          fill: zero
          
      # Add more players following the same pattern above!
    grid_options:
      columns: 24
      rows: auto
column_span: 2
~~~

---

## 4. The "Recent / History" Card
This card splits users into two buckets: those currently online (colorful) and those offline (grayscale). It sorts them by who was most recently active.

~~~yaml
type: grid
cards:
  - type: heading
    heading: Recent
    heading_style: title
    icon: mdi:controller
    grid_options:
      rows: 1
  - type: custom:auto-entities
    card:
      type: vertical-stack
    card_param: cards
    filter:
      include:
        # 1. ONLINE USERS (Colorful)
        - entity_id: sensor.*_gaming_status
          state: /^(?!Offline|offline|unavailable|unknown).+/
          sort:
            method: last_changed
            reverse: true
          options:
            type: custom:mushroom-template-card
            primary: >-
              {{ state_attr(entity, 'friendly_name') | replace(' Gaming Status', '') }}
            secondary: "{{ states(entity) }} {{ state_attr(entity, 'secondary') }}"
            picture: "{{ state_attr(entity, 'entity_picture') }}"
            badge_icon: |
              {% set p = state_attr(entity, 'active_platform') | lower %}
              {% if 'steam' in p %} mdi:steam
              {% elif 'xbox' in p %} mdi:microsoft-xbox
              {% elif 'playstation' in p %} mdi:sony-playstation
              {% else %} mdi:gamepad-variant {% endif %}
            badge_color: |
              {% set p = state_attr(entity, 'active_platform') | lower %}
              {% if 'steam' in p %} rgb(2, 173, 239)
              {% elif 'xbox' in p %} rgb(11, 124, 16)
              {% elif 'playstation' in p %} rgb(0, 48, 135)
              {% else %} rgb(100, 50, 100) {% endif %}
            tap_action: { action: more-info }
            card_mod:
              style: |
                ha-card {
                  position: relative; overflow: hidden; pointer-events: none;
                  /* Colored border for online */
                  {% set p = state_attr(config.entity, 'active_platform') | lower %}
                  {% if 'steam' in p %} {% set color = '2, 173, 239' %}
                  {% elif 'xbox' in p %} {% set color = '11, 124, 16' %}
                  {% elif 'playstation' in p %} {% set color = '0, 48, 135' %}
                  {% else %} {% set color = '100, 50, 100' %} {% endif %}
                  border-right: 8px solid rgb({{color}});
                }
                ha-card::before {
                  content: ''; position: absolute; top: -10px; left: -10px; right: -10px; bottom: -10px;
                  {% set game_art = state_attr(config.entity, 'game_cover_art') %}
                  {% set profile_pic = state_attr(config.entity, 'entity_picture') %}
                  {% set bg_image = game_art if game_art else profile_pic %}
                  background-image: linear-gradient(-90deg, rgba(0, 0, 0, 0), rgba(0, 0, 0, 1)), url('{{ bg_image }}');
                  background-size: cover; background-position: center;
                  filter: blur(5px) brightness(0.7); z-index: 0;
                }
                ha-card > * { position: relative; z-index: 1; }
                
        # 2. OFFLINE USERS (Grayscale)
        - entity_id: sensor.*_gaming_status
          state: /^(Offline|offline|unavailable|unknown)$/
          sort:
            method: attribute
            attribute: last_online_valid_timestamp
            reverse: true
          options:
            type: custom:mushroom-template-card
            primary: >-
              {{ state_attr(entity, 'friendly_name') | replace(' Gaming Status', '') }}
            secondary: "{{ state_attr(entity, 'secondary') }}"
            picture: "{{ state_attr(entity, 'entity_picture') }}"
            badge_icon: |
              {% set p = state_attr(entity, 'active_platform') | lower %}
              {% if 'steam' in p %} mdi:steam
              {% elif 'xbox' in p %} mdi:microsoft-xbox
              {% elif 'playstation' in p %} mdi:sony-playstation
              {% else %} mdi:gamepad-variant {% endif %}
            badge_color: grey # Grey badge for offline
            tap_action: { action: more-info }
            card_mod:
              style: |
                ha-card { position: relative; overflow: hidden; pointer-events: none; }
                ha-card::before {
                  content: ''; position: absolute; top: -10px; left: -10px; right: -10px; bottom: -10px;
                  {% set game_art = state_attr(config.entity, 'game_cover_art') %}
                  {% set profile_pic = state_attr(config.entity, 'entity_picture') %}
                  {% set bg_image = game_art if game_art else profile_pic %}
                  background-image: linear-gradient(-90deg, rgba(0, 0, 0, 0), rgba(0, 0, 0, 1)), url('{{ bg_image }}');
                  background-size: cover; background-position: center;
                  /* Grayscale effect for offline */
                  filter: blur(5px) grayscale(100%) brightness(0.5); z-index: 0;
                }
                ha-card > * { position: relative; z-index: 1; }
    grid_options:
      columns: 12
      rows: auto
~~~

---

## 5. Platform-Specific Breakdowns
If you want to view all Steam users or all Xbox users in one spot, you can target the child sensors (e.g., `sensor.*_steam`) instead of the master sensor.

*Below is the template for **Steam**. To adapt this for **Xbox** or **PlayStation**, simply replace `sensor.*_steam` with `sensor.*_xbox` and adjust the colors/icons!*

~~~yaml
type: grid
cards:
  - type: heading
    heading: Steam
    heading_style: title
    icon: mdi:steam
    grid_options:
      rows: 1
  - type: custom:auto-entities
    card:
      type: vertical-stack
    card_param: cards
    filter:
      include:
        # --- ONLINE STEAM USERS ---
        - options:
            type: custom:mushroom-template-card
            primary: "{{ state_attr(entity, 'friendly_name') | replace(' Steam', '') }}"
            badge_icon: mdi:steam
            tap_action: { action: more-info }
            secondary: |
              {{ states(entity) }} {{ state_attr(entity, 'secondary') }}
            picture: "{{ state_attr(entity, 'entity_picture') }}"
            badge_color: rgb(2, 173, 239)
            card_mod:
              style: |
                ha-card {
                  position: relative; overflow: hidden; pointer-events: none;
                  border-right: 8px solid rgb(2, 173, 239);
                }
                ha-card::before {
                  content: ''; position: absolute; top: -10px; left: -10px; right: -10px; bottom: -10px;
                  background-image: linear-gradient(-90deg, rgba(0, 0, 0, 0.5), rgba(2, 173, 239, 1)), url('{{ state_attr(config.entity, 'entity_picture') }}');
                  background-size: cover; background-position: center; filter: blur(5px); z-index: 0;
                }
                ha-card > * { position: relative; z-index: 1; }
          # Filter for the Steam component
          entity_id: sensor.*_steam
          state: /^(?!Offline|offline|unavailable|unknown).+/
          sort:
            method: attribute
            attribute: last_online_valid_timestamp
            reverse: true
            
        # --- OFFLINE STEAM USERS ---
        - options:
            type: custom:mushroom-template-card
            primary: "{{ state_attr(entity, 'friendly_name') | replace(' Steam', '') }}"
            badge_icon: mdi:steam
            tap_action: { action: more-info }
            secondary: |
              {{ state_attr(entity, 'secondary') }}
            picture: "{{ state_attr(entity, 'entity_picture') }}"
            badge_color: rgb(2, 173, 239)
            card_mod:
              style: |
                ha-card { position: relative; overflow: hidden; pointer-events: none; }
                ha-card::before {
                  content: ''; position: absolute; top: -10px; left: -10px; right: -10px; bottom: -10px;
                  background-image: linear-gradient(-90deg, rgba(0, 0, 0, 0.5), rgba(2, 173, 239, 1)), url('{{ state_attr(config.entity, 'entity_picture') }}');
                  background-size: cover; background-position: center; filter: blur(5px); z-index: 0;
                }
                ha-card > * { position: relative; z-index: 1; }
          # Filter for the Steam component
          entity_id: sensor.*_steam
          state: /^(Offline|offline|unavailable|unknown)$/
          sort:
            method: attribute
            attribute: last_online_valid_timestamp
            reverse: true
    grid_options:
      columns: 12
      rows: auto
column_span: 1
~~~