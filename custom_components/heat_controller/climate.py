"""Virtual thermostat: room temperature and current target for the card.

The standard thermostat card works on this entity: it shows the
temperature of the thermostat room (the one holding the boiler
thermostat) and the current target (day target of that room, night or
away setpoint depending on the mode).

Adjusting the temperature: during the day (and while pre-heating) the
+/- buttons change the thermostat room's day target (persisted in its
room sub-entry); at night and in away mode they change the global
night/away setpoints. The on/off button mirrors the « Heating control »
switch.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_AWAY_SETPOINT, CONF_NIGHT_SETPOINT, CONF_ROOM_TARGET
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
    async_add_entities([HeatThermostat(entry, shared, controller)])


class HeatThermostat(HeatControllerEntity, ClimateEntity):
    """Thermostat-style view of the heating program."""

    _attr_translation_key = "thermostat"
    _attr_icon = "mdi:thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(
        self, entry: ConfigEntry, shared: HeatState, controller: HeatController
    ) -> None:
        super().__init__(entry, shared, "thermostat")
        self._controller = controller

    # --- State ---------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Temperature of the thermostat room."""
        if self.hass is None:
            return None
        state = self.hass.states.get(self._controller.indoor_sensor)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @property
    def target_temperature(self) -> float:
        """Current target, even when the control is off."""
        decision = self._shared.decision
        if decision is not None:
            return decision.target_setpoint
        return self._controller.thermostat_room_target()

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._shared.active else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        decision = self._shared.decision
        if decision is None:
            return HVACAction.OFF
        if decision.heating_needed:
            return HVACAction.HEATING
        return HVACAction.IDLE

    # --- Commands ------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            self._shared.set_active(False)
        elif hvac_mode == HVACMode.HEAT:
            self._shared.set_active(True)
        else:  # only heat and off are supported
            return
        self._controller.request_update()
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        value = min(max(round(float(temperature), 1), self.min_temp), self.max_temp)
        # Match the 0.5 step of the room targets.
        value = round(value * 2) / 2

        decision = self._shared.decision
        if decision is not None and decision.away_mode_active:
            key = CONF_AWAY_SETPOINT
        elif decision is not None and not decision.in_day_period:
            key = CONF_NIGHT_SETPOINT
        else:
            # Day (or pre-heating): the thermostat room's day target.
            self._set_thermostat_room_target(value)
            self.async_write_ha_state()
            return

        self._controller.update_config({**self._controller.config, key: value})
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, key: value}
        )
        self.async_write_ha_state()

    def _set_thermostat_room_target(self, value: float) -> None:
        """Write the new day target into the thermostat room's sub-entry.

        The entry reloads through the update listener: the new target
        applies on the next pass.
        """
        room_id = self._controller.thermostat_room_id
        subentry = self._entry.subentries.get(room_id)
        if subentry is None:
            return
        self.hass.config_entries.async_update_subentry(
            self._entry,
            subentry,
            data={**subentry.data, CONF_ROOM_TARGET: value},
        )
