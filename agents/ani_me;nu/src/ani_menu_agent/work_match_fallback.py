"""LLM-assisted work matching for titles the deterministic resolver cannot map."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from time import perf_counter
from typing import Literal
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from ani_menu_agent.config import get_settings
from ani_menu_agent.generation import estimate_gpt_6_luna_cost
from ani_menu_agent.models import StructuredWorkReference, UsageRecord, WorkMatchEvidence
from ani_menu_agent.work_catalog import (
    _alias_scope_matches,
    _franchise_work_id,
    allows_multiple_work_match,
    load_work_aliases,
    load_work_catalog,
    load_work_entries,
    load_work_external_entries,
)
from ani_menu_agent.work_catalog_registration import register_unmatched_work_from_kitsu

logger = logging.getLogger(__name__)
_CATALOG_ALIAS_INDEX_CACHE: tuple | None = None
_GENERIC_TITLE_TOKENS = {
    "anime",
    "announcement",
    "article",
    "episode",
    "event",
    "goods",
    "goodsnews",
    "interview",
    "new",
    "news",
    "official",
    "project",
    "review",
    "season",
    "series",
    "special",
    "the",
    "title",
    "volume",
    "work",
}


def _title_queries(titles: list[str]) -> list[str]:
    """Keep the full headline and its explicit title-like clauses for retrieval."""
    queries: list[str] = []
    for raw_title in titles:
        title = str(raw_title or "").strip()
        if not title:
            continue
        queries.append(title)
        clauses = re.split(r"\s+(?:[—–|/])\s+|\s+[·•]\s+", title)
        queries.extend(clause.strip(" \t:-") for clause in clauses if clause.strip(" \t:-"))
    return list(dict.fromkeys(query for query in queries if len(query) >= 3))


def _retrieval_score(query: str, alias: str) -> float:
    """Rank aliases using full-title, token, and contained-phrase similarity."""
    alias_key = _normalized_title_evidence(alias)
    query_key = _normalized_title_evidence(query)
    if len(alias_key) <= 2:
        # Very short titles such as "C" are too collision-prone for fuzzy retrieval;
        # in particular, do not read the C in "D.C." as a separate work title.
        return 100.0 if query_key == alias_key else 0.0
    alias_tokens = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", alias).casefold()))
    query_tokens = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", query).casefold()))
    shared_tokens = len(alias_tokens & query_tokens)
    partial_weight = 0.88 if shared_tokens >= min(2, len(alias_tokens)) else 0.45
    scores = [
        fuzz.WRatio(query, alias),
        fuzz.token_set_ratio(query, alias) * 0.96,
        fuzz.partial_ratio(query, alias) * partial_weight,
    ]
    return max(scores)


def _normalized_title_evidence(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _title_tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))
    return {
        token
        for token in tokens
        if len(_normalized_title_evidence(token)) >= 3
        and token not in _GENERIC_TITLE_TOKENS
    }


def _evidence_is_from_source(evidence_title: str, supplied_titles: set[str]) -> bool:
    evidence = _normalized_title_evidence(evidence_title)
    if len(evidence) < 3:
        return False
    return any(
        evidence in _normalized_title_evidence(title)
        for title in supplied_titles
        if title
    )


class CatalogWorkSelection(BaseModel):
    menu_work_id: int = Field(gt=0)
    catalog_title: str = Field(min_length=1, max_length=300)


class WorkIdentityDecision(BaseModel):
    """Constrained LLM decision; IDs must come from the supplied catalog candidates."""

    action: Literal["match_existing", "register_new", "unresolved"]
    catalog_matches: list[CatalogWorkSelection] = Field(default_factory=list, max_length=10)
    evidence_title: str = Field(default="", max_length=500)
    evidence_basis: Literal["headline_or_about", "body_excerpt"] = "headline_or_about"
    evidence_quote: str = Field(default="", max_length=600)
    subject_relation: Literal["primary_subject", "incidental_mention", "unclear"] = "unclear"
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(default="", max_length=600)


def _candidate_catalog_rows(
    titles: list[str],
    *,
    body_queries: list[str] | None = None,
    source_url: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Retrieve a multilingual shortlist without fuzzy-scoring the full catalog per query."""
    global _CATALOG_ALIAS_INDEX_CACHE
    catalog_rows = load_work_catalog()
    entry_rows = load_work_entries()
    external_rows = load_work_external_entries()
    reviewed_alias_rows = load_work_aliases()
    cache_key = (
        id(catalog_rows),
        id(entry_rows),
        id(external_rows),
        id(reviewed_alias_rows),
    )
    if _CATALOG_ALIAS_INDEX_CACHE and _CATALOG_ALIAS_INDEX_CACHE[0] == cache_key:
        (
            _key,
            _row_refs,
            base_grouped,
            alias_work_ids,
            normalized_aliases,
            token_aliases,
            aliases,
        ) = (
            _CATALOG_ALIAS_INDEX_CACHE
        )
    else:
        base_grouped: dict[int, dict] = {}
        alias_work_ids: dict[str, set[int]] = {}

        def ensure_candidate(row: dict, *, year: str = "") -> dict | None:
            try:
                work_id = _franchise_work_id(int(row["menu_work_id"]))
            except (KeyError, TypeError, ValueError):
                return None
            return base_grouped.setdefault(
                work_id,
                {
                    "menu_work_id": work_id,
                    "canonical_titles": [],
                    "entry_titles": {},
                    "year": year,
                    "score": 0,
                },
            )

        def add_alias(row: dict, alias: str, *, canonical: bool, year: str = "") -> None:
            alias = str(alias or "").strip()
            if not alias:
                return
            candidate = ensure_candidate(row, year=year)
            if candidate is None:
                return
            alias_work_ids.setdefault(alias, set()).add(candidate["menu_work_id"])
            if canonical:
                if alias not in candidate["canonical_titles"]:
                    candidate["canonical_titles"].append(alias)
            else:
                candidate["entry_titles"].setdefault(alias, 0)

        for row in catalog_rows:
            year = str(row.get("first_year") or row.get("year") or "")
            for key in ("title_ko", "title_romaji", "title_english", "title_native"):
                add_alias(row, row.get(key), canonical=True, year=year)

        for row in entry_rows:
            year = str(row.get("year") or "")
            for key in ("title_ko", "title_romaji", "title_english", "title_native"):
                add_alias(row, row.get(key), canonical=False, year=year)

        for row in external_rows:
            add_alias(
                row,
                row.get("canonical_title"),
                canonical=False,
                year=str(row.get("year") or ""),
            )

        for row in reviewed_alias_rows:
            if row.get("source_url_prefix"):
                continue
            add_alias(row, row.get("alias"), canonical=False)

        normalized_aliases = {
            alias: _normalized_title_evidence(alias) for alias in alias_work_ids
        }
        aliases = list(alias_work_ids)
        token_aliases: dict[str, set[str]] = {}
        for alias in aliases:
            for token in _title_tokens(alias):
                token_aliases.setdefault(token, set()).add(alias)
        _row_refs = (catalog_rows, entry_rows, external_rows, reviewed_alias_rows)
        _CATALOG_ALIAS_INDEX_CACHE = (
            cache_key,
            _row_refs,
            base_grouped,
            alias_work_ids,
            normalized_aliases,
            token_aliases,
            aliases,
        )

    grouped = {
        work_id: {
            **candidate,
            "canonical_titles": list(candidate["canonical_titles"]),
            "entry_titles": dict(candidate["entry_titles"]),
            "score": 0,
        }
        for work_id, candidate in base_grouped.items()
    }

    queries = _title_queries(titles)
    # Exact mentions in the title or opening body are cheap to find by scanning
    # normalized aliases. Fuzzy search is then limited to headline clauses; the
    # former implementation fuzzy-scored every alias against up to 12 body
    # sentences for every unresolved item, which dominated the matching runtime.
    retrieved_scores: dict[str, float] = {}
    scoped_aliases: dict[str, tuple[str, set[int]]] = {}
    for row in reviewed_alias_rows:
        if not row.get("source_url_prefix") or not _alias_scope_matches(row, source_url):
            continue
        alias = str(row.get("alias") or "").strip()
        alias_key = _normalized_title_evidence(alias)
        if len(alias_key) < 3:
            continue
        try:
            work_id = _franchise_work_id(int(row["menu_work_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if alias_key not in scoped_aliases:
            scoped_aliases[alias_key] = (alias, {work_id})
        else:
            scoped_aliases[alias_key][1].add(work_id)
    scoped_keys = {
        key for key, (_alias, work_ids) in scoped_aliases.items() if len(work_ids) == 1
    }
    exact_texts = [*queries, *(body_queries or [])]
    normalized_texts = [
        _normalized_title_evidence(text) for text in exact_texts if len(str(text or "")) >= 3
    ]
    for alias, alias_key in normalized_aliases.items():
        if (
            len(alias_key) >= 3
            and alias_key not in scoped_keys
            and any(alias_key in text for text in normalized_texts)
        ):
            retrieved_scores[alias] = 100.0
    scoped_retrieved = {
        alias_key: (alias, next(iter(work_ids)))
        for alias_key, (alias, work_ids) in scoped_aliases.items()
        if len(work_ids) == 1 and any(alias_key in text for text in normalized_texts)
    }

    # Fuzzy scoring only runs on aliases sharing informative tokens with a
    # headline. This avoids invoking Python RapidFuzz scorers across the entire
    # catalog. Exact multilingual title mentions in the opening body were handled
    # above, so body prose is never fuzzily compared with thousands of aliases.
    for query in queries[:4]:
        tokens = _title_tokens(query)
        indexed_tokens = sorted(
            (token for token in tokens if token in token_aliases),
            key=lambda token: (len(token_aliases[token]), token),
        )
        if not indexed_tokens:
            continue
        selected_tokens = indexed_tokens[:4]
        fuzzy_aliases: set[str] = set()
        for token in selected_tokens:
            fuzzy_aliases.update(token_aliases[token])
        if len(fuzzy_aliases) > 1_500:
            fuzzy_aliases = set(token_aliases[selected_tokens[0]])
        for alias in fuzzy_aliases:
            if _normalized_title_evidence(alias) in scoped_keys:
                continue
            score = _retrieval_score(query, alias)
            retrieved_scores[alias] = max(retrieved_scores.get(alias, 0.0), score)

    for alias, score in retrieved_scores.items():
        for work_id in alias_work_ids[alias]:
            candidate = grouped[work_id]
            candidate["score"] = max(candidate["score"], score)
            if alias in candidate["entry_titles"]:
                candidate["entry_titles"][alias] = max(
                    candidate["entry_titles"][alias], score
                )
    for alias, work_id in scoped_retrieved.values():
        candidate = grouped.get(work_id)
        if candidate is not None:
            candidate["score"] = 100.0
            candidate["entry_titles"][alias] = 100.0

    ranked = sorted(
        grouped.values(), key=lambda item: (-item["score"], item["menu_work_id"])
    )[:limit]
    candidates = []
    for item in ranked:
        canonical = item["canonical_titles"][:4]
        entry_titles = sorted(
            item["entry_titles"].items(), key=lambda pair: (-pair[1], -len(pair[0]))
        )
        selected_titles = list(dict.fromkeys(
            [*canonical, *(title for title, _score in entry_titles[:4])]
        ))
        candidates.append(
            {
                "menu_work_id": item["menu_work_id"],
                "titles": selected_titles,
                "year": item["year"],
                "similarity_hint": round(item["score"] / 100, 2),
            }
        )
    return candidates


def _body_context_queries(body_excerpt: str) -> list[str]:
    """Use opening-page passages for candidate retrieval after title matching fails."""
    sentences = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", body_excerpt)
    return list(
        dict.fromkeys(
            sentence.strip()[:320]
            for sentence in sentences[:16]
            if len(sentence.strip()) >= 3
        )
    )[:12]


def _normalized_source_quote(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _body_evidence_is_grounded(
    *, evidence_title: str, evidence_quote: str, body_excerpt: str
) -> bool:
    quote = _normalized_source_quote(evidence_quote)
    body = _normalized_source_quote(body_excerpt)
    title = _normalized_title_evidence(evidence_title)
    quote_title = _normalized_title_evidence(evidence_quote)
    return len(quote) >= 20 and quote in body and len(title) >= 3 and title in quote_title


def _is_shortened_or_untranslated_name(evidence_title: str, catalog_titles: set[str]) -> bool:
    """Whether a headline name can differ from these titles only in form, not in work."""
    evidence = _normalized_title_evidence(evidence_title)
    if len(evidence) >= 3 and any(
        evidence in _normalized_title_evidence(title) for title in catalog_titles
    ):
        return True
    has_hangul = re.compile(r"[가-힣]").search
    return bool(has_hangul(evidence_title)) and not any(map(has_hangul, catalog_titles))


def _is_generic_listing_url(source_url: str | None) -> bool:
    path = urlparse(source_url or "").path.casefold()
    return bool(
        re.search(r"/special/\d+/recent(?:/|$)", path)
        or re.fullmatch(r"/release/(?:prtimes|dreamnews)/?", path)
    )


def resolve_unmatched_work_with_llm(
    *,
    titles: list[str],
    source_url: str | None = None,
    structured_references: list[StructuredWorkReference] | None = None,
    body_excerpt: str | None = None,
    outbound_links: list[dict] | None = None,
    content_type: str | None = None,
    usage_records: list[dict] | None = None,
    audit: dict | None = None,
) -> list[WorkMatchEvidence]:
    """Resolve an unmatched title; stage confirmed new works for team review.

    This fallback runs only after deterministic title and metadata matching fails.
    A short opening excerpt may help identify the article's primary subject, but
    incidental body mentions and outbound-link labels alone are never sufficient.
    Curation lists may link several works only when each selected catalog alias is
    present in the same quoted source passage as an actual listed target.
    """
    settings = get_settings()
    clean_titles = list(
        dict.fromkeys(str(value).strip() for value in titles if str(value).strip())
    )
    if not settings.openai_api_key or not clean_titles:
        if audit is not None:
            audit.update({"status": "skipped", "reason": "missing_api_key_or_title"})
        return []

    about_refs = [
        ref.model_dump(mode="json")
        for ref in structured_references or []
        if ref.relation == "about"
    ]
    clean_body_excerpt = " ".join(str(body_excerpt or "").split())[:2_000]
    body_chars_available = len(" ".join(str(body_excerpt or "").split()))
    if clean_body_excerpt and _is_generic_listing_url(source_url):
        if audit is not None:
            audit.update(
                {
                    "status": "body_context_skipped_listing_page",
                    "reason": "A collection/listing page is not article-level work evidence.",
                }
            )
        return []
    identity_titles = [*clean_titles, *(ref["name"] for ref in about_refs)]
    catalog_candidates = _candidate_catalog_rows(
        identity_titles,
        body_queries=(
            _body_context_queries(clean_body_excerpt) if clean_body_excerpt else []
        ),
        source_url=source_url,
    )
    if audit is not None:
        audit["capture_diagnostics"] = {
            "source_url": source_url,
            "title_count": len(clean_titles),
            "about_reference_count": len(about_refs),
            "body_excerpt_chars": len(clean_body_excerpt),
            "body_excerpt_truncated": body_chars_available > len(clean_body_excerpt),
            "candidate_work_count": len(catalog_candidates),
        }
    payload = {
        "titles": clean_titles,
        "content_type": content_type,
        "publisher_about_references": about_refs[:12],
        "opening_body_excerpt_for_fallback_only": clean_body_excerpt,
        "outbound_links_for_corroboration_only": [
            {"text": str(link.get("text") or "")[:200], "url": str(link.get("url") or "")[:350]}
            for link in (outbound_links or [])[:20]
            if isinstance(link, dict)
        ],
        "existing_catalog_candidates": catalog_candidates,
    }
    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openai_api_key,
    ).with_structured_output(
        WorkIdentityDecision,
        method="function_calling",
        include_raw=True,
    )
    started = perf_counter()
    try:
        response = model.invoke(
            [
                (
                    "system",
                    "Identify the anime work that is the primary subject of this source item. "
                    "This LLM fallback runs only after deterministic title/metadata matching "
                    "failed. First use supplied page/headline titles and publisher-declared "
                    "about references. "
                    "If those do not identify the work, you may inspect the opening body excerpt. "
                    "A verified catalog alias in the supplied headline or publisher about-reference "
                    "is sufficient title evidence. If the alias appears only in the body, require "
                    "the quoted sentence to explicitly connect that alias to the work (for example, "
                    "by spelling out the full title nearby or labeling the abbreviation). Use the "
                    "exact source wording as evidence_title. Do not treat a passing mention of a "
                    "work elsewhere in the article as its primary subject. "
                    "A name merely mentioned in the body is not enough: distinguish the article's "
                    "main subject from examples, comparisons, recommendations, ads, and incidental "
                    "mentions. For body-based decisions, set evidence_basis='body_excerpt', copy "
                    "one exact supporting sentence or contiguous passage into evidence_quote, and "
                    "set subject_relation='primary_subject' only when that passage establishes the "
                    "work as the article's main subject. Otherwise choose unresolved. "
                    "Outbound links are corroboration only; never assign a work from an anchor "
                    "label or URL alone. Roundups naming several works without one clear primary "
                    "subject must remain unresolved unless content_type is curation and the "
                    "evidence explicitly enumerates the works as the actual items being curated. "
                    "Prefer an exact existing menu_work_id when its alias names the same work. "
                    "A season or movie entry already listed under a canonical work maps to that "
                    "entry's menu_work_id; do not create a separate ID for it. Keep a distinct "
                    "series such as Yu-Gi-Oh! GX separate from another series in its franchise. "
                    "Do not infer a parent link from franchise familiarity alone; require the "
                    "headline, publisher about-reference, or clear opening-page evidence to "
                    "identify "
                    "the primary work clearly. "
                    "Put each selected ID and alias from its candidate row in catalog_matches. "
                    "Keep distinct sequels and spinoffs separate unless the catalog maps them "
                    "to one canonical work. Return multiple IDs only for a clear collaboration, "
                    "or for curation when every selected work is explicitly listed as a roundup, "
                    "ranking, recommendation, or poll target in the supplied evidence. Do not "
                    "include works that are merely examples, comparisons, or incidental mentions. "
                    "For such a curation list, set subject_relation='primary_subject' only when "
                    "the evidence quote includes the actual itemized targets and every selected "
                    "catalog alias is grounded in that quote. "
                    "Choose register_new only for a distinct anime absent from the candidates. "
                    "A confirmed new title is logged for team review and receives no menu ID yet. "
                    "For match_existing or register_new, set evidence_title to the exact work-name "
                    "phrase appearing in the selected evidence. If evidence_basis is "
                    "headline_or_about, the phrase must occur in a supplied title or publisher "
                    "about-reference. If it is body_excerpt, evidence_title must occur in "
                    "evidence_quote and the quote must be "
                    "copied verbatim from the opening excerpt. Never invent titles or quotes. "
                    "Kitsu confirmation only records a discovery; "
                    "never add a new work ID. "
                    "If uncertain, choose unresolved. Never invent IDs or evidence titles. "
                    "Confidence must reflect the evidence.",
                ),
                ("human", json.dumps(payload, ensure_ascii=False)),
            ]
        )
    except Exception:
        logger.exception("LLM fallback failed while resolving an unmatched work title")
        if audit is not None:
            audit.update({"status": "llm_error"})
        return []

    raw = response.get("raw") if isinstance(response, dict) else None
    usage = getattr(raw, "usage_metadata", None) or {}
    input_details = usage.get("input_token_details") or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached_tokens = int(input_details.get("cache_read", 0) or 0)
    cache_write_tokens = int(
        input_details.get("cache_write", input_details.get("cache_creation", 0)) or 0
    )
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    usage_record = UsageRecord(
        model=settings.model_name,
        stage="work_match_fallback",
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimate_gpt_6_luna_cost(
            input_tokens, cached_tokens, cache_write_tokens, output_tokens
        ),
        elapsed_seconds=round(perf_counter() - started, 3),
    )
    if usage_records is not None:
        usage_records.append(usage_record.model_dump(mode="json"))
    decision = response.get("parsed") if isinstance(response, dict) else None
    if not isinstance(decision, WorkIdentityDecision):
        logger.warning("LLM returned no structured work identity decision")
        if audit is not None:
            audit.update({"status": "invalid_llm_response"})
        return []

    if audit is not None:
        audit.update(
            {
                "action": decision.action,
                "confidence": decision.confidence,
                "evidence_title": decision.evidence_title,
                "evidence_basis": decision.evidence_basis,
                "evidence_quote": decision.evidence_quote,
                "subject_relation": decision.subject_relation,
                "catalog_matches": [
                    match.model_dump(mode="json") for match in decision.catalog_matches
                ],
                "rationale": decision.rationale,
            }
        )

    supplied_title_evidence = {
        *clean_titles,
        *(str(ref.get("name") or "") for ref in about_refs),
    }
    title_evidence_valid = _evidence_is_from_source(
        decision.evidence_title, supplied_title_evidence
    )
    body_evidence_valid = bool(clean_body_excerpt) and _body_evidence_is_grounded(
        evidence_title=decision.evidence_title,
        evidence_quote=decision.evidence_quote,
        body_excerpt=clean_body_excerpt,
    )
    evidence_valid = (
        title_evidence_valid
        if decision.evidence_basis == "headline_or_about"
        else body_evidence_valid and decision.subject_relation == "primary_subject"
    )
    if decision.action != "unresolved" and not evidence_valid:
        logger.warning("Rejecting LLM work match with title evidence absent from source metadata")
        if audit is not None:
            audit["status"] = (
                "invalid_body_subject_evidence"
                if decision.evidence_basis == "body_excerpt"
                else "invalid_source_title_evidence"
            )
        return []
    minimum_confidence = {
        "match_existing": 0.80,
        "register_new": 0.90,
        "unresolved": 0.0,
    }[decision.action]
    if decision.confidence < minimum_confidence:
        logger.info(
            "Leaving work title unresolved: %s confidence %.2f below %.2f; rationale=%s",
            decision.action,
            decision.confidence,
            minimum_confidence,
            decision.rationale,
        )
        if audit is not None:
            audit["status"] = "low_confidence"
        return []
    candidate_ids = {int(row["menu_work_id"]) for row in catalog_candidates}
    if decision.action == "match_existing":
        selected = list(
            {match.menu_work_id: match for match in decision.catalog_matches}.values()
        )
        ids = [match.menu_work_id for match in selected]
        if not ids or any(work_id not in candidate_ids for work_id in ids):
            logger.warning("Rejecting LLM work match containing IDs outside the catalog shortlist")
            if audit is not None:
                audit["status"] = "id_outside_catalog_shortlist"
            return []
        if len(ids) > 1 and not allows_multiple_work_match(
            " ".join(
                [*clean_titles, decision.evidence_quote, decision.evidence_title, source_url or ""]
            ),
            ids,
            content_type,
        ):
            if audit is not None:
                audit.update(
                    {
                        "status": "multiple_work_match_not_allowed",
                        "reason": "Multiple IDs require an explicit collaboration or itemized curation evidence.",
                    }
                )
            return []
        aliases_by_id = {
            int(row["menu_work_id"]): set(row["titles"])
            for row in catalog_candidates
        }
        if any(
            match.catalog_title not in aliases_by_id[match.menu_work_id] for match in selected
        ):
            logger.warning("Rejecting LLM work match without a catalog title supporting every ID")
            if audit is not None:
                audit["status"] = "catalog_title_not_in_shortlist"
            return []
        evidence_quote = decision.evidence_quote or decision.evidence_title
        normalized_quote = _normalized_title_evidence(evidence_quote)
        grounded_aliases_by_id = {
            match.menu_work_id: [
                alias
                for alias in aliases_by_id[match.menu_work_id]
                if len(_normalized_title_evidence(alias)) >= 2
                and _normalized_title_evidence(alias) in normalized_quote
            ]
            for match in selected
        }
        ungrounded_ids = [
            work_id for work_id, aliases in grounded_aliases_by_id.items() if not aliases
        ]
        if ungrounded_ids:
            if len(ids) != 1:
                if audit is not None:
                    audit.update(
                        {
                            "status": "catalog_alias_not_grounded_for_collaboration",
                            "ungrounded_ids": ungrounded_ids,
                        }
                    )
                return []
            confirmed = register_unmatched_work_from_kitsu(
                [decision.evidence_title],
                structured_references=structured_references,
                existing_menu_work_id=ids[0],
                source_url=source_url,
            )
            if confirmed is None:
                # A confident headline reading can link the work when the name differs from
                # the catalog only by abbreviation or by a Korean title it does not have yet.
                if (
                    decision.evidence_basis == "headline_or_about"
                    and decision.confidence >= 0.9
                    and _is_shortened_or_untranslated_name(
                        decision.evidence_title, aliases_by_id[ids[0]]
                    )
                ):
                    if audit is not None:
                        audit.update(
                            {
                                "status": "matched",
                                "catalog_alias_confirmation": "llm_headline_only",
                                "ungrounded_ids": ungrounded_ids,
                            }
                        )
                    return [
                        WorkMatchEvidence(
                            menu_work_id=ids[0],
                            basis="llm_title_match",
                            matched_value=decision.evidence_title,
                            evidence_url=source_url,
                            confidence=decision.confidence,
                        )
                    ]
                logger.info(
                    "Rejecting LLM work match: catalog alias was not grounded or Kitsu-confirmed"
                )
                if audit is not None:
                    audit.update(
                        {
                            "status": "catalog_alias_not_confirmed",
                            "ungrounded_ids": ungrounded_ids,
                        }
                    )
                return []
            if audit is not None:
                audit.update(
                    {
                        "status": "matched",
                        "catalog_alias_confirmation": "kitsu_exact_title",
                    }
                )
            return [confirmed]
        if audit is not None:
            audit["grounded_catalog_aliases"] = grounded_aliases_by_id
        logger.info(
            "LLM matched source title to catalog matches=%s at confidence %.2f; rationale=%s",
            [(match.menu_work_id, match.catalog_title) for match in selected],
            decision.confidence,
            decision.rationale,
        )
        if audit is not None:
            audit["status"] = "matched"
        if len(ids) == 1:
            # Kitsu's exact title confirmation lets future runs resolve an AniList
            # season/movie title to the canonical menu_work_id without repeating LLM work.
            # The external ID stays in menu_work_external_entries.csv, never in anilist_id.
            mapped_entry = register_unmatched_work_from_kitsu(
                [decision.evidence_title], existing_menu_work_id=ids[0]
            )
            if mapped_entry is not None:
                if audit is not None:
                    audit["external_title_mapping"] = "kitsu_confirmed"
                return [mapped_entry]
            if audit is not None:
                audit["external_title_mapping"] = "not_confirmed"
        return [
            WorkMatchEvidence(
                menu_work_id=work_id,
                basis=(
                    "llm_curation_list"
                    if content_type == "curation" and len(ids) > 1
                    else "llm_body_context"
                    if decision.evidence_basis == "body_excerpt"
                    else "llm_title_match"
                ),
                matched_value=(
                    grounded_aliases_by_id[work_id][0]
                    if content_type == "curation" and len(ids) > 1
                    else decision.evidence_title
                ),
                evidence_url=source_url,
                confidence=decision.confidence,
            )
            for work_id in ids
        ]

    if decision.action == "register_new" and decision.evidence_title.strip():
        logger.info(
            "LLM proposed a distinct title for Kitsu verification: %s; rationale=%s",
            decision.evidence_title,
            decision.rationale,
        )
        discovery_audit: dict = {}
        registered = register_unmatched_work_from_kitsu(
            [decision.evidence_title],
            source_url=source_url,
            audit=discovery_audit,
        )
        if audit is not None:
            audit["status"] = discovery_audit.get(
                "status",
                "registered_existing" if registered else "kitsu_confirmation_failed",
            )
            if discovery_audit:
                audit["discovered_work"] = discovery_audit
            if registered:
                audit["menu_work_id"] = registered.menu_work_id
        return [registered] if registered else []
    logger.info("LLM left unmatched work title unresolved: %s", decision.rationale)
    if audit is not None:
        audit["status"] = "unresolved"
    return []
