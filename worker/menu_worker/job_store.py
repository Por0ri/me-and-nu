"""Dedicated PostgreSQL job ledger and durable dispatch source.

Every method commits before returning. No transaction spans producer execution.
The generation-start marker is permanent: lease recovery never starts it again.
"""
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import re
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from menu_worker.job_contract import JobInput, validate_input

DATABASE_NAMES = frozenset({"menu_content_jobs", "menu_content_jobs_test"})
DATABASE_HOSTS = frozenset({"jobs-db", "localhost", "127.0.0.1", "::1"})
SCHEMA_VERSION = 1


class JobStoreError(RuntimeError):
    pass


class JobConflict(ValueError):
    def __init__(self, code: str, existing_job_id: str):
        self.code = code
        self.existing_job_id = str(existing_job_id)
        super().__init__(code)


def validate_database_url(dsn: str, expected_database: str = "menu_content_jobs") -> dict:
    if expected_database not in DATABASE_NAMES or not isinstance(dsn, str) or not dsn:
        raise JobStoreError("An explicit dedicated content-job database URL is required")
    try:
        values = conninfo_to_dict(dsn)
    except (psycopg.Error, ValueError):
        raise JobStoreError("Invalid content-job database connection settings") from None
    if (
        values.get("dbname") != expected_database
        or values.get("host") not in DATABASE_HOSTS
        or any(key in values for key in ("service", "servicefile", "hostaddr", "options"))
    ):
        raise JobStoreError("Only an explicitly named local content-job database is allowed")
    return values


def _plain(value):
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _identity(value: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid job or claim UUID is required") from None


def _lease(seconds: int) -> int:
    if type(seconds) is not int or not 5 <= seconds <= 3600:
        raise ValueError("lease_seconds must be an integer from 5 to 3600")
    return seconds


def _task_id(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value)):
        raise ValueError("Invalid task ID")
    return value


class JobStore:
    def __init__(self, dsn: str | None = None, *, expected_database: str = "menu_content_jobs"):
        selected = dsn if dsn is not None else os.environ.get("CONTENT_JOB_DATABASE_URL", "")
        validate_database_url(selected, expected_database)
        self._dsn = selected
        self.expected_database = expected_database

    def _connect(self):
        connection = None
        try:
            connection = psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5)
            actual = connection.execute("SELECT current_database() AS name").fetchone()["name"]
            if actual != self.expected_database:
                connection.close()
                raise JobStoreError("Connected database does not match the content-job target")
            return connection
        except psycopg.Error:
            if connection is not None:
                connection.close()
            raise JobStoreError("Could not connect to the dedicated content-job database") from None

    def initialize(self) -> dict:
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(20261102, 1)")
            marker = connection.execute("SELECT to_regclass('public.menu_worker_schema') AS marker").fetchone()["marker"]
            if marker:
                versions = connection.execute("SELECT version FROM public.menu_worker_schema").fetchall()
                if versions != [{"version": SCHEMA_VERSION}]:
                    raise JobStoreError("Unsupported content-job schema version")
                for table in ("content_generation_job", "content_generation_attempt"):
                    exists = connection.execute("SELECT to_regclass(%s) AS name", ("public." + table,)).fetchone()["name"]
                    if not exists:
                        raise JobStoreError("Content-job schema is incomplete")
                return {"version": SCHEMA_VERSION, "created": False}
            existing = connection.execute(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1"
            ).fetchone()
            if existing:
                raise JobStoreError("Refusing to initialize a nonempty unversioned database")
            migration = Path(__file__).with_name("migrations") / "001_content_jobs.sql"
            connection.execute(migration.read_text(encoding="utf-8"), prepare=False)
        return {"version": SCHEMA_VERSION, "created": True}

    def submit(self, request: JobInput | dict) -> dict:
        request = request if isinstance(request, JobInput) else validate_input(request)
        with self._connect() as connection:
            created = connection.execute(
                """INSERT INTO public.content_generation_job
                   (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,input_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *""",
                (uuid4(), request.idempotency_key, request.source_id, request.source_url,
                 request.canonical_url, request.content_type, request.input_sha256),
            ).fetchone()
            if created:
                return {**_plain(created), "created": True}
            previous = connection.execute(
                "SELECT * FROM public.content_generation_job WHERE idempotency_key=%s", (request.idempotency_key,)
            ).fetchone()
            if previous:
                if previous["input_sha256"] != request.input_sha256:
                    raise JobConflict("idempotency_conflict", previous["job_id"])
                return {**_plain(previous), "created": False}
            previous = connection.execute(
                "SELECT job_id FROM public.content_generation_job WHERE canonical_url=%s", (request.canonical_url,)
            ).fetchone()
            if previous:
                raise JobConflict("candidate_exists", previous["job_id"])
            raise JobStoreError("Concurrent submission changed; repeat the same idempotency key")

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM public.content_generation_job WHERE job_id=%s", (_identity(job_id),)).fetchone()
            if row is None:
                return None
            row["attempts"] = connection.execute(
                "SELECT * FROM public.content_generation_attempt WHERE job_id=%s ORDER BY attempt_no", (_identity(job_id),)
            ).fetchall()
            return _plain(row)

    def list_pending(self, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 to 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE execution_status='queued'
                   AND generation_started_at IS NULL
                   AND (last_dispatched_at IS NULL OR last_dispatched_at < clock_timestamp() - INTERVAL '30 seconds')
                   ORDER BY created_at LIMIT %s""", (limit,),
            ).fetchall()
            return _plain(rows)

    def record_dispatched(self, job_id: str, task_id: str) -> bool:
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET last_dispatched_at=CURRENT_TIMESTAMP,
                   dispatch_count=dispatch_count+1,last_task_id=%s,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s AND execution_status='queued' AND generation_started_at IS NULL RETURNING job_id""",
                (_task_id(task_id), _identity(job_id)),
            ).fetchone())

    def _claim(self, job_id: str, task_id: str | None, lease_seconds: int, *, recovery: bool) -> dict | None:
        lease_seconds, task_id = _lease(lease_seconds), _task_id(task_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM public.content_generation_job WHERE job_id=%s FOR UPDATE", (_identity(job_id),)
            ).fetchone()
            expected = "needs_attention" if recovery else "queued"
            if not row or row["execution_status"] != expected:
                return None
            if bool(row["generation_started_at"]) != recovery:
                return None
            attempt, token = uuid4(), uuid4()
            number = row["attempt_count"] + 1
            mode = "recovery" if recovery else "generation"
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id,job_id,attempt_no,mode,celery_task_id,claim_token)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (attempt, row["job_id"], number, mode, task_id, token),
            )
            row = connection.execute(
                """UPDATE public.content_generation_job SET execution_status='running',current_attempt_id=%s,
                   generation_attempt_id=%s,attempt_count=%s,claim_token=%s,
                   lease_expires_at=clock_timestamp() + (%s * INTERVAL '1 second'),
                   error_code=NULL,finished_at=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s RETURNING *""",
                (attempt, row["generation_attempt_id"] if recovery else attempt, number, token, lease_seconds, row["job_id"]),
            ).fetchone()
            return {**_plain(row), "attempt_id": str(attempt), "mode": mode}

    def claim(self, job_id: str, task_id: str | None = None, lease_seconds: int = 30) -> dict | None:
        return self._claim(job_id, task_id, lease_seconds, recovery=False)

    def claim_recovery(self, job_id: str, task_id: str | None = None, lease_seconds: int = 30) -> dict | None:
        return self._claim(job_id, task_id, lease_seconds, recovery=True)

    def mark_generation_started(self, job_id: str, claim_token: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE public.content_generation_job SET generation_started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s AND claim_token=%s AND execution_status='running'
                   AND lease_expires_at>clock_timestamp() AND generation_started_at IS NULL
                   AND current_attempt_id=generation_attempt_id RETURNING current_attempt_id""",
                (_identity(job_id), _identity(claim_token)),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE public.content_generation_attempt SET generation_started_at=CURRENT_TIMESTAMP WHERE attempt_id=%s",
                    (row["current_attempt_id"],),
                )
            return bool(row)

    def renew_lease(self, job_id: str, claim_token: str, lease_seconds: int = 30) -> bool:
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET lease_expires_at=clock_timestamp() + (%s * INTERVAL '1 second'),
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=%s AND claim_token=%s AND execution_status='running'
                   AND lease_expires_at>clock_timestamp() RETURNING job_id""",
                (_lease(lease_seconds), _identity(job_id), _identity(claim_token)),
            ).fetchone())

    def complete(self, job_id: str, claim_token: str, *, execution_status: str, outcome: str | None = None,
                 producer_status: str | None = None, artifact_path: str | None = None,
                 result_sha256: str | None = None, error_code: str | None = None) -> bool:
        if execution_status not in {"succeeded", "failed", "needs_attention"}:
            raise ValueError("Unsupported completion status")
        if outcome not in {None, "ready_for_staging", "held", "skipped"}:
            raise ValueError("Unsupported producer outcome")
        for value in (producer_status, error_code):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value)):
                raise ValueError("Status and error codes must be bounded identifiers")
        if artifact_path is not None:
            path = PurePosixPath(artifact_path)
            if path.is_absolute() or ".." in path.parts or "\\" in artifact_path or not artifact_path.startswith(f"jobs/{_identity(job_id)}/attempts/"):
                raise ValueError("Artifact path must be a relative path for this job")
        if result_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", result_sha256):
            raise ValueError("Result SHA-256 must contain 64 hexadecimal characters")
        if execution_status == "succeeded" and (outcome is None or not producer_status or not artifact_path or not result_sha256):
            raise ValueError("Successful completion requires a preserved, identified result")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE job_id=%s AND claim_token=%s
                   AND execution_status='running' FOR UPDATE""",
                (_identity(job_id), _identity(claim_token)),
            ).fetchone()
            if not row:
                return False
            if execution_status == "succeeded" and row["generation_started_at"] is None:
                return False
            if artifact_path is not None and not artifact_path.startswith(f"jobs/{row['job_id']}/attempts/{row['generation_attempt_id']}/"):
                raise ValueError("Artifact path must refer to the original generation attempt")
            values = (execution_status, outcome, producer_status, artifact_path, result_sha256, error_code)
            completed = connection.execute(
                """UPDATE public.content_generation_job SET execution_status=%s,outcome=%s,producer_status=%s,
                   artifact_path=%s,result_sha256=%s,error_code=%s,finished_at=CURRENT_TIMESTAMP,
                   claim_token=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s AND lease_expires_at>clock_timestamp() RETURNING job_id""",
                (*values, row["job_id"]),
            ).fetchone()
            if not completed:
                return False
            connection.execute(
                """UPDATE public.content_generation_attempt SET execution_status=%s,outcome=%s,producer_status=%s,
                   artifact_path=%s,result_sha256=%s,error_code=%s,finished_at=CURRENT_TIMESTAMP WHERE attempt_id=%s""",
                (*values, row["current_attempt_id"]),
            )
            return True

    def recover_expired(self) -> dict[str, int]:
        counts = {"queued": 0, "needs_attention": 0}
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE execution_status='running'
                   AND lease_expires_at<=clock_timestamp() FOR UPDATE SKIP LOCKED"""
            ).fetchall()
            for row in rows:
                started = row["generation_started_at"] is not None
                status = "needs_attention" if started else "queued"
                code = "GENERATION_INTERRUPTED" if started else "LEASE_EXPIRED_BEFORE_GENERATION"
                connection.execute(
                    """UPDATE public.content_generation_attempt SET execution_status=%s,error_code=%s,
                       finished_at=CURRENT_TIMESTAMP WHERE attempt_id=%s""",
                    ("needs_attention" if started else "failed", code, row["current_attempt_id"]),
                )
                connection.execute(
                    """UPDATE public.content_generation_job SET execution_status=%s,error_code=%s,
                       claim_token=NULL,lease_expires_at=NULL,last_dispatched_at=NULL,updated_at=CURRENT_TIMESTAMP
                       WHERE job_id=%s""", (status, code, row["job_id"]),
                )
                counts[status] += 1
        return counts

    def cancel(self, job_id: str) -> bool:
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET execution_status='cancelled',finished_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=%s AND execution_status='queued'
                   AND generation_started_at IS NULL RETURNING job_id""", (_identity(job_id),),
            ).fetchone())
