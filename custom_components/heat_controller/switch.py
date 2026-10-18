"""Interrupteurs du contrôleur de chauffage : actif et mode absent."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .controller import HeatController
from .entity import HeatControllerEntity
from .state import HeatState

_LOGGER = logging.getLogger(__name__)


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
            HeatAbsentSwitch(entry, shared, controller),
        ]
    )


class _HeatSwitch(HeatControllerEntity, SwitchEntity, RestoreEntity):
    """Interrupteur avec persistance, comme les input_boolean d'origine."""

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
    """Marche/arrêt du pilotage (input_boolean.chauffage_actif)."""

    _key = "actif"
    _attr_icon = "mdi:hvac"
    _attr_translation_key = "actif"

    @property
    def is_on(self) -> bool:
        return self._shared.actif

    async def async_turn_on(self, **kwargs) -> None:
        self._shared.set_actif(True)
        self._controller.request_update()

    async def async_turn_off(self, **kwargs) -> None:
        self._shared.set_actif(False)

    def _restore(self, value: bool) -> None:
        self._shared.set_actif(value)


class HeatAbsentSwitch(_HeatSwitch):
    """Mode absence avec retour programmé (input_boolean.chauffage_mode_absent)."""

    _key = "mode_absent"
    _attr_icon = "mdi:bag-checked"
    _attr_translation_key = "mode_absent"

    @property
    def is_on(self) -> bool:
        return self._shared.mode_absent

    async def async_turn_on(self, **kwargs) -> None:
        self._shared.set_mode_absent(True)
        self._controller.request_update()

    async def async_turn_off(self, **kwargs) -> None:
        self._shared.set_mode_absent(False)
        self._controller.request_update()

    def _restore(self, value: bool) -> None:
        self._shared.set_mode_absent(value)
