"""Rain window maths (no Home Assistant needed)."""

from datetime import datetime, timedelta, timezone

from custom_components.waterha.rain import (
    RainHour,
    parse_open_meteo,
    parse_weather_forecast,
    sum_window,
    summarise,
)

NOW = datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc)


def hours(start: datetime, values: list[float]) -> list[RainHour]:
    return [RainHour(start + timedelta(hours=i + 1), v) for i, v in enumerate(values)]


def test_parse_open_meteo_skips_missing_values():
    payload = {"hourly": {"time": [0, 3600, 7200], "precipitation": [0.5, None, 1], "precipitation_probability": [10]}}
    parsed = parse_open_meteo(payload)
    assert [h.mm for h in parsed] == [0.5, 1.0]
    assert parsed[0].probability == 10 and parsed[1].probability is None


def test_parse_weather_forecast_hour_ends_one_hour_after_start():
    parsed = parse_weather_forecast(
        [{"datetime": "2026-11-14T13:00:00+00:00", "precipitation": 2.0, "precipitation_probability": 80},
         {"datetime": "2026-11-14T14:00:00Z"}, {"nope": 1}]
    )
    assert parsed[0].end == datetime(2026, 11, 14, 14, tzinfo=timezone.utc)
    assert parsed[0].mm == 2.0 and parsed[1].mm == 0.0
    assert len(parsed) == 2


def test_sum_window_counts_partial_hours_by_overlap():
    start = NOW.replace(minute=0) - timedelta(hours=1)  # 11:00
    series = hours(start, [1.0, 1.0])  # 11-12, 12-13
    # Window 12:30 -> 13:30 overlaps half of the 12-13 hour only.
    assert sum_window(series, NOW, NOW + timedelta(hours=1)) == 0.5
    assert sum_window(series, NOW + timedelta(hours=5), NOW + timedelta(hours=6)) is None


def test_summarise_uses_past_and_forecast_sources():
    base = NOW.replace(minute=0)
    past = hours(base - timedelta(hours=30), [1.0] * 60)  # 1 mm every hour
    future = hours(base, [0.5] * 30)
    summary = summarise(NOW, past, future, 24, "test")
    assert summary.past_mm == 24.0
    assert summary.next_mm == 12.0
    assert summary.total_mm == 36.0
    # Chart: past hours from `past`, later hours from `future` only.
    assert all(h.mm == 1.0 for h in summary.hours if h.end <= NOW)
    assert all(h.mm == 0.5 for h in summary.hours if h.end > NOW)
