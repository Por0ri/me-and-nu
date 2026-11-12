"""RAG lane responsibilities and evidence handling rules for the seven content types."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ani_rag.models import EvidenceDocument, EvidenceType, RagContentType


@dataclass(frozen=True)
class ContentProfile:
    content_type: RagContentType
    purpose: str
    accepted_evidence_types: tuple[EvidenceType, ...]
    work_identity_priority: str
    preserve_context: tuple[str, ...]
    handling_rule: str


CONTENT_PROFILES: Mapping[RagContentType, ContentProfile] = MappingProxyType(
    {
        RagContentType.CHARACTER: ContentProfile(
            content_type=RagContentType.CHARACTER,
            purpose="Character identity, profile, birthday, relationships, and sourced trivia.",
            accepted_evidence_types=(EvidenceType.OFFICIAL_FACT, EvidenceType.REFERENCE_FACT),
            work_identity_priority="required_before_promotion",
            preserve_context=("character_name", "work_identity", "source_locator"),
            handling_rule="Resolve the character and work separately; keep unresolved links unresolved.",
        ),
        RagContentType.GUIDE: ContentProfile(
            content_type=RagContentType.GUIDE,
            purpose="Work introductions, episodes/arcs, world details, terminology, and viewing order.",
            accepted_evidence_types=(
                EvidenceType.OFFICIAL_FACT,
                EvidenceType.REFERENCE_FACT,
                EvidenceType.EDITORIAL_CLAIM,
            ),
            work_identity_priority="required_before_promotion",
            preserve_context=("work_identity", "episode_or_arc", "source_locator"),
            handling_rule="Keep claims tied to the work and the original section or passage.",
        ),
        RagContentType.PEOPLE: ContentProfile(
            content_type=RagContentType.PEOPLE,
            purpose="Cast, staff, creators, studios, and work-specific artist credits.",
            accepted_evidence_types=(
                EvidenceType.OFFICIAL_FACT,
                EvidenceType.REFERENCE_FACT,
                EvidenceType.EDITORIAL_CLAIM,
            ),
            work_identity_priority="preferred",
            preserve_context=("person_name", "role", "work_credit", "published_at", "valid_from"),
            handling_rule="Distinguish a person's general profile from a credit for a specific work.",
        ),
        RagContentType.MUSIC: ContentProfile(
            content_type=RagContentType.MUSIC,
            purpose="OST/BGM, theme songs, tracks, albums, artists, and release information.",
            accepted_evidence_types=(
                EvidenceType.OFFICIAL_FACT,
                EvidenceType.REFERENCE_FACT,
                EvidenceType.EDITORIAL_CLAIM,
            ),
            work_identity_priority="preferred",
            preserve_context=("work_identity", "track", "artist", "album", "published_at", "valid_from"),
            handling_rule="Keep work, song, artist, and album identities distinct; retain release dates.",
        ),
        RagContentType.HUMOR: ContentProfile(
            content_type=RagContentType.HUMOR,
            purpose="Community humor signals and source-backed production behind-scenes material.",
            accepted_evidence_types=(
                EvidenceType.COMMUNITY_SIGNAL,
                EvidenceType.PRODUCTION_BEHIND_SCENES,
            ),
            work_identity_priority="contextual",
            preserve_context=("humor_subtype", "community_or_publisher", "source_locator"),
            handling_rule="Never promote community opinion or a trend signal as an established fact.",
        ),
        RagContentType.CURATION: ContentProfile(
            content_type=RagContentType.CURATION,
            purpose="Polls, rankings, recommendations, and editorial selection sets.",
            accepted_evidence_types=(EvidenceType.CURATION_RESULT, EvidenceType.REFERENCE_FACT),
            work_identity_priority="contextual",
            preserve_context=(
                "survey_window",
                "sample_size",
                "aggregation_method",
                "publisher_context",
            ),
            handling_rule=(
                "Use reference facts as factual support for editorial groupings; use curation results only with "
                "their sample and method context, and never present an editorial selection as an objective ranking."
            ),
        ),
        RagContentType.REVIEW: ContentProfile(
            content_type=RagContentType.REVIEW,
            purpose="Attributed criticism, interpretation, and comparative reviews.",
            accepted_evidence_types=(EvidenceType.EDITORIAL_REVIEW,),
            work_identity_priority="preferred",
            preserve_context=("author_name", "claims", "published_at", "claim_supporting_excerpts"),
            handling_rule="Keep reviewer claims attributed and separate from verified factual records.",
        ),
    }
)


def get_content_profile(content_type: RagContentType | str) -> ContentProfile:
    """Return the policy profile for a RAG-owned content type."""
    try:
        key = RagContentType(content_type)
    except ValueError as error:
        raise ValueError(f"Unsupported RAG content type: {content_type!r}") from error
    return CONTENT_PROFILES[key]


def validate_document_profile(document: EvidenceDocument) -> None:
    """Check the evidence kind is allowed for this profile; perform no promotion."""
    profile = get_content_profile(document.content_type)
    if document.evidence_type not in profile.accepted_evidence_types:
        raise ValueError(
            f"Evidence type {document.evidence_type.value!r} is not allowed for "
            f"content type {document.content_type.value!r}"
        )
