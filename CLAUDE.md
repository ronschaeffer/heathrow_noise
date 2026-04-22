# CLAUDE.md — heathrow_noise

## What this is

Containerised Python service that classifies the Heathrow runway alternation
state from a local ADS-B receiver, computes a 7-day forward noise schedule,
and publishes MQTT sensors to Home Assistant via auto-discovery.
Also serves a web UI + JSON API on port 47480.

## Type: App (not a library)

- Public repo: `ronschaeffer/heathrow_noise`
- Runs as Docker container on Unraid (`heathrow_noise`)
- Image: `ghcr.io/ronschaeffer/heathrow_noise`
- Entry point: `heathrow-noise` script → `heathrow_noise.__main__:main`

## Key dependencies

- `ha-mqtt-publisher` (ronschaeffer/ha_mqtt_publisher) — MQTT + HA discovery
- `haversine` — distance/proximity calculations for approach detection
- `httpx` — fetch aircraft.json from FLIGHTTRACK receiver
- `feedparser` — parse xcancel RSS feed (@HeathrowRunways mirror)
- `fastapi` + `uvicorn` — web server
- `pypdf` — parse Heathrow's annual alternation PDF for schedule validation

## Toolchain

Python 3.11+, Poetry, ruff, pytest

## Key commands

```bash
poetry install --with dev
make fix          # lint + format
make test         # pytest
make ci-check     # lint + test
heathrow-noise status   # one-shot: classify + print state
heathrow-noise service  # long-running loop
```

## Architecture

```
src/heathrow_noise/
  config.py          Config with .data property (required by ha_mqtt_publisher)
  models.py          Dataclasses: RunwayState, ForwardSchedule, HeathrowState, ValidationResult
  receiver.py        Fetch aircraft.json from FLIGHTTRACK (http://10.10.10.234)
  classifier.py      Classify arrivals runway from aircraft positions + headings
  schedule.py        Algorithmic 2-week alternation cycle → 7-day forward schedule
  deviation_feed.py  Poll rss.xcancel.com/HeathrowRunways/rss for deviations
  pdf_parser.py      Fetch + parse Heathrow annual PDF; two-step URL discovery
  validator.py       Rolling classifier-vs-engine comparison + PDF-backed drift detection
  mqtt_publisher.py  HA device-bundle discovery + per-poll state publishing
  server.py          FastAPI: /, /api/state, /api/schedule, /health, /health/mqtt
  __main__.py        CLI entry: service loop + status one-shot
```

## Data sources

1. **ADS-B receiver** — `http://10.10.10.234/tar1090/data/aircraft.json`
   (FLIGHTTRACK host, tar1090/readsb, antenna on Ron's roof)
2. **Schedule** — computed algorithmically from anchor `2025-04-07` (27R AM/27L PM)
   Strict 2-week cycle, switchover at 15:00 daily, weekly flip Mon 06:00
3. **Deviations** — `rss.xcancel.com/HeathrowRunways/rss` (Nitter mirror of @HeathrowRunways)
   Polled every 10 min, keyword-filtered, degrades gracefully if unavailable
4. **Validation PDF** — `heathrow.com/…/Heathrow_Runway_Alternation_Programme_{year}.pdf`
   Fetched on demand (triggered by sustained classifier/engine disagreement).
   Two-step fetch: predictable CDN URL first, then page-scrape fallback.

## Runway geometry (relative to Ron's house at 51.462°N, -0.329°E)

- **27L** (southern, ~51.4647°N centreline) — flies DIRECTLY over Isleworth → **High impact**
- **27R** (northern, ~51.4775°N centreline) — further north, less noise → **Low impact**
- **09R** (easterly southern) — **High impact** departures
- **09L** (easterly northern) — **Low impact**
- Classification: aircraft within 12km of Heathrow, <4000ft, heading ±35° of 270 (westerly) or 90 (easterly)
- North/south runway split at lat 51.471°N

## ha_mqtt_publisher integration

Uses the **device-bundle** discovery pattern (single retained message to
`homeassistant/device/{prefix}/config`). Follows the same pattern as `flights`.

Key: `Entity(config, device, component="sensor", unique_id=..., **kwargs)`
where `config` must have a `.data` property returning the raw dict.
`HealthTracker.attach(publisher)` wires MQTT liveness automatically.

## MQTT topics

All under prefix `heathrow_noise/`:
`mode`, `arrivals_runway`, `overhead_impact`, `next_switch`, `next_quiet`,
`next_high_impact`, `schedule_json` (state=current runway), `deviation_active`,
`deviation_text`, `feed_available`, `aircraft_seen`, `classifier_confidence`,
`schedule_agreement_rate`, `schedule_drift_suspected`, `pdf_verification_result`,
`pdf_verification_detail`, `pdf_last_checked`, `pdf_feed_degraded`

Availability: `heathrow_noise/availability`

## Health endpoint

`/health/mqtt` — returns 200 only if MQTT broker connected AND a publish
succeeded within 3× poll_interval seconds. Used by Docker HEALTHCHECK.

## Configuration

`config/config.yaml` — key settings:
- `adsb.aircraft_json_url` — FLIGHTTRACK aircraft.json URL
- `home.lat/lon` — Ron's house coords (51.46234642850292, -0.32897472370677866)
- `schedule.anchor_date` — update each January when new PDF published
- `deviation_feed.enabled` — set false to disable xcancel polling
- `web.external_url` — set to `http://server:47480` for Docker
- `validation.*` — rolling buffer size, disagreement threshold, PDF check interval

## Coding conventions

- Line length: 88, double quotes, lf endings
- No f-strings in logging (G004)
- Type hints on all public API
- StrEnum for models (not str+Enum)
- HA sensor values use title case throughout (e.g. `High`/`Low`, not `HIGH`/`LOW`)

## ⚠ Mounted config vs baked-in config

The Docker image bakes `config/config.yaml` at build time, but the production
container mounts the host directory `/mnt/user/appdata/heathrow_noise/config`
over `/app/config`, which **takes precedence**. This means:

- Changes to `config/config.yaml` in the repo are NOT picked up automatically
  on container restart unless the host file is also updated.
- **Critical for StrEnum value changes**: if a StrEnum value is renamed
  (e.g. `"HIGH"` → `"High"` in `OverheadImpact`), the corresponding string
  in the live host config must be updated manually, otherwise `OverheadImpact(value)`
  raises `ValueError` and falls back to `UNKNOWN` silently.
- After any config-value-affecting code change, always update the host config:
  ```bash
  # on Unraid
  sed -i 's/impact: "HIGH"/impact: "High"/g' \
    /mnt/user/appdata/heathrow_noise/config/config.yaml
  docker restart heathrow_noise
  ```
- The validation state file (`validation_state.json`) lives in the same
  mounted directory and persists across container restarts intentionally.

## Ship it checklist

1. `make ci-check`
2. `make fix`, commit, push to main
3. Bump version in `pyproject.toml` + `src/heathrow_noise/__init__.py`
4. Tag `v0.x.y`, push → triggers `docker-publish.yml`
5. Pull new image and recreate container on Unraid
6. If any config values changed (especially StrEnum strings), update the live
   host config at `/mnt/user/appdata/heathrow_noise/config/config.yaml` and restart
7. Verify: `/health/mqtt` returns 200, HA entities appear with correct values
