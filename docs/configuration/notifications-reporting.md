# Notifications & Reporting
Manage where your [gaming alerts and weekly reports](../notifications.md) are sent.

* **Enable Notifications & Reporting:** Turns session alerts and weekly summaries on or off. Your notification methods and settings are kept while it's off. To flip it without opening another screen, leave the menu dropdown on **Save and return to main menu** and submit.
* **Notification Artwork:** Choose which fetched artwork type (Cover, Hero, Logo, Icon, or No Artwork) attaches to your notifications. Default: Cover.
* **Add Notification:** Map a friendly name (e.g., "Dad's Phone") to an existing Home Assistant `notify.` service. It fully supports standard mobile app notifications, Discord, and SMS.
* **Discord Notification Colors:** Customize the embed colors for Discord alerts (Default, Platform Colors, Game Color, or Custom Hex). Custom Hex has a separate color per event: started playing (default `#00FF00`), finished playing (`#FF0000`), parental alerts (`#0000FF`), and the Rich weekly report (`#F1C40F`).
* **Weekly Report:** Send a summary of everyone's weekly playtime to your selected notification methods on a specific day and time (off by default; default time 09:00). Toggle **Enabled**, pick the **Day** and **Time**, and choose the destinations.
    * **Report Style:** **Simple** (a plain, unranked line per player — the original format, unchanged) or **Rich** (players ranked by hours with their top game(s) for the week, a real Discord embed with artwork for Discord destinations, and a sorted plain-text version for Mobile App/SMS destinations).
    * **Players to Include:** *(Rich style only)* Leave blank to include every tracked player, or pick specific players to limit the report to just them.
    * **Include players with zero hours:** *(Rich style only)* Off by default, matching the original behavior — players with no logged hours last week are left off the report.
    * **Include artwork:** *(Rich style only)* On by default — attaches the top-ranked player's top game artwork to Discord embeds.
    * **Show each player's top game(s):** *(Rich style only)* On by default. Each player's line becomes a header (e.g. `1. Josh (45h 36m)`) followed by one indented line per game, e.g.:
        ```
        1. Josh (45h 36m)
           - Far Far West (22h 3m)
           - Genshin Impact (16h 18m)
           - ULTRAKILL (14h 24m)
        ```
    * **Number of Top Games to Show:** *(Rich style only, shown when the option above is on)* How many of each player's top games to list, 1-5 (default: 1).
    * **Show rank numbers / Show total summary line:** *(Rich style only)* Both on by default, matching the original Rich format — turn either off to trim the report down.
