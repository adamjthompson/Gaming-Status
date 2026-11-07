# Prerequisites

This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have any necessary base integrations installed and working in Home Assistant. Not all are required, but it is recommended that you have at least ONE official integration installed. Discord and Playnite can also be used for game tracking with or without the other integrations, but they are more complicated to set up:

* [Official PlayStation Network Integration](https://www.home-assistant.io/integrations/playstation_network)
* [Official Steam Integration](https://www.home-assistant.io/integrations/steam_online)
* [Official Xbox Integration](https://www.home-assistant.io/integrations/xbox)

## Recommended
While not required for functionality, I recommend installing the following for the most robust dashboard cards and tracking:

* [SteamGridDB API Key](https://www.steamgriddb.com/) - Provides artwork for games. *This is not REQUIRED, but it is HIGHLY recommended!*
* [Gaming Status Cards](https://github.com/adamjthompson/Gaming-Status-Cards) - Easy to use companion dashboard cards, so you don't have to make your own.
* [Official Discord Integration](https://www.home-assistant.io/integrations/discord) - Requires setting up a Discord Bot. *REQUIRED if you want to use Discord for notifications but NOT required for Discord game tracking.*
* [Mosquitto Broker](https://github.com/home-assistant/addons/tree/master/mosquitto) - *REQUIRED if you plan to use Playnite or Gaming Status Agent for tracking games.* You will also need an MQTT add-on installed in Playnite (such as [Playnite MQTT Client](https://playnite.link/addons.html#MQTTClient_90c44048-4f8f-43f7-a0c1-f8164bf1d7ef)) to broadcast your status to Home Assistant.
* [Gaming Status Agent](https://github.com/adamjthompson/Gaming-Status-Agent) - A Windows tray app that detects games from numerous launchers and publishes them to Home Assistant over MQTT. *REQUIRES an MQTT broker (see Mosquitto above). See [Setting up Gaming Status Agent Tracking](../setup/pc-tracking.md).*

### Obtaining a SteamGridDB API Key
To display beautiful, high-resolution game covers on your dashboard, this integration requires a free API key from SteamGridDB.

1. Go to [SteamGridDB.com](https://www.steamgriddb.com/).
2. Click **Login** in the top right corner and authenticate using your Steam account.
3. Click your profile picture in the top right and select **Preferences**.
4. Navigate to the **API** tab on the left menu.
5. Click **Generate API Key**.
6. Copy the generated key and paste it into the **SteamGridDB API Key** field during Initial Setup or under the Advanced menu. 

### Content Ratings
If you plan to use the **Content Rating Limit** parental control, enable **Enable Native Content Ratings** (either under the **Achievements & Ratings** menu or on the main **Parental Controls** screen). It's the same setting, shown in both places for convenience. This looks up each game's ESRB rating directly from Steam, Xbox, or PlayStation, reusing whatever credentials you already have configured on the official integrations. No separate API key or account needed. This is independent from achievement/trophy tracking, so you don't need to enable that (or its heavier credential/polling requirements) just to get ratings working. Content Rating Limit's own fields only appear on a player's screen once this is on, since there's nothing for the rule to check against otherwise.

*Note: native rating coverage isn't complete for every title. If a game comes back unrated, you can manually assign it a rating using the Content Rating Overrides field under [Overrides & Exclusions](../configuration/overrides-exclusions.md).*

### Epic Games Achievements
Epic achievements are read from a player's **public Epic profile**, so no Epic sign-in or API key is needed. Epic works a little differently from the other platforms, because there's no Epic status sensor:

| Feature | Requirement(s) |
| --- | --- |
| Epic library (games, completion, XP) via the [Full Game Library Scan](../configuration/achievements-ratings.md) | The player's Epic account only |
| Online status and the Epic game being played | [Gaming Status Agent](../setup/pc-tracking.md), which reports games from the Epic launcher |
| Live achievement counts and unlocks for the current Epic game | Gaming Status Agent plus the Epic account |
| Epic unlocks in the player's achievement history and unlock notifications | Gaming Status Agent plus the Epic account (the history is kept on the Agent sensor) |

Without the Agent, the Epic library sensor still works on its own; it just has no live status or unlock history to go with it.

1. Check **Epic Games** under **Platforms to Track** in [Global Settings](../configuration/global-settings.md). This adds the **Epic account** field to each player.
2. Make the profile public: sign in at [epicgames.com](https://www.epicgames.com/), open your account's privacy settings, and set your profile and its achievements to public.
3. Copy the profile link: open your profile in the Epic Games Store (it looks like `https://store.epicgames.com/u/<32 characters>`). The 32-character account ID shown in your Epic account settings also works.
4. Paste it into the player's **Epic account** field under [Manage Players](../configuration/manage-players.md). Gaming Status checks the profile when you save and tells you if it's private or can't be found.

*Note: only games that use Epic's own achievement system have Epic achievements; many Epic games don't (for example, Marvel's Guardians of the Galaxy), and those simply show none. A game also only appears on a public profile after its first achievement is unlocked.*

### Ubisoft Achievements
Ubisoft keeps achievement data only on the gaming PC, inside Ubisoft Connect, so it comes entirely from [Gaming Status Agent](../setup/pc-tracking.md). The Agent reads Ubisoft Connect's local files (read-only) and sends the library and unlocks to Home Assistant over MQTT, including each achievement's real icon.

| Feature | Requirement(s) |
| --- | --- |
| Ubisoft library (games with achievements, completion) via the [Full Game Library Scan](../configuration/achievements-ratings.md) | Gaming Status Agent with Ubisoft ticked and **Send Ubisoft achievements and library to Home Assistant** on (both under the Agent's **Platforms**) |
| Live achievement counts for the Ubisoft game being played | The same, plus the game launched from Ubisoft Connect |
| Ubisoft unlocks in the player's achievement history and unlock notifications | The same; unlocks arrive within seconds |

1. Update Gaming Status Agent to a version with Ubisoft achievements, and make sure Ubisoft and **Send Ubisoft achievements and library to Home Assistant** are ticked under its **Platforms**.
2. Check **Ubisoft** under **Platforms to Track** in [Global Settings](../configuration/global-settings.md). There's no account to enter: the player's Gaming Status Agent sensor is the source.
3. Turn on Ubisoft for the player under Achievement/Trophy Tracking and/or Full Game Library Scan in their Player Details.

*Notes: only games played on that PC have achievement data, so games played elsewhere don't appear until they're played there. Copies of a game from other stores (e.g. its Steam copy) are merged into one entry. Ubisoft games launched through Steam still count toward the library, but only games launched from Ubisoft Connect get the live current-game view.*
