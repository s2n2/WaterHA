"""Constants for WaterHA."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "waterha"
VERSION = "0.1.1"

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

SUBENTRY_TAP = "tap"

# Hub (config entry) options
CONF_FORECAST_SOURCE = "forecast_source"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SEQUENTIAL = "sequential"
SOURCE_OPEN_METEO = "open_meteo"
SOURCE_WEATHER = "weather_entity"

# Tap (subentry) data
CONF_NAME = "name"
CONF_TAP_ENTITY = "tap_entity"
CONF_MODE = "mode"
CONF_AMOUNT = "amount"
CONF_FLOW_SENSOR = "flow_sensor"
CONF_START_TIME = "start_time"
CONF_DAYS = "days"
CONF_RAIN_THRESHOLD = "rain_threshold"
CONF_MAX_MINUTES = "max_minutes"
CONF_ENABLED = "enabled"

MODE_MINUTES = "minutes"
MODE_LITRES = "litres"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_AMOUNT_MINUTES = 10.0
DEFAULT_AMOUNT_LITRES = 50.0
DEFAULT_START_TIME = "06:00:00"
DEFAULT_RAIN_THRESHOLD = 5.0
DEFAULT_MAX_MINUTES = 45

# Rain is refreshed this often; the window is 24 h either side of now.
RAIN_REFRESH_MINUTES = 30
RAIN_WINDOW_HOURS = 24

# Litres mode: stop if this little water has flowed after this long.
NO_FLOW_AFTER_SECONDS = 300
NO_FLOW_LITRES = 0.5
FLOW_TICK_SECONDS = 10

# Run results
RESULT_COMPLETED = "completed"
RESULT_MAX_TIME = "max_time"
RESULT_NO_FLOW = "no_flow"
RESULT_STOPPED = "stopped"
RESULT_STOPPED_EXTERNALLY = "stopped_externally"
RESULT_INTERRUPTED = "interrupted_by_restart"
RESULT_SKIPPED_RAIN = "skipped_rain"
RESULT_FAILED = "failed"

# Status values of the per-tap status sensor
STATUS_IDLE = "idle"
STATUS_WATERING = "watering"
STATUS_QUEUED = "queued"
STATUS_DISABLED = "disabled"
STATUS_RAIN_HOLD = "rain_hold"

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
