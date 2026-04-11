"""Data models for Heathrow runway state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OperationsMode(StrEnum):
    WESTERLY = "Westerly"
    EASTERLY = "Easterly"
    UNKNOWN = "Unknown"


class OverheadImpact(StrEnum):
    HIGH = "HIGH"  # 27L or 09R arrivals — directly over Isleworth
    LOW = "LOW"  # 27R or 09L arrivals — further north
    NONE = "None"  # not applicable
    UNKNOWN = "Unknown"


@dataclass
class RunwayState:
    """Current observed runway state from aircraft.json."""

    mode: OperationsMode = OperationsMode.UNKNOWN
    arrivals_runway: str = "Unknown"
    departures_runway: str = "Unknown"
    overhead_impact: OverheadImpact = OverheadImpact.UNKNOWN
    aircraft_seen: int = 0
    observed_at: datetime = field(default_factory=datetime.utcnow)
    confidence: str = "low"


@dataclass
class SchedulePeriod:
    """A single alternation period."""

    start: datetime
    end: datetime
    arrivals_runway: str
    overhead_impact: OverheadImpact
    is_scheduled: bool = True


@dataclass
class ForwardSchedule:
    """Forward-looking alternation schedule."""

    periods: list[SchedulePeriod] = field(default_factory=list)
    computed_at: datetime = field(default_factory=datetime.utcnow)
    next_switch: datetime | None = None
    next_high_impact_start: datetime | None = None
    next_quiet_start: datetime | None = None


@dataclass
class DeviationNotice:
    """Parsed deviation from @HeathrowRunways feed."""

    text: str
    published: datetime
    url: str = ""


@dataclass
class HeathrowState:
    """Combined current + forward state published to HA."""

    runway: RunwayState = field(default_factory=RunwayState)
    schedule: ForwardSchedule = field(default_factory=ForwardSchedule)
    deviations: list[DeviationNotice] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.utcnow)
    feed_available: bool = False
