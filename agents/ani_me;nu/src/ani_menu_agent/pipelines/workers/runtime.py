"""Independent consumers for durable generation and review jobs."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ani_menu_agent.agents.content_generation.graph import build_content_generation_agent
from ani_menu_agent.agents.reviewer.graph import build_reviewer_agent
from ani_menu_agent.classification import infer_period_content_type, infer_toei_press_type
from ani_menu_agent.nodes import (
    archive_rejected,
    crawler_update_required,
    feed_queue,
    intake,
    revision_checkpoint,
    rights_preflight,
    route_review,
)
from ani_menu_agent.models import ContentPackage, Fact
from ani_menu_agent.period_repair import REPAIRABLE_ISSUES, evidence_snapshot, has_repaired_evidence
from ani_menu_agent.period_schedule import attach_source_schedule, primary_source_schedule
from ani_menu_agent.pipelines.common.jobs import (
    PipelineJob,
    PipelineJobStatus,
    PipelineLane,
    PipelineStage,
)
from ani_menu_agent.policy import POLICY
from ani_menu_agent.source_freshness import expiry_evidence, immediate_news_action_expiry
from ani_menu_agent.storage import AgentRepository, ExpiredFeedContentError
from ani_menu_agent.work_catalog import UnconfirmedPeriodWorkError, refresh_work_catalog_caches

LEASE_SECONDS = 900
MAX_JOB_ATTEMPTS = 5
MAX_RECOLLECTION_ATTEMPTS = 3
TIME_SENSITIVE_TYPES = {"news", "event", "goods"}


@dataclass(frozen=True)
class PipelineJobOutcome:
    status: PipelineJobStatus = PipelineJobStatus.COMPLETED
    detail: str | None = None


def _state_issue_detail(prefix: str, state: dict) -> str:
    issues = state.get("qa_report", [])
    details = []
    for issue in issues:
        if isinstance(issue, dict):
            code = str(issue.get("code") or "")
            message = str(issue.get("message") or "")
            details.append(": ".join(part for part in (code, message) if part))
        elif issue:
            details.append(str(issue))
    return prefix + (": " + " | ".join(details[:4]) if details else "")


def _crawler_return_detail(state: dict) -> str:
    return _state_issue_detail("needs_crawler_update", state)


def _lease_heartbeat(repository: AgentRepository, job: PipelineJob, worker_id: str):
    stopped = threading.Event()
    lost = threading.Event()

    def beat() -> None:
        while not stopped.wait(max(10, LEASE_SECONDS // 3)):
            try:
                if not repository.renew_pipeline_job_lease(
                    job.job_id, worker_id=worker_id, lease_seconds=LEASE_SECONDS
                ):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=beat, name=f"lease-{job.job_id[:8]}", daemon=True)
    thread.start()
    return stopped, lost, thread


def _state_for_job(job: PipelineJob, package: dict, *, usage_records=None) -> dict:
    payload = job.payload
    return {
        "request_id": job.request_id or package.get("request_id"),
        "topic": package.get("topic", ""),
        "content_kind": package.get("content_kind", "information"),
        "requested_content_type": payload.get("content_type") or package.get("content_type"),
        "package": package,
        "usage_records": list(usage_records if usage_records is not None else payload.get("usage_records", [])),
        "trace": list(payload.get("trace", [])),
        "revision_count": int(payload.get("revision_count", 0)),
        "review_failures": int(payload.get("review_failures", 0)),
        "max_revision": int(payload.get("max_revision", POLICY.max_review_failures)),
        "needs_revision": bool(payload.get("needs_revision", False)),
        "revision_owner": str(payload.get("revision_owner", "")),
        "revision_owners": list(payload.get("revision_owners", [])),
        "qa_report": list(payload.get("qa_report", [])),
        "minimal_review": True,
        "allow_llm": True,
        "download_images": False,
        "render_required": True,
    }


def _enqueue_review(repository: AgentRepository, job: PipelineJob, state: dict) -> None:
    request_id = str(state.get("request_id") or job.request_id or "")
    revision = int(state.get("revision_count", 0))
    payload = {
        **{key: job.payload[key] for key in ("repair_id", "repair_round") if key in job.payload},
        "content_type": state.get("requested_content_type"),
        "revision_count": revision,
        "review_failures": int(state.get("review_failures", 0)),
        "max_revision": int(state.get("max_revision", POLICY.max_review_failures)),
        "usage_records": list(state.get("usage_records", [])),
        "trace": list(state.get("trace", [])),
    }
    repository.enqueue_pipeline_job(
        lane=job.lane,
        stage=PipelineStage.REVIEW,
        idempotency_key=_revision_job_key(job, "review", revision),
        request_id=request_id,
        payload=payload,
    )


def _revision_job_key(job: PipelineJob, stage: str, revision: int) -> str:
    key = f"{job.request_id}:{stage}:{revision}"
    return f"{key}:{job.payload['repair_id']}" if job.payload.get("repair_id") else key


def _hold_period_package(
    repository: AgentRepository, package: dict, content_type: str, request_id: str,
    *, temporal: dict | None = None,
) -> str | None:
    """Persist and return the reason a period package cannot proceed."""
    if temporal is None:
        temporal = expiry_evidence(package, content_type)
    if temporal["status"] == "active":
        return None
    held_package = ContentPackage.model_validate(package)
    status = (
        "stale_candidate_skipped"
        if temporal["status"] == "expired"
        else "expiry_unresolved_held"
    )
    repository.upsert_record(
        agent_type="information",
        record_id=f"temporal-gate:{request_id}",
        record_kind="temporal_gate",
        package=held_package,
        payload={
            "content_type": content_type,
            "status": status,
            "end_date": temporal.get("end_date"),
            "evidence_snapshot": evidence_snapshot(held_package),
            "evidence": temporal.get("evidence"),
            "source_urls": [str(source.url) for source in held_package.sources],
        },
        status=status,
    )
    return status


def _hold_immediate_news_package(
    repository: AgentRepository, package: dict, request_id: str
) -> str | None:
    """Prevent expired or undated start-announcement news from being generated or passed."""
    temporal = immediate_news_action_expiry(package)
    if temporal["status"] not in {"expired", "unresolved"}:
        return None
    held_package = ContentPackage.model_validate(package)
    status = (
        "stale_candidate_skipped"
        if temporal["status"] == "expired"
        else "expiry_unresolved_held"
    )
    repository.upsert_record(
        agent_type="information",
        record_id=f"temporal-news-gate:{request_id}",
        record_kind="temporal_gate",
        package=held_package,
        payload={
            "content_type": "news",
            "status": status,
            "published_at": temporal.get("published_at"),
            "evidence_snapshot": evidence_snapshot(held_package),
            "evidence": temporal.get("evidence"),
            "source_urls": [str(source.url) for source in held_package.sources],
        },
        status=status,
    )
    return status


def _attach_release_window(package: ContentPackage, temporal: dict) -> None:
    """Keep approximate release wording as evidence without inventing calendar dates."""
    release_text = str(temporal.get("release_text") or "").strip()
    precision = temporal.get("release_precision")
    if not (
        package.sources and release_text and precision
        and temporal.get("availability_status") == "upcoming_release"
    ):
        return
    if any(
        fact.fact_type == "product_availability"
        and fact.details.get("evidence_kind") == "source_release_window"
        and fact.details.get("source_quote") == release_text
        for fact in package.facts
    ):
        return
    package.facts.append(Fact(
        id=f"{package.request_id}-upcoming-release-period",
        statement=release_text,
        source_ids=[package.sources[0].id],
        verified=False,
        time_sensitive=True,
        fact_type="product_availability",
        details={
            "availability_status": "upcoming_release",
            "release_precision": str(precision),
            "release_text": release_text,
            "source_quote": release_text,
            "evidence_kind": "source_release_window",
        },
    ))


def _process_generation(repository: AgentRepository, job: PipelineJob) -> PipelineJobOutcome | None:
    request_id = job.request_id
    if not request_id:
        raise ValueError("generation job is missing request_id")
    revision = bool(job.payload.get("needs_revision"))
    package = (
        repository.load_generated_draft(request_id).model_dump(mode="json")
        if revision
        else repository.load_content_package(
            request_id,
            job.payload.get("content_kind", "information"),
            content_type=job.payload.get("content_type"),
        ).model_dump(mode="json")
    )
    content_type = str(job.payload.get("content_type") or package.get("content_type") or "")
    is_toei_press = any(
        str(source.get("id") or "") == "src_www_toei_anim_co_jp"
        for source in package.get("sources", [])
        if isinstance(source, dict)
    )
    if not revision and is_toei_press:
        headline = " ".join(
            [
                str(package.get("topic") or ""),
                *[
                    str(source.get("title") or "")
                    for source in package.get("sources", [])
                    if isinstance(source, dict)
                ],
            ]
        )
        routed_type = infer_toei_press_type(headline)
        content_type = routed_type
        package["content_type"] = routed_type
        updated_job_payload = dict(job.payload)
        updated_job_payload["content_type"] = routed_type
        job = replace(job, payload=updated_job_payload)
    elif not revision and content_type == "news":
        inferred_period_type = infer_period_content_type(ContentPackage.model_validate(package))
        if inferred_period_type:
            content_type = inferred_period_type
            package["content_type"] = inferred_period_type
            updated_job_payload = dict(job.payload)
            updated_job_payload["content_type"] = inferred_period_type
            job = replace(job, payload=updated_job_payload)
    if content_type in {"news", "event", "goods"}:
        dated = ContentPackage.model_validate(package)
        dated.content_type = content_type
        attach_source_schedule(dated, primary_source_schedule(dated, content_type))
        package = dated.model_dump(mode="json")
        if content_type == "news":
            hold = _hold_immediate_news_package(repository, package, request_id)
        else:
            temporal = expiry_evidence(package, content_type)
            hold = _hold_period_package(repository, package, content_type, request_id, temporal=temporal)
            if not hold and content_type == "goods":
                _attach_release_window(dated, temporal)
                package = dated.model_dump(mode="json")
        if hold:
            return PipelineJobOutcome(PipelineJobStatus.CANCELLED, hold)
        if not revision and content_type in {"event", "goods"}:
            repository.persist_information(dated)
    state = _state_for_job(job, package)
    if not revision:
        state.update(intake(state))
        state.update(rights_preflight(state))
        if state.get("status") == "needs_crawler_update":
            crawler_update_required(state)
            return PipelineJobOutcome(PipelineJobStatus.RETURNED, _crawler_return_detail(state))
    generated = build_content_generation_agent().invoke(state)
    state.update(generated)
    if state.get("status") == "needs_crawler_update":
        crawler_update_required(state)
        return PipelineJobOutcome(PipelineJobStatus.RETURNED, _crawler_return_detail(state))
    if state.get("status") in {"invalid_input", "generation_failed"}:
        return PipelineJobOutcome(
            PipelineJobStatus.FAILED,
            _state_issue_detail(state["status"], state),
        )
    if state.get("status") == "rejected_archived":
        return PipelineJobOutcome(
            PipelineJobStatus.CANCELLED,
            _state_issue_detail("rejected_archived", state),
        )
    # The card renderer persists before the detail writer runs. Save the final
    # package again so its detail article and source links reach the review worker.
    if state.get("package"):
        final_package = ContentPackage.model_validate(state["package"])
        repository.persist_generated_content(
            final_package,
            "generated_pending_review",
        )
    _enqueue_review(repository, job, state)


def _process_review(repository: AgentRepository, job: PipelineJob) -> PipelineJobOutcome | None:
    request_id = job.request_id
    if not request_id:
        raise ValueError("review job is missing request_id")
    package = repository.load_generated_draft(request_id).model_dump(mode="json")
    content_type = str(job.payload.get("content_type") or package.get("content_type") or "")
    if content_type == "news":
        content_type = infer_period_content_type(ContentPackage.model_validate(package)) or content_type
    dated = ContentPackage.model_validate(package)
    dated.content_type = content_type
    attach_source_schedule(dated, primary_source_schedule(dated, content_type))
    package = dated.model_dump(mode="json")
    hold = None
    if content_type == "news":
        hold = _hold_immediate_news_package(repository, package, request_id)
    elif content_type in {"event", "goods"}:
        hold = _hold_period_package(repository, package, content_type, request_id)
    if hold:
        repository.set_content_status(request_id, hold)
        return PipelineJobOutcome(PipelineJobStatus.CANCELLED, hold)
    if content_type != str(job.payload.get("content_type") or ""):
        package["content_type"] = content_type
        updated_job_payload = dict(job.payload)
        updated_job_payload["content_type"] = content_type
        job = replace(job, payload=updated_job_payload)
    state = _state_for_job(job, package)
    reviewed = build_reviewer_agent().invoke(state)
    state.update(reviewed)
    route = route_review(state)
    if route == "feed_queue":
        state.update(feed_queue(state))
    elif route == "crawler_update_required":
        state.update(crawler_update_required(state))
        return PipelineJobOutcome(PipelineJobStatus.RETURNED, _crawler_return_detail(state))
    elif route == "archive_rejected":
        state.update(archive_rejected(state))
    elif route == "revision_checkpoint":
        state.update(revision_checkpoint(state))
        revision = int(state.get("revision_count", 0))
        payload = {
            **{key: job.payload[key] for key in ("repair_id", "repair_round") if key in job.payload},
            "content_type": state.get("requested_content_type"),
            "content_kind": state.get("content_kind", "information"),
            "needs_revision": True,
            "revision_count": revision,
            "review_failures": int(state.get("review_failures", 0)),
            "max_revision": int(state.get("max_revision", POLICY.max_review_failures)),
            "revision_owner": state.get("revision_owner", ""),
            "revision_owners": list(state.get("revision_owners", [])),
            "qa_report": list(state.get("qa_report", [])),
            "usage_records": list(state.get("usage_records", [])),
            "trace": list(state.get("trace", [])),
        }
        repository.enqueue_pipeline_job(
            lane=job.lane,
            stage=PipelineStage.GENERATE,
            idempotency_key=_revision_job_key(job, "generate", revision),
            request_id=request_id,
            payload=payload,
        )


def run_queue_worker(
    *,
    stage: PipelineStage | str,
    lane: PipelineLane | str = PipelineLane.TIME_SENSITIVE,
    idle_sleep_seconds: float = 3,
    stop_requested: Callable[[], bool] = lambda: False,
) -> None:
    """Consume one queue stage until the caller requests shutdown."""
    stage = PipelineStage(stage)
    if stage == PipelineStage.CRAWL:
        raise ValueError("Use run_crawler_worker to consume discovery and crawler returns")
    lane = PipelineLane(lane)
    worker_id = f"{stage.value}-{uuid.uuid4()}"
    repository = AgentRepository()
    repository.initialize()
    processor = _process_generation if stage == PipelineStage.GENERATE else _process_review
    while not stop_requested():
        job = repository.claim_pipeline_job(
            stage=stage,
            lane=lane,
            worker_id=worker_id,
            lease_seconds=LEASE_SECONDS,
        )
        if job is None:
            time.sleep(max(0.2, idle_sleep_seconds))
            continue
        job_content_type = str(job.payload.get("content_type") or "")
        if job_content_type not in TIME_SENSITIVE_TYPES:
            repository.finish_pipeline_job(
                job.job_id,
                worker_id=worker_id,
                status=PipelineJobStatus.CANCELLED,
                error=f"content_type is outside the time-sensitive lane: {job_content_type or 'missing'}",
                ignore_lost_lease=True,
            )
            continue
        stopped, lease_lost, heartbeat = _lease_heartbeat(repository, job, worker_id)
        try:
            outcome = processor(repository, job)
            if lease_lost.is_set():
                continue
            repository.finish_pipeline_job(
                job.job_id,
                worker_id=worker_id,
                status=outcome.status if outcome else PipelineJobStatus.COMPLETED,
                error=outcome.detail if outcome else None,
                ignore_lost_lease=True,
            )
        except UnconfirmedPeriodWorkError:
            repository.finish_pipeline_job(
                job.job_id, worker_id=worker_id, status=PipelineJobStatus.CANCELLED,
                error="work_id_unresolved_skipped", draft_status="work_id_unresolved_skipped",
                ignore_lost_lease=True,
            )
        except ExpiredFeedContentError:
            if lease_lost.is_set():
                continue
            if stage == PipelineStage.REVIEW and job.request_id:
                repository.set_content_status(job.request_id, "stale_candidate_skipped")
            repository.finish_pipeline_job(
                job.job_id,
                worker_id=worker_id,
                status=PipelineJobStatus.CANCELLED,
                error="stale_source_sale_ended",
                ignore_lost_lease=True,
            )
            print(
                f"{stage.value} skipped request_id={job.request_id} reason=source_sale_ended",
                flush=True,
            )
        except Exception as exc:
            status = (
                PipelineJobStatus.RETRY
                if job.attempts < MAX_JOB_ATTEMPTS
                else PipelineJobStatus.FAILED
            )
            repository.finish_pipeline_job(
                job.job_id,
                worker_id=worker_id,
                status=status,
                error=f"{type(exc).__name__}: {exc}"[:1000],
                retry_after_seconds=min(300, 5 * (2 ** max(0, job.attempts - 1))),
                ignore_lost_lease=True,
            )
        finally:
            stopped.set()
            heartbeat.join(timeout=2)


def _process_period_repair(
    repository: AgentRepository,
    job: PipelineJob,
    worker_id: str,
    stop_requested: Callable[[], bool],
) -> tuple[str, str]:
    from ani_menu_agent.cli import recollect_period_evidence
    from ani_menu_agent.sources.period_images import compact_image_exclusions

    if (
        job.attempts > MAX_RECOLLECTION_ATTEMPTS
        or int(job.payload.get("repair_round", 0)) >= POLICY.max_candidate_retries
    ):
        return "cancelled", "period_repair_limit_reached"
    record = repository.period_repair_return(job)
    issues = record.get("issues", [])
    if not any(issue.get("code") in REPAIRABLE_ISSUES for issue in issues):
        return "cancelled", "period_return_requires_other_evidence"
    content_type = record.get("content_type") or job.payload["content_type"]
    original = repository.load_content_package(
        job.request_id, "information", content_type=content_type, for_crawler_repair=True
    )
    # Older returns did not record their baseline. Compare the durable inputs before the refetch.
    baseline = record.get("evidence_snapshot") or evidence_snapshot(original)
    if stop_requested():
        raise InterruptedError("Crawler stop requested")
    package, capture = recollect_period_evidence(
        repository, original, stop_requested=stop_requested
    )
    if stop_requested():
        raise InterruptedError("Crawler stop requested")
    observations = compact_image_exclusions(capture)
    if observations or job.payload.get("image_exclusions"):
        repository.record_repair_image_exclusions(
            job, worker_id, observations.get("image_exclusions", [])
        )
    if not has_repaired_evidence(package, baseline, issues):
        return "retry", "no_new_usable_evidence"
    duplicate = repository.find_duplicate_content(package, exclude_returned_request=True)
    if duplicate:
        return "cancelled", "duplicate_evidence"
    payload = {
        "content_type": content_type,
        "content_kind": "information",
        "revision_count": int(record.get("revision", job.payload.get("revision_count", 0))),
        "max_revision": job.payload.get("max_revision", POLICY.max_review_failures),
        "review_failures": record.get("review_failures", job.payload.get("review_failures", 0)),
        "usage_records": record.get("usage_records", job.payload.get("usage_records", [])),
        "trace": [*record.get("trace", job.payload.get("trace", [])), "crawler_evidence_repaired"],
        "repair_id": job.job_id,
        "repair_round": int(job.payload.get("repair_round", 0)) + 1,
        **observations,
    }
    if repository.publish_period_repair(job, worker_id, package, capture, payload):
        return "completed", "new_evidence_queued"
    return "cancelled", "period_return_superseded"


def _drain_period_repair(
    repository: AgentRepository,
    content_type: str,
    worker_id: str,
    stop_requested: Callable[[], bool] = lambda: False,
) -> None:
    repository.schedule_period_repair(content_type)
    job = repository.claim_pipeline_job(
        stage="crawl",
        lane="time_sensitive",
        worker_id=worker_id,
        lease_seconds=LEASE_SECONDS,
        repair_content_type=content_type,
    )
    if job is None:
        return
    stopped, lost, heartbeat = _lease_heartbeat(repository, job, worker_id)
    try:
        try:
            status, reason = _process_period_repair(
                repository, job, worker_id, lambda: lost.is_set() or stop_requested()
            )
        except InterruptedError:
            repository.release_period_repair(job, worker_id)
            return
        except UnconfirmedPeriodWorkError:
            status, reason = "cancelled", "work_id_unresolved_skipped"
        except ExpiredFeedContentError:
            status, reason = "cancelled", "stale_candidate_skipped"
        except Exception as exc:
            status, reason = "retry", f"{type(exc).__name__}: {exc}"[:500]
        if status == "retry" and job.attempts >= MAX_RECOLLECTION_ATTEMPTS:
            status = "cancelled"
        if status != "completed" and not lost.is_set():
            repository.finish_pipeline_job(
                job.job_id,
                worker_id=worker_id,
                status=status,
                error=reason,
                ignore_lost_lease=True,
                retry_after_seconds=300 * (2 ** min(job.attempts - 1, 2))
                if status == "retry"
                else 0,
            )
        print(
            f"crawler repair type={content_type} request_id={job.request_id} status={status} reason={reason}",
            flush=True,
        )
    finally:
        stopped.set()
        heartbeat.join(timeout=2)


def run_crawler_worker(
    *,
    content_types: tuple[str, ...],
    scan_interval_seconds: int = 300,
    batch_limit: int = 20,
    stop_requested: Callable[[], bool] = lambda: False,
) -> None:
    """Persist full listing scans, then drain stored candidates in bounded chunks."""
    invalid_types = set(content_types) - TIME_SENSITIVE_TYPES
    if invalid_types:
        raise ValueError(
            "period crawler only accepts news/event/goods, got: "
            + ", ".join(sorted(invalid_types))
        )
    # Import lazily so queue-only workers do not initialize crawler integrations.
    from ani_menu_agent.cli import crawl_type
    from ani_menu_agent.agents.crawlers import get_type_crawler

    repository = AgentRepository()
    repository.initialize()
    llm_retry_after = 0.0
    next_scan_at = dict.fromkeys(content_types, 0.0)
    catalog_refresh_at = 0.0
    pending_types = set(content_types)
    worker_id = f"crawl-{uuid.uuid4()}"
    # One candidate per type per turn. The listing limit still controls how
    # many leads are collected, and unprocessed leads remain in the database.
    turn_limit = 1 if len(next_scan_at) > 1 else batch_limit
    while not stop_requested():
        if time.monotonic() >= catalog_refresh_at:
            refresh_work_catalog_caches()
            catalog_refresh_at = time.monotonic() + max(1, scan_interval_seconds)
        for content_type in next_scan_at:
            if stop_requested():
                return
            scanning = time.monotonic() >= next_scan_at[content_type]
            if scanning:
                try:
                    run = get_type_crawler(content_type).run(
                        limit=min(max(batch_limit, 20), 200),
                        region_policy="mixed",
                        capture_images=False,
                        exhaustive_listing=False,
                        retain_fetched_candidates=True,
                    )
                    print(
                        f"crawler discovery type={content_type} "
                        f"found={len(run.items)} persisted={run.persisted_count} "
                        f"errors={len(run.errors)}",
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"crawler discovery failed type={content_type}: "
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                        flush=True,
                    )
                next_scan_at[content_type] = time.monotonic() + max(1, scan_interval_seconds)
                pending_types.add(content_type)
            if stop_requested():
                return
            if scanning:
                try:
                    _drain_period_repair(repository, content_type, worker_id, stop_requested)
                except Exception as exc:
                    print(f"crawler repair failed type={content_type}: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                if stop_requested():
                    return
            if content_type not in pending_types or time.monotonic() < llm_retry_after:
                continue
            try:
                summary = crawl_type(
                    content_type=content_type,
                    limit=turn_limit,
                    target_passed=None,
                    retry_candidate_id=None,
                    retry_note=None,
                    retry_prior_failures=False,
                    retry_work_identity_failures=False,
                    stored_candidates_only=True,
                    retry_before=None,
                    region_policy="mixed",
                    output_dir=Path("output/pipeline_runs"),
                    enqueue_only=True,
                    discover_sources=scanning,
                )
            except Exception as exc:
                print(
                    f"crawler candidate batch failed type={content_type}: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    flush=True,
                )
                pending_types.discard(content_type)
                continue

            if not (summary.get("funnel") or {}).get("attempted"):
                pending_types.discard(content_type)
            error_text = str(summary).lower()
            if any(marker in error_text for marker in (
                "credit_balance_exhausted", "insufficient_quota", "no credits remaining"
            )):
                llm_retry_after = time.monotonic() + 3600
                print(
                    "crawler LLM work paused for 60 minutes after API quota "
                    "exhaustion; source discovery will continue",
                    flush=True,
                )
        if not pending_types or time.monotonic() < llm_retry_after:
            time.sleep(1)
