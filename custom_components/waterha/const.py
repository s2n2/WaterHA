"""Constants for WaterHA."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "waterha"
VERSION = "0.3.0"

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
CONF_COOL_BELOW = "cool_below"
CONF_HOT_FROM = "hot_from"
# Hub options that change no entities, so editing them never reloads.
SOFT_OPTIONS = {CONF_COOL_BELOW, CONF_HOT_FROM}
SOURCE_OPEN_METEO = "open_meteo"
SOURCE_WEATHER = "weather_entity"

# Tap (subentry) data
CONF_NAME = "name"
CONF_TAP_ENTITY = "tap_entity"
CONF_MODE = "mode"
CONF_AMOUNT = "amount"  # 0.1.x single amount; read as the fallback for every band
CONF_SEQUENTIAL = "sequential"  # one at a time: queue behind other one-at-a-time taps
CONF_FLOW_SENSOR = "flow_sensor"
CONF_START_TIME = "start_time"  # slot 1 time (the only time in 0.1/0.2)
CONF_START_TIME_2 = "start_time_2"
CONF_SLOT_1 = "slot_1"  # slot on/off
CONF_SLOT_2 = "slot_2"
CONF_DAYS = "days"
CONF_RAIN_THRESHOLD = "rain_threshold"
CONF_MAX_MINUTES = "max_minutes"
CONF_ENABLED = "enabled"

MODE_MINUTES = "minutes"
MODE_LITRES = "litres"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_AMOUNT_MINUTES = 10.0
DEFAULT_AMOUNT_LITRES = 50.0

# Day types from today's forecast high: cool < cool_below <= warm < hot_from <= hot.
BAND_COOL = "cool"
BAND_WARM = "warm"
BAND_HOT = "hot"
BANDS = [BAND_COOL, BAND_WARM, BAND_HOT]
DEFAULT_COOL_BELOW = 16.0
DEFAULT_HOT_FROM = 23.0
DEFAULT_BAND_AMOUNTS = {
    "minutes": {BAND_COOL: 5.0, BAND_WARM: 10.0, BAND_HOT: 15.0},
    "litres": {BAND_COOL: 20.0, BAND_WARM: 40.0, BAND_HOT: 60.0},
}


def amount_key(band: str) -> str:
    return f"amount_{band}"


# At HA start every tap is closed, then checked again after this long
# (radio integrations may not be ready the first time).
STARTUP_RECHECK_SECONDS = 60
DEFAULT_START_TIME = "06:00:00"
DEFAULT_START_TIME_2 = "19:00:00"
# Two daily slots: (number, time key, on/off key, on by default).
SLOTS = [(1, CONF_START_TIME, CONF_SLOT_1, True), (2, CONF_START_TIME_2, CONF_SLOT_2, False)]
DEFAULT_SLOT_TIMES = {1: DEFAULT_START_TIME, 2: DEFAULT_START_TIME_2}
DEFAULT_RAIN_THRESHOLD = 5.0
DEFAULT_MAX_MINUTES = 45

# Rain is refreshed this often; the window is 24 h either side of now.
RAIN_REFRESH_MINUTES = 30
RAIN_WINDOW_HOURS = 24
# After a failed fetch (e.g. network not up yet at startup), try again this soon.
RAIN_RETRY_SECONDS = 120

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
