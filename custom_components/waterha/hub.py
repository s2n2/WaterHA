"""The WaterHA engine: rain data, schedules and watering runs.

One hub per config entry. Each tap is a config subentry; its settings live in
the subentry data (so the integration page and the entities edit the same
thing). Run history and any run in progress are kept in a Store so a restart
mid-run closes the tap instead of leaving it open.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from functools import partial
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BAND_COOL,
    BAND_HOT,
    BAND_WARM,
    CONF_AMOUNT,
    CONF_BACKUP_WEATHER,
    CONF_BASELINE_MINUTES,
    CONF_COOL_BELOW,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_ET_COOL_BELOW,
    CONF_ET_HOT_FROM,
    CONF_FLOW_SENSOR,
    CONF_FORECAST_SOURCE,
    CONF_HOT_FROM,
    CONF_MAX_MINUTES,
    CONF_MODE,
    CONF_NAME,
    CONF_PAST_RAIN_SENSOR,
    CONF_RAIN_THRESHOLD,
    CONF_SEQUENTIAL,
    CONF_START_TIME,
    CONF_TAP_ENTITY,
    CONF_WATER_PRICE,
    CONF_WEATHER_ENTITY,
    DAYS,
    DEFAULT_BAND_AMOUNTS,
    DEFAULT_BASELINE_MINUTES,
    DEFAULT_COOL_BELOW,
    DEFAULT_ET_COOL_BELOW,
    DEFAULT_ET_HOT_FROM,
    DEFAULT_HOT_FROM,
    DEFAULT_MAX_MINUTES,
    DEFAULT_SLOT_TIMES,
    DEFAULT_WATER_PRICE,
    DOMAIN,
    FLOW_TICK_SECONDS,
    LOG_DAYS,
    LOG_MAX,
    MODE_LITRES,
    MODE_MINUTES,
    NO_FLOW_AFTER_SECONDS,
    NO_FLOW_LITRES,
    RAIN_REFRESH_MINUTES,
    RAIN_RETRY_SECONDS,
    RAIN_WINDOW_HOURS,
    RESULT_COMPLETED,
    RESULT_FAILED,
    RESULT_INTERRUPTED,
    RESULT_MAX_TIME,
    RESULT_NO_FLOW,
    RESULT_SKIPPED_RAIN,
    RESULT_STOPPED,
    RESULT_STOPPED_EXTERNALLY,
    SAVINGS_DAYS,
    SIGNAL_UPDATE,
    SLOT_RULES,
    SLOTS,
    SOURCE_WEATHER,
    STARTUP_RECHECK_SECONDS,
    STATUS_DISABLED,
    STATUS_IDLE,
    STATUS_QUEUED,
    STATUS_RAIN_HOLD,
    STATUS_WATERING,
    SUBENTRY_TAP,
    USAGE_DAYS,
    WHEN_ALWAYS,
    WHEN_HOT,
    WHEN_WARM_HOT,
    amount_key,
)
from .rain import (
    RainHour,
    RainSummary,
    day_et,
    day_max_temp,
    fetch_open_meteo,
    intensity_hours,
    parse_weather_forecast,
    summarise,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
OPEN_STATES = {"on", "open", "opening"}
WEATHER_HOURLY = 2  # WeatherEntityFeature.FORECAST_HOURLY
# Forecast sensors (…_3h, …_1d) are not a record of rain that fell.
FORECAST_SENSOR = re.compile(r"_\d+[hd]$")
RATE_TO_MM_PER_H = {"mm/h": 1.0, "in/h": 25.4, "mm/s": 3600.0, "in/d": 25.4 / 24, "mm/d": 1 / 24}
# If the tap has not reported open this long after we asked, the run failed.
OPEN_CONFIRM_SECONDS = 90

# Litres per second for each Home Assistant volume-flow unit.
FLOW_TO_LPS: dict[str, float] = {
    "m³/h": 1000 / 3600,
    "m³/min": 1000 / 60,
    "m³/s": 1000,
    "ft³/min": 28.316846592 / 60,
    "L/h": 1 / 3600,
    "L/min": 1 / 60,
    "L/s": 1,
    "mL/s": 0.001,
    "gal/h": 3.785411784 / 3600,
    "gal/min": 3.785411784 / 60,
    "gal/d": 3.785411784 / 86400,
}


def flow_lps(state: Any) -> float:
    """Current flow in litres per second from a flow-rate sensor state (0 if unknown)."""
    if state is None:
        return 0.0
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    unit = state.attributes.get("unit_of_measurement") or "m³/h"
    return value * FLOW_TO_LPS.get(unit, FLOW_TO_LPS["m³/h"])


def band_for(temp: float | None, cool_below: float, hot_from: float) -> str:
    """Cool, warm or hot day for a forecast high; warm when the high is unknown."""
    if temp is None:
        return BAND_WARM
    if temp < cool_below:
        return BAND_COOL
    if temp >= hot_from:
        return BAND_HOT
    return BAND_WARM


BAND_ORDER = [BAND_COOL, BAND_WARM, BAND_HOT]


def hotter(*bands: str | None) -> str:
    """The hottest of the given day types (None ignored; warm if none)."""
    known = [b for b in bands if b in BAND_ORDER]
    return max(known, key=BAND_ORDER.index) if known else BAND_WARM


def slot_runs_on(when: str | None, band: str) -> bool:
    """Whether a slot set to run `when` runs on a `band` day."""
    if when == WHEN_HOT:
        return band == BAND_HOT
    if when == WHEN_WARM_HOT:
        return band in (BAND_WARM, BAND_HOT)
    return True


def band_amount(cfg: dict[str, Any], band: str) -> float:
    """A tap's amount for a day type (0.1.x taps had one `amount` for every day)."""
    value = cfg.get(amount_key(band))
    if value is None:
        value = cfg.get(CONF_AMOUNT)
    if value is None:
        value = DEFAULT_BAND_AMOUNTS.get(cfg.get(CONF_MODE, MODE_MINUTES), {}).get(band, 0)
    return float(value)


def _scheduled_key(slot: int) -> str:
    # Slot 1 keeps the 0.2 key so an upgrade the same morning does not run it twice.
    return "last_scheduled" if slot == 1 else f"last_scheduled_{slot}"


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def next_run_after(now: datetime, start: time | None, days: list[str]) -> datetime | None:
    """The next local datetime strictly after `now` that matches start time and days."""
    if start is None or not days:
        return None
    wanted = {DAYS.index(d) for d in days if d in DAYS}
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() not in wanted:
            continue
        moment = datetime.combine(day, start.replace(second=0, microsecond=0), tzinfo=now.tzinfo)
        if moment > now:
            return moment
    return None


@dataclass
class Run:
    """A watering run in progress."""

    tap_id: str
    mode: str
    target: float
    started: datetime
    reason: str
    max_seconds: float
    entity_id: str = ""
    name: str = ""
    flow: bool = False
    sequential: bool = True
    band: str | None = None
    rain_factor: float | None = None  # scheduled runs: share left after rain
    cfg_max_seconds: float = 0.0
    litres: float = 0.0
    rate_lps: float = 0.0
    last_sample: datetime | None = None
    unsubs: list[Callable[[], None]] = field(default_factory=list)
    stopping: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "started": self.started.isoformat(),
            "reason": self.reason,
            "litres": round(self.litres, 1),
            "entity_id": self.entity_id,
        }


class WaterHub:
    """Everything one WaterHA config entry does."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.rain: RainSummary | None = None
        self.rain_error: str | None = None
        self.rain_updated: datetime | None = None
        self.runs: dict[str, Run] = {}
        # (tap_id, amount, mode, reason, band) of one-at-a-time taps waiting their turn
        self.queue: list[tuple[str, float, str, str, str | None]] = []
        self._hours: list[RainHour] = []  # Open-Meteo hourly, kept for temperatures
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._state: dict[str, dict[str, Any]] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._retry_unsub: Callable[[], None] | None = None
        self._open_meteo_ok = True
        self._temp_high_backup: float | None = None
        self.log: list[dict[str, Any]] = []
        self._pending_factor: dict[str, float] = {}
        # {local date: {tap_id: litres}} and the all-time total, for usage and cost.
        self.daily: dict[str, dict[str, float]] = {}
        self.total_litres = 0.0
        self.device_id: str | None = None

    # ------------------------------------------------------------------ setup
    @property
    def signal(self) -> str:
        return SIGNAL_UPDATE.format(self.entry.entry_id)

    @property
    def taps(self) -> dict[str, ConfigSubentry]:
        return {
            sid: sub
            for sid, sub in self.entry.subentries.items()
            if sub.subentry_type == SUBENTRY_TAP
        }

    def tap(self, tap_id: str) -> dict[str, Any]:
        return dict(self.entry.subentries[tap_id].data)

    def tap_state(self, tap_id: str) -> dict[str, Any]:
        return self._state.setdefault(tap_id, {})

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self._state = stored.get("taps", {})
        self.log = stored.get("log", [])
        self.daily = stored.get("daily", {})
        self.total_litres = float(stored.get("total_litres", 0.0))
        # Savings are counted from the first day WaterHA tracked usage.
        self.tracking_since = stored.get("tracking_since") or dt_util.now().date().isoformat()
        self._prune_log()
        active = stored.get("active", {})
        # Set up while HA is starting = a restart: close every tap once their
        # integrations are up, in case one was stuck open. A reload while HA is
        # running only closes runs WaterHA itself had going.
        ha_starting = self.hass.state is not CoreState.running

        async def _at_started(_hass: HomeAssistant) -> None:
            await self._recover(active)
            if ha_starting:
                await self.async_close_all_taps("Home Assistant started")
                self._unsubs.append(
                    async_call_later(self.hass, STARTUP_RECHECK_SECONDS, self._startup_recheck)
                )
            self.notify()

        if active or ha_starting:
            self._unsubs.append(async_at_started(self.hass, _at_started))
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._refresh_rain_cb, timedelta(minutes=RAIN_REFRESH_MINUTES)
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)
        )
        self.hass.async_create_background_task(self.async_refresh_rain(), f"{DOMAIN} rain")
        self._unsubs.append(async_at_started(self.hass, self._seed_flows))

    async def async_stop(self) -> None:
        """Unload: close any open tap so a reload never leaves water running."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None
        self.queue.clear()
        for tap_id in list(self.runs):
            await self.async_finish(tap_id, RESULT_STOPPED)
        await self._store.async_save(self._as_store())

    async def _on_ha_stop(self, _event: Event) -> None:
        # Close taps on shutdown; the run is recorded as interrupted.
        for tap_id in list(self.runs):
            await self.async_finish(tap_id, RESULT_INTERRUPTED)
        await self._store.async_save(self._as_store())

    async def async_close_all_taps(self, why: str, only_open: bool = False) -> None:
        """Send close to every configured tap not running a WaterHA run."""
        for tap_id, sub in self.taps.items():
            entity_id = sub.data.get(CONF_TAP_ENTITY)
            if not entity_id or tap_id in self.runs:
                continue
            state = self.hass.states.get(entity_id)
            if only_open and (state is None or state.state not in OPEN_STATES):
                continue
            if state is not None and state.state in OPEN_STATES:
                _LOGGER.warning("Closing %s (%s): it was open", entity_id, why)
            try:
                await self._set_tap(entity_id, False)
            except HomeAssistantError as err:
                _LOGGER.warning("Could not close %s (%s): %s", entity_id, why, err)

    async def _startup_recheck(self, _now: datetime) -> None:
        await self.async_close_all_taps("still open after start", only_open=True)

    async def _recover(self, active: dict[str, Any]) -> None:
        """A run that was in progress when HA stopped: close the tap."""
        for tap_id, run in active.items():
            if tap_id not in self.taps:
                continue
            entity_id = run.get("entity_id") or self.tap(tap_id).get(CONF_TAP_ENTITY)
            _LOGGER.warning("Closing %s: a watering run was interrupted by a restart", entity_id)
            try:
                await self._set_tap(entity_id, False)
            except HomeAssistantError as err:  # entity may not be ready yet
                _LOGGER.warning("Could not close %s after restart: %s", entity_id, err)
            state = self.tap_state(tap_id)
            state.update(
                last_run=run.get("started"),
                last_end=dt_util.utcnow().isoformat(),
                last_result=RESULT_INTERRUPTED,
                last_litres=run.get("litres"),
            )
            # HA went down without a clean stop, so when the water stopped is unknown.
            self._log({
                "tap": tap_id,
                "name": self.tap(tap_id).get(CONF_NAME),
                "start": run.get("started") or dt_util.utcnow().isoformat(),
                "end": run.get("started") or dt_util.utcnow().isoformat(),
                "minutes": None,
                "litres": run.get("litres"),
                "target": run.get("target"),
                "mode": run.get("mode"),
                "reason": run.get("reason"),
                "result": RESULT_INTERRUPTED,
            })
        self._save()

    def _as_store(self) -> dict[str, Any]:
        return {
            "taps": self._state,
            "active": {tap_id: run.as_dict() for tap_id, run in self.runs.items()},
            "log": self.log,
            "daily": self.daily,
            "total_litres": round(self.total_litres, 1),
            "tracking_since": self.tracking_since,
        }

    # ------------------------------------------------------------------- log
    def _prune_log(self) -> None:
        cutoff = (dt_util.utcnow() - timedelta(days=LOG_DAYS)).isoformat()
        self.log = [item for item in self.log if (item.get("end") or item["start"]) >= cutoff][-LOG_MAX:]

    def _log(self, item: dict[str, Any]) -> None:
        self.log.append(item)
        self._prune_log()

    # ----------------------------------------------------------- usage/cost
    @property
    def water_price(self) -> float:
        """Price per 1000 L (= per m³)."""
        return float(self.entry.options.get(CONF_WATER_PRICE, DEFAULT_WATER_PRICE))

    def _add_usage(self, tap_id: str, litres: float, when: datetime) -> None:
        if litres <= 0:
            return
        day = dt_util.as_local(when).date().isoformat()
        taps = self.daily.setdefault(day, {})
        taps[tap_id] = round(taps.get(tap_id, 0.0) + litres, 1)
        self.total_litres += litres
        cutoff = (dt_util.now().date() - timedelta(days=USAGE_DAYS)).isoformat()
        for old in [d for d in self.daily if d < cutoff]:
            del self.daily[old]

    # ------------------------------------------------------------- savings
    @property
    def baseline_minutes(self) -> float:
        """Minutes per tap per day the savings are compared against."""
        return float(self.entry.options.get(CONF_BASELINE_MINUTES, DEFAULT_BASELINE_MINUTES))

    def flow_lpm(self, tap_id: str) -> float | None:
        """A tap's typical flow (L/min): from its recorded history, refined by each run."""
        value = self.tap_state(tap_id).get("flow_lpm")
        return float(value) if value else None

    def _learn_flow(self, tap_id: str, run: Run) -> None:
        minutes = (dt_util.utcnow() - run.started).total_seconds() / 60
        if minutes < 1 or run.litres <= 0:
            return
        measured = run.litres / minutes
        old = self.flow_lpm(tap_id)
        self.tap_state(tap_id)["flow_lpm"] = round(measured if old is None else 0.7 * old + 0.3 * measured, 2)

    async def _seed_flows(self, _hass: HomeAssistant) -> None:
        """Taps with no known flow yet: the median of their flow sensor's non-zero history."""
        if "recorder" not in self.hass.config.components:
            return
        from homeassistant.components.recorder import get_instance, history

        now = dt_util.utcnow()
        for tap_id, sub in self.taps.items():
            sensor = sub.data.get(CONF_FLOW_SENSOR)
            if not sensor or self.flow_lpm(tap_id) is not None:
                continue
            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    partial(
                        history.state_changes_during_period,
                        self.hass,
                        now - timedelta(days=30),
                        now,
                        entity_id=sensor,
                        no_attributes=True,
                        include_start_time_state=False,
                    )
                )
            except Exception as err:  # noqa: BLE001 - recorder may be busy
                _LOGGER.debug("No flow history for %s: %s", sensor, err)
                continue
            current = self.hass.states.get(sensor)
            unit = (current.attributes.get("unit_of_measurement") if current else None) or "m³/h"
            values = sorted(
                float(s.state) * FLOW_TO_LPS.get(unit, FLOW_TO_LPS["m³/h"]) * 60
                for s in states.get(sensor, [])
                if _positive(s.state)
            )
            if values:
                self.tap_state(tap_id)["flow_lpm"] = round(values[len(values) // 2], 2)
        self._save()
        self.notify()

    def savings_view(self) -> dict[str, Any]:
        """Water and money saved over the last 12 months of tracking (completed days),
        against every tap running `baseline_minutes` a day at its typical flow."""
        price = self.water_price
        today = dt_util.now().date()
        try:
            since = datetime.fromisoformat(self.tracking_since).date()
        except (TypeError, ValueError):
            since = today
        first = max(since, today - timedelta(days=SAVINGS_DAYS))
        days = [first + timedelta(days=i) for i in range((today - first).days)]  # up to yesterday
        flows = {tap_id: self.flow_lpm(tap_id) for tap_id in self.taps}
        counted = {tap_id: f for tap_id, f in flows.items() if f}
        baseline = len(days) * self.baseline_minutes * sum(counted.values())
        actual = sum(
            self.daily.get(day.isoformat(), {}).get(tap_id, 0.0) for day in days for tap_id in counted
        )
        saved = baseline - actual
        per_day = saved / len(days) if days else 0.0
        names = {tap_id: sub.data.get(CONF_NAME) for tap_id, sub in self.taps.items()}
        return {
            "days": len(days),
            "since": first.isoformat(),
            "baseline_minutes": self.baseline_minutes,
            "baseline_litres": round(baseline, 1),
            "actual_litres": round(actual, 1),
            "saved_litres": round(saved, 1),
            "saved_cost": round(saved * price / 1000, 2),
            "per_year_litres": round(per_day * 365, 1),
            "per_year_cost": round(per_day * 365 * price / 1000, 2),
            "flows": {names[t]: f for t, f in flows.items()},
            "not_counted": [names[t] for t, f in flows.items() if not f],
        }

    def live_litres(self) -> dict[str, float]:
        """Litres so far of runs in progress (flow-measured taps only)."""
        return {tap_id: run.litres for tap_id, run in self.runs.items() if run.flow and run.litres > 0}

    def total_with_live(self) -> float:
        return round(self.total_litres + sum(self.live_litres().values()), 1)

    def usage_view(self, days: int = 31) -> dict[str, Any]:
        """Daily litres and cost (oldest first) plus totals, for the cost card."""
        price = self.water_price
        today = dt_util.now().date()
        names = {tap_id: sub.data.get(CONF_NAME) for tap_id, sub in self.taps.items()}
        live = self.live_litres()
        series = []
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            taps = dict(self.daily.get(day, {}))
            if offset == 0:
                for tap_id, litres in live.items():
                    taps[tap_id] = round(taps.get(tap_id, 0.0) + litres, 1)
            litres = round(sum(taps.values()), 1)
            series.append({
                "date": day,
                "litres": litres,
                "cost": round(litres * price / 1000, 2),
                "taps": {names.get(t, t): v for t, v in taps.items()},
            })

        def total(n: int) -> float:
            return round(sum(d["litres"] for d in series[-n:]), 1)

        month = [d for d in series if d["date"][:7] == today.isoformat()[:7]]
        month_litres = round(sum(d["litres"] for d in month), 1)
        days_in_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        projected = month_litres / len(month) * days_in_month.day if month else 0.0
        per_tap: dict[str, float] = {}
        for d in series[-30:]:
            for name, litres in d["taps"].items():
                per_tap[name] = round(per_tap.get(name, 0.0) + litres, 1)

        def money(litres: float) -> float:
            return round(litres * price / 1000, 2)

        no_flow = [sub.data.get(CONF_NAME) for sub in self.taps.values() if not sub.data.get(CONF_FLOW_SENSOR)]
        return {
            "price": price,
            "series": series,
            "today": {"litres": total(1), "cost": money(total(1))},
            "week": {"litres": total(7), "cost": money(total(7))},
            "days30": {"litres": total(30), "cost": money(total(30))},
            "month": {"litres": month_litres, "cost": money(month_litres)},
            "projected_month": {"litres": round(projected, 1), "cost": money(projected)},
            "per_tap_30d": [
                {"name": name, "litres": litres, "cost": money(litres)}
                for name, litres in sorted(per_tap.items(), key=lambda kv: -kv[1])
            ],
            "since": min(self.daily) if self.daily else None,
            "not_measured": no_flow,
            "savings": self.savings_view(),
        }

    def log_view(self) -> list[dict[str, Any]]:
        """The log newest first, with runs in progress (end None) on top."""
        now = dt_util.utcnow()
        live = [
            {
                "tap": run.tap_id,
                "name": run.name,
                "start": run.started.isoformat(),
                "end": None,
                "minutes": round((now - run.started).total_seconds() / 60, 1),
                "litres": round(run.litres, 1) if run.flow else None,
                "target": run.target,
                "mode": run.mode,
                "band": run.band,
                "reason": run.reason,
                "rain_factor": run.rain_factor,
                "result": "running",
            }
            for run in self.runs.values()
        ]
        return live + list(reversed(self.log))

    def _save(self) -> None:
        self._store.async_delay_save(self._as_store, 2)

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # ------------------------------------------------------------- settings
    async def async_set_option(self, tap_id: str, key: str, value: Any) -> None:
        """Change one tap setting (from an entity) in the subentry data."""
        subentry = self.entry.subentries[tap_id]
        self.hass.config_entries.async_update_subentry(
            self.entry, subentry, data={**subentry.data, key: value}
        )
        self.notify()

    async def async_set_hub_option(self, key: str, value: Any) -> None:
        """Change a hub option (temperature splits) from an entity."""
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )
        self.notify()

    # ----------------------------------------------------------- temperature
    @property
    def cool_below(self) -> float:
        return float(self.entry.options.get(CONF_COOL_BELOW, DEFAULT_COOL_BELOW))

    @property
    def hot_from(self) -> float:
        return float(self.entry.options.get(CONF_HOT_FROM, DEFAULT_HOT_FROM))

    def temp_max_today(self) -> float | None:
        """Today's forecast high: the backup's daily high when on backup, else the hourly max."""
        if self._temp_high_backup is not None:
            return self._temp_high_backup
        tz = dt_util.get_default_time_zone()
        return day_max_temp(self._hours, dt_util.now().date(), tz)

    @property
    def et_cool_below(self) -> float:
        return float(self.entry.options.get(CONF_ET_COOL_BELOW, DEFAULT_ET_COOL_BELOW))

    @property
    def et_hot_from(self) -> float:
        return float(self.entry.options.get(CONF_ET_HOT_FROM, DEFAULT_ET_HOT_FROM))

    def et_today(self) -> float | None:
        """Today's evapotranspiration (mm) from Open-Meteo; None on backup data."""
        if self._temp_high_backup is not None:
            return None
        return day_et(self._hours, dt_util.now().date(), dt_util.get_default_time_zone())

    def band_by_temp(self) -> str:
        return band_for(self.temp_max_today(), self.cool_below, self.hot_from)

    def band_by_et(self) -> str | None:
        et = self.et_today()
        return None if et is None else band_for(et, self.et_cool_below, self.et_hot_from)

    def band_today(self) -> str:
        """The hotter of the temperature and evapotranspiration day types.

        A windy, dry 21 °C day can dry soil like a hot one; ET catches it.
        """
        return hotter(self.band_by_temp(), self.band_by_et())

    def band_reason(self) -> str:
        by_et = self.band_by_et()
        if by_et is not None and BAND_ORDER.index(by_et) > BAND_ORDER.index(self.band_by_temp()):
            return "evapotranspiration"
        return "temperature"

    def amount_today(self, tap_id: str) -> float:
        return band_amount(self.tap(tap_id), self.band_today())

    # ------------------------------------------------------------------ rain
    async def _refresh_rain_cb(self, _now: datetime) -> None:
        await self.async_refresh_rain()

    async def _retry_rain(self, _now: datetime) -> None:
        self._retry_unsub = None
        await self.async_refresh_rain()

    async def async_refresh_rain(self) -> None:
        now = dt_util.utcnow()
        session = async_get_clientsession(self.hass)
        try:
            past = await fetch_open_meteo(session, self.hass.config.latitude, self.hass.config.longitude)
        except Exception as err:  # noqa: BLE001 - network errors of every kind
            # A timeout's message is empty, so name the error type too.
            reason = f"{type(err).__name__}: {err}".rstrip(": ")
            log = _LOGGER.warning if self._open_meteo_ok else _LOGGER.debug
            self._open_meteo_ok = False
            if await self._refresh_from_backup(now, reason):
                log("Open-Meteo unavailable (%s); using %s", reason, self.rain.source)
                self.notify()
                return
            log("Open-Meteo unavailable (%s) and no backup rain source; retrying in %s s",
                reason, RAIN_RETRY_SECONDS)
            self.rain_error = f"Open-Meteo: {reason}; no backup rain source"
            # Nothing at all yet (e.g. network not up at startup): try again soon.
            if self.rain is None and self._retry_unsub is None:
                self._retry_unsub = async_call_later(self.hass, RAIN_RETRY_SECONDS, self._retry_rain)
            self.notify()
            return
        if not self._open_meteo_ok:
            _LOGGER.info("Open-Meteo rain data is back")
        self._open_meteo_ok = True
        self._temp_high_backup = None
        self._hours = past
        future: list[RainHour] = past
        source = "Open-Meteo"
        self.rain_error = None
        if self.entry.options.get(CONF_FORECAST_SOURCE) == SOURCE_WEATHER:
            weather = self.entry.options.get(CONF_WEATHER_ENTITY)
            try:
                future = await self._weather_forecast(weather)
                source = f"Open-Meteo (past), {weather} (forecast)"
            except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Forecast from %s unavailable, using Open-Meteo: %s", weather, err)
                self.rain_error = f"{weather}: {err}; using Open-Meteo forecast"
        self.rain = summarise(now, past, future, RAIN_WINDOW_HOURS, source)
        self.rain_updated = now
        self.notify()

    async def _weather_forecast(self, entity_id: str | None, kind: str = "hourly") -> list[RainHour]:
        if not entity_id:
            raise ValueError("no weather entity configured")
        response = await self.hass.services.async_call(
            "weather",
            "get_forecasts",
            {ATTR_ENTITY_ID: entity_id, "type": kind},
            blocking=True,
            return_response=True,
        )
        hours = parse_weather_forecast(response[entity_id]["forecast"])
        if not hours:
            raise ValueError(f"empty {kind} forecast")
        return hours

    # ------------------------------------------------------------ backup rain
    def backup_weather(self) -> list[str]:
        """Weather entities to forecast from when Open-Meteo is down.

        The configured ones, else every weather entity with an hourly forecast.
        """
        configured = self.entry.options.get(CONF_BACKUP_WEATHER)
        if configured:
            return list(configured)
        return [
            state.entity_id
            for state in self.hass.states.async_all("weather")
            if int(state.attributes.get("supported_features") or 0) & WEATHER_HOURLY
        ]

    def past_rain_sensor(self) -> str | None:
        """A rain-rate sensor whose history gives the past 24 h when Open-Meteo is down.

        The configured one, else the first precipitation-intensity sensor that is a
        current reading (not a forecast such as ..._3h or ..._1d).
        """
        configured = self.entry.options.get(CONF_PAST_RAIN_SENSOR)
        if configured:
            return configured
        for state in self.hass.states.async_all("sensor"):
            if (
                state.attributes.get("device_class") == "precipitation_intensity"
                and not FORECAST_SENSOR.search(state.entity_id)
            ):
                return state.entity_id
        return None

    async def _refresh_from_backup(self, now: datetime, reason: str) -> bool:
        """Rain (and today's high) from HA's own weather entities and rain history."""
        future: list[RainHour] = []
        forecast_from = None
        high = None
        for entity_id in self.backup_weather():
            try:
                future = await self._weather_forecast(entity_id)
            except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Backup forecast from %s unavailable: %s", entity_id, err)
                continue
            forecast_from = entity_id
            high = await self._daily_high(entity_id)
            break
        past: list[RainHour] = []
        sensor = self.past_rain_sensor()
        if sensor:
            try:
                past = await self._past_from_history(sensor, now)
            except Exception as err:  # noqa: BLE001 - recorder may be absent or busy
                _LOGGER.debug("Past rain from %s unavailable: %s", sensor, err)
                past = []
        if not future and not past:
            return False
        parts = []
        if past:
            parts.append(f"{sensor} history (past)")
        if forecast_from:
            parts.append(f"{forecast_from} (forecast)")
        self._hours = future
        if high is None:
            high = day_max_temp(future, dt_util.now().date(), dt_util.get_default_time_zone())
        self._temp_high_backup = high
        self.rain = summarise(now, past, future, RAIN_WINDOW_HOURS, "Backup: " + ", ".join(parts))
        self.rain_updated = now
        missing = [] if past else ["past 24 h"]
        if not forecast_from:
            missing.append("forecast")
        self.rain_error = f"Open-Meteo unavailable ({reason}); using backup" + (
            f", no {' or '.join(missing)}" if missing else ""
        )
        return True

    async def _daily_high(self, entity_id: str) -> float | None:
        """Today's high from a weather entity's daily forecast, if it has one."""
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts", {ATTR_ENTITY_ID: entity_id, "type": "daily"},
                blocking=True, return_response=True,
            )
            today = dt_util.now().date()
            for item in response[entity_id]["forecast"]:
                when = dt_util.parse_datetime(str(item.get("datetime")))
                if when is not None and dt_util.as_local(when).date() == today:
                    return float(item["temperature"])
        except (HomeAssistantError, KeyError, TypeError, ValueError):
            return None
        return None

    async def _past_from_history(self, entity_id: str, now: datetime) -> list[RainHour]:
        """Hourly rain for the past ~day from the recorded history of a rain-rate sensor."""
        if "recorder" not in self.hass.config.components:
            raise HomeAssistantError("recorder is not running")
        from homeassistant.components.recorder import get_instance, history

        start = now - timedelta(hours=RAIN_WINDOW_HOURS + 1)
        states = await get_instance(self.hass).async_add_executor_job(
            partial(
                history.state_changes_during_period,
                self.hass,
                start,
                now,
                entity_id=entity_id,
                no_attributes=True,
                include_start_time_state=True,
            )
        )
        points = []
        for state in states.get(entity_id, []):
            try:
                points.append((state.last_changed, float(state.state)))
            except (TypeError, ValueError):
                points.append((state.last_changed, 0.0))  # unavailable: assume dry
        current = self.hass.states.get(entity_id)
        unit = (current.attributes.get("unit_of_measurement") if current else None) or "mm/h"
        return intensity_hours(points, start, now, RATE_TO_MM_PER_H.get(unit, 1.0))

    def rain_total(self) -> float | None:
        return self.rain.total_mm if self.rain else None

    def rain_hold(self, tap_id: str) -> bool:
        return self.rain_factor(tap_id) <= 0

    def rain_factor(self, tap_id: str) -> float:
        """Share of the amount a scheduled run gives after rain: 1 - rain / threshold.

        2.5 mm against a 5 mm threshold waters 50%; at or over it, 0 (skip).
        No threshold, or no rain data, waters in full.
        """
        threshold = float(self.tap(tap_id).get(CONF_RAIN_THRESHOLD) or 0)
        total = self.rain_total()
        if threshold <= 0 or total is None:
            return 1.0
        return max(0.0, 1.0 - total / threshold)

    def scheduled_amount(self, tap_id: str) -> float:
        """What a scheduled run would give now: today's day-type amount, less for rain."""
        return round(self.amount_today(tap_id) * self.rain_factor(tap_id), 1)

    # -------------------------------------------------------------- schedule
    def slots(self, tap_id: str) -> list[tuple[int, time | None, bool]]:
        """The tap's two daily slots: (number, time, on)."""
        cfg = self.tap(tap_id)
        return [
            (n, parse_time(cfg.get(time_key) or DEFAULT_SLOT_TIMES[n]), bool(cfg.get(on_key, on_default)))
            for n, time_key, on_key, on_default in SLOTS
        ]

    def slot_rule(self, tap_id: str, slot: int) -> tuple[str, float]:
        """(runs on, share 0..1) of a slot: every day at 100% unless set otherwise."""
        when_key, share_key = SLOT_RULES[slot]
        cfg = self.tap(tap_id)
        share = float(cfg.get(share_key) or 100) / 100
        return cfg.get(when_key) or WHEN_ALWAYS, max(0.0, min(share, 1.0))

    def next_run(self, tap_id: str) -> datetime | None:
        """The soonest run of any slot that is on (None with automatic watering off)."""
        cfg = self.tap(tap_id)
        if not cfg.get(CONF_ENABLED, True):
            return None
        now = dt_util.now()
        band = self.band_today()
        runs = []
        for n, start, on in self.slots(tap_id):
            if not on:
                continue
            when, _share = self.slot_rule(tap_id, n)
            moment = next_run_after(now, start, cfg.get(CONF_DAYS, []))
            if moment is None:
                continue
            # A hot-day-only slot counts only when it is today and today qualifies:
            # later days' type is not known yet.
            if when != WHEN_ALWAYS and (moment.date() != now.date() or not slot_runs_on(when, band)):
                continue
            runs.append(moment)
        return min(runs) if runs else None

    def status(self, tap_id: str) -> str:
        if tap_id in self.runs:
            return STATUS_WATERING
        if any(item[0] == tap_id for item in self.queue):
            return STATUS_QUEUED
        if not self.tap(tap_id).get(CONF_ENABLED, True):
            return STATUS_DISABLED
        if self.rain_hold(tap_id):
            return STATUS_RAIN_HOLD
        return STATUS_IDLE

    async def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        changed = False
        for tap_id in self.taps:
            if (slot := self._due(tap_id, local)) is not None:
                await self._scheduled_run(tap_id, local, slot)
                changed = True
        if changed or self.runs:
            self.notify()

    def _due(self, tap_id: str, local: datetime) -> int | None:
        """The slot due to run this minute, if any (each slot runs at most once a day)."""
        cfg = self.tap(tap_id)
        if not cfg.get(CONF_ENABLED, True) or DAYS[local.weekday()] not in cfg.get(CONF_DAYS, []):
            return None
        today = local.date().isoformat()
        for n, start, on in self.slots(tap_id):
            if not on or start is None or (local.hour, local.minute) != (start.hour, start.minute):
                continue
            if self.tap_state(tap_id).get(_scheduled_key(n)) == today:
                continue
            when, _share = self.slot_rule(tap_id, n)
            if not slot_runs_on(when, self.band_today()):
                # Not that kind of day: nothing to do (and nothing to log).
                self.tap_state(tap_id)[_scheduled_key(n)] = today
                continue
            return n
        return None

    async def _scheduled_run(self, tap_id: str, local: datetime, slot: int = 1) -> None:
        state = self.tap_state(tap_id)
        state[_scheduled_key(slot)] = local.date().isoformat()
        state["last_slot"] = slot
        stale = self.rain_updated is None or dt_util.utcnow() - self.rain_updated > timedelta(
            minutes=RAIN_REFRESH_MINUTES + 15
        )
        if stale:
            await self.async_refresh_rain()
        total = self.rain_total()
        state["last_check_rain"] = total
        if self.rain_hold(tap_id):
            name = self.tap(tap_id).get(CONF_NAME)
            _LOGGER.info("Skipping %s: %.1f mm of rain in the 48 h window", name, total)
            now_iso = dt_util.utcnow().isoformat()
            state.update(last_skip=now_iso, last_skip_rain=total)
            state["last_result"] = RESULT_SKIPPED_RAIN
            self._log({
                "tap": tap_id, "name": name, "start": now_iso, "end": now_iso, "minutes": 0,
                "litres": None, "result": RESULT_SKIPPED_RAIN, "reason": "schedule",
                "slot": slot, "rain_mm": total, "rain_factor": 0.0,
            })
            self._save()
            self.notify()
            return
        band = self.band_today()
        factor = self.rain_factor(tap_id)
        _when, share = self.slot_rule(tap_id, slot)
        amount = round(band_amount(self.tap(tap_id), band) * share * factor, 1)
        state.update(
            last_check_temp=self.temp_max_today(), last_check_band=band, last_rain_factor=round(factor, 2)
        )
        if amount <= 0:
            return
        self._pending_factor[tap_id] = round(factor, 2)
        await self.async_water(tap_id, amount, None, "schedule", band)

    # ------------------------------------------------------------------ runs
    def _sequential(self, tap_id: str) -> bool:
        return bool(self.tap(tap_id).get(CONF_SEQUENTIAL, True))

    def _sequential_busy(self) -> bool:
        """A one-at-a-time tap is running, so other one-at-a-time taps must wait."""
        return any(run.sequential for run in self.runs.values())

    async def async_water(
        self,
        tap_id: str,
        amount: float | None = None,
        mode: str | None = None,
        reason: str = "manual",
        band: str | None = None,
    ) -> None:
        """Start (or queue) a run. Manual runs ignore the rain threshold.

        Without an amount the tap's amount for today's day type is used. A
        one-at-a-time tap waits while another one-at-a-time tap runs; the queue
        runs in the order taps became due.
        """
        if tap_id not in self.taps:
            raise HomeAssistantError(f"Unknown WaterHA tap {tap_id}")
        cfg = self.tap(tap_id)
        mode = mode or cfg.get(CONF_MODE, MODE_MINUTES)
        if mode == MODE_LITRES and not cfg.get(CONF_FLOW_SENSOR):
            raise HomeAssistantError(f"{cfg.get(CONF_NAME)} has no flow sensor for litres mode")
        if amount is None:
            band = band or self.band_today()
            amount = band_amount(cfg, band)
        if amount <= 0 or tap_id in self.runs:
            return
        if self._sequential(tap_id) and self._sequential_busy():
            self.queue = [item for item in self.queue if item[0] != tap_id]
            self.queue.append((tap_id, amount, mode, reason, band))
            self.notify()
            return
        await self._start(tap_id, amount, mode, reason, band)

    async def _start(
        self, tap_id: str, amount: float, mode: str, reason: str, band: str | None = None
    ) -> None:
        cfg = self.tap(tap_id)
        now = dt_util.utcnow()
        max_minutes = float(cfg.get(CONF_MAX_MINUTES) or DEFAULT_MAX_MINUTES)
        if mode == MODE_MINUTES:
            max_minutes = min(max_minutes, amount)
        flow_entity = cfg.get(CONF_FLOW_SENSOR)
        run = Run(
            tap_id,
            mode,
            amount,
            now,
            reason,
            max_minutes * 60,
            entity_id=cfg[CONF_TAP_ENTITY],
            name=cfg.get(CONF_NAME, ""),
            flow=bool(flow_entity),
            sequential=bool(cfg.get(CONF_SEQUENTIAL, True)),
            band=band,
            rain_factor=self._pending_factor.pop(tap_id, None) if reason == "schedule" else None,
            cfg_max_seconds=float(cfg.get(CONF_MAX_MINUTES) or DEFAULT_MAX_MINUTES) * 60,
            last_sample=now,
        )
        if flow_entity:
            run.rate_lps = flow_lps(self.hass.states.get(flow_entity))
        self.runs[tap_id] = run
        entity_id = cfg[CONF_TAP_ENTITY]
        try:
            await self._set_tap(entity_id, True)
        except HomeAssistantError as err:
            self.runs.pop(tap_id, None)
            self._record(tap_id, run, RESULT_FAILED)
            _LOGGER.error("Could not open %s: %s", entity_id, err)
            await self._next_in_queue()
            return
        _LOGGER.info("Watering %s: %s %s (%s, %s day)", cfg.get(CONF_NAME), amount, mode, reason, band)

        @callback
        def _tap_changed(event: Event[EventStateChangedData]) -> None:
            new = event.data["new_state"]
            if run.stopping or new is None or new.state in OPEN_STATES:
                return
            old = event.data["old_state"]
            if old is not None and old.state in OPEN_STATES:
                self.hass.async_create_task(self.async_finish(tap_id, RESULT_STOPPED_EXTERNALLY, False))

        run.unsubs.append(async_track_state_change_event(self.hass, entity_id, _tap_changed))
        if flow_entity:
            @callback
            def _flow_changed(event: Event[EventStateChangedData]) -> None:
                self._sample(run, event.data["new_state"])

            run.unsubs.append(async_track_state_change_event(self.hass, flow_entity, _flow_changed))
        run.unsubs.append(
            async_track_time_interval(self.hass, self._flow_tick_cb(tap_id), timedelta(seconds=FLOW_TICK_SECONDS))
        )
        run.unsubs.append(
            async_call_later(self.hass, run.max_seconds, self._finish_cb(tap_id, RESULT_MAX_TIME))
        )
        run.unsubs.append(
            async_call_later(self.hass, OPEN_CONFIRM_SECONDS, self._confirm_open_cb(tap_id))
        )
        self._save()
        self.notify()

    def _sample(self, run: Run, new_state: Any) -> None:
        """Integrate flow since the last sample, then take the new rate."""
        now = dt_util.utcnow()
        if run.last_sample is not None:
            run.litres += run.rate_lps * max((now - run.last_sample).total_seconds(), 0)
        run.last_sample = now
        if new_state is not None:
            run.rate_lps = flow_lps(new_state)
        self._check_target(run, now)

    def _check_target(self, run: Run, now: datetime) -> None:
        if run.stopping or run.mode != MODE_LITRES:
            return
        if run.litres >= run.target:
            self.hass.async_create_task(self.async_finish(run.tap_id, RESULT_COMPLETED))
        elif (now - run.started).total_seconds() >= NO_FLOW_AFTER_SECONDS and run.litres < NO_FLOW_LITRES:
            self.hass.async_create_task(self.async_finish(run.tap_id, RESULT_NO_FLOW))

    def _flow_tick_cb(self, tap_id: str) -> Callable[[datetime], None]:
        @callback
        def _tick(_now: datetime) -> None:
            run = self.runs.get(tap_id)
            if run is None:
                return
            self._sample(run, None)
            self.notify()

        return _tick

    def _finish_cb(self, tap_id: str, result: str) -> Callable[[datetime], Any]:
        async def _done(_now: datetime) -> None:
            run = self.runs.get(tap_id)
            if run is None:
                return
            if run.mode == MODE_MINUTES and result == RESULT_MAX_TIME:
                done = run.target * 60 <= run.cfg_max_seconds
                await self.async_finish(tap_id, RESULT_COMPLETED if done else RESULT_MAX_TIME)
            else:
                await self.async_finish(tap_id, result)

        return _done

    def _confirm_open_cb(self, tap_id: str) -> Callable[[datetime], Any]:
        async def _confirm(_now: datetime) -> None:
            run = self.runs.get(tap_id)
            if run is None:
                return
            state = self.hass.states.get(run.entity_id)
            if state is None or state.state not in OPEN_STATES:
                _LOGGER.error("%s never reported open; stopping the run", run.name)
                await self.async_finish(tap_id, RESULT_FAILED)

        return _confirm

    async def async_finish(self, tap_id: str, result: str, close: bool = True) -> None:
        """End a run: close the tap, record the result, start the next queued run."""
        run = self.runs.get(tap_id)
        if run is None:
            self.queue = [item for item in self.queue if item[0] != tap_id]
            self.notify()
            return
        if run.stopping:
            return
        run.stopping = True
        self._sample(run, None)
        for unsub in run.unsubs:
            unsub()
        if close:
            try:
                await self._set_tap(run.entity_id, False)
            except HomeAssistantError as err:
                _LOGGER.error("Could not close %s: %s", run.name, err)
        self.runs.pop(tap_id, None)
        self._record(tap_id, run, result)
        _LOGGER.info("%s finished: %s, %.1f L", run.name, result, run.litres)
        await self._next_in_queue()

    def _record(self, tap_id: str, run: Run, result: str) -> None:
        end = dt_util.utcnow()
        self.tap_state(tap_id).update(
            last_run=run.started.isoformat(),
            last_end=end.isoformat(),
            last_result=result,
            last_reason=run.reason,
            last_band=run.band,
            last_target=run.target,
            last_minutes=round((end - run.started).total_seconds() / 60, 1),
            last_litres=round(run.litres, 1) if run.flow else None,
        )
        if run.flow:
            self._add_usage(tap_id, run.litres, run.started)
            self._learn_flow(tap_id, run)
        self._log({
            "tap": tap_id,
            "name": run.name,
            "start": run.started.isoformat(),
            "end": end.isoformat(),
            "minutes": round((end - run.started).total_seconds() / 60, 1),
            "litres": round(run.litres, 1) if run.flow else None,
            "target": run.target,
            "mode": run.mode,
            "band": run.band,
            "reason": run.reason,
            "slot": self.tap_state(tap_id).get("last_slot") if run.reason == "schedule" else None,
            "rain_factor": run.rain_factor,
            "result": result,
        })
        self._save()
        self.notify()

    async def _next_in_queue(self) -> None:
        while self.queue and not self._sequential_busy():
            tap_id, amount, mode, reason, band = self.queue.pop(0)
            if tap_id in self.taps and tap_id not in self.runs:
                await self._start(tap_id, amount, mode, reason, band)
        self.notify()

    async def async_stop_tap(self, tap_id: str) -> None:
        await self.async_finish(tap_id, RESULT_STOPPED)

    async def async_stop_all(self) -> None:
        self.queue.clear()
        for tap_id in list(self.runs):
            await self.async_finish(tap_id, RESULT_STOPPED)

    async def _set_tap(self, entity_id: str | None, open_: bool) -> None:
        if not entity_id:
            raise HomeAssistantError("no tap entity")
        domain = entity_id.split(".", 1)[0]
        if domain == "valve":
            service = "open_valve" if open_ else "close_valve"
        else:
            domain, service = "switch", "turn_on" if open_ else "turn_off"
        await self.hass.services.async_call(
            domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )

    # ------------------------------------------------------------ attributes
    def tap_attributes(self, tap_id: str) -> dict[str, Any]:
        cfg = self.tap(tap_id)
        state = self.tap_state(tap_id)
        run = self.runs.get(tap_id)
        nxt = self.next_run(tap_id)
        band = self.band_today()
        attrs: dict[str, Any] = {
            "tap_entity": cfg.get(CONF_TAP_ENTITY),
            "mode": cfg.get(CONF_MODE),
            "amounts": {b: band_amount(cfg, b) for b in (BAND_COOL, BAND_WARM, BAND_HOT)},
            "day_type": band,
            "amount_today": band_amount(cfg, band),
            "rain_factor": round(self.rain_factor(tap_id), 2),
            "scheduled_amount": self.scheduled_amount(tap_id),
            "slot_plan": [
                {
                    "slot": n,
                    "on": on,
                    "when": self.slot_rule(tap_id, n)[0],
                    "share": round(self.slot_rule(tap_id, n)[1] * 100),
                    "runs_today": on and slot_runs_on(self.slot_rule(tap_id, n)[0], band),
                    "amount": round(
                        band_amount(cfg, band) * self.slot_rule(tap_id, n)[1] * self.rain_factor(tap_id), 1
                    ),
                }
                for n, _start, on in self.slots(tap_id)
            ],
            "one_at_a_time": bool(cfg.get(CONF_SEQUENTIAL, True)),
            "queue_position": next(
                (i + 1 for i, item in enumerate(self.queue) if item[0] == tap_id), None
            ),
            "start_time": cfg.get(CONF_START_TIME),
            "slots": [
                {"slot": n, "time": start.isoformat() if start else None, "on": on}
                for n, start, on in self.slots(tap_id)
            ],
            "last_slot": state.get("last_slot"),
            "days": cfg.get(CONF_DAYS, []),
            "rain_threshold_mm": cfg.get(CONF_RAIN_THRESHOLD),
            "rain_window_mm": self.rain_total(),
            "next_run": nxt.isoformat() if nxt else None,
            "last_run": state.get("last_run"),
            "last_result": state.get("last_result"),
            "last_band": state.get("last_band"),
            "last_target": state.get("last_target"),
            "last_rain_factor": state.get("last_rain_factor"),
            "last_minutes": state.get("last_minutes"),
            "last_litres": state.get("last_litres"),
            "last_skip": state.get("last_skip"),
            "last_skip_rain_mm": state.get("last_skip_rain"),
        }
        if run is not None:
            attrs["run"] = {
                **run.as_dict(),
                "elapsed_minutes": round((dt_util.utcnow() - run.started).total_seconds() / 60, 1),
                "flow_lpm": round(run.rate_lps * 60, 1),
            }
        return attrs
