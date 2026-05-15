"""Config flow for Gaming Status."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, CONF_STEAMGRIDDB_API_KEY

_LOGGER = logging.getLogger(__name__)

class GamingStatusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gaming Status."""
    
    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
            
        errors = {}
        
        if user_input is not None:
            # Grab the key and strip whitespace. If left blank, it saves as empty string ("")
            api_key = user_input.get(CONF_STEAMGRIDDB_API_KEY, "").strip()
            
            return self.async_create_entry(
                title="Gaming Status",
                data={CONF_STEAMGRIDDB_API_KEY: api_key},
                options={"show_sidebar": False} # Sidebar is hidden by default
            )
                
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(CONF_STEAMGRIDDB_API_KEY, default=""): str,
            }),
            errors=errors,
            description_placeholders={
                "api_url": "https://www.steamgriddb.com/profile/api"
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return GamingStatusOptionsFlowHandler(config_entry)


class GamingStatusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options (Configure button)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        # Restored your underscore fix to prevent the HA read-only crash!
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        try:
            if user_input is not None:
                # 1. Pop the API key out and update the core DATA dictionary
                api_key = user_input.pop(CONF_STEAMGRIDDB_API_KEY, "")
                new_data = dict(self._config_entry.data)
                new_data[CONF_STEAMGRIDDB_API_KEY] = api_key
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                
                # 2. Return the remaining user_input (the sidebar toggle) to be saved natively as OPTIONS
                return self.async_create_entry(title="", data=user_input)

            # Safely get current values using the underscored property
            current_api_key = self._config_entry.data.get(CONF_STEAMGRIDDB_API_KEY) or ""
            # Fallback is now False
            current_show_sidebar = self._config_entry.options.get("show_sidebar", False)

            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Optional(CONF_STEAMGRIDDB_API_KEY, default=current_api_key): str,
                    vol.Optional("show_sidebar", default=current_show_sidebar): bool,
                })
            )
        except Exception as e:
            _LOGGER.error(f"[Gaming Status] Options Flow Crash: {e}", exc_info=True)
            raise