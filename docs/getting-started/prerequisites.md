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
Epic achievements are read from a player's **public Epic profile**, so no Epic sign-in or API key is needed. They cover the Epic game a player is currently playing (as reported by [Gaming Status Agent](../setup/pc-tracking.md)) and the [Full Game Library Scan](../configuration/achievements-ratings.md).

1. Make the profile public: sign in at [epicgames.com](https://www.epicgames.com/), open your account's privacy settings, and set your profile and its achievements to public.
2. Copy the profile link: open your profile in the Epic Games Store (it looks like `https://store.epicgames.com/u/<32 characters>`). The 32-character account ID shown in your Epic account settings also works.
3. Paste it into the player's **Epic account** field under [Manage Players](../configuration/manage-players.md). Gaming Status checks the profile when you save and tells you if it's private or can't be found.

*Note: only games that use Epic's own achievement system have Epic achievements; many Epic games don't (for example, Marvel's Guardians of the Galaxy), and those simply show none. A game also only appears on a public profile after its first achievement is unlocked.*
