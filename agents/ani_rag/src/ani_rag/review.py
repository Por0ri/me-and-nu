"""Independent review result and queue routing; no generation/reviewer graph is coupled."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ani_rag.content import ContentDraft, ImageAsset
from ani_rag.models import EvidenceDocument
from ani_rag.quality import QualityIssue
from ani_rag.retrieval import EvidenceChunk

# Allow three requested regeneration rounds after the initial draft. A further
# revise decision archives the draft; this cap does not lower review standards.
DEFAULT_MAX_REVISIONS = 3


class ReviewDecision(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    REQUEST_EVIDENCE = "request_evidence"
    REJECT_ARCHIVE = "reject_archive"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class ReviewResult:
    draft_id: str
    decision: ReviewDecision
    issues: tuple[QualityIssue, ...]
    revision_instructions: tuple[str, ...] = ()
    evidence_requests: tuple[str, ...] = ()
    reviewer_id: str = ""
    scorecard: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.draft_id.strip() or not self.reviewer_id.strip():
            raise ValueError("Review must identify its draft and independent reviewer")
        if self.decision is ReviewDecision.REVISE and not self.revision_instructions:
            raise ValueError("Revision decisions need concrete instructions")
        if self.decision is ReviewDecision.REQUEST_EVIDENCE and not self.evidence_requests:
            raise ValueError("Evidence requests must identify the missing support")
        if self.decision is ReviewDecision.PASS and any(issue.blocking for issue in self.issues):
            raise ValueError("A passing review cannot contain blocking issues")
        if self.decision is ReviewDecision.REJECT_ARCHIVE and not self.issues:
            raise ValueError("An archived rejection must record at least one reason")
        if self.decision is ReviewDecision.HUMAN_REVIEW and not any(
            issue.blocking for issue in self.issues
        ):
            raise ValueError("Human review decisions need a blocking issue")


class IndependentReviewer(Protocol):
    async def review(self, review_input: "ReviewInput") -> ReviewResult:
        """Review a draft independently; never generate or auto-approve it."""


@dataclass(frozen=True)
class ReviewInput:
    draft: ContentDraft
    evidence: dict[str, EvidenceDocument]
    images: dict[str, ImageAsset]
    retrieved_chunks: dict[str, EvidenceChunk]
    rendered_cards: dict[str, bytes] = field(default_factory=dict)


class ReviewRoute(StrEnum):
    READY = "ready_for_downstream"
    GENERATION_QUEUE = "generation_queue"
    COLLECTION_QUEUE = "collection_queue"
    HUMAN_REVIEW = "awaiting_manual_visual_review"
    ARCHIVE = "rejected_archive"


def route_review(result: ReviewResult, revision_count: int, max_revisions: int = DEFAULT_MAX_REVISIONS) -> ReviewRoute:
    if revision_count < 0 or max_revisions < 0:
        raise ValueError("Revision counters cannot be negative")
    if result.decision is ReviewDecision.PASS:
        return ReviewRoute.READY
    if result.decision is ReviewDecision.REQUEST_EVIDENCE:
        return ReviewRoute.COLLECTION_QUEUE
    if result.decision is ReviewDecision.HUMAN_REVIEW:
        return ReviewRoute.HUMAN_REVIEW
    if result.decision is ReviewDecision.REVISE:
        return ReviewRoute.ARCHIVE if revision_count >= max_revisions else ReviewRoute.GENERATION_QUEUE
    return ReviewRoute.ARCHIVE
