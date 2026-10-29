"""Shared LangGraph workflow for the ten focused crawler agents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit

import httpx
from langgraph.graph import END, START, StateGraph

from ani_menu_agent.agents.image import image_curator
from ani_menu_agent.discovery import DiscoveryItem, collect_candidates, supports_discovery_adapter
from ani_menu_agent.models import (
    ContentKind,
    ContentPackage,
    ContentType,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.sources.page_collector import collect_official_page
from ani_menu_agent.sources.registry import load_source_registry
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog import resolve_menu_work_ids


@dataclass(frozen=True)
class CrawlerProfile:
    """Editorial and source-boundary rules unique to one crawler role."""

    content_type: ContentType
    source_roles: frozenset[str]
    purpose: str
    require_work_title: bool = False
    capture_primary_page_assets: bool = True
    asset_page_limit: int = 2
    required_signals: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceCrawlResult:
    source_id: str
    status: str
    attempted: bool
    candidate_count: int = 0
    error: str = ""


@dataclass(frozen=True)
class CrawlerRun:
    content_type: ContentType
    items: list[DiscoveryItem]
    errors: list[str]
    persisted_count: int
    source_results: list[SourceCrawlResult]
    rejected_count: int = 0
    captured_page_count: int = 0
    image_candidate_count: int = 0
    persisted_image_count: int = 0


class CrawlerState(TypedDict, total=False):
    work_title: str | None
    limit: int
    timeout: float
    region_policy: str
    source_ids: list[str]
    source_results: list[dict]
    items: list[dict]
    errors: list[str]
    rejected_count: int
    persisted_count: int
    capture_images: bool
    captured_page_count: int
    image_candidate_count: int
    persisted_image_count: int
    stop: bool
    repository: AgentRepository | None


def _canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def profile_signal_checks(profile: CrawlerProfile, text: str) -> dict[str, bool]:
    """Return auditable lexical hints for a type profile (not factual verification)."""
    return {
        label: bool(re.search(pattern, text, re.IGNORECASE))
        for label, pattern in profile.required_signals
    }


class TypeCrawlerAgent:
    """A bounded source-to-database workflow with type-specific admission rules.

    Each concrete agent owns a CrawlerProfile. The shared graph handles preflight,
    source allowlisting, candidate quality gates, optional same-page image capture,
    and idempotent persistence. It never writes generated copy or marks a lead verified.
    """

    profile: CrawlerProfile

    def __init__(self) -> None:
        graph = StateGraph(CrawlerState)
        graph.add_node("source_preflight", self._source_preflight)
        graph.add_node("collect_candidates", self._collect)
        graph.add_node("type_quality_gate", self._quality_gate)
        graph.add_node("persist_candidates", self._persist)
        graph.add_edge(START, "source_preflight")
        graph.add_conditional_edges(
            "source_preflight",
            lambda state: "finish" if state.get("stop") else "collect",
            {"finish": END, "collect": "collect_candidates"},
        )
        graph.add_edge("collect_candidates", "type_quality_gate")
        graph.add_edge("type_quality_gate", "persist_candidates")
        graph.add_edge("persist_candidates", END)
        self._graph = graph.compile()

    @property
    def content_type(self) -> ContentType:
        return self.profile.content_type

    @property
    def agent_id(self) -> str:
        return f"crawler_{self.content_type.value}"

    def source_ids(self) -> list[str]:
        return [
            source.id
            for source in load_source_registry().sources
            if self.content_type in source.content_types
        ]

    def _source_preflight(self, state: CrawlerState) -> dict:
        work_title = (state.get("work_title") or "").strip() or None
        if self.profile.require_work_title and not work_title:
            return {
                "source_ids": [],
                "errors": [f"{self.content_type.value} 크롤러는 --work-title이 필요합니다."],
                "source_results": [],
                "stop": True,
            }

        registry = load_source_registry()
        linked = [
            source
            for source in registry.sources
            if self.content_type in source.content_types
        ]
        selected = [
            source
            for source in linked
            if source.role in self.profile.source_roles
            and supports_discovery_adapter(source, work_title=work_title)
        ]
        selected_ids = [source.id for source in selected]
        selected_set = set(selected_ids)
        results = [
            {
                "source_id": source.id,
                "status": (
                    "ready"
                    if source.id in selected_set
                    else "not_approved"
                    if source.status not in {"active", "trend_only"}
                    else "discovery_disabled"
                    if not source.discovery_enabled
                    else "role_not_allowed"
                    if source.role not in self.profile.source_roles
                    else "adapter_or_query_unavailable"
                ),
                "attempted": False,
                "candidate_count": 0,
                "error": "",
            }
            for source in linked
        ]
        if not selected:
            return {
                "source_ids": [],
                "source_results": results,
                "errors": [f"{self.content_type.value}: 실행 가능한 승인 출처가 없습니다."],
                "stop": True,
            }
        return {"source_ids": selected_ids, "source_results": results, "stop": False}

    def _collect(self, state: CrawlerState) -> dict:
        source_report: list[dict] = []
        try:
            items, errors = collect_candidates(
                limit=state["limit"],
                timeout=state["timeout"],
                region_policy=state["region_policy"],
                content_type=self.content_type.value,
                work_title=state.get("work_title"),
                source_ids=set(state.get("source_ids", [])),
                source_report=source_report,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "items": [],
                "errors": [*state.get("errors", []), str(exc)],
                "source_results": self._merge_source_results(
                    state.get("source_results", []), source_report
                ),
            }
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "errors": [*state.get("errors", []), *errors],
            "source_results": self._merge_source_results(
                state.get("source_results", []), source_report
            ),
        }

    @staticmethod
    def _merge_source_results(configured: list[dict], runtime: list[dict]) -> list[dict]:
        by_id = {item["source_id"]: dict(item) for item in configured}
        for result in runtime:
            by_id[result["source_id"]] = {**by_id.get(result["source_id"], {}), **result}
        return list(by_id.values())

    def _quality_gate(self, state: CrawlerState) -> dict:
        registry = load_source_registry()
        sources = {source.id: source for source in registry.sources}
        selected = set(state.get("source_ids", []))
        accepted: list[DiscoveryItem] = []
        rejected = 0
        seen: set[str] = set()
        source_counts: dict[str, int] = {}
        for raw in state.get("items", []):
            try:
                item = DiscoveryItem.model_validate(raw)
            except (TypeError, ValueError):
                rejected += 1
                continue
            source = sources.get(item.source_id)
            reasons = []
            if source is None or item.source_id not in selected:
                reasons.append("source_not_selected")
            elif source.role not in self.profile.source_roles:
                reasons.append("source_role_mismatch")
            if not item.title.strip() or len(item.title.strip()) > 300:
                reasons.append("invalid_title")
            if str(item.url).split(":", 1)[0].lower() != "https":
                reasons.append("non_https_candidate")
            if self.content_type.value not in item.content_types:
                reasons.append("type_not_supported_by_candidate")
            if reasons:
                rejected += 1
                continue

            item = item.model_copy(
                update={
                    "title": re.sub(r"\s+", " ", item.title).strip(),
                    "summary": re.sub(r"\s+", " ", item.summary).strip()[:2000],
                }
            )
            canonical = _canonical_url(str(item.url))
            if canonical in seen:
                continue
            seen.add(canonical)
            accepted.append(item)
            source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1

        source_results = []
        for result in state.get("source_results", []):
            updated = dict(result)
            if updated.get("source_id") in source_counts:
                updated["candidate_count"] = source_counts[updated["source_id"]]
                if updated.get("attempted"):
                    updated["status"] = "succeeded_with_candidates"
            source_results.append(updated)
        return {
            "items": [item.model_dump(mode="json") for item in accepted],
            "rejected_count": rejected,
            "source_results": source_results,
        }

    def _persist(self, state: CrawlerState) -> dict:
        repository = state.get("repository") or AgentRepository()
        items = state.get("items", [])
        saved = repository.persist_discovery(items, state["region_policy"])
        capture = self._capture_images(
            state,
            repository,
            [DiscoveryItem.model_validate(item) for item in items],
        ) if state.get("capture_images") else {}
        return {"persisted_count": saved, **capture}

    def _capture_images(
        self,
        state: CrawlerState,
        repository: AgentRepository,
        items: list[DiscoveryItem],
    ) -> dict:
        """Capture same-page artwork only from registered, active HTML sources.

        Community images are deliberately excluded. This stage never searches
        arbitrary image hosts or borrows an unrelated work/product image.
        """
        if not self.profile.capture_primary_page_assets:
            return {}
        registry = {source.id: source for source in load_source_registry().sources}
        eligible = [
            item
            for item in items
            if (source := registry.get(item.source_id))
            and source.status == "active"
            and source.role in {"primary", "editorial", "reference"}
            and source.collection == "html"
        ][: self.profile.asset_page_limit]
        captured_pages = image_candidates = persisted_images = 0
        errors = []
        for item in eligible:
            source = registry[item.source_id]
            try:
                capture = collect_official_page(
                    source,
                    str(item.url),
                    timeout=state["timeout"],
                    expand_related_pages=False,
                )
                evidence = SourceEvidence(
                    id=source.id,
                    title=capture.title or item.title,
                    url=capture.url,
                    publisher=source.name,
                    is_official=source.role == "primary",
                    checked_at=capture.fetched_at,
                    role=source.role,
                    region=source.region,
                    allowed_asset_hosts=source.allowed_asset_hosts,
                )
                page_hash = hashlib.sha1(str(capture.url).encode()).hexdigest()[:14]
                request_id = f"typed-crawl-{self.content_type.value}-{page_hash}"
                package = ContentPackage(
                    request_id=request_id,
                    topic=capture.title or item.title,
                    content_kind=(
                        ContentKind.HUMOR
                        if source.role == "trend"
                        else ContentKind.INFORMATION
                    ),
                    content_type=self.content_type.value,
                    audience_region="KR",
                    sources=[evidence],
                )
                package.menu_work_ids = resolve_menu_work_ids(package)
                # Persist the full image set found on each source article so
                # later content generation can choose the most relevant assets.
                for image_url in capture.image_urls:
                    image_id = hashlib.sha1(str(image_url).encode()).hexdigest()[:16]
                    package.images.append(
                        ImageAsset(
                            id=f"{source.id}-{image_id}",
                            url=image_url,
                            source_url=capture.image_source_pages.get(
                                str(image_url), str(capture.url)
                            ),
                            official=source.role == "primary",
                            rights_status=RightsStatus.NEEDS_REVIEW,
                            provenance_source=evidence if source.role == "primary" else None,
                            menu_work_ids=package.menu_work_ids,
                            source_description=" ".join(
                                part
                                for part in (
                                    capture.image_alt_text.get(str(image_url), "").strip(),
                                    capture.image_source_titles.get(str(image_url), "").strip(),
                                )
                                if part
                            )[:240],
                        )
                    )

                curated = image_curator(
                    {
                        "package": package.model_dump(mode="json"),
                        "trace": [],
                        "download_images": True,
                        "repository": repository,
                    }
                )
                curated_package = ContentPackage.model_validate(curated["package"])
                image_rows = [
                    {
                        "id": image.id,
                        "url": str(image.url),
                        "source_url": str(image.source_url),
                        "alt_text": image.source_description,
                        "download_status": image.download_status,
                        "rights_status": image.rights_status.value,
                    }
                    for image in curated_package.images
                ]
                capture_payload = capture.model_dump(mode="json")
                capture_payload.update(
                    {
                        "content_type": self.content_type.value,
                        "discovery_candidate_id": item.id,
                        "crawl_purpose": self.profile.purpose,
                        "type_capture_checks": profile_signal_checks(
                            self.profile,
                            f"{item.title}\n{capture.title}\n{capture.text}",
                        ),
                        "images": image_rows,
                    }
                )
                repository.persist_page_capture(capture_payload, source.region)
                repository.upsert_record(
                    agent_type="information",
                    record_id=f"source:{request_id}:{source.id}",
                    record_kind="source",
                    package=curated_package,
                    payload=evidence.model_dump(mode="json"),
                    status="collected",
                )
                persisted_images += int(curated.get("persisted_image_count", 0))
                image_candidates += len(curated_package.images)
                captured_pages += 1
            except Exception as exc:
                errors.append(f"{source.id}/{item.id}: {str(exc)[:240]}")
        return {
            "captured_page_count": captured_pages,
            "image_candidate_count": image_candidates,
            "persisted_image_count": persisted_images,
            "errors": [*state.get("errors", []), *errors],
        }

    def run(
        self,
        *,
        work_title: str | None = None,
        limit: int = 20,
        timeout: float = 15.0,
        region_policy: str = "kr_first",
        repository: AgentRepository | None = None,
        capture_images: bool = True,
    ) -> CrawlerRun:
        if not 1 <= limit <= 50:
            raise ValueError("limit은 1~50 사이여야 합니다.")
        if timeout <= 0:
            raise ValueError("timeout은 0보다 커야 합니다.")
        if region_policy not in {"kr_first", "mixed", "jp_first"}:
            raise ValueError("region_policy는 kr_first, mixed, jp_first 중 하나여야 합니다.")
        final = self._graph.invoke(
            {
                "work_title": work_title,
                "limit": limit,
                "timeout": timeout,
                "region_policy": region_policy,
                "repository": repository,
                "capture_images": capture_images,
                "source_results": [],
                "errors": [],
            }
        )
        return CrawlerRun(
            content_type=self.content_type,
            items=[DiscoveryItem.model_validate(item) for item in final.get("items", [])],
            errors=final.get("errors", []),
            persisted_count=final.get("persisted_count", 0),
            source_results=[SourceCrawlResult(**item) for item in final.get("source_results", [])],
            rejected_count=final.get("rejected_count", 0),
            captured_page_count=final.get("captured_page_count", 0),
            image_candidate_count=final.get("image_candidate_count", 0),
            persisted_image_count=final.get("persisted_image_count", 0),
        )
