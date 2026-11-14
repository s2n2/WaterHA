"""The WaterHA engine: rain data, schedules and watering runs.

One hub per config entry. Each tap is a config subentry; its settings live in
the subentry data (so the integration page and the entities edit the same
thing). Run history and any run in progress are kept in a Store so a restart
mid-run closes the tap instead of leaving it open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
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
    CONF_COOL_BELOW,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_FLOW_SENSOR,
    CONF_FORECAST_SOURCE,
    CONF_HOT_FROM,
    CONF_MAX_MINUTES,
    CONF_MODE,
    CONF_NAME,
    CONF_RAIN_THRESHOLD,
    CONF_SEQUENTIAL,
    CONF_START_TIME,
    CONF_TAP_ENTITY,
    CONF_WEATHER_ENTITY,
    DAYS,
    DEFAULT_BAND_AMOUNTS,
    DEFAULT_COOL_BELOW,
    DEFAULT_HOT_FROM,
    DEFAULT_MAX_MINUTES,
    DOMAIN,
    FLOW_TICK_SECONDS,
    MODE_LITRES,
    MODE_MINUTES,
    NO_FLOW_AFTER_SECONDS,
    NO_FLOW_LITRES,
    RAIN_REFRESH_MINUTES,
    RAIN_WINDOW_HOURS,
    RESULT_COMPLETED,
    RESULT_FAILED,
    RESULT_INTERRUPTED,
    RESULT_MAX_TIME,
    RESULT_NO_FLOW,
    RESULT_SKIPPED_RAIN,
    RESULT_STOPPED,
    RESULT_STOPPED_EXTERNALLY,
    SIGNAL_UPDATE,
    SOURCE_WEATHER,
    STARTUP_RECHECK_SECONDS,
    STATUS_DISABLED,
    STATUS_IDLE,
    STATUS_QUEUED,
    STATUS_RAIN_HOLD,
    STATUS_WATERING,
    SUBENTRY_TAP,
    amount_key,
)
from .rain import (
    RainHour,
    RainSummary,
    day_max_temp,
    fetch_open_meteo,
    parse_weather_forecast,
    summarise,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
OPEN_STATES = {"on", "open", "opening"}
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


def band_amount(cfg: dict[str, Any], band: str) -> float:
    """A tap's amount for a day type (0.1.x taps had one `amount` for every day)."""
    value = cfg.get(amount_key(band))
    if value is None:
        value = cfg.get(CONF_AMOUNT)
    if value is None:
        value = DEFAULT_BAND_AMOUNTS.get(cfg.get(CONF_MODE, MODE_MINUTES), {}).get(band, 0)
    return float(value)


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

    async def async_stop(self) -> None:
        """Unload: close any open tap so a reload never leaves water running."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
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
        self._save()

    def _as_store(self) -> dict[str, Any]:
        return {
            "taps": self._state,
            "active": {tap_id: run.as_dict() for tap_id, run in self.runs.items()},
        }

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
        """Today's forecast high (local day) from Open-Meteo."""
        tz = dt_util.get_default_time_zone()
        return day_max_temp(self._hours, dt_util.now().date(), tz)

    def band_today(self) -> str:
        return band_for(self.temp_max_today(), self.cool_below, self.hot_from)

    def amount_today(self, tap_id: str) -> float:
        return band_amount(self.tap(tap_id), self.band_today())

    # ------------------------------------------------------------------ rain
    async def _refresh_rain_cb(self, _now: datetime) -> None:
        await self.async_refresh_rain()

    async def async_refresh_rain(self) -> None:
        now = dt_util.utcnow()
        session = async_get_clientsession(self.hass)
        try:
            past = await fetch_open_meteo(session, self.hass.config.latitude, self.hass.config.longitude)
        except Exception as err:  # noqa: BLE001 - network errors of every kind
            _LOGGER.warning("Open-Meteo rain data unavailable: %s", err)
            self.rain_error = f"Open-Meteo: {err}"
            self.notify()
            return
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

    async def _weather_forecast(self, entity_id: str | None) -> list[RainHour]:
        if not entity_id:
            raise ValueError("no weather entity configured")
        response = await self.hass.services.async_call(
            "weather",
            "get_forecasts",
            {ATTR_ENTITY_ID: entity_id, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
        hours = parse_weather_forecast(response[entity_id]["forecast"])
        if not hours:
            raise ValueError("empty hourly forecast")
        return hours

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
    def next_run(self, tap_id: str) -> datetime | None:
        cfg = self.tap(tap_id)
        if not cfg.get(CONF_ENABLED, True):
            return None
        return next_run_after(dt_util.now(), parse_time(cfg.get(CONF_START_TIME)), cfg.get(CONF_DAYS, []))

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
            if self._due(tap_id, local):
                await self._scheduled_run(tap_id, local)
                changed = True
        if changed or self.runs:
            self.notify()

    def _due(self, tap_id: str, local: datetime) -> bool:
        cfg = self.tap(tap_id)
        start = parse_time(cfg.get(CONF_START_TIME))
        if not cfg.get(CONF_ENABLED, True) or start is None:
            return False
        if DAYS[local.weekday()] not in cfg.get(CONF_DAYS, []):
            return False
        if (local.hour, local.minute) != (start.hour, start.minute):
            return False
        return self.tap_state(tap_id).get("last_scheduled") != local.date().isoformat()

    async def _scheduled_run(self, tap_id: str, local: datetime) -> None:
        state = self.tap_state(tap_id)
        state["last_scheduled"] = local.date().isoformat()
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
            state.update(last_skip=dt_util.utcnow().isoformat(), last_skip_rain=total)
            state["last_result"] = RESULT_SKIPPED_RAIN
            self._save()
            return
        band = self.band_today()
        factor = self.rain_factor(tap_id)
        amount = round(band_amount(self.tap(tap_id), band) * factor, 1)
        state.update(
            last_check_temp=self.temp_max_today(), last_check_band=band, last_rain_factor=round(factor, 2)
        )
        if amount <= 0:
            return
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
            "one_at_a_time": bool(cfg.get(CONF_SEQUENTIAL, True)),
            "queue_position": next(
                (i + 1 for i, item in enumerate(self.queue) if item[0] == tap_id), None
            ),
            "start_time": cfg.get(CONF_START_TIME),
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
