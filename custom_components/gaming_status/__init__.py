import os
import json
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from .const import DOMAIN
from .notifier import GamingNotifier

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor"]

class GamingProfilesAPI(HomeAssistantView):
    """Secure API endpoint to read and write gaming_profiles.json natively."""
    url = "/api/gaming_status/profiles"
    name = "api:gaming_status:profiles"
    requires_auth = True

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.file_path = hass.config.path("gaming_profiles.json")

    async def get(self, request):
        def read_file():
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    return {"_api_error": str(e)}
            return {}
        
        data = await self.hass.async_add_executor_job(read_file)
        if "_api_error" in data: return self.json(data, status_code=500)
            
        response = self.json(data)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    async def post(self, request):
        try:
            body = await request.read()
            data = json.loads(body)
        except Exception as e:
            return self.json({"error": "Invalid JSON"}, status_code=400)

        def write_file():
            # UNRAID FIX: Direct write with fsync avoids cross-device link errors on shfs
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
                
        try:
            await self.hass.async_add_executor_job(write_file)
            return self.json({"success": True})
        except Exception as e:
            _LOGGER.error(f"Failed to write config: {e}")
            return self.json({"error": "Write failed"}, status_code=500)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gaming Status from a UI config entry."""
    hass.data.setdefault(DOMAIN, {})

    notifier = GamingNotifier(hass)
    await notifier.async_start()
    hass.data[DOMAIN]["notifier"] = notifier

    hass.http.register_view(GamingProfilesAPI(hass))

    configurator_path = os.path.join(os.path.dirname(__file__), "gaming_profiles.html")
    controls_path = os.path.join(os.path.dirname(__file__), "gaming_controls.html")
    brand_path = os.path.join(os.path.dirname(__file__), "brand")
    
    await hass.http.async_register_static_paths([
        StaticPathConfig("/gaming_status/configurator", configurator_path, cache_headers=False),
        StaticPathConfig("/gaming_status/controls", controls_path, cache_headers=False),
        StaticPathConfig("/gaming_status/brand", brand_path, cache_headers=True),
    ])

    show_sidebar = entry.options.get("show_sidebar", False)

    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Gaming Status" if show_sidebar else None,
        sidebar_icon="mdi:controller" if show_sidebar else None,
        frontend_url_path="gaming-status-config",
        config={"url": "/gaming_status/configurator?v=191"}, 
        require_admin=True,
    )
    
    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title=None,
        sidebar_icon=None,
        frontend_url_path="gaming-status-controls",
        config={"url": "/gaming_status/controls?v=191"}, 
        require_admin=True,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if "notifier" in hass.data.get(DOMAIN, {}):
        await hass.data[DOMAIN]["notifier"].async_stop()
        
    try:
        async_remove_panel(hass, "gaming-status-config")
        async_remove_panel(hass, "gaming-status-controls")
    except ValueError:
        pass

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)