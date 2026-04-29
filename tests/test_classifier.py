"""Tests for the runway classifier."""

from unittest.mock import MagicMock

from heathrow_noise.classifier import classify
from heathrow_noise.models import OperationsMode, OverheadImpact


def _mock_config():
    cfg = MagicMock()
    cfg.get_float.side_effect = lambda key, default=0.0: {
        "adsb.heathrow_lat": 51.4775,
        "adsb.heathrow_lon": -0.4614,
        "adsb.classification_radius_km": 12.0,
        "adsb.max_altitude_ft": 4000.0,
    }.get(key, default)
    cfg.get.side_effect = lambda key, default=None: {
        "runways": {
            "27L": {"impact": "High"},
            "27R": {"impact": "Low"},
            "09L": {"impact": "Low"},
            "09R": {"impact": "High"},
        },
    }.get(key, default)
    return cfg


class TestClassifier:
    def test_empty_returns_unknown(self):
        cfg = _mock_config()
        result = classify({"aircraft": []}, cfg)
        assert result.mode == OperationsMode.UNKNOWN

    def test_westerly_27l_on_approach(self):
        cfg = _mock_config()
        # Aircraft on final approach for 27L: heading ~270, south of split, low alt
        # 27L centreline ~51.4647N, 8km east of Heathrow (~-0.35)
        aircraft = [
            {
                "lat": 51.464,
                "lon": -0.350,
                "alt_baro": 2000,
                "track": 272.0,
            }
        ]
        result = classify({"aircraft": aircraft}, cfg)
        assert result.mode == OperationsMode.WESTERLY
        assert result.arrivals_runway == "27L"
        assert result.overhead_impact == OverheadImpact.HIGH

    def test_westerly_27r_on_approach(self):
        cfg = _mock_config()
        # 27R centreline ~51.4775N (northern)
        aircraft = [
            {
                "lat": 51.478,
                "lon": -0.350,
                "alt_baro": 2000,
                "track": 268.0,
            }
        ]
        result = classify({"aircraft": aircraft}, cfg)
        assert result.mode == OperationsMode.WESTERLY
        assert result.arrivals_runway == "27R"
        assert result.overhead_impact == OverheadImpact.LOW

    def test_too_high_ignored(self):
        cfg = _mock_config()
        aircraft = [
            {
                "lat": 51.464,
                "lon": -0.350,
                "alt_baro": 8000,
                "track": 272.0,
            }
        ]
        result = classify({"aircraft": aircraft}, cfg)
        assert result.mode == OperationsMode.UNKNOWN

    def test_easterly_ops_mid_impact(self):
        """Easterly arrivals → Mid impact regardless of north/south runway."""
        cfg = _mock_config()
        # 09L aircraft (northern runway, heading east)
        aircraft = [
            {"lat": 51.478, "lon": -0.350, "alt_baro": 2000, "track": 90.0},
            {"lat": 51.478, "lon": -0.380, "alt_baro": 1800, "track": 88.0},
        ]
        result = classify({"aircraft": aircraft}, cfg)
        assert result.mode == OperationsMode.EASTERLY
        assert result.overhead_impact == OverheadImpact.MID

    def test_easterly_southern_runway_mid_impact(self):
        """Easterly 09R (southern) also gives Mid, not High."""
        cfg = _mock_config()
        aircraft = [
            {"lat": 51.464, "lon": -0.350, "alt_baro": 2000, "track": 90.0},
            {"lat": 51.464, "lon": -0.380, "alt_baro": 1800, "track": 92.0},
        ]
        result = classify({"aircraft": aircraft}, cfg)
        assert result.mode == OperationsMode.EASTERLY
        assert result.overhead_impact == OverheadImpact.MID
