"""Room model of the heat controller.

A room is described by a config sub-entry (`SUBENTRY_ROOM`): name, room
temperature sensor, day target (or the room it follows), whether it may
request the boiler, whether the boiler thermostat lives in it, and the
splits that heat it (power switches, climate entities, fans).

This module is voluntarily independent of Home Assistant: the room
configuration is built from duck-typed sub-entries so the decision logic
stays unit-testable. The hybrid regulation built on top of the rooms lives
in `rooms_logic`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .const import (
    CONF_ROOM_CLIMATES,
    CONF_ROOM_FANS,
    CONF_ROOM_FOLLOW_ROOM,
    CONF_ROOM_NAME,
    CONF_ROOM_SWITCHES,
    CONF_ROOM_TARGET,
    CONF_ROOM_TEMP_SENSOR,
    CONF_ROOM_THERMOSTAT_ROOM,
    SUBENTRY_ROOM,
)


@dataclass(frozen=True)
class RoomConfig:
    """Static configuration of a room (from a config sub-entry)."""

    id: str
    name: str
    sensor: str
    target: Optional[float] = None
    follow_room: Optional[str] = None
    thermostat_room: bool = False
    switches: tuple[str, ...] = field(default_factory=tuple)
    climates: tuple[str, ...] = field(default_factory=tuple)
    fans: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_splits(self) -> bool:
        return bool(self.switches)

    @property
    def boiler_trigger(self) -> bool:
        """A room without splits has only the boiler to heat it: only
        those rooms may request it on mild days (the split rooms fall
        back to the boiler automatically after the fallback delay)."""
        return not self.has_splits


def _target(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entities(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def room_from_subentry(subentry: Any) -> Optional[RoomConfig]:
    """Build a room from a config sub-entry (None if malformed)."""
    data: Mapping[str, Any] = getattr(subentry, "data", None) or {}
    sensor = str(data.get(CONF_ROOM_TEMP_SENSOR) or "")
    if not sensor:
        return None
    return RoomConfig(
        id=str(getattr(subentry, "subentry_id", "")),
        name=str(data.get(CONF_ROOM_NAME) or getattr(subentry, "title", "") or ""),
        sensor=sensor,
        target=_target(data.get(CONF_ROOM_TARGET)),
        follow_room=(str(data[CONF_ROOM_FOLLOW_ROOM]) if data.get(CONF_ROOM_FOLLOW_ROOM) else None),
        thermostat_room=bool(data.get(CONF_ROOM_THERMOSTAT_ROOM, False)),
        switches=_entities(data.get(CONF_ROOM_SWITCHES)),
        climates=_entities(data.get(CONF_ROOM_CLIMATES)),
        fans=_entities(data.get(CONF_ROOM_FANS)),
    )


def rooms_from_subentries(subentries: Any) -> list[RoomConfig]:
    """All the room sub-entries of a config entry, in a stable order."""
    rooms = []
    for subentry in (subentries or {}).values():
        if getattr(subentry, "subentry_type", None) != SUBENTRY_ROOM:
            continue
        room = room_from_subentry(subentry)
        if room is not None:
            rooms.append(room)
    return rooms


def rooms_signature(rooms: list[RoomConfig]) -> tuple:
    """Comparable snapshot of the room configuration (update listener)."""
    return tuple(
        (
            room.id,
            room.name,
            room.sensor,
            room.target,
            room.follow_room,
            room.boiler_trigger,
            room.thermostat_room,
            room.switches,
            room.climates,
            room.fans,
        )
        for room in rooms
    )
