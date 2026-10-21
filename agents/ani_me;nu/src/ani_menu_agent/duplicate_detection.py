"""Deterministic pre-generation duplicate checks for same-work content."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ani_menu_agent.models import ContentPackage

_TOKEN = re.compile(r"[a-z0-9]{2,}|[가-힣]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}", re.I)
_STOP = {
    "그리고", "그러나", "이번", "공식", "발표", "관련", "예정", "통해", "위해",
    "에서", "으로", "에는", "부터", "까지", "한다", "했다", "하는", "있는", "있다",
    "됩니다", "한다는", "대한", "함께", "일본", "현지", "작품", "애니메이션",
}


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "pubdate", "page", "ref"}
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(value) if token.casefold() not in _STOP}


def _fact_statements(package: dict) -> list[str]:
    return [
        str(item.get("statement", "")).strip()
        for item in package.get("facts", [])
        if isinstance(item, dict) and item.get("statement")
    ]


def duplicate_reason(candidate: ContentPackage, existing: dict) -> str | None:
    """Return a reason only for a high-confidence same-story match."""
    candidate_urls = {
        _canonical_url(str(source.url)) for source in candidate.sources if source.url
    }
    existing_urls = {
        _canonical_url(str(source.get("url", "")))
        for source in existing.get("sources", [])
        if source.get("url")
    }
    if candidate_urls & existing_urls:
        return "same_source_url"

    candidate_works = set(candidate.menu_work_ids)
    existing_works = {
        int(value)
        for value in existing.get("menu_work_ids", [])
        if str(value).isdigit()
    }
    if not candidate_works.intersection(existing_works):
        return None

    candidate_sources = [str(source.title) for source in candidate.sources]
    existing_sources = [str(source.get("title", "")) for source in existing.get("sources", [])]
    for left in candidate_sources:
        left_tokens = _tokens(left)
        if len(left_tokens) < 4:
            continue
        for right in existing_sources:
            right_tokens = _tokens(right)
            if len(right_tokens) < 4:
                continue
            overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
            text_ratio = SequenceMatcher(
                None,
                "".join(sorted(left_tokens)),
                "".join(sorted(right_tokens)),
            ).ratio()
            if overlap >= 0.78 or text_ratio >= 0.86:
                return "same_work_and_near_identical_source_title"

    candidate_facts = [_tokens(item.statement) for item in candidate.facts]
    existing_facts = [_tokens(text) for text in _fact_statements(existing)]
    matching_pairs = 0
    common_terms: set[str] = set()
    for left in candidate_facts:
        if len(left) < 4:
            continue
        for right in existing_facts:
            if len(right) < 4:
                continue
            shared = left & right
            containment = len(shared) / min(len(left), len(right))
            if len(shared) >= 3 and containment >= 0.72:
                matching_pairs += 1
                common_terms.update(shared)
    if matching_pairs >= 2 and len(common_terms) >= 5:
        return "same_work_and_overlapping_core_facts"
    return None
