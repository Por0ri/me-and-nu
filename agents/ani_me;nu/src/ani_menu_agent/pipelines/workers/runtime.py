"""Independent consumers for durable generation and review jobs."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
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
from ani_menu_agent.pipelines.common.jobs import (
    PipelineJob,
    PipelineJobStatus,
    PipelineLane,
    PipelineStage,
)
from ani_menu_agent.policy import POLICY
from ani_menu_agent.source_freshness import expiry_evidence, immediate_news_action_expiry
from ani_menu_agent.storage import AgentRepository

LEASE_SECONDS = 900
MAX_JOB_ATTEMPTS = 5
TIME_SENSITIVE_TYPES = {"news", "event", "goods"}


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
        idempotency_key=f"{request_id}:review:{revision}",
        request_id=request_id,
        payload=payload,
    )


def _hold_period_package(
    repository: AgentRepository, package: dict, content_type: str, request_id: str
) -> bool:
    """Persist a temporal hold and return True when the package must not proceed."""
    temporal = expiry_evidence(package, content_type)
    if temporal["status"] == "active":
        return False
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
            "evidence": temporal.get("evidence"),
            "source_urls": [str(source.url) for source in held_package.sources],
        },
        status=status,
    )
    return True


def _hold_immediate_news_package(
    repository: AgentRepository, package: dict, request_id: str
) -> bool:
    """Prevent expired or undated start-announcement news from being generated or passed."""
    temporal = immediate_news_action_expiry(package)
    if temporal["status"] not in {"expired", "unresolved"}:
        return False
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
            "evidence": temporal.get("evidence"),
            "source_urls": [str(source.url) for source in held_package.sources],
        },
        status=status,
    )
    return True


def _process_generation(repository: AgentRepository, job: PipelineJob) -> None:
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
    if not revision and content_type == "news" and _hold_immediate_news_package(
        repository, package, request_id
    ):
        return
    if not revision and content_type in {"event", "goods"}:
        temporal = expiry_evidence(package, content_type)
        if _hold_period_package(repository, package, content_type, request_id):
            return
        updated_package = ContentPackage.model_validate(package)
        if content_type == "event" and not updated_package.event_end:
            updated_package.event_end = str(temporal["end_date"])
        elif content_type == "goods" and not any(
            fact.fact_type == "sales_period" and fact.details.get("sale_end")
            for fact in updated_package.facts
        ):
            source_id = updated_package.sources[0].id if updated_package.sources else "source"
            updated_package.facts.append(
                Fact(
                    id=f"{request_id}-sales-period",
                    statement=f"판매 종료일: {temporal['end_date']}",
                    source_ids=[source_id],
                    verified=False,
                    time_sensitive=True,
                    fact_type="sales_period",
                    details={
                        "sale_end": str(temporal["end_date"]),
                        "source_quote": str(temporal.get("evidence") or ""),
                    },
                )
            )
        package = updated_package.model_dump(mode="json")
        repository.persist_information(updated_package)
    state = _state_for_job(job, package)
    if not revision:
        state.update(intake(state))
        state.update(rights_preflight(state))
        if state.get("status") == "needs_crawler_update":
            crawler_update_required(state)
            return
    generated = build_content_generation_agent().invoke(state)
    state.update(generated)
    if state.get("status") == "needs_crawler_update":
        crawler_update_required(state)
        return
    if state.get("status") in {"invalid_input", "generation_failed", "rejected_archived"}:
        return
    # The card renderer persists before the detail writer runs. Save the final
    # package again so its detail article and source links reach the review worker.
    if state.get("package"):
        final_package = ContentPackage.model_validate(state["package"])
        repository.persist_generated_content(
            final_package,
            "generated_pending_review",
        )
    _enqueue_review(repository, job, state)


def _process_review(repository: AgentRepository, job: PipelineJob) -> None:
    request_id = job.request_id
    if not request_id:
        raise ValueError("review job is missing request_id")
    package = repository.load_generated_draft(request_id).model_dump(mode="json")
    content_type = str(job.payload.get("content_type") or package.get("content_type") or "")
    if content_type == "news":
        content_type = infer_period_content_type(ContentPackage.model_validate(package)) or content_type
    if content_type == "news" and _hold_immediate_news_package(
        repository, package, request_id
    ):
        return
    if content_type in {"event", "goods"} and _hold_period_package(
        repository, package, content_type, request_id
    ):
        return
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
    elif route == "archive_rejected":
        state.update(archive_rejected(state))
    elif route == "revision_checkpoint":
        state.update(revision_checkpoint(state))
        revision = int(state.get("revision_count", 0))
        payload = {
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
            idempotency_key=f"{request_id}:generate:{revision}",
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
            )
            continue
        stopped, lease_lost, heartbeat = _lease_heartbeat(repository, job, worker_id)
        try:
            processor(repository, job)
            if lease_lost.is_set():
                continue
            repository.finish_pipeline_job(
                job.job_id, worker_id=worker_id, status=PipelineJobStatus.COMPLETED
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
    while not stop_requested():
        for content_type in content_types:
            if stop_requested():
                break
            try:
                run = get_type_crawler(content_type).run(
                    # Keep continuous polling focused on each source's newest
                    # listing entries. Exhaustive pagination walks deep into
                    # archives on every cycle and repeatedly discovers expired
                    # campaigns and old announcements.
                    limit=min(max(batch_limit, 20), 200),
                    region_policy="kr_first",
                    capture_images=False,
                    exhaustive_listing=False,
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
                continue

            # The batch size bounds one call, not the total backlog. Keep
            # processing durable leads until none remain, while generation and
            # review stay in their independent queue workers.
            while not stop_requested() and time.monotonic() >= llm_retry_after:
                try:
                    summary = crawl_type(
                        content_type=content_type,
                        limit=batch_limit,
                        target_passed=None,
                        retry_candidate_id=None,
                        retry_note=None,
                        retry_prior_failures=True,
                        retry_work_identity_failures=True,
                        stored_candidates_only=True,
                        retry_before=None,
                        region_policy="kr_first",
                        output_dir=Path("output/pipeline_runs"),
                        enqueue_only=True,
                    )
                except Exception as exc:
                    print(
                        f"crawler candidate batch failed type={content_type}: "
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                        flush=True,
                    )
                    break

                attempted = int((summary.get("funnel") or {}).get("attempted", 0))
                if attempted == 0:
                    break
                error_text = str(summary).lower()
                if any(
                    marker in error_text
                    for marker in (
                        "credit_balance_exhausted",
                        "insufficient_quota",
                        "no credits remaining",
                    )
                ):
                    # Keep collecting source listings while API-backed matching
                    # is unavailable. Avoid repeating quota-failing requests.
                    llm_retry_after = time.monotonic() + 3600
                    print(
                        "crawler LLM work paused for 60 minutes after API quota "
                        "exhaustion; source discovery will continue",
                        flush=True,
                    )
                    break
        for _ in range(max(1, scan_interval_seconds)):
            if stop_requested():
                break
            time.sleep(1)
