"""Tests of the shared primitives of logic.py (no homeassistant needed)."""

import importlib.util
import os
import sys
import unittest
from datetime import timedelta

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
        self.assertEqual(s.day_start, timedelta(hours=7))
        self.assertEqual(s.night_start, timedelta(hours=22))


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
