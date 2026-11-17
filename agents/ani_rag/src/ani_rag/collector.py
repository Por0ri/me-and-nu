"""Single-source MCP collection orchestration; the adapter is injected by a host app."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Mapping

from ani_rag.adapter import MCPReadSearchAdapter, ReadRequest, SearchRequest, canonical_source_url
from ani_rag.catalog import WorkSearchSeed
from ani_rag.interfaces import EvidenceRepository, StoredEvidenceReference, WorkCatalogResolver, WorkIdentity
from ani_rag.image_ingest import ImageAssetRepository, ImageCandidate, ingest_image_candidates
from ani_rag.entity_linking import EntityCatalogRecord, attach_image_entity_matches
from ani_rag.models import (
    CollectionMethod,
    EvidenceDocument,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)
from ani_rag.profiles import get_content_profile, validate_document_profile
from ani_rag.settings import EvergreenSourceRegistry, require_source_access, require_source_media_url, require_source_url


@dataclass(frozen=True)
class CollectionRequest:
    source_id: str
    content_type: RagContentType
    evidence_type: EvidenceType
    query: str
    language: str
    result_limit: int = 10
    seed_menu_work_id: int | None = None
    generation_query: str | None = None

    def __post_init__(self) -> None:
        if not self.query.strip() or not self.language.strip():
            raise ValueError("Collection requires a bounded query and original language")
        if not 1 <= self.result_limit <= 100:
            raise ValueError("result_limit must be between 1 and 100")
        if self.seed_menu_work_id is not None and self.seed_menu_work_id <= 0:
            raise ValueError("seed_menu_work_id must be a positive integer")
        if self.generation_query is not None and not self.generation_query.strip():
            raise ValueError("generation_query must be non-empty when supplied")
        if self.evidence_type not in get_content_profile(self.content_type).accepted_evidence_types:
            raise ValueError("Evidence type is not permitted for this content type")

    @classmethod
    def for_work_seed(
        cls, *, source_id: str, content_type: RagContentType, evidence_type: EvidenceType,
        language: str, seed: WorkSearchSeed, result_limit: int = 10,
        query_override: str | None = None,
        generation_query: str | None = None,
    ) -> "CollectionRequest":
        """Build one bounded request; query_override keeps source plans on a stable title."""
        return cls(
            source_id=source_id, content_type=content_type, evidence_type=evidence_type,
            query=query_override.strip() if query_override and query_override.strip() else seed.query,
            language=language, result_limit=result_limit,
            seed_menu_work_id=seed.menu_work_id, generation_query=generation_query,
        )


@dataclass(frozen=True)
class CollectionResult:
    source_id: str
    retrieved: int
    stored: tuple[StoredEvidenceReference, ...]
    stage_timings_ms: Mapping[str, int] = field(default_factory=dict)
    image_candidates: int = 0
    images_downloaded: int = 0
    images_reused: int = 0
    image_failures: int = 0
    duplicate_documents: int = 0


async def collect_from_source(
    request: CollectionRequest,
    *,
    registry: EvergreenSourceRegistry,
    adapter: MCPReadSearchAdapter,
    repository: EvidenceRepository,
    work_catalog: WorkCatalogResolver | None = None,
    identities_by_url: dict[str, WorkIdentity] | None = None,
    image_repository: ImageAssetRepository | None = None,
    image_asset_dir: Path | None = None,
    image_entity_catalog: tuple[EntityCatalogRecord, ...] = (),
) -> CollectionResult:
    """Search/read one approved source and store evidence; no broad web fallback exists."""
    collection_started = perf_counter()
    stage_timings_ms: dict[str, int] = {}
    image_candidate_count = 0
    images_downloaded = 0
    images_reused = 0
    image_failure_count = 0
    source = require_source_access(registry, request.source_id, "search", request.content_type)
    require_source_access(registry, request.source_id, "read", request.content_type)
    if not source.allowed_hosts:
        raise PermissionError(f"Source {source.source_id!r} has no allowed hosts")
    if not request.language.strip():
        raise ValueError("Original language must be supplied by the source adapter/operator")
    stage_started = perf_counter()
    live_tools = await adapter.list_tools(source.mcp_server)
    stage_timings_ms["tool_inventory"] = round((perf_counter() - stage_started) * 1000)
    bindings = dict(source.tool_bindings)
    required_tool_names = {bindings["search"], bindings["read"]}
    if not required_tool_names <= live_tools:
        raise PermissionError("Configured MCP tool names do not match the live server tool inventory")
    stage_started = perf_counter()
    hits = await adapter.search(SearchRequest(
        source_id=source.source_id,
        query=request.query,
        mcp_server_alias=source.mcp_server,
        tool_name=bindings["search"],
        allowed_hosts=source.allowed_hosts,
        limit=request.result_limit,
    ))
    stage_timings_ms["search"] = round((perf_counter() - stage_started) * 1000)
    if len(hits) > request.result_limit:
        raise ValueError("MCP search returned more results than the configured limit")
    stored: list[StoredEvidenceReference] = []
    read_ms = 0
    work_match_ms = 0
    image_ingest_ms = 0
    persistence_ms = 0
    for hit in hits:
        entity_linking_summary: dict[str, int] = {}
        if hit.source_id != source.source_id:
            raise PermissionError("MCP search returned a result attributed to another source")
        if hit.mcp_server_alias != source.mcp_server or hit.tool_name != bindings["search"]:
            raise PermissionError("MCP search result attestation does not match the configured server/tool")
        require_source_url(source, hit.url)
        stage_started = perf_counter()
        result = await adapter.read(ReadRequest(
            source_id=source.source_id,
            url=hit.url,
            mcp_server_alias=source.mcp_server,
            tool_name=bindings["read"],
            allowed_hosts=source.allowed_hosts,
            allowed_path_prefixes=source.allowed_path_prefixes,
        ))
        read_ms += round((perf_counter() - stage_started) * 1000)
        if result.source_id != source.source_id:
            raise PermissionError("MCP read returned a document attributed to another source")
        if result.mcp_server_alias != source.mcp_server or result.tool_name != bindings["read"]:
            raise PermissionError("MCP read result attestation does not match the configured server/tool")
        if not result.redirects_verified:
            raise PermissionError("MCP adapter did not attest in-scope redirect enforcement")
        for redirect_url in result.redirect_chain:
            require_source_url(source, redirect_url)
        require_source_url(source, result.url)
        if result.url != hit.url and (not result.redirect_chain or result.redirect_chain[-1] != result.url):
            raise PermissionError("MCP read final URL is not linked to the searched URL by its redirect chain")
        excerpts = result.excerpts
        retrieved_at = datetime.now(timezone.utc)
        content = result.body or "\n".join(item.text for item in excerpts)
        digest = hashlib.sha256(f"{source.source_id}\n{canonical_source_url(result.url)}\n{content}".encode("utf-8")).hexdigest()
        identity = (identities_by_url or {}).get(result.url)
        if identity is None and work_catalog is not None:
            identity = WorkIdentity(
                titles=tuple(dict.fromkeys(value for value in (result.title, hit.title) if value.strip())),
                external_ids=({source.source_id: result.source_native_id or hit.source_native_id}
                              if result.source_native_id or hit.source_native_id else {}),
                source_text=content, source_url=result.url, source_id=source.source_id,
                seed_menu_work_ids=(request.seed_menu_work_id,) if request.seed_menu_work_id else (),
            )
        elif identity is not None:
            identity = WorkIdentity(
                titles=identity.titles, external_ids=identity.external_ids,
                source_text=identity.source_text or content,
                source_url=identity.source_url or result.url,
                source_id=identity.source_id or source.source_id,
                seed_menu_work_ids=tuple(dict.fromkeys((
                    *identity.seed_menu_work_ids,
                    *((request.seed_menu_work_id,) if request.seed_menu_work_id else ()),
                ))),
            )
        identity_error = None
        stage_started = perf_counter()
        try:
            resolution = await work_catalog.resolve(identity) if work_catalog and identity else None
        except Exception as error:
            resolution = None
            identity_error = type(error).__name__
        work_match_ms += round((perf_counter() - stage_started) * 1000)
        document = EvidenceDocument(
            document_id=f"ev_{digest[:32]}",
            content_type=request.content_type,
            title=result.title,
            source_url=result.url,
            source_id=source.source_id,
            retrieved_at=retrieved_at,
            published_at=result.published_at or hit.published_at,
            evidence_type=request.evidence_type,
            collection_method=CollectionMethod.MCP_READ,
            language=result.language or request.language,
            source_native_id=result.source_native_id or hit.source_native_id,
            publisher=result.publisher,
            body=result.body,
            raw_payload=result.raw_payload or {},
            excerpts=excerpts,
            work_link_status=resolution.status if resolution else WorkLinkStatus.UNRESOLVED,
            menu_work_ids=resolution.menu_work_ids if resolution else (),
            candidate_menu_work_ids=resolution.candidate_menu_work_ids if resolution else (),
            new_work_registration=resolution.new_work if resolution else None,
            metadata={
                "mcp_server_alias": source.mcp_server,
                "mcp_tool": "read",
                **({"work_match_method": resolution.matched_on} if resolution and resolution.matched_on else {}),
                **({"work_entry_format": resolution.entry_format.value} if resolution and resolution.entry_format is not None else {}),
                **({"work_entry_relation": resolution.entry_relation.value} if resolution and resolution.entry_relation is not None else {}),
                **({"work_entry_year": str(resolution.entry_year)} if resolution and resolution.entry_year is not None else {}),
                **({"work_relation_evidence_quote": resolution.relation_evidence_quote}
                   if resolution and resolution.relation_evidence_quote else {}),
                **({"work_match_seed_menu_work_id": str(request.seed_menu_work_id)}
                   if request.seed_menu_work_id is not None else {}),
                **({"work_identity_error": identity_error} if identity_error else {}),
            },
        )
        validate_document_profile(document)
        if resolution and resolution.status is WorkLinkStatus.MATCHED:
            image_rows = document.raw_payload.get("image_candidates", [])
            if isinstance(image_rows, list):
                candidates: list[ImageCandidate] = []
                for image in image_rows:
                    url = image if isinstance(image, str) else image.get("url") if isinstance(image, dict) else None
                    if not isinstance(url, str):
                        continue
                    try:
                        require_source_media_url(source, url)
                    except PermissionError:
                        continue
                    image_candidate_count += 1
                    role = image.get("role", "source_page_image") if isinstance(image, dict) else "source_page_image"
                    image_native_id = image.get("source_native_id") if isinstance(image, dict) else None
                    alt_text = image.get("alt_text") if isinstance(image, dict) else None
                    for work_id in resolution.menu_work_ids:
                        candidates.append(ImageCandidate(
                            source_id=source.source_id, source_url=url, source_page_url=result.url,
                            source_native_id=str(image_native_id or result.source_native_id or hit.source_native_id or "unknown"),
                            menu_work_id=work_id, image_role=str(role),
                            alt_text=str(alt_text) if alt_text is not None else result.title,
                        ))
                if candidates:
                    if image_entity_catalog:
                        candidates, match_results = attach_image_entity_matches(
                            tuple(candidates), document, image_entity_catalog,
                        )
                        for outcome in match_results:
                            entity_linking_summary[outcome.status] = entity_linking_summary.get(outcome.status, 0) + 1
                    if image_repository is None or image_asset_dir is None:
                        registered = 0
                        image_failure_count += len(candidates)
                        failures = [{"source_url": item.source_url, "error_type": "image_storage_unconfigured"}
                                    for item in candidates]
                    else:
                        stage_started = perf_counter()
                        ingest_result = await ingest_image_candidates(
                            tuple(candidates), registry=registry, repository=image_repository,
                            destination_dir=image_asset_dir,
                            interval_seconds=1.0 if source.source_id == "kitsu_anime_catalog" else 0.25,
                        )
                        image_ingest_ms += round((perf_counter() - stage_started) * 1000)
                        registered = len(ingest_result.assets)
                        images_reused += ingest_result.reused_count
                        images_downloaded += len(ingest_result.assets) - ingest_result.reused_count
                        image_failure_count += len(ingest_result.failures)
                        failures = [{"source_url": item.source_url, "error_type": item.error_type}
                                    for item in ingest_result.failures]
                    document = replace(document, raw_payload={
                        **document.raw_payload,
                        **({"image_entity_linking": entity_linking_summary} if entity_linking_summary else {}),
                        "image_ingest": {
                            "registered": registered,
                            "failed": failures,
                        },
                    })
        stage_started = perf_counter()
        stored.append(await repository.upsert_and_schedule_generation(
            document, retrieval_query=request.generation_query or request.query,
        ))
        persistence_ms += round((perf_counter() - stage_started) * 1000)
    stage_timings_ms.update({
        "read": read_ms,
        "work_match": work_match_ms,
        "image_ingest": image_ingest_ms,
        "persistence": persistence_ms,
        "total": round((perf_counter() - collection_started) * 1000),
    })
    return CollectionResult(
        source_id=source.source_id, retrieved=len(hits), stored=tuple(stored),
        stage_timings_ms=stage_timings_ms,
        image_candidates=image_candidate_count,
        images_downloaded=images_downloaded,
        images_reused=images_reused,
        image_failures=image_failure_count,
        duplicate_documents=sum(item.disposition == "duplicate" for item in stored),
    )
