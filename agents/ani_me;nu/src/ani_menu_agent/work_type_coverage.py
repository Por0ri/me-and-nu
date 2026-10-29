"""Work-by-type discovery and evidence coverage diagnostics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ani_menu_agent.models import CONTENT_TYPE_LABELS, ContentKind, ContentPackage, SourceEvidence
from ani_menu_agent.sources.models import Source
from ani_menu_agent.work_catalog import resolve_menu_work_ids

FACT_TYPE_TO_CONTENT_TYPE = {
    "announcement": "news",
    "notice": "news",
    "publication": "news",
    "update": "news",
    "release": "news",
    "broadcast_schedule": "news",
    "broadcast_history": "news",
    "streaming_schedule": "news",
    "streaming_availability": "news",
    "availability": "news",
    "release_schedule": "news",
    "event": "event",
    "event_announcement": "event",
    "event_period": "event",
    "event_date": "event",
    "event_schedule": "event",
    "event_location": "event",
    "event_content": "event",
    "event_detail": "event",
    "event_details": "event",
    "event_overview": "event",
    "event_notice": "event",
    "event_program": "event",
    "event_time": "event",
    "event_format": "event",
    "tour_schedule": "event",
    "venue": "event",
    "ticket": "event",
    "ticketing": "event",
    "ticket_sales": "event",
    "screening_schedule": "event",
    "stage_schedule": "event",
    "story": "guide",
    "plot": "guide",
    "episode": "guide",
    "episode_plot": "guide",
    "episode_synopsis": "guide",
    "series_intro": "guide",
    "story_arc": "guide",
    "story_arc_ranges": "guide",
    "work_synopsis": "guide",
    "synopsis": "guide",
    "setting": "guide",
    "work_detail": "guide",
    "work_info": "guide",
    "work_title": "guide",
    "story_background": "guide",
    "cast": "people",
    "staff": "people",
    "creator": "people",
    "artist_comment": "people",
    "interview_quote": "people",
    "artist_profile": "people",
    "character": "character",
    "character_profile": "character",
    "character_cast": "character",
    "character_background": "character",
    "character_relationship": "character",
    "character_abilities": "character",
    "character_detail": "character",
    "character_list": "character",
    "character_description": "character",
    "character_arc": "character",
    "product": "goods",
    "product_price": "goods",
    "product_release": "goods",
    "product_features": "goods",
    "product_feature": "goods",
    "product_details": "goods",
    "product_list": "goods",
    "product_lineup": "goods",
    "product_options": "goods",
    "product_overview": "goods",
    "product_spec": "goods",
    "product_specification": "goods",
    "specification": "goods",
    "product_design": "goods",
    "product_count": "goods",
    "product_content": "goods",
    "offering": "goods",
    "novelty": "goods",
    "manufacturer": "goods",
    "display": "goods",
    "merchandise": "goods",
    "merchandise_sale": "goods",
    "sales": "goods",
    "sales_period": "goods",
    "sales_location": "goods",
    "sales_channel": "goods",
    "sales_channels": "goods",
    "preorder": "goods",
    "reservation": "goods",
    "price": "goods",
    "production": "humor",
    "production_behind": "humor",
    "making": "humor",
    "behind": "humor",
    "behind_the_scenes": "humor",
    "production_note": "humor",
    "review": "review",
    "review_opinion": "review",
    "opinion": "review",
    "interpretation": "review",
    "review_scope": "review",
    "comparison": "review",
    "ranking": "curation",
    "rank": "curation",
    "poll": "curation",
    "playlist": "curation",
    "selection": "curation",
    "recommendation": "curation",
    "track": "music",
    "music": "music",
    "theme_song": "music",
    "episode_music": "music",
    "album": "music",
    "music_release": "music",
    "song_description": "music",
}


def _ids_from_source(source: Source) -> list[int]:
    evidence = SourceEvidence(
        id=source.id,
        title="",
        url=str(source.url),
        publisher=source.name,
        is_official=source.role == "primary",
        role=source.role,
        region=source.region,
    )
    package = ContentPackage(
        request_id=f"coverage-{source.id}",
        topic="coverage",
        content_kind=ContentKind.INFORMATION,
        sources=[evidence],
    )
    return resolve_menu_work_ids(package)


def _ids_from_title(title: str) -> list[int]:
    if not title.strip():
        return []
    evidence = SourceEvidence(
        id="coverage_title",
        title=title,
        url="https://coverage.invalid/",
        publisher="coverage report",
        role="editorial",
    )
    package = ContentPackage(
        request_id="coverage-title",
        topic="coverage",
        content_kind=ContentKind.INFORMATION,
        sources=[evidence],
    )
    return resolve_menu_work_ids(package)


def _is_expired(value: Any, now: datetime) -> bool:
    if not value:
        return False
    try:
        expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry <= now
    except ValueError:
        # Invalid expiry metadata must not make a stale record look current.
        return True


def build_work_type_coverage(
    catalog: list[dict[str, Any]],
    sources: list[Source],
    records: list[dict[str, Any]],
    ready_rows: list[dict[str, Any]],
) -> dict[int, dict[str, dict[str, int]]]:
    """Return counts without treating discovery leads as verified facts."""
    result = {
        int(work["menu_work_id"]): {
            code.value: {
                "active_sources": 0,
                "discovery_sources": 0,
                "pending_sources": 0,
                "candidates": 0,
                "verified_facts": 0,
                "humor_posts": 0,
                "trend_signals": 0,
                "ready": 0,
            }
            for code in CONTENT_TYPE_LABELS
        }
        for work in catalog
    }
    source_by_id = {source.id: source for source in sources}
    work_ids_by_source = {source.id: set(_ids_from_source(source)) for source in sources}
    approved_trend_ids = {
        source.id
        for source in sources
        if source.role == "trend"
        and source.status == "trend_only"
        and source.collection != "manual"
    }

    for source in sources:
        for work_id in work_ids_by_source[source.id]:
            if work_id not in result:
                continue
            for content_type in source.content_types:
                row = result[work_id][content_type.value]
                if source.status == "active" or source.id in approved_trend_ids:
                    row["active_sources"] += 1
                    if source.discovery_enabled:
                        row["discovery_sources"] += 1
                else:
                    row["pending_sources"] += 1

    request_work_ids: dict[str, set[int]] = {}
    decoded_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in records:
        try:
            payload = json.loads(record.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        decoded_records.append((record, payload))
        if record.get("record_kind") not in {"source", "trend_source"}:
            continue
        source_id = str(payload.get("id") or "")
        work_ids = set(work_ids_by_source.get(source_id, set()))
        work_ids.update(_ids_from_title(str(payload.get("title") or "")))
        request_work_ids.setdefault(str(record.get("request_id") or ""), set()).update(work_ids)

    now = datetime.now(UTC)
    for record, payload in decoded_records:
        if _is_expired(record.get("expires_at"), now):
            continue
        kind = record.get("record_kind")
        if kind == "discovery_candidate":
            source = source_by_id.get(str(payload.get("source_id") or ""))
            work_ids = set(work_ids_by_source.get(source.id, set())) if source else set()
            if not work_ids:
                work_ids.update(_ids_from_title(str(payload.get("title") or "")))
            for work_id in work_ids:
                if work_id not in result:
                    continue
                for content_type in payload.get("content_types", []):
                    if content_type in result[work_id]:
                        result[work_id][content_type]["candidates"] += 1
        elif (
            kind == "fact"
            and record.get("status") == "collected"
            and payload.get("verified") is True
        ):
            content_type = FACT_TYPE_TO_CONTENT_TYPE.get(str(payload.get("fact_type") or ""))
            if not content_type:
                continue
            fact_sources = [
                source_by_id.get(str(source_id))
                for source_id in payload.get("source_ids", [])
            ]
            if not any(
                source is not None
                and source.status == "active"
                and content_type in {item.value for item in source.content_types}
                and (
                    source.role == "primary"
                    or (
                        source.role == "editorial"
                        and content_type in {"review", "curation", "humor"}
                    )
                    or (
                        source.role == "reference"
                        and content_type in {"guide", "people", "character", "curation"}
                    )
                )
                for source in fact_sources
            ):
                continue
            work_ids = request_work_ids.get(str(record.get("request_id") or ""), set())
            if not work_ids:
                for source_id in payload.get("source_ids", []):
                    work_ids.update(work_ids_by_source.get(str(source_id), set()))
            for work_id in work_ids:
                if work_id in result:
                    result[work_id][content_type]["verified_facts"] += 1
        elif kind == "humor_idea" and str(record.get("status", "")).startswith(
            "trend_only"
        ):
            source_ids = set(payload.get("source_ids") or [])
            details = payload.get("details") or {}
            source_url = urlparse(str(details.get("source_url") or ""))
            statement = str(payload.get("statement") or "").strip()
            if (
                not source_ids
                or not source_ids <= approved_trend_ids
                or source_url.scheme != "https"
                or not source_url.netloc
                or len(statement) < 60
            ):
                continue
            work_ids = request_work_ids.get(str(record.get("request_id") or ""), set())
            for work_id in work_ids:
                if work_id in result:
                    result[work_id]["humor"]["humor_posts"] += 1
        elif (
            kind == "trend_source"
            and str(record.get("status", "")).startswith("trend_only")
            and payload.get("status") == "trend_signal"
            and payload.get("source_id") in approved_trend_ids
        ):
            work_ids = request_work_ids.get(str(record.get("request_id") or ""), set())
            for work_id in work_ids:
                if work_id in result:
                    result[work_id]["humor"]["trend_signals"] += 1

    for row in ready_rows:
        work_id = int(row["menu_work_id"])
        content_type = row.get("content_type")
        if work_id in result and content_type in result[work_id]:
            result[work_id][content_type]["ready"] += 1
    return result


def find_work_ids(catalog: list[dict[str, Any]], title_or_id: str) -> list[int]:
    value = title_or_id.strip()
    if value.isdigit():
        work_id = int(value)
        return [work_id] if any(int(row["menu_work_id"]) == work_id for row in catalog) else []
    normalized = value.casefold()
    return [
        int(row["menu_work_id"])
        for row in catalog
        if normalized
        in {
            str(row.get(key) or "").strip().casefold()
            for key in ("title_ko", "title_romaji", "title_english", "title_native")
        }
    ]
