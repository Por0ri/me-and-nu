"""사전 글의 출처 판정 — 사실은 정보사전에서만 온다

기존 에이전트는 크롤링한 사이트가 승인 목록에 있는지 봤지만, 사전 글의 사실은 출처가 사전에 이미 기록돼 있다.
그래서 사실마다 정보사전 번호(details.dict_fact_id)가 있고 그 출처 주소가 https 인지만 본다.
"""

from __future__ import annotations

from typing import Any

RAG_CONTENT_TYPES = frozenset({"curation", "music", "people", "character", "guide"})


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def validate_generation_sources(sources: list[Any], facts: list[Any], content_kind: Any = None) -> set[str]:
    """쓸 수 있는 출처 번호를 돌려준다. 사전에서 오지 않은 사실이 하나라도 있으면 막는다."""
    https_ids = {str(_value(s, "id")) for s in sources if str(_value(s, "url", "")).startswith("https://")}
    verified = [f for f in facts if _value(f, "verified", False)]
    if not verified:
        raise ValueError("검증된 사전 사실이 없습니다.")
    used: set[str] = set()
    for fact in verified:
        fact_id = _value(fact, "id")
        if not (_value(fact, "details", {}) or {}).get("dict_fact_id"):
            raise ValueError(f"사실 {fact_id} 에 정보사전 번호가 없습니다.")
        source_ids = set(_value(fact, "source_ids", []) or [])
        if not source_ids or not source_ids <= https_ids:
            raise ValueError(f"사실 {fact_id} 의 출처가 기록돼 있지 않습니다.")
        used |= source_ids
    return used


def supported_content_types_for_sources(source_ids: set[str]) -> set[str]:
    return set(RAG_CONTENT_TYPES) if source_ids else set()


def validate_content_type_source_support(content_type: str, source_ids: set[str]) -> None:
    if content_type not in RAG_CONTENT_TYPES:
        raise ValueError(f"사전 글에서 쓰지 않는 콘텐츠 유형입니다: {content_type}")
