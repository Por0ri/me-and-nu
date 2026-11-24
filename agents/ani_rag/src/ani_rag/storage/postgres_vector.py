"""Dedicated pgvector sidecar; the relational RAG database stays read-only.

No model client, credential-file reader, or worker is imported here. Existing
float32 bytes and source IDs are retained. Writes use explicit transactions and
one advisory lock so a failed transfer/update can safely resume.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import re
import sqlite3
import struct
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
SCHEMA = "rag_vector"
LOCK_ID = 741836025
CHUNK_FIELDS = (
    "chunk_id", "document_id", "source_id", "source_grade", "content_type",
    "evidence_type", "title", "source_url", "language", "published_at",
    "source_kind", "source_index", "locator", "start_offset", "end_offset",
    "text_hash", "chunk_text",
)
VECTOR_FIELDS = ("text_hash", "model", "dimensions", "vector_f32", "input_tokens", "created_at")


def connect_postgres(database_url: str, *, expected_database: str, read_only: bool = False):
    """Open an explicitly named RAG-only database without printing its DSN."""
    if not re.fullmatch(r"menu_rag_vector(?:_[a-z0-9]+)*", expected_database):
        raise ValueError("Expected a dedicated menu_rag_vector[_suffix] database")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row

    options = conninfo_to_dict(database_url)
    if options.get("dbname") != expected_database or options.get("options"):
        raise ValueError("Database URL does not match the explicitly selected RAG database")
    connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    try:
        if connection.execute("SELECT current_database() AS name").fetchone()["name"] != expected_database:
            raise ValueError("Connected database does not match the selected RAG database")
        if read_only:
            connection.execute("SET default_transaction_read_only = on")
        return connection
    except BaseException:
        connection.close()
        raise


def open_sqlite_readonly(path: Path, *, immutable_snapshot: bool = False) -> sqlite3.Connection:
    """Read a live DB normally, or an explicitly frozen/checksummed handoff.

    SQLite may need writable shared-memory files for a WAL-mode database even
    when its main file is opened read-only. A verified, frozen snapshot can use
    immutable mode, but a live database must never do so: WAL/journal changes
    would be ignored. Refuse visible recovery data before opening that mode.
    """
    path = path.resolve(strict=True)
    if immutable_snapshot:
        for suffix in ("-wal", "-journal"):
            journal = Path(str(path) + suffix)
            if journal.exists() and journal.stat().st_size:
                raise ValueError("Frozen snapshot has nonempty WAL/journal recovery data")
    options = "?mode=ro&immutable=1" if immutable_snapshot else "?mode=ro"
    connection = sqlite3.connect(path.as_uri() + options, uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in connection.execute(
        "SELECT key,value FROM rag_vector.index_metadata"
    ).fetchall()}


def _set_metadata(connection, values: Mapping[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO rag_vector.index_metadata(key,value) VALUES(%s,%s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            list(values.items()),
        )


def _validate_metadata(metadata: Mapping[str, str]) -> None:
    if (metadata.get("model") != MODEL or metadata.get("dimensions") != str(DIMENSIONS)
            or not metadata.get("completed_at")):
        raise ValueError("A completed text-embedding-3-small 1024-dimension index is required")
    if metadata.get("max_chars") != "1200" or metadata.get("overlap") != "120":
        raise ValueError("The existing index chunking contract must be preserved")


def _validate_blob(blob: bytes) -> tuple[float, ...]:
    if len(blob) != DIMENSIONS * 4:
        raise ValueError("Stored vector does not contain exactly 1024 float32 values")
    values = struct.unpack("<1024f", blob)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Stored vector contains a non-finite value")
    return values


def _pg_vector_bytes(blob: bytes) -> bytes:
    _validate_blob(blob)
    values = array.array("f")
    values.frombytes(blob)
    # The preserved payload is always little-endian; pgvector wire values are
    # always big-endian, independent of the host's native array byte order.
    values.byteswap()
    return struct.pack("!HH", DIMENSIONS, 0) + values.tobytes()


def _copy_binary(connection, table: str, fields: Sequence[str], rows: Iterable[Sequence[Any]],
                 encoders: Sequence[Callable[[Any], bytes]]) -> None:
    """Stream Postgres binary COPY, including pgvector's binary representation."""
    with connection.cursor().copy(
        f"COPY {table} ({','.join(fields)}) FROM STDIN WITH (FORMAT BINARY)"
    ) as copy:
        copy.write(b"PGCOPY\n\xff\r\n\x00" + struct.pack("!ii", 0, 0))
        buffer = bytearray()
        for row in rows:
            buffer.extend(struct.pack("!h", len(fields)))
            for value, encode in zip(row, encoders, strict=True):
                if value is None:
                    buffer.extend(struct.pack("!i", -1))
                else:
                    encoded = encode(value)
                    buffer.extend(struct.pack("!i", len(encoded)))
                    buffer.extend(encoded)
            if len(buffer) >= 4 * 1024 * 1024:
                copy.write(buffer)
                buffer.clear()
        if buffer:
            copy.write(buffer)
        copy.write(struct.pack("!h", -1))


def _text(value: Any) -> bytes:
    return str(value).encode("utf-8")


def _int(value: Any) -> bytes:
    return struct.pack("!i", int(value))


def _source_prefix(source: sqlite3.Connection, through: int, *, after: int = 0,
                   previous: str = "00" * 32) -> str:
    digest = bytes.fromhex(previous)
    for row in source.execute(
        "SELECT raw_document_id,document_id,source_id,url,content_hash FROM rag_document "
        "WHERE raw_document_id>? AND raw_document_id<=? ORDER BY raw_document_id", (after, through),
    ):
        row_bytes = json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(digest + row_bytes + b"\n").digest()
    return digest.hex()


def _advance_source_binding(connection, source: sqlite3.Connection, through: int) -> None:
    metadata = _metadata(connection)
    previous = int(metadata["source_prefix_through"])
    if through > previous:
        _set_metadata(connection, {
            "source_prefix_through": str(through),
            "source_prefix_sha256": _source_prefix(
                source, through, after=previous, previous=metadata["source_prefix_sha256"],
            ),
        })


@contextmanager
def _sqlite_snapshot(source: sqlite3.Connection):
    own_transaction = not source.in_transaction
    if own_transaction:
        source.execute("BEGIN")
    try:
        yield
    finally:
        if own_transaction:
            source.rollback()


def _assert_readonly(source: sqlite3.Connection) -> None:
    if source.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise ValueError("The source SQLite connection must be read-only")


def _validate_source_links(source: sqlite3.Connection, index: sqlite3.Connection) -> None:
    """Verify every saved link with two sequential scans of frozen SQLite files.

    Joining an attached source by document ID causes one random index probe per
    chunk, which is prohibitively slow on read-only Windows bind mounts. Only
    source identifiers/URLs are retained in memory; text, images, and vectors
    are never materialized here.
    """
    documents = {
        row["document_id"]: (row["source_id"], row["url"])
        for row in source.execute("SELECT document_id,source_id,url FROM rag_document")
    }
    for row in index.execute("SELECT document_id,source_id,source_url FROM evidence_chunk"):
        link = documents.get(row["document_id"])
        if link is None or link != (row["source_id"], row["source_url"]):
            raise ValueError("Sidecar evidence does not match the supplied source snapshot")


def _assert_dedicated_connection(connection) -> None:
    row = connection.execute("SELECT current_database() AS name").fetchone()
    if not re.fullmatch(r"menu_rag_vector(?:_[a-z0-9]+)*", row["name"]):
        raise ValueError("Vector adapters require a dedicated menu_rag_vector[_suffix] database")


class PostgresVectorIndex:
    """Read-only query adapter; callers own and close the psycopg connection."""

    def __init__(self, connection):
        if not connection.autocommit:
            raise ValueError("Use an idle autocommit connection for the vector reader")
        _assert_dedicated_connection(connection)
        self.connection = connection
        self._lock = threading.RLock()

    def fetch_chunks(self, document_ids: tuple[str, ...], query_vector: tuple[float, ...]):
        if not document_ids:
            return []
        if len(query_vector) != DIMENSIONS or not all(math.isfinite(v) for v in query_vector):
            raise ValueError("Query vector must contain 1024 finite values")
        if not any(query_vector):
            raise ValueError("Query vector cannot be zero")
        vector = "[" + ",".join(repr(float(value)) for value in query_vector) + "]"
        with self._lock, self.connection.transaction():
            self.connection.execute("SET TRANSACTION READ ONLY")
            metadata = _metadata(self.connection)
            _validate_metadata(metadata)
            if metadata.get("migration_status") != "complete":
                raise RuntimeError("The PostgreSQL vector migration has not completed")
            return self.connection.execute(
                "SELECT c.*, CASE WHEN (v.embedding <#> v.embedding)=0 THEN NULL "
                "ELSE 1 - (v.embedding <=> %s::public.vector) END AS cosine_similarity "
                "FROM rag_vector.evidence_chunk c JOIN rag_vector.embedding_vector v USING(text_hash) "
                "WHERE c.document_id = ANY(%s)", (vector, list(document_ids)),
            ).fetchall()


class PostgresVectorStore:
    """Explicit writer for a dedicated sidecar, never for the service database."""

    def __init__(self, connection):
        if not connection.autocommit:
            raise ValueError("Use an idle autocommit connection for the vector writer")
        _assert_dedicated_connection(connection)
        self.connection = connection

    def initialize(self) -> None:
        with self.connection.transaction():
            self.connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            self.connection.execute("CREATE SCHEMA IF NOT EXISTS rag_vector")
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS rag_vector.index_metadata(
                    key text PRIMARY KEY, value text NOT NULL);
                CREATE TABLE IF NOT EXISTS rag_vector.embedding_vector(
                    text_hash text PRIMARY KEY CHECK(length(text_hash)=64),
                    model text NOT NULL CHECK(model='text-embedding-3-small'),
                    dimensions integer NOT NULL CHECK(dimensions=1024),
                    vector_f32 bytea NOT NULL CHECK(octet_length(vector_f32)=4096),
                    input_tokens integer NOT NULL CHECK(input_tokens>=0),
                    created_at text NOT NULL,
                    embedding public.vector(1024) NOT NULL);
                CREATE TABLE IF NOT EXISTS rag_vector.evidence_chunk(
                    chunk_id text PRIMARY KEY, document_id text NOT NULL,
                    source_id text NOT NULL, source_grade text NOT NULL,
                    content_type text NOT NULL, evidence_type text NOT NULL,
                    title text NOT NULL, source_url text NOT NULL, language text NOT NULL,
                    published_at text, source_kind text NOT NULL, source_index integer,
                    locator text NOT NULL, start_offset integer NOT NULL, end_offset integer NOT NULL,
                    text_hash text NOT NULL REFERENCES rag_vector.embedding_vector(text_hash),
                    chunk_text text NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_rag_vector_chunk_document
                    ON rag_vector.evidence_chunk(document_id);
                CREATE INDEX IF NOT EXISTS ix_rag_vector_chunk_scope
                    ON rag_vector.evidence_chunk(source_id,content_type,evidence_type);
            """)

    @contextmanager
    def locked(self):
        acquired = self.connection.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_ID,),
        ).fetchone()["acquired"]
        if not acquired:
            raise RuntimeError("Another vector transfer or update is already running")
        try:
            yield
        finally:
            self.connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))

    def counts(self) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT (SELECT count(*) FROM rag_vector.embedding_vector) AS vectors, "
            "(SELECT count(*) FROM rag_vector.evidence_chunk) AS chunks"
        ).fetchone()
        return {key: int(value) for key, value in row.items()}

    def _stage_vectors(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        self.connection.execute("CREATE TEMP TABLE IF NOT EXISTS rag_vector_stage "
                                "(LIKE rag_vector.embedding_vector INCLUDING DEFAULTS) ON COMMIT DELETE ROWS")
        values = []
        for row in rows:
            if row["model"] != MODEL or row["dimensions"] != DIMENSIONS:
                raise ValueError("Cannot mix embedding models or dimensions")
            blob = bytes(row["vector_f32"])
            values.append(tuple(row[key] for key in VECTOR_FIELDS) + (_pg_vector_bytes(blob),))
        _copy_binary(self.connection, "pg_temp.rag_vector_stage", VECTOR_FIELDS + ("embedding",), values,
                     (_text, _text, _int, bytes, _int, _text, bytes))
        mismatch = self.connection.execute("""
            SELECT 1 FROM rag_vector_stage s JOIN rag_vector.embedding_vector v USING(text_hash)
            WHERE ROW(s.model,s.dimensions,s.vector_f32,s.input_tokens,s.created_at)
               IS DISTINCT FROM ROW(v.model,v.dimensions,v.vector_f32,v.input_tokens,v.created_at)
            LIMIT 1
        """).fetchone()
        if mismatch:
            raise ValueError("Existing vector identity has different bytes or provenance")
        result = self.connection.execute(
            "INSERT INTO rag_vector.embedding_vector SELECT * FROM rag_vector_stage ON CONFLICT(text_hash) DO NOTHING"
        )
        return result.rowcount

    def _stage_chunks(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        self.connection.execute("CREATE TEMP TABLE IF NOT EXISTS rag_chunk_stage "
                                "(LIKE rag_vector.evidence_chunk INCLUDING DEFAULTS) ON COMMIT DELETE ROWS")
        for row in rows:
            if hashlib.sha256(str(row["chunk_text"]).encode("utf-8")).hexdigest() != row["text_hash"]:
                raise ValueError("Chunk text does not match its saved text hash")
        values = [tuple(row[key] for key in CHUNK_FIELDS) for row in rows]
        encoders = [_int if field in {"source_index", "start_offset", "end_offset"} else _text for field in CHUNK_FIELDS]
        _copy_binary(self.connection, "pg_temp.rag_chunk_stage", CHUNK_FIELDS, values, encoders)
        fields = CHUNK_FIELDS[1:]
        mismatch = self.connection.execute(
            "SELECT 1 FROM rag_chunk_stage s JOIN rag_vector.evidence_chunk c USING(chunk_id) WHERE ROW(" +
            ",".join("s." + key for key in fields) + ") IS DISTINCT FROM ROW(" +
            ",".join("c." + key for key in fields) + ") LIMIT 1"
        ).fetchone()
        if mismatch:
            raise ValueError("Existing chunk identity has different evidence or source links")
        result = self.connection.execute(
            "INSERT INTO rag_vector.evidence_chunk SELECT * FROM rag_chunk_stage ON CONFLICT(chunk_id) DO NOTHING"
        )
        return result.rowcount

    def import_sqlite(self, *, source_path: Path, index_path: Path, batch_size: int = 2000,
                      immutable_snapshot: bool = False,
                      progress: Callable[[Mapping[str, Any]], None] | None = None) -> dict[str, Any]:
        """Resume an immutable handoff. Original metadata/checkpoints are preserved."""
        if not 1 <= batch_size <= 10000:
            raise ValueError("batch_size must be between 1 and 10000")
        source_path, index_path = source_path.resolve(strict=True), index_path.resolve(strict=True)
        source_sha, index_sha = file_sha256(source_path), file_sha256(index_path)
        with closing(open_sqlite_readonly(source_path, immutable_snapshot=immutable_snapshot)) as source, \
                closing(open_sqlite_readonly(index_path, immutable_snapshot=immutable_snapshot)) as index:
            try:
                metadata = dict(index.execute("SELECT key,value FROM index_metadata").fetchall())
                _validate_metadata(metadata)
                # Compare immutable IDs and source links before writing any target rows.
                _validate_source_links(source, index)
                high_water = int(source.execute("SELECT COALESCE(MAX(raw_document_id),0) FROM rag_document").fetchone()[0])
                reference_high_water = int(source.execute("SELECT COALESCE(MAX(raw_document_id),0) FROM rag_document "
                                                          "WHERE evidence_type='reference_fact'").fetchone()[0])
                if int(metadata.get("last_source_raw_document_id", "0")) > reference_high_water:
                    raise ValueError("Index checkpoint exceeds the source snapshot")
                original_counts = {
                    "vectors": index.execute("SELECT count(*) FROM embedding_vector").fetchone()[0],
                    "chunks": index.execute("SELECT count(*) FROM evidence_chunk").fetchone()[0],
                }
                with self.locked():
                    self.initialize()
                    existing = _metadata(self.connection)
                    if existing:
                        if (existing.get("import_source_sha256") != source_sha
                                or existing.get("import_index_sha256") != index_sha):
                            raise ValueError("Destination is bound to a different source/index snapshot")
                        if existing.get("migration_status") == "complete":
                            return {"status": "already_imported", **self.counts(), "model_calls": 0}
                        if existing.get("import_cursor_format") != "sqlite_rowid_v1":
                            raise RuntimeError("Incomplete import uses an unsupported transfer cursor format")
                    else:
                        if any(self.counts().values()):
                            raise ValueError("Refusing to adopt nonempty unbound vector tables")
                        with self.connection.transaction():
                            _set_metadata(self.connection, {
                                **metadata, "adapter_schema_version": "1", "migration_status": "loading",
                                "import_cursor_format": "sqlite_rowid_v1",
                                "import_source_sha256": source_sha, "import_index_sha256": index_sha,
                                "bound_source_path": str(source_path), "source_prefix_through": str(high_water),
                                "source_prefix_sha256": _source_prefix(source, high_water),
                                "import_started_at": _now(),
                            })
                    for table, fields, save in (
                        ("embedding_vector", VECTOR_FIELDS, self._stage_vectors),
                        ("evidence_chunk", CHUNK_FIELDS, self._stage_chunks),
                    ):
                        # The SHA-bound snapshot cannot change rowids. Scanning
                        # them sequentially avoids random blob/page reads through
                        # the text-hash/chunk-ID secondary indexes on bind mounts.
                        cursor_key = "import_after_rowid_" + table
                        saved_cursor = _metadata(self.connection).get(cursor_key)
                        after = int(saved_cursor) if saved_cursor is not None else None
                        while True:
                            where = " WHERE rowid>?" if after is not None else ""
                            parameters = (after, batch_size) if after is not None else (batch_size,)
                            rows = index.execute(
                                f"SELECT rowid AS _import_rowid,{','.join(fields)} FROM {table}"
                                f"{where} ORDER BY rowid LIMIT ?", parameters,
                            ).fetchall()
                            if not rows:
                                break
                            after = int(rows[-1]["_import_rowid"])
                            with self.connection.transaction():
                                inserted = save(rows)
                                _set_metadata(self.connection, {cursor_key: str(after)})
                            if progress:
                                progress({"phase": table, "inserted": inserted})
                    actual = self.counts()
                    if actual != original_counts:
                        raise ValueError("Imported table counts differ from the supplied sidecar")
                    with self.connection.transaction():
                        _set_metadata(self.connection, {"migration_status": "complete", "import_completed_at": _now()})
                    return {"status": "imported", **actual, "model_calls": 0,
                            "source_sha256": source_sha, "index_sha256": index_sha}
            finally:
                source.close()
                index.close()

    def update_from_source(
        self, source: sqlite3.Connection, *,
        chunk_iterator: Callable[..., Iterable[Mapping[str, Any]]],
        embedding_provider: Callable[[Mapping[str, str]], Mapping[str, bytes]] | None = None,
        batch_size: int = 512, after_batch: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Append reference evidence using existing vectors; never call a model by default.

        An explicitly supplied provider is a dependency-injection seam, not a
        configured production client. Missing hashes fail closed without one.
        Saved batches are reusable; the document crossing a batch boundary stays
        before the checkpoint until its final chunk is committed.
        """
        _assert_readonly(source)
        if not 1 <= batch_size <= 10000:
            raise ValueError("batch_size must be between 1 and 10000")
        with self.locked(), _sqlite_snapshot(source):
            metadata = _metadata(self.connection)
            _validate_metadata(metadata)
            if metadata.get("migration_status") != "complete":
                raise RuntimeError("The PostgreSQL vector migration has not completed")
            through = int(metadata["source_prefix_through"])
            if _source_prefix(source, through) != metadata["source_prefix_sha256"]:
                raise ValueError("Source identity/history differs from the imported snapshot")
            checkpoint = int(metadata.get("last_source_raw_document_id", "0"))
            high_water = int(source.execute(
                "SELECT COALESCE(MAX(raw_document_id),0) FROM rag_document WHERE evidence_type='reference_fact'"
            ).fetchone()[0])
            if high_water < checkpoint:
                raise ValueError("Source is behind the saved index checkpoint")
            totals = {"chunks_added": 0, "vectors_added": 0, "provider_calls": 0}
            pending: list[Mapping[str, Any]] = []

            def flush():
                if not pending:
                    return
                # Deduplicate shared text inside a batch before resolving missing vectors.
                texts = {str(row["text_hash"]): str(row["chunk_text"]) for row in pending}
                saved = {row["text_hash"] for row in self.connection.execute(
                    "SELECT text_hash FROM rag_vector.embedding_vector WHERE text_hash=ANY(%s)", (list(texts),),
                ).fetchall()}
                missing = {key: text for key, text in texts.items() if key not in saved}
                vectors = []
                if missing:
                    if embedding_provider is None:
                        raise RuntimeError("Missing saved vectors; paid embedding is disabled")
                    supplied = embedding_provider(missing)
                    totals["provider_calls"] += 1
                    if set(supplied) != set(missing):
                        raise ValueError("Embedding provider result does not match the requested hashes")
                    vectors = [{"text_hash": key, "model": MODEL, "dimensions": DIMENSIONS,
                                "vector_f32": bytes(blob), "input_tokens": 0, "created_at": _now()}
                               for key, blob in supplied.items()]
                completed_through = max(int(row["source_raw_document_id"]) for row in pending) - 1
                with self.connection.transaction():
                    totals["vectors_added"] += self._stage_vectors(vectors)
                    totals["chunks_added"] += self._stage_chunks(pending)
                    current = int(_metadata(self.connection).get("last_source_raw_document_id", "0"))
                    _set_metadata(self.connection, {
                        "last_source_raw_document_id": str(max(current, completed_through)),
                        "incremental_updated_at": _now(),
                    })
                    _advance_source_binding(self.connection, source, completed_through)
                pending.clear()
                if after_batch:
                    after_batch()

            for chunk in chunk_iterator(source, after_raw_document_id=checkpoint,
                                        through_raw_document_id=high_water):
                pending.append(chunk)
                if len(pending) >= batch_size:
                    flush()
            flush()
            with self.connection.transaction():
                _set_metadata(self.connection, {"last_source_raw_document_id": str(high_water),
                                                "incremental_updated_at": _now()})
                _advance_source_binding(self.connection, source, high_water)
            return {"status": "complete", "checkpoint_before": checkpoint, "checkpoint_after": high_water,
                    **totals, **self.counts()}
