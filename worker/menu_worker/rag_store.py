"""RAG admissions in the dedicated jobs DB; never grants its own daily allowance.

Only a controller which durably reserved a shared daily slot may call submit.
An expired or possibly started attempt is preserved and never relaunched here.
"""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from psycopg.types.json import Jsonb

from menu_worker.job_store import JobConflict, JobStore, JobStoreError, _identity, _lease, _plain
from menu_worker.rag_contract import (canonical, digest, is_hash, require, validate_admission_receipt,
                                      validate_request)


class _GenerationGuard:
    def __init__(self, store, check, excluded):
        self.store, self._check, self.excluded, self.active = store, check, excluded, True

    def __call__(self):
        if not self.active:
            raise JobStoreError("rag_generation_guard_closed")
        self._check()
        if self.store._busy(self.excluded):
            raise JobStoreError("rag_generation_busy")


def _usage(records, complete):
    require(type(complete) is bool and isinstance(records, (list, tuple)) and len(records) <= 1000,
            "invalid_rag_usage")
    subtotal = Decimal(0)
    for record in records:
        require(isinstance(record, dict), "invalid_rag_usage")
        cost = record.get("estimated_cost_usd")
        if cost is None:
            complete = False
            continue
        try:
            require(not isinstance(cost, bool), "invalid_rag_usage")
            amount = Decimal(str(cost))
            require(amount.is_finite() and amount >= 0, "invalid_rag_usage")
            subtotal += amount
        except InvalidOperation:
            require(False, "invalid_rag_usage")
    try:
        require(len(canonical(records)) <= 512 * 1024, "invalid_rag_usage")
    except (ValueError, TypeError, OverflowError):
        require(False, "invalid_rag_usage")
    return list(records), complete, str(subtotal)


class RagStore:
    def __init__(self, dsn=None, *, expected_database="menu_content_jobs"):
        self.jobs = JobStore(dsn, expected_database=expected_database)

    def initialize(self):
        return self.jobs.initialize()

    def submit(self, request, admission_receipt):
        request = validate_request(request)
        receipt = validate_admission_receipt(admission_receipt, request)
        values = {"job_id": uuid4(), "namespace": request.namespace, "work_id": request.work_id,
                  "identity_key": request.identity_key, "input_sha256": request.input_sha256,
                  "request": Jsonb(request.as_dict()), "reservation_id": _identity(receipt["reservation_id"]),
                  "admission_receipt": Jsonb(receipt), "admission_sha256": digest(receipt)}
        with self.jobs._connect() as connection:
            row = connection.execute("""INSERT INTO public.content_rag_generation_job
                (job_id,namespace,work_id,identity_key,input_sha256,request,reservation_id,admission_receipt,admission_sha256)
                VALUES (%(job_id)s,%(namespace)s,%(work_id)s,%(identity_key)s,%(input_sha256)s,%(request)s,
                        %(reservation_id)s,%(admission_receipt)s,%(admission_sha256)s)
                ON CONFLICT DO NOTHING RETURNING *""", values).fetchone()
            if row:
                return {**_plain(row), "created": True}
            previous = connection.execute("""SELECT * FROM public.content_rag_generation_job
                WHERE namespace=%s AND work_id=%s""", (request.namespace, request.work_id)).fetchone()
            if previous:
                if previous["input_sha256"] != request.input_sha256:
                    raise JobConflict("rag_input_conflict", previous["job_id"])
                if previous["admission_sha256"] != digest(receipt):
                    raise JobConflict("rag_candidate_exists", previous["job_id"])
                return {**_plain(previous), "created": False}
            previous = connection.execute("SELECT job_id FROM public.content_rag_generation_job WHERE reservation_id=%s",
                                          (values["reservation_id"],)).fetchone()
            if previous:
                raise JobConflict("rag_reservation_conflict", previous["job_id"])
            raise JobStoreError("rag_admission_changed")

    def get(self, job_id):
        with self.jobs._connect() as connection:
            row = connection.execute("SELECT * FROM public.content_rag_generation_job WHERE job_id=%s",
                                     (_identity(job_id),)).fetchone()
            if row is None:
                return None
            row["attempts"] = connection.execute("SELECT * FROM public.content_rag_generation_attempt WHERE job_id=%s",
                                                  (_identity(job_id),)).fetchall()
            return _plain(row)

    def _busy(self, excluded=None):
        with self.jobs._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            return connection.execute("""SELECT EXISTS (
                SELECT 1 FROM public.content_generation_job
                  WHERE job_kind='anime_generate' AND execution_status='running'
                    AND generation_started_at IS NOT NULL AND lease_expires_at>CURRENT_TIMESTAMP
                UNION ALL
                SELECT 1 FROM public.content_rag_generation_job
                  WHERE execution_status='running' AND generation_started_at IS NOT NULL
                    AND lease_expires_at>CURRENT_TIMESTAMP AND (%s::uuid IS NULL OR job_id<>%s::uuid)
                ) AS busy""", (excluded, excluded)).fetchone()["busy"]

    @contextmanager
    def generation_guard(self, *, exclude_job_id=None):
        excluded = _identity(exclude_job_id) if exclude_job_id is not None else None
        # Exactly the period producer's generation-history-v1 / seed 3 lock.
        with self.jobs.generation_history_guard() as check:
            if check is None or self._busy(excluded):
                yield None
                return
            guard = _GenerationGuard(self, check, excluded)
            try:
                yield guard
            finally:
                guard.active = False

    def claim(self, job_id, *, lease_seconds=30):
        lease_seconds = _lease(lease_seconds)
        with self.jobs._connect() as connection:
            job = connection.execute("SELECT * FROM public.content_rag_generation_job WHERE job_id=%s FOR UPDATE",
                                     (_identity(job_id),)).fetchone()
            if not job or job["execution_status"] != "queued" or job["generation_attempt_id"] is not None:
                return None
            request = validate_request(job["request"])
            receipt = validate_admission_receipt(job["admission_receipt"], request)
            require((job["namespace"], job["work_id"], job["identity_key"], job["input_sha256"], job["admission_sha256"])
                    == (request.namespace, request.work_id, request.identity_key, request.input_sha256, digest(receipt)),
                    "rag_stored_admission_changed")
            attempt_id, token = uuid4(), uuid4()
            connection.execute("""INSERT INTO public.content_rag_generation_attempt(attempt_id,job_id,execution_status)
                VALUES (%s,%s,'running')""", (attempt_id, job["job_id"]))
            row = connection.execute("""UPDATE public.content_rag_generation_job SET execution_status='running',
                generation_attempt_id=%s,claim_token=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second')
                WHERE job_id=%s RETURNING *""", (attempt_id, token, lease_seconds, job["job_id"])).fetchone()
            return _plain(row)

    def renew_lease(self, job_id, claim_token, *, lease_seconds=30):
        with self.jobs._connect() as connection:
            return bool(connection.execute("""UPDATE public.content_rag_generation_job
                SET lease_expires_at=clock_timestamp()+(%s*interval '1 second')
                WHERE job_id=%s AND claim_token=%s AND execution_status='running'
                  AND lease_expires_at>clock_timestamp() RETURNING job_id""",
                (_lease(lease_seconds), _identity(job_id), _identity(claim_token))).fetchone())

    def mark_generation_started(self, job_id, claim_token, guard):
        require(isinstance(guard, _GenerationGuard) and guard.store is self and guard.active
                and guard.excluded == _identity(job_id), "rag_generation_guard_required")
        guard()
        with self.jobs._connect() as connection:
            row = connection.execute("""UPDATE public.content_rag_generation_job
                SET generation_started_at=clock_timestamp()
                WHERE job_id=%s AND claim_token=%s AND execution_status='running'
                  AND generation_started_at IS NULL AND lease_expires_at>clock_timestamp()
                RETURNING generation_attempt_id,generation_started_at""",
                (_identity(job_id), _identity(claim_token))).fetchone()
            if row is None:
                return False
            connection.execute("UPDATE public.content_rag_generation_attempt SET generation_started_at=%s WHERE attempt_id=%s",
                               (row["generation_started_at"], row["generation_attempt_id"]))
            return True

    def complete(self, job_id, claim_token, *, execution_status, outcome=None, producer_status=None,
               result_sha256=None, artifact_path=None, usage_records=(), cost_complete=False, error_code=None):
        require(execution_status in {"succeeded", "failed", "needs_attention"}, "invalid_rag_completion")
        require(outcome in {"ready_for_staging", "held", "skipped", "rejected"} if execution_status == "succeeded"
                else outcome is None, "invalid_rag_completion")
        import re
        require(error_code is None or isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", error_code),
                "invalid_rag_completion")
        require(producer_status is None or isinstance(producer_status, str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", producer_status), "invalid_rag_completion")
        records, complete, _ = _usage(usage_records, cost_complete)
        with self.jobs._connect() as connection:
            row = connection.execute("""SELECT * FROM public.content_rag_generation_job
                WHERE job_id=%s AND claim_token=%s AND execution_status='running'
                  AND lease_expires_at>clock_timestamp() FOR UPDATE""",
                (_identity(job_id), _identity(claim_token))).fetchone()
            if row is None:
                return False
            if result_sha256 is not None or artifact_path is not None or execution_status == "succeeded":
                require(is_hash(result_sha256) and artifact_path ==
                        f"rag/jobs/{row['job_id']}/attempts/{row['generation_attempt_id']}/manifest.json",
                        "invalid_rag_result_seal")
            require(execution_status != "succeeded" or row["generation_started_at"] is not None
                    and producer_status is not None, "invalid_rag_completion")
            values = (execution_status, outcome, producer_status, result_sha256, artifact_path, Jsonb(records), complete, error_code)
            connection.execute("""UPDATE public.content_rag_generation_attempt SET execution_status=%s,outcome=%s,
                producer_status=%s,result_sha256=%s,artifact_path=%s,usage_records=%s,cost_complete=%s,error_code=%s,
                finished_at=clock_timestamp() WHERE attempt_id=%s""", (*values, row["generation_attempt_id"]))
            connection.execute("""UPDATE public.content_rag_generation_job SET execution_status=%s,outcome=%s,
                producer_status=%s,result_sha256=%s,artifact_path=%s,usage_records=%s,cost_complete=%s,error_code=%s,
                finished_at=clock_timestamp(),claim_token=NULL,lease_expires_at=NULL WHERE job_id=%s""",
                (*values, row["job_id"]))
            return True

    def expire_claims(self):
        """Record an interrupted attempt; never clears its launch marker or requeues it."""
        with self.jobs._connect() as connection:
            rows = connection.execute("""SELECT * FROM public.content_rag_generation_job
                WHERE execution_status='running' AND lease_expires_at<=clock_timestamp() FOR UPDATE""").fetchall()
            for row in rows:
                started = row["generation_started_at"] is not None
                status = "needs_attention" if started else "failed"
                code = "RAG_GENERATION_INTERRUPTED" if started else "RAG_LEASE_EXPIRED_BEFORE_GENERATION"
                for table, column, identity in (("content_rag_generation_attempt", "attempt_id", row["generation_attempt_id"]),
                                                 ("content_rag_generation_job", "job_id", row["job_id"])):
                    extra = ",claim_token=NULL,lease_expires_at=NULL" if column == "job_id" else ""
                    connection.execute(f"UPDATE public.{table} SET execution_status=%s,error_code=%s,cost_complete=FALSE,"
                                       f"finished_at=clock_timestamp(){extra} WHERE {column}=%s", (status, code, identity))
            return len(rows)
