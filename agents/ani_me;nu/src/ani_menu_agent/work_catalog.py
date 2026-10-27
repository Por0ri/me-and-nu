"""Resolve generated content to the canonical menu_work_id dataset."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ani_menu_agent.models import (
    ContentPackage,
    StructuredWorkReference,
    WorkMatchEvidence,
)
from ani_menu_agent.sources import load_source_registry

_KOREAN_TITLE_PARTICLES = (
    "에서",
    "으로",
    "처럼",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "도",
    "만",
    "의",
    "로",
)
_SEQUEL_SUFFIX = re.compile(
    r"(?:\s*(?:season|series|part|cour)\s*[ivxlcdm0-9]+"
    r"|\s*[ivxlcdmⅰ-ⅿ]+"
    r"|\s*[0-9]+(?:st|nd|rd|th)?(?:\s*(?:season|series|part))?"
    r"|\s*第?[0-9０-９一二三四五六七八九十]+期)",
    re.IGNORECASE,
)
_COLLABORATION_MARKER = re.compile(
    r"\bcollab(?:oration)?\b|\bcrossover\b|\bcross-over\b|"
    r"\bjoint(?:ly)?\b|\b合作\b|\bcollaborazione\b|"
    r"\s+x\s+|×|"
    r"\u00d7|\ud611\uc5c5|\ucf5c\ub77c\ubcf4|\ud569\uc791|"
    r"\u30b3\u30e9\u30dc|\u30af\u30ed\u30b9\u30aa\u30fc\u30d0\u30fc",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def load_work_catalog() -> list[dict[str, Any]]:
    """Load canonical works without collapsing sequel/entry rows into one work."""
    catalog = Path(__file__).resolve().parents[2] / "data" / "reference" / "menu_works.csv"
    if not catalog.is_file():
        return []
    with catalog.open(encoding="utf-8-sig", newline="") as stream:
        return [
            {
                **row,
                "menu_work_id": int(row["menu_work_id"]),
                "popularity": int(row.get("popularity") or 0),
            }
            for row in csv.DictReader(stream)
        ]


@lru_cache(maxsize=1)
def _aliases() -> list[tuple[str, int]]:
    catalog = Path(__file__).resolve().parents[2] / "data" / "reference"
    aliases: dict[str, int] = {}
    canonical = catalog / "menu_works.csv"
    if not canonical.is_file():
        return []
    with canonical.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            work_id = int(row["menu_work_id"])
            # Canonical work titles take precedence over entry-level title variants.
            for key in ("title_ko", "title_romaji", "title_english", "title_native"):
                value = (row.get(key) or "").strip()
                if value:
                    aliases[value.casefold()] = work_id
    return sorted(((name, work_id) for name, work_id in aliases.items()), key=lambda x: -len(x[0]))


@lru_cache(maxsize=1)
def _registered_work_titles() -> dict[str, tuple[str, ...]]:
    titles: dict[str, tuple[str, ...]] = {}
    for source in load_source_registry().sources:
        # Some official publishers register only the native/original title.
        # Both fields are curated source metadata, so either is a safe fallback
        # when the article headline itself omits the franchise name.
        registered_titles = tuple(
            dict.fromkeys(
                title.strip()
                for title in (source.work_title_ko, source.work_title_original)
                if title and title.strip()
            )
        )
        if registered_titles:
            titles[source.id] = registered_titles
    return titles


def _match_title_work_ids(title: str) -> list[int]:
    """Match distinct, longest title spans; never promote a nested short title."""
    aliases = _aliases()
    # Normalize colons to spaces while keeping word boundaries and sequel suffixes
    # strict. For example, "Another World" must not also link to the unrelated
    # one-word title "Another" merely because it is a substring.
    normalized_title = re.sub(r"\s+", " ", re.sub(r"[:：]+", " ", title.casefold())).strip()
    found: list[tuple[int, int, int, int]] = []
    for alias, work_id in aliases:
        if len(alias.strip()) < 3:
            continue
        normalized_alias = re.sub(r"\s+", " ", re.sub(r"[:：]+", " ", alias.casefold())).strip()
        start = normalized_title.find(normalized_alias)
        while start >= 0:
            end = start + len(normalized_alias)
            # Semicolons can be internal to one stylized title (e.g. Steins;Gate).
            left_separator_is_internal = (
                start >= 2
                and normalized_title[start - 1] == ";"
                and normalized_title[start - 2].isalnum()
            )
            right_separator_is_internal = (
                end + 1 < len(normalized_title)
                and normalized_title[end] == ";"
                and normalized_title[end + 1].isalnum()
            )
            left_boundary = start == 0 or not (
                normalized_title[start - 1].isalnum() or normalized_title[start - 1] == "_"
            ) and not left_separator_is_internal
            right_boundary = end == len(normalized_title) or not (
                normalized_title[end].isalnum() or normalized_title[end] == "_"
            ) and not right_separator_is_internal or normalized_title.startswith(
                _KOREAN_TITLE_PARTICLES, end
            ) or bool(_SEQUEL_SUFFIX.match(normalized_title[end:]))
            if left_boundary and right_boundary:
                found.append((start, end, -len(normalized_alias), work_id))
                break
            start = normalized_title.find(normalized_alias, start + 1)

    selected: list[tuple[int, int, int, int]] = []
    for match in sorted(found, key=lambda item: (item[0], item[2])):
        start, end, _, _ = match
        nested_in_selected = any(
            parent_start <= start and parent_end >= end
            for parent_start, parent_end, _, _ in selected
        )
        if nested_in_selected:
            continue
        selected.append(match)
    ordered: list[int] = []
    for _, _, _, work_id in selected:
        if work_id not in ordered:
            ordered.append(work_id)
    return ordered


def _is_explicit_multi_work_collaboration(title: str, work_ids: list[int]) -> bool:
    """Only an explicit collaboration headline may associate multiple works."""
    return len(set(work_ids)) > 1 and bool(_COLLABORATION_MARKER.search(title or ""))


def resolve_title_work_ids(title: str) -> list[int]:
    """Resolve explicit catalog titles in a headline without using body text."""
    return _match_title_work_ids(title)


def resolve_work_matches(
    *,
    titles: list[str] | None = None,
    source_ids: list[str] | None = None,
    structured_references: list[StructuredWorkReference | dict[str, Any]] | None = None,
    outbound_links: list[Any] | None = None,
    content_type: str | None = None,
) -> list[WorkMatchEvidence]:
    """Resolve work identity from explicit titles and publisher-declared metadata.

    Free-form article prose, summaries, image content, and LLM guesses are never
    treated as identity evidence. The first available evidence tier wins.
    """
    def evidence_for(title: str, basis: str, confidence: float, url: str | None = None):
        ids = _match_title_work_ids(title)
        if not ids:
            return []
        if not _is_explicit_multi_work_collaboration(title, ids):
            ids = ids[:1]
        folded = title.casefold()
        aliases = [
            (alias, work_id)
            for alias, work_id in _aliases()
            if work_id in ids and alias.casefold() in folded
        ]
        def display_alias(work_id: int) -> str:
            alias = next((value for value, alias_id in aliases if alias_id == work_id), "")
            start = folded.find(alias.casefold()) if alias else -1
            return title[start : start + len(alias)] if start >= 0 else title

        return [
            WorkMatchEvidence(
                menu_work_id=work_id,
                basis=basis,
                matched_value=display_alias(work_id),
                evidence_url=url,
                confidence=confidence,
            )
            for work_id in ids
        ]

    for title in titles or []:
        matched = evidence_for(str(title or "").strip(), "article_title", 0.99)
        if matched:
            return matched

    registered_sources = {
        source.id: source for source in load_source_registry().sources
    }
    for source_id in source_ids or []:
        source = registered_sources.get(source_id)
        if not source:
            continue
        for value in (source.work_title_ko, source.work_title_original):
            if value:
                matched = evidence_for(value, "source_registry", 0.98, str(source.url))
                if matched:
                    return matched

    # Only links visibly declared by an approved publisher as official/press
    # sources may add a work mapping. The linked host must itself be a registered,
    # active primary source with an explicit work title.
    official_terms = re.compile(
        r"official|press release|announcement|official site|official website|"
        r"press|release|\bnews\b|\bofficial\b|公式|공식|보도자료|발표",
        re.IGNORECASE,
    )
    for link in outbound_links or []:
        link_data = link if isinstance(link, dict) else {}
        link_url = str(getattr(link, "url", None) or link_data.get("url") or "")
        label = str(getattr(link, "text", None) or link_data.get("text") or "")
        parsed = re.match(r"^https://([^/:?#]+)", link_url, re.IGNORECASE)
        if not parsed:
            continue
        host = parsed.group(1).lower().rstrip(".")
        candidates = []
        for source in registered_sources.values():
            registered_host = str(source.url.host or "").lower().rstrip(".")
            if (
                source.status != "active"
                or source.role != "primary"
                or source.collection != "html"
                or (
                    content_type
                    and content_type not in {item.value for item in source.content_types}
                )
                or not registered_host
                or not (host == registered_host or host.endswith(f".{registered_host}"))
                or not (source.work_title_ko or source.work_title_original)
            ):
                continue
            candidates.append(source)
        # Require a clear official label when multiple primary work sources share
        # the destination host; never pick one based on registry order.
        if len(candidates) != 1 or (len(candidates) > 1 and not official_terms.search(label)):
            continue
        source = candidates[0]
        for value in (source.work_title_ko, source.work_title_original):
            if value:
                matched = evidence_for(value, "registered_official_link", 0.97, link_url)
                if matched:
                    return matched

    about_matches: list[WorkMatchEvidence] = []
    mention_matches: list[WorkMatchEvidence] = []
    for raw_reference in structured_references or []:
        reference = (
            raw_reference
            if isinstance(raw_reference, StructuredWorkReference)
            else StructuredWorkReference.model_validate(raw_reference)
        )
        basis = "structured_about" if reference.relation == "about" else "structured_mention"
        confidence = 0.94 if reference.relation == "about" else 0.90
        matched = evidence_for(
            reference.name,
            basis,
            confidence,
            str(reference.url) if reference.url else None,
        )
        (about_matches if reference.relation == "about" else mention_matches).extend(matched)

    structured_matches = about_matches or mention_matches
    if structured_matches:
        unique: dict[int, WorkMatchEvidence] = {}
        for match in structured_matches:
            unique.setdefault(match.menu_work_id, match)
        collaboration_title = next(
            (
                str(title or "").strip()
                for title in titles or []
                if _COLLABORATION_MARKER.search(str(title or ""))
            ),
            "",
        )
        if not _is_explicit_multi_work_collaboration(
            collaboration_title, list(unique)
        ):
            return list(unique.values())[:1]
        return list(unique.values())

    return []


def resolve_menu_work_ids(package: ContentPackage) -> list[int]:
    """Resolve works from publication/article titles, never from body prose.

    A source is tied to an official work in the source registry; that work is the
    representative fallback when its page title omits the franchise name. Any
    additional works must be explicitly named in a source/article title. The
    extracted topic, facts, and generated copy are intentionally excluded.
    """
    if package.work_match_evidence:
        valid_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
        evidence_ids = list(dict.fromkeys(
            item.menu_work_id for item in package.work_match_evidence
            if item.menu_work_id in valid_ids
        ))
        collaboration_title = next(
            (
                evidence.title
                for evidence in package.sources
                if _COLLABORATION_MARKER.search(evidence.title or "")
            ),
            "",
        )
        if _is_explicit_multi_work_collaboration(collaboration_title, evidence_ids):
            return evidence_ids
        return evidence_ids[:1]

    registered_titles = _registered_work_titles()
    ordered: list[int] = []

    def append_title_matches(title: str) -> None:
        for work_id in _match_title_work_ids(title):
            if work_id not in ordered:
                ordered.append(work_id)

    primary_first = sorted(
        package.sources,
        key=lambda evidence: (evidence.role != "primary", not evidence.is_official),
    )
    for evidence in primary_first:
        # Match all explicit works in the actual publisher title first.
        append_title_matches(evidence.title)
        # Source registry mapping supplies a reliable representative when an
        # official article's headline does not repeat the franchise title.
        for registered_title in registered_titles.get(evidence.id, ()):
            append_title_matches(registered_title)
    collaboration_title = next(
        (
            evidence.title
            for evidence in primary_first
            if _is_explicit_multi_work_collaboration(
                evidence.title, _match_title_work_ids(evidence.title)
            )
        ),
        "",
    )
    if _is_explicit_multi_work_collaboration(collaboration_title, ordered):
        return ordered
    return ordered[:1]


def attach_menu_work_ids(package: ContentPackage) -> ContentPackage:
    package.menu_work_ids = resolve_menu_work_ids(package)
    return package
