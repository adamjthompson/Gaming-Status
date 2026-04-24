# 🎮 Gaming Status for Home Assistant

A powerful, unified custom integration for Home Assistant that tracks and consolidates gaming presence across Steam, Xbox Live, PlayStation Network, and custom PC clients into a single, clean dashboard sensor for each person in your household.

## ✨ Features
* **Unified Master Sensor:** Combines multiple gaming platforms into one "Master" status sensor per gamer.
* **Smart Ghosting Protection:** Automatically prevents echo sessions (e.g., when the Windows Xbox app incorrectly broadcasts a Steam game).
* **Custom PC Game Support:** Track non-platform games (like Epic Games, Minecraft, or Genshin Impact) using template funnels or binary sensors.
* **Playtime Tracking:** Automatically calculates rolling daily and weekly play hours.
* **Clean Dashboards:** Automatically sanitizes messy game titles (e.g., changes "Minecraft Launcher" to "Minecraft") and pulls high-quality cover art from SteamGridDB.

## ⚠️ Prerequisites
This integration acts as a "wrapper" that intelligently processes data from your existing integrations. Before installing, ensure you have the necessary base integrations installed and working in Home Assistant:
* Official Steam Integration
* Official Xbox Integration
* Official PlayStation Network Integration (or equivalent custom component)

## 📥 Installation

### HACS (Recommended)
1. Open HACS in Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the URL to this repository and select **Integration** as the category.
4. Click **Download**.
5. **Restart Home Assistant.**

## ⚙️ Configuration (Crucial Step)

Because every home setup is unique, this integration requires a manual configuration file to map your entities to the right gamer.

1. Navigate to your Home Assistant `config` folder.
2. Go to `custom_components/gaming_status/`.
3. Locate the file named `profiles.example.py` and rename it to **`profiles.py`**.
4. Open `profiles.py` and input the entity IDs for your household members. You can remove any platforms a user does not own.

### Activating the Integration
Once your `profiles.py` file is configured and saved:
1. Go to **Settings** ➔ **Devices & Services** in Home Assistant.
2. Click **+ Add Integration**.
3. Search for **Gaming Status** and select it.
4. The integration will instantly read your `profiles.py` file and generate the master tracking sensors for your dashboard!