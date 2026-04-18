"""Entry point for heathrow-noise service."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import logging
import threading
import time

from ha_mqtt_publisher import (
    AvailabilityPublisher,
    HealthTracker,
    install_signal_handlers,
)

from heathrow_noise.classifier import classify
from heathrow_noise.config import Config
from heathrow_noise.deviation_feed import fetch_deviations
from heathrow_noise.models import HeathrowState
from heathrow_noise.mqtt_publisher import (
    create_publisher,
    publish_discovery,
    publish_state,
)
from heathrow_noise.receiver import fetch_aircraft
from heathrow_noise.schedule import compute_schedule
from heathrow_noise.server import start_server, update_state
from heathrow_noise.validator import Validator

logger = logging.getLogger(__name__)


def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def cmd_service(config: Config) -> None:
    """Long-running service loop."""
    poll_interval = config.get_int("app.poll_interval_seconds", 60)
    deviation_interval = config.get_int("deviation_feed.poll_interval_seconds", 600)
    prefix = config.get("app.unique_id_prefix", "heathrow_noise")
    availability_topic = f"{prefix}/availability"

    publisher = create_publisher(config)
    validator = Validator(config)

    health_tracker = HealthTracker(max_publish_age_seconds=poll_interval * 3)
    health_tracker.attach(publisher)

    base_url = start_server(config, health_tracker)
    logger.info("Service starting — web UI at %s", base_url)

    publisher.connect()

    availability = AvailabilityPublisher(publisher, availability_topic)
    availability.online(retain=True)

    publish_discovery(config, publisher)

    _orig_on_connect = publisher.client.on_connect

    def _on_reconnect(client, userdata, *args, **kwargs):
        if _orig_on_connect:
            _orig_on_connect(client, userdata, *args, **kwargs)
        if publisher._connected:
            try:
                availability.online(retain=True)
                publish_discovery(config, publisher)
                logger.info("Re-published discovery after reconnect")
            except Exception:
                logger.exception("Failed to re-publish on reconnect")

    publisher.client.on_connect = _on_reconnect

    shutdown_event = threading.Event()

    with install_signal_handlers(shutdown_cb=shutdown_event.set):
        current_state = HeathrowState()
        deviations: list = []
        feed_available = False
        last_deviation_poll = 0.0

        while not shutdown_event.is_set():
            loop_start = time.monotonic()

            # --- ADS-B receiver ---
            aircraft_data, receiver_ok = fetch_aircraft(config)
            if receiver_ok:
                runway_state = classify(aircraft_data, config)
            else:
                runway_state = current_state.runway
                runway_state.confidence = "low"

            # --- Forward schedule ---
            schedule = compute_schedule(config)

            # --- Deviation feed (less frequent) ---
            now_ts = time.monotonic()
            if now_ts - last_deviation_poll >= deviation_interval:
                deviations, feed_available = fetch_deviations(config)
                last_deviation_poll = now_ts
                if deviations:
                    logger.info("%d deviation notice(s) active", len(deviations))

            # --- Schedule validation ---
            # schedule.periods[0] is the current period when inside 06:00–23:00;
            # record() gates out ineligible observations via its own hour check.
            predicted_runway = (
                schedule.periods[0].arrivals_runway if schedule.periods else "Unknown"
            )
            validator.record(
                predicted_runway=predicted_runway,
                observed_runway=runway_state.arrivals_runway,
                confidence=runway_state.confidence,
                deviation_active=bool(deviations),
                mode=runway_state.mode,
            )
            validation_result = validator.compute(
                observed_runway=runway_state.arrivals_runway,
                predicted_runway=predicted_runway,
            )

            # --- Combine and publish ---
            current_state = HeathrowState(
                runway=runway_state,
                schedule=schedule,
                deviations=deviations,
                last_updated=datetime.now(UTC),
                feed_available=feed_available,
                validation=validation_result,
            )

            try:
                publish_state(config, publisher, current_state)
            except Exception:
                logger.exception("Failed to publish MQTT state")

            update_state(current_state)

            elapsed = time.monotonic() - loop_start
            shutdown_event.wait(timeout=max(0.0, poll_interval - elapsed))

    try:
        availability.offline(retain=True)
        publisher.disconnect()
    except Exception:
        pass
    logger.info("Shutdown complete")


def cmd_status(config: Config) -> None:
    """One-shot: fetch state and print to stdout."""
    aircraft_data, ok = fetch_aircraft(config)
    if not ok:
        print("ERROR: Could not fetch aircraft.json")
        return
    runway = classify(aircraft_data, config)
    schedule = compute_schedule(config)
    deviations, feed_ok = fetch_deviations(config)

    print(f"Mode:            {runway.mode.value}")
    print(f"Arrivals runway: {runway.arrivals_runway}")
    print(f"Overhead impact: {runway.overhead_impact.value}")
    print(f"Confidence:      {runway.confidence}")
    print(f"Aircraft seen:   {runway.aircraft_seen}")
    print(f"Next switch:     {schedule.next_switch}")
    print(f"Next quiet:      {schedule.next_quiet_start}")
    feed_str = "feed ok" if feed_ok else "feed unavailable"
    print(f"Deviations:      {len(deviations)} ({feed_str})")
    if deviations:
        for d in deviations:
            print(f"  [{d.published.strftime('%d %b %H:%M')}] {d.text[:120]}")
    print(f"\n7-day schedule ({len(schedule.periods)} periods):")
    for p in schedule.periods[:6]:
        print(
            f"  {p.start.strftime('%a %d %b %H:%M')}–{p.end.strftime('%H:%M')}"
            f"  {p.arrivals_runway}  [{p.overhead_impact.value}]"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Heathrow Noise Tracker")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: config/config.yaml)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("service", help="Run the long-running service")
    sub.add_parser("status", help="Print current status and exit")

    args = parser.parse_args()
    _configure_logging(args.log_level)

    from pathlib import Path

    config = Config(Path(args.config) if args.config else None)

    if args.command == "service":
        cmd_service(config)
    elif args.command == "status":
        cmd_status(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
