"""Heat Controller: reads state, decides, applies.

Equivalent Python of YAML automations « Heating - PAC/Boiler changeover »
and « Heating - Day/Night/Away with anticipation ».

Voluntary differences from YAML, without changing behavior:
- Heartbeat every minutes (instead of 5/15 min) + sensor listening:
  decisions are idempotent and protected by hysteresis;
- Solar production comes from a real power sensor (Solenso central)
  instead of the Forecast.Solar forecast.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable, Mapping, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CHANGEOVER_FAN_DELAY_SECONDS,
    CONF_ACCESSORY_FANS,
    CONF_AWAY_SETPOINT,
    CONF_BOILER_CLIMATE,
    CONF_BOILER_RATE,
    CONF_DAY_SETPOINT,
    CONF_DAY_START,
    CONF_FROST_SETPOINT,
    CONF_HEAT_PUMP_CLIMATES,
    CONF_HEAT_PUMP_FANS,
    CONF_HEAT_PUMP_RATE,
    CONF_HYSTERESIS,
    CONF_IDLE_SETPOINT,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_NIGHT_SETPOINT,
    CONF_NIGHT_START,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_OUTDOOR_THRESHOLD,
    CONF_PRODUCTION_SENSOR,
    CONF_PRODUCTION_THRESHOLD,
    CONF_RETURN_TIME,
    CONF_SAFETY_MARGIN,
    CONF_SOLAR_END,
    CONF_SOLAR_SETPOINT,
    CONF_SOLAR_START,
    SETTING_DEFAULTS,
    DEFAULT_INDOOR_TEMP,
    DEFAULT_PRODUCTION,
    FAN_WAIT_TIMEOUT_SECONDS,
    HVAC_MODE_DELAY_SECONDS,
    POLL_INTERVAL_SECONDS,
    SERVICE_TIMEOUT_SECONDS,
    STARTUP_DELAY_SECONDS,
)
from .logic import Decision, Inputs, Settings, compute, parse_time, target_temperature
from .state import HeatState

_LOGGER = logging.getLogger(__name__)

HVAC_OFF = "off"
HVAC_HEAT = "heat"
HVAC_AUTO = "auto"


def _as_float(state: Optional[State], default: Optional[float] = None) -> Optional[float]:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return default
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return default


def settings_from_config(config: Mapping[str, Any]) -> Settings:
    """Build the settings from the entry configuration."""

    def number(key: str) -> float:
        try:
            return float(config.get(key, SETTING_DEFAULTS[key]))
        except (TypeError, ValueError):
            return float(SETTING_DEFAULTS[key])

    def moment(key: str) -> timedelta:
        return parse_time(config.get(key, SETTING_DEFAULTS[key])) or parse_time(
            SETTING_DEFAULTS[key]
        )

    return Settings(
        outdoor_threshold=number(CONF_OUTDOOR_THRESHOLD),
        day_setpoint=number(CONF_DAY_SETPOINT),
        night_setpoint=number(CONF_NIGHT_SETPOINT),
        away_setpoint=number(CONF_AWAY_SETPOINT),
        solar_setpoint=number(CONF_SOLAR_SETPOINT),
        idle_setpoint=number(CONF_IDLE_SETPOINT),
        frost_setpoint=number(CONF_FROST_SETPOINT),
        hysteresis=number(CONF_HYSTERESIS),
        production_threshold=number(CONF_PRODUCTION_THRESHOLD),
        heat_pump_rate=number(CONF_HEAT_PUMP_RATE),
        boiler_rate=number(CONF_BOILER_RATE),
        safety_margin=int(number(CONF_SAFETY_MARGIN)),
        day_start=moment(CONF_DAY_START),
        night_start=moment(CONF_NIGHT_START),
        return_time=moment(CONF_RETURN_TIME),
        solar_start=moment(CONF_SOLAR_START),
        solar_end=moment(CONF_SOLAR_END),
    )


class HeatController:
    """Drive the climate units, the fans and the heat source."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        config: Mapping[str, Any],
        shared: HeatState,
    ) -> None:
        self.hass = hass
        self.entry_id = entry.entry_id
        self.shared = shared
        self.config: dict[str, Any] = dict(config)
        self.settings = settings_from_config(self.config)

        self.outdoor_sensor: str = config.get(CONF_OUTDOOR_TEMP_SENSOR, "")
        self.indoor_sensor: str = config.get(CONF_INDOOR_TEMP_SENSOR, "")
        self.production_sensor: str = config.get(CONF_PRODUCTION_SENSOR, "")

        self.boiler_climate: str = config.get(CONF_BOILER_CLIMATE, "")
        self.heat_pump_climates: list[str] = list(config.get(CONF_HEAT_PUMP_CLIMATES, []))
        heat_pump_fans: list[str] = list(config.get(CONF_HEAT_PUMP_FANS, []))
        # Pairing by order: fan[i] powers climate[i] (None if missing).
        self.heat_pump_fans: list[Optional[str]] = [
            heat_pump_fans[i] if i < len(heat_pump_fans) else None
            for i in range(len(self.heat_pump_climates))
        ]
        self.accessory_fans: list[str] = list(config.get(CONF_ACCESSORY_FANS, []))

        self._lock = asyncio.Lock()
        self._cancel: list[Callable] = []
        self._first_run = True

    # --- Lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        """Start listening and heartbeat, then first pass."""
        watched = [
            entity
            for entity in (self.outdoor_sensor, self.indoor_sensor, self.production_sensor)
            if entity
        ]
        self._cancel.append(
            async_track_state_change_event(self.hass, watched, self._on_state_change)
        )
        self._cancel.append(
            async_track_time_interval(
                self.hass,
                self._on_heartbeat,
                timedelta(seconds=POLL_INTERVAL_SECONDS),
            )
        )
        self._cancel.append(
            async_call_later(
                self.hass,
                STARTUP_DELAY_SECONDS,
                self._on_first_run,
            )
        )

    async def async_stop(self) -> None:
        for cancel in self._cancel:
            cancel()
        self._cancel.clear()

    @callback
    def update_config(self, config: Mapping[str, Any]) -> None:
        """Apply new settings live, without reloading the entry."""
        self.config = dict(config)
        self.settings = settings_from_config(self.config)
        self.request_update()

    @callback
    def request_update(self) -> None:
        """Request a control pass (user action, setting change)."""
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_state_change(self, event: Event) -> None:
        if (new_state := event.data.get("new_state")) is None:
            return
        old_state = event.data.get("old_state")
        if old_state is not None and old_state.state == new_state.state:
            return
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_heartbeat(self, now) -> None:
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_first_run(self, now) -> None:
        self.hass.async_create_task(self.async_update())

    # --- Decision ------------------------------------------------------------

    def _snapshot_inputs(self) -> Inputs:
        outdoor_temp = _as_float(self.hass.states.get(self.outdoor_sensor))
        indoor_temp = _as_float(
            self.hass.states.get(self.indoor_sensor), DEFAULT_INDOOR_TEMP
        )
        production = _as_float(
            self.hass.states.get(self.production_sensor), DEFAULT_PRODUCTION
        )
        return Inputs(
            now=dt_util.now(),
            outdoor_temp=outdoor_temp,
            indoor_temp=indoor_temp if indoor_temp is not None else DEFAULT_INDOOR_TEMP,
            production=production if production is not None else DEFAULT_PRODUCTION,
            active=self.shared.active,
            away_mode=self.shared.away_mode,
            heat_pump_source=self.shared.heat_pump_source,
            preheating_flag=self.shared.preheating,
            demand_flag=self.shared.demand,
            forced_source=self.shared.forced_source,
            away_until=self.shared.away_until,
        )

    async def async_update(self) -> None:
        """Full pass: decision then application (serialized)."""
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._update_locked()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error during the control pass")
            finally:
                self._first_run = False

    async def _update_locked(self) -> None:
        shared = self.shared
        if not shared.active:
            # Control disabled: no decision is valid any more.
            if shared.decision is not None:
                shared.set_decision(None)
            return

        decision = compute(self.settings, self._snapshot_inputs())
        shared.set_decision(decision)

        # Return time exceeded: end of away mode (as in YAML).
        if decision.return_passed and shared.away_mode:
            shared.set_away_mode(False)
            shared.set_away_until(None)
        shared.set_preheating(decision.preheating)
        shared.set_demand(decision.heating_needed)

        # PAC / Boiler changeover (or reapplication on first pass).
        if decision.changeover:
            await self._apply_changeover(decision.should_use_heat_pump)
        elif self._first_run:
            await self._enforce_accessory_fans(shared.heat_pump_source)

        # Day/night/away/solar scheduling on the active source.
        await self._apply_program(decision)

    # --- Application ---------------------------------------------------------

    async def _call(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        payload = {ATTR_ENTITY_ID: entity_id, **data}
        try:
            await asyncio.wait_for(
                self.hass.services.async_call(domain, service, payload, blocking=True),
                SERVICE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _LOGGER.warning("Timeout of service %s.%s on %s", domain, service, entity_id)
        except Exception:  # noqa: BLE001 - missing service or entity
            _LOGGER.warning(
                "Failure of service %s.%s on %s", domain, service, entity_id, exc_info=True
            )

    def _is_on(self, entity_id: Optional[str]) -> bool:
        if not entity_id:
            return False
        return self.hass.states.is_state(entity_id, STATE_ON)

    @staticmethod
    def _valid(state: Optional[State]) -> bool:
        return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    async def _wait_fan_on(self, entity_id: str, timeout: int) -> bool:
        """Wait for the fan to turn on, like the YAML wait_template."""
        waited = 0.0
        while waited < timeout:
            if self._is_on(entity_id):
                return True
            await asyncio.sleep(1)
            waited += 1
        return self._is_on(entity_id)

    async def _apply_changeover(self, to_heat_pump: bool) -> None:
        """Reproduces « Heating - PAC/Boiler changeover »."""
        shared = self.shared
        if to_heat_pump:
            _LOGGER.info("Changeover to the heat pump (outdoor temperature high enough)")
            await self._call("climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_OFF)
            for fan in self.accessory_fans:
                await self._call("fan", "turn_on", fan)
            await asyncio.sleep(CHANGEOVER_FAN_DELAY_SECONDS)
            for clim in self.heat_pump_climates:
                await self._call("climate", "set_hvac_mode", clim, hvac_mode=HVAC_HEAT)
            shared.set_heat_pump_source(True)
        else:
            _LOGGER.info("Changeover to the boiler (outdoor temperature too low)")
            for clim in self.heat_pump_climates:
                await self._call("climate", "set_hvac_mode", clim, hvac_mode=HVAC_OFF)
            # Accessory fans (original YAML) and power fans: everything
            # that powers the heat pump must turn off.
            fans = dict.fromkeys(
                [*self.accessory_fans, *(fan for fan in self.heat_pump_fans if fan)]
            )
            for fan in fans:
                await self._call("fan", "turn_off", fan)
            await self._call("climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_AUTO)
            shared.set_heat_pump_source(False)

    async def _enforce_accessory_fans(self, on: bool) -> None:
        """Re-align the accessory fans with the active source."""
        for fan in self.accessory_fans:
            if on and not self._is_on(fan):
                await self._call("fan", "turn_on", fan)
            elif not on and self._is_on(fan):
                await self._call("fan", "turn_off", fan)

    async def _apply_program(self, decision: Decision) -> None:
        """Reproduces « Heating - Day/Night/Away scheduling »."""
        heating_needed = decision.heating_needed
        heat_pump_source = self.shared.heat_pump_source

        pairs: list[tuple[str, Optional[str]]] = (
            list(zip(self.heat_pump_climates, self.heat_pump_fans))
            if heat_pump_source
            else [(self.boiler_climate, None)]
        )

        # Heat pump climate units always stay in "heat" (no cooling risk when
        # idle); the boiler stays in "auto" like the original YAML.
        desired_mode = HVAC_HEAT if heat_pump_source else HVAC_AUTO

        for clim, fan in pairs:
            state = self.hass.states.get(clim)

            # Power fan supply before driving the climate unit.
            fan_on = True
            if fan:
                fan_on = self._is_on(fan)
                if heating_needed and heat_pump_source and not fan_on:
                    if self.hass.states.get(fan) is None:
                        _LOGGER.warning(
                            "Power fan %s does not exist in this instance; "
                            "check the integration configuration. %s stays in "
                            "its current mode",
                            fan,
                            clim,
                        )
                    else:
                        await self._call("fan", "turn_on", fan)
                        fan_on = await self._wait_fan_on(
                            fan, FAN_WAIT_TIMEOUT_SECONDS
                        )
                        if not fan_on:
                            _LOGGER.warning(
                                "Power fan %s did not confirm ON within %s s; "
                                "%s is not switched to heat. Verify the fan is "
                                "the unit's real power relay and that it "
                                "responds to fan.turn_on",
                                fan,
                                FAN_WAIT_TIMEOUT_SECONDS,
                                clim,
                            )

            # HVAC mode: heat (heat pump) or auto (boiler).
            if (
                self._valid(state)
                and state.state != desired_mode  # type: ignore[union-attr]
                and (not heat_pump_source or fan is None or fan_on)
            ):
                await self._call(
                    "climate", "set_hvac_mode", clim, hvac_mode=desired_mode
                )
                await asyncio.sleep(HVAC_MODE_DELAY_SECONDS)
                state = self.hass.states.get(clim)

            # Setpoint: heating setpoint when there is a need, idle setpoint
            # otherwise; the thermostat is never switched off.
            if self._valid(state):
                attributes = state.attributes if state else {}  # type: ignore[union-attr]
                setpoint = (
                    decision.target_setpoint
                    if heating_needed
                    else self.settings.idle_setpoint
                )
                target = target_temperature(
                    setpoint,
                    step=float(attributes.get("target_temp_step") or 0.5),
                    minimum=float(attributes.get("min_temp") or 5.0),
                    maximum=float(attributes.get("max_temp") or 30.0),
                )
                current = self._current_temperature(attributes)
                if current != target:
                    await self._call("climate", "set_temperature", clim, temperature=target)

            # Power fan shutdown when there is no need.
            if fan and not heating_needed and heat_pump_source and self._is_on(fan):
                await self._call("fan", "turn_off", fan)

    @staticmethod
    def _current_temperature(attributes: Mapping[str, Any]) -> float:
        try:
            return float(attributes.get("temperature") or 0)
        except (TypeError, ValueError):
            return 0.0