"""Build a bounded, rotating schedule from official and locally registered works."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from ani_rag.catalog import LocalWorkCatalog, WorkSearchSeed
from ani_rag.collector import CollectionRequest
from ani_rag.models import EvidenceType, RagContentType
from ani_rag.structured_mcp import StructuredCollectionRequest
from ani_rag.settings import EvergreenSourceRegistry, SourceStatus
from ani_rag.agents.mcp_crawler.agent import RecurringCollectionPlan
from ani_rag.web_discovery import WebDiscoveryRequest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN_CONFIG = PROJECT_ROOT / "config" / "automatic_plans.json"
DEFAULT_SCHEDULE_STATE = PROJECT_ROOT / "data" / "plans" / "automatic_schedule_state_rotation7d.json"
_MAX_INTERVAL_SECONDS = 31_536_000


def load_automatic_plans(
    registry: EvergreenSourceRegistry,
    *,
    config_path: Path = DEFAULT_PLAN_CONFIG,
    state_path: Path = DEFAULT_SCHEDULE_STATE,
    now: datetime | None = None,
) -> tuple[RecurringCollectionPlan, ...]:
    """Create staggered web-search and approved-reference plans without network calls."""
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unsupported automatic-plan configuration")
    if not config.get("enabled", False):
        return ()

    catalog = LocalWorkCatalog()
    seeds = catalog.work_search_seeds()
    if not seeds:
        raise ValueError("The local RAG work snapshot contains no searchable works")
    rotation_days = int(config.get("rotation_days", 7))
    if not 1 <= rotation_days <= 365:
        raise ValueError("Automatic work-catalog rotation must be between one and 365 days")
    content_types = tuple(RagContentType(value) for value in config.get("content_types", ()))
    if not content_types or len(set(content_types)) != len(content_types):
        raise ValueError("Automatic plans need a non-empty list of unique content types")
    templates = config.get("query_templates")
    routes = config.get("reference_sources")
    if not isinstance(templates, dict) or not isinstance(routes, dict):
        raise ValueError("Automatic plans need query_templates and reference_sources objects")
    if any(content_type.value not in templates for content_type in content_types):
        raise ValueError("Every automatic content type needs a query template")

    source_records = {source.source_id: source for source in registry.sources}
    enabled_routes: list[tuple[str, Mapping[str, object]]] = []
    for source_id, route in routes.items():
        source = source_records.get(source_id)
        if not isinstance(route, dict):
            raise ValueError(f"Invalid automatic source route for {source_id!r}")
        if (source is None or source.status is not SourceStatus.APPROVED
                or not source.is_collecting):
            continue
        route_types = tuple(RagContentType(item) for item in route.get("content_types", ()))
        if not set(route_types) <= set(source.content_types):
            raise ValueError(f"Automatic route {source_id!r} exceeds its approved content types")
        if not route_types:
            continue
        enabled_routes.append((source_id, route))

    task_count = len(seeds) * (len(content_types) + sum(
        len(tuple(RagContentType(item) for item in route.get("content_types", ())))
        for _, route in enabled_routes
    ))
    cycle_seconds = rotation_days * 86_400
    if cycle_seconds > _MAX_INTERVAL_SECONDS:
        raise ValueError(
            "Full work-catalog cycle exceeds the supported one-year plan interval; "
            "reduce the rotation period or catalog selection"
        )

    config_hash = hashlib.sha256(config_bytes).hexdigest()
    catalog_hash = _catalog_fingerprint(seeds)
    schedule_start = _load_or_create_schedule_start(
        state_path, config_hash=config_hash, catalog_hash=catalog_hash,
        work_count=len(seeds), task_count=task_count, cycle_seconds=cycle_seconds,
        now=now or datetime.now(timezone.utc),
    )

    plans: list[RecurringCollectionPlan] = []
    index = 0
    for seed in seeds:
        for content_type in content_types:
            query = _format_query(str(templates[content_type.value]), seed.titles[0])
            variants = _query_variants(str(templates[content_type.value]), seed)
            request = WebDiscoveryRequest(
                query=query,
                query_variants=variants,
                content_type=content_type,
                menu_work_id=seed.menu_work_id,
                expected_work_titles=seed.titles[:20],
                result_limit=int(config.get("search_result_limit", 3)),
                top_k=int(config.get("candidate_top_k", 8)),
                candidate_limit=int(config.get("candidate_limit", 40)),
                min_retrieval_score=float(config.get("min_retrieval_score", 0.2)),
            )
            plans.append(_make_plan(
                f"web-{content_type.value}-{seed.menu_work_id}", request,
                cycle_seconds, task_count, schedule_start, index, config_hash,
            ))
            index += 1

        for source_id, route in enabled_routes:
            source = source_records[source_id]
            route_types = tuple(RagContentType(item) for item in route.get("content_types", ()))
            for content_type in route_types:
                query = _format_query(str(templates[content_type.value]), seed.titles[0])
                evidence_types = route.get("evidence_types", {})
                evidence_name = (
                    evidence_types.get(content_type.value)
                    if isinstance(evidence_types, dict) else None
                ) or route.get("evidence_type")
                if not evidence_name:
                    raise ValueError(f"Automatic route {source_id!r} omits evidence type for {content_type.value}")
                evidence_type = EvidenceType(str(evidence_name))
                if "property_ids" in route:
                    request = StructuredCollectionRequest(
                        source_id=source_id, content_type=content_type,
                        evidence_type=evidence_type, query=query, language="en",
                        property_ids=tuple(str(item) for item in route["property_ids"]),
                        result_limit=int(config.get("source_result_limit", 5)),
                        seed_menu_work_id=seed.menu_work_id,
                        refresh_after_seconds=int(config.get("refresh_after_seconds", 2_592_000)),
                    )
                else:
                    request = CollectionRequest(
                        source_id=source_id, content_type=content_type,
                        evidence_type=evidence_type, query=query, language="en",
                        result_limit=int(config.get("source_result_limit", 5)),
                        seed_menu_work_id=seed.menu_work_id,
                        generation_query=query,
                        refresh_after_seconds=int(config.get("refresh_after_seconds", 2_592_000)),
                    )
                plans.append(_make_plan(
                    f"source-{source_id}-{content_type.value}-{seed.menu_work_id}",
                    request, cycle_seconds, task_count, schedule_start, index, config_hash,
                ))
                index += 1

    if index != task_count:
        raise AssertionError("Automatic plan count changed during schedule construction")
    return tuple(plans)


def _make_plan(
    plan_id: str, request: object, cycle_seconds: int, task_count: int, schedule_start: datetime,
    index: int, config_hash: str,
) -> RecurringCollectionPlan:
    suffix = hashlib.sha256(config_hash.encode("ascii")).hexdigest()[:8]
    safe_id = f"{plan_id}-{suffix}"
    # Work seeds are sorted by numeric ID; preserve that order while spreading
    # each rotation evenly across the configured cycle.
    first_run_at = schedule_start + timedelta(seconds=(cycle_seconds * index) // task_count)
    return RecurringCollectionPlan(
        plan_id=safe_id, request=request, interval_seconds=cycle_seconds,
        first_run_at=first_run_at,
    )


def _format_query(template: str, title: str) -> str:
    if "{title}" not in template:
        raise ValueError("Every automatic query template must contain {title}")
    return template.replace("{title}", title).strip()


def _query_variants(template: str, seed: WorkSearchSeed) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        _format_query(template, title) for title in seed.titles[1:4]
        if _format_query(template, title)
    ))


def _catalog_fingerprint(seeds: tuple[WorkSearchSeed, ...]) -> str:
    body = "\n".join(f"{item.menu_work_id}\t{'|'.join(item.titles)}" for item in seeds)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_or_create_schedule_start(
    path: Path, *, config_hash: str, catalog_hash: str, work_count: int,
    task_count: int, cycle_seconds: int, now: datetime,
) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Automatic schedule start time must include a timezone")
    expected = {
        "version": 2,
        "config_sha256": config_hash,
        "catalog_sha256": catalog_hash,
        "work_count": work_count,
        "task_count": task_count,
        "cycle_seconds": cycle_seconds,
    }
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        previous_work_count = payload.get("work_count")
        previous_task_count = payload.get("task_count")
        # Keep the same work rotation when the registry grows or the official
        # list is replaced. A config/route change still needs an explicit reset.
        if (any(payload.get(key) != expected[key] for key in ("version", "config_sha256", "cycle_seconds"))
                or not isinstance(previous_work_count, int) or previous_work_count <= 0
                or not isinstance(previous_task_count, int)
                or previous_task_count * work_count != task_count * previous_work_count):
            raise RuntimeError(
                "Automatic schedule checkpoint no longer matches the plan configuration. "
                "Review the changed config or approved routes before replacing the checkpoint."
            )
        start = datetime.fromisoformat(str(payload["started_at"]))
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("Automatic schedule checkpoint timestamp must include a timezone")
        start = start.astimezone(timezone.utc)
        if all(payload.get(key) == value for key, value in expected.items()):
            return start
    else:
        start = now.astimezone(timezone.utc)
    payload = {**expected, "started_at": start.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return start
