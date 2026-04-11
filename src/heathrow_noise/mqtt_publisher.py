"""MQTT discovery and publishing for Heathrow noise sensors."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from typing import Any

from ha_mqtt_publisher import Device, Entity, MQTTPublisher

from heathrow_noise import __version__
from heathrow_noise.config import Config
from heathrow_noise.models import HeathrowState, OperationsMode

logger = logging.getLogger(__name__)


def create_publisher(config: Config) -> MQTTPublisher:
    """Create MQTTPublisher from config.

    Config mqtt.broker_url is a plain host or IP (not a URL scheme).
    mqtt.broker_port is a separate integer, defaulting to 1883.
    """
    broker_host = config.get("mqtt.broker_host", "10.10.10.20")
    broker_port = config.get_int("mqtt.broker_port", 1883)
    client_id = config.get("mqtt.client_id", "heathrow_noise")
    return MQTTPublisher(
        broker_url=broker_host,
        broker_port=broker_port,
        client_id=client_id,
    )


def _create_device(config: Config) -> Device:
    prefix = config.get("app.unique_id_prefix", "heathrow_noise")
    return Device(
        config,
        identifiers=[prefix],
        name=config.get("app.name", "Heathrow Noise"),
        manufacturer="Ron (via Claude)",
        model="Runway Alternation Tracker",
        sw_version=__version__,
        configuration_url=config.get("web.external_url", ""),
    )


def _create_entities(config: Config, device: Device) -> list[Entity]:
    prefix = config.get("app.unique_id_prefix", "heathrow_noise")

    def topic(key: str) -> str:
        return f"{prefix}/{key}"

    return [
        Entity(
            config,
            device,
            component="sensor",
            unique_id="mode",
            name="Operations Mode",
            state_topic=topic("mode"),
            icon="mdi:airplane",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="arrivals_runway",
            name="Arrivals Runway",
            state_topic=topic("arrivals_runway"),
            icon="mdi:runway",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="overhead_impact",
            name="Overhead Impact",
            state_topic=topic("overhead_impact"),
            icon="mdi:home-sound-in",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="next_switch",
            name="Next Runway Switch",
            state_topic=topic("next_switch"),
            device_class="timestamp",
            icon="mdi:clock-outline",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="next_quiet",
            name="Next Quiet Period",
            state_topic=topic("next_quiet"),
            device_class="timestamp",
            icon="mdi:volume-off",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="next_high_impact",
            name="Next High Impact Period",
            state_topic=topic("next_high_impact"),
            device_class="timestamp",
            icon="mdi:volume-high",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="schedule_json",
            name="Schedule",
            state_topic=topic("schedule_summary"),
            json_attributes_topic=topic("schedule_json"),
            icon="mdi:calendar-clock",
        ),
        Entity(
            config,
            device,
            component="binary_sensor",
            unique_id="deviation_active",
            name="Deviation Active",
            state_topic=topic("deviation_active"),
            payload_on="yes",
            payload_off="no",
            device_class="problem",
            icon="mdi:alert-circle-outline",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="deviation_text",
            name="Deviation Notice",
            state_topic=topic("deviation_text"),
            icon="mdi:text-box-outline",
        ),
        Entity(
            config,
            device,
            component="binary_sensor",
            unique_id="feed_available",
            name="Feed Available",
            state_topic=topic("feed_available"),
            payload_on="yes",
            payload_off="no",
            device_class="connectivity",
            entity_category="diagnostic",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="aircraft_seen",
            name="Aircraft on Approach",
            state_topic=topic("aircraft_seen"),
            unit_of_measurement="aircraft",
            icon="mdi:airplane-landing",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="classifier_confidence",
            name="Classifier Confidence",
            state_topic=topic("classifier_confidence"),
            entity_category="diagnostic",
            icon="mdi:chart-bar",
        ),
        Entity(
            config,
            device,
            component="sensor",
            unique_id="status",
            name="Status",
            state_topic=topic("status"),
            entity_category="diagnostic",
            icon="mdi:information-outline",
        ),
    ]


def publish_discovery(config: Config, publisher: MQTTPublisher) -> None:
    """Publish HA device-bundle discovery (single retained message)."""
    prefix = config.get("app.unique_id_prefix", "heathrow_noise")
    availability_topic = f"{prefix}/availability"
    disc_prefix = config.get("mqtt.discovery_prefix", "homeassistant")

    device = _create_device(config)
    entities = _create_entities(config, device)

    dev: dict[str, Any] = {
        "ids": prefix,
        "name": device.name,
        "mf": "Ron (via Claude)",
        "mdl": "Runway Alternation Tracker",
        "sw": __version__,
    }
    ext_url = config.get("web.external_url", "")
    if ext_url:
        dev["cu"] = ext_url

    cmps: dict[str, dict] = {}
    for entity in entities:
        comp = entity.get_config_payload().copy()
        comp.pop("device", None)
        comp["p"] = entity.component
        cmps[entity.unique_id] = comp

    payload = {
        "dev": dev,
        "o": {
            "name": "heathrow_noise",
            "sw": __version__,
            "url": "https://github.com/ronschaeffer/heathrow_noise",
        },
        "cmps": cmps,
        "availability": [{"topic": availability_topic}],
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    topic = f"{disc_prefix}/device/{prefix}/config"
    publisher.publish(topic=topic, payload=json.dumps(payload), retain=True)
    logger.info(
        "Published HA discovery bundle (%d entities) to %s", len(entities), topic
    )


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return "unavailable"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def publish_state(
    config: Config, publisher: MQTTPublisher, state: HeathrowState
) -> None:
    """Publish all sensor states."""
    prefix = config.get("app.unique_id_prefix", "heathrow_noise")
    rwy = state.runway
    sched = state.schedule

    schedule_payload = [
        {
            "start": _iso(p.start),
            "end": _iso(p.end),
            "runway": p.arrivals_runway,
            "impact": p.overhead_impact.value,
        }
        for p in sched.periods[:14]
    ]

    deviation_texts = [d.text for d in state.deviations[:3]]

    payloads = {
        "mode": rwy.mode.value,
        "arrivals_runway": rwy.arrivals_runway,
        "overhead_impact": rwy.overhead_impact.value,
        "next_switch": _iso(sched.next_switch),
        "next_quiet": _iso(sched.next_quiet_start),
        "next_high_impact": _iso(sched.next_high_impact_start),
        "schedule_json": json.dumps({"periods": schedule_payload}),
        "schedule_summary": (
            f"{len(schedule_payload)} periods, next: "
            f"{schedule_payload[0]['runway'] if schedule_payload else 'n/a'}"
        ),
        "deviation_active": "yes" if state.deviations else "no",
        "deviation_text": "; ".join(deviation_texts) if deviation_texts else "None",
        "feed_available": "yes" if state.feed_available else "no",
        "aircraft_seen": str(rwy.aircraft_seen),
        "classifier_confidence": rwy.confidence,
        "status": "Ok" if rwy.mode != OperationsMode.UNKNOWN else "Classifying",
    }

    for key, value in payloads.items():
        publisher.publish(f"{prefix}/{key}", value, retain=True)

    logger.debug(
        "Published: mode=%s runway=%s impact=%s confidence=%s deviations=%d",
        rwy.mode.value,
        rwy.arrivals_runway,
        rwy.overhead_impact.value,
        rwy.confidence,
        len(state.deviations),
    )
