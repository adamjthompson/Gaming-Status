# 🎮 Gaming Status for Home Assistant

A powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, and custom PC clients into a single, clean dashboard sensor for each person in your household.

## ✨ Features
* **Unified Master Sensor:** Combines Xbox, PlayStation, Steam, and Custom PC clients into one clean "Master Status" sensor per person.
* **Custom PC Game Support:** Track non-platform games (like Epic Games, Minecraft, or Genshin Impact) using template funnels or binary sensors.
* **Smart Ghosting Protection:** Automatically prevents echo sessions (e.g., when the Windows Xbox app incorrectly broadcasts a Steam game).
* **Drop-Out Protection:** Built-in grace periods prevent a gamer from appearing "Offline" if their game crashes, they switch titles, or their internet briefly blips, keeping play sessions perfectly intact.
* **Playtime Analytics:** Automatically calculates session time, daily hours, and a rolling 7-day total for easy dashboard charting.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.
* **Advanced Exclusion Filtering:** Prevent media apps (Netflix, YouTube, Spotify) or background processes from triggering gaming statuses using the global exclusions list.
* **Zero-Bloat Cover Art:** Automatically fetches gorgeous, clean game covers from SteamGridDB and passes them to your dashboard via URL, ensuring your local HA storage never gets bloated with downloaded images.
* **"Last Seen" Memory:** When gamers go offline, the sensor retains their last played game and calculates exactly how long ago they were active (e.g., *Last seen 3h ago: Genshin Impact*).
* **Custom Avatars:** Automatically pulls live gamer pictures from platform APIs, with the option to easily override missing or incorrect images with your own local images.

## ⚠️ Prerequisites
This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have any necessary base integrations installed and working in Home Assistant:
* Official PlayStation Network Integration
* Official Steam Integration
* Official Xbox Integration

## 📥 Installation

### HACS (Recommended)
1. Open HACS in Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the URL to this repository and select **Integration** as the category.
4. Click **Download**.
5. **Restart Home Assistant.**

### Manual Installation
If you prefer not to use HACS, you can install the integration manually:

1. Download the latest release from the [GitHub Releases page](https://github.com/YOUR_GITHUB_USERNAME/ha-gaming-status/releases). *(Note: Download the Source Code ZIP, not the main repository branch).*
2. Extract the downloaded ZIP file.
3. Locate the `custom_components/gaming_status/` folder inside the extracted files.
4. Copy that entire `gaming_status` folder into your Home Assistant `config/custom_components/` directory. *(If the `custom_components` folder does not exist, create it).*
5. **Restart Home Assistant.**
6. Follow the Configuration steps below to set up your `profiles.py` file.

## ⚙️ Configuration (Crucial Step)
Because every home setup is unique, this integration requires a manual configuration file to map your entities to the right gamer.

### 🔑 Obtaining a SteamGridDB API Key
To display beautiful, high-resolution game covers on your dashboard, this integration requires a free API key from SteamGridDB. If you skip this step, the integration will simply fall back to displaying the gamer's profile picture.

1. Go to [SteamGridDB.com](https://www.steamgriddb.com/).
2. Click **Login** in the top right corner and authenticate using your Steam account.
3. Click your profile picture in the top right and select **Preferences**.
4. Navigate to the **API** tab on the left menu.
5. Click **Generate API Key**.
6. Copy the string of letters and numbers generated. You will paste this into your `profiles.py` file!

### ⚙️ Editing the Settings

1. Navigate to your Home Assistant `config` folder.
2. Go to `custom_components/gaming_status/`.
3. Locate the file named `example.profiles.py` and rename it to **`profiles.py`**.
4. Open `profiles.py` and input the entity IDs for your household members. You can exclude any platforms a user does not use.
5. Paste your **SteamGridDB API Key** where indicated.

### Activating the Integration
Once your `profiles.py` file is configured and saved:
1. Go to **Settings** ➔ **Devices & Services** in Home Assistant.
2. Click **+ Add Integration**.
3. Search for **Gaming Status** and select it.
4. The integration will instantly read your `profiles.py` file and generate the master tracking sensors for your dashboard!
5. Look for the new master sensors named `sensor.XXXXXXXX.gaming_status`. Additionally, individual platform sensors will be created ending in `_playstation`, `_steam`, `_xbox`, and `_custom`, where applicable.

## 🛠️ Troubleshooting & FAQ
**My Master Sensor always says "Offline" even when I'm playing!**
99% of the time, this is a typo in your `profiles.py` file. 
1. Go to **Developer Tools ➔ States** and find your base platform sensor (e.g., `sensor.steam_765611...`).
2. Make sure the entity ID in your `profiles.py` matches that exact string letter-for-letter. 
3. Ensure the base official integrations (Steam, Xbox, PlayStation) are actually signed in and working. If the official Xbox sensor is broken, this integration has no data to read!
*(Note: Remember to restart Home Assistant after making any changes to `profiles.py`!)*

**The game cover art is missing or incorrect.**
The integration searches SteamGridDB for high-quality cover art based on the name of the game. If a publisher names a game weirdly on Xbox (like *"Call of Duty®: HQ - Cross-Gen Bundle"*), the search will fail.
* **The Fix:** Open your `profiles.py` and use the `GAME_TITLE_OVERRIDES` dictionary to map that messy title to a clean one (e.g., `"Call of Duty®: HQ - Cross-Gen Bundle": "Call of Duty"`). The integration will instantly find the right art!

**My Gamerpic / Avatar is blank or broken.**
Sometimes, official APIs (especially PlayStation) fail to pass the avatar image URL to Home Assistant. 
* **The Fix (Local Override):** You can force your own profile picture! Create a folder inside your Home Assistant `www` directory called `gaming_status`. Drop a `.png` or `.jpg` image in there using the format: `[platform]_[profile_name]_avatar.jpg`. 
* *Example:* If your profile name is "Player One" and you want an Xbox avatar, name the file `xbox_player_one_avatar.jpg` and the integration will automatically use it!

**My Custom PC Game (or Epic Game) isn't triggering.**
If you are using a PC companion app like HASS.Agent to track a running `.exe` file, it often reports the *number of running processes* rather than a simple "on/off" state. 
* **The Fix:** Ensure your Template Funnel Sensor checks for a number greater than zero, rather than just checking if the state is exactly '1'. *(See the [Templates Documentation](docs/templates.md) for the exact code to fix this).*

**I changed a game's title, but the old name is still showing.**
The Master Sensor caches history and states to prevent drop-outs. If things look stuck, simply restart Home Assistant to flush the cache and force it to rebuild from the current live data.