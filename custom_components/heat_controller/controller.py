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
from dataclasses import replace
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
    CONF_BOILER_DEMAND_SETPOINT,
    CONF_BOILER_RATE,
    CONF_CONSUMPTION_SENSOR,
    CONF_DAY_SETPOINT,
    CONF_DAY_START,
    CONF_FROST_SETPOINT,
    CONF_HEAT_PUMP_CLIMATES,
    CONF_HEAT_PUMP_SWITCHES,
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
    CONF_SPLIT_FAN_OSCILLATING,
    CONF_SPLIT_FAN_PRESET,
    CONF_SPLIT_OFFSET,
    CONF_SOLAR_BONUS,
    CONF_SURPLUS_THRESHOLD,
    SETTING_DEFAULTS,
    DEFAULT_INDOOR_TEMP,
    DEFAULT_PRODUCTION,
    HVAC_MODE_DELAY_SECONDS,
    POLL_INTERVAL_SECONDS,
    SERVICE_TIMEOUT_SECONDS,
    STARTUP_DELAY_SECONDS,
    SWITCH_WAIT_TIMEOUT_SECONDS,
)
from .logic import Decision, Inputs, Settings, compute, parse_time, target_temperature
from .rooms import RoomConfig, rooms_from_subentries, rooms_signature
from .rooms_logic import (
    HouseDecision,
    HouseInputs,
    RoomDecision,
    RoomState,
    compute_house,
    next_solar_bonus,
    resolve_target,
    update_room_states,
)
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
        # `parse_time` returns timedelta(0) for "00:00:00": falsy but valid,
        # so only an actual parse failure falls back to the default.
        parsed = parse_time(config.get(key, SETTING_DEFAULTS[key]))
        return parsed if parsed is not None else parse_time(SETTING_DEFAULTS[key])

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
        split_offset=number(CONF_SPLIT_OFFSET),
        boiler_demand_setpoint=number(CONF_BOILER_DEMAND_SETPOINT),
        solar_bonus=number(CONF_SOLAR_BONUS),
        surplus_threshold=number(CONF_SURPLUS_THRESHOLD),
        split_fan_preset=str(config.get(CONF_SPLIT_FAN_PRESET, SETTING_DEFAULTS[CONF_SPLIT_FAN_PRESET])),
        split_fan_oscillating=bool(
            config.get(CONF_SPLIT_FAN_OSCILLATING, SETTING_DEFAULTS[CONF_SPLIT_FAN_OSCILLATING])
        ),
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
        self.consumption_sensor: str = config.get(CONF_CONSUMPTION_SENSOR, "")

        self.boiler_climate: str = config.get(CONF_BOILER_CLIMATE, "")
        self.heat_pump_climates: list[str] = list(config.get(CONF_HEAT_PUMP_CLIMATES, []))
        heat_pump_switches: list[str] = list(config.get(CONF_HEAT_PUMP_SWITCHES, []))
        # Pairing by order: switch[i] powers climate[i] (None if missing).
        self.heat_pump_switches: list[Optional[str]] = [
            heat_pump_switches[i] if i < len(heat_pump_switches) else None
            for i in range(len(self.heat_pump_climates))
        ]
        self.accessory_fans: list[str] = list(config.get(CONF_ACCESSORY_FANS, []))

        # Rooms (config sub-entries): when present, the hybrid multi-room
        # regulation replaces the single-zone changeover.
        self.rooms: list[RoomConfig] = rooms_from_subentries(
            getattr(entry, "subentries", None)
        )
        self.rooms_signature: tuple = rooms_signature(self.rooms)
        self.room_states: dict[str, RoomState] = {
            room.id: RoomState(room) for room in self.rooms
        }
        self._trigger_demand = False  # latched boiler demand (trigger rooms)
        self._solar_bonus = False  # latched soft overheating state
        # In hybrid mode the decisions and the virtual thermostat follow
        # the thermostat room, not the configured indoor sensor.
        thermostat_room = next((r for r in self.rooms if r.thermostat_room), None)
        if thermostat_room is not None:
            self.indoor_sensor = thermostat_room.sensor

        self._lock = asyncio.Lock()
        self._cancel: list[Callable] = []
        self._first_run = True

    # --- Lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        """Start listening and heartbeat, then first pass."""
        watched = [
            entity
            for entity in (
                self.outdoor_sensor,
                self.indoor_sensor,
                self.production_sensor,
                self.consumption_sensor,
            )
            if entity
        ]
        watched.extend(room.sensor for room in self.rooms if room.sensor)
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

        if self.rooms:
            # Hybrid multi-room regulation.
            await self._update_rooms(decision)
            return

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

    async def _wait_switch_on(self, entity_id: str, timeout: int) -> bool:
        """Wait for the power switch to turn on, like the YAML wait_template."""
        waited = 0.0
        while waited < timeout:
            if self._is_on(entity_id):
                return True
            await asyncio.sleep(1)
            waited += 1
        return self._is_on(entity_id)

    async def _power_on(self, switch: str, clim: str) -> bool:
        """Power a heat pump unit on, before any climate command."""
        if self._is_on(switch):
            return True
        if self.hass.states.get(switch) is None:
            _LOGGER.warning(
                "Power switch %s does not exist in this instance; "
                "check the integration configuration. %s is not driven",
                switch,
                clim,
            )
            return False
        await self._call("switch", "turn_on", switch)
        if not await self._wait_switch_on(switch, SWITCH_WAIT_TIMEOUT_SECONDS):
            _LOGGER.warning(
                "Power switch %s did not confirm ON within %s s; "
                "%s is not driven. Verify the switch is the unit's real "
                "power relay and that it responds to switch.turn_on",
                switch,
                SWITCH_WAIT_TIMEOUT_SECONDS,
                clim,
            )
            return False
        return True

    async def _power_off(self, switch: str) -> None:
        """Power a heat pump unit off (its climate mode is never touched)."""
        if self._is_on(switch):
            await self._call("switch", "turn_off", switch)

    async def _apply_changeover(self, to_heat_pump: bool) -> None:
        """Reproduces « Heating - PAC/Boiler changeover »."""
        shared = self.shared
        if to_heat_pump:
            _LOGGER.info("Changeover to the heat pump (outdoor temperature high enough)")
            await self._call("climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_OFF)
            for fan in self.accessory_fans:
                await self._call("fan", "turn_on", fan)
            await asyncio.sleep(CHANGEOVER_FAN_DELAY_SECONDS)
            # The heat pump units are powered through their switches; the
            # program pass run right after this one turns them on first,
            # before any climate command.
            shared.set_heat_pump_source(True)
        else:
            _LOGGER.info("Changeover to the boiler (outdoor temperature too low)")
            # Cut the power of the heat pump units; their climate mode is
            # never used to switch them off.
            for switch in self.heat_pump_switches:
                if switch:
                    await self._power_off(switch)
            for fan in self.accessory_fans:
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
            list(zip(self.heat_pump_climates, self.heat_pump_switches))
            if heat_pump_source
            else [(self.boiler_climate, None)]
        )

        # The heat pump heats: keep the boiler's setpoint at the idle value
        # on every pass. It was set to "off" at changeover, but if the
        # device ignores or loses that mode, the low setpoint keeps it
        # from heating alongside the heat pump.
        if heat_pump_source and self.boiler_climate:
            await self._push_setpoint(self.boiler_climate, self.settings.idle_setpoint)

        # Heat pump climate units always stay in "heat" (no cooling risk when
        # idle); the boiler stays in "auto" like the original YAML.
        desired_mode = HVAC_HEAT if heat_pump_source else HVAC_AUTO

        for clim, switch in pairs:
            state = self.hass.states.get(clim)

            # Heat pump units are powered through their switch: it must be
            # on before any climate command, and off when there is no
            # heating need (the climate mode is never used to switch the
            # unit off).
            if heat_pump_source and switch is not None:
                if heating_needed:
                    if not await self._power_on(switch, clim):
                        continue
                    # Safe start: re-read the state after the switch is
                    # confirmed ON. A split climate often reports
                    # "unavailable" while unpowered; if it does not come
                    # back, the commands are skipped this cycle and
                    # retried on the next pass.
                    state = self.hass.states.get(clim)
                    if not self._valid(state):
                        _LOGGER.warning(
                            "Climate %s is not available after power switch "
                            "%s went on; commands are skipped this cycle and "
                            "retried on the next pass",
                            clim,
                            switch,
                        )
                        continue
                else:
                    await self._power_off(switch)
                    continue

            # HVAC mode: heat (heat pump) or auto (boiler).
            if self._valid(state) and state.state != desired_mode:  # type: ignore[union-attr]
                await self._call(
                    "climate", "set_hvac_mode", clim, hvac_mode=desired_mode
                )
                await asyncio.sleep(HVAC_MODE_DELAY_SECONDS)
                state = self.hass.states.get(clim)

            # Setpoint: heating setpoint when there is a need, idle setpoint
            # otherwise; a heat pump unit without a power switch keeps the
            # original behavior (on in heat mode with the idle setpoint).
            if self._valid(state):
                setpoint = (
                    decision.target_setpoint
                    if heating_needed
                    else self.settings.idle_setpoint
                )
                await self._push_setpoint(clim, setpoint)

    async def _push_setpoint(self, clim: str, setpoint: float) -> None:
        """Set a climate's temperature, clamped to its own bounds."""
        state = self.hass.states.get(clim)
        if not self._valid(state):
            return
        attributes = state.attributes if state else {}  # type: ignore[union-attr]
        target = target_temperature(
            setpoint,
            step=float(attributes.get("target_temp_step") or 0.5),
            minimum=float(attributes.get("min_temp") or 5.0),
            maximum=float(attributes.get("max_temp") or 30.0),
        )
        current = self._current_temperature(attributes)
        if current != target:
            await self._call("climate", "set_temperature", clim, temperature=target)

    @staticmethod
    def _current_temperature(attributes: Mapping[str, Any]) -> float:
        try:
            return float(attributes.get("temperature") or 0)
        except (TypeError, ValueError):
            return 0.0

    # --- Hybrid multi-room regulation ----------------------------------------

    async def _update_rooms(self, base: Decision) -> None:
        """One hybrid pass: rooms first, then the boiler."""
        shared = self.shared
        # Observe the rooms: temperatures and actual switch states.
        for room in self.rooms:
            state = self.room_states.setdefault(room.id, RoomState(room))
            state.temp = _as_float(self.hass.states.get(room.sensor))
            state.splits_on = any(self._is_on(switch) for switch in room.switches)
            state.base_target = resolve_target(self.rooms, room)
        # Soft overheating: latched on the solar surplus (or, without a
        # consumption sensor, on the existing production threshold).
        self._solar_bonus = next_solar_bonus(
            self.settings,
            _as_float(self.hass.states.get(self.production_sensor)),
            _as_float(self.hass.states.get(self.consumption_sensor))
            if self.consumption_sensor
            else None,
            self._solar_bonus,
        )
        house = compute_house(
            self.settings,
            HouseInputs(
                now=dt_util.now(),
                outdoor_temp=_as_float(self.hass.states.get(self.outdoor_sensor)),
                away_mode=shared.away_mode,
                forced_source=shared.forced_source,
                solar_bonus_active=self._solar_bonus,
            ),
            self.rooms,
            self.room_states,
            trigger_demand=self._trigger_demand,
        )
        self._trigger_demand = house.trigger_demand

        # The global decision keeps feeding the existing entities, with
        # the hybrid result overriding the single-zone fields.
        heating_needed = house.boiler_demand or any(
            decision.want_splits for decision in house.rooms
        )
        merged = replace(
            base,
            target_setpoint=(
                house.thermostat_room_target
                if house.thermostat_room_target is not None
                else base.target_setpoint
            ),
            heating_needed=heating_needed,
            should_use_heat_pump=house.splits_allowed,
            changeover=house.splits_allowed != shared.heat_pump_source,
        )
        shared.set_decision(merged)
        shared.set_demand(heating_needed)

        boiler_heating = self._boiler_heating()
        update_room_states(house, self.room_states, boiler_heating)

        for room, decision in zip(self.rooms, house.rooms):
            await self._apply_room(room, decision)

        await self._apply_hybrid_boiler(house)
        # Accessory fans follow the active source, as in the single-zone
        # changeover: on with the splits, off with the boiler.
        await self._enforce_accessory_fans(house.splits_allowed)

        shared.set_heat_pump_source(house.splits_allowed)

    def _boiler_heating(self) -> bool:
        state = self.hass.states.get(self.boiler_climate)
        return (
            state is not None
            and state.attributes.get("hvac_action") == "heating"
        )

    async def _apply_room(self, room: RoomConfig, decision: RoomDecision) -> None:
        """Apply the splits decision of one room."""
        if not room.has_splits:
            return
        state = self.room_states[room.id]

        if not decision.splits_allowed:
            # Forced stop (night, away, cold outside): only through the
            # power switches, whatever the room sensor says.
            for switch in room.switches:
                await self._power_off(switch)
            state.last_want = False
            return

        if not decision.controllable:
            # Sensor unavailable: the room is simply ignored.
            _LOGGER.debug("Room %s ignored: no usable sensor or target", room.name)
            return

        if not decision.want_splits:
            for switch in room.switches:
                await self._power_off(switch)
            state.last_want = False
            return

        # Safe start: every power switch must confirm ON, and every
        # climate entity must be available, before any command.
        for switch in room.switches:
            if not await self._power_on(switch, room.name):
                return  # retried on the next pass
        if any(not self._valid(self.hass.states.get(clim)) for clim in room.climates):
            _LOGGER.warning(
                "A climate entity of room %s is not available after the "
                "splits went on; commands are skipped this cycle and "
                "retried on the next pass",
                room.name,
            )
            return

        for clim in room.climates:
            climate_state = self.hass.states.get(clim)
            if climate_state is not None and climate_state.state != HVAC_HEAT:
                await self._call("climate", "set_hvac_mode", clim, hvac_mode=HVAC_HEAT)
                await asyncio.sleep(HVAC_MODE_DELAY_SECONDS)
            setpoint = (decision.target or 0.0) + self.settings.split_offset
            await self._push_setpoint(clim, setpoint)

        # Fan preset and oscillation are (re)applied on each start.
        if state.last_want is not True:
            for fan in room.fans:
                await self._call(
                    "fan",
                    "set_preset_mode",
                    fan,
                    preset_mode=self.settings.split_fan_preset,
                )
                await self._call(
                    "fan",
                    "oscillate",
                    fan,
                    oscillating=self.settings.split_fan_oscillating,
                )
        state.last_want = True

    async def _apply_hybrid_boiler(self, house: HouseDecision) -> None:
        """Drive the boiler thermostat: rest setpoint, or demand."""
        if not self.boiler_climate or house.boiler_setpoint is None:
            return
        state = self.hass.states.get(self.boiler_climate)
        if not self._valid(state):
            return
        # The boiler stays in auto in hybrid mode: it must be able to
        # answer a demand from a trigger room at any time.
        if state is not None and state.state != HVAC_AUTO:
            await self._call(
                "climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_AUTO
            )
            await asyncio.sleep(HVAC_MODE_DELAY_SECONDS)
        await self._push_setpoint(self.boiler_climate, house.boiler_setpoint)