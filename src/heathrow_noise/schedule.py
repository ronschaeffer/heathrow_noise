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
    all_periods: list[SchedulePeriod] = []

    for day_offset in range(lookahead + 1):
        d = now.date() + timedelta(days=day_offset)

        am_start = datetime.combine(d, time(6, 0), tzinfo=UTC)
        am_end = datetime.combine(d, time(switchover_hour, 0), tzinfo=UTC)
        am_runway = arrivals_runway_for_period(
            d, is_am=True, anchor_date=anchor_date, anchor_am_runway=anchor_am
        )

        pm_start = am_end
        pm_end = datetime.combine(d, time(23, 0), tzinfo=UTC)
        pm_runway = arrivals_runway_for_period(
            d, is_am=False, anchor_date=anchor_date, anchor_am_runway=anchor_am
        )

        all_periods.append(
            SchedulePeriod(
                start=am_start,
                end=am_end,
                arrivals_runway=am_runway,
                overhead_impact=_impact_for_runway(am_runway, config),
            )
        )
        all_periods.append(
            SchedulePeriod(
                start=pm_start,
                end=pm_end,
                arrivals_runway=pm_runway,
                overhead_impact=_impact_for_runway(pm_runway, config),
            )
        )

    # Find which period is current (now falls within it)
    current_period: SchedulePeriod | None = None
    for p in all_periods:
        if p.start <= now < p.end:
            current_period = p
            break

    # Future periods = those that start after now
    future_periods = [p for p in all_periods if p.start > now]

    # next_switch = start of the very next period boundary after now
    next_switch = future_periods[0].start if future_periods else None

    # next_quiet = next future period with LOW impact
    # (skip current even if LOW — we want the *next* change)
    next_quiet = next(
        (p.start for p in future_periods if p.overhead_impact == OverheadImpact.LOW),
        None,
    )

    # next_high = next future period with HIGH impact
    next_high = next(
        (p.start for p in future_periods if p.overhead_impact == OverheadImpact.HIGH),
        None,
    )

    # If currently in HIGH, next_quiet is the more useful "relief" timestamp
    # If currently in LOW, next_high is the more useful "incoming noise" timestamp
    # Both are already correct from the future_periods search above.

    # Periods to publish: current + future, capped at lookahead days
    periods_to_publish = []
    if current_period:
        periods_to_publish.append(current_period)
    periods_to_publish.extend(future_periods[: lookahead * 2])

    return ForwardSchedule(
        periods=periods_to_publish,
        computed_at=now,
        next_switch=next_switch,
        next_high_impact_start=next_high,
        next_quiet_start=next_quiet,
    )
