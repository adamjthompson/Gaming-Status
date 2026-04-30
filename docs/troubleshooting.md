# 🛠️ Troubleshooting & FAQ
Below are some of the possible issues you may encounter and their fixes.

## The integration loads, but no sensors are created
One possibility is a simple JSON formatting error in your `gaming_profiles.json` file. JSON is extremely strict and doesn't allow 'trailing commas' after the last item in a list. See example below:

**Bad JSON**
```json
"exclude_games": [
  "Genshin Impact",
  "Minecraft",  <-- THIS TRAILING COMMA BREAKS EVERYTHING
]
```

**Good JSON**
```json
"exclude_games": [
  "Genshin Impact",
  "Minecraft"
]
```

## My sensor always says "Offline" even when I'm playing! (1)
99% of the time, this is a typo in your `gaming_profiles.json` file. 
1. Go to **Developer Tools ➔ States** and find your base platform sensor (e.g., `sensor.steam_gamertag...`).
2. Make sure the entity ID in your `gaming_profiles.json` matches that exact string letter-for-letter. 
3. Ensure the base official integrations (Steam, Xbox, PlayStation) are actually signed in and working. If the official Xbox sensor is broken, this integration has no data to read!
*(Note: Remember to restart Home Assistant after making any changes to `gaming_profiles.json`!)*

## My sensor always says "Offline" even when I'm playing! (2)
Another possibility is the privacy settings of your account. Your online status and game activity must be viewable by the public in order to be properly reported by the Gaming Status integration. Instructions for changing the necessary settings is included below:

### PlayStation Network (PSN)
If your PSN sensor reports as "Offline" while you are gaming, your PSN privacy settings are likely restricting API access.

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

## The game cover art is missing or incorrect.
The integration searches SteamGridDB for high-quality cover art based on the name of the game. If a publisher names a game weirdly on Xbox (like *"Call of Duty®: HQ - Cross-Gen Bundle"*), the search will fail.
- **The Fix:** Open your `gaming_profiles.json` and use the `GAME_TITLE_OVERRIDES` dictionary to map that messy title to a clean one (e.g., `"Call of Duty®: HQ - Cross-Gen Bundle": "Call of Duty"`). The integration will instantly find the right art!

## My gamerpic / avatar is blank or broken.
Sometimes, official APIs (especially PlayStation) fail to pass the avatar image URL to Home Assistant. 
- **The Fix (Local Override):** You can force your own profile picture! Create a folder inside your Home Assistant `www` directory called `gaming_status`. Drop a `.png` or `.jpg` image in there using the format: `[platform]_[profile_name]_avatar.jpg`. 
- *Example:* If your profile name is "Player One" and you want an Xbox avatar, name the file `xbox_player_one_avatar.jpg` and the integration will automatically use it!

## My custom PC game (or Epic Game) isn't triggering.
If you are using a PC companion app like HASS.Agent to track a running `.exe` file, it often reports the *number of running processes* rather than a simple "on/off" state. 
- **The Fix:** Ensure your Template Funnel Sensor checks for a number greater than zero, rather than just checking if the state is exactly '1'. *(See the [Advanced Setup](docs/advanced.md) documentation for the exact code to fix this).*

## I changed a game's title, but the old name is still showing.
The Master Sensor caches history and states to prevent drop-outs. If things look stuck, simply restart Home Assistant to flush the cache and force it to rebuild from the current live data.
