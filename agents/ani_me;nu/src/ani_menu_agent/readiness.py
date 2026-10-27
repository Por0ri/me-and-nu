"""Counts of evidence that is actually eligible for automatic generation."""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any
from urllib.parse import urlparse

from ani_menu_agent.work_type_coverage import FACT_TYPE_TO_CONTENT_TYPE


def supported_verified_fact_type(
    fact_type: str,
    source_ids: set[str],
    information_source_ids: Set[str],
    source_type_map: Mapping[str, list[str]],
) -> str | None:
    """Map one verified fact to its actual type only when its source supports it."""
    content_type = FACT_TYPE_TO_CONTENT_TYPE.get(fact_type.strip().lower())
    if content_type is None:
        return None
    return content_type if any(
        source_id in information_source_ids
        and content_type in source_type_map.get(source_id, [])
        for source_id in source_ids
    ) else None


def is_automated_humor_post(
    record_kind: str,
    status: str,
    payload: Mapping[str, Any],
    approved_source_ids: Set[str],
) -> bool:
    """Only count a captured public post, never an aggregate hashtag snapshot."""
    if record_kind != "humor_idea" or not status.startswith("trend_only"):
        return False
    source_ids = set(payload.get("source_ids") or [])
    details = payload.get("details") or {}
    source_url = str(details.get("source_url") or "")
    statement = str(payload.get("statement") or "").strip()
    return bool(
        source_ids
        and source_ids <= approved_source_ids
        and urlparse(source_url).scheme == "https"
        and urlparse(source_url).netloc
        and len(statement) >= 60
    )
