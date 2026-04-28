"""Config flow for Gaming Status."""
import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN, CONF_STEAMGRIDDB_API_KEY

class GamingStatusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gaming Status."""
    
    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        # Only allow a single instance of this integration to be set up
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
        )