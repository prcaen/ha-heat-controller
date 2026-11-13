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
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self._states = states
        # Switches in this set accept switch.turn_on but never switch on,
        # like a relay that ignores the command.
        self.unresponsive_switches: set[str] = set()
        # switch entity -> climate entity that becomes available when the
        # switch goes on (a split climate often reports "unavailable" while
        # unpowered).
        self.power_restores: dict[str, str] = {}

    async def async_call(self, domain, service, data, blocking=True, target=None) -> None:
        self.calls.append((domain, service, dict(data), target))
        if domain == "notify":
            return  # notifications have no entity_id
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
            self._states.set(entity_id, data["hvac_mode"], dict(CLIMATE_ATTRIBUTES))
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
    "production_sensor": "sensor.solar",
    "consumption_sensor": "sensor.conso",
    "boiler_climate": "climate.boiler",
    "accessory_fans": ["fan.acc"],
    # legacy single day period covering the whole day, migrated to the
    # whole week: time-independent tests
    "day_start": "00:00:00",
    "night_start": "23:59:00",
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
    outdoor: str = "8.0",
    production: str = "0.0",
    subentries: dict[str, FakeSubentry] | None = None,
):
    """Build a controller wired to fake states and services."""
    data = {**BASE_DATA, **(entry_data or {})}
    states = FakeStates()
    states.set("sensor.ext", outdoor)
    states.set("sensor.solar", production)
    states.set("sensor.conso", "0.0")
    states.set("climate.boiler", "heat", dict(CLIMATE_ATTRIBUTES))
    states.set("fan.acc", "off")

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
        self.assertEqual(settings.night_setpoint, 16.0)
        self.assertEqual(settings.day_start_week.total_seconds(), 7 * 3600)
        self.assertEqual(settings.day_end_week.total_seconds(), 22 * 3600)
        self.assertEqual(settings.day_start_weekend.total_seconds(), 7 * 3600)
        self.assertEqual(settings.day_end_weekend.total_seconds(), 22 * 3600)
        self.assertTrue(settings.notifications_enabled)

    def test_overrides_and_fallbacks(self):
        settings = settings_from_config(
            {"outdoor_threshold": 5, "idle_setpoint": 14, "hysteresis": "oops"}
        )
        self.assertEqual(settings.outdoor_threshold, 5.0)
        self.assertEqual(settings.idle_setpoint, 14.0)
        self.assertEqual(settings.hysteresis, 0.3)  # invalid -> default

    def test_midnight_times_are_not_replaced_by_defaults(self):
        # timedelta(0) is falsy: "00:00:00" must still be honored.
        settings = settings_from_config(
            {"day_start": "00:00:00", "night_start": "x"}
        )
        self.assertEqual(settings.day_start_week.total_seconds(), 0)
        self.assertEqual(settings.day_end_week.total_seconds(), 22 * 3600)

    def test_legacy_day_times_migrate_to_week_and_weekend(self):
        settings = settings_from_config(
            {"day_start": "06:30:00", "night_start": "21:00:00"}
        )
        self.assertEqual(settings.day_start_week, timedelta(hours=6, minutes=30))
        self.assertEqual(settings.day_end_week, timedelta(hours=21))
        self.assertEqual(settings.day_start_weekend, timedelta(hours=6, minutes=30))
        self.assertEqual(settings.day_end_weekend, timedelta(hours=21))

    def test_schedule_keys_override_the_legacy_times(self):
        settings = settings_from_config(
            {
                "day_start": "06:00:00",
                "night_start": "21:00:00",
                "day_start_weekend": "09:00:00",
                "day_end_weekend": "20:00:00",
            }
        )
        self.assertEqual(settings.day_start_week, timedelta(hours=6))
        self.assertEqual(settings.day_end_week, timedelta(hours=21))
        self.assertEqual(settings.day_start_weekend, timedelta(hours=9))
        self.assertEqual(settings.day_end_weekend, timedelta(hours=20))

    def test_schedule_key_without_legacy_falls_back_to_the_default(self):
        settings = settings_from_config({"day_start_weekend": "09:00:00"})
        self.assertEqual(settings.day_start_weekend, timedelta(hours=9))
        self.assertEqual(settings.day_start_week, timedelta(hours=7))
        self.assertEqual(settings.day_end_week, timedelta(hours=22))

    def test_per_weekday_keys_of_the_intermediate_schedule_migrate(self):
        # Entries configured with the intermediate per-weekday schedule:
        # a weekday stands for the week, a Saturday for the weekend.
        settings = settings_from_config(
            {
                "day_start_mon": "06:00:00",
                "day_end_mon": "21:00:00",
                "day_start_sat": "09:00:00",
                "day_end_sat": "20:00:00",
            }
        )
        self.assertEqual(settings.day_start_week, timedelta(hours=6))
        self.assertEqual(settings.day_end_week, timedelta(hours=21))
        self.assertEqual(settings.day_start_weekend, timedelta(hours=9))
        self.assertEqual(settings.day_end_weekend, timedelta(hours=20))

    def test_per_weekday_fallback_wins_over_the_legacy_pair(self):
        settings = settings_from_config(
            {
                "day_start": "05:00:00",
                "day_start_mon": "06:00:00",
                "day_start_sat": "09:00:00",
            }
        )
        self.assertEqual(settings.day_start_week, timedelta(hours=6))
        self.assertEqual(settings.day_start_weekend, timedelta(hours=9))

    def test_week_weekend_key_wins_over_every_fallback(self):
        settings = settings_from_config(
            {
                "day_start": "05:00:00",
                "day_start_mon": "06:00:00",
                "day_start_week": "07:30:00",
            }
        )
        self.assertEqual(settings.day_start_week, timedelta(hours=7, minutes=30))
        # No day_start_sat: the weekend falls back to the legacy day_start.
        self.assertEqual(settings.day_start_weekend, timedelta(hours=5))


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
            subentries=self.rooms_subentries()
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
        self.assertTrue(
            any(
                call[0] == "fan" and call[1] == "set_preset_mode"
                for call in services.calls
            )
        )
        self.assertTrue(
            any(call[0] == "fan" and call[1] == "oscillate" for call in services.calls)
        )
        # Cuisine is at target: the boiler rests at the idle setpoint.
        # Its HVAC mode is never touched in hybrid mode (the Netatmo
        # only supports off/heat).
        self.assertFalse(
            any(
                call[1] == "set_hvac_mode" and call[2] == "climate.boiler"
                for call in calls
            )
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )
        # Accessory fans follow the splits mode.
        self.assertIn(("fan", "turn_on", "fan.acc", None, None), calls)

    async def test_room_fan_preset_overrides_the_global_one(self):
        subentries = self.rooms_subentries()
        # The salon overrides the preset; the chambre inherits it.
        subentries["salon"].data["fan_preset"] = "low"
        subentries["chambre"] = FakeSubentry("chambre", "Chambre", {
            "name": "Chambre",
            "temperature_sensor": "sensor.chambre_temp",
            "target": 20.0,
            "switches": ["switch.chambre"],
            "climates": ["climate.chambre"],
            "fans": ["fan.chambre"],
        })
        controller, shared, services, states = build(
            entry_data={"split_fan_preset": "high"},  # the global preset
            subentries=subentries,
        )
        self.seed_room_states(states)
        states.set("sensor.chambre_temp", "17.0")  # cold: its splits start too
        states.set("switch.chambre", "off")
        states.set("climate.chambre", "off", dict(CLIMATE_ATTRIBUTES))
        states.set("fan.chambre", "off")
        await controller._update_locked()

        presets = {
            call[2]["entity_id"]: call[2]["preset_mode"]
            for call in services.calls
            if call[0] == "fan" and call[1] == "set_preset_mode"
        }
        # The salon fans get the room preset, the chambre fans the global.
        self.assertEqual(presets["fan.salon_gauche"], "low")
        self.assertEqual(presets["fan.salon_droite"], "low")
        self.assertEqual(presets["fan.chambre"], "high")

    async def test_trigger_room_raises_the_boiler(self):
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
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
        # Release (after the debounce window has expired).
        states.set("sensor.cuisine_temp", "19.5")
        controller._trigger_changed_at = dt_util.now() - timedelta(minutes=6)
        await controller._update_locked()
        self.assertFalse(controller._trigger_demand)
        calls = summarise(services)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_boiler_demand_debounce_holds_a_fresh_release(self):
        """A trigger sensor fluttering cannot sawtooth the demand (regression)."""
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        # The cuisine (trigger room, follows the salon at 19.5) is cold:
        # the demand latches.
        states.set("sensor.salon_temp", "19.5")
        states.set("sensor.cuisine_temp", "19.0")
        await controller._update_locked()
        self.assertTrue(controller._trigger_demand)
        self.assertIsNotNone(controller._trigger_changed_at)

        # The cuisine recovers, but within the minimum run/stop time
        # the demand holds: no sawtooth on a fluttering sensor.
        states.set("sensor.cuisine_temp", "19.6")
        await controller._update_locked()
        self.assertTrue(controller._trigger_demand)

        # After the debounce window, the demand releases.
        controller._trigger_changed_at = dt_util.now() - timedelta(minutes=6)
        await controller._update_locked()
        self.assertFalse(controller._trigger_demand)

    async def test_night_forces_the_splits_off(self):
        # day period start after its end (migrated to every day): always night.
        controller, shared, services, states = build(
            {"day_start": "23:00:00", "night_start": "22:00:00"},
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
            {"surplus_threshold": 500.0, "solar_bonus": 0.5},
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

    async def test_minimum_cycle_time_blocks_a_quick_stop(self):
        controller, shared, services, states = build(
            {"min_cycle_minutes": 5}, subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()  # splits start
        state = controller.room_states["salon"]

        # Target reached one minute after the start: the split keeps
        # running (minimum run time).
        states.set("sensor.salon_temp", "19.6")
        state.last_change = dt_util.now() - timedelta(minutes=1)
        calls_before = len(services.calls)
        await controller._update_locked()
        self.assertFalse(
            any(
                call[0] == "switch" and call[1] == "turn_off"
                for call in services.calls[calls_before:]
            )
        )

        # Six minutes later: the split stops.
        state.last_change = dt_util.now() - timedelta(minutes=6)
        await controller._update_locked()
        self.assertIn(
            ("switch", "turn_off", "switch.salon_gauche", None, None),
            summarise(services),
        )

    async def test_manual_split_change_leaves_the_room_alone(self):
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()  # splits on, command recorded
        state = controller.room_states["salon"]
        self.assertEqual(state.cmd_on, True)
        # The switches confirm: the command is followed.
        await controller._update_locked()
        self.assertTrue(state.confirmed)

        # Someone turns the splits off with the remote, well after the
        # command: the room is left alone from the next pass on.
        state.cmd_time = dt_util.now() - timedelta(minutes=5)
        states.set("switch.salon_gauche", "off")
        states.set("switch.salon_droite", "off")
        calls_before = len(services.calls)
        await controller._update_locked()
        self.assertEqual(state.override_mode, "day")
        await controller._update_locked()
        calls = summarise(services)
        # No switch command for the salon after the override.
        self.assertFalse(
            any(
                call[0] == "switch" and call[2].startswith("switch.salon")
                for call in calls[calls_before:]
            )
        )

    async def test_hybrid_boiler_never_touches_the_hvac_mode(self):
        """Regression: the Netatmo only supports off/heat, not auto."""
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()
        await controller._update_locked()
        calls = summarise(services)
        self.assertFalse(
            any(
                call[0] == "climate"
                and call[1] == "set_hvac_mode"
                and call[2] == "climate.boiler"
                for call in calls
            )
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_fallback_room_raises_the_boiler(self):
        controller, shared, services, states = build(
            {"fallback_minutes": 120}, subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        # The salon split has been running without progress and is flagged.
        controller.room_states["salon"].fallback = True
        with self.assertLogs(
            "custom_components.heat_controller.controller", level="WARNING"
        ) as logs:
            await controller._update_locked()
        calls = summarise(services)

        self.assertTrue(shared.demand)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 21.0), calls
        )
        self.assertTrue(any("not reaching the target" in line for line in logs.output))
        # The warning is emitted once, not at every pass.
        states.set("sensor.salon_temp", "17.1")
        await controller._update_locked()
        # Target reached: the fallback clears and the boiler rests again
        # (the flag is read at the start of a pass, so it takes one more).
        states.set("sensor.salon_temp", "19.6")
        await controller._update_locked()
        self.assertFalse(controller.room_states["salon"].fallback)
        await controller._update_locked()
        calls = summarise(services)
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_room_without_sensor_is_left_alone(self):
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        states.set("sensor.salon_temp", "unavailable")
        await controller._update_locked()
        calls = summarise(services)

        # Splits untouched, boiler rests (cuisine is at target).
        self.assertFalse(
            any(
                call[2] in ("switch.salon_gauche", "climate.salon_gauche")
                for call in calls
            )
        )
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )

    async def test_thermostat_room_drives_the_global_decision(self):
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()

        # The virtual thermostat follows the salon, and the decision
        # carries its target.
        self.assertEqual(controller.indoor_sensor, "sensor.salon_temp")
        self.assertEqual(shared.decision.target_setpoint, 19.5)
        self.assertEqual(controller.thermostat_room_target(), 19.5)

    async def test_control_off_clears_decision(self):
        controller, shared, _, _ = build(subentries=self.rooms_subentries())
        self.seed_room_states(states := controller.hass.states)
        await controller._update_locked()
        self.assertIsNotNone(shared.decision)

        shared.set_active(False)
        await controller._update_locked()
        self.assertIsNone(shared.decision)
        self.assertIsNone(shared.house)

    async def test_passed_away_until_ends_away_mode(self):
        controller, shared, _, _ = build(subentries=self.rooms_subentries())
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() - timedelta(minutes=1))
        await controller._update_locked()

        self.assertFalse(shared.away_mode)
        self.assertIsNone(shared.away_until)

    async def test_update_config_applies_settings_live(self):
        controller, shared, services, _ = build(subentries=self.rooms_subentries())
        self.seed_room_states(controller.hass.states)
        await controller._update_locked()
        controller.update_config({**controller.config, "idle_setpoint": 15.0})
        await controller._update_locked()
        calls = summarise(services)

        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 15.0), calls
        )

    async def test_preheating_uses_the_boiler_never_the_splits(self):
        """Regression: pre-heating must not start the splits."""
        controller, shared, services, states = build(
            {"initial_preheat_minutes": 60}, subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        states.set("switch.salon_gauche", "on")
        states.set("switch.salon_droite", "on")
        # Absence ending in 30 min: the pre-heating window (60 min) is open.
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() + timedelta(minutes=30))
        await controller._update_locked()
        calls = summarise(services)

        house = shared.house
        self.assertEqual(house.mode, "away")
        self.assertTrue(house.room("salon").preheating)
        # The splits are forced off and never started.
        self.assertFalse(house.splits_allowed)
        self.assertFalse(house.room("salon").splits_allowed)
        self.assertIn(("switch", "turn_off", "switch.salon_gauche", None, None), calls)
        self.assertFalse(
            any(call[0] == "switch" and call[1] == "turn_on" for call in calls)
        )
        self.assertFalse(
            any(call[2].startswith("climate.salon") for call in calls)
        )
        # The boiler does the pre-heating at the salon's day target.
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 19.5), calls
        )
        self.assertTrue(shared.decision.preheating)

    async def test_preheating_with_splits_in_an_opted_in_room(self):
        """A room opted in pre-heats with its splits (the PAC)."""
        subentries = self.rooms_subentries()
        subentries["salon"].data["preheat_with_splits"] = True
        controller, shared, services, states = build(
            {"initial_preheat_minutes": 60}, subentries=subentries
        )
        self.seed_room_states(states)
        # Absence ending in 30 min: the pre-heating window (60 min) is open.
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() + timedelta(minutes=30))
        await controller._update_locked()
        calls = summarise(services)

        house = shared.house
        self.assertEqual(house.mode, "away")
        self.assertTrue(house.room("salon").preheating)
        self.assertTrue(house.room("salon").splits_allowed)
        self.assertTrue(house.splits_allowed)
        # The splits are started and driven (day target + offset).
        self.assertIn(("switch", "turn_on", "switch.salon_gauche", None, None), calls)
        self.assertIn(("switch", "turn_on", "switch.salon_droite", None, None), calls)
        self.assertIn(
            ("climate", "set_temperature", "climate.salon_gauche", None, 21.5), calls
        )
        self.assertFalse(
            any(call[0] == "switch" and call[1] == "turn_off" for call in calls)
        )
        # The thermostat room pre-heats with the PAC: the boiler rests.
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 12.0), calls
        )
        self.assertTrue(shared.decision.preheating)

    async def test_preheating_with_splits_only_in_the_opted_in_room(self):
        """The PAC pre-heats only the opted-in room; the boiler still does
        the pre-heating when the thermostat room did not opt in."""
        subentries = {
            "salon": FakeSubentry("salon", "Salon", {
                "name": "Salon",
                "temperature_sensor": "sensor.salon_temp",
                "target": 19.5,
                "thermostat_room": True,
                "switches": ["switch.salon_gauche"],
                "climates": ["climate.salon_gauche"],
            }),
            "chambre": FakeSubentry("chambre", "Chambre", {
                "name": "Chambre",
                "temperature_sensor": "sensor.chambre_temp",
                "target": 18.0,
                "switches": ["switch.chambre"],
                "climates": ["climate.chambre"],
                "preheat_with_splits": True,
            }),
        }
        controller, shared, services, states = build(
            {"initial_preheat_minutes": 60}, subentries=subentries
        )
        states.set("sensor.salon_temp", "17.0")
        states.set("sensor.chambre_temp", "16.0")
        states.set("switch.salon_gauche", "off")
        states.set("climate.salon_gauche", "off", dict(CLIMATE_ATTRIBUTES))
        states.set("switch.chambre", "off")
        states.set("climate.chambre", "off", dict(CLIMATE_ATTRIBUTES))
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() + timedelta(minutes=30))
        await controller._update_locked()
        calls = summarise(services)

        house = shared.house
        self.assertTrue(house.room("salon").preheating)
        self.assertFalse(house.room("salon").splits_allowed)
        self.assertTrue(house.room("chambre").preheating)
        self.assertTrue(house.room("chambre").splits_allowed)
        self.assertTrue(house.splits_allowed)
        # The chambre's splits are started (target + offset); the salon's
        # are left off.
        self.assertIn(("switch", "turn_on", "switch.chambre", None, None), calls)
        self.assertIn(
            ("climate", "set_temperature", "climate.chambre", None, 20.0), calls
        )
        self.assertFalse(
            any(call[2] == "switch.salon_gauche" and call[1] == "turn_on" for call in calls)
        )
        self.assertFalse(any(call[2].startswith("climate.salon") for call in calls))
        # The thermostat room did not opt in: the boiler does the
        # pre-heating at the salon's day target.
        self.assertIn(
            ("climate", "set_temperature", "climate.boiler", None, 19.5), calls
        )

    async def test_surplus_notification_on_the_rising_edge(self):
        controller, shared, services, states = build(
            {
                "notify_service": "notify.mobile_app_phone",
                "consumption_sensor": "sensor.conso",
                "surplus_threshold": 500.0,
                "solar_bonus": 0.5,
            },
            production="1500.0",
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("sensor.conso", "1000.0")  # surplus +500 W: bonus on
        await controller._update_locked()
        surplus_calls = [
            c for c in services.calls
            if c[0] == "notify" and "surplus" in c[2].get("message", "").lower()
        ]
        self.assertEqual(len(surplus_calls), 1)
        self.assertIn("+500 W", surplus_calls[0][2]["message"])
        self.assertIn("+0.5", surplus_calls[0][2]["message"])

        # Still active on the next pass: no repeated notification.
        await controller._update_locked()
        surplus_calls = [
            c for c in services.calls
            if c[0] == "notify" and "surplus" in c[2].get("message", "").lower()
        ]
        self.assertEqual(len(surplus_calls), 1)

        # The surplus drops below -threshold: bonus off, no notification.
        states.set("sensor.conso", "2200.0")
        await controller._update_locked()
        self.assertFalse(controller._solar_bonus)
        surplus_calls = [
            c for c in services.calls
            if c[0] == "notify" and "surplus" in c[2].get("message", "").lower()
        ]
        self.assertEqual(len(surplus_calls), 1)

    async def test_no_rooms_means_no_decision(self):
        controller, shared, services, states = build()
        with self.assertLogs(
            "custom_components.heat_controller.controller", level="WARNING"
        ):
            await controller._update_locked()
        self.assertIsNone(shared.decision)
        self.assertIsNone(shared.house)

    async def test_notifications_are_sent_through_the_configured_service(self):
        controller, shared, services, states = build(
            {"notify_service": "notify.mobile_app_phone", "fallback_minutes": 120},
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)

        def notify_calls():
            return [call for call in services.calls if call[0] == "notify"]

        # A stalled split asks the boiler: fallback notification.
        controller.room_states["salon"].fallback = True
        await controller._update_locked()
        fallback_calls = [
            c for c in notify_calls() if "boiler takes over" in c[2]["message"]
        ]
        self.assertEqual(len(fallback_calls), 1)
        self.assertEqual(fallback_calls[0][1], "mobile_app_phone")
        self.assertEqual(fallback_calls[0][2]["title"], "Heat Controller")

        # Away mode turns on with a planned return: one notification.
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() + timedelta(hours=8))
        await controller._update_locked()
        away_calls = [
            c for c in notify_calls() if "Away mode" in c[2]["message"]
        ]
        self.assertEqual(len(away_calls), 1)
        self.assertIn("Planned return", away_calls[0][2]["message"])

        # Away mode ends: another one.
        shared.set_away_mode(False)
        shared.set_away_until(None)
        await controller._update_locked()
        away_calls = [
            c for c in notify_calls() if "Away mode" in c[2]["message"]
        ]
        self.assertEqual(len(away_calls), 2)
        self.assertIn("ended", away_calls[1][2]["message"])

    async def test_no_notification_without_a_configured_service(self):
        controller, shared, services, states = build(
            subentries=self.rooms_subentries()
        )
        self.seed_room_states(states)
        await controller._update_locked()
        shared.set_away_mode(True)
        await controller._update_locked()
        shared.set_away_mode(False)
        await controller._update_locked()
        self.assertFalse(any(call[0] == "notify" for call in services.calls))

    async def test_heating_start_stop_notification_with_source_and_target(self):
        controller, shared, services, states = build(
            {
                "notify_service": "send_message",
                "notify_target": "notify.pierrick_phone",
            },
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("sensor.salon_temp", "19.5")  # at target: no heating yet
        await controller._update_locked()
        self.assertFalse(shared.decision.heating_needed)
        # First pass: no notification (no transition known yet).
        self.assertFalse(any(call[0] == "notify" for call in services.calls))

        # The salon cools down: the splits start, one notification.
        states.set("sensor.salon_temp", "17.0")
        await controller._update_locked()
        self.assertTrue(shared.decision.heating_needed)
        notify_calls = [call for call in services.calls if call[0] == "notify"]
        self.assertEqual(len(notify_calls), 1)
        domain, service, payload, target = notify_calls[0]
        self.assertEqual(service, "send_message")
        self.assertNotIn("target", payload)
        self.assertEqual(target, {"entity_id": "notify.pierrick_phone"})
        self.assertIn("Heating started", payload["message"])
        self.assertIn("Salon", payload["message"])  # the heated room
        self.assertIn("heat pump (splits)", payload["message"])
        self.assertIn("19.5", payload["message"])

        # Still heating on the next pass: no repeated notification.
        await controller._update_locked()
        notify_calls = [call for call in services.calls if call[0] == "notify"]
        self.assertEqual(len(notify_calls), 1)

        # The salon is back at target (past the minimum run time): stop.
        states.set("sensor.salon_temp", "20.0")
        controller.room_states["salon"].last_change = dt_util.now() - timedelta(minutes=6)
        await controller._update_locked()
        self.assertFalse(shared.decision.heating_needed)
        notify_calls = [call for call in services.calls if call[0] == "notify"]
        self.assertEqual(len(notify_calls), 2)
        self.assertIn("Heating stopped", notify_calls[1][2]["message"])
        self.assertIn("Salon", notify_calls[1][2]["message"])

    async def test_heating_start_notification_reports_the_boiler_source(self):
        controller, shared, services, states = build(
            {"notify_service": "notify.mobile_app_phone"},
            outdoor="-2.0",  # too cold for the splits: the boiler heats
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("sensor.cuisine_temp", "19.5")  # no demand yet
        await controller._update_locked()
        self.assertFalse(any(call[0] == "notify" for call in services.calls))

        states.set("sensor.cuisine_temp", "18.0")  # trigger room cold
        await controller._update_locked()
        self.assertTrue(shared.decision.heating_needed)
        notify_calls = [call for call in services.calls if call[0] == "notify"]
        self.assertEqual(len(notify_calls), 1)
        self.assertIn("Heating started", notify_calls[0][2]["message"])
        self.assertIn("Cuisine", notify_calls[0][2]["message"])  # the trigger room
        self.assertIn("boiler", notify_calls[0][2]["message"])
        self.assertNotIn("heat pump", notify_calls[0][2]["message"])

    async def test_notify_target_is_passed_as_the_call_target(self):
        """Regression: a "target" key inside the service data is rejected by
        the notify.send_message schema; the target must be the call target."""
        import voluptuous as vol
        from homeassistant.helpers import config_validation as cv

        controller, shared, services, states = build(
            {
                "notify_service": "send_message",
                "notify_target": "notify.pierrick_phone",
            },
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        shared.set_away_mode(True)
        shared.set_away_until(dt_util.now() + timedelta(hours=8))
        await controller._update_locked()
        notify_calls = [call for call in services.calls if call[0] == "notify"]
        self.assertEqual(len(notify_calls), 1)
        _, service, data, target = notify_calls[0]
        self.assertEqual(service, "send_message")
        self.assertNotIn("target", data)
        self.assertEqual(target, {"entity_id": "notify.pierrick_phone"})
        # What the service registry validates: data + target merged.
        send_message_schema = cv.make_entity_service_schema(
            {vol.Required("message"): cv.string, vol.Optional("title"): cv.string}
        )
        send_message_schema({**data, **(target or {})})

    async def test_notifications_disabled_by_setting(self):
        controller, shared, services, states = build(
            {
                "notify_service": "send_message",
                "notify_target": "notify.pierrick_phone",
                "notifications_enabled": False,
            },
            subentries=self.rooms_subentries(),
        )
        self.seed_room_states(states)
        states.set("sensor.salon_temp", "19.5")
        await controller._update_locked()
        states.set("sensor.salon_temp", "17.0")
        await controller._update_locked()
        states.set("sensor.salon_temp", "20.0")
        controller.room_states["salon"].last_change = dt_util.now() - timedelta(minutes=6)
        await controller._update_locked()
        shared.set_away_mode(True)
        await controller._update_locked()
        self.assertFalse(any(call[0] == "notify" for call in services.calls))

    def test_source_labels_follow_the_configured_language(self):
        controller, shared, services, states = build()
        controller.hass.config.language = "fr"
        self.assertEqual(controller._source_label(True), "pompe à chaleur (splits)")
        self.assertEqual(controller._source_label(False), "chaudière")
        controller.hass.config.language = "en"
        self.assertEqual(controller._source_label(True), "heat pump (splits)")
        self.assertEqual(controller._source_label(False), "boiler")


if __name__ == "__main__":
    unittest.main()
