# WaterHA

Water-saving garden watering for Home Assistant. Add your automated taps and set how much
each waters on a **cool, warm or hot day** (minutes, or litres from the tap's flow sensor),
when, and how much rain makes a run unnecessary. WaterHA pulls hourly rain for the **past
24 h and the next 24 h** and today's forecast high, then:

- picks the cool, warm or hot amount from today's forecast high,
- **shortens** a scheduled run in proportion to rain below the tap's threshold
  (2.5 mm against a 5 mm threshold waters 50%),
- **skips** it when rain reaches the threshold,
- runs taps marked **one at a time** one after another, so several taps can share a start time,
- gives each tap **two daily slots** (e.g. morning and evening), each with its own on/off.

Works with any `switch` or `valve` tap, such as SONOFF SWV Zigbee valves.

## Install

1. HACS → ⋮ → Custom repositories → `https://github.com/s2n2/WaterHA`, type **Integration**.
2. Download **WaterHA**.
3. Settings → Devices & services → Add integration → **WaterHA**.

## Set up

**Hub** (one per Home Assistant):

| Option | |
|---|---|
| Rain forecast from | **Open-Meteo** (default, no account) or a **weather entity** with an hourly forecast. Past rain and temperature always come from Open-Meteo for your home location. |
| Cool day below | Today's forecast high below this is a cool day. Default 16 °C. |
| Hot day from | Today's forecast high at or above this is a hot day. Default 23 °C. In between is warm. |
| Backup forecast | Weather entities used when Open-Meteo is unreachable: the first with an hourly forecast gives the next 24 h, and its daily forecast today's high. Empty = every weather entity with an hourly forecast. |
| Backup past rain | A rain-rate sensor (mm/h) whose recorded history gives the past 24 h when Open-Meteo is unreachable, e.g. `sensor.pirateweather_precip_intensity`. Empty = the first current rain-rate sensor. |

**Taps**: on the WaterHA integration page, choose **Add tap**. Each tap has:

| Setting | |
|---|---|
| Tap | The `switch.*` or `valve.*` that opens it |
| Water by | Minutes, or litres (needs a flow-rate sensor) |
| Cool / warm / hot day amount | Minutes open, or litres to deliver, for each day type |
| Flow-rate sensor | e.g. `sensor.tap_lawn_volume_flow_rate`; records litres used even in minutes mode |
| Slot 1 / Slot 2 | Two daily times, each switched on or off (default 06:00 on, 19:00 off). Each slot that runs gives the full amount, less for rain. |
| Days | Which days the slots run |
| Rain threshold | Skip when past 24 h + next 24 h rain ≥ this (mm); less rain shortens the run in proportion. 0 = ignore rain |
| Maximum run time | Safety limit; the tap closes after this whatever happens |
| One at a time | Wait for other one-at-a-time taps to finish. Taps due together run in the order they were added. |

Change a tap later with its ⋮ → **Reconfigure**, or from its entities on any dashboard.

### How a scheduled run is worked out

```
amount  = the tap's cool / warm / hot amount for today's forecast high
factor  = 1 − (rain past 24 h + next 24 h) ÷ rain threshold     (0 … 1)
run     = amount × factor        → skipped when factor is 0
```

**Water now** (button, card or service) uses today's day-type amount in full; it ignores rain.

## Entities

Hub: `Rain past 24 h`, `Rain next 24 h`, `Rain past and next 24 h` (with an hourly series),
`Forecast high today`, `Day type` (cool, warm, hot), `Cool day below`, `Hot day from`,
`Stop all taps`, `Refresh rain`.

Per tap: `Status` (idle, watering, waiting, automatic off, holding for rain; attributes include
today's amount, the rain factor and queue position), `Next run`, `Last run` (result, day type,
minutes, litres, rain skip), `Water given`, `Automatic watering`, `One at a time`,
`Cool / Warm / Hot day amount`, `Rain threshold`, `Maximum run time`, `Slot 1`, `Slot 2`,
`Slot 1 time`, `Slot 2 time`,
`Water now`, `Stop`.

## Dashboard card

The card is registered automatically:

```yaml
type: custom:waterha-card
title: Garden
```

It shows the hourly rain for the 48 h window (past in grey, forecast in blue, stronger for
higher probability), the totals and today's day type (tap it to change the splits). Each tap
has Water now/Stop, Auto, One at a time, its cool/warm/hot amounts (today's is outlined; tap
one for one-tap presets or to type any value), rain threshold, its two slots (tick to turn a
slot on or off, tap the time to change it), and what its next run will give after rain.

### History card

```yaml
type: custom:waterha-history-card
title: Watering history   # optional
hours: 24                 # chart window, optional
limit: 15                 # log rows before "Show all", optional
```

A timeline of the last 24 h with a row per tap: a bar for each run (blue ran, orange stopped
early, red failed, pulsing while watering) and a green diamond for a rain skip. Below it, the
log: when, which tap, minutes and litres, result, and whether it was a scheduled slot or
manual, the day type and any rain reduction. WaterHA keeps 7 days of log in the
`Watering log` sensor (its state is the number of runs in the last 24 h).

## Services

- `waterha.water_now`: `entity_id` (the tap switch/valve or any of its WaterHA entities),
  optional `amount`, `mode`. Without an amount, today's day-type amount. Ignores rain.
- `waterha.stop`: the given taps, or all.
- `waterha.refresh_rain`.

## Safety

- **When Home Assistant starts, every WaterHA tap is sent closed**, in case one was stuck on,
  and any still reporting open a minute later are closed again. (Reloading the integration
  while HA is running does not touch taps you opened by hand.)
- Every run has a maximum time, and litres runs stop if no water flows within 5 minutes.
- If the tap does not report open within 90 s, the run is marked failed and the tap is
  sent closed.
- Closing the tap anywhere else ends the run.
- A run in progress when Home Assistant stops or the integration reloads is closed and
  recorded as interrupted. Runs are not resumed, and missed schedules are not caught up.

## Rain and temperature data

Open-Meteo hourly precipitation and temperature for the home coordinates in Home
Assistant, refreshed every 30 minutes and before a scheduled run if older than that.

When Open-Meteo cannot be reached, WaterHA uses Home Assistant's own data instead (the
card's source line says "Backup: …"): the hourly forecast of a weather entity for the next
24 h and its daily forecast for today's high, and the recorded history of a rain-rate sensor
for the past 24 h. Open-Meteo is tried again at every refresh and takes over when it is back.

With no rain data at all at a scheduled time, the tap waters in full; with no temperature,
it uses the warm-day amount. The card always loads, showing "–" until data arrives.

## Upgrading

From 0.2: a tap's start time becomes slot 1 (on); slot 2 starts off at 19:00.

### From 0.1

A 0.1 tap's single amount becomes its cool, warm and hot amount until you change them, and
every tap starts as one at a time. The old `Minutes/Litres to water` entity is removed.

## Development

```bash
pip install "pytest-homeassistant-custom-component==0.13.375" "home-assistant-frontend==20260930.5" ruff==0.17.3
ruff check custom_components tests
pytest -q
```

`tests/card-preview.html` shows the card with a fake Home Assistant (serve the repo folder
over HTTP and open it).
