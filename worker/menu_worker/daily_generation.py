"""Finite, sequential daily generation; delivery envelopes never publish service content."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from uuid import UUID

from menu_worker.bounded_generation import Controller, StopRun, read_json, write_json
from menu_worker.bounded_resume import digest, require
from menu_worker.daily_allocation import article_identity, route_key, run_allocated
from menu_worker.daily_limits import effective_daily_attempt_limit, effective_total_attempt_limit, record_limit_metadata

KST = timezone(timedelta(hours=9))
MAX_DAILY_ATTEMPTS = 40
DAILY_SUCCESS_TARGET = 20
SAFE_DAY_STOPS = frozenset({"deadline_reached", "insufficient_time_for_new_job", "estimated_budget_reserve_unavailable"})
DAILY_COMPLETIONS = frozenset({"daily_success_target_reached", "daily_attempt_limit_reached"})


def successful_records(state):
    start = state["daily_policy"]["start_record_index"]
    return [r for r in state["records"][start:] if r.get("counts_slot") and r.get("state") == "complete"
            and r.get("execution_status") == "succeeded" and r.get("outcome") == "ready_for_staging"]


def verify_bundle(bundle_root, record, job, result):
    """Recover only an exact copy of the already sealed export, never regenerate it."""
    root = Path(bundle_root)
    job_id = str(job["job_id"])
    require(str(UUID(job_id)) == job_id, "invalid_delivery_job_id")
    path = root / job_id / "bundle.json"
    require(root.is_absolute() and root.resolve() == root and path.resolve() == path, "unsafe_delivery_bundle")
    if record.get("bundle_path"):
        require(Path(record["bundle_path"]) == path, "delivery_bundle_path_changed")
    document = read_json(path)
    package = result["package"]
    assets = [{"page": card["page"], "path": f"cards/card_{card['page']:02d}.png",
               "sha256": card["rendered_image_key"]} for card in package["cards"]]
    evidence = {key: result[key] for key in (
        "status", "review_mode", "editorial_decision", "quality_report", "qa_report", "language_review",
        "usage_records", "cost_accounting", "stage_timings", "stage_timings_seconds", "operator_correction",
    ) if key in result}
    evidence.update(jobId=job_id, generationAttemptId=job["generation_attempt_id"],
                    resultSha256=job["result_sha256"], operatorReviewCreated=False)
    expected = {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime",
                "items": [{"package": package, "assets": assets, "reviewEvidence": evidence}]}
    require(document == expected, "delivery_bundle_evidence_changed")
    for asset in assets:
        image = path.parent / asset["path"]
        require(image.resolve() == image and image.is_file() and image.stat().st_size <= 20 * 1024 * 1024
                and hashlib.sha256(image.read_bytes()).hexdigest() == asset["sha256"], "delivery_bundle_image_changed")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class DailyController(Controller):
    def budget_limit(self):
        policy = self.state["daily_policy"]
        if policy.get("schema_version") in {3, 4}:
            require(policy.get("limit_mode") == "daily_counts"
                    and self.config.get("limit_mode") == "daily_counts"
                    and self.config.get("total_budget_usd") is None,
                    "invalid_daily_count_limits")
            return None
        return super().budget_limit()

    def record_metadata(self):
        return {"policy_date": self.state["daily_policy"]["policy_date"],
                "day_policy_sha256": digest(self.state["daily_policy"]), **record_limit_metadata(self.state)}

    def completion_reason(self):
        successes = {article_identity(r) for r in successful_records(self.state)}
        if len(successes) >= DAILY_SUCCESS_TARGET:
            return "daily_success_target_reached"
        limit = effective_total_attempt_limit(self.state)
        if limit is not None and self.used_slots() >= limit:
            return "daily_attempt_limit_reached"
        return None

    def check(self):
        super().check()
        if datetime.fromtimestamp(self.now(), KST).date().isoformat() != self.state["daily_policy"]["policy_date"]:
            self.stop("deadline_reached")

    def _emit_delivery(self, record):
        self.lock_check()
        job = self.store.get(record["job_id"])
        require(job is not None and job.get("execution_status") == "succeeded"
                and job.get("outcome") == "ready_for_staging"
                and job.get("result_sha256") == record.get("result_sha256")
                and job.get("generation_attempt_id") == record.get("generation_attempt_id"), "delivery_job_changed")
        result = self.read_result(job)
        path, bundle_sha = verify_bundle(self.bundles, record, job, result)
        # A restored file is not a substitute for the article-level DB fence.
        # The same selection is idempotent; another selected result must block.
        self.store.select_staging_result(job)
        policy = self.state["daily_policy"]
        inbox = Path(policy["delivery_root"])
        require(inbox.is_dir() and inbox.resolve() == inbox, "unsafe_delivery_root")
        destination = inbox / (job["job_id"] + ".json")
        envelope = {"schema_version": 1, "job_id": job["job_id"],
            "generation_attempt_id": job["generation_attempt_id"], "result_sha256": job["result_sha256"],
            "bundle_path": path.relative_to(self.bundles).as_posix(), "bundle_sha256": bundle_sha,
            "source_id": self.config["source_id"], "canonical_url": record["canonical_url"],
            "content_type": self.config["content_type"], "policy_date": policy["policy_date"],
            "run_id": self.config["run_id"], "day_policy_sha256": digest(policy)}
        if policy.get("schema_version") in {2, 3, 4}:
            request = record["request"]
            require(route_key(request) in {route_key(route) for route in policy["routes"]}
                    and route_key(job) == route_key(request)
                    and job.get("canonical_url") == record["canonical_url"], "delivery_job_scope_changed")
            envelope.update(schema_version=2, source_id=request["source_id"],
                            content_type=request["content_type"], menu_work_id=request["menu_work_id"])
        if destination.exists():
            require(read_json(destination) == envelope, "delivery_envelope_changed")
        else:
            write_json(destination, envelope)
        metadata = dict(delivery_envelope_path=str(destination), delivery_envelope_sha256=digest(envelope))
        if any(record.get(key) != value for key, value in metadata.items()):
            record.update(metadata)
            self.save()

    def finish_job(self, record):
        # Export selects and writes the immutable bundle before checkpoint fsync.
        # If the controller died in that gap, verify it rather than overwrite it.
        if not record.get("bundle_path") and (self.bundles / record["job_id"]).exists():
            job = self.store.get(record["job_id"])
            require(job is not None and job.get("execution_status") == "succeeded"
                    and job.get("outcome") == "ready_for_staging", "uncheckpointed_bundle_job_mismatch")
            path, _ = verify_bundle(self.bundles, record, job, self.read_result(job))
            self.store.select_staging_result(job)
            record["bundle_path"] = str(path)
            self.save()
        super().finish_job(record)
        if record.get("outcome") == "ready_for_staging":
            self._emit_delivery(record)

    def validate_run_history(self):
        from menu_worker.daily_limits import validate_limit_revisions
        validate_limit_revisions(self.state)
        from menu_worker.failure_recovery import validate_recovery_history
        validate_recovery_history(self.state, store=self.store, artifact_root=Path(self.config["artifact_root"]))
        if self.state.get("stop_reason") == "failure_reconciled_paused":
            return False
        from menu_worker.daily_resume import current_failure_references, validate_failure_resume
        refs = current_failure_references(self.state)
        if refs and self.state.get("status") == "running":
            validate_failure_resume(self.state, refs[-1]["receipt_sha256"], store=self.store, now=self.now())
        return True

    def run(self):
        if self.state.get("rag_scope"):
            raise StopRun("mixed_checkpoint_requires_rag_controller")
        if not self.validate_run_history():
            return self.summary()
        # Completion may have been fsynced immediately before an envelope write.
        # The envelope is deterministic and the importer is idempotent per job.
        for record in successful_records(self.state):
            self._emit_delivery(record)
        if self.state["daily_policy"].get("schema_version") in {2, 3, 4}:
            return run_allocated(self)
        return super().run()

    def summary(self):
        policy = self.state["daily_policy"]
        today = self.state["records"][policy["start_record_index"]:]
        unknown = set(self.unknown_cost_jobs())
        daily_unknown = [row["job_id"] for row in today if row.get("job_id") in unknown]
        subtotal = str(self.spent() - Decimal(policy["prior_accounted_usd"]))
        return {**super().summary(), "policy_date": policy["policy_date"], "day_policy_sha256": digest(policy),
                "daily_attempts": sum(bool(r.get("counts_slot")) for r in today),
                "daily_successes": len({article_identity(r) for r in successful_records(self.state)}),
                "daily_deliveries": sum(bool(r.get("delivery_envelope_path")) for r in today),
                "daily_attempt_limit": effective_daily_attempt_limit(self.state),
                "max_new_jobs": effective_total_attempt_limit(self.state), "daily_success_target": DAILY_SUCCESS_TARGET,
                "daily_spent_usd": None if daily_unknown else subtotal,
                "daily_known_and_reserved_subtotal_usd": subtotal,
                "daily_unknown_cost_jobs": daily_unknown,
                "daily_budget_usd": policy.get("daily_budget_usd"),
                "limit_mode": policy.get("limit_mode", "daily_budget_and_counts"),
                "automatic_service_publication": False,
                "route_allocation": [{**route,
                    "attempts": sum(bool(row.get("counts_slot")) for row in today if route_key(row["request"]) == route_key(route)),
                    "successes": sum(1 for row in successful_records(self.state) if route_key(row["request"]) == route_key(route))}
                    for route in policy.get("routes", [])]}
