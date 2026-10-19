"""Developer smoke test: imports the component with a real homeassistant.

Usage: python tests/smoke_test_ha.py (requires `pip install homeassistant`,
Python 3.13+ environment). Checks imports, config flow schemas
and controller construction.
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import custom_components.heat_controller as hc  # noqa: E402
from custom_components.heat_controller import (  # noqa: E402
    binary_sensor,
    config_flow,
    controller,
    entity,
    logic,
    sensor,
    state,
    switch,
)

print("imports OK, domain =", hc.DOMAIN)

schemas = (
    config_flow._sensors_schema({}, None),
    config_flow._devices_schema({}),
    config_flow._settings_schema({}),
)
print("schemas OK:", len(schemas))

entry = MagicMock()
entry.entry_id = "test"
entry.data = {
    "outdoor_temp_sensor": "sensor.ext",
    "indoor_temp_sensor": "sensor.int",
    "production_sensor": "sensor.solenso_puissance",
    "boiler_climate": "climate.netatmo",
    "heat_pump_climates": ["climate.g", "climate.d"],
    "heat_pump_fans": ["fan.g", "fan.d"],
    "accessory_fans": ["fan.acc1"],
    "day_setpoint": 20,
    "night_setpoint": 16,
    "day_start": "07:00:00",
}
shared = state.HeatState()
hass = MagicMock()
ctrl = controller.HeatController(hass, entry, entry.data, shared)
assert ctrl.heat_pump_fans == ["fan.g", "fan.d"]
assert ctrl.settings.day_setpoint == 20.0
assert ctrl.settings.day_start.total_seconds() == 7 * 3600
print("controller OK:", ctrl.settings.outdoor_threshold, ctrl.heat_pump_fans)

entry.data["heat_pump_fans"] = ["fan.g"]
ctrl2 = controller.HeatController(hass, entry, entry.data, shared)
assert ctrl2.heat_pump_fans == ["fan.g", None]
print("pairing OK")

inputs = logic.Inputs(
    now=datetime(2026, 1, 15, 12, 0, 0),
    outdoor_temp=8.0,
    indoor_temp=19.0,
    production=650.0,
    active=True,
    away_mode=False,
    heat_pump_source=False,
)
decision = logic.compute(ctrl.settings, inputs)
assert decision.changeover and decision.should_use_heat_pump
assert decision.in_solar_period and decision.target_setpoint == 22.0
print("decision OK:", decision)

# --- Simulation of a full pass with fake states and services ---------------
import asyncio  # noqa: E402

from custom_components.heat_controller import controller as ctrl_mod  # noqa: E402


class FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self):
        self._states = {}

    def set(self, entity_id, state, attributes=None):
        self._states[entity_id] = FakeState(state, attributes)

    def get(self, entity_id):
        return self._states.get(entity_id)

    def is_state(self, entity_id, value):
        state = self._states.get(entity_id)
        return state is not None and state.state == value


class FakeServices:
    def __init__(self, states):
        self.calls = []
        self._states = states

    async def async_call(self, domain, service, data, blocking=True):
        self.calls.append((domain, service, dict(data)))
        # simulate the effect of the service on the state
        entity_id = data["entity_id"]
        if domain == "fan" and service == "turn_on":
            self._states.set(entity_id, "on")
        elif domain == "fan" and service == "turn_off":
            self._states.set(entity_id, "off")
        elif domain == "climate" and service == "set_hvac_mode":
            self._states.set(entity_id, data["hvac_mode"], {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 20})
        elif domain == "climate" and service == "set_temperature":
            state = self._states.get(entity_id)
            attributes = dict(state.attributes) if state else {}
            attributes["temperature"] = data["temperature"]
            self._states.set(entity_id, state.state, attributes)


async def run_pass():
    fake_states = FakeStates()
    fake_states.set("sensor.ext", "8.0")
    fake_states.set("sensor.int", "19.0")
    fake_states.set("sensor.solenso_puissance", "650")
    fake_states.set("climate.netatmo", "auto", {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 19})
    fake_states.set("climate.g", "off", {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 20})
    fake_states.set("climate.d", "off", {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 20})
    fake_states.set("fan.g", "off")
    fake_states.set("fan.d", "off")
    fake_states.set("fan.acc1", "off")

    fake_services = FakeServices(fake_states)
    entry.data["heat_pump_fans"] = ["fan.g", "fan.d"]
    # day and solar windows covering the whole day: time-independent test
    entry.data["day_start"] = "00:00:00"
    entry.data["night_start"] = "23:59:00"
    entry.data["solar_start"] = "00:00:00"
    entry.data["solar_end"] = "23:59:00"
    shared = state.HeatState()
    shared.set_active(True)
    shared.set_heat_pump_source(False)
    hass = MagicMock()
    hass.states = fake_states
    hass.services = fake_services
    ctrl = controller.HeatController(hass, entry, entry.data, shared)

    real_sleep = ctrl_mod.asyncio.sleep
    ctrl_mod.asyncio.sleep = _fast_sleep  # noqa type: ignore

    await ctrl._update_locked()
    ctrl_mod.asyncio.sleep = real_sleep

    summary = [(c[0], c[1], c[2]["entity_id"], c[2].get("hvac_mode"), c[2].get("temperature")) for c in fake_services.calls]
    for line in summary:
        print("call:", line)

    assert shared.heat_pump_source is True
    assert shared.decision.target_setpoint == 22.0
    assert shared.demand is True
    # changeover to heat pump: netatmo off, acc on, then clims heat
    assert summary[0] == ("climate", "set_hvac_mode", "climate.netatmo", "off", None)
    assert summary[1] == ("fan", "turn_on", "fan.acc1", None, None)
    assert ("climate", "set_hvac_mode", "climate.g", "heat", None) in summary
    assert ("climate", "set_hvac_mode", "climate.d", "heat", None) in summary
    # program: fans turned on, 22 setpoint pushed to the clims
    assert ("fan", "turn_on", "fan.g", None, None) in summary
    assert ("fan", "turn_on", "fan.d", None, None) in summary
    assert ("climate", "set_temperature", "climate.g", None, 22.0) in summary
    assert ("climate", "set_temperature", "climate.d", None, 22.0) in summary
    print("heat pump pass OK")

    # Second pass: it is cold, changeover back to the boiler, no more demand
    fake_states.set("sensor.ext", "2.0")
    fake_states.set("sensor.int", "22.5")
    fake_services.calls.clear()
    await ctrl._update_locked()

    summary2 = [(c[0], c[1], c[2]["entity_id"], c[2].get("hvac_mode"), c[2].get("temperature")) for c in fake_services.calls]
    for line in summary2:
        print("call:", line)
    assert shared.heat_pump_source is False
    assert shared.demand is False
    # changeover to boiler: clims off, all heat pump fans off, netatmo auto
    assert ("climate", "set_hvac_mode", "climate.g", "off", None) in summary2
    assert ("climate", "set_hvac_mode", "climate.d", "off", None) in summary2
    assert ("fan", "turn_off", "fan.acc1", None, None) in summary2
    assert ("fan", "turn_off", "fan.g", None, None) in summary2
    assert ("fan", "turn_off", "fan.d", None, None) in summary2
    assert ("climate", "set_hvac_mode", "climate.netatmo", "auto", None) in summary2
    # program: no demand -> thermostat kept on but reduced to 12, no clims touched
    assert ("climate", "set_temperature", "climate.netatmo", None, 12.0) in summary2
    assert not any(
        c[1] == "set_temperature" and c[2] in ("climate.g", "climate.d")
        for c in summary2
    )
    print("boiler pass OK")


async def _fast_sleep(_delay):
    return None


asyncio.run(run_pass())
print("SMOKE TEST DONE")