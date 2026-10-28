"""Config flow schema tests.

These tests import the real homeassistant package; they are skipped when it
is not installed (the CI integration job installs it).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

try:
    import homeassistant  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("homeassistant is not installed")

import voluptuous as vol

from custom_components.heat_controller import config_flow
from custom_components.heat_controller.const import (
    CONF_ACCESSORY_FANS,
    CONF_BOILER_CLIMATE,
    CONF_FROST_SETPOINT,
    CONF_IDLE_SETPOINT,
    CONF_OUTDOOR_THRESHOLD,
    CONF_ROOM_BOILER_TRIGGER,
    CONF_ROOM_CLIMATES,
    CONF_ROOM_FANS,
    CONF_ROOM_FOLLOW_ROOM,
    CONF_ROOM_NAME,
    CONF_ROOM_SWITCHES,
    CONF_ROOM_TARGET,
    CONF_ROOM_TEMP_SENSOR,
    CONF_ROOM_THERMOSTAT_ROOM,
    SETTING_DEFAULTS,
)


def schema_keys(schema: vol.Schema) -> set[str]:
    """Keys declared by a voluptuous schema."""
    return {
        key.schema if isinstance(key, vol.Marker) else key for key in schema.schema
    }


def as_number(value):
    """Numbers may come back as str or float depending on the selector."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


class TestSchemas(unittest.TestCase):
    """Schema keys, defaults and validation bounds."""

    def test_sensors_schema_keys(self):
        keys = schema_keys(config_flow._sensors_schema({}, None))
        self.assertEqual(
            keys,
            {
                "outdoor_temp_sensor",
                "indoor_temp_sensor",
                "production_sensor",
                "consumption_sensor",
            },
        )

    def test_devices_schema_keys(self):
        keys = schema_keys(config_flow._devices_schema({}))
        self.assertEqual(
            keys,
            {
                CONF_BOILER_CLIMATE,
                "heat_pump_climates",
                "heat_pump_switches",
                CONF_ACCESSORY_FANS,
            },
        )

    def test_settings_schema_defaults(self):
        result = config_flow._settings_schema({})({})
        for key, value in SETTING_DEFAULTS.items():
            self.assertEqual(as_number(result[key]), as_number(value), key)
        self.assertEqual(as_number(result[CONF_IDLE_SETPOINT]), 12.0)

    def test_outdoor_threshold_bounds(self):
        factory = config_flow._settings_schema(dict(SETTING_DEFAULTS))
        payload = dict(SETTING_DEFAULTS)
        factory({**payload, CONF_OUTDOOR_THRESHOLD: -5.0})
        factory({**payload, CONF_OUTDOOR_THRESHOLD: 10.0})
        with self.assertRaises(vol.Invalid):
            factory({**payload, CONF_OUTDOOR_THRESHOLD: -5.5})
        with self.assertRaises(vol.Invalid):
            factory({**payload, CONF_OUTDOOR_THRESHOLD: 10.5})

    def test_frost_setpoint_default_and_bounds(self):
        result = config_flow._settings_schema({})({})
        self.assertEqual(as_number(result[CONF_FROST_SETPOINT]), 7.0)
        factory = config_flow._settings_schema(dict(SETTING_DEFAULTS))
        payload = dict(SETTING_DEFAULTS)
        factory({**payload, CONF_FROST_SETPOINT: 15.0})
        with self.assertRaises(vol.Invalid):
            factory({**payload, CONF_FROST_SETPOINT: 15.5})

    def test_solenso_sensor_without_integration(self):
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = []
        self.assertIsNone(config_flow._solenso_power_sensor(hass))

    def test_room_schema_keys(self):
        keys = schema_keys(config_flow._room_schema([("s1", "Salon")]))
        self.assertEqual(
            keys,
            {
                CONF_ROOM_NAME,
                CONF_ROOM_TEMP_SENSOR,
                CONF_ROOM_TARGET,
                CONF_ROOM_FOLLOW_ROOM,
                CONF_ROOM_BOILER_TRIGGER,
                CONF_ROOM_THERMOSTAT_ROOM,
                CONF_ROOM_SWITCHES,
                CONF_ROOM_CLIMATES,
                CONF_ROOM_FANS,
            },
        )
        # No followable room yet: the follow selector is absent.
        keys = schema_keys(config_flow._room_schema([]))
        self.assertNotIn(CONF_ROOM_FOLLOW_ROOM, keys)

    def test_validate_room_input(self):
        valid = {
            CONF_ROOM_NAME: "Salon",
            CONF_ROOM_TEMP_SENSOR: "sensor.salon",
            CONF_ROOM_TARGET: 19.5,
        }
        self.assertEqual(config_flow.validate_room_input(valid), {})
        follow = {
            CONF_ROOM_NAME: "Cuisine",
            CONF_ROOM_TEMP_SENSOR: "sensor.cuisine",
            CONF_ROOM_FOLLOW_ROOM: "s1",
        }
        self.assertEqual(config_flow.validate_room_input(follow), {})
        # Neither a target nor a room to follow.
        self.assertEqual(
            config_flow.validate_room_input(
                {CONF_ROOM_NAME: "X", CONF_ROOM_TEMP_SENSOR: "sensor.x"}
            ),
            {"base": "target_required"},
        )
        # Splits without a climate entity.
        self.assertEqual(
            config_flow.validate_room_input(
                {**valid, CONF_ROOM_SWITCHES: ["switch.a"]}
            ),
            {"base": "climate_required"},
        )


class TestSubentrySupport(unittest.TestCase):
    """Sub-entry types declared to Home Assistant."""

    def test_room_subentry_supported(self):
        supported = config_flow.HeatControllerConfigFlow.async_get_supported_subentry_types(
            None
        )
        self.assertIn("room", supported)
        self.assertIs(supported["room"], config_flow.RoomSubentryFlow)


if __name__ == "__main__":
    unittest.main()
