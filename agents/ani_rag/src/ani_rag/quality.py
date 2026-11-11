"""Deterministic publication gates; semantic review is a separate service."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from ani_rag.content import ContentDraft, ImageAsset
from ani_rag.models import EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import EvidenceChunk

MIN_CARDS = 3
MAX_CARDS = 10
MAX_CARD_HEADLINE_CHARS = 42
MAX_CARD_BODY_CHARS = 60
_SOURCE_META_COPY = re.compile(
    r"(?:kitsu|wikidata|위키데이터|자료|원문|출처|검색 결과|데이터베이스|\bdb\b)"
    r".{0,24}(?:확인|기록|표기|포함|수록|검색|나타나)|"
    r"(?:이름|원문 표기).{0,16}(?:확인|기록|되어 있|포함|등장)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    blocking: bool = True


def validate_carousel_readability(draft: ContentDraft) -> tuple[QualityIssue, ...]:
    """Minimum review gate: carousel structure and copy that fits its card layout."""
    issues: list[QualityIssue] = []
    if not MIN_CARDS <= len(draft.cards) <= MAX_CARDS:
        issues.append(QualityIssue("CAROUSEL_CARD_COUNT", "Carousel must contain 3 to 10 pages."))
    if draft.cards:
        if draft.cards[0].role != "cover":
            issues.append(QualityIssue("CAROUSEL_COVER_MISSING", "The first page must be the cover."))
        if draft.cards[-1].role != "ending":
            issues.append(QualityIssue("CAROUSEL_ENDING_MISSING", "The last page must be the ending."))
    for page, card in enumerate(draft.cards, start=1):
        if not card.headline.strip() or len(card.headline) > MAX_CARD_HEADLINE_CHARS:
            issues.append(QualityIssue(
                "CARD_HEADLINE_UNREADABLE",
                f"Page {page} headline must be non-empty and at most {MAX_CARD_HEADLINE_CHARS} characters.",
            ))
        if not card.body.strip() or len(card.body) > MAX_CARD_BODY_CHARS:
            issues.append(QualityIssue(
                "CARD_BODY_UNREADABLE",
                f"Page {page} body must be non-empty and at most {MAX_CARD_BODY_CHARS} characters.",
            ))
    reader_copy = "\n".join(
        [draft.detail_body]
        + [part for card in draft.cards for part in (card.headline, card.body)]
    )
    if _SOURCE_META_COPY.search(reader_copy):
        issues.append(QualityIssue(
            "SOURCE_METADATA_AS_COPY",
            "Reader-facing copy describes source lookup or metadata instead of the subject; rewrite it as useful content.",
        ))
    return tuple(issues)


def validate_draft(
    draft: ContentDraft,
    evidence: dict[str, EvidenceDocument],
    images: dict[str, ImageAsset],
    retrieved_chunks: dict[str, EvidenceChunk],
    retrieval_id: str | None = None,
) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    if retrieval_id is not None and draft.retrieval_id != retrieval_id:
        issues.append(QualityIssue("RETRIEVAL_ID_MISMATCH", "Draft must retain the retrieval run that supplied its evidence."))
    if len(draft.cards) < MIN_CARDS:
        issues.append(QualityIssue("CAROUSEL_TOO_SHORT", f"At least {MIN_CARDS} cards are required."))
    if len({card.card_id for card in draft.cards}) != len(draft.cards):
        issues.append(QualityIssue("DUPLICATE_CARD_ID", "Card IDs must be unique."))
    image_ids = [card.image_asset_id for card in draft.cards]
    if len(set(image_ids)) != len(image_ids):
        issues.append(QualityIssue("IMAGE_REUSED", "A carousel cannot reuse the same image asset."))
    if not draft.source_footer.strip() or not draft.ai_disclosure.strip():
        issues.append(QualityIssue("SOURCE_OR_AI_FOOTER_MISSING", "Detail output needs source attribution and AI disclosure."))

    required_ids = set(draft.evidence_ids)
    all_claims = [claim for card in draft.cards for claim in card.claims] + list(draft.detail_claims)
    for card in draft.cards:
        if not card.claims:
            issues.append(QualityIssue("CARD_CLAIMS_UNSTRUCTURED", f"Card {card.card_id!r} has no claim-to-evidence links."))
    if not draft.detail_claims:
        issues.append(QualityIssue("DETAIL_CLAIMS_UNSTRUCTURED", "Detail article has no claim-to-evidence links."))
    for chunk_id in draft.retrieved_chunk_ids:
        chunk = retrieved_chunks.get(chunk_id)
        if chunk is None:
            issues.append(QualityIssue("RETRIEVED_CHUNK_MISSING", f"Retrieved chunk {chunk_id!r} is not available."))
        elif chunk.evidence_id not in required_ids:
            issues.append(QualityIssue("RETRIEVED_CHUNK_EVIDENCE_UNATTACHED", f"Chunk {chunk_id!r} belongs to evidence outside the draft."))
        else:
            source = evidence.get(chunk.evidence_id)
            source_text = None
            source_locator = None
            if source and chunk.source_kind == "body":
                source_text = source.body
                source_locator = "body"
            elif source and chunk.source_kind == "excerpt" and chunk.source_index is not None and 0 <= chunk.source_index < len(source.excerpts):
                source_text = source.excerpts[chunk.source_index].text
                source_locator = source.excerpts[chunk.source_index].locator or f"excerpt:{chunk.source_index}"
            if (source is None or source_text is None or chunk.content_type is not source.content_type
                    or chunk.source_id != source.source_id or chunk.source_url != source.source_url
                    or chunk.title != source.title or chunk.language != source.language
                    or chunk.published_at != source.published_at or chunk.menu_work_ids != source.menu_work_ids
                    or chunk.locator != f"{source_locator}:{chunk.start_offset}-{chunk.end_offset}"
                    or source_text[chunk.start_offset:chunk.end_offset].strip() != chunk.text):
                issues.append(QualityIssue("RETRIEVED_CHUNK_ORIGIN_MISMATCH", f"Chunk {chunk_id!r} does not match its preserved source document."))
    for claim in all_claims:
        linked_evidence_ids: set[str] = set()
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence:
                issues.append(QualityIssue("CLAIM_EVIDENCE_MISSING", f"Claim references unknown evidence {evidence_id!r}."))
            elif evidence_id not in required_ids:
                issues.append(QualityIssue("CLAIM_EVIDENCE_NOT_ATTACHED", f"Evidence {evidence_id!r} is not attached to this draft."))
        for chunk_id in claim.evidence_chunk_ids:
            chunk = retrieved_chunks.get(chunk_id)
            if chunk is None:
                issues.append(QualityIssue("CLAIM_CHUNK_MISSING", f"Claim references unknown retrieved chunk {chunk_id!r}."))
            elif chunk.evidence_id not in claim.evidence_ids:
                issues.append(QualityIssue("CLAIM_CHUNK_SOURCE_MISMATCH", f"Chunk {chunk_id!r} does not belong to the claim's cited evidence."))
            else:
                linked_evidence_ids.add(chunk.evidence_id)
        if not set(claim.evidence_ids) <= linked_evidence_ids:
            issues.append(QualityIssue("CLAIM_SOURCE_WITHOUT_CHUNK", "Each cited evidence document needs a linked supporting retrieval chunk."))
    if not all_claims and (draft.title or draft.detail_body):
        issues.append(QualityIssue("CLAIMS_NOT_STRUCTURED", "All factual copy must be represented as evidence-linked claims."))

    for evidence_id in required_ids:
        if evidence_id not in evidence:
            issues.append(QualityIssue("DRAFT_EVIDENCE_MISSING", f"Draft evidence {evidence_id!r} is not available."))
    required_source_urls = {
        evidence[evidence_id].source_url for evidence_id in required_ids if evidence_id in evidence
    }
    required_image_urls = {
        url for image_id in image_ids if image_id in images
        for url in (images[image_id].source_url, images[image_id].rights_evidence_url)
        if url
    }
    if any(url not in draft.source_footer for url in required_source_urls | required_image_urls):
        issues.append(QualityIssue("SOURCE_FOOTER_INCOMPLETE", "Detail footer must include every source and image provenance URL."))
    resolved_work_ids = {
        work_id for evidence_id in required_ids if evidence_id in evidence
        for work_id in evidence[evidence_id].menu_work_ids
    }
    if draft.content_type in {RagContentType.CHARACTER, RagContentType.GUIDE} and not draft.menu_work_ids:
        issues.append(QualityIssue("DRAFT_WORK_LINK_MISSING", "Character and guide drafts need an explicit resolved work ID."))
    if set(draft.menu_work_ids) - resolved_work_ids:
        issues.append(QualityIssue("DRAFT_WORK_LINK_UNSUPPORTED", "Draft work IDs must come from resolved evidence links."))
    if draft.content_type in {RagContentType.CHARACTER, RagContentType.GUIDE}:
        for evidence_id in required_ids:
            item = evidence.get(evidence_id)
            if item and item.work_link_status is not WorkLinkStatus.MATCHED:
                issues.append(QualityIssue("WORK_ID_UNRESOLVED", f"{draft.content_type.value} evidence needs a confirmed work link."))
    for image_id in image_ids:
        asset = images.get(image_id)
        if asset is None:
            issues.append(QualityIssue("IMAGE_ASSET_MISSING", f"Image asset {image_id!r} is unavailable."))
        elif not asset.source_url or not asset.source_id or not asset.rights_basis or not asset.rights_evidence_url:
            issues.append(QualityIssue("IMAGE_PROVENANCE_INCOMPLETE", f"Image asset {image_id!r} lacks source or rights evidence."))
        elif any(urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname for url in (asset.source_url, asset.rights_evidence_url)):
            issues.append(QualityIssue("IMAGE_PROVENANCE_URL_INVALID", f"Image asset {image_id!r} has an invalid provenance URL."))
        elif asset.work_link_status != "matched" or not asset.menu_work_ids:
            issues.append(QualityIssue("IMAGE_WORK_LINK_UNRESOLVED", f"Image asset {image_id!r} has no confirmed work relation."))
        elif draft.menu_work_ids and not set(asset.menu_work_ids).intersection(draft.menu_work_ids):
            issues.append(QualityIssue("IMAGE_WORK_MISMATCH", f"Image asset {image_id!r} is not linked to the draft work."))
        card = next((item for item in draft.cards if item.image_asset_id == image_id), None)
        if card and (not card.image_rights_status or not card.image_credit):
            issues.append(QualityIssue("CARD_IMAGE_RIGHTS_FIELDS_MISSING", f"Card {card.card_id!r} needs ERD image-rights status and credit."))

    if draft.content_type is RagContentType.REVIEW:
        for evidence_id in required_ids:
            item = evidence.get(evidence_id)
            if item and item.evidence_type is EvidenceType.EDITORIAL_REVIEW:
                if not item.author_name or not item.published_at:
                    issues.append(QualityIssue("REVIEW_ATTRIBUTION_INCOMPLETE", "Review claims require an author and publication date."))
    if draft.content_type is RagContentType.CURATION:
        for evidence_id in required_ids:
            item = evidence.get(evidence_id)
            if item:
                context = item.curation_context
                if (context is None or item.published_at is None or context.window_start is None
                        or context.window_end is None or context.sample_size is None
                        or not context.aggregation_method):
                    issues.append(QualityIssue("CURATION_CONTEXT_INCOMPLETE", "Curation needs its date window, sample size, aggregation method, and publication date."))
    if draft.content_type is RagContentType.HUMOR:
        # Humorous community signals are not eligible to support factual statements.
        for claim in all_claims:
            for evidence_id in claim.evidence_ids:
                item = evidence.get(evidence_id)
                if item and item.evidence_type is EvidenceType.COMMUNITY_SIGNAL and not claim.attribution:
                    issues.append(QualityIssue("COMMUNITY_SIGNAL_UNATTRIBUTED", "Community signals require explicit attribution."))
                if item and item.evidence_type is EvidenceType.COMMUNITY_SIGNAL and not item.metadata.get("community_or_publisher"):
                    issues.append(QualityIssue("COMMUNITY_SOURCE_UNNAMED", "Community signals must identify the community or publisher."))
    return tuple(issues)
