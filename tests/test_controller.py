"""HeatController tests with fake states and services.

These tests import the real homeassistant package; they are skipped when it
is not installed (the CI integration job installs it).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

try:
    import homeassistant  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("homeassistant is not installed")

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

    async def async_call(self, domain, service, data, blocking=True) -> None:
        self.calls.append((domain, service, dict(data)))
        entity_id = data["entity_id"]
        if domain == "fan" and service == "turn_on":
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
    "heat_pump_fans": ["fan.pac1"],
    "accessory_fans": ["fan.acc"],
    # windows covering the whole day: time-independent tests
    "day_start": "00:00:00",
    "night_start": "23:59:00",
    "solar_start": "00:00:00",
    "solar_end": "23:59:00",
}


def build(
    entry_data: dict | None = None,
    outdoor: str = "2.0",
    indoor: str = "19.0",
    production: str = "0.0",
    heat_pump_source: bool = False,
):
    """Build a controller wired to fake states and services."""
    data = {**BASE_DATA, **(entry_data or {})}
    states = FakeStates()
    states.set("sensor.ext", outdoor)
    states.set("sensor.int", indoor)
    states.set("sensor.solar", production)
    for climate in ("climate.boiler", "climate.pac1"):
        states.set(
            climate,
            "off",
            {
                "target_temp_step": 0.5,
                "min_temp": 5,
                "max_temp": 30,
                "temperature": 19,
            },
        )
    for fan in ("fan.pac1", "fan.acc"):
        states.set(fan, "off")

    services = FakeServices(states)
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = data
    hass = MagicMock()
    hass.states = states
    hass.services = services

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


class TestWiring(unittest.TestCase):
    """Fan pairing with the heat pump climate units."""

    def test_fan_pairing(self):
        controller, _, _, _ = build()
        self.assertEqual(controller.heat_pump_fans, ["fan.pac1"])

        controller, _, _, _ = build({"heat_pump_climates": ["climate.a", "climate.b"]})
        self.assertEqual(controller.heat_pump_fans, ["fan.pac1", None])


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
        # Changeover sequence: boiler off, accessory fan on, clim in heat.
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.boiler", "off", None), calls
        )
        self.assertIn(("fan", "turn_on", "fan.acc", None, None), calls)
        self.assertIn(
            ("climate", "set_hvac_mode", "climate.pac1", "heat", None), calls
        )
        # Heating need: power fan on, solar setpoint (22) pushed.
        self.assertIn(("fan", "turn_on", "fan.pac1", None, None), calls)
        self.assertIn(
            ("climate", "set_temperature", "climate.pac1", None, 22.0), calls
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

    async def test_idle_heat_pump_stays_in_heat_mode(self):
        controller, shared, services, states = build(
            outdoor="8.0", indoor="22.5", heat_pump_source=True
        )
        # Steady idle state: the climate unit is in heat, its power fan on.
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
        states.set("fan.pac1", "on")
        controller._first_run = False
        await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.heat_pump_source)
        self.assertFalse(shared.demand)
        # Heat pump climate units stay in heat (never off, never auto) with
        # the idle setpoint.
        self.assertFalse(
            any(call[1] == "set_hvac_mode" and call[2] == "climate.pac1" for call in calls)
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.pac1", None, 12.0), calls
        )
        # Power fan is stopped when there is no heating need.
        self.assertIn(("fan", "turn_off", "fan.pac1", None, None), calls)
        self.assertNotIn(("fan", "turn_on", "fan.pac1", None, None), calls)

    async def test_control_off_clears_decision(self):
        controller, shared, _, _ = build(outdoor="2.0", indoor="22.5")
        controller._first_run = False
        await controller._update_locked()
        self.assertIsNotNone(shared.decision)

        shared.set_active(False)
        await controller._update_locked()
        self.assertIsNone(shared.decision)


if __name__ == "__main__":
    unittest.main()
