"""Discover, test, and promote new source hosts without mixing them with article leads."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.agents.crawlers import get_type_crawler
from ani_menu_agent.discovery import DiscoveryItem, _inferred_candidate_types, parse_html_listing
from ani_menu_agent.models import CONTENT_TYPE_LABELS, ContentType
from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import _get_html, collect_official_page
from ani_menu_agent.sources.registry import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_TYPE_MAP_PATH,
    load_source_registry,
)

_NON_PUBLISHER_HOSTS = {
    "accounts.google.com", "facebook.com", "instagram.com", "line.me", "linktr.ee",
    "t.co", "twitter.com", "x.com", "youtube.com", "youtu.be", "amazon.co.jp",
    "amazon.com", "rakuten.co.jp", "store.shopping.yahoo.co.jp",
}
_VALID_TYPE_CODES = {item.value for item in CONTENT_TYPE_LABELS}


class SourceCandidate(BaseModel):
    """A candidate website, distinct from a candidate article/post."""

    id: str
    host: str
    root_url: HttpUrl
    sample_urls: dict[str, list[str]] = Field(default_factory=dict)
    discovered_via: list[str] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)
    status: str = "pending_validation"
    attempts: int = 0
    last_checked_at: str | None = None
    validation: dict = Field(default_factory=dict)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _is_registered_host(host: str, sources: list[Source]) -> bool:
    for source in sources:
        known = {
            _host(str(source.url)),
            *(item.lower().rstrip(".") for item in source.allowed_asset_hosts),
        }
        if any(host == item or host.endswith(f".{item}") for item in known if item):
            return True
    return False


def source_candidate_id(host: str) -> str:
    digest = hashlib.sha1(host.encode("utf-8")).hexdigest()[:16]
    return f"source-candidate:{digest}"


def extract_source_candidates(
    captures: list[dict], registered_sources: list[Source]
) -> list[SourceCandidate]:
    """Promote external article-body links into deduplicated source-host leads."""
    leads: dict[str, SourceCandidate] = {}
    for capture in captures:
        content_type = str(capture.get("content_type") or "")
        if content_type not in _VALID_TYPE_CODES:
            continue
        source_id = str(capture.get("source_id") or "")
        page_url = str(capture.get("url") or "")
        for link in capture.get("outbound_links", []):
            url = str(link.get("url") or "")
            parsed = urlparse(url)
            host = _host(url)
            if (
                parsed.scheme != "https"
                or not host
                or host in _NON_PUBLISHER_HOSTS
                or _is_registered_host(host, registered_sources)
            ):
                continue
            lead = leads.setdefault(
                host,
                SourceCandidate(
                    id=source_candidate_id(host),
                    host=host,
                    root_url=f"https://{host}/",
                ),
            )
            urls = lead.sample_urls.setdefault(content_type, [])
            if url not in urls and len(urls) < 5:
                urls.append(url)
            if source_id and source_id not in lead.discovered_via:
                lead.discovered_via.append(source_id)
            if page_url and page_url not in lead.validation.setdefault("seed_pages", []):
                lead.validation["seed_pages"].append(page_url)
            if content_type not in lead.content_types:
                lead.content_types.append(content_type)
    return sorted(leads.values(), key=lambda item: item.host)


def _probe_source(candidate: SourceCandidate) -> Source:
    return Source(
        id="source_probe",
        name=candidate.host,
        url=f"https://{candidate.host}/",
        category="source_discovery",
        content_types=[ContentType(item) for item in candidate.content_types],
        role="discovery",
        collection="html",
        status="active",
        region="global",
        allowed_asset_hosts=[candidate.host],
        min_interval_seconds=2.0,
        discovery_enabled=True,
    )


def validate_source_candidate(candidate: SourceCandidate) -> SourceCandidate:
    """Require an accessible listing, typed article links, and successful article capture."""
    candidate.attempts += 1
    candidate.last_checked_at = datetime.now(UTC).isoformat()
    checks: dict[str, dict] = {}
    probe = _probe_source(candidate)
    try:
        listing_html, final_url = _get_html(str(candidate.root_url), probe, timeout=20.0)
        if _host(final_url) != candidate.host:
            raise ValueError("홈페이지가 다른 도메인으로 이동합니다.")
        listing_items = parse_html_listing(listing_html, probe, limit=100)
        for type_code, sample_urls in candidate.sample_urls.items():
            type_result = {"listing_candidates": 0, "captured_articles": 0, "errors": []}
            crawler = get_type_crawler(type_code)
            profile = crawler.profile
            typed_items: list[DiscoveryItem] = []
            for item in listing_items:
                available = set(candidate.content_types)
                typed = item.model_copy(update={"content_types": sorted(available)})
                inferred = _inferred_candidate_types(typed, probe)
                text = f"{item.title} {item.summary}"
                if type_code in inferred and crawler and all(
                    re.search(pattern, text, re.IGNORECASE)
                    for _, pattern in profile.required_signals
                ):
                    typed_items.append(item)
            type_result["listing_candidates"] = len(typed_items)
            test_urls = list(
                dict.fromkeys(
                    [*(str(item.url) for item in typed_items[:2]), *sample_urls[:1]]
                )
            )
            for article_url in test_urls:
                try:
                    page = collect_official_page(
                        probe,
                        article_url,
                        timeout=20.0,
                        expand_related_pages=False,
                        allow_discovery_page=True,
                    )
                    article_text = f"{page.title} {page.text}"
                    if len(page.text.strip()) >= 160 and all(
                        re.search(pattern, article_text, re.IGNORECASE)
                        for _, pattern in profile.required_signals
                    ):
                        type_result["captured_articles"] += 1
                except Exception as exc:  # individual links should not stop other type checks
                    type_result["errors"].append(str(exc)[:240])
            checks[type_code] = type_result
        passed = [
            code
            for code, result in checks.items()
            if result["listing_candidates"] > 0 and result["captured_articles"] > 0
        ]
        candidate.validation = {
            "homepage": "passed",
            "final_url": final_url,
            "checks_by_type": checks,
            "passed_types": passed,
        }
        candidate.content_types = passed
        candidate.status = "validated" if passed else "failed"
    except Exception as exc:
        candidate.validation = {
            "homepage": "failed",
            "error": str(exc)[:500],
            "checks_by_type": checks,
            "passed_types": [],
        }
        candidate.content_types = []
        candidate.status = "failed"
    return candidate


def promote_source_candidate(
    candidate: SourceCandidate,
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    type_map_path: Path = DEFAULT_TYPE_MAP_PATH,
) -> str:
    """Register only technically validated hosts as active discovery endpoints."""
    if candidate.status != "validated" or not candidate.content_types:
        raise ValueError("검증 통과한 유형이 있는 출처 후보만 등록할 수 있습니다.")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    type_map = json.loads(type_map_path.read_text(encoding="utf-8"))
    for source in registry.get("sources", []):
        existing_host = _host(str(source.get("url") or ""))
        if existing_host == candidate.host:
            mappings = type_map.setdefault("sources", {})
            mappings[source["id"]] = sorted(
                set(mappings.get(source["id"], [])) | set(candidate.content_types)
            )
            type_map_path.write_text(
                json.dumps(type_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return source["id"]
    source_id = "src_" + re.sub(r"[^a-z0-9]+", "_", candidate.host).strip("_")[:48]
    existing_ids = {item["id"] for item in registry.get("sources", [])}
    base_id = source_id
    suffix = 2
    while source_id in existing_ids:
        source_id = f"{base_id}_{suffix}"
        suffix += 1
    registry.setdefault("sources", []).append(
        {
            "id": source_id,
            "name": candidate.host,
            "url": str(candidate.root_url),
            "category": "source_discovery",
            "role": "discovery",
            "collection": "html",
            "status": "active",
            "region": "global",
            "discovery_enabled": True,
            "notes": (
                "자동 출처 후보 검증 통과. 발견용 페이지로만 사용하며 "
                "원문 근거로 직접 승인하지 않음. "
                f"검증일 {candidate.last_checked_at}; 유형: {', '.join(candidate.content_types)}"
            ),
        }
    )
    type_map.setdefault("sources", {})[source_id] = sorted(set(candidate.content_types))
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    type_map_path.write_text(
        json.dumps(type_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Fail immediately if the pair of config files does not load as a valid registry.
    from ani_menu_agent.sources.registry import load_source_registry

    load_source_registry(registry_path)
    return source_id


def run_source_discovery(repository, *, validation_limit: int = 3) -> dict:
    """Harvest unknown article-body hosts, validate a small batch, and register passes.

    Failed candidates are retained with diagnostics and are not retried automatically.
    Promotion registers a source as discovery-only; it never grants primary-fact status.
    """
    registry = load_source_registry()
    candidates = extract_source_candidates(
        repository.list_source_discovery_captures(), registry.sources
    )
    existing = {
        str(item.get("id")): item
        for item in repository.list_source_candidates(limit=1000)
    }
    staged = 0
    for lead in candidates:
        prior = existing.get(lead.id)
        if prior:
            if prior.get("status") != "pending_validation":
                continue
            lead.attempts = int(prior.get("attempts", 0))
            lead.last_checked_at = prior.get("last_checked_at")
            lead.validation = {
                **(prior.get("validation") or {}),
                "seed_pages": list(
                    dict.fromkeys(
                        [
                            *(prior.get("validation", {}).get("seed_pages", [])),
                            *(lead.validation.get("seed_pages", [])),
                        ]
                    )
                )[-10:],
            }
            lead.sample_urls = {
                code: list(
                    dict.fromkeys(
                        [*(prior.get("sample_urls", {}).get(code, [])), *urls]
                    )
                )[:5]
                for code, urls in {
                    **(prior.get("sample_urls") or {}),
                    **lead.sample_urls,
                }.items()
            }
            lead.content_types = sorted(
                set(prior.get("content_types", [])) | set(lead.content_types)
            )
        repository.persist_source_candidate(lead.model_dump(mode="json"))
        staged += int(prior is None)

    pending = repository.list_source_candidates(status="pending_validation", limit=1000)
    checked = []
    for payload in pending[: max(0, validation_limit)]:
        lead = validate_source_candidate(SourceCandidate.model_validate(payload))
        if lead.status == "validated":
            source_id = promote_source_candidate(lead)
            lead.status = "registered"
            lead.validation["registered_source_id"] = source_id
        repository.persist_source_candidate(lead.model_dump(mode="json"))
        checked.append(
            {
                "id": lead.id,
                "host": lead.host,
                "status": lead.status,
                "content_types": lead.content_types,
                "validation": lead.validation,
            }
        )
    return {
        "new_candidates_staged": staged,
        "pending_candidates": max(0, len(pending) - len(checked)),
        "validated_and_registered": sum(item["status"] == "registered" for item in checked),
        "failed": sum(item["status"] == "failed" for item in checked),
        "checks": checked,
    }
