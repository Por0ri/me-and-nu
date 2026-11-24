"""Deterministic pre-generation duplicate checks for same-work content."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ani_menu_agent.models import ContentPackage
from ani_menu_agent.sources.anime_schedule import profile_slug, source_item_key

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


def story_source_key(source: dict) -> str:
    """Keep real article URLs canonical; a single season page can announce many episodes."""
    url = _canonical_url(str(source.get("url") or ""))
    source_id = source.get("source_id") or source.get("id")
    if source_id != "anime_schedule_rss" or not profile_slug(url):
        return url
    guid = source.get("feed_entry_guid")
    if not guid:
        # Older packages may still contain the original RAW feed headline.
        for label in ("discovery_title", "title"):
            match = re.fullmatch(r"Episode ([0-9]+(?:\.[0-9]+)?) of (.+) is out!", str(source.get(label) or "").strip())
            if match:
                guid = f"{match[2]} Episode {match[1]}"
                break
    return source_item_key(source_id, url, guid)


def _fact_statements(package: dict) -> list[str]:
    return [
        str(item.get("statement", "")).strip()
        for item in package.get("facts", [])
        if isinstance(item, dict) and item.get("statement")
    ]


def duplicate_reason(candidate: ContentPackage, existing: dict) -> str | None:
    """Return a reason only for a high-confidence same-story match."""
    candidate_sources = [source.model_dump(mode="json") for source in candidate.sources]
    existing_sources = [source for source in existing.get("sources", []) if isinstance(source, dict)]
    # The two entries use the same series page and nearly identical wording.
    # Shared background references and synopsis do not make them the same episode.
    if candidate_sources and existing_sources:
        current, previous = candidate_sources[0], existing_sources[0]
        if (current.get("id") == previous.get("id") == "anime_schedule_rss"
                and profile_slug(str(current.get("url"))) and
                _canonical_url(str(current.get("url"))) == _canonical_url(str(previous.get("url")))):
            return "same_source_url" if story_source_key(current) == story_source_key(previous) else None
    current_keys = {story_source_key(source) for source in candidate_sources if source.get("url")}
    existing_keys = {story_source_key(source) for source in existing_sources if source.get("url")}
    if current_keys & existing_keys:
        return "same_source_url"

    candidate_works = set(candidate.menu_work_ids)
    existing_works = {
        int(value)
        for value in existing.get("menu_work_ids", [])
        if str(value).isdigit()
    }
    if not candidate_works.intersection(existing_works):
        return None

    for left in candidate_sources:
        left_tokens = _tokens(str(left.get("title", "")))
        if len(left_tokens) < 4:
            continue
        for right in existing_sources:
            right_tokens = _tokens(str(right.get("title", "")))
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
