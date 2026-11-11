"""Structured MCP contract for entity/property sources such as Wikidata.

This deliberately does not treat entity IDs or statement lookups as URL scraping.
No MCP transport or server-specific response parser is implemented here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit

from ani_rag.interfaces import EvidenceRepository, StoredEvidenceReference, WorkCatalogResolver, WorkIdentity
from ani_rag.models import (
    CollectionMethod,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)
from ani_rag.profiles import get_content_profile, validate_document_profile
from ani_rag.image_ingest import ImageAssetRepository, ImageCandidate, ingest_image_candidates
from ani_rag.entity_linking import EntityCatalogRecord, attach_image_entity_matches
from ani_rag.settings import EvergreenSourceRegistry, require_source_access, require_source_media_url, require_source_url


@dataclass(frozen=True)
class StructuredItemHit:
    source_id: str
    item_id: str
    title: str
    item_url: str
    language: str
    mcp_server_alias: str
    tool_name: str
    description: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_id, self.item_id, self.title, self.language,
                                                self.mcp_server_alias, self.tool_name)):
            raise ValueError("Structured search hit needs source, item ID, title, language, server and tool")


@dataclass(frozen=True)
class StatementValue:
    property_id: str
    value: object
    qualifiers: Mapping[str, object]
    references: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if not self.property_id.strip():
            raise ValueError("Structured statement must identify its property")


@dataclass(frozen=True)
class StatementValuesResult:
    source_id: str
    mcp_server_alias: str
    tool_name: str
    item_id: str
    values: tuple[StatementValue, ...]


@dataclass(frozen=True)
class StructuredSearchRequest:
    source_id: str
    mcp_server_alias: str
    tool_name: str
    query: str
    limit: int
    allowed_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_id, self.mcp_server_alias, self.tool_name, self.query)) or not self.allowed_hosts:
            raise ValueError("Structured search needs source, server/tool, query, and allowed hosts")
        if not 1 <= self.limit <= 100:
            raise ValueError("Structured search limit must be between 1 and 100")


@dataclass(frozen=True)
class StatementValuesRequest:
    source_id: str
    mcp_server_alias: str
    tool_name: str
    item_id: str
    property_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_id, self.mcp_server_alias, self.tool_name, self.item_id)) or not self.property_ids:
            raise ValueError("Statement read needs source, server/tool, item ID, and explicit properties")


class StructuredMCPAdapter(Protocol):
    async def list_tools(self, mcp_server_alias: str) -> frozenset[str]: ...
    async def search_items(self, request: StructuredSearchRequest) -> Sequence[StructuredItemHit]: ...
    async def get_statement_values(self, request: StatementValuesRequest) -> StatementValuesResult: ...


@dataclass(frozen=True)
class StructuredCollectionRequest:
    source_id: str
    content_type: RagContentType
    evidence_type: EvidenceType
    query: str
    language: str
    property_ids: tuple[str, ...]
    result_limit: int = 10

    def __post_init__(self) -> None:
        if not self.query.strip() or not self.language.strip() or not self.property_ids or any(not value.strip() for value in self.property_ids):
            raise ValueError("Structured collection needs query, original language, and explicit properties")
        if not 1 <= self.result_limit <= 100:
            raise ValueError("result_limit must be between 1 and 100")
        if self.evidence_type not in get_content_profile(self.content_type).accepted_evidence_types:
            raise ValueError("Evidence type is not permitted for this content type")


async def collect_structured_items(
    request: StructuredCollectionRequest,
    *, registry: EvergreenSourceRegistry,
    adapter: StructuredMCPAdapter,
    repository: EvidenceRepository,
    work_catalog: WorkCatalogResolver | None = None,
    identities_by_item: Mapping[str, WorkIdentity] | None = None,
    image_repository: ImageAssetRepository | None = None,
    image_asset_dir: Path | None = None,
    image_entity_catalog: tuple[EntityCatalogRecord, ...] = (),
) -> tuple[StoredEvidenceReference, ...]:
    """Search explicit structured entities and preserve statements, qualifiers, and references."""
    source = require_source_access(registry, request.source_id, "search", request.content_type)
    require_source_access(registry, request.source_id, "read", request.content_type)
    if not source.allowed_hosts:
        raise PermissionError(f"Source {source.source_id!r} has no allowed hosts")
    bindings = dict(source.tool_bindings)
    live_tools = await adapter.list_tools(source.mcp_server)
    names = {bindings["search"], bindings["read"]}
    if not names <= live_tools:
        raise PermissionError("Configured MCP tool names do not match the live server tool inventory")
    hits = await adapter.search_items(StructuredSearchRequest(
        source_id=source.source_id,
        mcp_server_alias=source.mcp_server,
        tool_name=bindings["search"],
        query=request.query,
        limit=request.result_limit,
        allowed_hosts=source.allowed_hosts,
    ))
    if len(hits) > request.result_limit:
        raise ValueError("Structured MCP search returned more results than the configured limit")
    stored: list[StoredEvidenceReference] = []
    for hit in hits:
        if hit.source_id != source.source_id:
            raise PermissionError("Structured MCP search returned a result attributed to another source")
        if hit.mcp_server_alias != source.mcp_server or hit.tool_name != bindings["search"]:
            raise PermissionError("Structured search result attestation does not match configured server/tool")
        require_source_url(source, hit.item_url)
        statement_result = await adapter.get_statement_values(StatementValuesRequest(
            source_id=source.source_id,
            mcp_server_alias=source.mcp_server,
            tool_name=bindings["read"],
            item_id=hit.item_id,
            property_ids=request.property_ids,
        ))
        if (statement_result.source_id != source.source_id
                or statement_result.mcp_server_alias != source.mcp_server
                or statement_result.tool_name != bindings["read"]
                or statement_result.item_id != hit.item_id):
            raise PermissionError("Structured statement result identity does not match its request")
        statements = statement_result.values
        requested_properties = set(request.property_ids)
        if any(item.property_id not in requested_properties for item in statements):
            raise PermissionError("Structured MCP returned a property that was not requested")
        if not statements:
            continue
        payload = {
            "item_id": hit.item_id,
            "title": hit.title,
            "description": hit.description,
            "statements": [
                {"property_id": item.property_id, "value": item.value,
                 "qualifiers": item.qualifiers, "references": item.references}
                for item in statements
            ],
        }
        payload["image_candidates"] = _wikidata_image_candidates(hit.item_id, statements)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        excerpts = tuple(EvidenceExcerpt(
            text=json.dumps({"property_id": item.property_id, "value": item.value,
                             "qualifiers": item.qualifiers, "references": item.references},
                            ensure_ascii=False, sort_keys=True),
            locator=item.property_id,
        ) for item in statements)
        identity = (identities_by_item or {}).get(hit.item_id)
        if identity is None and work_catalog is not None:
            identity = WorkIdentity(
                titles=(hit.title,), external_ids={source.source_id: hit.item_id},
                source_text="\n".join((hit.title, hit.description or "", body)),
                source_url=hit.item_url, source_id=source.source_id,
            )
        elif identity is not None:
            identity = WorkIdentity(
                titles=identity.titles, external_ids=identity.external_ids,
                source_text=identity.source_text or "\n".join((hit.title, hit.description or "", body)),
                source_url=identity.source_url or hit.item_url,
                source_id=identity.source_id or source.source_id,
            )
        identity_error = None
        try:
            resolution = await work_catalog.resolve(identity) if work_catalog and identity else None
        except Exception as error:
            resolution = None
            identity_error = type(error).__name__
        digest = hashlib.sha256(f"{source.source_id}\n{hit.item_id}\n{body}".encode("utf-8")).hexdigest()
        document = EvidenceDocument(
            document_id=f"ev_{digest[:32]}",
            content_type=request.content_type,
            title=hit.title,
            source_url=hit.item_url,
            source_id=source.source_id,
            source_native_id=hit.item_id,
            publisher=source.name,
            retrieved_at=datetime.now(timezone.utc),
            evidence_type=request.evidence_type,
            collection_method=CollectionMethod.MCP_READ,
            language=hit.language or request.language,
            body=body,
            raw_payload=payload,
            excerpts=excerpts,
            work_link_status=resolution.status if resolution else WorkLinkStatus.UNRESOLVED,
            menu_work_ids=resolution.menu_work_ids if resolution else (),
            candidate_menu_work_ids=resolution.candidate_menu_work_ids if resolution else (),
            new_work_registration=resolution.new_work if resolution else None,
            metadata={"mcp_server_alias": source.mcp_server,
                      "mcp_search_tool": bindings["search"],
                      "mcp_read_tool": bindings["read"],
                      "property_ids": ",".join(request.property_ids),
                      **({"work_match_method": resolution.matched_on} if resolution and resolution.matched_on else {}),
                      **({"work_identity_error": identity_error} if identity_error else {})},
        )
        validate_document_profile(document)
        image_rows = payload.get("image_candidates", [])
        if (resolution and resolution.status is WorkLinkStatus.MATCHED
                and isinstance(image_rows, list) and image_rows):
            entity_linking_summary: dict[str, int] = {}
            candidates: list[ImageCandidate] = []
            for image in image_rows:
                if not isinstance(image, dict) or not isinstance(image.get("url"), str):
                    continue
                try:
                    require_source_media_url(source, image["url"])
                except PermissionError:
                    continue
                for work_id in resolution.menu_work_ids:
                    candidates.append(ImageCandidate(
                        source_id=source.source_id, source_url=image["url"],
                        source_page_url=hit.item_url, source_native_id=hit.item_id,
                        menu_work_id=work_id, image_role=str(image.get("role", "wikidata_image")),
                        alt_text=str(image.get("alt_text", hit.title)),
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
                    failures = [{"source_url": item.source_url, "error_type": "image_storage_unconfigured"}
                                for item in candidates]
                else:
                    ingest_result = await ingest_image_candidates(
                        tuple(candidates), registry=registry, repository=image_repository,
                        destination_dir=image_asset_dir,
                    )
                    registered = len(ingest_result.assets)
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
        stored.append(await repository.upsert_and_schedule_generation(document, retrieval_query=request.query))
    return tuple(stored)


def _wikidata_image_candidates(
    item_id: str, statements: Sequence[StatementValue],
) -> list[dict[str, str]]:
    """Turn explicitly requested Wikidata image properties into Commons file URLs."""
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for statement in statements:
        if statement.property_id not in {"P18", "P154"}:
            continue
        value = statement.value
        if not isinstance(value, str) or not value.strip():
            continue
        raw_value = value.strip()
        parsed = urlsplit(raw_value)
        if parsed.scheme in {"https", "http"} and parsed.hostname:
            image_url = raw_value
        else:
            image_url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(raw_value, safe="")
        if image_url in seen:
            continue
        seen.add(image_url)
        output.append({
            "url": image_url, "source_native_id": item_id,
            "role": "wikidata:image:P154" if statement.property_id == "P154" else "wikidata:image:P18",
            "alt_text": raw_value,
        })
    return output
