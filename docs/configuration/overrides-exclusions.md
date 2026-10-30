# Overrides & Exclusions
Manually adjust game titles, ratings, and what gets tracked.

* **Game Title Overrides:** Clean up messy or lengthy names. One per line, formatted as `raw name = display name`. *(Example: `Minecraft Launcher = Minecraft`)* The match must be exact, and the replacement happens before any API lookups or dashboard updates, so it's also the fix when cover art lookup fails due to a name mismatch.
    ```
    RaceTheSun = Race The Sun
    Call of Duty = Call of Duty: Black Ops 7
    Army of TWO: TFD = Army of Two: The 40th Day
    ```
* **Title Cleanups:** A list of regex patterns (or plain text, which works the same as a literal pattern) to automatically strip from game names, one per line. *(Example: `Tom Clancy's` on one line, `Sid Meier's` on the next, or a pattern like `(?<=Minecraft)\*.*` to strip any version suffix Minecraft appends after the word "Minecraft*")* Matching is case-insensitive. It's the best way to handle dynamic "Rich Presence" statuses that console integrations append to games. Invalid regex entries are skipped (and logged as a warning) rather than breaking the whole list.
    ```
    Tom Clancy's
    Sid Meier's
    Marvel's
    Director's Cut
    Steam Edition
    : Java Edition
    Open Network Test
    ```
    * *Regex example:* a title like `Minecraft* 26.2 - Multiplayer (Realms)` is reduced to just `Minecraft`, whatever the version number becomes later, by `(?<=Minecraft)\*.*`. It deletes everything from the `*` immediately after "Minecraft" to the end of the title, without touching unrelated titles like "Minecraft Dungeons" that don't have that `*`.
* **Global Exclusions:** Games or apps that should be universally ignored by the tracker, for every player, one per line. *(Example: `YouTube` on one line, `Netflix` on the next)* An excluded app forces the sensor to report "Offline", which keeps streaming apps, music players, and dashboard menus from padding out gaming hours or sending false "Online" triggers. Overrides are applied first, then Cleanups, in that order. An Override is a whole-name swap, and any Cleanup pattern still gets a chance to run against its result afterward. Exclusions are checked against *both* the pre-Cleanup and the fully-cleaned name, so it doesn't matter which form your exclusion entry happens to match: excluding `Minecraft` catches it even if a Cleanup is what produces that name from something longer, and excluding `Minecraft Launcher` still works even if a separate Cleanup (e.g. one that strips `Launcher` generally) would otherwise erase the very word that entry is matching on. Matching ignores case and punctuation (commas, colons, dashes, trademark symbols), so `Marvel Rivals™` and `marvel rivals` both match an exclusion entry of `Marvel Rivals`.
    ```
    Xbox App
    YouTube
    Netflix
    Hulu
    Amazon Prime Video
    Spotify
    Microsoft Store
    Store
    Xbox 360 Dashboard
    Setting up...
    Wallpaper Engine
    ```
    * These same Global Exclusions, combined with each player's own **Exclude Games** list (see [Manage Players](manage-players.md)), are also applied to that player's **Full Game Library Scan** if it's enabled — an excluded title won't show up in their Game Library sensors, and won't count toward the totals, achievement fetches, or artwork lookups either. Library scan data is cached, though, so a newly-added exclusion won't disappear until the next scheduled scan — press that player's **Game Library Refresh** button to apply it immediately instead of waiting.
* **Content Rating Overrides:** *(Only shown when Parental Controls is turned on in its menu)* Manually assign a rating to games your rating provider has no data for. One per line, formatted as `Game = Code`, using the codes `E`, `E10`, `T`, `M`, or `AO`. *(Example: `Skull and Bones = M`)*
