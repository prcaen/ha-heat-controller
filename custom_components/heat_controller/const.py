"""Heat controller constants."""

from typing import Any

DOMAIN = "heat_controller"

PLATFORMS = [
    "binary_sensor",
    "climate",
    "datetime",
    "number",
    "select",
    "sensor",
    "switch",
    "time",
]

# --- Entity selection --------------------------------------------------------

CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_PRODUCTION_SENSOR = "production_sensor"
CONF_CONSUMPTION_SENSOR = "consumption_sensor"
CONF_BOILER_CLIMATE = "boiler_climate"
CONF_ACCESSORY_FANS = "accessory_fans"

# --- Rooms (config sub-entries) ----------------------------------------------

SUBENTRY_ROOM = "room"
CONF_ROOM_NAME = "name"
CONF_ROOM_TEMP_SENSOR = "temperature_sensor"
CONF_ROOM_TARGET = "target"
CONF_ROOM_FOLLOW_ROOM = "follow_room"
CONF_ROOM_THERMOSTAT_ROOM = "thermostat_room"
CONF_ROOM_SWITCHES = "switches"
CONF_ROOM_CLIMATES = "climates"
CONF_ROOM_FANS = "fans"
CONF_ROOM_FAN_PRESET = "fan_preset"

# --- Settings ----------------------------------------------------------------

CONF_OUTDOOR_THRESHOLD = "outdoor_threshold"
CONF_NIGHT_SETPOINT = "night_setpoint"
CONF_AWAY_SETPOINT = "away_setpoint"
CONF_IDLE_SETPOINT = "idle_setpoint"
CONF_FROST_SETPOINT = "frost_setpoint"
CONF_HYSTERESIS = "hysteresis"
CONF_PRODUCTION_THRESHOLD = "production_threshold"
CONF_DAY_START = "day_start"
CONF_NIGHT_START = "night_start"
CONF_RETURN_TIME = "return_time"

# Hybrid (multi-room) settings.
CONF_SPLIT_OFFSET = "split_offset"
CONF_BOILER_DEMAND_SETPOINT = "boiler_demand_setpoint"
CONF_SPLIT_FAN_PRESET = "split_fan_preset"
CONF_SPLIT_FAN_OSCILLATING = "split_fan_oscillating"
CONF_SOLAR_BONUS = "solar_bonus"
CONF_SURPLUS_THRESHOLD = "surplus_threshold"
CONF_FALLBACK_MINUTES = "fallback_minutes"
CONF_MIN_CYCLE_MINUTES = "min_cycle_minutes"
CONF_INITIAL_PREHEAT_MINUTES = "initial_preheat_minutes"
CONF_MAX_PREHEAT_MINUTES = "max_preheat_minutes"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_NOTIFY_SERVICE = "notify_service"

DEFAULT_OUTDOOR_THRESHOLD = 3.0
DEFAULT_NIGHT_SETPOINT = 16.0
DEFAULT_AWAY_SETPOINT = 12.0
DEFAULT_IDLE_SETPOINT = 12.0
DEFAULT_FROST_SETPOINT = 7.0
DEFAULT_HYSTERESIS = 0.3
DEFAULT_PRODUCTION_THRESHOLD = 500.0
DEFAULT_DAY_START = "07:00:00"
DEFAULT_NIGHT_START = "22:00:00"
DEFAULT_RETURN_TIME = "18:00:00"

DEFAULT_SPLIT_OFFSET = 2.0
DEFAULT_BOILER_DEMAND_SETPOINT = 21.0
DEFAULT_SPLIT_FAN_PRESET = "auto"
DEFAULT_SPLIT_FAN_OSCILLATING = True
DEFAULT_SOLAR_BONUS = 0.5
DEFAULT_SURPLUS_THRESHOLD = 500.0
DEFAULT_FALLBACK_MINUTES = 120
DEFAULT_MIN_CYCLE_MINUTES = 5
DEFAULT_INITIAL_PREHEAT_MINUTES = 60
DEFAULT_MAX_PREHEAT_MINUTES = 180

SPLIT_FAN_PRESET_OPTIONS = ("low", "medium", "high", "auto")

# Per-room override of the split fan preset: "inherit" uses the global
# `split_fan_preset` setting.
ROOM_FAN_PRESET_INHERIT = "inherit"
ROOM_FAN_PRESET_OPTIONS = (ROOM_FAN_PRESET_INHERIT, *SPLIT_FAN_PRESET_OPTIONS)

# --- Config entry layout -----------------------------------------------------

# Keys describing the controlled installation (stored in the entry data).
CONNECTION_KEYS = (
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PRODUCTION_SENSOR,
    CONF_CONSUMPTION_SENSOR,
    CONF_BOILER_CLIMATE,
    CONF_ACCESSORY_FANS,
)

# Default settings exposed in the config flow (stored in the entry options
# once the entry has been reconfigured through the options flow).
SETTING_DEFAULTS = {
    CONF_NIGHT_SETPOINT: DEFAULT_NIGHT_SETPOINT,
    CONF_AWAY_SETPOINT: DEFAULT_AWAY_SETPOINT,
    CONF_IDLE_SETPOINT: DEFAULT_IDLE_SETPOINT,
    CONF_FROST_SETPOINT: DEFAULT_FROST_SETPOINT,
    CONF_OUTDOOR_THRESHOLD: DEFAULT_OUTDOOR_THRESHOLD,
    CONF_HYSTERESIS: DEFAULT_HYSTERESIS,
    CONF_PRODUCTION_THRESHOLD: DEFAULT_PRODUCTION_THRESHOLD,
    CONF_DAY_START: DEFAULT_DAY_START,
    CONF_NIGHT_START: DEFAULT_NIGHT_START,
    CONF_RETURN_TIME: DEFAULT_RETURN_TIME,
    CONF_SPLIT_OFFSET: DEFAULT_SPLIT_OFFSET,
    CONF_BOILER_DEMAND_SETPOINT: DEFAULT_BOILER_DEMAND_SETPOINT,
    CONF_SPLIT_FAN_PRESET: DEFAULT_SPLIT_FAN_PRESET,
    CONF_SPLIT_FAN_OSCILLATING: DEFAULT_SPLIT_FAN_OSCILLATING,
    CONF_SOLAR_BONUS: DEFAULT_SOLAR_BONUS,
    CONF_SURPLUS_THRESHOLD: DEFAULT_SURPLUS_THRESHOLD,
    CONF_FALLBACK_MINUTES: DEFAULT_FALLBACK_MINUTES,
    CONF_MIN_CYCLE_MINUTES: DEFAULT_MIN_CYCLE_MINUTES,
    CONF_INITIAL_PREHEAT_MINUTES: DEFAULT_INITIAL_PREHEAT_MINUTES,
    CONF_MAX_PREHEAT_MINUTES: DEFAULT_MAX_PREHEAT_MINUTES,
    CONF_WEATHER_ENTITY: "",
    CONF_NOTIFY_SERVICE: "",
}

# --- Settings entities -------------------------------------------------------

# Numeric settings: bounds and units shared by the config flow selectors and
# the dashboard `number` entities. The `mode` key ("box") matches the
# selector mode; `device_class` is a NumberDeviceClass value.
SETTING_NUMBERS: dict[str, dict[str, Any]] = {
    CONF_NIGHT_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_AWAY_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_IDLE_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_FROST_SETPOINT: {
        "min": 3.0, "max": 15.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_OUTDOOR_THRESHOLD: {
        "min": -5.0, "max": 10.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_HYSTERESIS: {"min": 0.0, "max": 5.0, "step": 0.1, "unit": "°C"},
    CONF_PRODUCTION_THRESHOLD: {
        "min": 0.0, "max": 20000.0, "step": 50.0, "unit": "W",
        "device_class": "power", "mode": "box",
    },
    CONF_SPLIT_OFFSET: {
        "min": 0.0, "max": 10.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_BOILER_DEMAND_SETPOINT: {
        "min": 10.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_SOLAR_BONUS: {
        "min": 0.0, "max": 3.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_SURPLUS_THRESHOLD: {
        "min": 0.0, "max": 20000.0, "step": 50.0, "unit": "W",
        "device_class": "power", "mode": "box",
    },
    CONF_FALLBACK_MINUTES: {
        "min": 10.0, "max": 480.0, "step": 5.0, "unit": "min",
        "device_class": "duration", "mode": "box",
    },
    CONF_MIN_CYCLE_MINUTES: {
        "min": 0.0, "max": 60.0, "step": 1.0, "unit": "min",
        "device_class": "duration",
    },
    CONF_INITIAL_PREHEAT_MINUTES: {
        "min": 0.0, "max": 480.0, "step": 5.0, "unit": "min",
        "device_class": "duration",
    },
    CONF_MAX_PREHEAT_MINUTES: {
        "min": 0.0, "max": 480.0, "step": 5.0, "unit": "min",
        "device_class": "duration",
    },
}

# Entity settings: configured in the flow, no dashboard entity.
SETTING_ENTITIES: dict[str, dict[str, Any]] = {
    CONF_WEATHER_ENTITY: {"domain": "weather"},
}

# Free-text settings: configured in the flow, no dashboard entity.
SETTING_TEXTS: dict[str, dict[str, Any]] = {
    CONF_NOTIFY_SERVICE: {},
}

# String settings exposed as `select` entities.
SETTING_SELECTS: dict[str, dict[str, Any]] = {
    CONF_SPLIT_FAN_PRESET: {"options": list(SPLIT_FAN_PRESET_OPTIONS)},
}

# Boolean settings exposed as `switch` entities.
SETTING_BOOLEANS: dict[str, dict[str, Any]] = {
    CONF_SPLIT_FAN_OSCILLATING: {},
}

# Time settings exposed as `time` entities.
SETTING_TIMES = (
    CONF_DAY_START,
    CONF_NIGHT_START,
    CONF_RETURN_TIME,
)

# --- Behavior -----------------------------------------------------------------

POLL_INTERVAL_SECONDS = 60  # Heartbeat every minute (decisions are idempotent)
STARTUP_DELAY_SECONDS = 5  # wait after startup for states to restore
SERVICE_TIMEOUT_SECONDS = 15  # max delay of a service call
HVAC_MODE_DELAY_SECONDS = 5  # delay after HVAC mode change
SWITCH_WAIT_TIMEOUT_SECONDS = 20  # wait for a power switch to turn on
