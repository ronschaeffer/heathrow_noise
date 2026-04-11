# heathrow_noise

Heathrow runway alternation tracker for Home Assistant. Publishes noise impact sensors via MQTT and serves a web UI with current status and 7-day forward schedule.

## How it works

- Reads live ADS-B data from a local receiver (`aircraft.json` from tar1090/readsb)
- Classifies which runway is currently receiving arrivals by heading + position
- Computes the 7-day forward alternation schedule algorithmically (strict 2-week cycle)
- Optionally polls `@HeathrowRunways` via the xcancel RSS mirror for deviation notices
- Publishes 13 MQTT sensors to Home Assistant via auto-discovery
- Serves a web UI on port 47480 with current state and schedule table

## Sensors published

| Sensor | Description |
|---|---|
| `heathrow_noise_mode` | `westerly` / `easterly` / `unknown` |
| `heathrow_noise_arrivals_runway` | `27L` / `27R` / `09L` / `09R` |
| `heathrow_noise_overhead_impact` | `HIGH` (27L/09R over Isleworth) or `LOW` |
| `heathrow_noise_next_switch` | Timestamp of next 15:00 or weekly switchover |
| `heathrow_noise_next_quiet` | Timestamp when next LOW impact period begins |
| `heathrow_noise_next_high_impact` | Timestamp when next HIGH impact period begins |
| `heathrow_noise_schedule_json` | JSON array of upcoming periods (7 days) |
| `heathrow_noise_deviation_active` | Binary — deviation notice active |
| `heathrow_noise_deviation_text` | Latest deviation notice text |
| `heathrow_noise_feed_available` | Binary — xcancel RSS feed reachable |
| `heathrow_noise_aircraft_seen` | Aircraft counted on approach in last poll |
| `heathrow_noise_classifier_confidence` | `high` / `medium` / `low` |
| `heathrow_noise_status` | `ok` / `classifying` |

## Data sources

- **ADS-B receiver**: local tar1090/readsb `aircraft.json` (primary, real-time)
- **Schedule**: algorithmic — 2-week alternation cycle from known anchor date
- **Deviations**: `rss.xcancel.com/HeathrowRunways/rss` (free Nitter mirror of @HeathrowRunways)

## Configuration

Copy `config/config.yaml` to your appdata dir and customise:
- `adsb.aircraft_json_url` — URL of your tar1090 aircraft.json
- `home.lat` / `home.lon` — your coordinates
- `mqtt.broker_url` — your EMQX/Mosquitto broker
- `web.external_url` — set to `http://server:47480` in Docker

## Development

```bash
poetry install --with dev
make test
make fix
heathrow-noise status     # one-shot status check
heathrow-noise service    # long-running service
```
