"""Resolve generated content to the canonical menu_work_id dataset."""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from ani_menu_agent.models import (
    ContentPackage,
    StructuredWorkReference,
    WorkMatchEvidence,
)
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.work_aliases import find_named_works

_CATALOG_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "reference"
_WORK_LIST_DIRECTORY = Path(__file__).resolve().parents[4] / "catalog"

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
@lru_cache(maxsize=1)
def load_work_catalog() -> list[dict[str, Any]]:
    """Load canonical works without collapsing sequel/entry rows into one work."""
    rows: list[dict[str, Any]] = []
    for path in (
        _WORK_LIST_DIRECTORY / "menu_works.csv",
        _CATALOG_DIRECTORY / "agent_works.csv",
    ):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(
                {
                    **row,
                    "menu_work_id": int(row["menu_work_id"]),
                    "popularity": int(row.get("popularity") or 0),
                }
                for row in csv.DictReader(stream)
                if row.get("menu_work_id", "").isdigit()
            )
    return rows


@lru_cache(maxsize=1)
def load_work_entries() -> list[dict[str, Any]]:
    """Load season/movie AniList entries and retain their canonical series ID."""
    entries_path = _WORK_LIST_DIRECTORY / "menu_work_entries.csv"
    valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
    if not entries_path.is_file():
        return []
    entries = []
    with entries_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                work_id = int(row["menu_work_id"])
                anilist_id = int(row["anilist_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if work_id not in valid_work_ids:
                continue
            entries.append({**row, "anilist_id": anilist_id, "menu_work_id": work_id})
    return entries


@lru_cache(maxsize=1)
def load_work_external_entries() -> list[dict[str, Any]]:
    """Load externally confirmed anime titles mapped to an existing work ID."""
    path = _CATALOG_DIRECTORY / "menu_work_external_entries.csv"
    if not path.is_file():
        return []
    valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                work_id = int(row.get("menu_work_id") or "")
            except (TypeError, ValueError):
                continue
            title = (row.get("canonical_title") or "").strip()
            if (
                work_id in valid_work_ids
                and row.get("source_id") == "kitsu_anime_catalog"
                and title
            ):
                entries.append({**row, "menu_work_id": work_id, "canonical_title": title})
    return entries


@lru_cache(maxsize=1)
def load_work_aliases() -> list[dict[str, Any]]:
    """Load reviewed title variants without modifying canonical work records."""
    path = _CATALOG_DIRECTORY / "menu_work_aliases.csv"
    if not path.is_file():
        return []
    valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
    aliases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                work_id = int(row.get("menu_work_id") or "")
            except (TypeError, ValueError):
                continue
            alias = str(row.get("alias") or "").strip()
            if work_id not in valid_work_ids or not alias or row.get("status") != "verified":
                continue
            aliases.append(
                {
                    **row,
                    "menu_work_id": work_id,
                    "alias": alias,
                    "source_url_prefix": str(row.get("source_url_prefix") or "").strip(),
                }
            )
    return aliases


@lru_cache(maxsize=1)
def _work_parent_overrides() -> dict[int, int]:
    """Load curated franchise-level links for standalone catalog works."""
    path = _CATALOG_DIRECTORY / "menu_work_parent_overrides.csv"
    if not path.is_file():
        return {}
    valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
    overrides: dict[int, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                child_id = int(row["menu_work_id"])
                parent_id = int(row["parent_menu_work_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if child_id in valid_work_ids and parent_id in valid_work_ids and child_id != parent_id:
                overrides[child_id] = parent_id
    return overrides


def _franchise_work_id(work_id: int) -> int:
    """Resolve curated child-work links to their top-level franchise ID."""
    overrides = _work_parent_overrides()
    seen: set[int] = set()
    while work_id in overrides and work_id not in seen:
        seen.add(work_id)
        work_id = overrides[work_id]
    return work_id


@lru_cache(maxsize=1)
def load_work_franchises() -> dict[int, str]:
    """Return franchise labels without changing a work's canonical menu_work_id."""
    path = _CATALOG_DIRECTORY / "menu_work_franchises.csv"
    if not path.is_file():
        return {}
    valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
    franchises: dict[int, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                work_id = int(row["menu_work_id"])
            except (KeyError, TypeError, ValueError):
                continue
            franchise_id = (row.get("franchise_id") or "").strip()
            if work_id in valid_work_ids and franchise_id:
                franchises[work_id] = franchise_id
    return franchises


def _normalize_catalog_title(value: str) -> str:
    """Normalize typography variants while retaining title words and boundaries."""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("☆", "")
    normalized = re.sub(
        r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u9fff])",
        "",
        normalized,
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _title_spelling_variants(value: str) -> tuple[str, ...]:
    """Add conservative spacing and parenthesized-reading variants for catalog titles."""
    variants = [value.strip()]
    if re.search(r"[\uac00-\ud7a3]", value) and re.search(r"\s", value.strip()):
        variants.append(re.sub(r"\s+", "", value.strip()))
    without_reading = re.sub(
        r"\s*[（(][\u3040-\u30ff・ー\s]+[）)]$",
        "",
        value.strip(),
    ).strip()
    if without_reading and without_reading != value.strip():
        variants.append(without_reading)
    return tuple(dict.fromkeys(variant for variant in variants if variant))


def _alias_scope_matches(alias_row: dict[str, Any], source_url: str | None) -> bool:
    prefix = str(alias_row.get("source_url_prefix") or "").strip()
    if not prefix:
        return True
    if not source_url:
        return False
    return str(source_url).casefold().startswith(prefix.casefold())


@lru_cache(maxsize=128)
def _aliases(source_url: str | None = None) -> list[tuple[str, int]]:
    """Index canonical and entry-level titles, excluding cross-series collisions."""
    aliases: dict[str, set[int]] = {}
    rows = load_work_catalog() + load_work_entries()
    rows.extend(
        {
            "menu_work_id": row["menu_work_id"],
            "title_native": row["canonical_title"],
        }
        for row in load_work_external_entries()
    )
    rows.extend(
        {
            "menu_work_id": row["menu_work_id"],
            "title_native": row["alias"],
        }
        for row in load_work_aliases()
        if not row.get("source_url_prefix")
    )
    for row in rows:
        # Curated child spin-offs may resolve to a parent work, while separate
        # series in the same franchise (such as Yu-Gi-Oh! GX) keep their own ID.
        work_id = _franchise_work_id(int(row["menu_work_id"]))
        for key in ("title_ko", "title_romaji", "title_english", "title_native"):
            for variant in _title_spelling_variants(str(row.get(key) or "")):
                value = _normalize_catalog_title(variant)
                if value:
                    aliases.setdefault(value, set()).add(work_id)
    # A title reused by different series is not enough evidence for an entity link.
    unique_aliases = [
        (name, next(iter(work_ids)))
        for name, work_ids in aliases.items()
        if len(work_ids) == 1
    ]
    # A publisher page can disambiguate a generic or reused native title (for
    # example, "ワンピース" on the One Piece prize site vs. a separate recap
    # entry). Apply URL-scoped aliases after global collision filtering.
    scoped_aliases: dict[str, tuple[str, set[int]]] = {}
    for row in load_work_aliases():
        if not row.get("source_url_prefix") or not _alias_scope_matches(row, source_url):
            continue
        work_id = _franchise_work_id(int(row["menu_work_id"]))
        for variant in _title_spelling_variants(row["alias"]):
            alias = _normalize_catalog_title(variant)
            if len(alias) < 3:
                continue
            if alias not in scoped_aliases:
                scoped_aliases[alias] = (variant, {work_id})
            else:
                scoped_aliases[alias][1].add(work_id)
    if scoped_aliases:
        scoped_keys = set(scoped_aliases)
        unique_aliases = [
            (alias, work_id)
            for alias, work_id in unique_aliases
            if _normalize_catalog_title(alias) not in scoped_keys
        ]
        unique_aliases.extend(
            (alias, next(iter(work_ids)))
            for alias, work_ids in scoped_aliases.values()
            if len(work_ids) == 1
        )
    return sorted(unique_aliases, key=lambda item: -len(item[0]))


def _is_title_quoted(title: str, alias: str) -> bool:
    quote_pairs = (
        ("『", "』"),
        ("「", "」"),
        ("《", "》"),
        ("【", "】"),
        ("“", "”"),
        ("‘", "’"),
        ('"', '"'),
        ("'", "'"),
    )
    for opening, closing in quote_pairs:
        if re.search(
            rf"{re.escape(opening)}\s*{re.escape(alias)}\s*{re.escape(closing)}",
            title,
            flags=re.IGNORECASE,
        ):
            return True
    return False


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


def refresh_work_catalog_caches() -> None:
    """Reload catalog-backed lookups after another worker registers a new work."""
    for loader in (
        load_work_catalog,
        load_work_entries,
        load_work_external_entries,
        load_work_aliases,
        _work_parent_overrides,
        load_work_franchises,
        _aliases,
        _registered_work_titles,
    ):
        loader.cache_clear()


def _match_title_work_ids(title: str, source_url: str | None = None) -> list[int]:
    """Match distinct, longest title spans; never promote a nested short title."""
    aliases = _aliases(source_url)
    # Normalize colons to spaces while keeping word boundaries and sequel suffixes
    # strict. For example, "Another World" must not also link to the unrelated
    # one-word title "Another" merely because it is a substring.
    normalized_title = re.sub(
        r"\s+", " ", re.sub(r"[:：]+", " ", _normalize_catalog_title(title))
    ).strip()
    found: list[tuple[int, int, int, int]] = []
    for alias, work_id in aliases:
        if len(alias.strip()) < 3 and not _is_title_quoted(title, alias):
            continue
        normalized_alias = re.sub(
            r"\s+", " ", re.sub(r"[:：]+", " ", _normalize_catalog_title(alias))
        ).strip()
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
            # A one-word English title at the end of a longer headline is weak
            # evidence (e.g. "Chiikawa Mermaid Island" must not match ISLAND).
            # Keep standalone titles and titles that begin with the work name;
            # explicit multi-work collaboration headlines still use the normal
            # collaboration guard below.
            single_english_word = bool(re.fullmatch(r"[a-z0-9]+", normalized_alias))
            if single_english_word and start > 0 and not _COLLABORATION_MARKER.search(title or ""):
                start = normalized_title.find(normalized_alias, start + 1)
                continue
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


def _headline_names_multiple_works(
    title: str, work_ids: list[int], content_type: str | None = None
) -> bool:
    """Allow multiple IDs for collaborations or explicit, itemized curation titles."""
    if len(set(work_ids)) <= 1 or len(_match_title_work_ids(title or "")) <= 1:
        return False
    if _COLLABORATION_MARKER.search(title or ""):
        return True
    return content_type == "curation" and bool(_CURATION_MARKER.search(title or ""))


def allows_multiple_work_match(
    evidence_text: str, work_ids: list[int], content_type: str | None = None
) -> bool:
    """Check whether the evidence explicitly supports a multi-work association."""
    return _headline_names_multiple_works(evidence_text, work_ids, content_type)


_COLLABORATION_MARKER = re.compile(
    r"\b(?:collab(?:oration)?|crossover|cross-over|team-up)\b|"
    r"콜라보(?:레이션)?|협업|합작|크로스오버|コラボ(?:レーション)?|クロスオーバー|×",
    re.IGNORECASE,
)

_CURATION_MARKER = re.compile(
    r"\b(?:ranking|ranked|rank|polls?|vote|top\s*\d+|top\s+(?:anime|shows?)|best|recommendations?|"
    r"roundup|selection|favorites?|collection|list)\b|"
    r"\u30e9\u30f3\u30ad\u30f3\u30b0|\u6295\u7968|\u304a\u3059\u3059\u3081|"
    r"\u7279\u96c6|\u4eba\u6c17\u4f5c|\u307e\u3068\u3081|"
    r"\ub7ad\ud0b9|\uc21c\uc704|\ud22c\ud45c|\ucd94\ucc9c|\ubaa8\uc74c|\uacb0\uc0b0|\uc120\uc815|\uc778\uae30\uc791",
    re.IGNORECASE,
)


def _headline_character_work_ids(title: str) -> list[int]:
    """Resolve character aliases in a headline; multiple works require collab wording."""
    matches = [
        match for match in find_named_works(title or "")
        if match.kind == "character"
    ]
    ids = list(dict.fromkeys(match.menu_work_id for match in matches))
    if len(ids) > 1 and not _COLLABORATION_MARKER.search(title or ""):
        return ids[:1]
    return ids


def resolve_title_work_ids(title: str, source_url: str | None = None) -> list[int]:
    """Resolve explicit catalog titles in a headline without using body text."""
    return _match_title_work_ids(title, source_url)


def resolve_work_matches(
    *,
    titles: list[str] | None = None,
    source_ids: list[str] | None = None,
    structured_references: list[StructuredWorkReference | dict[str, Any]] | None = None,
    outbound_links: list[Any] | None = None,
    content_type: str | None = None,
    source_url: str | None = None,
) -> list[WorkMatchEvidence]:
    """Resolve work identity from explicit titles and publisher-declared metadata.

    Free-form article prose, summaries, image content, and LLM guesses are never
    treated as identity evidence. The first available evidence tier wins.
    """
    def evidence_for(title: str, basis: str, confidence: float, url: str | None = None):
        ids = _match_title_work_ids(title, source_url)
        if not ids:
            return []
        if not _headline_names_multiple_works(title, ids, content_type):
            ids = ids[:1]
        folded = title.casefold()
        aliases = [
            (alias, work_id)
            for alias, work_id in _aliases(source_url)
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

    # Headline character names are the first fallback when the headline omits
    # a catalog work title. Theme-song and artist aliases are intentionally
    # excluded until their Japanese-name coverage is reliable.
    for title in titles or []:
        title = str(title or "").strip()
        character_work_ids = _headline_character_work_ids(title)
        if not character_work_ids:
            continue
        matches_by_id = {
            match.menu_work_id: match
            for match in find_named_works(title)
            if match.kind == "character"
        }
        return [
            WorkMatchEvidence(
                menu_work_id=work_id,
                basis="headline_character",
                matched_value=matches_by_id[work_id].name,
                confidence=0.92,
            )
            for work_id in character_work_ids
        ]

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
        multi_work_title = next(
            (
                str(title or "").strip()
                for title in titles or []
                if len(_match_title_work_ids(str(title or ""))) > 1
            ),
            "",
        )
        if not _headline_names_multiple_works(multi_work_title, list(unique)):
            return list(unique.values())[:1]
        return list(unique.values())

    return []


def resolve_menu_work_ids(package: ContentPackage) -> list[int]:
    """Resolve work links from explicit title matches or audited curation lists.

    A source is tied to an official work in the source registry; that work is the
    representative fallback when its page title omits the franchise name. Any
    additional works are allowed only for explicit collaborations or a curation
    match whose individual catalog aliases were grounded in the source evidence.
    Extracted topic and generated copy are intentionally excluded.
    """
    if package.work_match_evidence:
        valid_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
        evidence_ids = list(dict.fromkeys(
            item.menu_work_id for item in package.work_match_evidence
            if item.menu_work_id in valid_ids
        ))
        explicit_title_ids = list(dict.fromkeys(
            item.menu_work_id
            for item in package.work_match_evidence
            if item.basis in {"article_title", "headline_character"}
            and item.menu_work_id in valid_ids
        ))
        multi_work_title = next(
            (evidence.title for evidence in package.sources
             if len(_match_title_work_ids(evidence.title or "")) > 1),
            "",
        )
        if (
            _headline_names_multiple_works(
                multi_work_title, evidence_ids, package.content_type
            )
        ):
            return explicit_title_ids or evidence_ids
        if (
            package.content_type == "curation"
            and len(evidence_ids) > 1
            and any(item.basis == "llm_curation_list" for item in package.work_match_evidence)
        ):
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
    multi_work_title = next(
        (
            evidence.title
            for evidence in primary_first
            if len(_match_title_work_ids(evidence.title or "")) > 1
        ),
        "",
    )
    if multi_work_title and _headline_names_multiple_works(
        multi_work_title,
        _match_title_work_ids(multi_work_title),
        package.content_type,
    ):
        return _match_title_work_ids(multi_work_title)
    if ordered:
        return ordered[:1]

    # Character names are checked in publisher titles only; body copy,
    # extracted facts, and song/artist aliases are excluded.
    for evidence in primary_first:
        ids = _headline_character_work_ids(evidence.title or "")
        if ids:
            return ids

    # Source registry mapping supplies a reliable representative when neither
    # the title nor a headline character alias identified the work.
    for evidence in primary_first:
        for registered_title in registered_titles.get(evidence.id, ()):
            append_title_matches(registered_title)
    if ordered:
        return ordered[:1]
    return []


def attach_menu_work_ids(package: ContentPackage) -> ContentPackage:
    package.menu_work_ids = resolve_menu_work_ids(package)
    return package
