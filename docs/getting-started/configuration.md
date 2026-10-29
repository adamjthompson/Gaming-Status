# Configuration

Go to Settings / Devices & Services and press the Add Integration button, or click the shortcut button below (requires My Homeassistant configured):

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=gaming_status)

Gaming Status is configured entirely through the Home Assistant UI. **There is no YAML or JSON configuration required.** *(Note: If you are upgrading from an older version, your `gaming_profiles.json` file will be automatically migrated to the new database and can be safely deleted).*

## Initial Setup

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **+ Add Integration** and search for **Gaming Status**.
3. On the first screen, choose which **Platforms to Track**, whether to turn on **Notifications** and **Parental Controls** (both can be switched on or off later from their own menus), enter an optional **SteamGridDB API Key** (highly recommended for artwork; get a free key [here](https://www.steamgriddb.com/profile/api) or add it later under Advanced), and toggle **Local Image Caching** (pre-checked only when Home Assistant has an external HTTPS URL).
4. If **Discord** was selected, a dedicated step asks for your Discord Bot Token and Server ID (see [Setting up Discord Tracking](../setup/pc-tracking.md)).
5. Finally, name your first player and map their platform sensors. Click Submit.
