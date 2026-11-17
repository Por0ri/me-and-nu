"""Evidence-backed resolution of source images to authoritative ERD entities.

The matcher never mints entity IDs. A host must inject records from the
authoritative entity catalog; unresolved and ambiguous names remain unlinked.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from ani_rag.content import ImageEntityLink
from ani_rag.image_ingest import ImageCandidate
from ani_rag.models import EvidenceDocument
from ani_rag.retrieval import chunk_document


@dataclass(frozen=True)
class EntityCatalogRecord:
    entity_id: int
    entity_type: str
    name: str
    aliases: tuple[str, ...] = ()
    external_ids: Mapping[str, str] | None = None
    menu_work_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.entity_id <= 0 or not self.entity_type.strip() or not self.name.strip():
            raise ValueError("Entity catalog records need an authoritative ID, type, and name")
        if any(work_id <= 0 for work_id in self.menu_work_ids):
            raise ValueError("Entity catalog work IDs must be positive")


@dataclass(frozen=True)
class ImageEntityMatch:
    status: str
    links: tuple[ImageEntityLink, ...] = ()
    candidate_entity_ids: tuple[int, ...] = ()
    reason: str | None = None


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def match_image_to_entity(
    candidate: ImageCandidate,
    document: EvidenceDocument,
    catalog: Sequence[EntityCatalogRecord],
) -> ImageEntityMatch:
    """Resolve one image only when source identity or a unique work-scoped name agrees."""
    return _match_image_to_entity(candidate, document, catalog, chunk_document(document))


def attach_image_entity_matches(
    candidates: Sequence[ImageCandidate],
    document: EvidenceDocument,
    catalog: Sequence[EntityCatalogRecord],
) -> tuple[tuple[ImageCandidate, ...], tuple[ImageEntityMatch, ...]]:
    """Attach only confirmed links and return explicit unresolved/ambiguous outcomes."""
    chunks = chunk_document(document)
    enriched: list[ImageCandidate] = []
    outcomes: list[ImageEntityMatch] = []
    for candidate in candidates:
        result = _match_image_to_entity(candidate, document, catalog, chunks)
        outcomes.append(result)
        if result.status == "matched":
            existing = {(link.entity_id, link.relation_type): link for link in candidate.entity_links}
            existing.update({(link.entity_id, link.relation_type): link for link in result.links})
            enriched.append(replace(candidate, entity_links=tuple(existing.values())))
        else:
            enriched.append(candidate)
    return tuple(enriched), tuple(outcomes)


def _match_image_to_entity(candidate, document, catalog, chunks) -> ImageEntityMatch:
    """Internal pre-chunked variant used to avoid re-chunking long source pages."""
    if not candidate.image_role.startswith("character"):
        return ImageEntityMatch("not_applicable", reason="image_is_not_a_character_asset")
    if candidate.source_page_url != document.source_url:
        return ImageEntityMatch("unresolved", reason="image_page_does_not_match_evidence")
    if candidate.menu_work_id not in document.menu_work_ids:
        return ImageEntityMatch("unresolved", reason="evidence_has_no_matching_work_link")
    source_id = candidate.source_id
    external_matches = [
        row for row in catalog
        if row.entity_type.casefold() == "character" and row.external_ids
        and row.external_ids.get(source_id) == candidate.source_native_id
        and candidate.menu_work_id in row.menu_work_ids
    ]
    name = _normalize(candidate.alt_text or "")
    name_matches = [
        row for row in catalog
        if name and row.entity_type.casefold() == "character"
        and name in {_normalize(row.name), *(_normalize(alias) for alias in row.aliases)}
        and candidate.menu_work_id in row.menu_work_ids
    ]
    matches = external_matches if external_matches else name_matches
    if len(matches) != 1:
        ids = tuple(sorted({row.entity_id for row in matches}))
        return ImageEntityMatch(
            "ambiguous" if matches else "unresolved", candidate_entity_ids=ids,
            reason="entity_identity_not_unique" if matches else "entity_identity_not_in_catalog",
        )
    entity = matches[0]
    source_native = candidate.source_native_id.strip()
    matching_chunks = [
        chunk for chunk in chunks
        if (source_native and source_native in chunk.locator)
        or _chunk_has_entity_name(chunk.text, entity)
    ]
    if not matching_chunks:
        return ImageEntityMatch("unresolved", reason="source_chunk_does_not_support_image_identity")
    link = ImageEntityLink(
        entity_id=entity.entity_id, relation_type="depicts",
        source_document_id=document.document_id,
        source_chunk_id=matching_chunks[0].chunk_id,
        entity_name=entity.name,
    )
    return ImageEntityMatch("matched", (link,), (entity.entity_id,))


def _chunk_has_entity_name(text: str, entity: EntityCatalogRecord) -> bool:
    normalized = _normalize(text)
    return any(
        name and name in normalized
        for name in (_normalize(entity.name), *(_normalize(alias) for alias in entity.aliases))
    )
