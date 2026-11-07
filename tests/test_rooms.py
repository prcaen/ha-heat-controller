"""Room model tests (no homeassistant needed).

`rooms.py` only imports the constants, but its relative import needs a
package context: the module is loaded by path with stub package parents,
then the stubs are removed so the real package (imported by the
controller tests with homeassistant installed) is unaffected.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

BASE = Path(__file__).parent.parent / "custom_components" / "heat_controller"

_STUBS: list[str] = []


def _load_standalone(name: str, path: Path, package: str | None = None):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    _STUBS.append(name)
    if package is not None:
        module.__package__ = package
    spec.loader.exec_module(module)
    return module


def _setup() -> None:
    for name, path in (
        ("custom_components", BASE.parent),
        ("custom_components.heat_controller", BASE),
    ):
        if name in sys.modules:
            continue
        stub = types.ModuleType(name)
        stub.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[name] = stub
        _STUBS.append(name)
    _load_standalone("custom_components.heat_controller.const", BASE / "const.py")
    _load_standalone(
        "custom_components.heat_controller.rooms", BASE / "rooms.py",
        package="custom_components.heat_controller",
    )


def _teardown() -> None:
    while _STUBS:
        sys.modules.pop(_STUBS.pop(), None)


class FakeSubentry:
    """Duck-typed config sub-entry."""

    def __init__(
        self,
        subentry_id: str,
        subentry_type: str,
        title: str,
        data: dict,
    ) -> None:
        self.subentry_id = subentry_id
        self.subentry_type = subentry_type
        self.title = title
        self.data = data


class TestRooms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup()

    @classmethod
    def tearDownClass(cls):
        _teardown()

    def room_module(self):
        return sys.modules["custom_components.heat_controller.rooms"]

    def test_room_from_subentry(self):
        rooms = self.room_module()
        room = rooms.room_from_subentry(
            FakeSubentry(
                "s1",
                "room",
                "Salon",
                {
                    "name": "Salon",
                    "temperature_sensor": "sensor.salon",
                    "target": 19.5,
                    "boiler_trigger": False,
                    "thermostat_room": True,
                    "switches": ["switch.g", "switch.d"],
                    "climates": ["climate.g", "climate.d"],
                    "fans": ["fan.g", "fan.d"],
                    "fan_preset": "low",
                },
            )
        )
        self.assertEqual(room.id, "s1")
        self.assertEqual(room.name, "Salon")
        self.assertEqual(room.sensor, "sensor.salon")
        self.assertEqual(room.target, 19.5)
        self.assertTrue(room.thermostat_room)
        self.assertFalse(room.boiler_trigger)  # has splits: no boiler trigger
        self.assertEqual(room.switches, ("switch.g", "switch.d"))
        self.assertEqual(room.climates, ("climate.g", "climate.d"))
        self.assertTrue(room.has_splits)
        self.assertEqual(room.fan_preset, "low")
        self.assertEqual(room.fan_preset_or("auto"), "low")

    def test_fan_preset_inherits_by_default(self):
        rooms = self.room_module()
        # No stored value: the room inherits the global preset.
        room = rooms.room_from_subentry(
            FakeSubentry("s1", "room", "Salon", {"temperature_sensor": "sensor.salon"})
        )
        self.assertEqual(room.fan_preset, "inherit")
        self.assertEqual(room.fan_preset_or("auto"), "auto")
        # An unknown stored value (older/edited config) also inherits.
        stale = rooms.room_from_subentry(
            FakeSubentry("s2", "room", "S2", {
                "temperature_sensor": "sensor.s2",
                "fan_preset": "turbo",
            })
        )
        self.assertEqual(stale.fan_preset, "inherit")
        self.assertEqual(stale.fan_preset_or("medium"), "medium")

    def test_boiler_trigger_derived_from_the_splits(self):
        rooms = self.room_module()
        # A room without splits (kitchen, bathroom...) can request the
        # boiler: it is its only heat source on mild days.
        no_split = rooms.room_from_subentry(
            FakeSubentry("s4", "room", "Cuisine", {
                "name": "Cuisine",
                "temperature_sensor": "sensor.cuisine",
                # a stale boiler_trigger flag must not override the rule
                "boiler_trigger": False,
            })
        )
        self.assertTrue(no_split.boiler_trigger)
        # A room with splits cannot: it falls back to the boiler only
        # through the automatic stall detection.
        with_split = rooms.room_from_subentry(
            FakeSubentry("s5", "room", "Salon", {
                "name": "Salon",
                "temperature_sensor": "sensor.salon",
                "boiler_trigger": True,
                "switches": ["switch.g"],
                "climates": ["climate.g"],
            })
        )
        self.assertFalse(with_split.boiler_trigger)

    def test_follow_room_without_target(self):
        rooms = self.room_module()
        room = rooms.room_from_subentry(
            FakeSubentry(
                "s2",
                "room",
                "Cuisine",
                {"temperature_sensor": "sensor.cuisine", "follow_room": "s1"},
            )
        )
        self.assertIsNone(room.target)
        self.assertEqual(room.follow_room, "s1")
        self.assertFalse(room.has_splits)

    def test_title_used_when_name_missing(self):
        rooms = self.room_module()
        room = rooms.room_from_subentry(
            FakeSubentry(
                "s3", "room", "Salle de bain", {"temperature_sensor": "sensor.sb"}
            )
        )
        self.assertEqual(room.name, "Salle de bain")

    def test_malformed_subentries_are_skipped(self):
        rooms = self.room_module()
        subs = {
            "s1": FakeSubentry("s1", "room", "Salon", {"temperature_sensor": "sensor.salon", "target": 19.5}),
            "bad": FakeSubentry("bad", "room", "No sensor", {"target": 18}),
            "other": FakeSubentry("other", "not_a_room", "X", {"temperature_sensor": "s"}),
        }
        found = rooms.rooms_from_subentries(subs)
        self.assertEqual([room.id for room in found], ["s1"])
        # Empty and None inputs are tolerated.
        self.assertEqual(rooms.rooms_from_subentries({}), [])
        self.assertEqual(rooms.rooms_from_subentries(None), [])

    def test_signature_tracks_every_field(self):
        rooms = self.room_module()
        data = {"temperature_sensor": "sensor.salon", "target": 19.5}
        first = rooms.rooms_signature(
            [rooms.room_from_subentry(FakeSubentry("s1", "room", "Salon", data))]
        )
        same = rooms.rooms_signature(
            [rooms.room_from_subentry(FakeSubentry("s1", "room", "Salon", data))]
        )
        self.assertEqual(first, same)
        changed = dict(data, target=20.0)
        other = rooms.rooms_signature(
            [rooms.room_from_subentry(FakeSubentry("s1", "room", "Salon", changed))]
        )
        self.assertNotEqual(first, other)
        # The fan preset is part of the signature (it reloads the entry).
        changed_fan = dict(data, fan_preset="high")
        other_fan = rooms.rooms_signature(
            [rooms.room_from_subentry(FakeSubentry("s1", "room", "Salon", changed_fan))]
        )
        self.assertNotEqual(first, other_fan)


if __name__ == "__main__":
    unittest.main()
