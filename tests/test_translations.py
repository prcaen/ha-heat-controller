"""Translation files consistency tests (no homeassistant needed)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

BASE = Path(__file__).parent.parent / "custom_components" / "heat_controller"

_spec = importlib.util.spec_from_file_location(
    "heat_controller_const", str(BASE / "const.py")
)
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)


def load(name: str) -> dict:
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def structure(value):
    """Keys-only skeleton of a JSON document."""
    if isinstance(value, dict):
        return {key: structure(item) for key, item in value.items()}
    return None


class TestTranslations(unittest.TestCase):
    """strings.json is the source of truth; en/fr must match its keys."""

    def setUp(self):
        self.strings = load("strings.json")
        self.en = load("translations/en.json")
        self.fr = load("translations/fr.json")

    def test_files_have_the_same_structure(self):
        self.assertEqual(structure(self.strings), structure(self.en))
        self.assertEqual(structure(self.strings), structure(self.fr))

    def test_settings_keys_are_translated(self):
        for section in ("config", "options"):
            data = self.strings[section]["step"]["settings"]["data"]
            for key in const.SETTING_DEFAULTS:
                self.assertIn(key, data, f"{section} settings missing {key}")

    def test_entity_keys(self):
        self.assertEqual(set(self.strings["entity"]["switch"]), {"active", "away_mode"})
        self.assertEqual(
            set(self.strings["entity"]["binary_sensor"]),
            {
                "heat_pump_source",
                "preheating",
                "heating_demand",
                "day_period",
                "solar_period",
            },
        )
        self.assertEqual(
            set(self.strings["entity"]["sensor"]),
            {"target_setpoint", "preheating_duration", "source"},
        )


if __name__ == "__main__":
    unittest.main()
