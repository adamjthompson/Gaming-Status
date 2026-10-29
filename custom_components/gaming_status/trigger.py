"""Target-based triggers ("Started playing" / "Switched games" / "Stopped
playing") for Home Assistant's newer automation trigger picker, which lists
integration trigger platforms rather than classic device triggers
(device_trigger.py still covers the older Device trigger flow).

All three fire off the same filtered session events notifier.py puts on the
bus, so they skip launcher hand-offs and cross-platform restarts exactly
like notifications do. A target matches a session when it references the
player's device, or any entity on it (master, Steam, Xbox, ...).

Version support: the Trigger base class these build on arrived in HA
2025.12. HA preloads this module whenever the integration loads, on every
version, so on anything older the imports below fail quietly and no
triggers are registered -- the bus events and device_trigger.py's device
triggers still work there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import EVENT_SESSION_ENDED, EVENT_SESSION_STARTED, EVENT_SESSION_SWITCHED

if TYPE_CHECKING:
    # Annotation only (HA 2026.10+); never imported at runtime.
    from homeassistant.helpers.trigger import TriggerNotTriggeredReporter

try:
    from homeassistant.helpers.target import async_extract_referenced_entity_ids
    from homeassistant.helpers.trigger import (
        ENTITY_STATE_TRIGGER_SCHEMA,
        Trigger,
        TriggerActionRunner,
        TriggerConfig,
    )

    try:
        from homeassistant.helpers.target import TargetSelection
    except ImportError:  # HA 2025.12 -- renamed in 2026.2
        from homeassistant.helpers.target import (  # type: ignore[attr-defined,no-redef]
            TargetSelectorData as TargetSelection,
        )
except ImportError:  # HA older than 2025.12
    Trigger = None  # type: ignore[assignment,misc]

TRIGGERS: dict[str, Any] = {}

if Trigger is not None:

    class _SessionTrigger(Trigger):
        _event_type: str

        @classmethod
        async def async_validate_config(
            cls,
            hass: HomeAssistant,  # noqa: ARG003 -- required by the Trigger interface
            config: ConfigType,
        ) -> ConfigType:
            return cast(ConfigType, ENTITY_STATE_TRIGGER_SCHEMA(config))

        def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
            super().__init__(hass, config)
            self._target = TargetSelection(config.target or {})

        def _targeted_device_ids(self) -> set[str]:
            """Resolved on every event rather than once at attach, so a player
            device later moved into a targeted area/label is picked up."""
            selected = async_extract_referenced_entity_ids(self._hass, self._target)
            registry = er.async_get(self._hass)
            device_ids = set(selected.referenced_devices)
            for entity_id in selected.referenced | selected.indirectly_referenced:
                entry = registry.async_get(entity_id)
                if entry and entry.device_id:
                    device_ids.add(entry.device_id)
            return device_ids

        async def async_attach_runner(
            self,
            run_action: TriggerActionRunner,
            did_not_trigger: TriggerNotTriggeredReporter | None = None,  # noqa: ARG002
        ) -> CALLBACK_TYPE:
            @callback
            def _handle(event: Event[dict[str, Any]]) -> None:
                device_id = event.data.get(ATTR_DEVICE_ID)
                if not device_id or device_id not in self._targeted_device_ids():
                    return
                run_action(
                    {**event.data, ATTR_ENTITY_ID: event.data.get("entity_id")},
                    f"{event.data.get('player')} "
                    f"{self._event_type.rsplit('_', 1)[-1]} {event.data.get('game')}",
                    event.context,
                )

            return self._hass.bus.async_listen(self._event_type, _handle)

    class SessionStartedTrigger(_SessionTrigger):
        _event_type = EVENT_SESSION_STARTED

    class SessionSwitchedTrigger(_SessionTrigger):
        _event_type = EVENT_SESSION_SWITCHED

    class SessionEndedTrigger(_SessionTrigger):
        _event_type = EVENT_SESSION_ENDED

    TRIGGERS = {
        "session_started": SessionStartedTrigger,
        "session_switched": SessionSwitchedTrigger,
        "session_ended": SessionEndedTrigger,
    }


async def async_get_triggers(
    hass: HomeAssistant,  # noqa: ARG001 -- required by the trigger platform API
) -> dict[str, Any]:
    return TRIGGERS
