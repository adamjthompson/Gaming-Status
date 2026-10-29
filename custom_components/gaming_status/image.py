"""Image entity platform -- one image.gaming_status_<player>_game_art per
player showing the cover art of the game they're playing (or last played),
for picture cards and notifications without template tricks.

Most cover art is cached under /local/gaming_status_cache/ (see utils.py),
a path HA's own URL fetcher can't load without an absolute URL, so
async_image reads those straight from the www folder instead.
"""

import json
import mimetypes
from pathlib import Path

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import OPT_PLAYERS
from .device import (
    ensure_player_device,
    resolve_master_entity_id,
    safe_owner_slug,
)
from .utils import is_public_url

_ART_ATTRIBUTE = "game_cover_art"
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def async_setup_entry(hass, config_entry, async_add_entities):
    raw_players = config_entry.options.get(OPT_PLAYERS, "{}")
    try:
        players = json.loads(raw_players) if raw_players else {}
    except (ValueError, TypeError):
        players = {}
    async_add_entities(
        GameArtImage(hass, name, ensure_player_device(hass, config_entry, name))
        for name in players
    )


class GameArtImage(ImageEntity):
    _attr_should_poll = False

    def __init__(self, hass, player_name, device_info):
        super().__init__(hass)
        safe_owner = safe_owner_slug(player_name)
        self._master_entity_id = resolve_master_entity_id(hass, player_name)
        self.entity_id = f"image.gaming_status_{safe_owner}_game_art"
        self._attr_unique_id = f"gaming_status_{safe_owner}_game_art"
        self._attr_name = f"{player_name} Game Art"
        self._attr_device_info = device_info
        self._art_url = None
        self._image = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._master_entity_id], self._async_master_changed
            )
        )
        self._update_art()

    @callback
    def _async_master_changed(self, _event):
        if self._update_art():
            self.async_write_ha_state()

    def _update_art(self) -> bool:
        state = self.hass.states.get(self._master_entity_id)
        art_url = state.attributes.get(_ART_ATTRIBUTE) if state else None
        if not art_url or art_url == self._art_url:
            # Keeps the last game's art while offline rather than blanking.
            return False
        self._art_url = art_url
        self._image = None
        self._attr_image_last_updated = dt_util.utcnow()
        return True

    async def async_image(self) -> bytes | None:
        if self._image is None and self._art_url:
            self._image = await self._async_load(self._art_url)
        return self._image

    async def _async_load(self, url: str) -> bytes | None:
        local_marker = "/local/"
        if url.startswith(local_marker) or (
            local_marker in url
            and url.split(local_marker, maxsplit=1)[0].count("/") == 2
        ):
            return await self.hass.async_add_executor_job(
                self._read_local, url.split(local_marker, 1)[1].split("?", 1)[0]
            )
        if not url.startswith(("http://", "https://")) or not await is_public_url(
            self.hass, url
        ):
            return None
        session = async_get_clientsession(self.hass)
        try:
            # No redirects: is_public_url only vetted this URL's own host.
            async with session.get(url, timeout=15, allow_redirects=False) as resp:
                content_type = resp.headers.get("content-type", "")
                if resp.status != 200 or not content_type.startswith("image/"):
                    return None
                data = await resp.content.read(_MAX_IMAGE_BYTES + 1)
        except (OSError, TimeoutError):
            return None
        if len(data) > _MAX_IMAGE_BYTES:
            return None
        self._attr_content_type = content_type.split(";")[0]
        return data

    def _read_local(self, relative: str) -> bytes | None:
        www = Path(self.hass.config.path("www")).resolve()
        path = (www / relative).resolve()
        # Never serve anything outside www, whatever the attribute says.
        if not path.is_relative_to(www) or not path.is_file():
            return None
        self._attr_content_type = mimetypes.guess_type(path.name)[0] or "image/png"
        return path.read_bytes()
