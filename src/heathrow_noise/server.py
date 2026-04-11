"""FastAPI web server — data and health endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ha_mqtt_publisher import HealthTracker, make_fastapi_router
import uvicorn

from heathrow_noise import __version__
from heathrow_noise.config import Config
from heathrow_noise.models import HeathrowState

logger = logging.getLogger(__name__)

app = FastAPI(title="Heathrow Noise", version=__version__)

# Module-level state injected by start_server()
_state_ref: dict[str, Any] = {
    "heathrow_state": None,
    "config": None,
    "port": 47480,
}


# Base URL is derived from the incoming request (see index())


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _fmt(dt: datetime | None) -> str:
    """Human-readable London-time string for web UI."""
    if dt is None:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        from zoneinfo import ZoneInfo

        dt = dt.astimezone(ZoneInfo("Europe/London"))
    except Exception:
        pass
    return dt.strftime("%a %d %b, %H:%M %Z")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    state: HeathrowState | None = _state_ref.get("heathrow_state")
    # Derive base URL from incoming request so links work from any address:
    # IP, hostname, or Cloudflare tunnel (heathrownoise.homelab.haus)
    base = str(request.base_url).rstrip("/")

    if state is None:
        body = "<p>Initialising — no data yet.</p>"
    else:
        rwy = state.runway
        sched = state.schedule
        impact_colour = {
            "HIGH": "#d32f2f",
            "LOW": "#388e3c",
            "unknown": "#757575",
        }.get(rwy.overhead_impact.value, "#757575")

        rows = "".join(
            f"<tr><td>{p.start.strftime('%a %d %b %H:%M')}</td>"
            f"<td>{p.end.strftime('%H:%M')}</td>"
            f"<td>{p.arrivals_runway}</td>"
            "<td style='color:"
            + ("#d32f2f" if p.overhead_impact.value == "HIGH" else "#388e3c")
            + "'>"
            + f"{p.overhead_impact.value}</td></tr>"
            for p in sched.periods[:14]
        )

        deviations_html = ""
        if state.deviations:
            items = "".join(
                f"<li>{d.published.strftime('%d %b %H:%M')} — {d.text[:200]}</li>"
                for d in state.deviations
            )
            deviations_html = f"<h2>⚠️ Deviation Notices</h2><ul>{items}</ul>"

        body = f"""
        <div style="margin-bottom:1rem">
          <span style="font-size:2rem;font-weight:bold;color:{impact_colour}">
            {rwy.overhead_impact.value} IMPACT
          </span>
          &nbsp;|&nbsp;
          <strong>{rwy.mode.value}</strong> ops
          &nbsp;|&nbsp;
          Arrivals on <strong>{rwy.arrivals_runway}</strong>
          &nbsp;|&nbsp;
          Confidence: <em>{rwy.confidence}</em>
          &nbsp;|&nbsp;
          {rwy.aircraft_seen} aircraft seen
        </div>
        <p>
          Next switch: <strong>{_fmt(sched.next_switch)}</strong><br>
          Next quiet: <strong>{_fmt(sched.next_quiet_start)}</strong><br>
          Feed: {"✅ available" if state.feed_available else "❌ unavailable"}
        </p>
        {deviations_html}
        <h2>7-Day Schedule</h2>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><th>Start</th><th>End</th><th>Arrivals Runway</th><th>Impact</th></tr>
          {rows}
        </table>
        <p style="color:#888;font-size:0.8rem">
          Updated: {_fmt(state.last_updated)}
        </p>
        """

    return f"""<!DOCTYPE html>
<html><head>
  <title>Heathrow Noise</title>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <style>
    body{{font-family:sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}
    table{{border-collapse:collapse;width:100%}}
    th{{background:#f5f5f5}}
    a{{color:#1976d2}}
  </style>
</head><body>
  <h1>✈️ Heathrow Noise Tracker</h1>
  {body}
  <hr>
  <p><a href="{base}/api/state">JSON state</a> |
     <a href="{base}/api/schedule">schedule</a> |
     <a href="{base}/health">health</a> |
     <a href="{base}/health/mqtt">MQTT health</a>
  </p>
</body></html>"""


@app.get("/api/state")
def api_state() -> JSONResponse:
    state: HeathrowState | None = _state_ref.get("heathrow_state")
    if state is None:
        return JSONResponse({"error": "no data yet"}, status_code=503)
    rwy = state.runway
    sched = state.schedule
    return JSONResponse(
        {
            "mode": rwy.mode.value,
            "arrivals_runway": rwy.arrivals_runway,
            "departures_runway": rwy.departures_runway,
            "overhead_impact": rwy.overhead_impact.value,
            "aircraft_seen": rwy.aircraft_seen,
            "confidence": rwy.confidence,
            "observed_at": _iso(rwy.observed_at),
            "next_switch": _iso(sched.next_switch),
            "next_quiet": _iso(sched.next_quiet_start),
            "next_high_impact": _iso(sched.next_high_impact_start),
            "deviation_active": len(state.deviations) > 0,
            "deviations": [
                {"text": d.text, "published": _iso(d.published), "url": d.url}
                for d in state.deviations
            ],
            "feed_available": state.feed_available,
            "last_updated": _iso(state.last_updated),
            "version": __version__,
        }
    )


@app.get("/api/schedule")
def api_schedule() -> JSONResponse:
    state: HeathrowState | None = _state_ref.get("heathrow_state")
    if state is None:
        return JSONResponse({"error": "no data yet"}, status_code=503)
    return JSONResponse(
        {
            "computed_at": _iso(state.schedule.computed_at),
            "periods": [
                {
                    "start": _iso(p.start),
                    "end": _iso(p.end),
                    "arrivals_runway": p.arrivals_runway,
                    "overhead_impact": p.overhead_impact.value,
                    "is_scheduled": p.is_scheduled,
                }
                for p in state.schedule.periods
            ],
        }
    )


def attach_health_router(tracker: HealthTracker) -> None:
    """Mount the ha_mqtt_publisher health router onto the app."""
    router = make_fastapi_router(tracker)
    # Insert at front so it wins over any catch-all routes
    app.router.routes[:0] = router.routes


def update_state(state: HeathrowState) -> None:
    """Called from the main loop to update the state served by the web server."""
    _state_ref["heathrow_state"] = state


def start_server(config: Config, health_tracker: HealthTracker) -> str:
    """Start uvicorn in a daemon thread. Returns the base URL."""
    port = config.get_int("web.port", 47480)

    _state_ref["config"] = config
    _state_ref["port"] = port

    attach_health_router(health_tracker)

    def _run() -> None:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="uvicorn")
    t.start()
    logger.info("Web server started on port %d", port)

    return f"http://localhost:{port}"
