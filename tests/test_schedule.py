"""Tests for the schedule computation."""

from datetime import UTC, date, datetime

from heathrow_noise.schedule import arrivals_runway_for_period, compute_schedule

ANCHOR_DATE = date(2025, 4, 7)  # week of 7 Apr 2025: 27R AM / 27L PM
ANCHOR_AM = "27R"


class TestArrivalsRunway:
    def test_anchor_week_am(self):
        result = arrivals_runway_for_period(ANCHOR_DATE, True, ANCHOR_DATE, ANCHOR_AM)
        assert result == "27R"

    def test_anchor_week_pm(self):
        result = arrivals_runway_for_period(ANCHOR_DATE, False, ANCHOR_DATE, ANCHOR_AM)
        assert result == "27L"

    def test_week_two_flips_am(self):
        # Week of 14 Apr 2025 (one week later) should flip
        d = date(2025, 4, 14)
        assert arrivals_runway_for_period(d, True, ANCHOR_DATE, ANCHOR_AM) == "27L"

    def test_week_two_flips_pm(self):
        d = date(2025, 4, 14)
        assert arrivals_runway_for_period(d, False, ANCHOR_DATE, ANCHOR_AM) == "27R"

    def test_week_three_back_to_anchor(self):
        d = date(2025, 4, 21)
        assert arrivals_runway_for_period(d, True, ANCHOR_DATE, ANCHOR_AM) == "27R"


class TestComputeSchedule:
    def _mock_config(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.get_int.side_effect = lambda key, default=0: {
            "app.schedule_lookahead_days": 7,
            "schedule.switchover_hour": 15,
        }.get(key, default)
        cfg.get.side_effect = lambda key, default=None: {
            "schedule.anchor_date": "2025-04-07",
            "schedule.anchor_am_runway": "27R",
            "runways": {
                "27L": {"impact": "HIGH"},
                "27R": {"impact": "LOW"},
                "09L": {"impact": "LOW"},
                "09R": {"impact": "HIGH"},
            },
        }.get(key, default)
        return cfg

    def test_produces_periods(self):
        from datetime import datetime

        cfg = self._mock_config()
        now = datetime(2025, 4, 7, 10, 0, tzinfo=UTC)
        sched = compute_schedule(cfg, from_dt=now)
        assert len(sched.periods) > 0

    def test_next_switch_is_future(self):
        cfg = self._mock_config()
        now = datetime(2025, 4, 7, 10, 0, tzinfo=UTC)
        sched = compute_schedule(cfg, from_dt=now)
        assert sched.next_switch is not None
        assert sched.next_switch > now

    def test_alternation_flips_at_15(self):
        cfg = self._mock_config()
        # Start at 06:00 on anchor week
        now = datetime(2025, 4, 7, 6, 0, tzinfo=UTC)
        sched = compute_schedule(cfg, from_dt=now)
        if len(sched.periods) >= 2:
            assert sched.periods[0].arrivals_runway != sched.periods[1].arrivals_runway
