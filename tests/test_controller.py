"""HeatController tests with fake states and services.

These tests import the real homeassistant package; they are skipped when it
is not installed (the CI integration job installs it).
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

try:
    import homeassistant  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("homeassistant is not installed")

from homeassistant.util import dt as dt_util

from custom_components.heat_controller import controller as controller_module
from custom_components.heat_controller.controller import (
    HeatController,
    settings_from_config,
)
from custom_components.heat_controller.state import HeatState


class FakeState:
    """Minimal state object returned by FakeStates.get()."""

    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


CLIMATE_ATTRIBUTES = {
    "target_temp_step": 0.5,
    "min_temp": 5,
    "max_temp": 30,
    "temperature": 19,
}


class FakeStates:
    """Minimal stand-in for hass.states."""

    def __init__(self) -> None:
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, state: str, attributes: dict | None = None) -> None:
        self._states[entity_id] = FakeState(state, attributes)

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)

    def is_state(self, entity_id: str, value: str) -> bool:
        state = self._states.get(entity_id)
        return state is not None and state.state == value


class FakeServices:
    """Minimal stand-in for hass.services, recording the calls."""

    def __init__(self, states: FakeStates) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._states = states
        # Switches in this set accept switch.turn_on but never switch on,
        # like a relay that ignores the command.
        self.unresponsive_switches: set[str] = set()
        # switch entity -> climate entity that becomes available when the
        # switch goes on (a split climate often reports "unavailable" while
        # unpowered).
        self.power_restores: dict[str, str] = {}

    async def async_call(self, domain, service, data, blocking=True) -> None:
        self.calls.append((domain, service, dict(data)))
        entity_id = data["entity_id"]
        if domain == "switch" and service == "turn_on":
            if entity_id not in self.unresponsive_switches:
                self._states.set(entity_id, "on")
                climate = self.power_restores.get(entity_id)
                if climate is not None:
                    self._states.set(climate, "off", CLIMATE_ATTRIBUTES)
        elif domain == "switch" and service == "turn_off":
            self._states.set(entity_id, "off")
        elif domain == "fan" and service == "turn_on":
            self._states.set(entity_id, "on")
        elif domain == "fan" and service == "turn_off":
            self._states.set(entity_id, "off")
        elif domain == "climate" and service == "set_hvac_mode":
            self._states.set(
                entity_id,
                data["hvac_mode"],
                {
                    "target_temp_step": 0.5,
                    "min_temp": 5,
                    "max_temp": 30,
                    "temperature": 19,
                },
            )
        elif domain == "climate" and service == "set_temperature":
            state = self._states.get(entity_id)
            attributes = dict(state.attributes) if state else {}
            attributes["temperature"] = data["temperature"]
            self._states.set(entity_id, state.state if state else "auto", attributes)


async def _fast_sleep(_delay) -> None:
    """Skip the delays of the changeover sequences."""
    return None


BASE_DATA = {
    "outdoor_temp_sensor": "sensor.ext",
    "indoor_temp_sensor": "sensor.int",
    "production_sensor": "sensor.solar",
    "boiler_climate": "climate.boiler",
    "heat_pump_climates": ["climate.pac1"],
    "heat_pump_switches": ["switch.pac1"],
    "accessory_fans": ["fan.acc"],
    # windows covering the whole day: time-independent tests
    "day_start": "00:00:00",
    "night_start": "23:59:00",
    "solar_start": "00:00:00",
    "solar_end": "23:59:00",
}


class FakeSubentry:
    """Minimal stand-in for a config sub-entry."""

    def __init__(self, subentry_id: str, title: str, data: dict) -> None:
        self.subentry_id = subentry_id
        self.subentry_type = "room"
        self.title = title
        self.data = data


def build(
    entry_data: dict | None = None,
    outdoor: str = "2.0",
    indoor: str = "19.0",
    production: str = "0.0",
    heat_pump_source: bool = False,
    subentries: dict[str, FakeSubentry] | None = None,
):
    """Build a controller wired to fake states and services."""
    data = {**BASE_DATA, **(entry_data or {})}
    states = FakeStates()
    states.set("sensor.ext", outdoor)
    states.set("sensor.int", indoor)
    states.set("sensor.solar", production)
    for climate in ("climate.boiler", "climate.pac1"):
        states.set(climate, "off", dict(CLIMATE_ATTRIBUTES))
    for entity in ("switch.pac1", "fan.acc"):
        states.set(entity, "off")

    services = FakeServices(states)
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = data
    entry.subentries = subentries or {}
    hass = MagicMock()
    hass.states = states
    hass.services = services
    hass.async_create_task = lambda coro: coro.close()

    shared = HeatState()
    shared.set_active(True)
    shared.set_heat_pump_source(heat_pump_source)
    controller = HeatController(hass, entry, data, shared)
    return controller, shared, services, states


def summarise(services: FakeServices) -> list[tuple]:
    """(domain, service, entity_id, hvac_mode, temperature) of each call."""
    return [
        (
            call[0],
            call[1],
            call[2]["entity_id"],
            call[2].get("hvac_mode"),
            call[2].get("temperature"),
        )
        for call in services.calls
    ]


class TestSettingsFromConfig(unittest.TestCase):
    """settings_from_config: defaults, overrides and fallbacks."""

    def test_defaults(self):
        settings = settings_from_config({})
        self.assertEqual(settings.outdoor_threshold, 3.0)
        self.assertEqual(settings.idle_setpoint, 12.0)
        self.assertEqual(settings.day_setpoint, 20.0)
        self.assertEqual(settings.day_start.total_seconds(), 7 * 3600)

    def test_overrides_and_fallbacks(self):
        settings = settings_from_config(
            {"outdoor_threshold": 5, "idle_setpoint": 14, "day_setpoint": "oops"}
        )
        self.assertEqual(settings.outdoor_threshold, 5.0)
        self.assertEqual(settings.idle_setpoint, 14.0)
        self.assertEqual(settings.day_setpoint, 20.0)  # invalid -> default

    def test_midnight_times_are_not_replaced_by_defaults(self):
        # timedelta(0) is falsy: "00:00:00" must still be honored.
        settings = settings_from_config(
            {"day_start": "00:00:00", "solar_start": "00:00:00", "night_start": "x"}
        )
        self.assertEqual(settings.day_start.total_seconds(), 0)
        self.assertEqual(settings.solar_start.total_seconds(), 0)
        self.assertEqual(settings.night_start.total_seconds(), 22 * 3600)


class TestWiring(unittest.TestCase):
    """Switch pairing with the heat pump climate units."""

    def test_switch_pairing(self):
        controller, _, _, _ = build()
        self.assertEqual(controller.heat_pump_switches, ["switch.pac1"])

        controller, _, _, _ = build({"heat_pump_climates": ["climate.a", "climate.b"]})
        self.assertEqual(controller.heat_pump_switches, ["switch.pac1", None])


class TestControlPass(unittest.IsolatedAsyncioTestCase):
    """Full control passes with fake states and services."""

    def setUp(self):
        patcher = patch.object(controller_module.asyncio, "sleep", _fast_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_changeover_to_heat_pump_and_setpoint(self):
        controller, shared, services, _ = build(
            outdoor="8.0", indoor="19.0", production="650"
        )
        await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.heat_pump_source)
        # Changeover sequence: boiler off, accessory fan on.
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.boiler", "off", None), calls
        )
        self.assertIn(("fan", "turn_on", "fan.acc", None, None), calls)
        # Heating need: the power switch is turned on before any climate
        # command, then the unit goes to heat and the solar setpoint (22)
        # is pushed.
        self.assertIn(("switch", "turn_on", "switch.pac1", None, None), calls)
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.pac1", "heat", None), calls
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.pac1", None, 22.0), calls
        )
        switch_call = calls.index(("switch", "turn_on", "switch.pac1", None, None))
        mode_calls = [
            i for i, call in enumerate(calls)
            if call[1] == "set_hvac_mode" and call[2] == "climate.pac1"
        ]
        self.assertTrue(all(i > switch_call for i in mode_calls))
        temp_calls = [
            i for i, call in enumerate(calls)
            if call[1] == "set_temperature" and call[2] == "climate.pac1"
        ]
        self.assertTrue(all(i > switch_call for i in temp_calls))
        # The boiler also gets its setpoint pushed down to the idle value,
        # so it cannot heat alongside the heat pump even if it does not
        # respect the "off" mode set during the changeover.
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_heat_pump_source_keeps_boiler_at_idle_setpoint(self):
        controller, shared, services, states = build(
            {"idle_setpoint": 11}, outdoor="8.0", indoor="19.0", heat_pump_source=True
        )
        # Steady heat pump state: the boiler ignored the "off" mode from a
        # previous changeover and still heats with its old setpoint.
        states.set(
            "climate.boiler",
            "auto",
            {
                "target_temp_step": 0.5,
                "min_temp": 5,
                "max_temp": 30,
                "temperature": 20,
            },
        )
        states.set(
            "climate.pac1",
            "heat",
            {
                "target_temp_step": 0.5,
                "min_temp": 5,
                "max_temp": 30,
                "temperature": 19,
            },
        )
        states.set("switch.pac1", "on")
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        # Every control pass re-asserts the configured idle setpoint on the
        # boiler: no dual heating with the heat pump.
        self.assertTrue(shared.heat_pump_source)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 11.0), calls
        )
        # The heat pump unit is driven normally.
        self.assertIn(
            ("climate", "set_temperature", "climate.pac1", None, 20.0), calls
        )

    async def test_idle_boiler_pushes_configured_idle_setpoint(self):
        controller, shared, services, _ = build(
            {"idle_setpoint": 14}, outdoor="2.0", indoor="22.5"
        )
        controller._first_run = False  # steady state: first pass already done
        await controller._update_locked()
        calls = summarise(services)

        self.assertFalse(shared.heat_pump_source)
        self.assertFalse(shared.demand)
        # Boiler stays in auto with the idle setpoint, never switched off.
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.boiler", "auto", None), calls
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 14.0), calls
        )
        self.assertNotIn(
            ("climate", "set_hvac_mode", "climate.boiler", "off", None), calls
        )
        self.assertFalse(any(call[0] == "fan" for call in calls))

    async def test_idle_heat_pump_cut_from_power(self):
        controller, shared, services, states = build(
            outdoor="8.0", indoor="22.5", heat_pump_source=True
        )
        # Steady idle state: the climate unit is in heat, powered on.
        states.set(
            "climate.pac1",
            "heat",
            {
                "target_temp_step": 0.5,
                "min_temp": 5,
                "max_temp": 30,
                "temperature": 19,
            },
        )
        states.set("switch.pac1", "on")
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.heat_pump_source)
        self.assertFalse(shared.demand)
        # The power switch is cut when there is no heating need...
        self.assertIn(("switch", "turn_off", "switch.pac1", None, None), calls)
        self.assertNotIn(("switch", "turn_on", "switch.pac1", None, None), calls)
        # ...and the climate unit is not driven at all: its mode is never
        # used to switch it off and no idle setpoint is pushed.
        self.assertFalse(any(call[2] == "climate.pac1" for call in calls))

    async def test_unresponsive_power_switch_blocks_climate_and_warns(self):
        controller, shared, services, _ = build(
            outdoor="8.0", indoor="19.0", heat_pump_source=True
        )
        services.unresponsive_switches = {"switch.pac1"}
        controller._first_run = False
        with self.assertLogs(
            "custom_components.heat_controller.controller", level="WARNING"
        ) as logs:
            await controller._update_locked()
        calls = summarise(services)

        # The unit is never driven while the power switch does not confirm
        # ON, and a warning explains why.
        self.assertFalse(any(call[2] == "climate.pac1" for call in calls))
        self.assertTrue(any("switch.pac1" in line for line in logs.output))

    async def test_climate_unavailable_while_unpowered_recovers_after_power_on(self):
        """A split climate often reports unavailable while unpowered."""
        controller, shared, services, states = build(
            outdoor="8.0", indoor="19.0", heat_pump_source=True
        )
        states.set("climate.pac1", "unavailable")
        # The unit comes back when its power switch goes on.
        services.power_restores = {"switch.pac1": "climate.pac1"}
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        # The state is re-read after the switch confirms ON: the unit is
        # driven normally in the same cycle.
        self.assertIn(("switch", "turn_on", "switch.pac1", None, None), calls)
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.pac1", "heat", None), calls
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.pac1", None, 20.0), calls
        )

    async def test_climate_still_unavailable_after_power_on_is_skipped(self):
        controller, shared, services, states = build(
            outdoor="8.0", indoor="19.0", heat_pump_source=True
        )
        states.set("climate.pac1", "unavailable")
        # The switch turns on but the climate never comes back.
        controller._first_run = False
        with self.assertLogs(
            "custom_components.heat_controller.controller", level="WARNING"
        ) as logs:
            await controller._update_locked()
        calls = summarise(services)

        self.assertIn(("switch", "turn_on", "switch.pac1", None, None), calls)
        # No climate command is sent while the unit is unavailable; the
        # next cycle retries it.
        self.assertFalse(any(call[2] == "climate.pac1" for call in calls))
        self.assertTrue(any("climate.pac1" in line for line in logs.output))

    async def test_changeover_to_boiler_cuts_power_switch(self):
        controller, shared, services, states = build(outdoor="2.0", indoor="19.0")
        shared.set_heat_pump_source(True)
        states.set("switch.pac1", "on")
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        self.assertFalse(shared.heat_pump_source)
        # The heat pump is stopped through its power switch only: its
        # climate mode is never set to off.
        self.assertIn(("switch", "turn_off", "switch.pac1", None, None), calls)
        self.assertFalse(
            any(call[1] == "set_hvac_mode" and call[2] == "climate.pac1" for call in calls)
        )
        # Accessory fans are stopped, the boiler takes over in auto.
        self.assertIn(("fan", "turn_off", "fan.acc", None, None), calls)
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.boiler", "auto", None), calls
        )

    async def test_control_off_clears_decision(self):
        controller, shared, _, _ = build(outdoor="2.0", indoor="22.5")
        controller._first_run = False
        await controller._update_locked()
        self.assertIsNotNone(shared.decision)

        shared.set_active(False)
        await controller._update_locked()
        self.assertIsNone(shared.decision)

    async def test_manual_override_keeps_boiler(self):
        controller, shared, services, _ = build(outdoor="8.0", indoor="19.0")
        shared.set_forced_source("boiler")
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        # Outdoor is high enough for the heat pump but the override wins.
        self.assertFalse(shared.heat_pump_source)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 20.0), calls
        )
        self.assertFalse(any(call[2] == "climate.pac1" for call in calls))

    async def test_update_config_applies_settings_live(self):
        controller, shared, services, _ = build(outdoor="2.0", indoor="22.5")
        controller.update_config({**controller.config, "idle_setpoint": 15.0})
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        self.assertFalse(shared.demand)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 15.0), calls
        )

    async def test_passed_away_until_ends_away_mode(self):
        controller, shared, _, _ = build(outdoor="2.0", indoor="22.5")
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() - timedelta(minutes=1))
        controller._first_run = False
        await controller._update_locked()

        self.assertFalse(shared.away_mode)
        self.assertIsNone(shared.away_until)


class TestHybridRooms(unittest.IsolatedAsyncioTestCase):
    """Hybrid multi-room regulation with room sub-entries."""

    def setUp(self):
        patcher = patch.object(controller_module.asyncio, "sleep", _fast_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def rooms_subentries():
        return {
            "salon": FakeSubentry("salon", "Salon", {
                "name": "Salon",
                "temperature_sensor": "sensor.salon_temp",
                "target": 19.5,
                "boiler_trigger": False,
                "thermostat_room": True,
                "switches": ["switch.salon_gauche", "switch.salon_droite"],
                "climates": ["climate.salon_gauche", "climate.salon_droite"],
                "fans": ["fan.salon_gauche", "fan.salon_droite"],
            }),
            "cuisine": FakeSubentry("cuisine", "Cuisine", {
                "name": "Cuisine",
                "temperature_sensor": "sensor.cuisine_temp",
                "follow_room": "salon",
                "boiler_trigger": True,
                "thermostat_room": False,
            }),
        }

    @staticmethod
    def seed_room_states(states):
        states.set("sensor.salon_temp", "17.0")
        states.set("sensor.cuisine_temp", "19.5")
        for entity in ("switch.salon_gauche", "switch.salon_droite"):
            states.set(entity, "off")
        for entity in ("climate.salon_gauche", "climate.salon_droite"):
            states.set(entity, "off", dict(CLIMATE_ATTRIBUTES))
        for entity in ("fan.salon_gauche", "fan.salon_droite"):
            states.set(entity, "off")

    async def test_mild_day_splits_regulate_and_boiler_rests(self):
        controller, shared, services, states = build(
            outdoor="8.0", subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.heat_pump_source)  # splits mode
        self.assertTrue(shared.demand)
        # Salon is cold (17 < 19.5 - 0.3): its splits start, switch first.
        self.assertIn(("switch", "turn_on", "switch.salon_gauche", None, None), calls)
        self.assertIn(("switch", "turn_on", "switch.salon_droite", None, None), calls)
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.salon_gauche", "heat", None), calls
        )
        # Split setpoint = target + offset (19.5 + 2 = 21.5).
        self.assertIn(
            ("climate", "set_temperature", "climate.salon_gauche", None, 21.5), calls
        )
        # Fan preset and oscillation on start.
        self.assertIn(
            ("fan", "set_preset_mode", "fan.salon_gauche", None, None)
            or self._preset_call(services, "fan.salon_gauche"),
            calls,
        )
        self.assertTrue(
            any(
                call[0] == "fan" and call[1] == "oscillate"
                for call in services.calls
            )
        )
        # Cuisine is at target: the boiler rests at the idle setpoint.
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.boiler", "auto", None), calls
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )
        # Accessory fans follow the splits mode.
        self.assertIn(("fan", "turn_on", "fan.acc", None, None), calls)

    @staticmethod
    def _preset_call(services, fan):
        for call in services.calls:
            if call[0] == "fan" and call[1] == "set_preset_mode" and call[2]["entity_id"] == fan:
                return (call[0], call[1], call[2]["entity_id"], None, None)
        return None

    async def test_trigger_room_raises_the_boiler(self):
        controller, shared, services, states = build(
            outdoor="8.0", subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        states.set("sensor.cuisine_temp", "18.0")
        await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.demand)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 21.0), calls
        )
        # The demand latches through the passes.
        states.set("sensor.cuisine_temp", "19.4")
        await controller._update_locked()
        self.assertTrue(controller._trigger_demand)
        states.set("sensor.cuisine_temp", "19.5")
        await controller._update_locked()
        self.assertFalse(controller._trigger_demand)
        calls = summarise(services)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_night_forces_the_splits_off(self):
        # day_start after night_start: the day period is always over.
        controller, shared, services, states = build(
            {"day_start": "23:00:00", "night_start": "22:00:00"},
            outdoor="8.0",
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("switch.salon_gauche", "on")
        states.set("switch.salon_droite", "on")
        states.set("fan.acc", "on")  # boiler mode must stop it
        await controller._update_locked()
        calls = summarise(services)

        self.assertFalse(shared.heat_pump_source)
        self.assertIn(("switch", "turn_off", "switch.salon_gauche", None, None), calls)
        self.assertIn(("switch", "turn_off", "switch.salon_droite", None, None), calls)
        # Boiler follows the thermostat room at the night setpoint.
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 16.0), calls
        )
        # Accessory fans off with the boiler mode.
        self.assertIn(("fan", "turn_off", "fan.acc", None, None), calls)

    async def test_cold_outside_splits_off_boiler_normal(self):
        controller, shared, services, states = build(
            outdoor="1.0", subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()
        calls = summarise(services)

        self.assertFalse(shared.heat_pump_source)
        self.assertFalse(
            any(call[0] == "switch" and call[1] == "turn_on" for call in calls)
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 19.5), calls
        )

    async def test_solar_surplus_raises_the_split_setpoint(self):
        controller, shared, services, states = build(
            {
                "consumption_sensor": "sensor.conso",
                "surplus_threshold": 500.0,
                "solar_bonus": 0.5,
            },
            outdoor="8.0",
            production="1500.0",
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("sensor.conso", "1000.0")  # surplus = +500 W: bonus on
        await controller._update_locked()
        calls = summarise(services)

        # Salon target 19.5 + 0.5 bonus, split setpoint = target + 2.
        self.assertIn(
            ("climate", "set_temperature", "climate.salon_gauche", None, 22.0), calls
        )
        # Below the surplus threshold, the bonus switches off (the
        # production alone no longer lifts the setpoint).
        states.set("sensor.conso", "1600.0")  # surplus = -100 W... still in band
        await controller._update_locked()
        self.assertTrue(controller._solar_bonus)  # band: keeps the state
        states.set("sensor.conso", "2200.0")  # surplus = -700 W: off
        await controller._update_locked()
        self.assertFalse(controller._solar_bonus)
        calls = summarise(services)
        self.assertIn(
            ("climate", "set_temperature", "climate.salon_gauche", None, 21.5), calls
        )

    async def test_room_without_sensor_is_left_alone(self):
        controller, shared, services, states = build(
            outdoor="8.0", subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        states.set("sensor.salon_temp", "unavailable")
        await controller._update_locked()
        calls = summarise(services)

        # Splits untouched, boiler rests (cuisine is at target).
        self.assertFalse(
            any(call[2] in ("switch.salon_gauche", "climate.salon_gauche") for call in calls)
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_thermostat_room_drives_the_global_decision(self):
        controller, shared, services, states = build(
            outdoor="8.0", subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()

        # The virtual thermostat follows the salon, not the configured
        # indoor sensor.
        self.assertEqual(controller.indoor_sensor, "sensor.salon_temp")
        self.assertEqual(shared.decision.target_setpoint, 19.5)


if __name__ == "__main__":
    unittest.main()
