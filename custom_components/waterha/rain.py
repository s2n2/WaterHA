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


def parse_open_meteo(payload: dict[str, Any]) -> list[RainHour]:
    """Turn an Open-Meteo hourly payload (timeformat=unixtime) into RainHours."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rain = hourly.get("precipitation") or []
    prob = hourly.get("precipitation_probability") or []
    hours: list[RainHour] = []
    for index, stamp in enumerate(times):
        mm = _num(rain[index]) if index < len(rain) else None
        if mm is None:
            continue
        p = _num(prob[index]) if index < len(prob) else None
        hours.append(RainHour(datetime.fromtimestamp(int(stamp), timezone.utc), mm, p))
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
            RainHour(moment + timedelta(hours=1), mm, _num(item.get("precipitation_probability")))
        )
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
        "hourly": "precipitation,precipitation_probability",
        "past_days": "2",
        "forecast_days": "3",
        "timezone": "UTC",
        "timeformat": "unixtime",
    }
    async with session.get(
        OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    return parse_open_meteo(payload)
