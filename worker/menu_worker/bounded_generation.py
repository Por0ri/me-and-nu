"""Finite Celery controller, carried forward from the audited beta run.

Use menu_worker.bounded_resume for explicit plan/apply entry points.
This module never starts a Worker or calls a model/service DB.

Budget figures are estimated spending targets, not a provider billing hard cap.
Only this controller may publish into its dedicated generation broker/database.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

TERMINAL = {"succeeded", "failed", "cancelled", "needs_attention"}
APPROVED_RESERVATION = {
    "job_id": "83eaaf72-4402-4458-a35f-a28935d0fb03",
    "generation_attempt_id": "0e3f7207-cdda-4089-ac4c-3e9a7bcdb1ef",
    "source_result_sha256": "33cd7b72a2ee681b0588e95a86ace8dd6ef912827c0d748cf61aa609bb1c8450",
    "producer_status": "content_type_mismatch_skipped",
}


class StopRun(RuntimeError):
    """Fixed, credential-free reason, safe to print."""


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise StopRun("invalid_cost_evidence") from None


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise StopRun("timezone_required")
    return parsed.timestamp()


def read_json(path):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise StopRun("duplicate_json_key")
        return value
    if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise StopRun("unsafe_json_file")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(StopRun("invalid_json_number")))


def write_json(path, value):
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise StopRun("unsafe_checkpoint_path")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def result_cost(result):
    records = result.get("usage_records")
    summary = result.get("usage_summary") or {}
    accounting = result.get("cost_accounting") or {}
    if not isinstance(summary, dict) or not isinstance(accounting, dict):
        raise StopRun("invalid_cost_evidence")
    if accounting.get("complete") is False or accounting.get("cost_complete") is False:
        raise StopRun("incomplete_cost_evidence")
    if records is None or records == []:
        explicit_zero = (summary.get("llm_calls") == 0 or result.get("llm_calls") == 0)
        if not explicit_zero:
            raise StopRun("missing_cost_evidence")
        total = Decimal(0)
    elif isinstance(records, list):
        total = Decimal(0)
        for row in records:
            if not isinstance(row, dict) or "estimated_cost_usd" not in row:
                raise StopRun("missing_cost_evidence")
            total += money(row["estimated_cost_usd"])
        if "llm_calls" in summary and summary["llm_calls"] != len(records):
            raise StopRun("inconsistent_cost_evidence")
    else:
        raise StopRun("invalid_cost_evidence")
    for source in (summary, accounting):
        for field in ("estimated_cost_usd", "total_estimated_cost_usd"):
            if field in source and abs(money(source[field]) - total) > Decimal("0.00000002"):
                raise StopRun("inconsistent_cost_evidence")
    if total == 0 and any(money(summary.get(k, 0)) > 0 for k in ("total_tokens", "input_tokens", "output_tokens")):
        raise StopRun("zero_cost_with_paid_tokens")
    return total.quantize(Decimal("0.00000001"))


def validate_reservations(document):
    """Only the explicitly reviewed 83388 omission may reserve one dollar."""
    if (not isinstance(document, dict) or set(document) != {"schema_version", "records"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["records"], list) or len(document["records"]) != 1):
        raise StopRun("invalid_accounting_reservation")
    row = document["records"][0]
    fields = {*APPROVED_RESERVATION, "accounting_method", "measured_cost", "reserved_usd", "reason"}
    if (not isinstance(row, dict) or set(row) != fields
            or any(row.get(key) != value for key, value in APPROVED_RESERVATION.items())
            or row["accounting_method"] != "reserve" or row["measured_cost"] is not None
            or not isinstance(row["reserved_usd"], str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,8})?", row["reserved_usd"])
            or money(row["reserved_usd"]) != Decimal("1.00")
            or not isinstance(row["reason"], str) or not 10 <= len(row["reason"].strip()) <= 1000):
        raise StopRun("invalid_accounting_reservation")
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {row["job_id"]: {**row, "receipt_sha256": digest}}


@contextmanager
def local_lock(path):
    lock = path.with_name(path.name + ".lock")
    if lock.is_symlink():
        raise StopRun("unsafe_checkpoint_lock")
    with lock.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StopRun("controller_already_running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def database_lock(store, run_id):
    with store._connect() as connection:
        connection.commit()
        connection.autocommit = True
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 16)) AS acquired", (f"bounded-beta:{run_id}",)
        ).fetchone()["acquired"]
        if not acquired:
            raise StopRun("controller_already_running")
        def check():
            connection.execute("SELECT 1")
        yield check


def audit_existing_jobs(store, state):
    """A missing/rewound checkpoint must not reset the cumulative job limit."""
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT job_id,idempotency_key FROM public.content_generation_job WHERE job_kind='anime_generate'"
        ).fetchall()
    owned = {record.get("job_id"): record["request"]["idempotency_key"]
             for record in state["records"] if record.get("job_id")}
    prefix = state["config"]["run_id"] + ":"
    if len(rows) > state["config"]["max_new_jobs"]:
        raise StopRun("database_job_cap_already_exceeded")
    for row in rows:
        if not row["idempotency_key"].startswith(prefix) or owned.get(str(row["job_id"])) != row["idempotency_key"]:
            raise StopRun("database_history_not_owned_by_checkpoint")
    if set(owned) != {str(row["job_id"]) for row in rows}:
        raise StopRun("checkpoint_job_missing_from_database")


class Controller:
    def __init__(self, *, config, checkpoint, store, discover, validate, publish, read_result, export,
                 now=time.time, sleep=time.sleep, lock_check=lambda: None, reservations=None):
        self.config, self.path, self.store = config, checkpoint, store
        self.discover, self.validate, self.publish = discover, validate, publish
        self.read_result, self.export, self.now, self.sleep = read_result, export, now, sleep
        self.lock_check = lock_check
        self.reservations = reservations or {}
        self.deadline = timestamp(config["deadline"])
        self.bundles = Path(config["bundle_root"])
        if checkpoint.exists():
            self.state = read_json(checkpoint)
            if self.state.get("schema_version") != 1 or self.state.get("config") != config:
                raise StopRun("checkpoint_configuration_mismatch")
        else:
            self.state = {"schema_version": 1, "config": config, "status": "running", "records": [],
                          "seen": list(config["excluded_urls"]), "scans": 0, "created_at": now()}
            self.save()

    def save(self):
        self.state["updated_at"] = self.now()
        write_json(self.path, self.state)

    def known_spent(self):
        return money(self.config["baseline_estimated_cost_usd"]) + sum(
            (money(row["estimated_cost_usd"]) for row in self.state["records"] if row.get("estimated_cost_usd") is not None), Decimal(0))

    def reserved_spent(self):
        return sum((money(row["reserved_cost_usd"]) for row in self.state["records"]
                    if row.get("accounting_method") == "reserve"), Decimal(0))

    def spent(self):
        return self.known_spent() + self.reserved_spent()

    def budget_limit(self):
        """Legacy runs keep their approved cap; daily policies may override it."""
        return money(self.config["total_budget_usd"])

    def budget_exceeded(self, additional=Decimal(0)):
        limit = self.budget_limit()
        return limit is not None and self.spent() + additional > limit

    def matching_reservation(self, job, result):
        receipt = self.reservations.get(job["job_id"])
        if receipt is None:
            raise StopRun("accounting_reservation_not_for_this_job")
        actual = {"job_id": job["job_id"], "generation_attempt_id": job.get("generation_attempt_id"),
                  "source_result_sha256": job.get("result_sha256"), "producer_status": job.get("producer_status")}
        if (any(actual[key] != receipt[key] for key in actual)
                or job.get("execution_status") != "succeeded" or job.get("outcome") != "skipped"
                or result.get("status") != receipt["producer_status"]):
            raise StopRun("accounting_reservation_source_mismatch")
        return receipt

    def resume_after_accounting(self):
        """Caller holds both locks; never changes producer files or job rows."""
        self.lock_check()
        audits = self.state.get("accounting_resume_audit", [])
        receipt_ids = {row["receipt_sha256"] for row in self.reservations.values()}
        if self.state["status"] in {"running", "complete"}:
            if audits and audits[-1]["receipt_sha256"] in receipt_ids:
                return  # The fsynced resume already happened before a process restart.
            raise StopRun("accounting_resume_not_allowed")
        if self.state.get("stop_reason") != "missing_cost_evidence":
            raise StopRun("accounting_resume_not_allowed")
        pending = [r for r in self.state["records"] if r["state"] not in {"complete", "duplicate"}]
        if len(pending) != 1 or not pending[0].get("job_id"):
            raise StopRun("accounting_resume_pending_job_mismatch")
        record = pending[0]
        job = self.store.get(record["job_id"])
        if job is None:
            raise StopRun("owned_job_missing")
        result = self.read_result(job)  # Reverify the original sealed manifest before accepting a receipt.
        receipt = self.matching_reservation(job, result)
        try:
            result_cost(result)
        except StopRun as exc:
            if str(exc) != "missing_cost_evidence":
                raise StopRun("accounting_resume_not_missing_evidence") from None
        else:
            raise StopRun("accounting_resume_not_missing_evidence")
        reserved = money(receipt["reserved_usd"])
        existing = money(record.get("reserved_cost_usd", "0"))
        if self.budget_exceeded(reserved - existing):
            raise StopRun("accounting_reservation_exceeds_budget")
        if self.now() >= self.deadline:
            raise StopRun("deadline_reached")
        record.update(estimated_cost_usd=None, measured_cost=None, reserved_cost_usd=str(reserved),
                      accounting_method="reserve", accounting_reservation=dict(receipt),
                      result_sha256=job["result_sha256"], generation_attempt_id=job["generation_attempt_id"])
        self.state.setdefault("accounting_resume_audit", []).append({
            **dict(receipt), "resumed_at": self.now(), "previous_status": self.state["status"],
            "previous_stop_reason": self.state["stop_reason"], "provider_cost_measured": False,
        })
        self.state.update(status="running", stop_reason=None)
        self.save()  # Reservation and permission to resume commit in one durable file replacement.

    def used_slots(self):
        return sum(row.get("counts_slot", False) for row in self.state["records"])

    def completion_reason(self):
        return "new_job_slots_consumed" if self.used_slots() >= self.config["max_new_jobs"] else None

    def record_metadata(self):
        return {}

    def stop(self, reason):
        self.state.update(status="stopped", stop_reason=reason)
        self.save()
        raise StopRun(reason)

    def check(self):
        self.lock_check()
        if self.now() >= self.deadline:
            self.stop("deadline_reached")

    def finish_job(self, record):
        """A publish intent is durable before the broker call; it is never replayed."""
        job_id = record["job_id"]
        while True:
            self.check()
            job = self.store.get(job_id)
            if job is None:
                self.stop("owned_job_missing")
            if job["execution_status"] in TERMINAL:
                break
            if record.get("publish_intent") and job["execution_status"] == "queued" and not job.get("last_task_id"):
                # Waiting for an in-flight first publication is allowed only in
                # the same process; a restart cannot know whether Redis accepted it.
                self.stop("ambiguous_delivery_no_republish")
            if self.now() - record["reserved_at"] >= self.config["job_timeout_seconds"]:
                self.stop("job_wait_timeout")
            self.sleep(3)
        record.update(execution_status=job["execution_status"], outcome=job.get("outcome"),
                      producer_status=job.get("producer_status"))
        if job["execution_status"] == "needs_attention":
            self.stop("job_needs_attention")
        if job["execution_status"] in {"failed", "cancelled"}:
            # A confirmed pre-launch failure has incurred no model cost. Any
            # producer-start marker makes missing output a stop condition.
            if job.get("generation_started_at") or job.get("artifact_path") or any(
                attempt.get("generation_started_at") for attempt in job.get("attempts", [])):
                self.stop("failed_generation_cost_unknown")
            record.update(estimated_cost_usd="0.00000000", state="complete")
            self.save()
            return
        result = self.read_result(job)
        try:
            cost = result_cost(result)
        except StopRun as exc:
            if str(exc) != "missing_cost_evidence" or record.get("accounting_method") != "reserve":
                self.stop(str(exc))
            receipt = self.matching_reservation(job, result)
            if record.get("accounting_reservation") != receipt:
                self.stop("accounting_reservation_changed")
            record.update(estimated_cost_usd=None, measured_cost=None,
                          reserved_cost_usd=str(money(receipt["reserved_usd"])))
        else:
            if record.get("accounting_method") == "reserve":
                self.stop("cost_evidence_changed_after_reservation")
            record["estimated_cost_usd"] = str(cost)
        record.update(result_sha256=job["result_sha256"], generation_attempt_id=job["generation_attempt_id"])
        self.save()
        if self.budget_exceeded():
            self.stop("estimated_budget_target_exceeded")
        if job.get("outcome") == "ready_for_staging":
            destination = self.bundles / job_id
            if record.get("bundle_path"):
                if not Path(record["bundle_path"]).is_file():
                    self.stop("recorded_bundle_missing")
            elif destination.exists():
                # A prior export may have published immediately before checkpoint
                # fsync. Never overwrite it; operator can inspect/resume explicitly.
                self.stop("uncheckpointed_bundle_exists")
            else:
                exported = self.export(job_id, destination)
                record["bundle_path"] = exported["bundle_path"]
        elif job.get("outcome") not in {"rejected", "held", "skipped"}:
            self.stop("unexpected_terminal_outcome")
        record["state"] = "complete"
        self.save()

    def handle(self, record):
        if record.get("job_id") is None:
            submitted = self.store.submit(record["request"], job_kind="anime_generate")
            if not submitted.get("created"):
                if submitted["execution_status"] not in TERMINAL:
                    self.stop("foreign_or_uncheckpointed_pending_job")
                record.update(state="duplicate", counts_slot=False, existing_job_id=submitted["job_id"])
                self.save()
                return
            record["job_id"] = submitted["job_id"]
            self.save()
        if not record.get("publish_intent"):
            self.check()
            record["publish_intent"] = True
            record["publish_intent_at"] = self.now()
            self.save()
            published = self.store.publish_pending(record["job_id"], self.publish, expected_kind="anime_generate")
            if not published:
                self.stop("owned_job_not_publishable")
            record["published"] = True
            self.save()
        self.finish_job(record)

    def run(self):
        if self.state["status"] in {"complete", "stopped"}:
            return self.summary()
        for record in self.state["records"]:
            if record["state"] not in {"complete", "duplicate"}:
                self.handle(record)
        while self.completion_reason() is None:
            self.check()
            if self.budget_exceeded(money(self.config["next_job_reserve_usd"])):
                self.stop("estimated_budget_reserve_unavailable")
            scan = self.discover(self.config["source_id"], self.config["content_type"], 20)
            articles = scan.get("articles", [])
            if not isinstance(articles, list) or len(articles) > 20 or scan.get("discovered", 0) > 20:
                self.stop("discovery_limit_exceeded")
            self.state["scans"] += 1
            self.state["last_scan"] = {k: scan.get(k) for k in ("discovered", "unresolved", "fetch_failed", "listing_partial")}
            self.save()
            for article in articles:
                self.check()
                if self.completion_reason() is not None:
                    break
                if self.budget_exceeded(money(self.config["next_job_reserve_usd"])):
                    self.stop("estimated_budget_reserve_unavailable")
                if self.now() + self.config["job_timeout_seconds"] > self.deadline:
                    self.stop("insufficient_time_for_new_job")
                if article.get("source_id") != self.config["source_id"] or article.get("content_type") != self.config["content_type"]:
                    self.stop("discovery_scope_mismatch")
                fields = {**article, "idempotency_key": "bounded-validation"}
                canonical, digest = self.validate(fields)
                if canonical in self.state["seen"]:
                    continue
                fields["idempotency_key"] = f"{self.config['run_id']}:{digest[:48]}"
                record = {"request": fields, "canonical_url": canonical, "state": "reserved",
                          "counts_slot": True, "reserved_at": self.now()}
                record.update(self.record_metadata())
                self.state["records"].append(record)
                self.state["seen"].append(canonical)
                self.save()
                try:
                    self.handle(record)
                except Exception as exc:
                    # A pre-existing article under another idempotency key is
                    # not ours. It may only be skipped when already terminal.
                    if getattr(exc, "code", None) != "candidate_exists":
                        raise
                    other = self.store.get(str(exc.existing_job_id))
                    if not other or other["execution_status"] not in TERMINAL:
                        self.stop("foreign_pending_candidate")
                    record.update(state="duplicate", counts_slot=False, existing_job_id=other["job_id"])
                    self.save()
            if self.completion_reason() is None:
                self.sleep(min(self.config["scan_interval_seconds"], max(0, self.deadline - self.now())))
        self.state.update(status="complete", stop_reason=self.completion_reason())
        self.save()
        return self.summary()

    def summary(self):
        return {"run_id": self.config["run_id"], "status": self.state["status"],
                "stop_reason": self.state.get("stop_reason"), "baseline_count": self.config["baseline_count"],
                "new_slots_consumed": self.used_slots(), "max_new_jobs": self.config["max_new_jobs"],
                "estimated_total_cost_usd": str(self.known_spent()),
                "reserved_cost_usd": str(self.reserved_spent()), "budget_accounted_total_usd": str(self.spent()),
                "unmeasured_cost_jobs": [r["job_id"] for r in self.state["records"] if r.get("accounting_method") == "reserve"],
                "target_budget_usd": self.config["total_budget_usd"],
                "provider_hard_cap": False, "service_db_writes": 0,
                "bundles": [r["bundle_path"] for r in self.state["records"] if r.get("bundle_path")]}
