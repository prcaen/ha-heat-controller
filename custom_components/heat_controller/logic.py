"""Heat Controller shared primitives.

Voluntarily independent of Home Assistant for unit testing. This module
holds the settings, the decision dataclass consumed by the entities and
small helpers shared with `rooms_logic` (time parsing, setpoint rounding).

The regulation itself lives in `rooms_logic` (multi-room hybrid mode).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

# Manual heat source override (select entity).
SOURCE_AUTO = "auto"
SOURCE_HEAT_PUMP = "heat_pump"
SOURCE_BOILER = "boiler"
SOURCE_OPTIONS = (SOURCE_AUTO, SOURCE_HEAT_PUMP, SOURCE_BOILER)


def round_half_up(value: float) -> int:
    """Round to the nearest integer, 0.5 rounds up (like Jinja |round(0))."""
    return math.floor(value + 0.5)


def parse_time(value: object) -> Optional[timedelta]:
    """Convert "HH:MM" or "HH:MM:SS" to a duration since midnight."""
    if value is None:
        return None
    parts = str(value).split(":")
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def at_time(now: datetime, since_midnight: timedelta) -> datetime:
    """The datetime of day "now" at the given time (duration since midnight)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + since_midnight


def time_of_day(since_midnight: timedelta) -> time:
    """The time of day corresponding to a duration since midnight."""
    return (datetime.min + since_midnight).time()


def day_period_bounds(settings: Settings, weekday: int) -> tuple[timedelta, timedelta]:
    """(start, end) of the day period: week (Mon-Fri) or weekend (0 = Monday)."""
    if weekday < 5:
        return settings.day_start_week, settings.day_end_week
    return settings.day_start_weekend, settings.day_end_weekend


def in_day_period(settings: Settings, now: datetime) -> bool:
    """Whether `now` falls inside the day period of its weekday.

    Slots never cross midnight: a start at or after the end means an empty
    window, so the whole day is night.
    """
    start, end = day_period_bounds(settings, now.weekday())
    if start >= end:
        return False
    since_midnight = now - at_time(now, timedelta(0))
    return start <= since_midnight < end


def next_day_start(settings: Settings, now: datetime) -> Optional[datetime]:
    """The next instant at which a day period starts, None if none within a week."""
    midnight = at_time(now, timedelta(0))
    for offset in range(8):  # 8 days cover a full week
        candidate_day = now + timedelta(days=offset)
        start, end = day_period_bounds(settings, candidate_day.weekday())
        if start >= end:
            continue
        candidate = midnight + timedelta(days=offset) + start
        if candidate > now:
            return candidate
    return None


@dataclass(frozen=True)
class Settings:
    """Settings, already validated and converted."""

    outdoor_threshold: float = 3.0
    night_setpoint: float = 16.0
    away_setpoint: float = 12.0
    idle_setpoint: float = 12.0
    frost_setpoint: float = 7.0
    hysteresis: float = 0.3
    production_threshold: float = 500.0
    # Schedule: one day period for the week (Mon-Fri), one for the weekend
    # (slots never cross midnight).
    day_start_week: timedelta = timedelta(hours=7)
    day_end_week: timedelta = timedelta(hours=22)
    day_start_weekend: timedelta = timedelta(hours=7)
    day_end_weekend: timedelta = timedelta(hours=22)
    return_time: timedelta = timedelta(hours=18)
    # Hybrid (multi-room) regulation.
    split_offset: float = 2.0
    boiler_demand_setpoint: float = 21.0
    split_fan_preset: str = "auto"
    split_fan_oscillating: bool = True
    solar_bonus: float = 0.5
    surplus_threshold: float = 500.0
    fallback_minutes: float = 120.0
    min_cycle_minutes: float = 5.0
    initial_preheat_minutes: float = 60.0
    max_preheat_minutes: float = 180.0


@dataclass(frozen=True)
class Decision:
    """Global decision, feeding the existing entities.

    Built from the multi-room house decision by the controller.
    """

    in_day_period: bool
    in_solar_period: bool
    preheating_minutes: int  # estimated preheating duration (min)
    return_passed: bool  # return time exceeded in away mode
    away_mode_active: bool
    preheating: bool
    target_setpoint: float
    heating_needed: bool
    should_use_heat_pump: bool
    changeover: bool  # the source mode must change now
    frost_protection: bool  # a room is at or below the frost setpoint


def target_temperature(
    setpoint: float,
    step: float = 0.5,
    minimum: float = 5.0,
    maximum: float = 30.0,
) -> float:
    """Round the setpoint to the device step then clamp to [min, max].

    Equivalent of the template: ((setpoint / step) | round(0) * step),
    clamped, rounded to 1 decimal.
    """
    device_step = step if step and step > 0 else 0.5
    low = minimum
    high = maximum
    if high < low:
        low, high = high, low
    target = round_half_up(setpoint / device_step) * device_step
    target = min(max(target, low), high)
    return round(target, 1)
