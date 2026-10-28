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
setpoint while there is a demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .logic import SOURCE_AUTO, SOURCE_BOILER, SOURCE_HEAT_PUMP, Settings, at_time
from .rooms import RoomConfig

MODE_DAY = "day"
MODE_NIGHT = "night"
MODE_AWAY = "away"

SOURCE_IDLE = "idle"
SOURCE_SPLITS = "splits"
SOURCE_BOILER_SOURCE = "boiler"


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


@dataclass(frozen=True)
class HouseInputs:
    """Instantaneous house state used for the hybrid decision."""

    now: datetime
    outdoor_temp: Optional[float]  # None = unknown -> splits still allowed
    away_mode: bool
    forced_source: str = SOURCE_AUTO


@dataclass
class RoomDecision:
    """Decision for one room."""

    room_id: str
    mode: str
    temp: Optional[float]
    target: Optional[float]
    controllable: bool  # sensor and target known
    splits_allowed: bool  # splits may run (day + outdoor + override)
    want_splits: bool
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
    """Global mode: away wins, then the day/night clock."""
    if inputs.away_mode:
        return MODE_AWAY
    now = inputs.now
    if at_time(now, settings.day_start) <= now < at_time(now, settings.night_start):
        return MODE_DAY
    return MODE_NIGHT


def splits_allowed_now(
    settings: Settings, inputs: HouseInputs, mode: str
) -> bool:
    """Whether the splits may run: mild day, or forced heat pump."""
    if inputs.forced_source == SOURCE_BOILER:
        return False
    if mode != MODE_DAY:
        return False  # splits stay off at night and away
    if inputs.forced_source == SOURCE_HEAT_PUMP:
        return True
    # An unknown outdoor temperature does not block the splits.
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
    allowed = splits_allowed_now(settings, inputs, mode)

    decisions: list[RoomDecision] = []
    for room in rooms:
        state = states.setdefault(room.id, RoomState(room))
        temp = state.temp
        base_target = resolve_target(rooms, room)
        target = room_target(settings, mode, base_target)
        controllable = temp is not None and target is not None

        if room.has_splits and controllable and allowed:
            # Hysteresis regulation: start below target - hysteresis,
            # stop back at the target.
            if state.splits_on:
                want = temp < target
            else:
                want = temp < target - settings.hysteresis
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
                splits_allowed=allowed,
                want_splits=want,
                boiler_trigger_cold=trigger_cold,
                source=SOURCE_IDLE,
            )
        )

    # Boiler demand: latched while any trigger room is too cold, released
    # only when all of them are back at their target.
    controllable_trigger = [d for d in decisions if d.controllable]
    if any(d.boiler_trigger_cold for d in controllable_trigger):
        trigger_demand = True
    elif all(
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

    if allowed and mode == MODE_DAY:
        # Mild day: the splits do the work; the boiler only steps in on
        # a demand from a trigger room (or a stalled split).
        setpoint: Optional[float] = (
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
        splits_allowed=allowed,
        rooms=decisions,
        boiler_demand=demand,
        trigger_demand=trigger_demand,
        boiler_setpoint=setpoint,
        thermostat_room_target=normal,
    )


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
