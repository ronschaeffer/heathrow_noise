"""Fetch aircraft.json from the local ADS-B receiver (FLIGHTTRACK)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from heathrow_noise.config import Config

logger = logging.getLogger(__name__)


def fetch_aircraft(config: Config) -> tuple[dict[str, Any], bool]:
    """Fetch aircraft.json. Returns (data, success)."""
    url = config.get(
        "adsb.aircraft_json_url", "http://10.10.10.234/tar1090/data/aircraft.json"
    )
    try:
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        count = len(data.get("aircraft", []))
        logger.debug("Fetched aircraft.json: %d aircraft", count)
        return data, True
    except httpx.TimeoutException:
        logger.warning("Timeout fetching aircraft.json from %s", url)
        return {}, False
    except Exception:
        logger.exception("Failed to fetch aircraft.json from %s", url)
        return {}, False
