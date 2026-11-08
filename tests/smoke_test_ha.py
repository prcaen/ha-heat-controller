"""Developer smoke test: imports the component with a real homeassistant.

Usage: python tests/smoke_test_ha.py (requires `pip install homeassistant`,
Python 3.13+ environment). Checks imports, config flow schemas,
controller construction and one full hybrid pass with fake states.
"""

import asyncio  # noqa: E402
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import custom_components.heat_controller as hc  # noqa: E402
from custom_components.heat_controller import (  # noqa: E402
    climate as climate_platform,
    config_flow,
    controller,
    datetime as datetime_platform,
    number,
    select,
    state,
    time as time_platform,
)
from homeassistant.components.climate import HVACAction, HVACMode  # noqa: E402

print("imports OK, domain =", hc.DOMAIN)

schemas = (
    config_flow._sensors_schema({}, None),
    config_flow._devices_schema({}),
    config_flow._settings_schema({}),
)
print("schemas OK:", len(schemas))


class FakeSubentry:
    def __init__(self, subentry_id, title, data):
        self.subentry_id = subentry_id
        self.subentry_type = "room"
        self.title = title
        self.data = data


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
        if domain == "notify":
            return
        entity_id = data["entity_id"]
        if domain == "switch" and service == "turn_on":
            self._states.set(entity_id, "on")
        elif domain == "switch" and service == "turn_off":
            self._states.set(entity_id, "off")
        elif domain == "fan" and service == "turn_on":
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


SUBENTRIES = {
    "salon": FakeSubentry("salon", "Salon", {
        "name": "Salon",
        "temperature_sensor": "sensor.salon",
        "target": 19.5,
        "boiler_trigger": False,
        "thermostat_room": True,
        "switches": ["switch.salon_g", "switch.salon_d"],
        "climates": ["climate.salon_g", "climate.salon_d"],
        "fans": ["fan.salon_g", "fan.salon_d"],
        "fan_preset": "low",  # per-room override of the global preset
    }),
    "cuisine": FakeSubentry("cuisine", "Cuisine", {
        "name": "Cuisine",
        "temperature_sensor": "sensor.cuisine",
        "follow_room": "salon",
        "boiler_trigger": True,
        "thermostat_room": False,
    }),
}

DATA = {
    "outdoor_temp_sensor": "sensor.ext",
    "production_sensor": "sensor.solenso_puissance",
    "consumption_sensor": "sensor.conso",
    "boiler_climate": "climate.netatmo",
    "accessory_fans": ["fan.acc1"],
    # legacy single day period covering the whole day, migrated to the week
    "day_start": "00:00:00",
    "night_start": "23:59:00",
}


async def run_pass():
    fake_states = FakeStates()
    fake_states.set("sensor.ext", "8.0")
    fake_states.set("sensor.salenso_missing", None)
    fake_states.set("sensor.solenso_puissance", "0")
    fake_states.set("sensor.conso", None)
    fake_states.set("sensor.salon", "17.0")   # cold: the splits start
    fake_states.set("sensor.cuisine", "19.5")  # at target
    fake_states.set("climate.netatmo", "heat", {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 19})
    for entity in ("climate.salon_g", "climate.salon_d"):
        fake_states.set(entity, "off", {"target_temp_step": 0.5, "min_temp": 5, "max_temp": 30, "temperature": 20})
    for entity in ("switch.salon_g", "switch.salon_d", "fan.acc1"):
        fake_states.set(entity, "off")

    fake_services = FakeServices(fake_states)
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = dict(DATA)
    entry.subentries = SUBENTRIES
    shared = state.HeatState()
    shared.set_active(True)
    hass = MagicMock()
    hass.states = fake_states
    hass.services = fake_services
    ctrl = controller.HeatController(hass, entry, entry.data, shared)

    real_sleep = controller.asyncio.sleep
    controller.asyncio.sleep = _fast_sleep
    await ctrl._update_locked()
    controller.asyncio.sleep = real_sleep

    summary = [
        (c[0], c[1], c[2]["entity_id"], c[2].get("hvac_mode"), c[2].get("temperature"))
        for c in fake_services.calls
    ]
    for line in summary:
        print("call:", line)

    assert shared.heat_pump_source is True
    assert shared.decision.target_setpoint == 19.5
    assert shared.demand is True
    # splits: switch on first, then heat mode and target + offset (21.5)
    assert ("switch", "turn_on", "switch.salon_g", None, None) in summary
    assert ("climate", "set_hvac_mode", "climate.salon_g", "heat", None) in summary
    assert ("climate", "set_temperature", "climate.salon_g", None, 21.5) in summary
    switch_g = summary.index(("switch", "turn_on", "switch.salon_g", None, None))
    assert summary.index(("climate", "set_hvac_mode", "climate.salon_g", "heat", None)) > switch_g
    # the boiler rests at the idle setpoint; the accessory fan follows
    assert ("climate", "set_temperature", "climate.netatmo", None, 12.0) in summary
    assert ("fan", "turn_on", "fan.acc1", None, None) in summary
    # the salon fans get the per-room preset (low), not the global one (auto)
    assert any(
        c[0] == "fan" and c[1] == "set_preset_mode"
        and c[2]["entity_id"] in ("fan.salon_g", "fan.salon_d")
        and c[2]["preset_mode"] == "low"
        for c in fake_services.calls
    )
    print("mild day pass OK")

    # Second pass: the trigger room gets cold, the boiler takes the demand
    fake_states.set("sensor.cuisine", "19.0")
    fake_services.calls.clear()
    await ctrl._update_locked()
    summary2 = [
        (c[0], c[1], c[2]["entity_id"], c[2].get("hvac_mode"), c[2].get("temperature"))
        for c in fake_services.calls
    ]
    assert shared.house.boiler_demand is True
    assert ("climate", "set_temperature", "climate.netatmo", None, 21.0) in summary2
    print("trigger demand pass OK")

    # Settings entities and the virtual thermostat
    mock_ctl = MagicMock()
    mock_ctl.settings = ctrl.settings
    mock_ctl.config = dict(DATA)
    num = number.HeatSettingNumber(entry, shared, mock_ctl, "night_setpoint")
    assert num.native_value == 16.0
    tim = time_platform.HeatSettingTime(entry, shared, mock_ctl, "day_start_mon")
    assert tim.native_value.hour == 0
    sel = select.HeatSourceOverrideSelect(entry, shared, mock_ctl)
    assert sel.current_option == "auto"
    moment = datetime_platform.HeatAwayUntilEntity(entry, shared, mock_ctl)
    assert moment.native_value is None
    print("settings entities OK")

    thermo = climate_platform.HeatThermostat(entry, shared, mock_ctl)
    assert thermo.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
    assert thermo.hvac_mode == HVACMode.HEAT
    assert thermo.hvac_action == HVACAction.HEATING
    assert thermo.target_temperature == 19.5
    assert ctrl.thermostat_room_target() == 19.5
    assert ctrl.thermostat_room().name == "Salon"
    print("thermostat OK")


async def _fast_sleep(_delay):
    return None


asyncio.run(run_pass())
print("SMOKE TEST DONE")
