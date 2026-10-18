"""Smoke test développeur : importe le composant avec un vrai homeassistant.

Usage : python tests/smoke_test_ha.py (nécessite `pip install homeassistant`,
environnement Python 3.13+). Vérifie imports, schémas du flux de configuration
et construction du contrôleur.
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
    "pac_climates": ["climate.g", "climate.d"],
    "pac_fans": ["fan.g", "fan.d"],
    "accessory_fans": ["fan.acc1"],
    "consigne_jour": 20,
    "consigne_nuit": 16,
    "heure_jour": "07:00:00",
}
shared = state.HeatState()
hass = MagicMock()
ctrl = controller.HeatController(hass, entry, entry.data, shared)
assert ctrl.pac_fans == ["fan.g", "fan.d"]
assert ctrl.settings.consigne_jour == 20.0
assert ctrl.settings.heure_jour.total_seconds() == 7 * 3600
print("controller OK:", ctrl.settings.seuil_exterieur, ctrl.pac_fans)

entry.data["pac_fans"] = ["fan.g"]
ctrl2 = controller.HeatController(hass, entry, entry.data, shared)
assert ctrl2.pac_fans == ["fan.g", None]
print("pairing OK")

inputs = logic.Inputs(
    now=datetime(2026, 1, 15, 12, 0, 0),
    temp_ext=8.0,
    temp_int=19.0,
    production=650.0,
    actif=True,
    mode_absent=False,
    source_pac=False,
)
decision = logic.compute(ctrl.settings, inputs)
assert decision.bascule and decision.doit_utiliser_pac
assert decision.en_periode_solaire and decision.consigne_cible == 22.0
print("decision OK:", decision)

# --- Simulation d'une passe complète avec états et services factices -------
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
        # simule l'effet du service sur l'état
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
    entry.data["pac_fans"] = ["fan.g", "fan.d"]
    # fenêtres jour et solaire sur toute la journée : test indépendant de l'heure
    entry.data["heure_jour"] = "00:00:00"
    entry.data["heure_nuit"] = "23:59:00"
    entry.data["heure_solaire_debut"] = "00:00:00"
    entry.data["heure_solaire_fin"] = "23:59:00"
    shared = state.HeatState()
    shared.set_actif(True)
    shared.set_source_pac(False)
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

    assert shared.source_pac is True
    assert shared.decision.consigne_cible == 22.0
    assert shared.demande is True
    # bascule vers PAC : netatmo off, acc on, puis clims heat
    assert summary[0] == ("climate", "set_hvac_mode", "climate.netatmo", "off", None)
    assert summary[1] == ("fan", "turn_on", "fan.acc1", None, None)
    assert ("climate", "set_hvac_mode", "climate.g", "heat", None) in summary
    assert ("climate", "set_hvac_mode", "climate.d", "heat", None) in summary
    # programme : ventilateurs allumés, consigne 22 poussée sur les clims
    assert ("fan", "turn_on", "fan.g", None, None) in summary
    assert ("fan", "turn_on", "fan.d", None, None) in summary
    assert ("climate", "set_temperature", "climate.g", None, 22.0) in summary
    assert ("climate", "set_temperature", "climate.d", None, 22.0) in summary
    print("passe PAC OK")

    # Deuxième passe : il fait froid, retour à la chaudière, plus de besoin
    fake_states.set("sensor.ext", "5.0")
    fake_states.set("sensor.int", "22.5")
    fake_services.calls.clear()
    await ctrl._update_locked()

    summary2 = [(c[0], c[1], c[2]["entity_id"], c[2].get("hvac_mode"), c[2].get("temperature")) for c in fake_services.calls]
    for line in summary2:
        print("call:", line)
    assert shared.source_pac is False
    assert shared.demande is False
    # bascule vers chaudière : clims off, tous les ventilateurs PAC off, netatmo auto
    assert ("climate", "set_hvac_mode", "climate.g", "off", None) in summary2
    assert ("climate", "set_hvac_mode", "climate.d", "off", None) in summary2
    assert ("fan", "turn_off", "fan.acc1", None, None) in summary2
    assert ("fan", "turn_off", "fan.g", None, None) in summary2
    assert ("fan", "turn_off", "fan.d", None, None) in summary2
    assert ("climate", "set_hvac_mode", "climate.netatmo", "auto", None) in summary2
    # programme : pas de besoin -> chaudière coupée, aucune consigne poussée
    assert ("climate", "set_hvac_mode", "climate.netatmo", "off", None) in summary2
    assert not any(c[1] == "set_temperature" for c in summary2)
    print("passe chaudière OK")


async def _fast_sleep(_delay):
    return None


asyncio.run(run_pass())
print("SMOKE TEST TERMINÉ")
