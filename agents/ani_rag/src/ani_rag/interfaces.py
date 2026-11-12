"""Pure interfaces for work matching and evidence persistence, with no DB implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from ani_rag.models import (
    EvidenceDocument, WorkEntryFormat, WorkEntryRelation, WorkLinkStatus, WorkRegistration,
)


@dataclass(frozen=True)
class WorkIdentity:
    """Source identity and original text used to resolve an existing or new work."""

    titles: tuple[str, ...]
    external_ids: Mapping[str, str]
    source_text: str = ""
    source_url: str = ""
    source_id: str = ""
    seed_menu_work_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if any(work_id <= 0 for work_id in self.seed_menu_work_ids):
            raise ValueError("Seed menu_work_ids must be positive integers")
        if len(set(self.seed_menu_work_ids)) != len(self.seed_menu_work_ids):
            raise ValueError("Seed menu_work_ids must be unique")


@dataclass(frozen=True)
class WorkResolution:
    """Resolution result aligned with ani_rag's local menu_work_id snapshot."""

    status: WorkLinkStatus
    menu_work_ids: tuple[int, ...] = ()
    candidate_menu_work_ids: tuple[int, ...] = ()
    matched_on: str | None = None
    new_work: WorkRegistration | None = None
    entry_format: WorkEntryFormat = WorkEntryFormat.UNKNOWN
    entry_relation: WorkEntryRelation = WorkEntryRelation.UNKNOWN
    entry_year: int | None = None
    relation_evidence_quote: str | None = None

    def __post_init__(self) -> None:
        if self.status is WorkLinkStatus.MATCHED:
            if not self.menu_work_ids or self.candidate_menu_work_ids:
                raise ValueError("Matched resolution needs resolved IDs only")
        elif self.status is WorkLinkStatus.AMBIGUOUS:
            if not self.candidate_menu_work_ids or self.menu_work_ids:
                raise ValueError("Ambiguous resolution needs candidate IDs only")
        elif self.menu_work_ids or self.candidate_menu_work_ids:
            raise ValueError("Unresolved resolutions cannot carry work IDs")
        if self.new_work is not None and self.status is not WorkLinkStatus.UNRESOLVED:
            raise ValueError("A proposed new work must remain unresolved until storage assigns its ID")
        if any(work_id <= 0 for work_id in (*self.menu_work_ids, *self.candidate_menu_work_ids)):
            raise ValueError("Work IDs must be positive integers")
        if self.entry_year is not None and not 1900 <= self.entry_year <= 2100:
            raise ValueError("entry_year must be between 1900 and 2100")
        if self.relation_evidence_quote is not None and not self.relation_evidence_quote.strip():
            raise ValueError("relation_evidence_quote cannot be blank")


class WorkCatalogResolver(Protocol):
    """Resolve against the local snapshot, escalating misses to source-bound identification."""

    async def resolve(self, identity: WorkIdentity) -> WorkResolution:
        """Return an existing match, supported new-work candidate, or unresolved result."""


@dataclass(frozen=True)
class StoredEvidenceReference:
    document_id: str
    canonical_record_id: str
    disposition: str = "inserted"

    def __post_init__(self) -> None:
        if self.disposition not in {"inserted", "duplicate"}:
            raise ValueError("Evidence disposition must be inserted or duplicate")


class EvidenceRepository(Protocol):
    """Persistence/outbox port; adapter must commit the document and pending generation row atomically."""

    async def upsert_and_schedule_generation(
        self, document: EvidenceDocument, *, retrieval_query: str | None = None,
        schedule_generation: bool = True,
    ) -> StoredEvidenceReference:
        """Upsert canonical evidence and optionally queue generation atomically."""
