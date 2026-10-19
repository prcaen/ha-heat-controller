"""Heat Controller decision logic.

Voluntarily independent of Home Assistant for unit testing.
It faithfully reproduces the original YAML automations:

- « Heating - PAC/Boiler changeover»: heat pump source change according to
  outdoor temperature, with 0.5 °C hysteresis around the threshold.
- « Heating - Day/Night/Away with anticipation»: target setpoint according to
  the period (day, night, away, solar), anticipated preheating proportional
  to the temperature difference and the active source's rise rate, heating
  demand hysteresis.

The solar production used for the solar period is the actual instantaneous
power produced (central power sensor, e.g. Solenso), instead of a forecast.

Added on top of the YAML automations: manual heat source override, frost
protection (minimum setpoint), and an explicit absence end date/time (the
away mode ends and preheats relative to it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

# Hysteresis (°C) around the outdoor changeover threshold.
CHANGEOVER_HYSTERESIS = 0.5

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


@dataclass(frozen=True)
class Settings:
    """Settings, already validated and converted."""

    outdoor_threshold: float = 3.0
    day_setpoint: float = 20.0
    night_setpoint: float = 16.0
    away_setpoint: float = 12.0
    solar_setpoint: float = 22.0
    idle_setpoint: float = 12.0
    frost_setpoint: float = 7.0
    hysteresis: float = 0.3
    production_threshold: float = 500.0
    heat_pump_rate: float = 1.0
    boiler_rate: float = 1.0
    safety_margin: int = 15
    day_start: timedelta = timedelta(hours=7)
    night_start: timedelta = timedelta(hours=22)
    return_time: timedelta = timedelta(hours=18)
    solar_start: timedelta = timedelta(hours=11)
    solar_end: timedelta = timedelta(hours=15)


@dataclass(frozen=True)
class Inputs:
    """Instantaneous state used for the decision."""

    now: datetime
    outdoor_temp: Optional[float]  # None = unknown -> no changeover
    indoor_temp: float
    production: float  # W produced at the moment
    active: bool = True
    away_mode: bool = False
    heat_pump_source: bool = False
    preheating_flag: bool = False
    demand_flag: bool = False
    forced_source: str = SOURCE_AUTO
    away_until: Optional[datetime] = None


@dataclass(frozen=True)
class Decision:
    """Decision calculated at a given instant."""

    in_day_period: bool
    in_solar_period: bool
    preheating_minutes: int  # estimated preheating duration (min)
    return_passed: bool  # return time exceeded in away mode
    away_mode_active: bool
    preheating: bool
    target_setpoint: float
    heating_needed: bool
    should_use_heat_pump: bool
    changeover: bool  # the source must change now
    frost_protection: bool  # indoor temperature at or below the frost setpoint


def compute(settings: Settings, inputs: Inputs) -> Decision:
    """Compute the full decision, equivalent to both automations."""
    now = inputs.now

    # --- Periods day / night / solar (cf. 2nd automation) ------------------
    day_dt = at_time(now, settings.day_start)
    night_dt = at_time(now, settings.night_start)
    in_day_period = day_dt <= now < night_dt

    solar_start = at_time(now, settings.solar_start)
    solar_end = at_time(now, settings.solar_end)
    in_solar_period = (
        in_day_period
        and solar_start <= now < solar_end
        and inputs.production >= settings.production_threshold
    )

    # --- Anticipation: duration needed to reach the target setpoint --------
    rate = settings.heat_pump_rate if inputs.heat_pump_source else settings.boiler_rate
    diff = max(settings.day_setpoint - inputs.indoor_temp, 0.0)
    if rate > 0:
        preheating_minutes = (
            round_half_up(diff / rate * 60) + settings.safety_margin
        )
    else:
        preheating_minutes = settings.safety_margin
    minutes = timedelta(minutes=preheating_minutes)

    # --- Absence and return -------------------------------------------------
    # An explicit absence end (date + time) takes precedence over the daily
    # return time.
    return_dt = at_time(now, settings.return_time)
    away_end = inputs.away_until if inputs.away_until is not None else return_dt
    return_passed = inputs.away_mode and now >= away_end
    away_mode_active = inputs.away_mode and not return_passed

    # --- Preheating ---------------------------------------------------------
    next_day = day_dt if now < day_dt else day_dt + timedelta(days=1)
    morning_preheating_start = (
        not away_mode_active
        and not in_day_period
        and now >= next_day - minutes
    )
    return_preheating_start = away_mode_active and now >= away_end - minutes
    preheating_ongoing = inputs.preheating_flag and not return_passed
    preheating = morning_preheating_start or return_preheating_start or (
        preheating_ongoing and (away_mode_active or not in_day_period)
    )

    # --- Target setpoint ----------------------------------------------------
    if preheating:
        target_setpoint = settings.day_setpoint
    elif away_mode_active:
        target_setpoint = settings.away_setpoint
    elif in_solar_period:
        target_setpoint = max(settings.solar_setpoint, settings.day_setpoint)
    elif in_day_period:
        target_setpoint = settings.day_setpoint
    else:
        target_setpoint = settings.night_setpoint

    # --- Frost protection ---------------------------------------------------
    # Never let the indoor temperature fall below the frost setpoint,
    # whatever the schedule, away mode or low setpoints say.
    frost_protection = inputs.indoor_temp <= settings.frost_setpoint
    target_setpoint = max(target_setpoint, settings.frost_setpoint)

    # --- Heating demand with hysteresis ------------------------------------
    if frost_protection:
        heating_needed = True
    else:
        heating_needed = (
            inputs.demand_flag
            and inputs.indoor_temp < target_setpoint + settings.hysteresis
        ) or (
            not inputs.demand_flag
            and inputs.indoor_temp < target_setpoint - settings.hysteresis
        )

    # --- PAC / Boiler changeover (cf. 1st automation) -----------------------
    if inputs.forced_source == SOURCE_HEAT_PUMP:
        # Manual override: the heat pump is forced.
        should_use_heat_pump = True
    elif inputs.forced_source == SOURCE_BOILER:
        # Manual override: the boiler is forced.
        should_use_heat_pump = False
    elif inputs.outdoor_temp is None:
        # Outdoor temperature unavailable: keep the current source.
        should_use_heat_pump = inputs.heat_pump_source
    else:
        threshold = settings.outdoor_threshold + (
            -CHANGEOVER_HYSTERESIS if inputs.heat_pump_source else CHANGEOVER_HYSTERESIS
        )
        should_use_heat_pump = inputs.outdoor_temp > threshold
    changeover = should_use_heat_pump != inputs.heat_pump_source

    return Decision(
        in_day_period=in_day_period,
        in_solar_period=in_solar_period,
        preheating_minutes=preheating_minutes,
        return_passed=return_passed,
        away_mode_active=away_mode_active,
        preheating=preheating,
        target_setpoint=target_setpoint,
        heating_needed=heating_needed,
        should_use_heat_pump=should_use_heat_pump,
        changeover=changeover,
        frost_protection=frost_protection,
    )


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