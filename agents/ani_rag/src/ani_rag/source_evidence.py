"""Keep the byline, quoted review passage and poll context from a source read."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import date, datetime
from typing import Mapping
from urllib.parse import urlsplit

from ani_rag.adapter import ReadResult, canonical_source_item_url
from ani_rag.models import (
    CollectionMethod, CurationContext, EvidenceDocument, EvidenceExcerpt, EvidenceType, WorkLinkStatus,
)
from ani_rag.retrieval import EvidenceChunk, RetrievedEvidence

WEB_ASSESSMENT_POLICY_VERSION = "work-and-quoted-sources-v2"


class WebSourceWorkConflict(RuntimeError):
    """A previously linked primary article belongs to a different work."""


def web_snapshot_key(url: str, title: str, body: str) -> tuple[str, str]:
    """Use the existing primary-source snapshot identity, including the version of the text."""
    def digest(parts: list[str]) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    content_hash = digest([title, body])
    return "web_" + digest([canonical_source_item_url(url), content_hash]), content_hash


def web_quote_chunk_id(url: str, title: str, body: str, passage: str) -> str:
    document_id, _ = web_snapshot_key(url, title, body)
    return "quote_" + hashlib.sha256((document_id + "\n" + passage).encode()).hexdigest()


def interview_speaker_label(body: str, speaker: str, context: str, passage: str) -> str | None:
    """Bind a direct answer to its printed speaker, including a Japanese surname label."""
    if not speaker.strip() or not passage.strip() or context not in body or passage not in context:
        return None
    before, _, after = context.partition(passage)
    turn = re.search(r"(?:^|\n)\s*[*_]*([^:\n：]{1,65}?)\s*[*_]*[:：][*_]*\s*[^:\n：]*$", before)
    if turn:
        label = turn.group(1).strip(" *_")
        label = re.sub(r"^(?:director|producer|composer|animator|actor)\s+", "", label, flags=re.I)
        surname = re.sub(r"-(?:san|sensei)$", "", label, flags=re.I)
        if " " not in surname and surname.isascii():
            names = {match.casefold() for match in re.findall(
                rf"(?<![\w-])([A-Z][a-zA-Z’'-]+)[ \t]+{re.escape(surname)}\b", body,
            ) if match.lower() not in {"director", "producer", "animator", "mr", "ms", "mrs", "composer", "actor"}}
            if len(names) > 1:
                return None
        if label.casefold() == speaker.casefold():
            return label
        parts = speaker.split()
        if (len(parts) >= 2 and speaker.casefold() in body.casefold()
                and surname.casefold() == parts[-1].casefold()):
            return label  # Preserve the printed answer label rather than guessing a full name.
        return None
    name = re.escape(speaker)
    says = r"(?:said|says|explained|recalled|added|told us|は語った|는 말했다|은 말했다)"
    if (re.search(rf"{name}\s+{says}[\s,:：\"“「-]*$", before, re.I)
            or re.match(rf"^[\s,\"”」-]*(?:{says}\s+{name}|{name}\s+{says})(?:\W|$)", after, re.I)):
        return speaker
    return None


def attach_web_quotes(
    retrieval: RetrievedEvidence, source: Mapping[str, object], existing: EvidenceDocument | None = None,
) -> RetrievedEvidence:
    """Supply only verifiable attributed passages from the exact assessed article snapshot."""
    quotes = [c for c in source.get("verified_claims", ()) if isinstance(c, dict) and c.get("kind") == "attributed_quote"]
    if not quotes:
        return retrieval
    url, title, body = (source[k] for k in ("url", "title", "original_text"))
    if not all(isinstance(value, str) and value.strip() for value in (url, title, body)):
        raise ValueError("Missing assessed article snapshot")
    document_id, _ = web_snapshot_key(url, title, body)
    document = EvidenceDocument(
        document_id=document_id, source_id=f"web_discovery:{urlsplit(url).hostname}",
        title=title, source_url=url, body=body, language=str(source.get("language") or "und"),
        retrieved_at=datetime.fromisoformat(str(source["fetched_at"])),
        published_at=datetime.fromisoformat(str(source["published_at"])) if source.get("published_at") else None,
        evidence_type=EvidenceType.EDITORIAL_CLAIM, collection_method=CollectionMethod.MCP_READ,
        content_type=retrieval.content_type, work_link_status=WorkLinkStatus.MATCHED,
        menu_work_ids=retrieval.menu_work_ids, metadata={"role": "web_candidate_original"},
    )
    if existing is not None:
        if existing.body != body or existing.title != title or canonical_source_item_url(existing.source_url) != canonical_source_item_url(url):
            raise ValueError("The registered snapshot differs from the assessed article")
        if (existing.work_link_status is WorkLinkStatus.AMBIGUOUS or
                (existing.menu_work_ids and set(existing.menu_work_ids) != set(retrieval.menu_work_ids))):
            raise WebSourceWorkConflict("Conflicting work link on primary interview")
        document = replace(existing, work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=retrieval.menu_work_ids)
    chunks = {}
    for quote in quotes:
        passage, speaker, context = (quote.get(k) for k in ("claim", "speaker", "attribution_excerpt"))
        if not all(isinstance(v, str) and v.strip() for v in (passage, speaker, context)):
            raise ValueError("Missing interview passage or speaker")
        if interview_speaker_label(body, speaker, context, passage) != speaker:
            raise ValueError("Interview attribution differs from the stored article")
        chunk_id = web_quote_chunk_id(url, title, body, passage)
        if tuple(quote.get("chunk_ids") or ()) != (chunk_id,):
            raise ValueError("Interview citation differs from the stored article")
        start = body.index(context) + context.index(passage)
        end = start + len(passage)
        chunks[chunk_id] = EvidenceChunk(
            chunk_id=chunk_id, evidence_id=document.document_id, title=title, text=passage,
            locator=f"body:{start}-{end}", source_kind="body", source_index=None,
            start_offset=start, end_offset=end, source_id=document.source_id, source_url=document.source_url,
            language=document.language, published_at=document.published_at,
            menu_work_ids=retrieval.menu_work_ids, content_type=document.content_type,
        )
    return replace(retrieval, documents=(*retrieval.documents, document), chunks=(*retrieval.chunks, *chunks.values()))


def review_passages(page: ReadResult) -> tuple[str, tuple[EvidenceExcerpt, ...]] | None:
    """Use the author's own observations from the approved English review articles."""
    path = urlsplit(page.url).path.lower()
    if (not re.search(r"\breview\b", page.title, re.I)
            or path.startswith(("/category/", "/tag/", "/author/"))):
        return None
    metadata = (page.raw_payload or {}).get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    byline = metadata.get("author") or metadata.get("article:author")
    if isinstance(byline, dict):
        byline = byline.get("name")
    body = page.body or ""
    if not isinstance(byline, str) or not byline.strip() or "://" in byline:
        by_match = re.search(
            r"(?im)^\s*(?:written\s+)?by\s+(?:\[([^\]\n]+)\]\([^\n)]+\)|([^\n|]{2,80}?))\s*$", body
        )
        byline = (by_match.group(1) or by_match.group(2)) if by_match else None
    if not byline:
        return None
    excerpts = []
    for i, paragraph in enumerate(re.split(r"\n\s*\n", body)):
        paragraph = paragraph.strip()
        if re.match(r"(?i)^(?:#{1,6}\s*)?(?:comments|about the author|related articles)\b", paragraph):
            break
        if (not paragraph or paragraph[0] in "#![]>*\"“" or "](" in paragraph):
            continue
        # A direct quotation with a locator. Never turn the publisher's boilerplate
        # or a character's reported speech into the review author's opinion.
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if (20 <= len(sentence) <= 450
                    and re.match(r"(?:For me,\s*|Personally,\s*)?I (?:think|thought|felt|found|enjoyed|loved|liked|prefer(?:red)?)\b",
                                  sentence, re.I)):
                excerpts.append(EvidenceExcerpt(sentence, f"review:paragraph:{i + 1}", len(excerpts)))
                break
        if len(excerpts) == 3:
            break
    return (byline.strip(), tuple(excerpts)) if excerpts else None


def poll_context(page: ReadResult) -> CurationContext | None:
    """Only record survey values the chart actually publishes."""
    body = page.body or ""
    if not re.search(r"\b(?:poll|chart|ranking)s?\b", page.title, re.I):
        return None
    period = re.search(
        r"(?im)^\s*(?:voting|survey|poll) (?:period|window)\s*:\s*"
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|[–—-])\s*(\d{4}-\d{2}-\d{2})\b", body
    )
    start = end = None
    if period:
        try:
            start, end = map(date.fromisoformat, period.groups())
        except ValueError:
            start = end = None
        if start and end and start > end:
            start = end = None
    sample = re.search(r"(?im)^[ \t]*(?:sample size|(?:total )?respondents)[ \t]*:[ \t]*(\d+(?:,\d{3})*)[ \t]*$", body)
    method = re.search(r"(?im)^\s*(?:aggregation|ranking|voting) method\s*:\s*([^\n]{2,300})$", body)
    return CurationContext(
        window_start=start, window_end=end,
        sample_size=int(sample.group(1).replace(",", "")) if sample else None,
        aggregation_method=method.group(1).strip() if method else None,
        source_context=page.title,
    )
