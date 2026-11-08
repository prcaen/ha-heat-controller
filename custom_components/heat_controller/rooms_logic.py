"""Hybrid multi-room decision logic.

Voluntarily independent of Home Assistant, like `logic.py`: the pure
functions below take the settings, the house inputs, the room
configurations and the runtime room states, and return the decisions the
controller applies (splits on/off per room, boiler thermostat setpoint,
boiler demand). The controller owns the state and does the I/O.

Strategy (mild day, outdoor >= threshold or unknown):
the splits regulate each room; the boiler thermostat stays at the idle
(rest) setpoint and is only raised to the demand setpoint while a
boiler-trigger room is below its target (or a split room stalls, handled
by the caller). Cold outside, night or away: the splits are off and the
boiler follows the thermostat room's target, raised to the demand
setpoint while there is a demand. A room may opt in to pre-heat with
its own splits (the PAC) during its pre-heating window; the other rooms
keep the boiler pre-heating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .logic import (
    SOURCE_AUTO,
    SOURCE_BOILER,
    SOURCE_HEAT_PUMP,
    Settings,
    in_day_period,
    next_day_start,
)
from .rooms import RoomConfig

MODE_DAY = "day"
MODE_NIGHT = "night"
MODE_AWAY = "away"

SOURCE_IDLE = "idle"
SOURCE_SPLITS = "splits"
SOURCE_BOILER_SOURCE = "boiler"

# Minimum temperature rise (°C) counted as progress for the boiler fallback.
PROGRESS_STEP = 0.2


@dataclass
class RoomState:
    """Runtime state of a room, owned by the controller."""

    config: RoomConfig
    temp: Optional[float] = None
    mode: str = MODE_DAY
    base_target: Optional[float] = None  # day target, follow resolved
    target: Optional[float] = None  # effective target (mode + bonus + frost)
    splits_on: bool = False  # observed state of the switches
    want_splits: bool = False  # regulation decision
    last_want: Optional[bool] = None  # last applied decision (fan commands)
    source: str = SOURCE_IDLE
    # Boiler fallback: progress tracking of a running split.
    best_temp: Optional[float] = None
    best_time: Optional[datetime] = None
    fallback: bool = False
    fallback_notify: bool = False
    # Manual override and command tracking.
    cmd_on: Optional[bool] = None  # last commanded state of the switches
    cmd_time: Optional[datetime] = None
    confirmed: bool = False  # the switches had followed the command
    unresponsive_notified: bool = False
    override_mode: Optional[str] = None  # mode during which the room is left alone
    last_change: Optional[datetime] = None  # when the switches last changed state
    # Learned heating rate (persisted by the controller).
    session: Optional[dict] = None  # heating session in progress
    rate: Optional[float] = None  # learned °C/h
    ref_outdoor: Optional[float] = None  # outdoor temp when the rate was learned
    preheat_minutes: float = 0.0
    next_preheat: Optional[datetime] = None


@dataclass(frozen=True)
class HouseInputs:
    """Instantaneous house state used for the hybrid decision."""

    now: datetime
    outdoor_temp: Optional[float]  # None = unknown -> splits still allowed
    away_mode: bool
    forced_source: str = SOURCE_AUTO
    production: Optional[float] = None  # W produced right now
    consumption: Optional[float] = None  # W consumed right now (None = no sensor)
    solar_bonus_active: bool = False  # latched soft overheating state
    away_end: Optional[datetime] = None  # planned return (away mode)
    forecast: Optional[float] = None  # average forecast outdoor temperature
    # False while the boiler demand debounce holds: a latched demand is
    # not released before the minimum run/stop time has passed, so a
    # room sensor fluttering across its dead band cannot sawtooth the
    # demand (and the boiler setpoint) every minute.
    demand_hold_expired: bool = True


def next_solar_bonus(
    settings: Settings,
    production: Optional[float],
    consumption: Optional[float],
    previous: bool,
) -> bool:
    """Soft overheating state, with a hysteresis band on the surplus.

    With a consumption sensor: the bonus starts once the surplus
    (production - consumption) reaches the threshold and stops only when
    it falls to minus the threshold, so the splits' own consumption
    cannot flip the mode. Without one, the existing production threshold
    is used.
    """
    if consumption is None:
        return production is not None and production >= settings.production_threshold
    surplus = (production or 0.0) - consumption
    if surplus >= settings.surplus_threshold:
        return True
    if surplus <= -settings.surplus_threshold:
        return False
    return previous


@dataclass
class RoomDecision:
    """Decision for one room."""

    room_id: str
    mode: str
    temp: Optional[float]
    target: Optional[float]
    controllable: bool  # sensor and target known
    splits_allowed: bool  # splits may run (effective day + outdoor + override)
    want_splits: bool
    left_alone: bool  # manual override: the room is not driven
    preheating: bool  # the room is pre-heated ahead of the schedule
    preheat_minutes: float
    next_preheat: Optional[datetime]
    boiler_trigger_cold: bool  # below target - hysteresis (trigger rooms)
    source: str


@dataclass
class HouseDecision:
    """Decisions for the whole house."""

    mode: str
    splits_allowed: bool
    rooms: list[RoomDecision] = field(default_factory=list)
    boiler_demand: bool = False  # trigger or fallback demand (this cycle)
    trigger_demand: bool = False  # latched trigger demand for the next cycle
    boiler_setpoint: Optional[float] = None
    thermostat_room_target: Optional[float] = None
    solar_bonus: bool = False  # soft overheating active (this cycle)
    now: Optional[datetime] = None  # instant of the decision
    production: Optional[float] = None  # W produced (for the mode sensor)
    consumption: Optional[float] = None  # W consumed (for the mode sensor)

    def room(self, room_id: str) -> Optional[RoomDecision]:
        return next((d for d in self.rooms if d.room_id == room_id), None)


def resolve_target(rooms: list[RoomConfig], room: RoomConfig, depth: int = 0) -> Optional[float]:
    """Day target of a room, following another room when configured."""
    if room.target is not None:
        return room.target
    if room.follow_room and depth < 3:
        followed = next((r for r in rooms if r.id == room.follow_room), None)
        if followed is not None:
            return resolve_target(rooms, followed, depth + 1)
    return None


def house_mode(settings: Settings, inputs: HouseInputs) -> str:
    """Global mode: away wins, then the weekly day/night schedule."""
    if inputs.away_mode:
        return MODE_AWAY
    if in_day_period(settings, inputs.now):
        return MODE_DAY
    return MODE_NIGHT


def outdoor_allows(settings: Settings, inputs: HouseInputs) -> bool:
    """Manual override and outdoor threshold, mode independent."""
    if inputs.forced_source == SOURCE_BOILER:
        return False
    if inputs.forced_source == SOURCE_HEAT_PUMP:
        return True
    return inputs.outdoor_temp is None or inputs.outdoor_temp >= settings.outdoor_threshold


def room_target(
    settings: Settings,
    mode: str,
    base_target: Optional[float],
) -> Optional[float]:
    """Effective target of a room: mode setpoint, never below frost."""
    if mode == MODE_NIGHT:
        target: Optional[float] = settings.night_setpoint
    elif mode == MODE_AWAY:
        target = settings.away_setpoint
    elif base_target is not None:
        target = base_target
    else:
        target = None
    if target is not None:
        target = max(target, settings.frost_setpoint)
    return target


def preheat_duration(
    settings: Settings,
    state: RoomState,
    base_target: Optional[float],
    forecast: Optional[float],
) -> float:
    """Pre-heating duration of a room, in minutes.

    (target - current) / learned rate, adjusted with the weather
    forecast (longer when colder than when the rate was learned),
    capped by the maximum duration. A fixed duration is used until a
    rate has been learned.
    """
    if (
        state.rate is not None
        and state.rate > 0.05
        and state.temp is not None
        and base_target is not None
    ):
        minutes = max(0.0, base_target - state.temp) / state.rate * 60
        if forecast is not None and state.ref_outdoor is not None:
            factor = 1 + WEATHER_FACTOR_PER_DEGREE * (state.ref_outdoor - forecast)
            minutes *= min(max(factor, WEATHER_FACTOR_MIN), WEATHER_FACTOR_MAX)
    else:
        minutes = settings.initial_preheat_minutes
    return min(minutes, settings.max_preheat_minutes)


def preheat_window(
    settings: Settings,
    inputs: HouseInputs,
    minutes: float,
    mode: str,
) -> tuple[bool, Optional[datetime]]:
    """Whether the pre-heating is active, and when it started."""
    delta = timedelta(minutes=minutes)
    now = inputs.now
    if mode == MODE_AWAY:
        end = inputs.away_end
        if end is None:
            return False, None
        start = end - delta
        return start <= now < end, start
    if mode == MODE_DAY:
        return False, None
    # Night: the next day period start of the weekly schedule.
    next_start = next_day_start(settings, now)
    if next_start is None:
        return False, None
    start = next_start - delta
    return start <= now < next_start, start


def compute_house(
    settings: Settings,
    inputs: HouseInputs,
    rooms: list[RoomConfig],
    states: dict[str, RoomState],
    trigger_demand: bool = False,
    fallback_demand: bool = False,
) -> HouseDecision:
    """Compute the hybrid decision for all the rooms and the boiler.

    `trigger_demand` is the latched demand of the previous cycle (the
    boiler keeps heating until every trigger room is back at its
    target); `fallback_demand` comes from the stall detection.
    """
    mode = house_mode(settings, inputs)
    outdoor_ok = outdoor_allows(settings, inputs)

    decisions: list[RoomDecision] = []
    for room in rooms:
        state = states.setdefault(room.id, RoomState(room))
        temp = state.temp
        # A manual override only lasts until the next mode transition.
        if state.override_mode is not None and state.override_mode != mode:
            state.override_mode = None
            state.cmd_on = None
            state.confirmed = False
        left_alone = state.override_mode is not None
        base_target = resolve_target(rooms, room)
        # Pre-heating: a room switches to "day" ahead of the schedule
        # (before the day period, or before the planned return).
        state.preheat_minutes = preheat_duration(
            settings, state, base_target, inputs.forecast
        )
        preheating, next_preheat = preheat_window(
            settings, inputs, state.preheat_minutes, mode
        )
        state.next_preheat = next_preheat
        effective_mode = MODE_DAY if preheating else mode
        # Soft overheating: on a surplus, split rooms aim slightly
        # above their day target (day only, when the splits may run).
        if (
            effective_mode == MODE_DAY
            and inputs.solar_bonus_active
            and room.has_splits
            and base_target is not None
        ):
            base_target = base_target + settings.solar_bonus
        target = room_target(settings, effective_mode, base_target)
        controllable = temp is not None and target is not None
        # The splits do not run while pre-heating, unless the room opted
        # in: its splits (the PAC) then do the pre-heating of this room.
        room_allowed = (
            outdoor_ok
            and effective_mode == MODE_DAY
            and (not preheating or room.preheat_with_splits)
        )

        if room.has_splits and controllable and room_allowed and not left_alone:
            # Hysteresis regulation: start below target - hysteresis,
            # stop back at the target.
            if state.splits_on:
                want = temp < target
            else:
                want = temp < target - settings.hysteresis
            # Compressor protection: a non-forced change of state waits
            # for the minimum run/stop time since the last change.
            if (
                want != state.splits_on
                and state.last_change is not None
                and (inputs.now - state.last_change)
                < timedelta(minutes=settings.min_cycle_minutes)
            ):
                want = state.splits_on
        else:
            want = False

        trigger_cold = bool(
            room.boiler_trigger and controllable and temp < target - settings.hysteresis
        )

        decisions.append(
            RoomDecision(
                room_id=room.id,
                mode=mode,
                temp=temp,
                target=target,
                controllable=controllable,
                splits_allowed=room_allowed,
                want_splits=want,
                left_alone=left_alone,
                preheating=preheating,
                preheat_minutes=state.preheat_minutes,
                next_preheat=next_preheat,
                boiler_trigger_cold=trigger_cold,
                source=SOURCE_IDLE,
            )
        )

    # Boiler demand: latched while any trigger room is too cold, released
    # only when all of them are back at their target — and only once the
    # demand debounce (the minimum run/stop time) has expired, so a
    # sensor fluttering across its dead band cannot flip the demand
    # every pass.
    controllable_trigger = [d for d in decisions if d.controllable]
    if any(d.boiler_trigger_cold for d in controllable_trigger):
        trigger_demand = True
    elif inputs.demand_hold_expired and all(
        not d.boiler_trigger_cold and d.temp >= d.target  # type: ignore[operator]
        for d in controllable_trigger
        if states[d.room_id].config.boiler_trigger
    ):
        trigger_demand = False
    demand = trigger_demand or fallback_demand

    # Normal boiler setpoint: the thermostat room's target.
    thermostat = next((r for r in rooms if r.thermostat_room), None)
    if thermostat is not None:
        normal = next((d.target for d in decisions if d.room_id == thermostat.id), None)
    else:
        targets = [d.target for d in decisions if d.target is not None]
        normal = max(targets) if targets else None

    # The house is in "splits mode" during the real day period when the
    # outdoor allows it; outside the day period, only while a room
    # pre-heats with its own splits (per-room opt-in).
    pac_preheat_ids = {
        d.room_id
        for d in decisions
        if d.preheating and d.splits_allowed and states[d.room_id].config.has_splits
    }
    splits_allowed = outdoor_ok and (mode == MODE_DAY or bool(pac_preheat_ids))
    # During a pre-heating the boiler rests only when the thermostat room
    # itself pre-heats with the PAC; otherwise it does the pre-heating,
    # at the thermostat room's day target.
    splits_first = outdoor_ok and (
        mode == MODE_DAY
        or (thermostat is not None and thermostat.id in pac_preheat_ids)
    )
    setpoint: Optional[float]
    if splits_first:
        # Mild day: the splits do the work; the boiler only steps in on
        # a demand from a trigger room (or a stalled split).
        setpoint = (
            settings.boiler_demand_setpoint if demand else settings.idle_setpoint
        )
    elif normal is not None:
        setpoint = (
            max(normal, settings.boiler_demand_setpoint) if demand else normal
        )
    else:
        setpoint = None

    return HouseDecision(
        mode=mode,
        splits_allowed=splits_allowed,
        rooms=decisions,
        boiler_demand=demand,
        trigger_demand=trigger_demand,
        boiler_setpoint=setpoint,
        thermostat_room_target=normal,
        solar_bonus=inputs.solar_bonus_active,
        now=inputs.now,
        production=inputs.production,
        consumption=inputs.consumption,
    )


# Delay before a command that was never followed is reported as
# unresponsive (the switch may legitimately take a while to switch).
UNRESPONSIVE_GRACE = timedelta(minutes=2)

# Learning of the heating rate (°C/h) per room.
LEARNING_ALPHA = 0.3  # weight of the newest heating-rate sample
MIN_SESSION = timedelta(minutes=20)  # shorter sessions are not used
MIN_RISE = 0.3  # °C: a smaller rise is not a heating session
MAX_SESSION = timedelta(hours=8)  # a longer session is finalized anyway
WEATHER_FACTOR_PER_DEGREE = 0.03  # +3 % of pre-heating per colder degree
WEATHER_FACTOR_MIN = 0.7
WEATHER_FACTOR_MAX = 1.8


def detect_manual(
    state: RoomState,
    actual_on: bool,
    now: datetime,
) -> Optional[str]:
    """Watch the switches against the last command.

    Returns "manual" when a split that had followed our command was
    changed by someone else (the room is then left alone until the next
    mode transition), "unresponsive" once when a command was never
    followed, None otherwise.
    """
    if state.cmd_on is None or state.cmd_time is None:
        return None
    if actual_on == state.cmd_on:
        state.confirmed = True
        state.unresponsive_notified = False
        return None
    if now - state.cmd_time < UNRESPONSIVE_GRACE:
        return None
    if state.confirmed:
        # It followed our command, then changed: manual action.
        state.override_mode = state.mode
        state.cmd_on = None
        state.cmd_time = None
        state.confirmed = False
        return "manual"
    if not state.unresponsive_notified:
        state.unresponsive_notified = True
        return "unresponsive"
    return None


def update_room_states(
    house: HouseDecision,
    states: dict[str, RoomState],
    boiler_heating: bool,
) -> None:
    """Fold the decision back into the runtime room states."""
    for decision in house.rooms:
        state = states.get(decision.room_id)
        if state is None:
            continue
        state.mode = decision.mode
        state.target = decision.target
        state.want_splits = decision.want_splits
        if decision.want_splits:
            state.source = SOURCE_SPLITS
        elif (
            boiler_heating
            and decision.controllable
            and decision.temp is not None
            and decision.target is not None
            and decision.temp < decision.target
        ):
            state.source = SOURCE_BOILER_SOURCE
        else:
            state.source = SOURCE_IDLE


def update_fallback(
    state: RoomState,
    decision: RoomDecision,
    settings: Settings,
    now: datetime,
) -> None:
    """Track the progress of a running split; flag the room when it stalls.

    The boiler takes over from a split that has not risen by
    PROGRESS_STEP within the fallback delay while still below its
    target; the flag clears when the target is reached (or the split
    stops).
    """
    active = bool(
        decision.splits_allowed
        and decision.want_splits
        and decision.temp is not None
        and decision.target is not None
        and decision.temp < decision.target
    )
    if not active:
        state.best_temp = None
        state.best_time = None
        state.fallback = False
        state.fallback_notify = False
        return
    assert decision.temp is not None and decision.target is not None
    if state.best_temp is None or decision.temp > state.best_temp + PROGRESS_STEP:
        state.best_temp = decision.temp
        state.best_time = now
    elif (
        not state.fallback
        and state.best_time is not None
        and now - state.best_time >= timedelta(minutes=settings.fallback_minutes)
        and decision.temp < decision.target - settings.hysteresis
    ):
        state.fallback = True


def update_learning(
    state: RoomState,
    decision: RoomDecision,
    now: datetime,
    outdoor_temp: Optional[float],
    boiler_heating: bool,
) -> bool:
    """Learn the heating rate (°C/h) of the room from heating sessions.

    Returns True when a session ended and produced (or updated) a rate:
    the caller persists the learned values.
    """
    temp = decision.temp
    target = decision.target
    heating = state.splits_on or boiler_heating
    active = bool(
        decision.controllable
        and heating
        and temp is not None
        and target is not None
        and temp < target
    )
    if active and temp is not None:
        if state.session is None:
            state.session = {
                "t0": now,
                "temp0": temp,
                "t": now,
                "temp": temp,
                "outdoor": outdoor_temp,
            }
            return False
        state.session["t"] = now
        state.session["temp"] = temp
        if now - state.session["t0"] > MAX_SESSION:
            return finalize_session(state)
        return False
    if state.session is not None:
        return finalize_session(state)
    return False


def finalize_session(state: RoomState) -> bool:
    """Fold the ongoing session into the learned rate. True when learned."""
    session = state.session
    state.session = None
    if not session:
        return False
    hours = (session["t"] - session["t0"]).total_seconds() / 3600
    rise = session["temp"] - session["temp0"]
    if hours * 60 < MIN_SESSION.total_seconds() / 60 or rise < MIN_RISE:
        return False
    sample = min(max(rise / hours, 0.1), 5.0)
    if state.rate is None:
        state.rate = sample
    else:
        state.rate = (1 - LEARNING_ALPHA) * state.rate + LEARNING_ALPHA * sample
    outdoor = session.get("outdoor")
    if outdoor is not None:
        if state.ref_outdoor is None:
            state.ref_outdoor = outdoor
        else:
            state.ref_outdoor = (
                (1 - LEARNING_ALPHA) * state.ref_outdoor + LEARNING_ALPHA * outdoor
            )
    return True
