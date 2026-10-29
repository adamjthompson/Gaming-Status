# Sensor Attributes

## Attributes for Master Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The `*_master` sensors provide the following attributes:

**Aggregate Analytics**
| Attribute | Description |
| --- | --- |
| friendly_name | Display name for this player's entity |
| total_daily_hours | Sum of daily hours across all platforms (float) |
| total_weekly_hours | Sum of weekly hours across all platforms (float) |
| rolling_weekly_hours | Sum of rolling hours across all platforms |
| total_weekly_hours_last_week | Sum of last week's hours |
| weekly_breakdown | A copy of `rolling_weekly_breakdown`, retained for backward-compatibility |
| calendar_weekly_breakdown | Consolidated dictionary of all games played across all platforms for the current calendar week (Monday–Sunday), formatted as human-readable strings (e.g., "5h 30m") |
| rolling_weekly_breakdown | Consolidated dictionary of all games played across all platforms, formatted as human-readable strings (e.g., "5h 30m") as a rolling 7-day total |
| raw_rolling_breakdown | Dictionary of game names to total hours (float) over the rolling 7-day window, used by the Gaming Status Cards for chart rendering |
| raw_calendar_breakdown | Dictionary of game names to total hours (float) for the current calendar week, used by the Gaming Status Cards for chart rendering |
| platform_split | A dictionary showing the percentage of total weekly hours spent on each platform (e.g., {"Steam": "55%"}) |
| longest_session | A copy of `rolling_longest_session`, retained for backward-compatibility |
| calendar_longest_session | A formatted string showing the game title and duration of the longest single session across all platforms for the current calendar week (e.g., "DELTARUNE (1h 24m)") |
| rolling_longest_session | A formatted string showing the game title and duration of the longest single session across all platforms over the rolling 7-day window |
| play_history | Per-day, per-game playtime aggregated across all platforms: `{"YYYY-MM-DD": {"Game Title": seconds, ...}}`. Used by the Gaming Status Cards for chart rendering |
| raw_calendar_breakdown_last_week | Same as `raw_calendar_breakdown`, for the previous calendar week |
| all_time_total_hours / all_time_session_count / all_time_top_games | Lifetime hours, session count, and top 10 games across all platforms |
| recent_achievements | Achievement/trophy unlocks merged from every platform (newest first, capped at 30, with up to 5 reserved per platform so one platform's burst can't crowd out another) |
| achievement_tracking_enabled / color_extraction_enabled | Whether achievement tracking is enabled for any of this player's platforms / whether vibrant color extraction is on |
| recent_sessions | List of the most recently completed play sessions across all of this player's platforms (newest first, capped at 20): `{game, platform, platform_key, duration_seconds, date, start_time, end_time, hero_art_url, game_dominant_color}`. `platform` is a display label (a Gaming Status Agent session shows its launcher); `platform_key` is the internal platform key |

**Parental/Limit Controls**
| Attribute | Description |
| --- | --- |
| daily_play_limit_minutes | The configured daily limit |
| remaining_play_time_minutes |Calculated remaining time based on current usage |
| current_game_rating | The rating label (e.g., "Mature") of the currently active game, only populated when the Content Rating Limit rule is enabled |
| rating_exceeded | `true` if the Content Rating Limit rule is enabled and the player's currently active game exceeds the configured age threshold |


**Active Status**
| Attribute | Description |
| --- | --- |
| secondary | Current state or the time elapsed and session duration of the last played game |
| active_platform | The name of the platform currently driving the status (e.g., "Steam"). For Gaming Status Agent this is the reported launcher (e.g., "Epic") |
| active_platform_key | The internal key of the platform driving the status (`steam`, `xbox`, `playstation`, `playnite`, `gsa`, `discord`), for dashboards that need to identify the platform regardless of the launcher label. A Steam or Xbox game from Gaming Status Agent reports `steam` or `xbox` |
| entity_picture | URL of the player's avatar fetched from the active platform |
| icon | Dynamic icon to match the active platform |
| game_cover_art | URL of cover art, either local or SteamGridDB |
| game_hero_art | URL of hero art, either local or SteamGridDB |
| game_logo_art | URL of logo art, either local or SteamGridDB |
| game_icon_art | URL of icon art, either local or SteamGridDB |
| game_dominant_color | The automatically extracted vibrant hex color from the game's artwork, or a manually assigned color |
| game_content_rating | Rating metadata for the currently active game: `{esrb, pegi, age_floor, descriptors, unrated, source}` (`source` is `"steam_native"`, `"xbox_native"`, `"psn_native"`, `"override"`, or `null` if unrated/never looked up) |
| current_game | Inherited from the most active underlying platform tracker |
| play_start_time | Inherited from the most active underlying platform tracker |
| last_played_game | Title of the most recent game detected across all tracked platforms |
| last_online_valid_timestamp | Timestamp of the last time detected online |
| gamertag | The real account name (Steam persona name, Xbox gamertag, PSN online ID, Discord username, or Gaming Status Agent `Profile Name`) of whichever platform is currently active, or the most recently active platform if everything's offline. The `..._pc` sub-master sensor exposes this same attribute, scoped to just its own PC platforms (Playnite, Gaming Status Agent, Custom, Steam, Discord) |

**Identity (per platform)**
| Attribute | Description |
| --- | --- |
| steam_gamertag | This player's Steam persona name, if Steam is configured |
| xbox_gamertag | This player's Xbox gamertag, if Xbox is configured |
| psn_gamertag | This player's PlayStation Network online ID, if PlayStation is configured |
| discord_gamertag | This player's permanent Discord username (not their server nickname), if Discord is configured |
| gsa_gamertag | The `Profile Name` reported by this player's Gaming Status Agent sensor, if Gaming Status Agent is configured |

## Attributes for Platform Sensors
Each sensor has a set of attributes that can be utilized in dashboards charts, etc. The individual `*_steam`, `*_xbox`, `*_playstation`, `*_discord`, `*_playnite`, `*_gsa`, and `*_custom` sensors provide the following attributes:

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
| xbox_suppressed | Xbox sensors only. `true` when a detected game was suppressed because another player's `suppresses_xbox_sensors` setting names this sensor and one of that player's own sensors is confirmed playing the same game (see Smart Ghosting Protection). Always `false` for non-Xbox platforms |
| cached_game_cover | Backup URL for the last known game cover art, used during grace periods or offline states |
| game_cover_art | URL of cover art, either local or SteamGridDB |
| game_hero_art | URL of hero art, either local or SteamGridDB |
| game_logo_art | URL of logo art, either local or SteamGridDB |
| game_icon_art | URL of icon art, either local or SteamGridDB |
| game_dominant_color | The automatically extracted vibrant hex color from the game's artwork, or a manually assigned color |
| game_content_rating | Rating metadata for the currently active/cached game: `{esrb, pegi, age_floor, descriptors, unrated, source}` (`source` is `"steam_native"`, `"xbox_native"`, `"psn_native"`, `"override"`, or `null` if unrated/never looked up) |
| gamertag | This player's real account name for this platform (Steam persona name, Xbox gamertag, PSN online ID, Discord username, or the Gaming Status Agent `Profile Name`). Always `null` for Playnite sensors, which have no equivalent identity concept |
| launcher | Gaming Status Agent sensors only. The launcher the current or last game came from (e.g. `Epic`, `GOG`, `Custom`), kept while offline for "last seen" displays |

**Rich Tracking & Analytics**
| Attribute | Description |
| --- | --- |
| icon | Icon to match the current platform |
| daily_play_time | Total seconds played today |
| weekly_play_time | Total seconds played this calendar week |
| weekly_play_time_last_week | Total seconds played in the previous week |
| last_played_game | Title of the most recently closed game detected on this specific platform |
| weekly_game_breakdown | Dictionary mapping game names to total seconds over the rolling 7-day window (alias of `rolling_weekly_breakdown`) |
| rolling_weekly_breakdown | Dictionary mapping game names to total seconds over the rolling 7-day window |
| calendar_weekly_breakdown | Dictionary mapping game names to total seconds for the current calendar week (Monday–Sunday) |
| calendar_weekly_breakdown_last_week | Same as `calendar_weekly_breakdown`, for the previous calendar week |
| all_time_total_hours / all_time_session_count / all_time_top_games | Lifetime hours, session count, and top 20 games on this platform |
| recent_achievements | Cross-game history of achievement/trophy unlocks on this platform (newest first, capped at 30) |
| current_game_achievements_earned / current_game_achievements_total / achievements_source | *(Achievement tracking only)* Earned/possible counts for the current game and where they came from |
| current_game_gamerscore_earned / current_game_gamerscore_total | *(Xbox only)* Gamerscore for the current game |
| current_game_trophies_bronze / _silver / _gold / _platinum (+ `_total` variants) | *(PlayStation only)* Trophy counts for the current game |
| recent_unlocked_achievements / recent_unlocked_trophies | Recent unlocks for the current game (achievements on Steam/Xbox, trophies on PlayStation) |
| color_extraction_enabled | Whether automatic vibrant color extraction is on |
| play_history | Per-day, per-game playtime for this platform: `{"YYYY-MM-DD": {"Game Title": seconds, ...}}` |
| recent_sessions | List of the most recently completed play sessions on this platform (newest first, capped at 20): `{game, platform, platform_key, duration_seconds, date, start_time, end_time, hero_art_url, game_dominant_color}`. `platform` is a display label (a Gaming Status Agent session shows its launcher); `platform_key` is the internal platform key. A session only appears here once it has ended |
| longest_session_details | Dict of `{game, duration}` (seconds) for the longest single session today; resets at midnight |
| rolling_longest_session_details | Dict of `{game, duration}` (seconds) for the longest single session over the rolling 7-day window |
| calendar_longest_session_details | Dict of `{game, duration}` (seconds) for the longest single session in the current calendar week |

## Attributes for Library Sensors
*(Requires [Full Game Library Scan](../configuration/achievements-ratings.md) to be enabled)*

The sensor's **state** on both the summary and per-platform Library sensors is a raw *count of achievements/trophies earned* — not a percentage and not a game count. On the summary sensor that's the total earned across every tracked platform combined; on a platform sensor (e.g. `..._library_steam`) it's earned on that platform only. The rest of the picture — totals, percentages, game counts — lives in the attributes below.

**Summary Sensor (`..._library_summary`)**
| Attribute | Description |
| --- | --- |
| total_achievements_possible | Sum of every achievement/trophy that exists across all tracked games, on all platforms |
| total_gamerscore | Total Xbox gamerscore earned, summed across all Xbox games |
| total_platinum_trophies | Total PlayStation platinum trophies earned, summed across all PlayStation games |
| average_completion_percent | Average per-game completion percentage across every tracked game on every platform |
| game_count | Total number of games tracked across all platforms |
| tracked_platforms | List of platforms included in this scan (e.g. `["steam", "xbox"]`) |
| last_sync_success | `true` if the most recent scan completed with no errors on any platform |
| platform_errors | `"None"` if the last scan was clean, otherwise a dict of `{platform: error message}` for any platform whose scan failed (e.g. rate-limited, network error, credentials not configured) |
| last_synced | Timestamp of the most recent completed scan |
| games | Full list of every tracked game across all platforms — see below for each entry's shape |
| achievement_backfill_pending / achievement_backfill_total / achievement_backfill_complete | Progress of the Xbox/PlayStation achievement backfill (titles still pending, titles total, and whether it's done) |
| achievement_backfill_by_platform | The same progress broken out per platform: `{xbox: {pending, total}, playstation: {pending, total}}` |

*Paced backfill:* Per-game achievement details on Xbox and PlayStation are fetched gradually in the background, every 15 minutes. A small batch per player (8 Xbox / 5 PlayStation titles), staggered between players, is used to stay within platform rate limits; a title that fails 5 times is skipped. Steam refreshes a rotating slice of the library each scan instead. A large library can therefore take a while to fully populate.

*Steam account check:* Each Steam library remembers which Steam account it was scanned from. If that changes (or on the first scan after updating from a version that didn't record it), games that aren't in the current account's library are dropped instead of kept. This cleans up libraries where an older version mixed in games from the Steam API key owner's account.

**Platform Sensors (`..._library_steam`, `..._library_xbox`, `..._library_playstation`)**
| Attribute | Description |
| --- | --- |
| achievements_total | Sum of every achievement/trophy that exists across this platform's tracked games |
| game_count | Number of games tracked on this platform |
| games | List of games on this platform — see below for each entry's shape |
| playtime_hours | *(Steam only)* Total hours played across every owned Steam game, summed from Steam's own playtime data |
| gamerscore_earned / gamerscore_total | *(Xbox only)* Total gamerscore earned / possible, summed across all Xbox games |
| trophies_bronze / trophies_silver / trophies_gold / trophies_platinum (+ `_total` variants) | *(PlayStation only)* Trophy counts earned / possible, per tier, summed across all PlayStation games |

**Each entry in a `games` list**
| Field | Description |
| --- | --- |
| title | Game name |
| platform | `steam`, `xbox`, or `playstation` |
| id | Platform-specific game identifier (Steam appid, Xbox title_id, or PSN npCommunicationId) |
| achievements_earned / achievements_total | Earned / possible achievements or trophies for this game |
| percent | Completion percentage for this game, `0.0` if the game has no achievements/trophies at all |
| playtime_hours | *(Steam only)* Hours played, from Steam's own playtime data |
| gamerscore_earned / gamerscore_total | *(Xbox only)* Gamerscore earned / possible for this game |
| trophies_earned / trophies_total | *(PlayStation only)* Dicts of `{bronze, silver, gold, platinum}` counts earned / possible |
| game_cover_art / game_hero_art / game_logo_art / game_icon_art | Artwork URLs from SteamGridDB, if configured |

## Attributes for Players Online Sensor
| Attribute | Description |
| --- | --- |
| active_games | A comma-separated string of the games currently being played by online players |

## Note
Several of these attributes (e.g., artwork URLs, breakdown dictionaries, session detail dicts, `play_history`, and `recent_sessions`) are explicitly added to `_unrecorded_attributes` in the sensor classes. This is a deliberate performance optimization to prevent Home Assistant from saving these frequently-changing or large values into the long-term database (recorder), which keeps your database file from growing excessively large.
