"""Heat Controller: reads state, decides, applies.

Multi-room hybrid regulation: the splits heat the rooms on mild days,
the boiler rests at the idle setpoint and answers the demands of the
trigger rooms; cold days, nights and absences switch to the boiler
following the thermostat room's target.

One pass per minute and on every watched sensor change: the decisions
are idempotent and protected by hysteresis.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
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
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACCESSORY_FANS,
    CONF_AWAY_SETPOINT,
    CONF_BOILER_CLIMATE,
    CONF_BOILER_DEMAND_SETPOINT,
    CONF_CONSUMPTION_SENSOR,
    CONF_DAY_END_WEEK,
    CONF_DAY_END_WEEKEND,
    CONF_DAY_START,
    CONF_DAY_START_WEEK,
    CONF_DAY_START_WEEKEND,
    CONF_FALLBACK_MINUTES,
    CONF_FROST_SETPOINT,
    CONF_HYSTERESIS,
    CONF_IDLE_SETPOINT,
    CONF_NIGHT_SETPOINT,
    CONF_NIGHT_START,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_OUTDOOR_THRESHOLD,
    CONF_PRODUCTION_SENSOR,
    CONF_PRODUCTION_THRESHOLD,
    CONF_RETURN_TIME,
    CONF_SOLAR_BONUS,
    CONF_SPLIT_FAN_OSCILLATING,
    CONF_SPLIT_FAN_PRESET,
    CONF_SPLIT_OFFSET,
    CONF_SURPLUS_THRESHOLD,
    CONF_FALLBACK_MINUTES,
    CONF_MIN_CYCLE_MINUTES,
    CONF_INITIAL_PREHEAT_MINUTES,
    CONF_MAX_PREHEAT_MINUTES,
    CONF_WEATHER_ENTITY,
    CONF_NOTIFY_SERVICE,
    DAY_END_WEEKEND_FALLBACK,
    DAY_END_WEEK_FALLBACK,
    DAY_START_WEEKEND_FALLBACK,
    DAY_START_WEEK_FALLBACK,
    DOMAIN,
    SETTING_DEFAULTS,
    HVAC_MODE_DELAY_SECONDS,
    POLL_INTERVAL_SECONDS,
    SERVICE_TIMEOUT_SECONDS,
    STARTUP_DELAY_SECONDS,
    SWITCH_WAIT_TIMEOUT_SECONDS,
)
from .logic import (
    Decision,
    Settings,
    at_time,
    parse_time,
    target_temperature,
)
from .rooms import RoomConfig, rooms_from_subentries, rooms_signature
from .rooms_logic import (
    HouseDecision,
    HouseInputs,
    RoomDecision,
    RoomState,
    compute_house,
    detect_manual,
    next_solar_bonus,
    resolve_target,
    update_fallback,
    update_learning,
    update_room_states,
)
from .state import HeatState

_LOGGER = logging.getLogger(__name__)

HVAC_HEAT = "heat"
HVAC_OFF = "off"

STORAGE_VERSION = 1  # learned heating rates
FORECAST_CACHE_MINUTES = 30

NOTIFICATION_TITLE = "Heat Controller"
NOTIFICATIONS: dict[str, dict[str, str]] = {
    "en": {
        "away_start": "Away mode enabled. Planned return: {when}.",
        "away_start_open": "Away mode enabled (no return planned).",
        "away_end": "Away mode ended. Back to the normal schedule.",
        "fallback": "The splits in {room} are not reaching the target: "
        "the boiler takes over.",
        "unresponsive": "The splits in {room} are not responding.",
        "surplus": "Solar surplus in use ({surplus} W): the split rooms are "
        "heated above their target (+{bonus} °C).",
        "window_open": "A window is open in {room}: its splits are stopped "
        "until it is closed.",
    },
    "fr": {
        "away_start": "Mode absence activé. Retour prévu : {when}.",
        "away_start_open": "Mode absence activé (aucun retour prévu).",
        "away_end": "Mode absence terminé. Retour au planning normal.",
        "fallback": "Les splits de {room} n'atteignent pas la cible : "
        "la chaudière prend le relais.",
        "unresponsive": "Le split de {room} ne répond pas.",
        "surplus": "Surplus solaire utilisé ({surplus} W) : les pièces avec "
        "splits sont chauffées au-dessus de leur consigne (+{bonus} °C).",
        "window_open": "Une fenêtre est ouverte dans {room} : ses splits sont "
        "arrêtés jusqu'à sa fermeture.",
    },
}


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

    def day_moment(key: str, fallbacks: tuple[str, ...]) -> timedelta:
        # Week/weekend schedule key. Older entries carry the per-weekday
        # keys of the intermediate schedule (most recent, tried first) or
        # the legacy single day_start/night_start pair; an explicit but
        # invalid value falls back to the default, like `moment`.
        if key in config:
            parsed = parse_time(config[key])
        else:
            parsed = None
            for fallback in fallbacks:
                if fallback in config:
                    parsed = parse_time(config[fallback])
                    if parsed is not None:
                        break
        if parsed is not None:
            return parsed
        return parse_time(SETTING_DEFAULTS[key])

    return Settings(
        outdoor_threshold=number(CONF_OUTDOOR_THRESHOLD),
        night_setpoint=number(CONF_NIGHT_SETPOINT),
        away_setpoint=number(CONF_AWAY_SETPOINT),
        idle_setpoint=number(CONF_IDLE_SETPOINT),
        frost_setpoint=number(CONF_FROST_SETPOINT),
        hysteresis=number(CONF_HYSTERESIS),
        production_threshold=number(CONF_PRODUCTION_THRESHOLD),
        day_start_week=day_moment(
            CONF_DAY_START_WEEK, (DAY_START_WEEK_FALLBACK, CONF_DAY_START)
        ),
        day_end_week=day_moment(
            CONF_DAY_END_WEEK, (DAY_END_WEEK_FALLBACK, CONF_NIGHT_START)
        ),
        day_start_weekend=day_moment(
            CONF_DAY_START_WEEKEND, (DAY_START_WEEKEND_FALLBACK, CONF_DAY_START)
        ),
        day_end_weekend=day_moment(
            CONF_DAY_END_WEEKEND, (DAY_END_WEEKEND_FALLBACK, CONF_NIGHT_START)
        ),
        return_time=moment(CONF_RETURN_TIME),
        split_offset=number(CONF_SPLIT_OFFSET),
        boiler_demand_setpoint=number(CONF_BOILER_DEMAND_SETPOINT),
        solar_bonus=number(CONF_SOLAR_BONUS),
        surplus_threshold=number(CONF_SURPLUS_THRESHOLD),
        fallback_minutes=number(CONF_FALLBACK_MINUTES),
        min_cycle_minutes=number(CONF_MIN_CYCLE_MINUTES),
        initial_preheat_minutes=number(CONF_INITIAL_PREHEAT_MINUTES),
        max_preheat_minutes=number(CONF_MAX_PREHEAT_MINUTES),
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
        self.production_sensor: str = config.get(CONF_PRODUCTION_SENSOR, "")
        self.consumption_sensor: str = config.get(CONF_CONSUMPTION_SENSOR, "")

        self.boiler_climate: str = config.get(CONF_BOILER_CLIMATE, "")
        self.accessory_fans: list[str] = list(config.get(CONF_ACCESSORY_FANS, []))

        # Rooms (config sub-entries): the hybrid multi-room regulation.
        self.rooms: list[RoomConfig] = rooms_from_subentries(
            getattr(entry, "subentries", None)
        )
        self.rooms_signature: tuple = rooms_signature(self.rooms)
        self.room_states: dict[str, RoomState] = {
            room.id: RoomState(room) for room in self.rooms
        }
        self._trigger_demand = False  # latched boiler demand (trigger rooms)
        self._trigger_changed_at: Optional[datetime] = None  # for the debounce
        self._solar_bonus = False  # latched soft overheating state
        # The decisions and the virtual thermostat follow the thermostat
        # room (the first room by default).
        thermostat_room = next(
            (r for r in self.rooms if r.thermostat_room), self.rooms[0] if self.rooms else None
        )
        self.thermostat_room_id: Optional[str] = (
            thermostat_room.id if thermostat_room else None
        )
        self.indoor_sensor: str = thermostat_room.sensor if thermostat_room else ""

        self._lock = asyncio.Lock()
        self._cancel: list[Callable] = []
        self._first_run = True
        self._away_last = shared.away_mode  # no away notification on startup
        # Learned heating rates: persisted store + forecast cache.
        self._store = None  # created in async_start (needs a real hass)
        self._forecast_temp: Optional[float] = None
        self._forecast_time: Optional[datetime] = None

    # --- Lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        """Start listening and heartbeat, then first pass."""
        self._store = Store(self.hass, STORAGE_VERSION, f"{DOMAIN}.{self.entry_id}")
        await self._load_learned_rates()
        watched = [
            entity
            for entity in (
                self.outdoor_sensor,
                self.production_sensor,
                self.consumption_sensor,
            )
            if entity
        ]
        watched.extend(room.sensor for room in self.rooms if room.sensor)
        for room in self.rooms:
            watched.extend(sensor for sensor in room.window_sensors if sensor)
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

    def thermostat_room(self) -> Optional[RoomConfig]:
        """The room the boiler thermostat lives in."""
        return next((r for r in self.rooms if r.id == self.thermostat_room_id), None)

    def thermostat_room_target(self) -> float:
        """Day target of the thermostat room (for the thermostat card)."""
        room = self.thermostat_room()
        if room is None:
            return self.settings.night_setpoint
        target = resolve_target(self.rooms, room)
        return target if target is not None else self.settings.night_setpoint

    def _away_end(self) -> Optional[datetime]:
        """Effective end of the absence: the planned return, or the
        daily return time as a fallback."""
        now = dt_util.now()
        if self.shared.away_until is not None:
            return self.shared.away_until
        if self.shared.away_mode:
            return at_time(now, self.settings.return_time)
        return None

    async def _update_locked(self) -> None:
        shared = self.shared
        if not shared.active:
            # Control disabled: no decision is valid any more.
            if shared.decision is not None:
                shared.set_decision(None)
            if shared.house is not None:
                shared.set_house(None)
            return

        # Away mode start/end notifications (whatever triggered them).
        if shared.away_mode != self._away_last:
            if shared.away_mode:
                if shared.away_until is not None:
                    when = dt_util.as_local(shared.away_until).strftime("%d/%m %H:%M")
                    await self._notify("away_start", when=when)
                else:
                    await self._notify("away_start_open")
            else:
                await self._notify("away_end")
            self._away_last = shared.away_mode

        # Return time exceeded: end of away mode (as in the YAML).
        away_end = self._away_end()
        return_passed = shared.away_mode and away_end is not None and dt_util.now() >= away_end
        if return_passed:
            shared.set_away_mode(False)
            shared.set_away_until(None)

        if not self.rooms:
            _LOGGER.warning(
                "No room is configured on this entry: nothing is controlled. "
                "Add rooms on the integration page."
            )
            if shared.decision is not None:
                shared.set_decision(None)
            if shared.house is not None:
                shared.set_house(None)
            return

        await self._update_rooms(return_passed)

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

    async def _enforce_accessory_fans(self, on: bool) -> None:
        """Re-align the accessory fans with the active source."""
        for fan in self.accessory_fans:
            if on and not self._is_on(fan):
                await self._call("fan", "turn_on", fan)
            elif not on and self._is_on(fan):
                await self._call("fan", "turn_off", fan)

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

    async def _update_rooms(self, return_passed: bool) -> None:
        """One hybrid pass: rooms first, then the boiler."""
        shared = self.shared
        # Observe the rooms: temperatures and actual switch states.
        for room in self.rooms:
            state = self.room_states.setdefault(room.id, RoomState(room))
            state.temp = _as_float(self.hass.states.get(room.sensor))
            actual_on = any(self._is_on(switch) for switch in room.switches)
            if state.splits_on != actual_on:
                state.last_change = dt_util.now()  # for the minimum run/stop time
            state.splits_on = actual_on
            state.base_target = resolve_target(self.rooms, room)
            # Window sensors: any "on" means open; an unavailable or
            # unknown sensor counts as closed (the splits may run).
            window_open = any(
                self.hass.states.is_state(sensor, STATE_ON)
                for sensor in room.window_sensors
            )
            state.window_open = window_open
            if window_open and actual_on and not state.window_notified:
                # A running split is cut because of an open window.
                state.window_notified = True
                await self._notify("window_open", room=room.name)
            if not window_open:
                state.window_notified = False
            # A split changed by someone else: leave the room alone until
            # the next mode transition. A command never followed is
            # reported once as unresponsive.
            event = detect_manual(state, state.splits_on, dt_util.now())
            if event == "manual":
                _LOGGER.info(
                    "The splits of room %s were changed manually; the room "
                    "is left alone until the next mode transition",
                    room.name,
                )
            elif event == "unresponsive":
                _LOGGER.warning(
                    "The splits of room %s are not following the commands",
                    room.name,
                )
                await self._notify("unresponsive", room=room.name)
        # Soft overheating: latched on the solar surplus (or, without a
        # consumption sensor, on the existing production threshold).
        production = _as_float(self.hass.states.get(self.production_sensor))
        consumption = (
            _as_float(self.hass.states.get(self.consumption_sensor))
            if self.consumption_sensor
            else None
        )
        previous_bonus = self._solar_bonus
        self._solar_bonus = next_solar_bonus(
            self.settings, production, consumption, self._solar_bonus
        )
        if self._solar_bonus and not previous_bonus:
            # The surplus starts being used: notify once, on the rising edge.
            surplus = (
                production - consumption
                if production is not None and consumption is not None
                else production
            )
            await self._notify(
                "surplus",
                surplus=f"{round(surplus):+d}" if surplus is not None else "0",
                bonus=self.settings.solar_bonus,
            )
        away_end = self._away_end()
        house = compute_house(
            self.settings,
            HouseInputs(
                now=dt_util.now(),
                outdoor_temp=_as_float(self.hass.states.get(self.outdoor_sensor)),
                away_mode=shared.away_mode,
                forced_source=shared.forced_source,
                solar_bonus_active=self._solar_bonus,
                production=production,
                consumption=consumption,
                away_end=away_end,
                forecast=await self._forecast_temperature(),
                # Boiler demand debounce: a latched demand is not
                # released before the minimum run/stop time has passed.
                demand_hold_expired=(
                    self._trigger_changed_at is None
                    or dt_util.now() - self._trigger_changed_at
                    >= timedelta(minutes=self.settings.min_cycle_minutes)
                ),
            ),
            self.rooms,
            self.room_states,
            trigger_demand=self._trigger_demand,
            fallback_demand=any(state.fallback for state in self.room_states.values()),
        )
        if house.trigger_demand != self._trigger_demand:
            # Latched demand changed now: the debounce window restarts.
            self._trigger_changed_at = dt_util.now()
        self._trigger_demand = house.trigger_demand
        shared.set_house(house)

        # Global decision feeding the entities, built from the house.
        heating_needed = house.boiler_demand or any(
            decision.want_splits for decision in house.rooms
        )
        any_preheating = any(decision.preheating for decision in house.rooms)
        frost = any(
            decision.temp is not None
            and decision.temp <= self.settings.frost_setpoint
            for decision in house.rooms
        )
        target = house.thermostat_room_target
        decision = Decision(
            in_day_period=house.mode == "day",
            in_solar_period=house.solar_bonus,
            preheating_minutes=int(
                max((d.preheat_minutes for d in house.rooms), default=0)
            ),
            return_passed=return_passed,
            away_mode_active=shared.away_mode,
            preheating=any_preheating,
            target_setpoint=target
            if target is not None
            else self.settings.night_setpoint,
            heating_needed=heating_needed or frost,
            should_use_heat_pump=house.splits_allowed,
            changeover=house.splits_allowed != shared.heat_pump_source,
            frost_protection=frost,
        )
        shared.set_decision(decision)
        shared.set_demand(decision.heating_needed)
        shared.set_preheating(any_preheating)

        boiler_heating = self._boiler_heating()
        update_room_states(house, self.room_states, boiler_heating)

        # Stall detection: a running split that does not progress asks
        # the boiler for help (from the next cycle on, until the target
        # is reached again).
        outdoor_temp = _as_float(self.hass.states.get(self.outdoor_sensor))
        learned = False
        for decision in house.rooms:
            state = self.room_states.get(decision.room_id)
            if state is None:
                continue
            update_fallback(
                state, decision, self.settings, house.now or dt_util.now()
            )
            if state.fallback and not state.fallback_notify:
                state.fallback_notify = True
                _LOGGER.warning(
                    "The splits of room %s are not reaching the target: the "
                    "boiler is asked to take over",
                    state.config.name,
                )
                await self._notify("fallback", room=state.config.name)
            # Heating rate learning (splits or boiler heating the room).
            if update_learning(
                state,
                decision,
                house.now or dt_util.now(),
                outdoor_temp,
                boiler_heating,
            ):
                learned = True
        if learned:
            self._save_learned_rates()

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

    async def _load_learned_rates(self) -> None:
        """Restore the learned heating rates from the store."""
        if self._store is None:
            return
        data = await self._store.async_load() or {}
        for room_id, values in (data.get("rooms") or {}).items():
            state = self.room_states.get(room_id)
            if state is None:
                continue
            state.rate = values.get("rate")
            state.ref_outdoor = values.get("ref_outdoor")

    def _save_learned_rates(self) -> None:
        """Persist the learned rates (delayed, storage may be absent)."""
        if self._store is None:
            return
        self._store.async_delay_save(
            (
                lambda: {
                    "rooms": {
                        room_id: {
                            "rate": state.rate,
                            "ref_outdoor": state.ref_outdoor,
                        }
                        for room_id, state in self.room_states.items()
                    }
                }
            ),
            10,
        )

    async def _forecast_temperature(self) -> Optional[float]:
        """Average outdoor temperature of the next hours (cached)."""
        entity = str(self.config.get(CONF_WEATHER_ENTITY, "") or "")
        if not entity:
            return None
        now = dt_util.utcnow()
        if (
            self._forecast_time is not None
            and now - self._forecast_time < timedelta(minutes=FORECAST_CACHE_MINUTES)
        ):
            return self._forecast_temp
        self._forecast_time = now
        self._forecast_temp = None
        for forecast_type, count in (("hourly", 6), ("daily", 1)):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"type": forecast_type},
                    target={"entity_id": entity},
                    blocking=True,
                    return_response=True,
                )
            except Exception:  # noqa: BLE001 - no weather service or entity
                continue
            items = ((response or {}).get(entity) or {}).get("forecast", [])
            temps = [
                item["temperature"]
                for item in items[:count]
                if item.get("temperature") is not None
            ]
            if temps:
                self._forecast_temp = sum(temps) / len(temps)
                break
        return self._forecast_temp

    async def _notify(self, key: str, **kwargs: Any) -> None:
        """Send a notification through the configured notify service."""
        service = str(self.config.get(CONF_NOTIFY_SERVICE, "") or "")
        if not service:
            return
        language = str(getattr(self.hass.config, "language", "en") or "en")
        catalog = NOTIFICATIONS.get("fr" if language.startswith("fr") else "en")
        message = catalog[key].format(**kwargs)
        try:
            await self.hass.services.async_call(
                "notify",
                service.removeprefix("notify."),
                {"title": NOTIFICATION_TITLE, "message": message},
                blocking=True,
            )
        except Exception:  # noqa: BLE001 - missing service or entity
            _LOGGER.warning("Could not send the %s notification", key, exc_info=True)

    async def _apply_room(self, room: RoomConfig, decision: RoomDecision) -> None:
        """Apply the splits decision of one room."""
        if not room.has_splits:
            return
        state = self.room_states[room.id]
        now = dt_util.now()

        if decision.window_blocked:
            # Open (or just closed) window: everything off, like
            # switching the heating off — power switches, climate modes
            # and fans.
            for switch in room.switches:
                await self._power_off(switch)
            for clim in room.climates:
                climate_state = self.hass.states.get(clim)
                if climate_state is not None and climate_state.state != HVAC_OFF:
                    await self._call(
                        "climate", "set_hvac_mode", clim, hvac_mode=HVAC_OFF
                    )
            for fan in room.fans:
                if self._is_on(fan):
                    await self._call("fan", "turn_off", fan)
            if state.last_want is not False:
                # New command: the switches must follow it (the
                # confirmation is checked on the next passes).
                state.cmd_on = False
                state.cmd_time = now
                state.confirmed = False
            state.last_want = False
            return

        if not decision.splits_allowed:
            # Forced stop (night, away, cold outside): only through the
            # power switches, whatever the room sensor says.
            for switch in room.switches:
                await self._power_off(switch)
            if state.last_want is not False:
                # New command: the switches must follow it (the
                # confirmation is checked on the next passes).
                state.cmd_on = False
                state.cmd_time = now
                state.confirmed = False
            state.last_want = False
            return

        if decision.left_alone:
            # Manual override: the room is not driven.
            _LOGGER.debug("Room %s left alone (manual override)", room.name)
            return

        if not decision.controllable:
            # Sensor unavailable: the room is simply ignored.
            _LOGGER.debug("Room %s ignored: no usable sensor or target", room.name)
            return

        if not decision.want_splits:
            for switch in room.switches:
                await self._power_off(switch)
            if state.last_want is not False:
                state.cmd_on = False
                state.cmd_time = now
                state.confirmed = False
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

        # Fan preset and oscillation are (re)applied on each start; the
        # preset may be overridden per room ("inherit" uses the global one).
        if state.last_want is not True:
            preset = room.fan_preset_or(self.settings.split_fan_preset)
            for fan in room.fans:
                await self._call(
                    "fan",
                    "set_preset_mode",
                    fan,
                    preset_mode=preset,
                )
                await self._call(
                    "fan",
                    "oscillate",
                    fan,
                    oscillating=self.settings.split_fan_oscillating,
                )
        # New command only on the off -> on transition; the switches
        # must follow it (checked on the next passes).
        if state.last_want is not True:
            state.cmd_on = True
            state.cmd_time = now
            state.confirmed = False
        state.last_want = True

    async def _apply_hybrid_boiler(self, house: HouseDecision) -> None:
        """Drive the boiler thermostat: rest setpoint, or demand."""
        if not self.boiler_climate or house.boiler_setpoint is None:
            return
        state = self.hass.states.get(self.boiler_climate)
        if not self._valid(state):
            return
        # The HVAC mode is never touched in hybrid mode: many boiler
        # thermostats (Netatmo) only support off/heat, the boiler must
        # be able to answer a demand at any time, and a mode set by
        # hand (e.g. off for the summer) is respected. Only the
        # setpoint is driven.
        await self._push_setpoint(self.boiler_climate, house.boiler_setpoint)