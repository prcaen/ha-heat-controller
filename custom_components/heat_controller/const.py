"""Heat controller constants."""

from typing import Any

DOMAIN = "heat_controller"

PLATFORMS = [
    "binary_sensor",
    "datetime",
    "number",
    "select",
    "sensor",
    "switch",
    "time",
]

# --- Entity selection --------------------------------------------------------

CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_INDOOR_TEMP_SENSOR = "indoor_temp_sensor"
CONF_PRODUCTION_SENSOR = "production_sensor"
CONF_BOILER_CLIMATE = "boiler_climate"
CONF_HEAT_PUMP_CLIMATES = "heat_pump_climates"
CONF_HEAT_PUMP_FANS = "heat_pump_fans"
CONF_ACCESSORY_FANS = "accessory_fans"

# --- Settings ----------------------------------------------------------------

CONF_OUTDOOR_THRESHOLD = "outdoor_threshold"
CONF_DAY_SETPOINT = "day_setpoint"
CONF_NIGHT_SETPOINT = "night_setpoint"
CONF_AWAY_SETPOINT = "away_setpoint"
CONF_SOLAR_SETPOINT = "solar_setpoint"
CONF_IDLE_SETPOINT = "idle_setpoint"
CONF_FROST_SETPOINT = "frost_setpoint"
CONF_HYSTERESIS = "hysteresis"
CONF_PRODUCTION_THRESHOLD = "production_threshold"
CONF_HEAT_PUMP_RATE = "heat_pump_rate"
CONF_BOILER_RATE = "boiler_rate"
CONF_SAFETY_MARGIN = "safety_margin"
CONF_DAY_START = "day_start"
CONF_NIGHT_START = "night_start"
CONF_RETURN_TIME = "return_time"
CONF_SOLAR_START = "solar_start"
CONF_SOLAR_END = "solar_end"

# Default values taken from the original YAML automations.
DEFAULT_OUTDOOR_THRESHOLD = 3.0
DEFAULT_DAY_SETPOINT = 20.0
DEFAULT_NIGHT_SETPOINT = 16.0
DEFAULT_AWAY_SETPOINT = 12.0
DEFAULT_SOLAR_SETPOINT = 22.0
DEFAULT_IDLE_SETPOINT = 12.0
DEFAULT_FROST_SETPOINT = 7.0
DEFAULT_HYSTERESIS = 0.3
DEFAULT_PRODUCTION_THRESHOLD = 500.0
DEFAULT_HEAT_PUMP_RATE = 1.0
DEFAULT_BOILER_RATE = 1.0
DEFAULT_SAFETY_MARGIN = 15
DEFAULT_DAY_START = "07:00:00"
DEFAULT_NIGHT_START = "22:00:00"
DEFAULT_RETURN_TIME = "18:00:00"
DEFAULT_SOLAR_START = "11:00:00"
DEFAULT_SOLAR_END = "15:00:00"

DEFAULT_INDOOR_TEMP = 19.0  # fallback if the indoor sensor is silent
DEFAULT_PRODUCTION = 0.0  # fallback if the production sensor is silent

# --- Config entry layout -----------------------------------------------------

# Keys describing the controlled installation (stored in the entry data).
CONNECTION_KEYS = (
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_PRODUCTION_SENSOR,
    CONF_BOILER_CLIMATE,
    CONF_HEAT_PUMP_CLIMATES,
    CONF_HEAT_PUMP_FANS,
    CONF_ACCESSORY_FANS,
)

# Default settings exposed in the config flow (stored in the entry options
# once the entry has been reconfigured through the options flow).
SETTING_DEFAULTS = {
    CONF_DAY_SETPOINT: DEFAULT_DAY_SETPOINT,
    CONF_NIGHT_SETPOINT: DEFAULT_NIGHT_SETPOINT,
    CONF_AWAY_SETPOINT: DEFAULT_AWAY_SETPOINT,
    CONF_SOLAR_SETPOINT: DEFAULT_SOLAR_SETPOINT,
    CONF_IDLE_SETPOINT: DEFAULT_IDLE_SETPOINT,
    CONF_FROST_SETPOINT: DEFAULT_FROST_SETPOINT,
    CONF_OUTDOOR_THRESHOLD: DEFAULT_OUTDOOR_THRESHOLD,
    CONF_HYSTERESIS: DEFAULT_HYSTERESIS,
    CONF_PRODUCTION_THRESHOLD: DEFAULT_PRODUCTION_THRESHOLD,
    CONF_HEAT_PUMP_RATE: DEFAULT_HEAT_PUMP_RATE,
    CONF_BOILER_RATE: DEFAULT_BOILER_RATE,
    CONF_SAFETY_MARGIN: DEFAULT_SAFETY_MARGIN,
    CONF_DAY_START: DEFAULT_DAY_START,
    CONF_NIGHT_START: DEFAULT_NIGHT_START,
    CONF_RETURN_TIME: DEFAULT_RETURN_TIME,
    CONF_SOLAR_START: DEFAULT_SOLAR_START,
    CONF_SOLAR_END: DEFAULT_SOLAR_END,
}

# --- Settings entities -------------------------------------------------------

# Numeric settings: bounds and units shared by the config flow selectors and
# the dashboard `number` entities. The `mode` key ("box") matches the
# selector mode; `device_class` is a NumberDeviceClass value.
SETTING_NUMBERS: dict[str, dict[str, Any]] = {
    CONF_DAY_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_NIGHT_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_AWAY_SETPOINT: {
        "min": 5.0, "max": 30.0, "step": 0.5, "unit": "°C",
        "device_class": "temperature",
    },
    CONF_SOLAR_SETPOINT: {
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
    CONF_HEAT_PUMP_RATE: {"min": 0.0, "max": 10.0, "step": 0.1, "unit": "°C/h"},
    CONF_BOILER_RATE: {"min": 0.0, "max": 10.0, "step": 0.1, "unit": "°C/h"},
    CONF_SAFETY_MARGIN: {
        "min": 0.0, "max": 240.0, "step": 5.0, "unit": "min",
        "device_class": "duration", "mode": "box",
    },
}

# Time settings exposed as `time` entities.
SETTING_TIMES = (
    CONF_DAY_START,
    CONF_NIGHT_START,
    CONF_RETURN_TIME,
    CONF_SOLAR_START,
    CONF_SOLAR_END,
)

# --- Behavior -----------------------------------------------------------------

POLL_INTERVAL_SECONDS = 60  # Heartbeat every minute (decisions are idempotent)
STARTUP_DELAY_SECONDS = 5  # wait after startup for states to restore
SERVICE_TIMEOUT_SECONDS = 15  # max delay of a service call
CHANGEOVER_FAN_DELAY_SECONDS = 15  # accessory fans -> heat pump clims delay (original YAML)
HVAC_MODE_DELAY_SECONDS = 5  # delay after HVAC mode change (original YAML)
FAN_WAIT_TIMEOUT_SECONDS = 20  # wait for a fan to turn on (original YAML)
