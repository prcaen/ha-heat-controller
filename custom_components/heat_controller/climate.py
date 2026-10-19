"""Virtual thermostat: room temperature and program setpoint for the card.

The standard thermostat card works on this entity: it shows the indoor
temperature and the current setpoint (same value as
`sensor.heating_target_setpoint`, so it does not drop to the idle setpoint
when there is no heating need).

Adjusting the temperature updates the setpoint of the active period (day,
night, away or solar) — the matching `number` entity follows. The on/off
button mirrors the « Heating control » switch.
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
from homeassistant.util import dt as dt_util

from .controller import HeatController
from .entity import HeatControllerEntity
from .logic import SETPOINT_DAY, SETPOINT_NIGHT, active_setpoint, at_time
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
        """Indoor temperature, from the sensor used for decisions."""
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
        """Current setpoint, even when the source is idle."""
        decision = self._shared.decision
        if decision is not None:
            return decision.target_setpoint
        return self._fallback_setpoint()

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
        key = self._setpoint_key()
        self._controller.update_config({**self._controller.config, key: value})
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, key: value}
        )
        self.async_write_ha_state()

    # --- Helpers -------------------------------------------------------------

    def _setpoint_key(self) -> str:
        """Setting the card adjusts: active period, or clock when off."""
        decision = self._shared.decision
        if decision is not None:
            return active_setpoint(decision)
        return self._fallback_key()

    def _fallback_key(self) -> str:
        """Day/night setpoint by clock, used when the control is off."""
        now = dt_util.now()
        day_dt = at_time(now, self._controller.settings.day_start)
        night_dt = at_time(now, self._controller.settings.night_start)
        return SETPOINT_DAY if day_dt <= now < night_dt else SETPOINT_NIGHT

    def _fallback_setpoint(self) -> float:
        return float(getattr(self._controller.settings, self._fallback_key()))
