"""Cheap freshness checks used before expensive collection and generation stages."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

TIME_SENSITIVE_TYPES = {"event", "goods", "music", "news"}
MAX_SOURCE_AGE = timedelta(days=90)


def candidate_publication_date(candidate: dict) -> date | None:
    """Read the source date from discovery metadata or a dated headline prefix."""
    value = str(candidate.get("published_at") or "").strip()
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass

    title = str(candidate.get("title") or "")
    match = re.match(
        r"^\s*(20\d{2})\s*(?:[./-]|年)\s*(\d{1,2})\s*(?:[./-]|月)\s*(\d{1,2})\s*日?",
        title,
    )
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def candidate_is_stale(
    candidate: dict,
    type_code: str,
    *,
    today: date | None = None,
) -> bool:
    """Skip old time-sensitive stories; keep evergreen guides and profiles eligible."""
    if type_code not in TIME_SENSITIVE_TYPES:
        return False
    published = candidate_publication_date(candidate)
    reference = today or datetime.now(UTC).date()
    return published is not None and published < reference - MAX_SOURCE_AGE
