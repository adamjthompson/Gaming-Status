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
            api_key = user_input.get(CONF_STEAMGRIDDB_API_KEY, "").strip()
            if not api_key:
                errors[CONF_STEAMGRIDDB_API_KEY] = "api_key_required"
            else:
                return self.async_create_entry(
                    title="Gaming Status",
                    data={CONF_STEAMGRIDDB_API_KEY: api_key}
                )
                
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_STEAMGRIDDB_API_KEY): str,
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
        # FIX: Added an underscore to bypass the new HA read-only property restriction
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        try:
            if user_input is not None:
                # Update the config entry with the new API key
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data={**self._config_entry.data, **user_input}
                )
                # Must return empty dict to close the modal successfully
                return self.async_create_entry(title="", data={})

            # Safely get the API key, defaulting to empty string
            current_api_key = self._config_entry.data.get(CONF_STEAMGRIDDB_API_KEY) or ""

            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Optional(CONF_STEAMGRIDDB_API_KEY, default=current_api_key): str,
                })
            )
        except Exception as e:
            _LOGGER.error(f"[Gaming Status] Options Flow Crash: {e}", exc_info=True)
            raise