"""Poll xcancel RSS mirror of @HeathrowRunways for deviation notices."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging

import feedparser

from heathrow_noise.config import Config
from heathrow_noise.models import DeviationNotice

logger = logging.getLogger(__name__)

# Keywords that suggest an actual operational deviation (not routine updates)
_DEVIATION_KEYWORDS = [
    "out of alternation",
    "de-alternation",
    "team mode",
    "both runways",
    "resurfacing",
    "maintenance",
    "deviation",
    "suspended",
    "suspension",
    "unplanned",
    "emergency",
]


def _is_deviation(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _DEVIATION_KEYWORDS)


def fetch_deviations(config: Config) -> tuple[list[DeviationNotice], bool]:
    """Fetch and parse RSS feed. Returns (notices, feed_available)."""
    url = config.get("deviation_feed.url", "")
    if not url or not config.get_bool("deviation_feed.enabled", True):
        return [], False

    user_agent = config.get(
        "deviation_feed.user_agent",
        "Mozilla/5.0 (compatible; heathrow-noise/0.1)",
    )
    max_age_h = config.get_int("deviation_feed.max_age_hours", 24)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_h)

    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": user_agent})
        if feed.bozo and not feed.entries:
            logger.warning("RSS feed parse error: %s", feed.bozo_exception)
            return [], False

        notices: list[DeviationNotice] = []
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title} {summary}".strip()

            # Parse published date
            pub = entry.get("published", "")
            try:
                pub_dt = parsedate_to_datetime(pub).astimezone(UTC)
            except Exception:
                pub_dt = datetime.now(UTC)

            if pub_dt < cutoff:
                continue

            if _is_deviation(text):
                notices.append(
                    DeviationNotice(
                        text=text[:500],
                        published=pub_dt,
                        url=entry.get("link", ""),
                    )
                )

        return notices, True

    except Exception:
        logger.exception("Failed to fetch deviation RSS feed")
        return [], False
