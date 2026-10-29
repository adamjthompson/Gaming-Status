![Gaming Status Logo](assets/logo.png)

# Gaming Status for Home Assistant

This is a powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, Discord, Playnite, and the Gaming Status Agent PC app into a single, clean dashboard sensor for each person in your household / friend group. It's super useful if you want to be able to track who is online, what they're playing, and for how long. 

I started developing this as a way to have persistent sensors since I was annoyed that the Xbox and Steam sensors would regularly flutter between online/offline status, making notifications and my gaming dashboard unreliable. This evolved into making master sensors instead of simply tracking each platform independently. Over time, this has grown into a much more complex integration that now tracks game time, records the last game played, provides cover art, allows for rich notifications through Discord and much, much more.

Some of the key features are listed below.

## Features

* **Unified Master Sensor:** Combines Xbox, PlayStation, Steam, Discord, Playnite, and Gaming Status Agent into one clean "Master Status" sensor per person.
* **Online/Offline Notifications:** Receive Discord, SMS, and/or Mobile notifications when users start or finish playing a game.
* **Parental Controls:** Track daily playtime and receive notifications when a limit, curfew, or age-rating threshold is reached.
* **Discord Rich Presence:** Track hundreds of standalone games, emulators, and Epic/EA/Ubisoft launchers automatically by hooking into Discord's Rich Presence status. Automatically ignores custom text statuses to prevent false positives.
* **Gaming Status Agent Support:** Track PC games from Amazon Games, Battle.net, EA, Epic, GOG, HoYoverse, Minecraft (Java Edition), Playnite, Riot Games, Roblox, Rockstar Games, and Ubisoft (plus your own custom game rules) with the companion [Gaming Status Agent](https://github.com/adamjthompson/Gaming-Status-Agent) Windows app, which reports the game title, launcher, and gamertag over MQTT.
* **PC Sub-Master Sensor:** Automatically aggregates Gaming Status Agent, Steam, Playnite, and Discord tracking into a single, unified "PC" status. It features smart platform yielding (e.g., Discord quietly steps aside if Steam is tracking the same game) to eliminate double-counting in your playtime analytics.
* **Smart Ghosting Protection:** Prevents echo sessions on a shared PC — e.g., when the Windows Xbox app incorrectly broadcasts another player's game (on Steam, PlayStation, or their own Xbox) as its own. Configured on the *authoritative* player (pick which other players' Xbox sensors to suppress).
* **Drop-Out Protection:** Built-in grace periods prevent a gamer from appearing "Offline" if their game crashes, they switch titles, or their internet briefly blips, keeping play sessions perfectly intact and avoiding unnecessary notifications.
* **Playtime Analytics:** Automatically calculates session time, daily hours, and a rolling 7-day total for easy dashboard charting.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.
* **Advanced Exclusion Filtering:** Prevent media apps (Netflix, YouTube, Spotify) or background processes from triggering gaming statuses using a global or per-player exclusions list — the same list also keeps those titles out of the optional full game library scan below.
* **Customizable Cover Art:** Automatically fetches gorgeous images from SteamGridDB and passes them to your dashboard via URL and caches them locally for fast updates.
* **"Last Seen" Memory:** When gamers go offline, the sensor retains their last played game and calculates exactly how long ago they were active (e.g., *Last seen 3h ago: Genshin Impact (1h 37m)*).
* **Custom Avatars:** Automatically pulls live gamer pictures from platform APIs, with the option to override missing or incorrect images with your own local images.
* **Native Content Ratings:** Look up each game's ESRB/age rating directly from Steam, Xbox, or PlayStation — no extra API key or account, and it reuses whatever credentials your existing integrations already have.
* **Native Achievement/Trophy Tracking:** Pull real achievement/trophy counts, recent unlocks, and gamerscore for whatever game is currently being played, straight from Steam, Xbox, or PlayStation.
* **Full Game Library Scan:** Periodically scans a player's *entire* game library (not just what's currently running), tracking total achievements/trophies, completion percentage, and — for Steam — playtime per game, with a manual refresh button for instant updates.
* **Public Status Page:** An optional, read-only page served by Home Assistant that anyone with the link can view without logging in, showing only the players you choose, using the same cards as your dashboards.
* **Per-Player Devices:** Every player's sensors and buttons are grouped under one Home Assistant device (Settings → Devices & Services) for a clean, unified view instead of a flat list of unrelated entities.
