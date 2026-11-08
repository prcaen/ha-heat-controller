"""Tests of the shared primitives of logic.py (no homeassistant needed)."""

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta

# logic.py does not depend on Home Assistant: direct loading by path
# to avoid importing the package (which depends on homeassistant).
_LOGIC_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "heat_controller",
    "logic.py",
)
_spec = importlib.util.spec_from_file_location("heat_controller_logic", _LOGIC_PATH)
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic
_spec.loader.exec_module(logic)

parse_time = logic.parse_time
target_temperature = logic.target_temperature
at_time = logic.at_time
time_of_day = logic.time_of_day
Settings = logic.Settings


class TestFormats(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("07:00"), timedelta(hours=7))
        self.assertEqual(parse_time("07:00:30"), timedelta(hours=7, seconds=30))
        self.assertIsNone(parse_time("nonsense"))
        self.assertIsNone(parse_time(None))

    def test_midnight_is_valid(self):
        # timedelta(0) is falsy: midnight must still be honored.
        self.assertEqual(parse_time("00:00:00"), timedelta(0))

    def test_at_time_and_time_of_day(self):
        import datetime as dt

        now = dt.datetime(2026, 10, 21, 12, 34, 56)
        moment = at_time(now, timedelta(hours=18, minutes=30))
        self.assertEqual((moment.hour, moment.minute), (18, 30))
        tod = time_of_day(timedelta(hours=6, minutes=15))
        self.assertEqual((tod.hour, tod.minute), (6, 15))


class TestSettingsDefaults(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertEqual(s.outdoor_threshold, 3.0)
        self.assertEqual(s.night_setpoint, 16.0)
        self.assertEqual(s.away_setpoint, 12.0)
        self.assertEqual(s.idle_setpoint, 12.0)
        self.assertEqual(s.frost_setpoint, 7.0)
        self.assertEqual(s.hysteresis, 0.3)
        self.assertEqual(s.min_cycle_minutes, 5.0)
        self.assertEqual(s.split_offset, 2.0)
        self.assertEqual(s.boiler_demand_setpoint, 21.0)
        for day in logic.WEEKDAYS:
            self.assertEqual(getattr(s, f"day_start_{day}"), timedelta(hours=7))
            self.assertEqual(getattr(s, f"day_end_{day}"), timedelta(hours=22))


class TestSchedule(unittest.TestCase):
    """Weekly day period: in_day_period and next_day_start (2026-10-21 = Wednesday)."""

    def test_in_day_period_default_window(self):
        s = Settings()
        self.assertTrue(logic.in_day_period(s, datetime(2026, 10, 21, 12, 0)))
        self.assertTrue(logic.in_day_period(s, datetime(2026, 10, 21, 7, 0)))  # start inclusive
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 21, 22, 0)))  # end exclusive
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 21, 3, 0)))

    def test_in_day_period_per_weekday(self):
        s = Settings(day_start_sat=timedelta(hours=9), day_end_sat=timedelta(hours=23))
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 24, 8, 0)))  # Saturday
        self.assertTrue(logic.in_day_period(s, datetime(2026, 10, 24, 9, 0)))
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 24, 23, 0)))
        self.assertTrue(logic.in_day_period(s, datetime(2026, 10, 25, 10, 0)))  # Sunday: default
        self.assertTrue(logic.in_day_period(s, datetime(2026, 10, 21, 8, 0)))  # Wednesday: default

    def test_inverted_window_is_always_night(self):
        s = Settings(day_start_wed=timedelta(hours=23), day_end_wed=timedelta(hours=22))
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 21, 12, 0)))
        self.assertFalse(logic.in_day_period(s, datetime(2026, 10, 21, 23, 30)))

    def test_next_day_start_same_day(self):
        s = Settings()
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 21, 5, 0)),
            datetime(2026, 10, 21, 7, 0),
        )

    def test_next_day_start_rolls_to_the_next_day(self):
        s = Settings()
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 21, 23, 0)),
            datetime(2026, 10, 22, 7, 0),
        )

    def test_next_day_start_uses_the_weekday_window(self):
        s = Settings(day_start_sat=timedelta(hours=9), day_end_sat=timedelta(hours=23))
        # Thursday 23:30 -> the next window is Saturday 09:00 (Friday 07:00 first).
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 22, 23, 30)),
            datetime(2026, 10, 23, 7, 0),
        )
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 23, 23, 30)),
            datetime(2026, 10, 24, 9, 0),
        )

    def test_next_day_start_skips_inverted_days(self):
        s = Settings(day_start_thu=timedelta(hours=23), day_end_thu=timedelta(hours=22))
        # Wednesday night -> Thursday is inverted, Friday 07:00 is next.
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 21, 23, 0)),
            datetime(2026, 10, 23, 7, 0),
        )

    def test_next_day_start_wraps_the_week(self):
        s = Settings(day_start_sun=timedelta(hours=10), day_end_sun=timedelta(hours=20))
        # Sunday 21:00 -> the next window is Monday 07:00.
        self.assertEqual(
            logic.next_day_start(s, datetime(2026, 10, 25, 21, 0)),
            datetime(2026, 10, 26, 7, 0),
        )

    def test_next_day_start_none_without_any_valid_window(self):
        kwargs = {}
        for day in logic.WEEKDAYS:
            kwargs[f"day_start_{day}"] = timedelta(hours=23)
            kwargs[f"day_end_{day}"] = timedelta(hours=22)
        s = Settings(**kwargs)
        self.assertIsNone(logic.next_day_start(s, datetime(2026, 10, 21, 12, 0)))


class TestTargetTemperature(unittest.TestCase):
    def test_rounding_to_step(self):
        self.assertEqual(target_temperature(20.24), 20.0)
        self.assertEqual(target_temperature(20.25), 20.5)
        self.assertEqual(target_temperature(20.26), 20.5)

    def test_bounds(self):
        self.assertEqual(target_temperature(2.0), 5.0)  # clamped to min
        self.assertEqual(target_temperature(35.0), 30.0)  # clamped to max

    def test_invalid_step(self):
        self.assertEqual(target_temperature(20.3, step=0), 20.5)
        self.assertEqual(target_temperature(20.3, step=None), 20.5)

    def test_swapped_bounds(self):
        self.assertEqual(target_temperature(20.3, minimum=30.0, maximum=5.0), 20.5)


if __name__ == "__main__":
    unittest.main()
