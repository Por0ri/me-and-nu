"""Fail-closed source checks for live content generation."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from ani_menu_agent.models import ContentKind
from ani_menu_agent.sources.registry import load_source_registry


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _host_matches(url: str, registered_url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    registered_host = (urlparse(registered_url).hostname or "").lower().rstrip(".")
    return bool(
        host
        and registered_host
        and (host == registered_host or host.endswith(f".{registered_host}"))
    )


def validate_generation_sources(
    sources: list[Any], facts: list[dict[str, Any]], content_kind: str | ContentKind
) -> set[str]:
    """Reject evidence collected under a source that is not currently approved.

    Discovery leads are not evidence. Information facts require an active first-party
    source, or an active editorial source for review, curation, or factual humor
    material. Community humor facts require an approved automated trend signal.
    This also invalidates stale records.
    """
    kind = ContentKind(content_kind)
    registry = {source.id: source for source in load_source_registry().sources}
    eligible_ids: set[str] = set()
    source_records = {str(_value(source, "id", "")): source for source in sources}
    for source_id, record in source_records.items():
        configured = registry.get(source_id)
        if configured is None:
            continue
        source_url = str(_value(record, "url", ""))
        if not source_url.startswith("https://") or not _host_matches(
            source_url, str(configured.url)
        ):
            continue
        if kind == ContentKind.INFORMATION:
            if configured.role == "primary":
                approved = (
                    configured.status == "active"
                    and _value(record, "is_official", False) is True
                    and _value(record, "role", "") == "primary"
                )
            elif configured.role == "editorial":
                editorial_types = {item.value for item in configured.content_types}
                approved = (
                    configured.status == "active"
                    and bool(editorial_types)
                    and editorial_types <= {"review", "curation", "humor", "people"}
                    and _value(record, "is_official", False) is False
                    and _value(record, "role", "") == "editorial"
                )
            elif configured.role == "reference":
                reference_types = {item.value for item in configured.content_types}
                approved = (
                    configured.status == "active"
                    and bool(reference_types)
                    and reference_types <= {"guide", "people", "character", "curation"}
                    and _value(record, "is_official", False) is False
                    and _value(record, "role", "") == "reference"
                )
            else:
                approved = False
        else:
            approved = (
                configured.status == "trend_only"
                and configured.role == "trend"
                and configured.collection != "manual"
                and _value(record, "role", "") == "trend"
            )
        if approved:
            eligible_ids.add(source_id)

    required_role = (
        "활성 1차/리뷰·큐레이션 편집 출처"
        if kind == ContentKind.INFORMATION
        else "자동 수집이 승인된 트렌드"
    )
    if not eligible_ids:
        raise ValueError(
            f"현재 레지스트리에서 생성에 사용할 수 있는 {required_role} 출처가 없습니다. "
            "출처가 보류·비활성화되었거나 수집 방식이 자동화되지 않았습니다."
        )

    for fact in facts:
        fact_source_ids = set(fact.get("source_ids", []))
        if kind == ContentKind.INFORMATION and fact.get("verified") is not True:
            raise ValueError(f"검증되지 않은 사실은 생성에 사용할 수 없습니다: {fact.get('id')}")
        if not fact_source_ids or not fact_source_ids <= eligible_ids:
            invalid = sorted(fact_source_ids - eligible_ids)
            raise ValueError(
                f"자료 {fact.get('id')}에 현재 승인되지 않은 출처가 연결되어 있습니다: "
                f"{', '.join(invalid) or '출처 ID 없음'}"
            )
    return eligible_ids


def validate_content_type_source_support(content_type: str, source_ids: set[str]) -> None:
    """A carousel type must be supported by at least one of its own sources."""
    from ani_menu_agent.sources.registry import DEFAULT_TYPE_MAP_PATH

    mapping = json.loads(DEFAULT_TYPE_MAP_PATH.read_text(encoding="utf-8")).get("sources", {})
    supported = {
        source_id for source_id in source_ids if content_type in mapping.get(source_id, [])
    }
    if not supported:
        raise ValueError(
            f"콘텐츠 유형 {content_type!r}을 뒷받침하는 등록 출처가 없습니다. "
            "자료 목적에 맞는 유형을 선택하거나 유형-출처 연결을 검증하세요."
        )


def supported_content_types_for_sources(source_ids: set[str]) -> set[str]:
    """Return only editorial types supported by the evidence source set."""
    from ani_menu_agent.sources.registry import DEFAULT_TYPE_MAP_PATH

    mapping = json.loads(DEFAULT_TYPE_MAP_PATH.read_text(encoding="utf-8")).get("sources", {})
    return {
        content_type
        for source_id in source_ids
        for content_type in mapping.get(source_id, [])
    }
