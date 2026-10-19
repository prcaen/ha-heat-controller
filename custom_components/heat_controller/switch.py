"""Heat controller switches: active and away mode."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .controller import HeatController
from .entity import HeatControllerEntity
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = hass.data["heat_controller"][entry.entry_id]
    shared: HeatState = data["shared"]
    controller: HeatController = data["controller"]
    async_add_entities(
        [
            HeatActiveSwitch(entry, shared, controller),
            HeatAwayModeSwitch(entry, shared, controller),
        ]
    )


class _HeatSwitch(HeatControllerEntity, SwitchEntity, RestoreEntity):
    """Switch with persistence, like the original input_boolean."""

    def __init__(self, entry: ConfigEntry, shared: HeatState, controller: HeatController) -> None:
        super().__init__(entry, shared, self._key)
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._restore(last_state.state == STATE_ON)

    def _restore(self, value: bool) -> None:
        raise NotImplementedError


class HeatActiveSwitch(_HeatSwitch):
    """Heating control on/off (input_boolean.chauffage_actif)."""

    _key = "active"
    _attr_icon = "mdi:hvac"
    _attr_translation_key = "active"

    @property
    def is_on(self) -> bool:
        return self._shared.active

    async def async_turn_on(self, **kwargs) -> None:
        self._shared.set_active(True)
        self._controller.request_update()

    async def async_turn_off(self, **kwargs) -> None:
        self._shared.set_active(False)
        # Let the controller drop the stale decision (computed entities
        # become unavailable while the control is off).
        self._controller.request_update()

    def _restore(self, value: bool) -> None:
        self._shared.set_active(value)


class HeatAwayModeSwitch(_HeatSwitch):
    """Away mode with scheduled return (input_boolean.chauffage_mode_absent)."""

    _key = "away_mode"
    _attr_icon = "mdi:bag-checked"
    _attr_translation_key = "away_mode"

    @property
    def is_on(self) -> bool:
        return self._shared.away_mode

    async def async_turn_on(self, **kwargs) -> None:
        self._shared.set_away_mode(True)
        self._controller.request_update()

    async def async_turn_off(self, **kwargs) -> None:
        # Returning home: drop any pending absence end as well.
        self._shared.set_away_mode(False)
        self._shared.set_away_until(None)
        self._controller.request_update()

    def _restore(self, value: bool) -> None:
        self._shared.set_away_mode(value)
