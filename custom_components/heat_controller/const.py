"""Heat controller constants."""

DOMAIN = "heat_controller"

PLATFORMS = ["binary_sensor", "sensor", "switch"]

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
    CONF_OUTDOOR_THRESHOLD: DEFAULT_OUTDOOR_THRESHOLD,
    CONF_DAY_SETPOINT: DEFAULT_DAY_SETPOINT,
    CONF_NIGHT_SETPOINT: DEFAULT_NIGHT_SETPOINT,
    CONF_AWAY_SETPOINT: DEFAULT_AWAY_SETPOINT,
    CONF_SOLAR_SETPOINT: DEFAULT_SOLAR_SETPOINT,
    CONF_IDLE_SETPOINT: DEFAULT_IDLE_SETPOINT,
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

# --- Behavior -----------------------------------------------------------------

POLL_INTERVAL_SECONDS = 60  # Heartbeat every minute (decisions are idempotent)
STARTUP_DELAY_SECONDS = 5  # wait after startup for states to restore
SERVICE_TIMEOUT_SECONDS = 15  # max delay of a service call
CHANGEOVER_FAN_DELAY_SECONDS = 15  # accessory fans -> heat pump clims delay (original YAML)
HVAC_MODE_DELAY_SECONDS = 5  # delay after HVAC mode change (original YAML)
FAN_WAIT_TIMEOUT_SECONDS = 20  # wait for a fan to turn on (original YAML)
