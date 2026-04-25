"""Config flow for Gaming Status."""
import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN

class GamingStatusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gaming Status."""
    
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        # Only allow one instance of the integration to be set up
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # When they click submit, create the empty config entry 
            # which triggers your async_setup_entry in sensor.py!
            return self.async_create_entry(title="Gaming Status", data={})

        return self.async_show_form(step_id="user")