# WaterHA

Rain-aware garden watering for Home Assistant. Add your automated taps, say how much
each should water (minutes, or litres from the tap's flow sensor), when, and how much rain
makes a run unnecessary. WaterHA pulls hourly rain for the **past 24 h and the next 24 h**
and skips a scheduled run when their total reaches the tap's threshold.

Works with any `switch` or `valve` tap, such as SONOFF SWV Zigbee valves.

## Install

1. HACS → ⋮ → Custom repositories → `https://github.com/s2n2/WaterHA`, type **Integration**.
2. Download **WaterHA**.
3. Settings → Devices & services → Add integration → **WaterHA**.

## Set up

**Hub** (one per Home Assistant):

| Option | |
|---|---|
| Rain forecast from | **Open-Meteo** (default, no account) or a **weather entity** with an hourly forecast. Past rain always comes from Open-Meteo for your home location. |
| Run one tap at a time | Taps due together wait their turn (keeps pressure up). |

**Taps**: on the WaterHA integration page, choose **Add tap**. Each tap has:

| Setting | |
|---|---|
| Tap | The `switch.*` or `valve.*` that opens it |
| Water by | Minutes, or litres (needs a flow-rate sensor) |
| Amount | Minutes open, or litres to deliver |
| Flow-rate sensor | e.g. `sensor.tap_lawn_volume_flow_rate`; records litres used even in minutes mode |
| Start time, Days | When it runs |
| Rain threshold | Skip when past 24 h + next 24 h rain ≥ this (mm). 0 = never skip |
| Maximum run time | Safety limit; the tap closes after this whatever happens |

Change a tap later with its ⋮ → **Reconfigure**, or from its entities (amount, rain
threshold, start time, automatic on/off) on any dashboard.

## Entities

Hub: `Rain past 24 h`, `Rain next 24 h`, `Rain past and next 24 h` (with an hourly series),
`Stop all taps`, `Refresh rain`.

Per tap: `Status` (idle, watering, waiting, automatic off, holding for rain), `Next run`,
`Last run` (result, minutes, litres, last rain skip), `Water given`, `Automatic watering`,
`Minutes/Litres to water`, `Rain threshold`, `Maximum run time`, `Start time`,
`Water now`, `Stop`.

## Dashboard card

The card is registered automatically:

```yaml
type: custom:waterha-card
title: Garden
```

It shows the hourly rain for the 48 h window (past in grey, forecast in blue, stronger for
higher probability), the totals, and each tap with Water now/Stop, Auto, amount and
threshold (tap the value for one-tap presets or type any number) and its next run.

## Services

- `waterha.water_now`: `entity_id` (the tap switch/valve or any of its WaterHA entities),
  optional `amount`, `mode`. Manual runs ignore the rain threshold.
- `waterha.stop`: the given taps, or all.
- `waterha.refresh_rain`.

## Safety

- Every run has a maximum time, and litres runs stop if no water flows within 5 minutes.
- If the tap does not report open within 90 s, the run is marked failed and the tap is
  sent closed.
- Closing the tap anywhere else ends the run.
- A run in progress when Home Assistant restarts or the integration reloads is closed and
  recorded as interrupted. Runs are not resumed.
- Scheduled runs missed while Home Assistant was down are not caught up.

## Rain data

Open-Meteo hourly precipitation (`past_days`, `forecast_days`) for the home coordinates in
Home Assistant, refreshed every 30 minutes and before a scheduled run if older than that.
If rain data is unavailable at a scheduled time, the tap waters as normal.

## Development

```bash
pip install "pytest-homeassistant-custom-component==0.13.375" ruff==0.17.3
ruff check custom_components tests
pytest -q
```
