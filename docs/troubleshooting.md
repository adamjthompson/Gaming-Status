# 🛠️ Troubleshooting & FAQ
Below are some of the possible issues you may encounter and their fixes.

---

## My game is being tracked by more than one platform at the same time
This generally happens if you're using Discord for tracking along with other services. Sometimes, game titles are reported differently, resulting in a game being tracked by more than one service simultaneously. For example, Xbox may track the title as "DOOM Eternal" while Discord tracks it as "DOOM Eternal (BATTLEMODE)". The solution is to add a game title override to the [**Advanced Settings**](advanced.md#game-title-overrides) to normalize the name. This would look like "DOOM Eternal (BATTLEMODE) = DOOM Eternal". Once the names match, the prioritization of trackers will take effect, with Xbox tracking the game and Discord stepping out of the way.

## My sensor always says "Offline" even when I'm playing!
One possibility is the privacy settings of your account. Your online status and game activity must be viewable by the public in order to be properly reported by the Gaming Status integration. Instructions for changing the necessary settings is included below:

### PlayStation Network (PSN)
*Please remember that only the <ins>official</ins> PlayStation Integration will work properly with Gaming Status; if you have the HACS version installed, uninstall or disable it and install the official integration.* If your PSN sensor reports as "Offline" while you are gaming, your PSN privacy settings are likely restricting API access.

**On your PS5 Console:**
- Go to Settings > Users and Accounts > Privacy.
- Select View and Customize Your Privacy Settings.
- Scroll down to your activity and locate the option for "Who can see your online status and what you're currently playing".
- Change this setting to Anyone.

*Note on Hidden Games: In the same Privacy menu, check the "Hide your games from other players" setting. Any game toggled to be hidden in this list will refuse to broadcast its title to Home Assistant.*

### Steam
Steam separates your overall profile visibility from your gaming activity. If "Game details" are restricted, Home Assistant will see you as online but will not know what game you are playing and report you as "Offline".

**Global Privacy Settings:**
- Go to your Steam Profile in the desktop client or web browser.
- Click Edit Profile, then navigate to the Privacy Settings tab.
- Ensure My profile is set to Public.
- Ensure Game details is set to Public. This specific setting controls whether you are seen as "in-game" and broadcasts the title of the software you are running.

**Per-Game Privacy:**
Steam allows you to mark individual games as private. If a specific game is not showing up, right-click the game in your Steam Library, click the gear icon on the game details page, and go to Privacy to ensure it is not marked as a private game.

### Xbox / Microsoft
Xbox possesses highly granular privacy controls that explicitly separate your online status from your game history. Both must be publicly visible for the integration to function properly.

**Via Web Browser (Recommended):**
- Sign in to Xbox.com and click your gamerpic in the top-right corner of the page.
- Click "..." (More options) > Xbox settings > Privacy & online safety > Privacy.
- Under the "Others can" section, find "Others can see if you're online" and set it to Everyone.
- Find "Others can see your game and app history" and set it to Everyone.
- Scroll to the bottom and click Submit to save your changes.

**Via Xbox Console:**
- Press the Xbox button to open the guide, then go to Profile & system > Settings.
- Navigate to Account > Privacy & online safety > Xbox privacy > View details & customize.
- Select Online status & history.
- Ensure both "Others can see if you're online" and "Others can see your game and app history" are set to allow visibility.

**For PC Gamers:**
- If you are playing non-Xbox games on a Windows PC, open the Game Bar by pressing the Windows key + G.
- Go to the Xbox Social widget, click Xbox Social Options in the title bar, then select Account.
- Under "Others can see your PC game activity", ensure this is set to Allow so that Microsoft can collect your PC game activity and determine what you are playing.

### Discord
Discord requires specific settings to be enabled on both your local client and the specific server where your Home Assistant bot resides. If your status is not updating, Discord is likely refusing to broadcast your Rich Presence data.

**Client Activity Privacy:**
- Open Discord and click the gear icon (User Settings) in the bottom left.
- Navigate to Activity Privacy under the Activity Settings section.
- Ensure Display current activity as a status message is toggled ON. This is the master switch that allows Discord to broadcast what game you are playing.

**Server-Specific Privacy:**
Even if your global activity is enabled, Discord allows you to hide your status from specific servers. If your bot is on a server where your activity is hidden, it will not be able to track your games.
- Open the Discord server where your Home Assistant bot is located.
- Click the Server Name at the top left of the screen to open the dropdown menu.
- Select Privacy Settings.
- Ensure Activity Status is toggled ON to allow your gaming activity to be visible within that specific server.

*Note on Custom Games: If you manually added a non-supported game to Discord via the "Registered Games" menu, ensure the eye icon next to the game is not crossed out. If it is crossed out, Discord will hide that specific game from your status.*

## The game cover art is missing or incorrect.
The integration searches SteamGridDB for high-quality cover art based on the name of the game. If a publisher names a game weirdly on Xbox (like *"Call of Duty®: HQ - Cross-Gen Bundle"*), the search will fail.
- **The Fix:** Use the **Game Title Overrides** in the Advanced section to map that messy title to a clean one (e.g., `Call of Duty®: HQ - Cross-Gen Bundle = Call of Duty`). The integration will instantly find the right art!

## My gamerpic / avatar is blank or broken.
Sometimes, official APIs (especially PlayStation) fail to pass the avatar image URL to Home Assistant. 
- **The Fix (Local Override):** You can force your own profile picture! Create a folder inside your Home Assistant `www` directory called `gaming_status`. Drop a `.png` or `.jpg` image in there using the format: `[platform]_[profile_name]_avatar.jpg`. 
- *Example:* If your profile name is "Player One" and you want an Xbox avatar, name the file `xbox_player_one_avatar.jpg` and the integration will automatically use it!

## My custom PC game (or Epic Game) isn't triggering.
If you are using a PC companion app like HASS.Agent to track a running `.exe` file, it often reports the *number of running processes* rather than a simple "on/off" state. 
- **The Fix:** Ensure your Template Funnel Sensor checks for a number greater than zero, rather than just checking if the state is exactly '1'. *(See the [Advanced Setup](docs/advanced.md) documentation for the exact code to fix this).*

## I changed a game's title, but the old name is still showing.
The Master Sensor caches history and states to prevent drop-outs. If things look stuck, simply restart Home Assistant to flush the cache and force it to rebuild from the current live data.
