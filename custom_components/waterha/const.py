"""Constants for WaterHA."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "waterha"
VERSION = "0.8.0"

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
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
CONF_BACKUP_WEATHER = "backup_weather"
CONF_PAST_RAIN_SENSOR = "past_rain_sensor"
CONF_ET_COOL_BELOW = "et_cool_below"  # mm of evapotranspiration today
CONF_ET_HOT_FROM = "et_hot_from"
CONF_WATER_PRICE = "water_price"
# Savings compare against running every tap this many minutes a day (15 min x 2).
CONF_BASELINE_MINUTES = "baseline_minutes"  # per 1000 L (per m³), in HA's currency
SOFT_OPTIONS = {
    CONF_COOL_BELOW,
    CONF_HOT_FROM,
    CONF_ET_COOL_BELOW,
    CONF_ET_HOT_FROM,
    CONF_BACKUP_WEATHER,
    CONF_PAST_RAIN_SENSOR,
    CONF_WATER_PRICE,
    CONF_BASELINE_MINUTES,
}
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
# Evapotranspiration (ET0, mm/day: water lost to heat, sun, wind and dry air).
# The day type is the hotter of the temperature rule and this one, so a
# windy, dry mild day counts as hot.
DEFAULT_ET_COOL_BELOW = 3.0
DEFAULT_ET_HOT_FROM = 5.0
DEFAULT_BAND_AMOUNTS = {
    "minutes": {BAND_COOL: 5.0, BAND_WARM: 10.0, BAND_HOT: 15.0},
    "litres": {BAND_COOL: 20.0, BAND_WARM: 40.0, BAND_HOT: 60.0},
}


def amount_key(band: str) -> str:
    return f"amount_{band}"


# At HA start every tap is closed, then checked again after this long
# (radio integrations may not be ready the first time).
STARTUP_RECHECK_SECONDS = 60

# Run log kept for the history card.
LOG_DAYS = 7
LOG_MAX = 500
# Litres per tap per day, kept for the cost card.
USAGE_DAYS = 400
DEFAULT_WATER_PRICE = 1.48
DEFAULT_BASELINE_MINUTES = 30
SAVINGS_DAYS = 365
DEFAULT_START_TIME = "06:00:00"
DEFAULT_START_TIME_2 = "19:00:00"
# Two daily slots: (number, time key, on/off key, on by default).
SLOTS = [(1, CONF_START_TIME, CONF_SLOT_1, True), (2, CONF_START_TIME_2, CONF_SLOT_2, False)]
DEFAULT_SLOT_TIMES = {1: DEFAULT_START_TIME, 2: DEFAULT_START_TIME_2}
# Per slot: on which day types it runs, and what share of the day-type amount it gives.
WHEN_ALWAYS = "always"
WHEN_WARM_HOT = "warm_hot"
WHEN_HOT = "hot"
SLOT_WHEN = [WHEN_ALWAYS, WHEN_WARM_HOT, WHEN_HOT]
SLOT_RULES = {1: ("slot_1_when", "slot_1_share"), 2: ("slot_2_when", "slot_2_share")}
DEFAULT_RAIN_THRESHOLD = 5.0
DEFAULT_MAX_MINUTES = 45

# Rain is refreshed this often; the window is 24 h either side of now.
RAIN_REFRESH_MINUTES = 30
RAIN_WINDOW_HOURS = 24
# Effective rain (irrigation practice): the first ~2 mm wets leaves and the
# surface and evaporates; of the rest about 80% reaches the roots.
EFFECTIVE_RAIN_SHARE = 0.8
RAIN_INTERCEPTION_MM = 2.0
# Rain fallen since a tap's last check counts back at most this far.
RAIN_LOOKBACK_HOURS = 72
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
