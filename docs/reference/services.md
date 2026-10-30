# Services
Beyond the dashboard, Gaming Status exposes five Home Assistant services (**Developer Tools → Actions**, or callable from automations/scripts) for cleaning up a player's stored game history after the fact — useful when a data source ever misreports a game's name (e.g. `DOOM Eternal In the menu` instead of `DOOM Eternal`) or when you simply want to remove a game's history entirely, or to fix individual sessions.

**`gaming_status.rename_game`**
Renames a game across a player's stored history (recent sessions, daily/weekly breakdowns, and archived per-day totals), merging its playtime into an existing name if one already matches. Does **not** rename a currently in-progress session — the fix applies once that session ends naturally.

| Field | Required | Description |
| --- | --- | --- |
| `player` | Yes | The player's configured name, e.g. `Player1` |
| `platform` | No | Limit the rename to one platform (`steam`, `xbox`, `playstation`, `playnite`, `gsa`, `discord`). Omit to apply across all of the player's platforms |
| `old_name` | Yes | The exact game name currently stored, e.g. `DOOM Eternal In the menu` |
| `new_name` | Yes | The name to rename it to, e.g. `DOOM Eternal` |

**`gaming_status.delete_game`**
Permanently purges every trace of a named game from a player's stored history — every recorded session, today's live totals, every archived day's contribution to daily/weekly breakdowns, and any recorded achievement/trophy unlocks for that game. **This cannot be undone.**

| Field | Required | Description |
| --- | --- | --- |
| `player` | Yes | The player's configured name, e.g. `Player1` |
| `platform` | No | Limit the deletion to one platform. Omit to purge across all of the player's platforms |
| `game` | Yes | The exact game name to delete |

**`gaming_status.delete_session`**
Permanently removes one specific recorded session, correcting only that session's contribution to daily/weekly/lifetime totals. Only works for sessions still in that sensor's `recent_sessions` list.

| Field | Required | Description |
| --- | --- | --- |
| `player` | Yes | The player's configured name |
| `platform` | No | Limit the search to one platform. Omit to search all of the player's platforms |
| `game` | Yes | The session's game name |
| `start_time` | Yes | The session's exact `start_time` (ISO timestamp), as shown in `recent_sessions` |

**`gaming_status.add_session`**
Manually inserts a completed session (e.g. to backfill play that was never tracked), updating totals the same way a tracked session would.

| Field | Required | Description |
| --- | --- | --- |
| `player` | Yes | The player's configured name |
| `platform` | Yes | Exactly one platform to add the session to, e.g. `steam` |
| `game` | Yes | The game name |
| `start_time` | Yes | ISO timestamp the session started |
| `end_time` | Yes | ISO timestamp the session ended (must be after `start_time`) |

**`gaming_status.reassign_session`**
Moves one recorded session to another player (and/or platform), correcting totals on both ends — useful when the wrong account was signed in on a shared console or app.

| Field | Required | Description |
| --- | --- | --- |
| `from_player` | Yes | The player the session is currently attributed to |
| `from_platform` | No | Limit the search to one of `from_player`'s platforms |
| `game` | Yes | The session's game name |
| `start_time` | Yes | The session's exact `start_time`, as shown in the source sensor's `recent_sessions` |
| `to_player` | Yes | The player to move the session to |
| `to_platform` | Yes | Exactly one platform on `to_player` to move it onto |

The rename and delete services can also be triggered from a dashboard using the [Game Management card](https://github.com/adamjthompson/Gaming-Status-Cards#7-gaming-status---game-management), which provides player/platform/game dropdowns populated from real data instead of requiring you to type exact names.
