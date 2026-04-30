import os
import json
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]

class GamingProfilesAPI(HomeAssistantView):
    """Secure API endpoint to read and write gaming_profiles.json natively."""
    url = "/api/gaming_status/profiles"
    name = "api:gaming_status:profiles"
    requires_auth = True  # SECURITY FIX: Requires a valid Bearer Token

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.file_path = hass.config.path("gaming_profiles.json")

    async def get(self, request):
        """Read the profiles file with strict error handling."""
        def read_file():
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except json.JSONDecodeError as e:
                    _LOGGER.error(f"[Gaming Status] Syntax Error in gaming_profiles.json: {e}")
                    return {"_api_error": f"JSON Syntax Error: {e}"}
                except Exception as e:
                    _LOGGER.error(f"[Gaming Status] API Read Error: {e}")
                    return {"_api_error": str(e)}
            return {}
        
        data = await self.hass.async_add_executor_job(read_file)
        
        if "_api_error" in data:
            return self.json(data, status_code=500)
            
        return self.json(data)

    async def post(self, request):
        """Write to the profiles file securely."""
        # 1. Size Limit Enforcement & JSON Parsing
        try:
            body = await request.read()
            if len(body) > 1_000_000:  # 1MB hard limit on raw bytes
                _LOGGER.warning("[Gaming Status] API Write Error - Payload exceeded 1MB limit")
                return self.json({"error": "Payload too large"}, status_code=413)
            
            data = json.loads(body)
        except Exception as e:
            _LOGGER.error(f"[Gaming Status] API Write Error - Invalid JSON payload: {e}")
            return self.json({"error": "Invalid JSON"}, status_code=400)

        # 2. Basic Schema Validation (Ensure the payload is actually a dictionary)
        if not isinstance(data, dict):
            _LOGGER.error("[Gaming Status] API Write Error - Payload is not a dictionary object")
            return self.json({"error": "Invalid payload format"}, status_code=400)

        def write_file():
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                
        await self.hass.async_add_executor_job(write_file)
        return self.json({"success": True})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gaming Status from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    hass.http.register_view(GamingProfilesAPI(hass))

    configurator_path = os.path.join(os.path.dirname(__file__), "gaming_profiles.html")
    brand_path = os.path.join(os.path.dirname(__file__), "brand")
    
    await hass.http.async_register_static_paths([
        StaticPathConfig("/gaming_status/configurator", configurator_path, cache_headers=False),
        StaticPathConfig("/gaming_status/brand", brand_path, cache_headers=True),
    ])

    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Gaming Status",
        sidebar_icon="mdi:controller",
        frontend_url_path="gaming-status-config",
        config={"url": "/gaming_status/configurator?v=173"}, 
        require_admin=True,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel(hass, "gaming-status-config")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
