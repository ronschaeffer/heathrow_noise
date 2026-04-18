"""Rolling validation of the schedule engine against ADS-B observations
and the Heathrow PDF.

Phase 1: collect up to lookback_samples eligible observations (westerly,
high-confidence, no deviation, 07:00–22:00 UTC) and track the agreement rate
between the schedule engine's prediction and the classifier's observation.

Phase 2: when trigger_consecutive consecutive disagreements accumulate, fetch
and parse the official Heathrow alternation PDF and compare its ruling against
both the engine's prediction and the classifier's observation.

Drift verdict rules
-------------------
  drift_suspected = True  only when BOTH:
    • rolling disagreement_rate >= disagreement_threshold
    • most recent PDF check returned "mismatch"

  pdf_feed_degraded = True when >= pdf_failure_limit consecutive PDF checks
  have all failed — signals the safety net itself is broken.

State persistence
-----------------
The sample buffer, consecutive-disagreement counter, and last PDF check result
are persisted to JSON after every mutation so history survives container restarts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path

from heathrow_noise.config import Config
from heathrow_noise.models import OperationsMode, ValidationResult
from heathrow_noise.pdf_parser import fetch_and_parse, lookup_week

logger = logging.getLogger(__name__)


@dataclass
class _Sample:
    predicted: str
    observed: str
    timestamp: str  # ISO-8601


@dataclass
class _PDFCheck:
    checked_at: str  # ISO-8601
    result: str  # "match" | "mismatch" | "ambiguous" | "unavailable"
    source_url: str
    detail: str
    consecutive_failures: int = 0


class Validator:
    """Accumulates observations and maintains PDF-backed drift detection."""

    def __init__(self, config: Config) -> None:
        self._lookback = config.get_int("validation.lookback_samples", 20)
        self._threshold = config.get_float("validation.disagreement_threshold", 0.40)
        self._trigger_n = config.get_int("validation.trigger_consecutive", 5)
        self._recheck_h = config.get_int("validation.pdf_recheck_interval_hours", 24)
        self._failure_limit = config.get_int("validation.pdf_failure_limit", 3)
        self._state_path = Path(
            config.get("validation.state_file", "/app/config/validation_state.json")
        )

        self._samples: deque[_Sample] = deque(maxlen=self._lookback)
        self._consecutive: int = 0
        self._pdf_fails: int = 0
        self._last_pdf: _PDFCheck | None = None

        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        predicted_runway: str,
        observed_runway: str,
        confidence: str,
        deviation_active: bool,
        mode: OperationsMode,
        now: datetime | None = None,
    ) -> None:
        """Record one observation if conditions make it eligible for comparison.

        Eligibility requirements (all must hold):
          • Westerly operations (easterly has no daytime alternation currently)
          • High classifier confidence
          • No active deviation notice
          • 07:00–22:00 UTC (avoids 06–07 TEAM window and overnight quiet hours)
        """
        now = now or datetime.now(UTC)
        if mode != OperationsMode.WESTERLY:
            return
        if confidence.lower() != "high":
            return
        if deviation_active:
            return
        if not (7 <= now.hour < 22):
            return

        self._samples.append(
            _Sample(
                predicted=predicted_runway,
                observed=observed_runway,
                timestamp=now.isoformat(),
            )
        )
        if predicted_runway != observed_runway:
            self._consecutive += 1
        else:
            self._consecutive = 0

        self._save()

    def compute(
        self,
        observed_runway: str,
        predicted_runway: str,
    ) -> ValidationResult:
        """Return current validation state; triggers a PDF check when warranted."""
        if self._should_pdf_check():
            logger.info(
                "Triggering PDF check after %d consecutive disagreements",
                self._consecutive,
            )
            self._last_pdf = self._do_pdf_check(observed_runway, predicted_runway)
            self._save()

        rate = self._rate()
        pdf_result = self._last_pdf.result if self._last_pdf else "unavailable"

        return ValidationResult(
            agreement_rate=round((1.0 - rate) * 100, 1),
            sample_count=len(self._samples),
            drift_suspected=(rate >= self._threshold and pdf_result == "mismatch"),
            pdf_result=pdf_result,
            pdf_detail=self._last_pdf.detail if self._last_pdf else "No check run yet",
            pdf_last_checked=self._last_pdf.checked_at if self._last_pdf else None,
            pdf_source=self._last_pdf.source_url if self._last_pdf else "",
            pdf_feed_degraded=(self._pdf_fails >= self._failure_limit),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate(self) -> float:
        if not self._samples:
            return 0.0
        n = sum(1 for s in self._samples if s.predicted != s.observed)
        return n / len(self._samples)

    def _should_pdf_check(self) -> bool:
        if self._consecutive < self._trigger_n:
            return False
        if self._last_pdf is None:
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(self._last_pdf.checked_at)
        return age >= timedelta(hours=self._recheck_h)

    def _do_pdf_check(self, observed: str, predicted: str) -> _PDFCheck:
        now = datetime.now(UTC)
        result = fetch_and_parse()

        if not result.success:
            self._pdf_fails += 1
            return _PDFCheck(
                checked_at=now.isoformat(),
                result="unavailable",
                source_url=result.source_url,
                detail=result.error or "Unknown fetch/parse error",
                consecutive_failures=self._pdf_fails,
            )

        self._pdf_fails = 0
        row = lookup_week(result.rows, now.date())

        if row is None:
            return _PDFCheck(
                checked_at=now.isoformat(),
                result="unavailable",
                source_url=result.source_url,
                detail=(
                    "Current week not found in PDF — possible year-boundary edge case"
                ),
                consecutive_failures=0,
            )

        pdf_runway = row.am_runway if now.hour < 15 else row.pm_runway
        engine_ok = pdf_runway == predicted
        obs_ok = pdf_runway == observed

        if engine_ok and obs_ok:
            verdict = "match"
            detail = f"PDF, engine and classifier all agree: {pdf_runway}."
        elif engine_ok and not obs_ok:
            verdict = "match"
            detail = (
                f"PDF says {pdf_runway} — agrees with engine. "
                f"Classifier sees {observed}; likely TEAM or transient."
            )
        elif obs_ok and not engine_ok:
            verdict = "mismatch"
            detail = (
                f"PDF says {pdf_runway} — agrees with classifier ({observed}). "
                f"Engine predicts {predicted}. Schedule rules may have changed."
            )
        else:
            verdict = "ambiguous"
            detail = (
                f"PDF says {pdf_runway}, engine says {predicted}, "
                f"classifier says {observed}. All three differ."
            )

        logger.info("PDF check verdict: %s — %s", verdict, detail)
        return _PDFCheck(
            checked_at=now.isoformat(),
            result=verdict,
            source_url=result.source_url,
            detail=detail,
            consecutive_failures=0,
        )

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
            for s in raw.get("samples", []):
                self._samples.append(_Sample(**s))
            self._consecutive = raw.get("consecutive", 0)
            self._pdf_fails = raw.get("pdf_fails", 0)
            if pdf := raw.get("last_pdf"):
                self._last_pdf = _PDFCheck(**pdf)
            logger.debug(
                "Validation state loaded: %d samples, %d consecutive",
                len(self._samples),
                self._consecutive,
            )
        except Exception as exc:
            logger.warning("Could not load validation state: %s", exc)

    def _save(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(
                    {
                        "samples": [asdict(s) for s in self._samples],
                        "consecutive": self._consecutive,
                        "pdf_fails": self._pdf_fails,
                        "last_pdf": asdict(self._last_pdf) if self._last_pdf else None,
                    },
                    indent=2,
                )
            )
        except Exception as exc:
            logger.warning("Could not save validation state: %s", exc)
