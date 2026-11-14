"""Rain data: Open-Meteo (past + forecast) and Home Assistant weather forecasts.

Everything here is pure (no Home Assistant objects) apart from the fetch
helpers, so the window maths can be tested without a running instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class RainHour:
    """Rain in the hour ending at `end` (Open-Meteo's convention)."""

    end: datetime
    mm: float
    probability: float | None = None
    temp: float | None = None  # air temperature (°C) at `end`, when the source gives it
    et: float | None = None  # reference evapotranspiration in the hour (mm)

    def as_dict(self) -> dict[str, Any]:
        return {"t": self.end.isoformat(), "mm": round(self.mm, 2), "p": self.probability}


@dataclass
class RainSummary:
    """Rain either side of `now`."""

    now: datetime
    past_mm: float | None
    next_mm: float | None
    hours: list[RainHour] = field(default_factory=list)
    source: str = ""

    @property
    def total_mm(self) -> float | None:
        if self.past_mm is None and self.next_mm is None:
            return None
        return (self.past_mm or 0.0) + (self.next_mm or 0.0)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _temp(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def day_max_temp(hours: list[RainHour], day: Any, tz: Any) -> float | None:
    """Highest temperature in the hours of local calendar `day` (None if none known)."""
    temps = [h.temp for h in hours if h.temp is not None and h.end.astimezone(tz).date() == day]
    return round(max(temps), 1) if temps else None


def day_et(hours: list[RainHour], day: Any, tz: Any) -> float | None:
    """Evapotranspiration over local calendar `day` (None if the source has none)."""
    values = [h.et for h in hours if h.et is not None and h.end.astimezone(tz).date() == day]
    return round(sum(values), 1) if values else None


def parse_open_meteo(payload: dict[str, Any]) -> list[RainHour]:
    """Turn an Open-Meteo hourly payload (timeformat=unixtime) into RainHours."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rain = hourly.get("precipitation") or []
    prob = hourly.get("precipitation_probability") or []
    temps = hourly.get("temperature_2m") or []
    ets = hourly.get("et0_fao_evapotranspiration") or []
    hours: list[RainHour] = []
    for index, stamp in enumerate(times):
        mm = _num(rain[index]) if index < len(rain) else None
        if mm is None:
            continue
        p = _num(prob[index]) if index < len(prob) else None
        t = _temp(temps[index]) if index < len(temps) else None
        e = _num(ets[index]) if index < len(ets) else None
        hours.append(RainHour(datetime.fromtimestamp(int(stamp), timezone.utc), mm, p, t, e))
    return hours


def parse_weather_forecast(forecast: list[dict[str, Any]]) -> list[RainHour]:
    """Turn a weather.get_forecasts hourly list into RainHours.

    HA forecast entries start at `datetime` and cover the following hour, so
    the hour ends one hour later.
    """
    hours: list[RainHour] = []
    for item in forecast or []:
        start = item.get("datetime")
        if not start:
            continue
        try:
            moment = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        mm = _num(item.get("precipitation"))
        if mm is None:
            mm = 0.0
        hours.append(
            RainHour(
                moment + timedelta(hours=1),
                mm,
                _num(item.get("precipitation_probability")),
                _temp(item.get("temperature")),
            )
        )
    return hours


def intensity_hours(
    points: list[tuple[datetime, float]], start: datetime, end: datetime, to_mm_per_h: float = 1.0
) -> list[RainHour]:
    """Hourly rain from a rain-rate history (each value holds until the next).

    Buckets end on the hour, covering start..end; the last may be partial.
    """
    points = sorted(points)
    if not points:
        return []
    first = start.replace(minute=0, second=0, microsecond=0)
    hours: list[RainHour] = []
    bucket_end = first + timedelta(hours=1)
    while bucket_end - timedelta(hours=1) < end:
        b_start = max(bucket_end - timedelta(hours=1), start)
        b_end = min(bucket_end, end)
        mm = 0.0
        for i, (moment, rate) in enumerate(points):
            seg_start = max(moment, b_start)
            seg_end = min(points[i + 1][0] if i + 1 < len(points) else end, b_end)
            if seg_end > seg_start and rate > 0:
                mm += rate * to_mm_per_h * (seg_end - seg_start).total_seconds() / 3600
        hours.append(RainHour(bucket_end, round(mm, 3)))
        bucket_end += timedelta(hours=1)
    return hours


def sum_window(hours: list[RainHour], start: datetime, end: datetime) -> float | None:
    """Rain from hours whose end falls in (start, end]; None when none do.

    The hour containing `start` or `end` is counted by its overlap share.
    """
    total = 0.0
    found = False
    for hour in hours:
        hour_start = hour.end - timedelta(hours=1)
        overlap = (min(hour.end, end) - max(hour_start, start)).total_seconds()
        if overlap <= 0:
            continue
        found = True
        total += hour.mm * min(overlap / 3600.0, 1.0)
    return round(total, 2) if found else None


def summarise(
    now: datetime,
    past_hours: list[RainHour],
    future_hours: list[RainHour],
    window_hours: int = 24,
    source: str = "",
) -> RainSummary:
    """Sum rain in the window before and after now, and keep a chart series."""
    window = timedelta(hours=window_hours)
    past = sum_window(past_hours, now - window, now)
    nxt = sum_window(future_hours, now, now + window)
    # Chart: what has fallen from the past source, what is coming from the
    # forecast source (the hour in progress counts as coming).
    chart = [h for h in past_hours if now - window < h.end <= now]
    chart += [h for h in future_hours if now < h.end <= now + window + timedelta(hours=1)]
    chart.sort(key=lambda h: h.end)
    return RainSummary(now=now, past_mm=past, next_mm=nxt, hours=chart, source=source)


async def fetch_open_meteo(
    session: aiohttp.ClientSession, latitude: float, longitude: float
) -> list[RainHour]:
    """Fetch hourly rain for yesterday through tomorrow from Open-Meteo."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "hourly": "precipitation,precipitation_probability,temperature_2m,et0_fao_evapotranspiration",
        "past_days": "2",
        "forecast_days": "3",
        "timezone": "UTC",
        "timeformat": "unixtime",
    }
    async with session.get(
        OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    return parse_open_meteo(payload)
