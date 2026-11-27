"""Dedicated PostgreSQL job ledger and durable dispatch source.

Dispatch holds a job row lock through the broker send and receipt write.
Workers commit their claims before running the content agent.
Lease recovery never relaunches an attempt with a generation-start marker.
"""
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import re
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from menu_worker.anime_catalog import generation_article_key, one_piece_news_path
from menu_worker.job_contract import JobInput, JobInputError, job_input_hash, validate_job_kind, validate_job_request

DATABASE_NAMES = frozenset({"menu_content_jobs", "menu_content_jobs_test", "menu_content_jobs_migration_test"})
DATABASE_HOSTS = frozenset({"jobs-db", "localhost", "127.0.0.1", "::1"})
MIGRATIONS = ("001_content_jobs.sql", "002_job_kind.sql", "003_anime_work_input.sql", "004_anime_generation.sql",
              "005_article_identity.sql", "006_staging_selection.sql", "007_independent_reviews.sql", "008_rag_jobs.sql")
SCHEMA_VERSION = len(MIGRATIONS)
# Older workers recorded these adapter errors as needs_attention after arming
# the launch fence. Each is emitted exclusively before the producer starts.
PRELAUNCH_SETUP_ERRORS = ("invalid_agent_secret", "agent_attempt_setup_failed", "agent_spawn_failed")
PRELAUNCH_ERRORS = (*PRELAUNCH_SETUP_ERRORS, "agent_runtime_unavailable",
                    "cover_history_invalid", "cover_history_unavailable")
MAX_GENERATION_ATTEMPTS = 3
# These failures confirm that the producer never ran. Configuration/input
# errors still require correction rather than automatic retries.
AUTO_RETRY_PRELAUNCH_ERRORS = ("agent_attempt_setup_failed", "agent_spawn_failed")


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
                if len(versions) != 1 or versions[0]["version"] not in range(1, SCHEMA_VERSION + 1):
                    raise JobStoreError("Unsupported content-job schema version")
                installed = versions[0]["version"]
                tables = ["content_generation_job", "content_generation_attempt"]
                if installed >= 6:
                    tables.append("content_staging_selection")
                if installed >= 7:
                    tables.append("content_generation_review")
                if installed >= 8:
                    tables.extend(("content_rag_generation_job", "content_rag_generation_attempt"))
                for table in tables:
                    exists = connection.execute("SELECT to_regclass(%s) AS name", ("public." + table,)).fetchone()["name"]
                    if not exists:
                        raise JobStoreError("Content-job schema is incomplete")
            else:
                existing = connection.execute(
                    "SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1"
                ).fetchone()
                if existing:
                    raise JobStoreError("Refusing to initialize a nonempty unversioned database")
                installed = 0
            for filename in MIGRATIONS[installed:]:
                migration = Path(__file__).with_name("migrations") / filename
                connection.execute(migration.read_text(encoding="utf-8"), prepare=False)
        return {"version": SCHEMA_VERSION, "created": installed == 0}

    def submit(self, request: JobInput | dict, *, job_kind: str = "fake") -> dict:
        request, canonical_url, input_hash = validate_job_request(request, job_kind)
        news_path = one_piece_news_path(request.source_id, canonical_url) if job_kind == "anime_generate" else None
        with self._connect() as connection:
            if news_path:
                # Older v6 rows may include ref in their sealed URL and artifact digest.
                # Serialize manual and scheduled admissions while consulting those identities.
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))", (news_path,))
                same_key = connection.execute(
                    "SELECT * FROM public.content_generation_job WHERE idempotency_key=%s", (request.idempotency_key,)
                ).fetchone()
                if same_key:
                    return self._replay(same_key, request, job_kind, canonical_url)
                siblings = connection.execute(
                    """SELECT job_id,candidate_url FROM public.content_generation_job
                       WHERE job_kind='anime_generate' AND source_id=%s AND duplicate_of_job_id IS NULL
                         AND (candidate_url=%s OR candidate_url LIKE %s)
                       ORDER BY created_at,job_id""", (request.source_id, news_path, news_path + "?%"),
                )
                for sibling in siblings:
                    if generation_article_key(request.source_id, sibling["candidate_url"]) == canonical_url:
                        raise JobConflict("candidate_exists", sibling["job_id"])
            created = connection.execute(
                """INSERT INTO public.content_generation_job
                   (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,input_sha256,job_kind,menu_work_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *""",
                (uuid4(), request.idempotency_key, request.source_id, request.source_url,
                 canonical_url, request.content_type, input_hash, job_kind, request.menu_work_id),
            ).fetchone()
            if created:
                return {**_plain(created), "created": True}
            previous = connection.execute(
                "SELECT * FROM public.content_generation_job WHERE idempotency_key=%s", (request.idempotency_key,)
            ).fetchone()
            if previous:
                return self._replay(previous, request, job_kind, canonical_url)
            # The probe and generation indexes may each have an owner for this URL.
            collision_kinds = ["anime_generate"] if job_kind == "anime_generate" else ["fake", "anime"]
            previous = connection.execute(
                """SELECT job_id FROM public.content_generation_job WHERE source_id=%s AND candidate_url=%s
                   AND duplicate_of_job_id IS NULL AND job_kind=ANY(%s)""",
                (request.source_id, canonical_url, collision_kinds),
            ).fetchone()
            if previous:
                raise JobConflict("candidate_exists", previous["job_id"])
            raise JobStoreError("Concurrent submission changed; repeat the same idempotency key")

    @staticmethod
    def _replay(previous, request, job_kind, canonical_url):
        stored_identity = previous["candidate_url"]
        if job_kind == "anime_generate":
            stored_identity = generation_article_key(previous["source_id"], stored_identity)
        # Compare logical identity, but verify the digest against the originally sealed URL.
        if (previous["job_kind"] != job_kind or previous["source_id"] != request.source_id
                or stored_identity != canonical_url
                or previous["input_sha256"] != job_input_hash(request, job_kind, previous["canonical_url"])):
            raise JobConflict("idempotency_conflict", previous["job_id"])
        return {**_plain(previous), "created": False}

    @contextmanager
    def discovery_slot(self, source_id: str, content_type: str):
        # The short-lived connection owns the session lock across the HTTP scan.
        # Close releases it even on failure. No transaction stays open during collection.
        with self._connect() as connection:
            connection.commit()
            connection.autocommit = True
            route = f"anime-discovery:{source_id}:{content_type}"
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 2)) AS ok", (route,)
            ).fetchone()["ok"]
            yield acquired

    @contextmanager
    def generation_history_guard(self):
        """Serialize generation/history snapshots without an open transaction.

        The caller must check the dedicated lock connection at its launch fence
        and while the producer runs. Closing it also releases the session lock.
        """
        connection = None
        try:
            try:
                connection = self._connect()
                connection.commit()
                connection.autocommit = True
                acquired = connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 3)) AS ok",
                    ("generation-history-v1",),
                ).fetchone()["ok"]
            except (psycopg.Error, JobStoreError):
                raise JobStoreError("cover_history_unavailable") from None

            def check():
                try:
                    if connection.execute("SELECT 1 AS ok").fetchone()["ok"] != 1:
                        raise JobStoreError("cover_history_unavailable")
                except psycopg.Error:
                    raise JobStoreError("cover_history_unavailable") from None

            yield check if acquired else None
        finally:
            if connection is not None:
                connection.close()

    def generation_history_busy(self, exclude_job_id: str) -> bool:
        """Keep a new producer behind another live generation launch fence.

        The session lock may be released before its old subprocess has stopped.
        An unexpired started claim still fences that handoff; unresolved or
        expired jobs retain the existing next-job progress policy.
        """
        try:
            with self._connect() as connection:
                connection.commit()
                connection.execute("SET TRANSACTION READ ONLY")
                return connection.execute(
                    """SELECT EXISTS (
                        SELECT 1 FROM public.content_generation_job
                        WHERE job_kind='anime_generate' AND job_id<>%s AND execution_status='running'
                          AND generation_started_at IS NOT NULL AND lease_expires_at>CURRENT_TIMESTAMP
                        UNION ALL
                        SELECT 1 FROM public.content_rag_generation_job
                        WHERE execution_status='running' AND generation_started_at IS NOT NULL
                          AND lease_expires_at>CURRENT_TIMESTAMP
                    ) AS busy""", (_identity(exclude_job_id),),
                ).fetchone()["busy"]
        except psycopg.Error:
            raise JobStoreError("cover_history_unavailable") from None

    def cover_history_sources(self, exclude_job_id: str) -> list[dict]:
        """Read completed generation evidence, preserving every original input.

        Artifacts still require read_sealed_staging_result() verification before
        use. Unresolved attempts without a succeeded ledger/attempt are omitted:
        their unverified artifacts cannot safely seed history or block the queue.
        No result, attempt, article owner or legacy URL is rewritten here.
        """
        evidence_keys = ("outcome", "producer_status", "artifact_path", "result_sha256")
        try:
            excluded = _identity(exclude_job_id)
            with self._connect() as connection:
                connection.commit()
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                rows = connection.execute(
                    """WITH evidence AS (
                        SELECT j.job_id, TRUE AS ledger, j.generation_attempt_id AS attempt_id,
                               'generation' AS mode, j.outcome, j.producer_status, j.artifact_path,
                               j.result_sha256, j.finished_at
                        FROM public.content_generation_job j
                        WHERE j.job_kind='anime_generate' AND j.job_id<>%(excluded)s
                          AND j.execution_status='succeeded'
                        UNION ALL
                        SELECT a.job_id, FALSE, a.attempt_id, a.mode, a.outcome, a.producer_status,
                               a.artifact_path, a.result_sha256, a.finished_at
                        FROM public.content_generation_attempt a
                        JOIN public.content_generation_job j ON j.job_id=a.job_id
                        WHERE j.job_kind='anime_generate' AND j.job_id<>%(excluded)s
                          AND a.execution_status='succeeded'
                    )
                    SELECT to_jsonb(j) AS job, to_jsonb(e) AS evidence, to_jsonb(g) AS generation
                    FROM evidence e JOIN public.content_generation_job j ON j.job_id=e.job_id
                    LEFT JOIN public.content_generation_attempt g ON g.job_id=j.job_id
                      AND g.attempt_id=substring(e.artifact_path FROM
                          '^jobs/[0-9a-f-]{36}/attempts/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/manifest[.]json$')::uuid
                      AND e.artifact_path=('jobs/' || j.job_id::text || '/attempts/' || g.attempt_id::text
                                           || '/manifest.json')
                    ORDER BY j.created_at,j.job_id,e.ledger DESC,e.attempt_id LIMIT 10001""",
                    {"excluded": excluded},
                ).fetchall()
            if len(rows) > 10000:
                raise JobStoreError("cover_history_invalid")
            sources = {}
            for row in rows:
                job, evidence, generation = row["job"], row["evidence"], row["generation"]
                if (not generation or generation["mode"] != "generation"
                        or generation["generation_started_at"] is None or not evidence["finished_at"]
                        or evidence["outcome"] not in {"ready_for_staging", "held", "rejected", "skipped"}
                        or not isinstance(evidence["producer_status"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", evidence["producer_status"])
                        or not isinstance(evidence["result_sha256"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", evidence["result_sha256"])
                        or (evidence["mode"] == "generation"
                            and evidence["attempt_id"] != generation["attempt_id"])):
                    raise JobStoreError("cover_history_invalid")
                identity = (job["job_id"], generation["attempt_id"])
                source = {**job, **{key: evidence[key] for key in evidence_keys},
                          "execution_status": "succeeded", "generation_attempt_id": generation["attempt_id"],
                          "generation_started_at": generation["generation_started_at"]}
                previous = sources.get(identity)
                if previous and any(previous[key] != source[key] for key in evidence_keys):
                    raise JobStoreError("cover_history_invalid")
                sources[identity] = source
            return list(sources.values())
        except psycopg.Error:
            raise JobStoreError("cover_history_unavailable") from None
        except (KeyError, TypeError, ValueError):
            raise JobStoreError("cover_history_invalid") from None

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM public.content_generation_job WHERE job_id=%s", (_identity(job_id),)).fetchone()
            if row is None:
                return None
            row["attempts"] = connection.execute(
                "SELECT * FROM public.content_generation_attempt WHERE job_id=%s ORDER BY attempt_no", (_identity(job_id),)
            ).fetchall()
            return _plain(row)

    @staticmethod
    def _staging_article(connection, job: dict, *, reviewed: bool) -> dict:
        # Recovery can finish the result on a later recovery attempt. Preserve
        # that history and bind its artifacts to the original generation attempt.
        outcome, producer = (("rejected", "rejected_archived") if reviewed
                             else ("ready_for_staging", "ready_for_feed"))
        article = connection.execute(
            """SELECT COALESCE(j.duplicate_of_job_id, j.job_id) AS article_job_id
               FROM public.content_generation_job j
               JOIN public.content_generation_attempt a
                 ON a.attempt_id=j.generation_attempt_id AND a.job_id=j.job_id AND a.mode='generation'
               WHERE j.job_id=%(job_id)s AND j.job_kind='anime_generate' AND j.execution_status='succeeded'
                 AND j.outcome=%(required_outcome)s AND j.producer_status=%(required_producer)s
                 AND j.generation_attempt_id=%(generation_attempt_id)s AND j.result_sha256=%(result_sha256)s
                 AND j.artifact_path=%(artifact_path)s FOR SHARE OF j, a""",
            {**job, "required_outcome": outcome, "required_producer": producer},
        ).fetchone()
        if article is None:
            raise JobInputError("Verified result no longer matches the job ledger", "staging_ledger_mismatch")
        return article

    def register_staging_review(self, job: dict, review: dict) -> dict:
        """Append an already verified, sealed review without changing generation history."""
        try:
            review_id = _identity(review["review_id"])
            hashes = (review["review_result_sha256"], review["artifact_sha256"])
            artifact_path = review["artifact_path"]
            if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
                raise ValueError
            if not isinstance(artifact_path, str) or not artifact_path:
                raise ValueError
            path = PurePosixPath(artifact_path)
            if (path.is_absolute() or ".." in path.parts or "\\" in artifact_path
                    or path.as_posix() != artifact_path or artifact_path == "."):
                raise ValueError
            if artifact_path != f"reviews/{_identity(job['job_id'])}/{review_id}.json":
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise JobInputError("A sealed independent review identity is required", "staging_review_mismatch") from None
        values = {
            "review_id": review_id, "job_id": job["job_id"],
            "generation_attempt_id": job["generation_attempt_id"], "source_result_sha256": job["result_sha256"],
            "review_result_sha256": hashes[0], "artifact_path": artifact_path, "artifact_sha256": hashes[1],
        }
        with self._connect() as connection:
            self._staging_article(connection, job, reviewed=True)
            inserted = connection.execute(
                """INSERT INTO public.content_generation_review
                       (review_id, job_id, generation_attempt_id, source_result_sha256,
                        review_result_sha256, artifact_path, artifact_sha256)
                   VALUES (%(review_id)s, %(job_id)s, %(generation_attempt_id)s, %(source_result_sha256)s,
                           %(review_result_sha256)s, %(artifact_path)s, %(artifact_sha256)s)
                   ON CONFLICT DO NOTHING RETURNING *""", values,
            ).fetchone()
            if inserted is not None:
                return _plain(inserted)
            # ON CONFLICT waits for a concurrent registrant. This new statement
            # then sees that committed row, which must identify the same seal.
            existing = connection.execute(
                """SELECT * FROM public.content_generation_review
                   WHERE job_id=%(job_id)s AND generation_attempt_id=%(generation_attempt_id)s
                     AND source_result_sha256=%(source_result_sha256)s
                     AND review_result_sha256=%(review_result_sha256)s""", values,
            ).fetchone()
            if existing is None or any(_plain(existing[key]) != _plain(value) for key, value in values.items()):
                raise JobInputError("Registered review differs from the sealed review", "staging_review_mismatch")
            return _plain(existing)

    def get_staging_review(self, review_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM public.content_generation_review WHERE review_id=%s", (_identity(review_id),)
            ).fetchone()
            return _plain(row) if row is not None else None

    def select_staging_result(self, job: dict, *, review_id: str | None = None) -> None:
        """Commit the verified result for this article before making its bundle visible."""
        review_id = _identity(review_id) if review_id is not None else None
        with self._connect() as connection:
            article = self._staging_article(connection, job, reviewed=review_id is not None)
            if review_id is not None:
                registered = connection.execute(
                    """SELECT review_id FROM public.content_generation_review
                       WHERE review_id=%(review_id)s AND job_id=%(job_id)s
                         AND generation_attempt_id=%(generation_attempt_id)s
                         AND source_result_sha256=%(result_sha256)s FOR SHARE""", {**job, "review_id": review_id},
                ).fetchone()
                if registered is None:
                    raise JobInputError("Review does not belong to the sealed result", "staging_review_mismatch")

            inserted = connection.execute(
                """INSERT INTO public.content_staging_selection
                       (article_job_id, selected_job_id, generation_attempt_id, result_sha256, review_id)
                   VALUES (%(article_job_id)s, %(job_id)s, %(generation_attempt_id)s, %(result_sha256)s, %(review_id)s)
                   ON CONFLICT (article_job_id) DO NOTHING RETURNING selected_job_id""",
                {**job, **article, "review_id": review_id},
            ).fetchone()
            if inserted is not None:
                return
            # A separate statement sees the competing transaction's committed selection.
            selected = connection.execute(
                """SELECT selected_job_id, generation_attempt_id, result_sha256, review_id FROM public.content_staging_selection
                   WHERE article_job_id=%(article_job_id)s""", article,
            ).fetchone()
            if str(selected["selected_job_id"]) != job["job_id"]:
                raise JobConflict("staging_result_already_selected", selected["selected_job_id"])
            if (str(selected["generation_attempt_id"]), selected["result_sha256"]) != (
                job["generation_attempt_id"], job["result_sha256"]
            ):
                raise JobInputError("Selected result differs from the sealed result", "staging_ledger_mismatch")
            if selected["review_id"] != review_id:
                raise JobInputError("Another review is already selected for this result", "staging_review_already_selected")

    def list_pending(self, limit: int = 100, *, expected_kind: str = "fake") -> list[dict]:
        validate_job_kind(expected_kind)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 to 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE job_kind=%s AND execution_status='queued'
                   AND generation_started_at IS NULL AND last_dispatched_at IS NULL
                   ORDER BY created_at LIMIT %s""", (expected_kind, limit),
            ).fetchall()
            return _plain(rows)

    def publish_pending(self, job_id: str, publish: Callable[[str], str], *, expected_kind: str = "fake") -> bool:
        """Serialize the broker send and its receipt with other dispatchers and worker claims."""
        validate_job_kind(expected_kind)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT job_id FROM public.content_generation_job
                   WHERE job_id=%s AND job_kind=%s AND execution_status='queued'
                   AND generation_started_at IS NULL AND last_dispatched_at IS NULL FOR UPDATE SKIP LOCKED""",
                (_identity(job_id), expected_kind),
            ).fetchone()
            if row is None:
                return False
            task_id = _task_id(publish(str(row["job_id"])))
            if task_id is None:
                raise ValueError("Publisher must return a task ID")
            connection.execute(
                """UPDATE public.content_generation_job SET last_dispatched_at=clock_timestamp(),
                   dispatch_count=dispatch_count+1,last_task_id=%s,updated_at=clock_timestamp() WHERE job_id=%s""",
                (task_id, row["job_id"]),
            )
            return True

    def requeue_delivery(self, job_id: str, task_id: str, *, expected_kind: str = "fake") -> bool:
        """Release one confirmed delivery for explicit redelivery if its exact receipt is still current."""
        validate_job_kind(expected_kind)
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET last_dispatched_at=NULL,
                   updated_at=clock_timestamp() WHERE job_id=%s AND job_kind=%s AND execution_status='queued'
                   AND generation_started_at IS NULL AND last_dispatched_at IS NOT NULL AND last_task_id=%s
                   RETURNING job_id""",
                (_identity(job_id), expected_kind, _task_id(task_id)),
            ).fetchone())

    def _claim(self, job_id: str, task_id: str | None, lease_seconds: int, *, recovery: bool, expected_kind: str) -> dict | None:
        validate_job_kind(expected_kind)
        lease_seconds, task_id = _lease(lease_seconds), _task_id(task_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE job_id=%s AND job_kind=%s
                   AND (job_kind='fake' OR menu_work_id IS NOT NULL) FOR UPDATE""",
                (_identity(job_id), expected_kind),
            ).fetchone()
            expected = "needs_attention" if recovery else "queued"
            if not row or row["execution_status"] != expected:
                return None
            if bool(row["generation_started_at"]) != recovery:
                return None
            if not recovery and self._generation_attempts(connection, row["job_id"]) >= MAX_GENERATION_ATTEMPTS:
                connection.execute(
                    """UPDATE public.content_generation_job SET execution_status='failed',
                       error_code='GENERATION_ATTEMPTS_EXHAUSTED',finished_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP WHERE job_id=%s""", (row["job_id"],),
                )
                return None
            return self._start_attempt(connection, row, task_id, lease_seconds, recovery=recovery)

    @staticmethod
    def _generation_attempts(connection, job_id) -> int:
        return connection.execute(
            """SELECT count(*) AS total FROM public.content_generation_attempt
               WHERE mode='generation' AND job_id IN (
                   SELECT job_id FROM public.content_generation_job WHERE job_id=%s OR duplicate_of_job_id=%s
               )""", (job_id, job_id),
        ).fetchone()["total"]

    @staticmethod
    def _start_attempt(connection, row, task_id, lease_seconds, *, recovery, retry_token=None):
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
               generation_started_at=CASE WHEN %s THEN generation_started_at ELSE NULL END,
               lease_expires_at=clock_timestamp() + (%s * INTERVAL '1 second'),
               error_code=NULL,finished_at=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE job_id=%s AND (NOT %s OR (claim_token=%s AND lease_expires_at>clock_timestamp())) RETURNING *""",
            (attempt, row["generation_attempt_id"] if recovery else attempt, number, token,
             recovery, lease_seconds, row["job_id"], retry_token is not None, retry_token),
        ).fetchone()
        if row is None:
            return None
        return {**_plain(row), "attempt_id": str(attempt), "mode": mode}

    def retry_failure(self, job_id: str, claim_token: str, *, error_code: str,
                      expected_kind: str, agent_not_launched: bool = False, lease_seconds: int = 30) -> dict | None:
        """Seal a safe failure and claim its next attempt without exposing a queued row.

        The same Celery delivery owns all attempts. Dispatchers and duplicate
        deliveries cannot claim the job between retries, and old tokens are fenced.
        """
        validate_job_kind(expected_kind)
        lease_seconds = _lease(lease_seconds)
        if expected_kind == "fake":
            if error_code != "fake_generation_failed" or agent_not_launched:
                return None
        elif expected_kind != "anime_generate" or not agent_not_launched or error_code not in AUTO_RETRY_PRELAUNCH_ERRORS:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE job_id=%s AND claim_token=%s
                   AND job_kind=%s AND execution_status='running' AND lease_expires_at>clock_timestamp()
                   AND duplicate_of_job_id IS NULL AND current_attempt_id=generation_attempt_id
                   AND artifact_path IS NULL AND result_sha256 IS NULL FOR UPDATE""",
                (_identity(job_id), _identity(claim_token), expected_kind),
            ).fetchone()
            if not row or self._generation_attempts(connection, row["job_id"]) >= MAX_GENERATION_ATTEMPTS:
                return None
            if expected_kind == "anime_generate":
                unsafe = connection.execute(
                    """SELECT 1 FROM public.content_generation_job AS member
                       WHERE (member.job_id=%s OR member.duplicate_of_job_id=%s) AND (
                           (member.job_id<>%s AND (member.execution_status IN ('running','succeeded')
                               OR member.generation_started_at IS NOT NULL))
                           OR member.artifact_path IS NOT NULL OR member.result_sha256 IS NOT NULL
                           OR EXISTS (SELECT 1 FROM public.content_generation_attempt AS previous
                               WHERE previous.job_id=member.job_id AND previous.attempt_id<>%s
                               AND (previous.generation_started_at IS NOT NULL
                                   OR previous.execution_status IN ('running','succeeded')
                                   OR previous.artifact_path IS NOT NULL OR previous.result_sha256 IS NOT NULL)))
                       LIMIT 1""", (row["job_id"], row["job_id"], row["job_id"], row["current_attempt_id"]),
                ).fetchone()
                if unsafe:
                    return None
            attempt = connection.execute(
                """UPDATE public.content_generation_attempt SET execution_status='failed',error_code=%s,
                   generation_started_at=CASE WHEN %s THEN NULL ELSE generation_started_at END,
                   finished_at=CURRENT_TIMESTAMP WHERE attempt_id=%s AND mode='generation'
                   AND execution_status='running' RETURNING celery_task_id""",
                (error_code, agent_not_launched, row["current_attempt_id"]),
            ).fetchone()
            if attempt is None:
                return None
            next_job = self._start_attempt(connection, row, attempt["celery_task_id"], lease_seconds,
                                           recovery=False, retry_token=row["claim_token"])
            if next_job is None:
                # A blocked attempt update may outlive the lease. Neither the
                # failure nor the new attempt may survive that lost claim.
                connection.rollback()
            return next_job

    def claim(self, job_id: str, task_id: str | None = None, lease_seconds: int = 30,
              *, expected_kind: str = "fake") -> dict | None:
        return self._claim(job_id, task_id, lease_seconds, recovery=False, expected_kind=expected_kind)

    def claim_recovery(self, job_id: str, task_id: str | None = None, lease_seconds: int = 30,
                       *, expected_kind: str = "fake") -> dict | None:
        return self._claim(job_id, task_id, lease_seconds, recovery=True, expected_kind=expected_kind)

    def mark_generation_started(self, job_id: str, claim_token: str, *, expected_kind: str = "fake") -> bool:
        validate_job_kind(expected_kind)
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE public.content_generation_job SET generation_started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s AND claim_token=%s AND job_kind=%s AND execution_status='running'
                   AND (job_kind='fake' OR menu_work_id IS NOT NULL)
                   AND lease_expires_at>clock_timestamp() AND generation_started_at IS NULL
                   AND current_attempt_id=generation_attempt_id RETURNING current_attempt_id""",
                (_identity(job_id), _identity(claim_token), expected_kind),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE public.content_generation_attempt SET generation_started_at=CURRENT_TIMESTAMP WHERE attempt_id=%s",
                    (row["current_attempt_id"],),
                )
            return bool(row)

    def renew_lease(self, job_id: str, claim_token: str, lease_seconds: int = 30,
                    *, expected_kind: str = "fake") -> bool:
        validate_job_kind(expected_kind)
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET lease_expires_at=clock_timestamp() + (%s * INTERVAL '1 second'),
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=%s AND claim_token=%s AND job_kind=%s AND execution_status='running'
                   AND lease_expires_at>clock_timestamp() RETURNING job_id""",
                (_lease(lease_seconds), _identity(job_id), _identity(claim_token), expected_kind),
            ).fetchone())

    def complete(self, job_id: str, claim_token: str, *, execution_status: str, outcome: str | None = None,
                 producer_status: str | None = None, artifact_path: str | None = None,
                 result_sha256: str | None = None, error_code: str | None = None,
                 expected_kind: str = "fake", agent_not_launched: bool = False) -> bool:
        validate_job_kind(expected_kind)
        if agent_not_launched and (expected_kind != "anime_generate" or execution_status != "failed"
                                   or error_code not in PRELAUNCH_ERRORS or artifact_path or result_sha256):
            raise ValueError("Unconfirmed pre-launch failure")
        if execution_status not in {"succeeded", "failed", "needs_attention"}:
            raise ValueError("Unsupported completion status")
        if expected_kind == "fake" and outcome not in {None, "ready_for_staging", "held", "skipped"}:
            raise ValueError("Unsupported fake producer outcome")
        if expected_kind == "anime":
            if execution_status == "succeeded":
                if (outcome, producer_status) != ("routing_verified", "anime_probe_completed"):
                    raise ValueError("An anime probe can only record routing verification")
            elif outcome is not None:
                raise ValueError("An incomplete anime probe cannot record an outcome")
        if expected_kind == "anime_generate":
            if execution_status == "succeeded":
                if outcome not in {"ready_for_staging", "held", "skipped", "rejected"}:
                    raise ValueError("Unsupported live producer outcome")
            elif outcome is not None:
                raise ValueError("An incomplete generation cannot record a producer outcome")
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
                   AND job_kind=%s AND execution_status='running' FOR UPDATE""",
                (_identity(job_id), _identity(claim_token), expected_kind),
            ).fetchone()
            if not row:
                return False
            if agent_not_launched and row["current_attempt_id"] != row["generation_attempt_id"]:
                return False
            if execution_status == "succeeded" and row["generation_started_at"] is None:
                return False
            if artifact_path is not None and not artifact_path.startswith(f"jobs/{row['job_id']}/attempts/{row['generation_attempt_id']}/"):
                raise ValueError("Artifact path must refer to the original generation attempt")
            values = (execution_status, outcome, producer_status, artifact_path, result_sha256, error_code)
            completed = connection.execute(
                """UPDATE public.content_generation_job SET execution_status=%s,outcome=%s,producer_status=%s,
                   artifact_path=%s,result_sha256=%s,error_code=%s,finished_at=CURRENT_TIMESTAMP,
                   generation_started_at=CASE WHEN %s THEN NULL ELSE generation_started_at END,
                   claim_token=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%s AND lease_expires_at>clock_timestamp() RETURNING job_id""",
                (*values, agent_not_launched, row["job_id"]),
            ).fetchone()
            if not completed:
                return False
            connection.execute(
                """UPDATE public.content_generation_attempt SET execution_status=%s,outcome=%s,producer_status=%s,
                   artifact_path=%s,result_sha256=%s,error_code=%s,finished_at=CURRENT_TIMESTAMP,
                   generation_started_at=CASE WHEN %s THEN NULL ELSE generation_started_at END WHERE attempt_id=%s""",
                (*values, agent_not_launched, row["current_attempt_id"]),
            )
            return True

    def recover_expired(self, *, expected_kind: str = "fake") -> dict[str, int]:
        validate_job_kind(expected_kind)
        counts = {"queued": 0, "needs_attention": 0}
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM public.content_generation_job WHERE job_kind=%s AND execution_status='running'
                   AND lease_expires_at<=clock_timestamp() FOR UPDATE SKIP LOCKED""", (expected_kind,),
            ).fetchall()
            for row in rows:
                started = row["generation_started_at"] is not None
                status = "needs_attention" if started else "queued"
                code = "GENERATION_INTERRUPTED" if started else "LEASE_EXPIRED_BEFORE_GENERATION"
                if not started and self._generation_attempts(connection, row["job_id"]) >= MAX_GENERATION_ATTEMPTS:
                    status, code = "failed", "GENERATION_ATTEMPTS_EXHAUSTED"
                if row["job_kind"] == "anime" and row["menu_work_id"] is None:
                    status = "needs_attention" if started else "cancelled"
                    code = "LEGACY_ANIME_FIXTURE_RETIRED"
                connection.execute(
                    """UPDATE public.content_generation_attempt SET execution_status=%s,error_code=%s,
                       finished_at=CURRENT_TIMESTAMP WHERE attempt_id=%s""",
                    ("failed" if status == "queued" else status, code, row["current_attempt_id"]),
                )
                connection.execute(
                    """UPDATE public.content_generation_job SET execution_status=%s,error_code=%s,
                       claim_token=NULL,lease_expires_at=NULL,last_dispatched_at=NULL,updated_at=CURRENT_TIMESTAMP,
                       finished_at=CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE finished_at END
                       WHERE job_id=%s""", (status, code, status in {"cancelled", "failed"}, row["job_id"]),
                )
                counts[status] = counts.get(status, 0) + 1
        return counts

    def retry_preflight(self, job_id: str, *, expected_kind: str) -> bool:
        """Requeue the live owner only if no job for the article may have launched its producer."""
        validate_job_kind(expected_kind)
        if expected_kind != "anime_generate":
            return False
        with self._connect() as connection:
            return bool(connection.execute(
                """WITH article_jobs AS (
                       SELECT job_id, execution_status, generation_started_at, generation_attempt_id,
                              artifact_path, result_sha256
                       FROM public.content_generation_job
                       WHERE job_id=%(job_id)s OR duplicate_of_job_id=%(job_id)s
                   ), article_attempts AS (
                       SELECT attempt.* FROM public.content_generation_attempt AS attempt
                       JOIN article_jobs USING (job_id)
                   ), legacy_prelaunch AS (
                       SELECT attempt_id FROM article_attempts
                       WHERE mode='generation' AND execution_status='needs_attention'
                         AND generation_started_at IS NOT NULL AND error_code=ANY(%(setup_errors)s)
                         AND artifact_path IS NULL AND result_sha256 IS NULL
                   )
                   UPDATE public.content_generation_job AS job SET execution_status='queued', generation_started_at=NULL,
                   current_attempt_id=NULL, generation_attempt_id=NULL, claim_token=NULL, lease_expires_at=NULL,
                   last_dispatched_at=NULL, last_task_id=NULL, error_code=NULL, finished_at=NULL,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE job_id=%(job_id)s AND job_kind='anime_generate' AND duplicate_of_job_id IS NULL
                   AND (SELECT count(*) FROM article_attempts WHERE mode='generation') < %(max_attempts)s
                   AND artifact_path IS NULL AND result_sha256 IS NULL
                   AND ((execution_status='failed' AND generation_started_at IS NULL AND error_code=ANY(%(errors)s))
                        OR (execution_status='needs_attention' AND error_code=ANY(%(setup_errors)s)
                            AND current_attempt_id=generation_attempt_id
                            AND current_attempt_id IN (SELECT attempt_id FROM legacy_prelaunch)))
                   AND NOT EXISTS (
                       SELECT 1 FROM article_jobs AS member
                       WHERE member.execution_status IN ('running','succeeded')
                          OR member.artifact_path IS NOT NULL OR member.result_sha256 IS NOT NULL
                          OR (member.generation_started_at IS NOT NULL AND NOT EXISTS (
                              SELECT 1 FROM legacy_prelaunch WHERE attempt_id=member.generation_attempt_id))
                   )
                   AND NOT EXISTS (
                       SELECT 1 FROM article_attempts
                       WHERE execution_status='succeeded' OR artifact_path IS NOT NULL OR result_sha256 IS NOT NULL
                          OR (generation_started_at IS NOT NULL AND attempt_id NOT IN (SELECT attempt_id FROM legacy_prelaunch))
                   )
                   RETURNING job_id""", {"job_id": _identity(job_id), "errors": list(PRELAUNCH_ERRORS),
                                         "setup_errors": list(PRELAUNCH_SETUP_ERRORS),
                                         "max_attempts": MAX_GENERATION_ATTEMPTS},
            ).fetchone())

    def cancel(self, job_id: str, *, expected_kind: str = "fake") -> bool:
        validate_job_kind(expected_kind)
        with self._connect() as connection:
            return bool(connection.execute(
                """UPDATE public.content_generation_job SET execution_status='cancelled',finished_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=%s AND job_kind=%s AND execution_status='queued'
                   AND generation_started_at IS NULL RETURNING job_id""", (_identity(job_id), expected_kind),
            ).fetchone())
