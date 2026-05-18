"""Binary sensor platform for Gaming Status."""
import logging
import json

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import OPT_PLAYERS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the binary sensor platform."""
    
    # Load players directly from the new native HA options database
    raw_players = config_entry.options.get(OPT_PLAYERS, "{}")
    try:
        players = json.loads(raw_players) if raw_players else {}
    except (ValueError, TypeError):
        players = {}
        
    master_sensor_ids = []
    
    # Grab all the profiles to build our dynamic listener list
    for name in players.keys():
        safe_owner = name.lower().replace(" ", "_")
        master_sensor_ids.append(f"sensor.{safe_owner}_gaming_status")
        
    if master_sensor_ids:
        async_add_entities([GlobalGamingSensor(hass, master_sensor_ids)])


class GlobalGamingSensor(BinarySensorEntity):
    _attr_should_poll = False
    
    def __init__(self, hass, master_sensor_ids):
        self.hass = hass
        self._master_sensor_ids = master_sensor_ids
        self._attr_name = "Anyone Gaming"
        self._attr_unique_id = "global_anyone_gaming_v1"
        # Home Assistant automatically prepends binary_sensor. to the ID because of the filename!
        self._attr_is_on = False
        self._attr_icon = "mdi:controller-off"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        
        # Create a hyper-efficient, targeted listener for ONLY the dynamic list of master sensors
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._master_sensor_ids, self._async_gamers_changed
            )
        )
        self._update_state()

    @callback
    def _async_gamers_changed(self, event):
        self._update_state()

    def _update_state(self):
        is_anyone_gaming = False
        offline_states = ['Offline', 'offline', 'unavailable', 'unknown', 'None', 'none']
        
        for sensor_id in self._master_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state and state.state not in offline_states:
                is_anyone_gaming = True
                break
                
        # True Binary Sensors use _attr_is_on with True/False
        self._attr_is_on = is_anyone_gaming
        self._attr_icon = "mdi:controller" if is_anyone_gaming else "mdi:controller-off"
            
        self.async_write_ha_state()