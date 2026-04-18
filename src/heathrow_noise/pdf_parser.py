"""Fetch and parse Heathrow's annual runway alternation PDF.

Two-step fetch strategy:
  1. Fast path — try the predictable CDN URL for the current year.
  2. Fallback — scrape the Heathrow alternation page for any PDF link
     matching "runway-alternation" and ".pdf", preferring the current year.

If both fail, or if the PDF contains no recognisable daytime schedule rows,
returns PDFParseResult(success=False). A parse failure is always treated as
unavailable — never as a mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_CDN_TEMPLATE = (
    "https://www.heathrow.com/content/dam/heathrow/web/common/documents/"
    "company/local-community/noise/operations/runway-alternation/"
    "Heathrow_Runway_Alternation_Programme_{year}.pdf"
)
_ALTERNATION_PAGE = "https://www.heathrow.com/company/local-community/noise/operations/runway-alternation"
_PDF_HREF_RE = re.compile(
    r'href=["\']([^"\']*runway[_-]alternation[^"\']*\.pdf)["\']',
    re.IGNORECASE,
)
# Daytime schedule row: "5 Jan  27L  27R"
_ROW_RE = re.compile(
    r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))"
    r"\s+(27[LR])\s+(27[LR])"
)


@dataclass
class PDFScheduleRow:
    week_start_str: str  # e.g. "13 Apr"
    am_runway: str  # 06:00–15:00
    pm_runway: str  # 15:00–last departure


@dataclass
class PDFParseResult:
    success: bool
    rows: list[PDFScheduleRow] = field(default_factory=list)
    source_url: str = ""
    error: str | None = None


def _pdf_to_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _discover_url(year: int, timeout: float) -> str | None:
    """Scrape the Heathrow alternation page for a PDF link."""
    try:
        r = httpx.get(_ALTERNATION_PAGE, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        matches = _PDF_HREF_RE.findall(r.text)
        if not matches:
            logger.debug("No PDF links found on alternation page")
            return None
        for href in matches:
            if str(year) in href:
                return (
                    href
                    if href.startswith("http")
                    else f"https://www.heathrow.com{href}"
                )
        href = matches[0]
        return href if href.startswith("http") else f"https://www.heathrow.com{href}"
    except Exception as exc:
        logger.warning("Alternation page scrape failed: %s", exc)
        return None


def _fetch_bytes(url: str, timeout: float) -> bytes | None:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content
        logger.debug("Non-PDF or bad status from %s (status=%d)", url, r.status_code)
    except Exception as exc:
        logger.warning("HTTP fetch failed for %s: %s", url, exc)
    return None


def _parse_week_date(date_str: str, year: int) -> date | None:
    """Parse '5 Jan' → date, trying year and neighbours for Dec/Jan crossover."""
    from datetime import datetime

    for y in (year, year - 1, year + 1):
        try:
            return datetime.strptime(f"{date_str} {y}", "%d %b %Y").date()
        except ValueError:
            continue
    return None


def fetch_and_parse(year: int | None = None, timeout: float = 15.0) -> PDFParseResult:
    """Fetch and parse Heathrow's daytime alternation schedule for year."""
    from datetime import UTC, datetime

    if year is None:
        year = datetime.now(UTC).year

    primary_url = _CDN_TEMPLATE.format(year=year)
    used_url = primary_url

    content = _fetch_bytes(primary_url, timeout)

    if content is None:
        logger.info("Fast-path PDF fetch failed; trying discovery fallback")
        discovered = _discover_url(year, timeout)
        if discovered:
            content = _fetch_bytes(discovered, timeout)
            if content is not None:
                used_url = discovered
                logger.info("PDF fetched via discovery fallback: %s", used_url)

    if content is None:
        return PDFParseResult(
            success=False,
            source_url=used_url,
            error="PDF not reachable via direct URL or page discovery",
        )

    try:
        text = _pdf_to_text(content)
    except Exception as exc:
        return PDFParseResult(
            success=False,
            source_url=used_url,
            error=f"PDF text extraction failed: {exc}",
        )

    matches = _ROW_RE.findall(text)
    if not matches:
        return PDFParseResult(
            success=False,
            source_url=used_url,
            error="No daytime schedule rows found — PDF format may have changed",
        )

    rows = [
        PDFScheduleRow(week_start_str=m[0], am_runway=m[1], pm_runway=m[2])
        for m in matches
    ]
    logger.info("PDF parsed: %d rows from %s", len(rows), used_url)
    return PDFParseResult(success=True, rows=rows, source_url=used_url)


def lookup_week(rows: list[PDFScheduleRow], target_date: date) -> PDFScheduleRow | None:
    """Return the row whose Monday-commencing week contains target_date."""
    for row in rows:
        week_start = _parse_week_date(row.week_start_str, target_date.year)
        if week_start is None:
            continue
        if week_start <= target_date < week_start + timedelta(days=7):
            return row
    return None
