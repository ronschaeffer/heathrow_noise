"""Classify current runway state from aircraft.json data."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from haversine import Unit, haversine

from heathrow_noise.config import Config
from heathrow_noise.models import OperationsMode, OverheadImpact, RunwayState

logger = logging.getLogger(__name__)

# ILS glideslope centreline lats (WGS84)
# 27L southern runway threshold ~51.4647°N
# 27R northern runway threshold ~51.4775°N
_RUNWAY_CENTRELINE_LAT = {
    "27L": 51.4647,
    "27R": 51.4775,
    "09L": 51.4775,
    "09R": 51.4647,
}

# On final approach inbound heading (degrees magnetic, approximate)
_ARRIVAL_HEADING = {
    "27L": 270,  # westerly arrival — heading west
    "27R": 270,
    "09L": 90,  # easterly arrival — heading east
    "09R": 90,
}
_HEADING_TOLERANCE = 35  # ±35° from centreline heading counts as "on approach"


def _heading_matches(track: float, expected: int) -> bool:
    diff = abs((track - expected + 180) % 360 - 180)
    return diff <= _HEADING_TOLERANCE


def _is_on_approach(
    lat: float,
    lon: float,
    alt_ft: float,
    track: float,
    heathrow_lat: float,
    heathrow_lon: float,
    radius_km: float,
    max_alt: float,
) -> tuple[bool, str | None]:
    """Return (is_on_approach, runway_hint) based on position/heading."""
    dist = haversine((lat, lon), (heathrow_lat, heathrow_lon), unit=Unit.KILOMETERS)
    if dist > radius_km or alt_ft > max_alt:
        return False, None

    # Determine E/W mode from track
    westerly = _heading_matches(track, 270)
    easterly = _heading_matches(track, 90)
    if not (westerly or easterly):
        return False, None

    # Distinguish north/south runway by latitude
    # Northern runway centre ~51.4775, southern ~51.4647; split at ~51.471
    north_south_split = 51.471
    northern = lat >= north_south_split

    if westerly:
        runway = "27R" if northern else "27L"
    else:
        runway = "09L" if northern else "09R"

    return True, runway


def classify(
    aircraft_data: dict[str, Any],
    config: Config,
) -> RunwayState:
    """Classify current runway state from a parsed aircraft.json payload."""
    heathrow_lat = config.get_float("adsb.heathrow_lat", 51.4775)
    heathrow_lon = config.get_float("adsb.heathrow_lon", -0.4614)
    radius_km = config.get_float("adsb.classification_radius_km", 12.0)
    max_alt = config.get_float("adsb.max_altitude_ft", 4000)

    runway_hits: dict[str, int] = {}

    for ac in aircraft_data.get("aircraft", []):
        lat = ac.get("lat")
        lon = ac.get("lon")
        alt = ac.get("alt_baro") or ac.get("alt_geom")
        track = ac.get("track")

        if lat is None or lon is None or alt is None or track is None:
            continue
        if isinstance(alt, str):  # "ground"
            continue

        on_approach, runway = _is_on_approach(
            lat,
            lon,
            float(alt),
            float(track),
            heathrow_lat,
            heathrow_lon,
            radius_km,
            max_alt,
        )
        if on_approach and runway:
            runway_hits[runway] = runway_hits.get(runway, 0) + 1

    if not runway_hits:
        return RunwayState(
            mode=OperationsMode.UNKNOWN,
            arrivals_runway="Unknown",
            departures_runway="Unknown",
            overhead_impact=OverheadImpact.UNKNOWN,
            aircraft_seen=0,
            observed_at=datetime.now(UTC),
            confidence="low",
        )

    # Most-seen runway is the arrivals runway
    arrivals_runway = max(runway_hits, key=lambda r: runway_hits[r])
    total = sum(runway_hits.values())
    dominant_fraction = runway_hits[arrivals_runway] / total

    if dominant_fraction > 0.75:
        confidence = "high"
    elif dominant_fraction > 0.5:
        confidence = "medium"
    else:
        confidence = "low"

    # Derive ops mode and departures runway
    if arrivals_runway in ("27L", "27R"):
        mode = OperationsMode.WESTERLY
        departures_runway = "27R" if arrivals_runway == "27L" else "27L"
    elif arrivals_runway in ("09L", "09R"):
        mode = OperationsMode.EASTERLY
        departures_runway = "09R" if arrivals_runway == "09L" else "09L"
    else:
        mode = OperationsMode.UNKNOWN
        departures_runway = "unknown"

    # Impact relative to home position
    runway_cfg = config.get("runways", {})
    rwy_info = runway_cfg.get(arrivals_runway, {})
    impact_str = (
        rwy_info.get("impact", "UNKNOWN") if isinstance(rwy_info, dict) else "UNKNOWN"
    )
    try:
        impact = OverheadImpact(impact_str)
    except ValueError:
        impact = OverheadImpact.UNKNOWN

    return RunwayState(
        mode=mode,
        arrivals_runway=arrivals_runway,
        departures_runway=departures_runway,
        overhead_impact=impact,
        aircraft_seen=total,
        observed_at=datetime.now(UTC),
        confidence=confidence,
    )
