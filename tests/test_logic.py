"""Logic decision tests, equivalents to YAML automations."""

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

CHANGEOVER_HYSTERESIS = logic.CHANGEOVER_HYSTERESIS
Inputs = logic.Inputs
Settings = logic.Settings
compute = logic.compute
parse_time = logic.parse_time
target_temperature = logic.target_temperature


def settings(**kwargs) -> Settings:
    base = dict(
        outdoor_threshold=3.0,
        day_setpoint=20.0,
        night_setpoint=16.0,
        away_setpoint=12.0,
        solar_setpoint=22.0,
        idle_setpoint=12.0,
        hysteresis=0.3,
        production_threshold=500.0,
        heat_pump_rate=1.0,
        boiler_rate=1.0,
        safety_margin=15,
        day_start=timedelta(hours=7),
        night_start=timedelta(hours=22),
        return_time=timedelta(hours=18),
        solar_start=timedelta(hours=11),
        solar_end=timedelta(hours=15),
    )
    base.update(kwargs)
    return Settings(**base)


def inputs(now, outdoor_temp=10.0, indoor_temp=19.0, production=0.0, **kwargs) -> Inputs:
    base = dict(
        active=True,
        away_mode=False,
        heat_pump_source=False,
        preheating_flag=False,
        demand_flag=False,
    )
    base.update(kwargs)
    return Inputs(
        now=now,
        outdoor_temp=outdoor_temp,
        indoor_temp=indoor_temp,
        production=production,
        **base,
    )


class TestParseTime(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("07:00:00"), timedelta(hours=7))
        self.assertEqual(parse_time("22:30"), timedelta(hours=22, minutes=30))
        self.assertIsNone(parse_time("choux"))
        self.assertIsNone(parse_time(None))


class TestSettingsDefaults(unittest.TestCase):
    """Default values matching the original YAML configuration."""

    def test_default_outdoor_threshold(self):
        self.assertEqual(Settings().outdoor_threshold, 3.0)

    def test_default_idle_setpoint(self):
        self.assertEqual(Settings().idle_setpoint, 12.0)


class TestChangeover(unittest.TestCase):
    """Outdoor threshold with 0.5 °C hysteresis (1st automation)."""

    def test_boiler_to_heat_pump(self):
        s = settings()
        # boiler source: changeover if outdoor_temp > 3.5
        d = compute(s, inputs(datetime(2026, 1, 15, 12), outdoor_temp=3.5))
        self.assertFalse(d.changeover)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), outdoor_temp=3.6))
        self.assertTrue(d.changeover)
        self.assertTrue(d.should_use_heat_pump)

    def test_heat_pump_to_boiler(self):
        s = settings()
        # heat pump source: changeover if outdoor_temp <= 2.5
        d = compute(
            s, inputs(datetime(2026, 1, 15, 12), outdoor_temp=2.5, heat_pump_source=True)
        )
        self.assertTrue(d.changeover)
        self.assertFalse(d.should_use_heat_pump)
        d = compute(
            s, inputs(datetime(2026, 1, 15, 12), outdoor_temp=2.6, heat_pump_source=True)
        )
        self.assertFalse(d.changeover)

    def test_outdoor_temp_unknown(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12), outdoor_temp=None))
        self.assertFalse(d.changeover)
        self.assertEqual(d.should_use_heat_pump, False)


class TestPeriods(unittest.TestCase):
    def test_day_period(self):
        s = settings()
        self.assertTrue(compute(s, inputs(datetime(2026, 1, 15, 7, 0, 0))).in_day_period)
        self.assertFalse(compute(s, inputs(datetime(2026, 1, 15, 6, 59, 59))).in_day_period)
        self.assertFalse(compute(s, inputs(datetime(2026, 1, 15, 22, 0, 0))).in_day_period)
        self.assertTrue(compute(s, inputs(datetime(2026, 1, 15, 21, 59, 59))).in_day_period)

    def test_solar_period(self):
        s = settings()
        # 12:00, inside the 11:00-15:00 window, enough production
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=500.0))
        self.assertTrue(d.in_solar_period)
        # not enough production
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=499.9))
        self.assertFalse(d.in_solar_period)
        # outside the window
        d = compute(s, inputs(datetime(2026, 1, 15, 16), production=3000.0))
        self.assertFalse(d.in_solar_period)
        # at night, even when producing (nothing), no solar period
        d = compute(s, inputs(datetime(2026, 1, 15, 23), production=3000.0))
        self.assertFalse(d.in_solar_period)


class TestTargetSetpoint(unittest.TestCase):
    def test_day_night(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12)))
        self.assertEqual(d.target_setpoint, 20.0)
        d = compute(s, inputs(datetime(2026, 1, 15, 23)))
        self.assertEqual(d.target_setpoint, 16.0)

    def test_solar_max_with_day(self):
        s = settings(solar_setpoint=22.0, day_setpoint=23.5)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=800.0))
        self.assertEqual(d.target_setpoint, 23.5)

    def test_away(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12), away_mode=True))
        self.assertTrue(d.away_mode_active)
        self.assertEqual(d.target_setpoint, 12.0)

    def test_away_preheating_uses_day_setpoint(self):
        s = settings()
        # return 18:00, preheating needed ~ (20-15)/1*60+15 = 315 min
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 13, 0, 0),
                indoor_temp=15.0,
                away_mode=True,
            ),
        )
        self.assertTrue(d.preheating)
        self.assertEqual(d.target_setpoint, 20.0)


class TestPreheating(unittest.TestCase):
    def test_preheating_minutes(self):
        s = settings(heat_pump_rate=2.0, safety_margin=15)
        d = compute(
            s,
            inputs(datetime(2026, 1, 15, 12), indoor_temp=17.0, heat_pump_source=True),
        )
        # difference 3 °C / 2 °C/h * 60 = 90 min + 15 = 105
        self.assertEqual(d.preheating_minutes, 105)

    def test_zero_rate(self):
        s = settings(heat_pump_rate=0.0, safety_margin=20)
        d = compute(
            s,
            inputs(datetime(2026, 1, 15, 12), indoor_temp=17.0, heat_pump_source=True),
        )
        self.assertEqual(d.preheating_minutes, 20)

    def test_morning_preheating_start(self):
        s = settings(safety_margin=15)
        # (20-17)/1*60 + 15 = 195 min -> preheating from 03:45
        d = compute(s, inputs(datetime(2026, 1, 15, 3, 44, 0), indoor_temp=17.0))
        self.assertFalse(d.preheating)
        d = compute(s, inputs(datetime(2026, 1, 15, 3, 45, 0), indoor_temp=17.0))
        self.assertTrue(d.preheating)

    def test_preheating_persists_outside_day_period(self):
        s = settings()
        # flag raised, we stay in preheating as long as the day period has not started
        d = compute(s, inputs(datetime(2026, 1, 15, 5, 0, 0), preheating_flag=True))
        self.assertTrue(d.preheating)
        # flag raised but day period started: no more preheating
        d = compute(s, inputs(datetime(2026, 1, 15, 12, 0, 0), preheating_flag=True))
        self.assertFalse(d.preheating)

    def test_return_preheating_start(self):
        s = settings()
        # return 18:00, preheating 195 min -> 14:45
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 14, 44, 0),
                indoor_temp=17.0,
                away_mode=True,
            ),
        )
        self.assertFalse(d.preheating)
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 14, 45, 0),
                indoor_temp=17.0,
                away_mode=True,
            ),
        )
        self.assertTrue(d.preheating)

    def test_return_passed(self):
        s = settings()
        d = compute(
            s,
            inputs(datetime(2026, 1, 15, 18, 30, 0), away_mode=True),
        )
        self.assertTrue(d.return_passed)
        self.assertFalse(d.away_mode_active)
        # as in YAML: away mode must be turned off, day setpoint
        self.assertEqual(d.target_setpoint, 20.0)


class TestHeatingDemand(unittest.TestCase):
    def test_hysteresis(self):
        s = settings(hysteresis=0.3)
        # without demand: starts below 19.7 (20 - 0.3)
        self.assertFalse(
            compute(s, inputs(datetime(2026, 1, 15, 12), indoor_temp=19.7)).heating_needed
        )
        self.assertTrue(
            compute(s, inputs(datetime(2026, 1, 15, 12), indoor_temp=19.6)).heating_needed
        )
        # with demand: stops at 20.3 (setpoint + hysteresis)
        self.assertTrue(
            compute(
                s,
                inputs(datetime(2026, 1, 15, 12), indoor_temp=20.29, demand_flag=True),
            ).heating_needed
        )
        self.assertFalse(
            compute(
                s,
                inputs(datetime(2026, 1, 15, 12), indoor_temp=20.31, demand_flag=True),
            ).heating_needed
        )


class TestNoHeatingDemand(unittest.TestCase):
    """Tests for when there is no heating need (heating_needed = False)."""

    def test_no_need_above_setpoint_with_hysteresis(self):
        """Without demand flag: heating stops above setpoint + hysteresis."""
        s = settings(hysteresis=0.3)
        # day_setpoint=20, hysteresis=0.3 -> demand stops at 20.3
        d = compute(s, inputs(datetime(2026, 1, 15, 12), indoor_temp=20.31))
        self.assertFalse(d.heating_needed)
        self.assertEqual(d.target_setpoint, 20.0)

    def test_no_need_above_setpoint_with_demand(self):
        """With demand flag: heating stops at setpoint + hysteresis."""
        s = settings(hysteresis=0.3)
        # with demand: stops at 20.3 (setpoint + hysteresis)
        d = compute(
            s,
            inputs(datetime(2026, 1, 15, 12), indoor_temp=20.31, demand_flag=True),
        )
        self.assertFalse(d.heating_needed)
        self.assertEqual(d.target_setpoint, 20.0)

    def test_no_need_below_setpoint_negative_hysteresis(self):
        """Without demand flag below setpoint - hysteresis: demand = True."""
        s = settings(hysteresis=0.3)
        # below setpoint - hysteresis -> demand=True
        self.assertTrue(
            compute(s, inputs(datetime(2026, 1, 15, 12), indoor_temp=19.6)).heating_needed
        )
        # well below -> demand=True with margin
        self.assertTrue(
            compute(s, inputs(datetime(2026, 1, 15, 12), indoor_temp=19.0)).heating_needed
        )

    def test_target_setpoint_away_when_no_need(self):
        """When no heating need in away mode, target_setpoint should be 12.0."""
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12), away_mode=True))
        self.assertTrue(d.away_mode_active)
        self.assertEqual(d.target_setpoint, 12.0)
        self.assertFalse(d.heating_needed)


class TestTargetTemperature(unittest.TestCase):
    def test_rounding_to_step(self):
        self.assertEqual(target_temperature(21.0), 21.0)
        self.assertEqual(target_temperature(21.3, step=0.5), 21.5)
        self.assertEqual(target_temperature(21.24, step=0.5), 21.0)
        self.assertEqual(target_temperature(21.7, step=1.0), 22.0)

    def test_bounds(self):
        self.assertEqual(target_temperature(2.0, minimum=5.0), 5.0)
        self.assertEqual(target_temperature(35.0, maximum=30.0), 30.0)

    def test_invalid_step(self):
        self.assertEqual(target_temperature(21.0, step=0.0), 21.0)


if __name__ == "__main__":
    unittest.main()