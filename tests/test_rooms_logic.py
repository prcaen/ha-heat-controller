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
            room("cuisine", "Cuisine", "sensor.cuisine", follow_room="salon",
                 boiler_trigger=True),
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
            room("cuisine", "Cuisine", "sensor.cuisine", follow_room="salon",
                 boiler_trigger=True),
            room("bain", "Salle de bain", "sensor.bain", follow_room="salon",
                 boiler_trigger=True),
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


def tearDownModule():
    unload_pure()


if __name__ == "__main__":
    unittest.main()
