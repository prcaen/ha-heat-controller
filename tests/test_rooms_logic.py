"""Hybrid multi-room decision tests (no homeassistant needed)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from pure_loader import load_pure, unload_pure

logic = load_pure("logic")
rooms_logic = load_pure("rooms_logic")
rooms_module = load_pure("rooms")

Settings = logic.Settings
RoomConfig = rooms_module.RoomConfig
RoomState = rooms_logic.RoomState
HouseInputs = rooms_logic.HouseInputs
compute_house = rooms_logic.compute_house
update_fallback = rooms_logic.update_fallback
detect_manual = rooms_logic.detect_manual
update_learning = rooms_logic.update_learning

NOW = datetime(2026, 10, 21, 12, 0, 0)  # a Tuesday, within the day period


def settings(**kwargs) -> Settings:
    base = dict(
        outdoor_threshold=3.0,
        night_setpoint=16.0,
        away_setpoint=12.0,
        idle_setpoint=12.0,
        frost_setpoint=7.0,
        hysteresis=0.3,
        split_offset=2.0,
        boiler_demand_setpoint=21.0,
        day_start=timedelta(hours=7),
        night_start=timedelta(hours=22),
    )
    base.update(kwargs)
    return Settings(**base)


def room(room_id: str, name: str, sensor: str, **kwargs) -> RoomConfig:
    return RoomConfig(id=room_id, name=name, sensor=sensor, **kwargs)


def house_inputs(**kwargs) -> HouseInputs:
    base = dict(now=NOW, outdoor_temp=8.0, away_mode=False, forced_source="auto")
    base.update(kwargs)
    return HouseInputs(**base)


def states_of(rooms, temps):
    """Room states keyed by id, with the given temperatures."""
    return {
        r.id: RoomState(r, temp=temps.get(r.id))
        for r in rooms
    }


class TestHouseMode(unittest.TestCase):
    def test_day_then_night_then_away(self):
        s = settings()
        self.assertEqual(rooms_logic.house_mode(s, house_inputs()), "day")
        night = house_inputs(now=datetime(2026, 10, 21, 23, 0))
        self.assertEqual(rooms_logic.house_mode(s, night), "night")
        away = house_inputs(away_mode=True)
        self.assertEqual(rooms_logic.house_mode(s, away), "away")


class TestSplitsAllowed(unittest.TestCase):
    def test_mild_day(self):
        house = compute_house(settings(), house_inputs(), [], {})
        self.assertTrue(house.splits_allowed)

    def test_cold_outside(self):
        house = compute_house(
            settings(), house_inputs(outdoor_temp=1.0), [], {}
        )
        self.assertFalse(house.splits_allowed)

    def test_unknown_outdoor_still_allowed(self):
        house = compute_house(settings(), house_inputs(outdoor_temp=None), [], {})
        self.assertTrue(house.splits_allowed)

    def test_night_and_away_block_the_splits(self):
        night = house_inputs(now=datetime(2026, 10, 21, 23, 0))
        self.assertFalse(
            compute_house(settings(), night, [], {}).splits_allowed
        )
        away = house_inputs(away_mode=True)
        self.assertFalse(
            compute_house(settings(), away, [], {}).splits_allowed
        )

    def test_manual_override(self):
        rooms = [room("s1", "Salon", "sensor.s1", target=19.5, switches=("sw1",), climates=("cl1",))]
        forced_pump = house_inputs(outdoor_temp=1.0, forced_source="heat_pump")
        self.assertTrue(
            compute_house(settings(), forced_pump, rooms, states_of(rooms, {"s1": 19.0})).splits_allowed
        )
        forced_boiler = house_inputs(forced_source="boiler")
        self.assertFalse(
            compute_house(settings(), forced_boiler, rooms, states_of(rooms, {"s1": 19.0})).splits_allowed
        )


class TestRoomRegulation(unittest.TestCase):
    def setUp(self):
        self.rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5,
                 thermostat_room=True, switches=("sw1", "sw2"),
                 climates=("cl1", "cl2"), fans=("f1", "f2")),
            room("cleo", "Chambre de Cleo", "sensor.cleo", target=18.0,
                 switches=("sw3",), climates=("cl3",)),
        ]

    def test_split_starts_below_target_minus_hysteresis(self):
        states = states_of(self.rooms, {"salon": 19.0, "cleo": 17.8})
        states["cleo"].splits_on = False
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertFalse(house.room("cleo").want_splits)  # 17.8 > 18 - 0.3
        states["cleo"].temp = 17.5
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertTrue(house.room("cleo").want_splits)

    def test_split_stops_back_at_target(self):
        states = states_of(self.rooms, {"salon": 19.0, "cleo": 18.0})
        states["cleo"].splits_on = True
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertFalse(house.room("cleo").want_splits)  # at target: stop
        states["cleo"].temp = 17.9
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertTrue(house.room("cleo").want_splits)  # still on: keep heating

    def test_follow_room_target(self):
        rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5),
            room("cuisine", "Cuisine", "sensor.cuisine", follow_room="salon"),
        ]
        states = states_of(rooms, {"salon": 19.0, "cuisine": 18.0})
        house = compute_house(settings(), house_inputs(), rooms, states)
        self.assertEqual(house.room("cuisine").target, 19.5)

    def test_sensor_unavailable_room_is_ignored(self):
        states = states_of(self.rooms, {"salon": 19.0})
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        cleo = house.room("cleo")
        self.assertFalse(cleo.controllable)
        self.assertFalse(cleo.want_splits)


class TestBoiler(unittest.TestCase):
    def setUp(self):
        self.rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5, thermostat_room=True,
                 switches=("sw1",), climates=("cl1",)),
            room("cuisine", "Cuisine", "sensor.cuisine", follow_room="salon"),
            room("bain", "Salle de bain", "sensor.bain", follow_room="salon"),
        ]

    def test_mild_day_boiler_at_rest(self):
        states = states_of(self.rooms, {"salon": 19.0, "cuisine": 19.5, "bain": 19.5})
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertFalse(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 12.0)  # idle/rest setpoint

    def test_trigger_room_raises_the_boiler_until_all_recovered(self):
        states = states_of(self.rooms, {"salon": 19.0, "cuisine": 19.0, "bain": 18.5})
        house = compute_house(settings(), house_inputs(), self.rooms, states)
        self.assertTrue(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 21.0)
        self.assertTrue(house.trigger_demand)
        # The demand latches: cuisine warms up but bain has not.
        states["bain"].temp = 18.9
        states["cuisine"].temp = 19.4
        house = compute_house(
            settings(), house_inputs(), self.rooms, states, trigger_demand=True
        )
        self.assertTrue(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 21.0)
        # Every trigger room back at target: the boiler rests again.
        states["bain"].temp = 19.5
        states["cuisine"].temp = 19.5
        house = compute_house(
            settings(), house_inputs(), self.rooms, states, trigger_demand=True
        )
        self.assertFalse(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 12.0)

    def test_cold_outside_boiler_follows_the_thermostat_room(self):
        states = states_of(self.rooms, {"salon": 19.0, "cuisine": 19.5, "bain": 19.5})
        house = compute_house(
            settings(), house_inputs(outdoor_temp=0.0), self.rooms, states
        )
        self.assertFalse(house.splits_allowed)
        self.assertEqual(house.boiler_setpoint, 19.5)  # normal: salon target

    def test_cold_outside_demand_raises_above_the_normal_setpoint(self):
        states = states_of(self.rooms, {"salon": 19.0, "cuisine": 18.0, "bain": 19.0})
        house = compute_house(
            settings(), house_inputs(outdoor_temp=0.0), self.rooms, states
        )
        self.assertTrue(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 21.0)  # max(normal, demand)

    def test_night_uses_the_night_setpoint(self):
        states = states_of(self.rooms, {"salon": 19.0, "cuisine": 19.0, "bain": 19.0})
        night = house_inputs(now=datetime(2026, 10, 21, 23, 0))
        house = compute_house(settings(), night, self.rooms, states)
        self.assertEqual(house.boiler_setpoint, 16.0)
        self.assertEqual(house.thermostat_room_target, 16.0)

    def test_away_uses_the_away_setpoint(self):
        states = states_of(self.rooms, {"salon": 13.0, "cuisine": 13.0, "bain": 13.0})
        house = compute_house(
            settings(), house_inputs(away_mode=True), self.rooms, states
        )
        self.assertEqual(house.boiler_setpoint, 12.0)

    def test_demand_release_is_debounced(self):
        """A sensor fluttering across the dead band cannot sawtooth the demand."""
        rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5, thermostat_room=True),
        ]
        states = states_of(rooms, {"salon": 19.2})  # in the dead band
        # A cold dip latches the demand.
        states["salon"].temp = 19.1
        house = compute_house(settings(), house_inputs(), rooms, states,
                              trigger_demand=False)
        self.assertTrue(house.boiler_demand)
        # The sensor bounces back above the target, but the debounce
        # holds the demand for the minimum run/stop time.
        states["salon"].temp = 19.7
        house = compute_house(settings(), house_inputs(
            demand_hold_expired=False
        ), rooms, states, trigger_demand=True)
        self.assertTrue(house.boiler_demand)
        # Once the debounce expired, the release applies.
        house = compute_house(settings(), house_inputs(
            demand_hold_expired=True
        ), rooms, states, trigger_demand=True)
        self.assertFalse(house.boiler_demand)
        self.assertFalse(house.trigger_demand)

    def test_frost_floor(self):
        states = states_of(self.rooms, {"salon": 12.0, "cuisine": 12.0, "bain": 12.0})
        house = compute_house(
            settings(), house_inputs(away_mode=True), self.rooms, states
        )
        self.assertEqual(house.boiler_setpoint, 12.0)  # away 12 > frost 7
        house = compute_house(
            settings(away_setpoint=5.0), house_inputs(away_mode=True),
            self.rooms, states,
        )
        self.assertEqual(house.boiler_setpoint, 7.0)  # frost floor
        # A room at 6 C in away mode is a demand, whatever the setpoint says.
        states["cuisine"].temp = 6.0
        states["bain"].temp = 6.0
        house = compute_house(
            settings(away_setpoint=5.0), house_inputs(away_mode=True),
            self.rooms, states,
        )
        self.assertTrue(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 21.0)


class TestSources(unittest.TestCase):
    def test_room_source_follows_splits_and_boiler(self):
        rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                      thermostat_room=True, switches=("sw1",), climates=("cl1",))]
        states = states_of(rooms, {"salon": 19.0})
        house = compute_house(settings(), house_inputs(), rooms, states)
        rooms_logic.update_room_states(house, states, boiler_heating=True)
        self.assertEqual(states["salon"].source, "splits")
        # Split stopped, boiler heating, room still below target.
        states["salon"].temp = 19.4
        states["salon"].splits_on = False
        house = compute_house(settings(), house_inputs(), rooms, states)
        rooms_logic.update_room_states(house, states, boiler_heating=True)
        self.assertEqual(states["salon"].source, "boiler")
        rooms_logic.update_room_states(house, states, boiler_heating=False)
        self.assertEqual(states["salon"].source, "idle")


class TestSolarBonus(unittest.TestCase):
    def test_surplus_hysteresis_band(self):
        s = settings(surplus_threshold=500.0)
        next_bonus = rooms_logic.next_solar_bonus
        # Surplus >= +threshold: bonus on.
        self.assertTrue(next_bonus(s, 1500.0, 1000.0, False))
        # Surplus between -threshold and +threshold: keep the previous state.
        self.assertTrue(next_bonus(s, 1000.0, 1000.0, True))  # surplus 0
        self.assertFalse(next_bonus(s, 1000.0, 1000.0, False))
        # Surplus <= -threshold: bonus off.
        self.assertFalse(next_bonus(s, 800.0, 1400.0, True))

    def test_fallback_without_consumption_sensor(self):
        s = settings(production_threshold=500.0)
        next_bonus = rooms_logic.next_solar_bonus
        self.assertTrue(next_bonus(s, 650.0, None, False))
        self.assertFalse(next_bonus(s, 400.0, None, True))
        self.assertFalse(next_bonus(s, None, None, True))

    def test_bonus_raises_split_room_targets_only(self):
        rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5,
                 thermostat_room=True, switches=("sw1",), climates=("cl1",)),
            room("cuisine", "Cuisine", "sensor.cuisine", follow_room="salon"),
        ]
        states = states_of(rooms, {"salon": 19.0, "cuisine": 19.5})
        inputs = house_inputs(solar_bonus_active=True)
        house = compute_house(settings(), inputs, rooms, states)
        self.assertEqual(house.room("salon").target, 20.0)  # 19.5 + 0.5
        self.assertEqual(house.room("cuisine").target, 19.5)  # no split: no bonus

    def test_no_bonus_at_night(self):
        rooms = [
            room("salon", "Salon", "sensor.salon", target=19.5,
                 switches=("sw1",), climates=("cl1",)),
        ]
        states = states_of(rooms, {"salon": 15.0})
        night = house_inputs(now=datetime(2026, 10, 21, 23, 0),
                             solar_bonus_active=True)
        house = compute_house(settings(), night, rooms, states)
        self.assertEqual(house.room("salon").target, 16.0)  # night setpoint


class TestBoilerFallback(unittest.TestCase):
    def setUp(self):
        self.rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                           thermostat_room=True, switches=("sw1",), climates=("cl1",))]
        self.settings = settings(fallback_minutes=120)
        self.t0 = NOW

    def decision(self, states, temp):
        states["salon"].temp = temp
        states["salon"].splits_on = True
        return compute_house(self.settings, house_inputs(), self.rooms, states)

    def test_stalled_split_requests_the_boiler(self):
        states = states_of(self.rooms, {"salon": 17.0})
        # First pass: the split starts, the progress window opens.
        house = self.decision(states, 17.0)
        state = states["salon"]
        update_fallback(state, house.room("salon"), self.settings, self.t0)
        self.assertFalse(state.fallback)

        # Two hours without 0.2 C of progress and still below target.
        later = self.t0 + timedelta(minutes=121)
        house = self.decision(states, 17.1)  # less than PROGRESS_STEP
        update_fallback(state, house.room("salon"), self.settings, later)
        self.assertTrue(state.fallback)

        # The boiler demand includes the fallback rooms.
        house = compute_house(
            self.settings, house_inputs(), self.rooms, states,
            fallback_demand=state.fallback,
        )
        self.assertTrue(house.boiler_demand)
        self.assertEqual(house.boiler_setpoint, 21.0)

    def test_progress_resets_the_window(self):
        states = states_of(self.rooms, {"salon": 17.0})
        house = self.decision(states, 17.0)
        state = states["salon"]
        update_fallback(state, house.room("salon"), self.settings, self.t0)
        later = self.t0 + timedelta(minutes=90)
        house = self.decision(states, 17.5)  # real progress
        update_fallback(state, house.room("salon"), self.settings, later)
        self.assertFalse(state.fallback)
        # The window restarted at the last progress.
        even_later = later + timedelta(minutes=100)
        house = self.decision(states, 17.6)
        update_fallback(state, house.room("salon"), self.settings, even_later)
        self.assertFalse(state.fallback)

    def test_target_reached_clears_the_fallback(self):
        states = states_of(self.rooms, {"salon": 17.0})
        house = self.decision(states, 17.0)
        state = states["salon"]
        state.fallback = True
        state.fallback_notify = True
        house = self.decision(states, 19.5)  # target reached
        update_fallback(state, house.room("salon"), self.settings, self.t0)
        self.assertFalse(state.fallback)
        self.assertFalse(state.fallback_notify)

    def test_no_fallback_when_the_split_is_not_running(self):
        states = states_of(self.rooms, {"salon": 17.0})
        states["salon"].splits_on = False
        house = compute_house(self.settings, house_inputs(), self.rooms, states)
        state = states["salon"]
        update_fallback(state, house.room("salon"), self.settings,
                        self.t0 + timedelta(minutes=200))
        self.assertFalse(state.fallback)


class TestManualOverride(unittest.TestCase):
    def setUp(self):
        self.rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                           thermostat_room=True, switches=("sw1",), climates=("cl1",))]
        self.state = states_of(self.rooms, {"salon": 17.0})["salon"]
        self.now = NOW

    def test_followed_command_is_confirmed(self):
        self.state.cmd_on = True
        self.state.cmd_time = self.now
        event = detect_manual(self.state, True, self.now + timedelta(minutes=3))
        self.assertIsNone(event)
        self.assertTrue(self.state.confirmed)

    def test_change_after_confirmation_is_manual(self):
        self.state.cmd_on = True
        self.state.cmd_time = self.now
        self.state.confirmed = True
        later = self.now + timedelta(minutes=10)
        event = detect_manual(self.state, False, later)
        self.assertEqual(event, "manual")
        self.assertEqual(self.state.override_mode, "day")
        self.assertIsNone(self.state.cmd_on)
        self.assertFalse(self.state.confirmed)

    def test_command_never_followed_is_unresponsive_once(self):
        self.state.cmd_on = True
        self.state.cmd_time = self.now
        later = self.now + timedelta(minutes=3)
        self.assertEqual(detect_manual(self.state, False, later), "unresponsive")
        self.assertIsNone(detect_manual(self.state, False, later + timedelta(minutes=1)))

    def test_left_alone_room_is_not_regulated_until_the_transition(self):
        self.state.override_mode = "day"
        house = compute_house(settings(), house_inputs(), self.rooms,
                              {"salon": self.state})
        decision = house.room("salon")
        self.assertTrue(decision.left_alone)
        self.assertFalse(decision.want_splits)

        # Night comes: the override clears and the splits are cut.
        night = house_inputs(now=datetime(2026, 10, 21, 23, 0))
        house = compute_house(settings(), night, self.rooms, {"salon": self.state})
        decision = house.room("salon")
        self.assertFalse(decision.left_alone)
        self.assertFalse(decision.splits_allowed)
        self.assertIsNone(self.state.override_mode)


class TestMinCycle(unittest.TestCase):
    def setUp(self):
        self.rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                           thermostat_room=True, switches=("sw1",), climates=("cl1",))]

    def test_recent_change_keeps_the_split_in_its_current_state(self):
        s = settings(min_cycle_minutes=5)
        # Split running since a minute, room now at target: keep it on.
        states = states_of(self.rooms, {"salon": 19.6})
        states["salon"].splits_on = True
        states["salon"].last_change = NOW - timedelta(minutes=1)
        house = compute_house(s, house_inputs(), self.rooms, states)
        self.assertTrue(house.room("salon").want_splits)

        # Split stopped a minute ago, room cold: keep it off.
        states["salon"].splits_on = False
        states["salon"].temp = 18.0
        house = compute_house(s, house_inputs(), self.rooms, states)
        self.assertFalse(house.room("salon").want_splits)

    def test_change_after_the_minimum_time_is_applied(self):
        s = settings(min_cycle_minutes=5)
        states = states_of(self.rooms, {"salon": 19.6})
        states["salon"].splits_on = True
        states["salon"].last_change = NOW - timedelta(minutes=6)
        house = compute_house(s, house_inputs(), self.rooms, states)
        self.assertFalse(house.room("salon").want_splits)

    def test_forced_stop_ignores_the_minimum_time(self):
        s = settings(min_cycle_minutes=60)
        states = states_of(self.rooms, {"salon": 17.0})
        states["salon"].splits_on = True
        states["salon"].last_change = NOW - timedelta(minutes=1)
        cold = house_inputs(outdoor_temp=0.0)
        house = compute_house(s, cold, self.rooms, states)
        self.assertFalse(house.room("salon").splits_allowed)
        self.assertFalse(house.room("salon").want_splits)

    def test_zero_minimum_time_changes_immediately(self):
        s = settings(min_cycle_minutes=0)
        states = states_of(self.rooms, {"salon": 19.6})
        states["salon"].splits_on = True
        states["salon"].last_change = NOW
        house = compute_house(s, house_inputs(), self.rooms, states)
        self.assertFalse(house.room("salon").want_splits)


class TestLearning(unittest.TestCase):
    def setUp(self):
        self.rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                           thermostat_room=True, switches=("sw1",), climates=("cl1",))]
        self.state = states_of(self.rooms, {"salon": 17.0})["salon"]
        self.settings = settings()

    def decision(self, temp, splits_on=True):
        self.state.temp = temp
        self.state.splits_on = splits_on
        house = compute_house(self.settings, house_inputs(), self.rooms,
                              {"salon": self.state})
        return house.room("salon")

    def test_session_produces_a_rate(self):
        decision = self.decision(17.0)
        update_learning(self.state, decision, NOW, 5.0, False)
        self.assertIsNotNone(self.state.session)

        # The room warms up over an hour, then the session ends.
        later = NOW + timedelta(hours=1)
        decision = self.decision(18.4)
        learned = update_learning(self.state, decision, later, 5.0, False)
        self.assertFalse(learned)  # session still ongoing
        # Heating stops: the session is finalized.
        decision = self.decision(18.5, splits_on=False)
        learned = update_learning(self.state, decision, later, 5.0, False)
        self.assertTrue(learned)
        self.assertAlmostEqual(self.state.rate, 1.4, places=3)
        self.assertEqual(self.state.ref_outdoor, 5.0)

    def test_short_session_is_ignored(self):
        decision = self.decision(17.0)
        update_learning(self.state, decision, NOW, 5.0, False)
        later = NOW + timedelta(minutes=10)  # below the minimum
        decision = self.decision(17.6, splits_on=False)
        learned = update_learning(self.state, decision, later, 5.0, False)
        self.assertFalse(learned)
        self.assertIsNone(self.state.rate)

    def test_rate_is_smoothed(self):
        self.state.rate = 1.0
        self.state.session = {
            "t0": NOW, "temp0": 17.0, "t": NOW + timedelta(hours=1),
            "temp": 19.0, "outdoor": 5.0,
        }
        # Finalize directly: a 2 C/h sample with alpha 0.3.
        learned = rooms_logic.finalize_session(self.state)
        self.assertTrue(learned)
        self.assertAlmostEqual(self.state.rate, 1.0 * 0.7 + 2.0 * 0.3, places=3)
        self.assertAlmostEqual(self.state.ref_outdoor, 5.0, places=3)


class TestPreheat(unittest.TestCase):
    def setUp(self):
        self.rooms = [room("salon", "Salon", "sensor.salon", target=19.5,
                           thermostat_room=True, switches=("sw1",), climates=("cl1",))]

    def test_fixed_duration_until_a_rate_is_learned(self):
        s = settings(initial_preheat_minutes=60, max_preheat_minutes=180)
        state = states_of(self.rooms, {"salon": 17.0})["salon"]
        minutes = rooms_logic.preheat_duration(s, state, 19.5, None)
        self.assertEqual(minutes, 60)

    def test_learned_rate_gives_the_duration(self):
        s = settings(initial_preheat_minutes=60, max_preheat_minutes=180)
        state = states_of(self.rooms, {"salon": 17.0})["salon"]
        state.rate = 1.0  # 1 C/h: 2.5 C gap -> 150 min
        state.ref_outdoor = 5.0
        minutes = rooms_logic.preheat_duration(s, state, 19.5, None)
        self.assertAlmostEqual(minutes, 150.0, places=3)

    def test_weather_factor_extends_the_duration_when_colder(self):
        s = settings(initial_preheat_minutes=60, max_preheat_minutes=1000)
        state = states_of(self.rooms, {"salon": 17.0})["salon"]
        state.rate = 1.0
        state.ref_outdoor = 5.0
        # Forecast 10 C colder than when the rate was learned: +30 %.
        minutes = rooms_logic.preheat_duration(s, state, 19.5, -5.0)
        self.assertAlmostEqual(minutes, 150.0 * 1.3, places=3)
        # ...but the factor is clamped.
        minutes = rooms_logic.preheat_duration(s, state, 19.5, -100.0)
        self.assertAlmostEqual(minutes, 150.0 * rooms_logic.WEATHER_FACTOR_MAX, places=3)

    def test_duration_is_capped(self):
        s = settings(initial_preheat_minutes=60, max_preheat_minutes=100)
        state = states_of(self.rooms, {"salon": 10.0})["salon"]
        state.rate = 0.1
        minutes = rooms_logic.preheat_duration(s, state, 19.5, None)
        self.assertEqual(minutes, 100)

    def test_preheat_before_the_day_period(self):
        s = settings(initial_preheat_minutes=60)
        state = states_of(self.rooms, {"salon": 17.0})["salon"]
        # 05:00, day starts at 07:00, 60 min of pre-heating: not yet.
        inputs = house_inputs(now=datetime(2026, 10, 21, 5, 0))
        active, start = rooms_logic.preheat_window(s, inputs, 60, "night")
        self.assertFalse(active)
        # 06:15: pre-heating.
        inputs = house_inputs(now=datetime(2026, 10, 21, 6, 15))
        active, start = rooms_logic.preheat_window(s, inputs, 60, "night")
        self.assertTrue(active)
        self.assertEqual(start, datetime(2026, 10, 21, 6, 0))

    def test_preheat_before_the_planned_return(self):
        s = settings(initial_preheat_minutes=60)
        inputs = house_inputs(
            now=datetime(2026, 10, 21, 16, 0),
            away_mode=True,
            away_end=datetime(2026, 10, 21, 17, 0),
        )
        active, start = rooms_logic.preheat_window(s, inputs, 60, "away")
        self.assertTrue(active)
        self.assertEqual(start, datetime(2026, 10, 21, 16, 0))
        # No planned return: no pre-heating.
        inputs = house_inputs(now=datetime(2026, 10, 21, 16, 0), away_mode=True)
        active, start = rooms_logic.preheat_window(s, inputs, 60, "away")
        self.assertFalse(active)

    def test_preheating_room_switches_to_day_early(self):
        s = settings(initial_preheat_minutes=90)
        states = states_of(self.rooms, {"salon": 17.0})
        # 05:45, day at 07:00, 90 min pre-heating: the room is "day".
        inputs = house_inputs(now=datetime(2026, 10, 21, 5, 45))
        house = compute_house(s, inputs, self.rooms, states)
        decision = house.room("salon")
        self.assertTrue(decision.preheating)
        self.assertEqual(decision.target, 19.5)  # day target, not night 16
        self.assertEqual(states["salon"].next_preheat,
                         datetime(2026, 10, 21, 5, 30))
        # The splits never run while pre-heating: the boiler does it.
        self.assertFalse(decision.splits_allowed)
        self.assertFalse(decision.want_splits)
        self.assertFalse(house.splits_allowed)
        self.assertEqual(house.mode, "night")
        # The boiler follows the thermostat room's day target, whether
        # the night is mild or cold.
        self.assertEqual(house.boiler_setpoint, 19.5)

        cold = house_inputs(now=datetime(2026, 10, 21, 5, 45), outdoor_temp=0.0)
        house = compute_house(s, cold, self.rooms, states)
        self.assertEqual(house.boiler_setpoint, 19.5)


def tearDownModule():
    unload_pure()


if __name__ == "__main__":
    unittest.main()
