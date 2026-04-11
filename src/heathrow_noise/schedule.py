"""Compute the Heathrow daytime alternation schedule algorithmically.

The schedule is a strict two-week cycle:
  Week A: 27R arrivals 06:00–15:00, 27L arrivals 15:00–last departure
  Week B: 27L arrivals 06:00–15:00, 27R arrivals 15:00–last departure
  Cycle repeats, flipping each Monday at 06:00.

Anchor: week of 2025-04-07 → 27R AM / 27L PM  (verified from 2025 PDF)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import logging

from heathrow_noise.config import Config
from heathrow_noise.models import ForwardSchedule, OverheadImpact, SchedulePeriod

logger = logging.getLogger(__name__)

# Daytime operational hours
_DAY_START = time(6, 0)
_SWITCHOVER = time(15, 0)
_DAY_END = time(23, 0)  # approximate last departure


def _week_number_from_anchor(anchor: date, target: date) -> int:
    """Return weeks elapsed since anchor (0-indexed)."""
    delta = (target - anchor).days
    return delta // 7


def arrivals_runway_for_period(
    target_date: date,
    is_am: bool,
    anchor_date: date,
    anchor_am_runway: str,
) -> str:
    """Return the scheduled arrivals runway for a given date and period."""
    weeks = _week_number_from_anchor(anchor_date, target_date)
    # Each week flips; even weeks = anchor pattern, odd weeks = flipped
    flipped = (weeks % 2) == 1

    anchor_pm = "27L" if anchor_am_runway == "27R" else "27R"

    if not flipped:
        return anchor_am_runway if is_am else anchor_pm
    else:
        return anchor_pm if is_am else anchor_am_runway


def _impact_for_runway(runway: str, config: Config) -> OverheadImpact:
    runway_cfg = config.get("runways", {})
    rwy = runway_cfg.get(runway, {})
    impact_str = rwy.get("impact", "UNKNOWN") if isinstance(rwy, dict) else "UNKNOWN"
    try:
        return OverheadImpact(impact_str)
    except ValueError:
        return OverheadImpact.UNKNOWN


def compute_schedule(
    config: Config, from_dt: datetime | None = None
) -> ForwardSchedule:
    """Compute forward schedule from now for lookahead_days."""
    lookahead = config.get_int("app.schedule_lookahead_days", 7)
    switchover_hour = config.get_int("schedule.switchover_hour", 15)
    anchor_str = config.get("schedule.anchor_date", "2025-04-07")
    anchor_am = config.get("schedule.anchor_am_runway", "27R")
    anchor_date = date.fromisoformat(anchor_str)

    now = from_dt or datetime.now(UTC)
    periods: list[SchedulePeriod] = []

    # Generate periods day by day for lookahead_days
    for day_offset in range(lookahead + 1):
        d = now.date() + timedelta(days=day_offset)

        # AM period: 06:00–15:00
        am_start = datetime.combine(d, time(6, 0), tzinfo=UTC)
        am_end = datetime.combine(d, time(switchover_hour, 0), tzinfo=UTC)
        am_runway = arrivals_runway_for_period(
            d, is_am=True, anchor_date=anchor_date, anchor_am_runway=anchor_am
        )

        # PM period: 15:00–23:00
        pm_start = am_end
        pm_end = datetime.combine(d, time(23, 0), tzinfo=UTC)
        pm_runway = arrivals_runway_for_period(
            d, is_am=False, anchor_date=anchor_date, anchor_am_runway=anchor_am
        )

        periods.append(
            SchedulePeriod(
                start=am_start,
                end=am_end,
                arrivals_runway=am_runway,
                overhead_impact=_impact_for_runway(am_runway, config),
            )
        )
        periods.append(
            SchedulePeriod(
                start=pm_start,
                end=pm_end,
                arrivals_runway=pm_runway,
                overhead_impact=_impact_for_runway(pm_runway, config),
            )
        )

    # Trim past periods, keep from current period onward
    periods = [p for p in periods if p.end > now]

    # Find next switch and next quiet start
    next_switch = periods[1].start if len(periods) > 1 else None
    next_quiet = next(
        (p.start for p in periods if p.overhead_impact == OverheadImpact.LOW),
        None,
    )
    next_high = next(
        (p.start for p in periods if p.overhead_impact == OverheadImpact.HIGH),
        None,
    )

    return ForwardSchedule(
        periods=periods[: lookahead * 2],
        computed_at=now,
        next_switch=next_switch,
        next_high_impact_start=next_high,
        next_quiet_start=next_quiet,
    )
