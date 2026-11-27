"""Opt-in RAG admissions inside the existing, locked daily controller.

This module does not start a worker or activate a schedule. The caller owns the
same local/database controller locks as the period-only daily entry point. A
reviewed scope is pinned in the checkpoint, while all previous records remain
byte-for-byte values. Both producers share the same reviewed daily limits.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from menu_worker.bounded_generation import StopRun, result_cost, money
from menu_worker.bounded_resume import digest, require
from menu_worker.daily_allocation import (
    allocation_key, article_identity, collect_round, ready_for_new_job, seen_articles,
)
from menu_worker.daily_generation import DailyController, successful_records
from menu_worker.rag_contract import RagRequest


def rag_route(request):
    return {"source_id": "rag_" + request.namespace, "content_type": request.content_type,
            "menu_work_id": request.menu_work_id}


def validate_scope(scope, *, current_catalog=False):
    require(isinstance(scope, dict) and set(scope) == {"schema_version", "purpose", "routes"}
            and scope["schema_version"] == 1 and scope["purpose"] == "rag_daily_generation",
            "invalid_rag_daily_scope")
    routes = scope["routes"]
    require(isinstance(routes, list) and 1 <= len(routes) <= 200, "invalid_rag_daily_scope")
    keys = set()
    for route in routes:
        require(isinstance(route, dict) and set(route) == {
            "namespace", "menu_work_id", "content_type", "allowed_source_ids"}, "invalid_rag_daily_route")
        # Reuse the strict admission contract rather than a weaker route parser.
        request = RagRequest.parse({**route, "work_id": "scope-validation", "query": "scope validation",
            "seed_evidence": [{"document_id": "scope-validation", "content_sha256": "0" * 64}]})
        key = request.namespace, request.menu_work_id, request.content_type
        require(key not in keys, "duplicate_rag_daily_route")
        keys.add(key)
    if current_catalog:
        import csv
        from menu_worker.anime_catalog import CATALOG_ROOT
        try:
            with (CATALOG_ROOT / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
                ids = [int(row["menu_work_id"]) for row in csv.DictReader(stream)]
            require(ids and len(ids) == len(set(ids)) and all(value > 0 for value in ids),
                    "invalid_rag_service_catalog")
        except (OSError, ValueError, KeyError, csv.Error):
            raise StopRun("rag_service_catalog_unavailable") from None
        require(all(route["menu_work_id"] in ids for route in routes), "rag_work_not_in_service_catalog")
    return deepcopy(scope)


def pin_scope(controller, scope, *, reviewed_sha256):
    """Explicit local checkpoint preparation, never a paid activation command."""
    scope = validate_scope(scope, current_catalog=True)
    require(digest(scope) == reviewed_sha256, "rag_scope_not_reviewed")
    controller.lock_check()
    require(controller.state.get("daily_policy", {}).get("limit_mode") == "daily_counts",
            "rag_requires_shared_daily_count_policy")
    previous = controller.state.get("rag_scope")
    require(previous is None or previous == scope, "rag_daily_scope_changed")
    if previous is None:
        require(not any(row["state"] not in {"complete", "duplicate"} for row in controller.state["records"]),
                "rag_scope_requires_idle_checkpoint")
        controller.state["rag_scope"] = scope
        controller.state["rag_scope_sha256"] = reviewed_sha256
        controller.save()


class MixedDailyController(DailyController):
    """One allocation loop; native RAG results never impersonate article results."""

    def __init__(self, *, rag_store, rag_discover, rag_publish, rag_read_result,
                 rag_export, rag_scope_sha256, **kwargs):
        super().__init__(**kwargs)
        self.rag_store, self.rag_discover, self.rag_publish = rag_store, rag_discover, rag_publish
        self.rag_read_result, self.rag_export = rag_read_result, rag_export
        scope = validate_scope(self.state.get("rag_scope"))
        require(digest(scope) == rag_scope_sha256 == self.state.get("rag_scope_sha256"),
                "rag_daily_scope_changed")
        require(self.state["daily_policy"].get("limit_mode") == "daily_counts",
                "rag_requires_shared_daily_count_policy")
        self.rag_scope = scope

    def known_spent(self):
        return super().known_spent() + sum((money(row["rag_known_partial_usd"])
            for row in self.state["records"] if row.get("rag_known_partial_usd") is not None), Decimal(0))

    def unknown_cost_jobs(self):
        return sorted(set(super().unknown_cost_jobs()) | {
            row["job_id"] for row in self.state["records"] if row.get("rag_cost_incomplete")})

    def summary(self):
        report = super().summary()
        today = self.state["records"][self.state["daily_policy"]["start_record_index"]:]
        report["daily_deliveries"] += sum(bool(row.get("rag_delivery")) for row in today)
        report["rag_route_allocation"] = [{**route,
            "attempts": sum(bool(row.get("counts_slot")) for row in today
                            if row.get("producer") == "ani_rag"
                            and self._request(row).namespace == route["namespace"]
                            and row["request"]["menu_work_id"] == route["menu_work_id"]
                            and row["request"]["content_type"] == route["content_type"]),
            "successes": sum(1 for row in successful_records(self.state)
                             if row.get("producer") == "ani_rag"
                             and self._request(row).namespace == route["namespace"]
                             and row["request"]["menu_work_id"] == route["menu_work_id"]
                             and row["request"]["content_type"] == route["content_type"])}
            for route in self.rag_scope["routes"]]
        return report

    def _request(self, record):
        request = RagRequest.parse(record["request"]["rag_request"])
        require(record["canonical_url"] == "urn:menu-rag:" + request.identity_key,
                "rag_record_identity_changed")
        return request

    def collect_rag(self):
        candidates = []
        seen = seen_articles(self.state)
        for route in self.rag_scope["routes"]:
            ready_for_new_job(self)
            excluded = {seed["document_id"] for row in self.state["records"]
                        if row.get("producer") == "ani_rag"
                        for seed in row["request"]["rag_request"]["seed_evidence"]}
            batch = self.rag_discover(deepcopy(route), 20, excluded_document_ids=tuple(sorted(excluded)))
            require(isinstance(batch, list) and len(batch) <= 20, "invalid_rag_candidate_batch")
            for raw in batch:
                request = RagRequest.parse(raw)
                require(request.namespace == route["namespace"]
                        and request.content_type == route["content_type"]
                        and request.menu_work_id == route["menu_work_id"]
                        and set(request.allowed_source_ids) <= set(route["allowed_source_ids"]),
                        "rag_discovery_scope_mismatch")
                identity = ("ani_rag", request.identity_key)
                if identity in seen:
                    continue
                candidates.append({**rag_route(request), "producer": "ani_rag",
                    "rag_request": request.as_dict(), "idempotency_key": self.config["run_id"] + ":rag:" + request.identity_key,
                    "canonical_url": "urn:menu-rag:" + request.identity_key})
                seen.add(identity)
        return candidates

    def _emit_delivery(self, record):
        if record.get("producer") != "ani_rag":
            return super()._emit_delivery(record)
        # This adapter returns a native RAG staging envelope. The RAG importer
        # performs its own multi-document validation before operator-review DB writes.
        require(record.get("outcome") == "ready_for_staging", "rag_delivery_not_reviewed")
        exported = self.rag_export(self.rag_store.get(record["job_id"]), self.bundles / record["job_id"])
        require(isinstance(exported, dict) and exported.get("producer") == "ani_rag"
                and exported.get("job_id") == record["job_id"]
                and exported.get("generation_attempt_id") == record["generation_attempt_id"]
                and exported.get("result_sha256") == record["result_sha256"], "rag_delivery_binding_changed")
        if record.get("rag_delivery") is not None:
            require(record["rag_delivery"] == exported, "rag_delivery_changed")
        else:
            record["rag_delivery"] = deepcopy(exported)
            self.save()

    def finish_job(self, record):
        if record.get("producer") != "ani_rag":
            return super().finish_job(record)
        self._request(record)
        while True:
            self.check()
            job = self.rag_store.get(record["job_id"])
            require(job is not None and job["input_sha256"] == record["rag_input_sha256"],
                    "rag_owned_job_changed")
            if job["execution_status"] in {"succeeded", "failed", "needs_attention", "cancelled"}:
                break
            if self.now() - record.get("published_at", record["reserved_at"]) >= self.config["job_timeout_seconds"]:
                self.stop("rag_job_wait_timeout")
            self.sleep(3)
        if (job["execution_status"] == "failed" and job.get("error_code") == "rag_preflight_failed"
                and job.get("cost_complete") is True and job.get("usage_records") == []):
            # Validate before copying any fields: contradictory checkpoint cost
            # or result evidence must never be overwritten with a guessed zero.
            from menu_worker.rag_preflight_failure import complete_zero_preflight_record
            try:
                completed = complete_zero_preflight_record(self.state, record, job,
                    artifact_root=self.config["artifact_root"])
            except StopRun:
                self.stop("rag_generation_requires_attention")
            record.clear()
            record.update(completed)
            self.save()
            return
        record.update(execution_status=job["execution_status"], outcome=job.get("outcome"),
                      producer_status=job.get("producer_status"),
                      generation_attempt_id=job.get("generation_attempt_id"), result_sha256=job.get("result_sha256"))
        self.save()
        if job["execution_status"] != "succeeded":
            # A failed provider/parser/renderer can still have paid usage. Keep
            # the jobs ledger intact and expose unknown totals before stopping.
            usage = deepcopy(job.get("usage_records", []))
            self._record_usage(record, {"usage_records": usage,
                "llm_calls": len(usage) if job.get("cost_complete") is True else None,
                "cost_accounting": {"complete": job.get("cost_complete") is True}})
            self.stop("rag_generation_requires_attention")
        try:
            result = self.rag_read_result(job)
            require(result.get("producer") == "ani_rag", "rag_result_producer_mismatch")
        except Exception:
            record["rag_result_verification_failed"] = True
            # A damaged/missing seal must not make already recorded provider
            # charges disappear from the daily report. Total remains unknown.
            self._record_usage(record, {"usage_records": deepcopy(job.get("usage_records", [])),
                                       "cost_accounting": {"complete": False}})
            raise  # _record_usage stops; retain the fail-closed fallback.
        self._record_usage(record, result)
        require(record["outcome"] in {"ready_for_staging", "held", "rejected", "skipped"},
                "rag_invalid_terminal_outcome")
        if record["outcome"] == "ready_for_staging":
            self._emit_delivery(record)
        record["state"] = "complete"
        self.save()

    def _record_usage(self, record, result):
        record["rag_usage_records"] = deepcopy(result.get("usage_records", []))
        try:
            record["estimated_cost_usd"] = str(result_cost(result))
        except StopRun:
            record.update(estimated_cost_usd=None, rag_cost_incomplete=True)
            known = [row.get("estimated_cost_usd") for row in result.get("usage_records", [])
                     if isinstance(row, dict) and row.get("estimated_cost_usd") is not None]
            record["rag_known_partial_usd"] = str(sum((money(value) for value in known), Decimal(0)))
            self.save()
            self.stop("rag_usage_requires_reconciliation")
        record.pop("rag_known_partial_usd", None)
        record.pop("rag_cost_incomplete", None)
        self.save()

    def handle(self, record):
        if record.get("producer") != "ani_rag":
            return super().handle(record)
        request = self._request(record)
        if not record.get("job_id"):
            try:
                submitted = self.rag_store.submit(request, record["rag_admission"])
            except Exception as error:
                if getattr(error, "code", None) != "rag_candidate_exists":
                    raise
                previous = self.rag_store.get(error.existing_job_id)
                require(previous is not None and previous["execution_status"] in {
                    "succeeded", "failed", "needs_attention", "cancelled"}, "foreign_pending_rag_candidate")
                record.update(state="duplicate", counts_slot=False, existing_job_id=previous["job_id"])
                self.save()
                return
            if not submitted["created"]:
                if submitted.get("admission_receipt") != record["rag_admission"]:
                    record.update(state="duplicate", counts_slot=False, existing_job_id=submitted["job_id"])
                    self.save()
                    return
                # Submission succeeded before a crash, but publication has not
                # been attempted yet. Recover the exact owned reservation only.
            record["job_id"] = submitted["job_id"]
            self.save()
        if not record.get("publish_intent"):
            self.check()
            record["publish_intent"] = True
            self.save()
            self.rag_publish(record["job_id"])
            record["published"] = True
            # Preserve the original quota reservation. A confirmed broker
            # response starts the processing wait; ambiguous/legacy deliveries
            # retain their original reservation-based timeout.
            record["published_at"] = self.now()
            self.save()
        self.finish_job(record)  # An ambiguous publish is observed, never sent again.

    def reserve_rag(self, fields):
        ready_for_new_job(self)
        require(self.completion_reason() is None, "daily_limit_reached")
        request = RagRequest.parse(fields["rag_request"])
        require(fields.get("producer") == "ani_rag"
                and fields.get("canonical_url") == "urn:menu-rag:" + request.identity_key
                and all(fields.get(key) == value for key, value in rag_route(request).items())
                and any(request.namespace == route["namespace"]
                        and request.menu_work_id == route["menu_work_id"]
                        and request.content_type == route["content_type"]
                        and set(request.allowed_source_ids) <= set(route["allowed_source_ids"])
                        for route in self.rag_scope["routes"]), "rag_discovery_scope_mismatch")
        record = {"producer": "ani_rag", "request": fields, "canonical_url": fields["canonical_url"],
                  "state": "reserved", "counts_slot": True, "reserved_at": self.now(), **self.record_metadata()}
        today = self.state["records"][self.state["daily_policy"]["start_record_index"]:]
        record["rag_input_sha256"] = request.input_sha256
        record["rag_admission"] = {"schema_version": 1, "reservation_id": str(uuid4()),
            "policy_date": self.state["daily_policy"]["policy_date"],
            "policy_sha256": digest({"daily": self.state["daily_policy"], "rag": self.rag_scope}),
            "input_sha256": request.input_sha256, "counts_slot": True,
            "attempt_ordinal": sum(bool(row.get("counts_slot")) for row in today) + 1}
        from menu_worker.daily_limits import effective_daily_attempt_limit
        if effective_daily_attempt_limit(self.state) is None:
            record["rag_admission"].update(schema_version=2,
                limit_revision_sha256=record.get("daily_limit_revision_sha256"))
        self.state["records"].append(record)
        self.state["seen"].append(record["canonical_url"])
        self.save()  # Count and request are durable before any queue submission.
        return record

    def validate_run_history(self):
        may_run = super().validate_run_history()
        from menu_worker.rag_daily_runtime import verify_rag_completion
        for record in self.state["records"]:
            if record.get("producer") == "ani_rag" and record.get("state") == "complete" \
                    and record.get("execution_status") == "failed":
                verify_rag_completion(record, self.rag_store, self.rag_read_result,
                    state=self.state, artifact_root=self.config["artifact_root"])
        return may_run

    def run(self):
        if not self.validate_run_history():
            return self.summary()
        for record in successful_records(self.state):
            self._emit_delivery(record)
        if self.state["status"] in {"complete", "stopped"}:
            return self.summary()
        for record in self.state["records"]:
            if record["state"] not in {"complete", "duplicate"}:
                try:
                    self.handle(record)
                except Exception as error:
                    if getattr(error, "code", None) != "candidate_exists":
                        raise
                    previous = self.store.get(error.existing_job_id)
                    require(previous is not None and previous["execution_status"] in {
                        "succeeded", "failed", "cancelled", "needs_attention"}, "foreign_pending_candidate")
                    record.update(state="duplicate", counts_slot=False, existing_job_id=previous["job_id"])
                    self.save()
        while self.completion_reason() is None:
            candidates = collect_round(self) + self.collect_rag()
            while candidates and self.completion_reason() is None:
                fields = min(candidates, key=lambda item: allocation_key(self.state, item))
                if fields.get("producer") == "ani_rag":
                    identity = ("ani_rag", RagRequest.parse(fields["rag_request"]).identity_key)
                else:
                    identity = (fields["source_id"], fields["canonical_url"])
                candidates.remove(fields)
                if identity in seen_articles(self.state):
                    continue
                if fields.get("producer") == "ani_rag":
                    record = self.reserve_rag(fields)
                else:
                    ready_for_new_job(self)
                    record = {"request": fields, "canonical_url": fields["canonical_url"], "state": "reserved",
                        "counts_slot": True, "reserved_at": self.now(), **self.record_metadata()}
                    self.state["records"].append(record)
                    self.state["seen"].append(record["canonical_url"])
                    self.save()
                try:
                    self.handle(record)
                except Exception as error:
                    if getattr(error, "code", None) != "candidate_exists":
                        raise
                    previous = self.store.get(error.existing_job_id)
                    require(previous is not None and previous["execution_status"] in {
                        "succeeded", "failed", "cancelled", "needs_attention"}, "foreign_pending_candidate")
                    record.update(state="duplicate", counts_slot=False, existing_job_id=previous["job_id"])
                    self.save()
            if self.completion_reason() is None:
                self.sleep(min(self.config["scan_interval_seconds"], max(0, self.deadline - self.now())))
        self.state.update(status="complete", stop_reason=self.completion_reason())
        self.save()
        return self.summary()
