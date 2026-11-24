"""Stored-vector migration/update checks; no API client is used in these tests."""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

import pytest

from ani_rag.storage.postgres_vector import (
    DIMENSIONS, MODEL, PostgresVectorIndex, PostgresVectorStore,
    connect_postgres, file_sha256, open_sqlite_readonly,
)

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["me_nu_checking", "anime_db", "menu_dict", "menu_content_jobs", "postgres", "menu_rag_vector_test;drop"])
def test_service_databases_rejected_before_connection(name):
    with pytest.raises(ValueError, match="dedicated"):
        connect_postgres("postgresql://not-used", expected_database=name)


def test_source_sqlite_cannot_be_written(tmp_path):
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sentinel(value text)")
    before = file_sha256(path)
    with closing(open_sqlite_readonly(path)) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("INSERT INTO sentinel VALUES('changed')")
    assert file_sha256(path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_frozen_snapshot_rejects_recovery_data(tmp_path, suffix):
    path = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE snapshot(value text)")
    Path(str(path) + suffix).write_bytes(b"uncheckpointed recovery data")
    before = file_sha256(path)
    with pytest.raises(ValueError, match="recovery data"):
        open_sqlite_readonly(path, immutable_snapshot=True)
    assert file_sha256(path) == before


@pytest.mark.skipif(os.name != "posix", reason="Tests SQLite WAL recovery under real POSIX read-only directory permissions")
def test_frozen_wal_header_snapshot_reads_without_creating_shared_memory():
    # Own synthetic files only. An unprivileged subprocess makes directory
    # permissions effective even when the Docker test process itself is root.
    with tempfile.TemporaryDirectory(prefix="rag-frozen-snapshot-") as directory:
        folder = Path(directory)
        path = folder / "snapshot.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE snapshot(value text)")
            db.execute("INSERT INTO snapshot VALUES('preserved')")
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert path.read_bytes()[18:20] == b"\x02\x02"
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
        before = file_sha256(path)
        path.chmod(0o444)
        folder.chmod(0o555)
        program = """
import sqlite3, sys
from pathlib import Path
from ani_rag.storage.postgres_vector import open_sqlite_readonly
path=Path(sys.argv[1])
ordinary=open_sqlite_readonly(path)
try:
    ordinary.execute('SELECT value FROM snapshot').fetchone()
except sqlite3.OperationalError:
    pass
else:
    raise AssertionError('WAL reader unexpectedly initialized shared memory in a read-only directory')
finally:
    ordinary.close()
frozen=open_sqlite_readonly(path, immutable_snapshot=True)
try:
    assert frozen.execute('SELECT value FROM snapshot').fetchone()[0] == 'preserved'
    assert frozen.execute('PRAGMA query_only').fetchone()[0] == 1
finally:
    frozen.close()
"""
        options = {"user": 65534, "group": 65534} if os.geteuid() == 0 else {}
        try:
            completed = subprocess.run([sys.executable, "-c", program, str(path)],
                                       cwd="/tmp", capture_output=True, text=True, **options)
            assert completed.returncode == 0, completed.stderr
            assert file_sha256(path) == before
            assert not Path(str(path) + "-wal").exists()
            assert not Path(str(path) + "-shm").exists()
        finally:
            folder.chmod(0o755)
            path.chmod(0o644)


@pytest.fixture
def pg():
    dsn = os.environ.get("RAG_VECTOR_TEST_DSN")
    if not dsn:
        pytest.skip("RAG_VECTOR_TEST_DSN must target an isolated PostgreSQL service")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    settings = conninfo_to_dict(dsn)
    assert settings["dbname"] == "menu_rag_vector_test"
    assert settings.get("host") in {"127.0.0.1", "localhost", "db"}
    name = "menu_rag_vector_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    test_dsn = make_conninfo(dsn, dbname=name)
    connection = connect_postgres(test_dsn, expected_database=name)
    try:
        yield connection, test_dsn, name
    finally:
        connection.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def add_document(path, identity, text):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO rag_document(document_id,source_id,content_type,evidence_type,title,url,"
            "original_language,published_at,raw_text,content_hash) VALUES(?, 'source', 'guide',"
            "'reference_fact', ?, ?, 'en', NULL, ?, ?)",
            (identity, identity, "https://example.test/" + identity, text, hashlib.sha256(text.encode()).hexdigest()),
        )


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT / "scripts"))
    build = importlib.import_module("build_vector_evidence_index")
    source = tmp_path / "source.sqlite3"
    index = tmp_path / "index.sqlite3"
    with closing(sqlite3.connect(source)) as db, db:
        db.executescript("""
            CREATE TABLE rag_source(source_id text PRIMARY KEY,source_grade text);
            INSERT INTO rag_source VALUES('source','official');
            CREATE TABLE rag_document(raw_document_id integer PRIMARY KEY AUTOINCREMENT,
              document_id text UNIQUE,source_id text,content_type text,evidence_type text,title text,
              url text,original_language text,published_at text,raw_text text,content_hash text);
            CREATE TABLE rag_excerpt(excerpt_id integer PRIMARY KEY,document_id text,locator text,excerpt_text text);
        """)
    add_document(source, "one", "original evidence")
    with closing(open_sqlite_readonly(source)) as db:
        chunks = list(build._iter_evidence_chunks(db))
    with closing(build._open_index(index, source)) as db, db:
        for chunk in chunks:
            db.execute("INSERT INTO embedding_vector VALUES(?,?,?,?,?,?)", (
                chunk["text_hash"], MODEL, DIMENSIONS, struct.pack("<1024f", 1.0, *([0.0] * 1023)), 3, "2026-11-24",
            ))
            from ani_rag.storage.postgres_vector import CHUNK_FIELDS
            db.execute("INSERT INTO evidence_chunk VALUES(" + ",".join("?" for _ in CHUNK_FIELDS) + ")",
                       tuple(chunk[key] for key in CHUNK_FIELDS))
        db.executemany("INSERT OR REPLACE INTO index_metadata VALUES(?,?)", [
            ("last_source_raw_document_id", "1"), ("completed_at", "2026-11-24"),
        ])
    return source, index, build


def test_import_twice_preserves_bytes_links_metadata_and_readonly_score(pg, snapshot):
    connection, dsn, name = pg
    source, index, build = snapshot
    originals = file_sha256(source), file_sha256(index)
    store = PostgresVectorStore(connection)
    assert store.import_sqlite(source_path=source, index_path=index)["status"] == "imported"
    assert store.import_sqlite(source_path=source, index_path=index)["status"] == "already_imported"
    assert store.counts() == {"chunks": 1, "vectors": 1}
    row = connection.execute("SELECT c.*,v.vector_f32 FROM rag_vector.evidence_chunk c JOIN rag_vector.embedding_vector v USING(text_hash)").fetchone()
    assert row["document_id"] == "one" and row["source_url"] == "https://example.test/one"
    assert bytes(row["vector_f32"]) == struct.pack("<1024f", 1.0, *([0.0] * 1023))
    with closing(connect_postgres(dsn, expected_database=name, read_only=True)) as reader:
        rows = PostgresVectorIndex(reader).fetch_chunks(("one",), tuple([1.0] + [0.0] * 1023))
        assert rows[0]["cosine_similarity"] == pytest.approx(1.0)
        assert PostgresVectorIndex(reader).fetch_chunks(("missing",), tuple([1.0] + [0.0] * 1023)) == []
        with pytest.raises(Exception, match="read-only"):
            reader.execute("DELETE FROM rag_vector.evidence_chunk")
    assert (file_sha256(source), file_sha256(index)) == originals


def test_interrupted_import_resumes_without_duplicates(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    add_document(source, "two", "second distinct evidence")
    add_document(source, "three", "third distinct evidence")
    with closing(open_sqlite_readonly(source)) as db:
        chunks = list(build._iter_evidence_chunks(db))
    from ani_rag.storage.postgres_vector import CHUNK_FIELDS
    with closing(sqlite3.connect(index)) as db, db:
        for chunk in chunks:
            db.execute("INSERT OR IGNORE INTO embedding_vector VALUES(?,?,?,?,?,?)", (
                chunk["text_hash"], MODEL, DIMENSIONS,
                struct.pack("<1024f", 1.0, *([0.0] * 1023)), 3, "2026-11-24",
            ))
            db.execute("INSERT OR IGNORE INTO evidence_chunk VALUES(" + ",".join("?" for _ in CHUNK_FIELDS) + ")",
                       tuple(chunk[key] for key in CHUNK_FIELDS))
        db.execute("UPDATE index_metadata SET value='3' WHERE key='last_source_raw_document_id'")
    store = PostgresVectorStore(connection)
    def interrupt(_):
        raise TimeoutError("simulated interruption after committed vector batch")
    with pytest.raises(TimeoutError):
        store.import_sqlite(source_path=source, index_path=index, batch_size=1, progress=interrupt)
    assert store.counts() == {"chunks": 0, "vectors": 1}
    assert connection.execute("SELECT value FROM rag_vector.index_metadata WHERE key='import_after_rowid_embedding_vector'").fetchone()["value"] == "1"
    with pytest.raises(RuntimeError, match="not completed"):
        PostgresVectorIndex(connection).fetch_chunks(("one",), tuple([1.0] + [0.0] * 1023))
    assert store.import_sqlite(source_path=source, index_path=index)["status"] == "imported"
    assert store.counts() == {"chunks": 3, "vectors": 3}


@pytest.mark.parametrize("mismatch", ["document", "source", "url"])
def test_mismatched_snapshot_link_rejected_before_target_creation(pg, snapshot, mismatch):
    connection, _, _ = pg
    source, index, _ = snapshot
    with sqlite3.connect(index) as db:
        if mismatch == "document":
            db.execute("UPDATE evidence_chunk SET document_id='absent-document'")
        elif mismatch == "source":
            db.execute("UPDATE evidence_chunk SET source_id='different-source'")
        else:
            db.execute("UPDATE evidence_chunk SET source_url='https://other.example.test/one'")
    before = file_sha256(source), file_sha256(index)
    with pytest.raises(ValueError, match="does not match the supplied source snapshot"):
        PostgresVectorStore(connection).import_sqlite(source_path=source, index_path=index)
    assert connection.execute("SELECT to_regnamespace('rag_vector') AS schema").fetchone()["schema"] is None
    assert before == (file_sha256(source), file_sha256(index))


def test_reuses_existing_vector_for_new_document_and_repeat_update(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    add_document(source, "two", "original evidence")
    original = file_sha256(source)
    with closing(open_sqlite_readonly(source)) as db:
        first = store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)
        second = store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)
    assert first["chunks_added"] == 1 and first["vectors_added"] == first["provider_calls"] == 0
    assert second["chunks_added"] == second["vectors_added"] == second["provider_calls"] == 0
    assert first["checkpoint_after"] == second["checkpoint_after"] == 2
    assert store.counts() == {"chunks": 2, "vectors": 1}
    assert file_sha256(source) == original


def test_missing_embedding_stops_without_advancing_checkpoint(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    add_document(source, "two", "not previously embedded")
    with closing(open_sqlite_readonly(source)) as db, pytest.raises(RuntimeError, match="paid embedding is disabled"):
        store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)
    assert store.counts() == {"chunks": 1, "vectors": 1}
    assert connection.execute("SELECT value FROM rag_vector.index_metadata WHERE key='last_source_raw_document_id'").fetchone()["value"] == "1"


def test_partial_document_restart_reuses_saved_chunks_and_vectors(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    add_document(source, "long", "a" * 1200 + "b" * 1200 + "c" * 40)
    supplied = []
    def provider(texts):
        supplied.extend(texts)
        return {key: struct.pack("<1024f", 0.0, 1.0, *([0.0] * 1022)) for key in texts}
    def interrupt():
        raise TimeoutError("after first committed partial-document batch")
    with closing(open_sqlite_readonly(source)) as db, pytest.raises(TimeoutError):
        store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks,
                                 embedding_provider=provider, batch_size=1, after_batch=interrupt)
    assert connection.execute("SELECT value FROM rag_vector.index_metadata WHERE key='last_source_raw_document_id'").fetchone()["value"] == "1"
    with closing(open_sqlite_readonly(source)) as db:
        resumed = store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks,
                                           embedding_provider=provider, batch_size=1)
        repeated = store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks,
                                            embedding_provider=provider, batch_size=1)
    assert resumed["checkpoint_after"] == 2 and repeated["chunks_added"] == 0
    assert len(supplied) == len(set(supplied)) == 3
    assert store.counts() == {"chunks": 4, "vectors": 4}


def test_source_identity_change_rejected(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE rag_document SET url='https://unrelated.test/'")
    with closing(open_sqlite_readonly(source)) as db, pytest.raises(ValueError, match="identity/history"):
        store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)


def test_newly_indexed_source_identity_is_also_bound(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    add_document(source, "two", "original evidence")
    with closing(open_sqlite_readonly(source)) as db:
        store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE rag_document SET url='https://changed.test/two' WHERE document_id='two'")
    with closing(open_sqlite_readonly(source)) as db, pytest.raises(ValueError, match="identity/history"):
        store.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)


def test_concurrent_update_is_rejected_until_existing_writer_finishes(pg, snapshot):
    connection, dsn, name = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    with closing(connect_postgres(dsn, expected_database=name)) as other:
        second = PostgresVectorStore(other)
        with store.locked(), closing(open_sqlite_readonly(source)) as db:
            with pytest.raises(RuntimeError, match="already running"):
                second.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)
        with closing(open_sqlite_readonly(source)) as db:
            assert second.update_from_source(db, chunk_iterator=build._iter_evidence_chunks)["chunks_added"] == 0
@pytest.mark.parametrize("values", [(3.25, 4.125), (-3.25, -4.125), (0.0, 0.0)])
def test_pg_cosine_matches_stored_float32_python_reference(pg, snapshot, values):
    connection, _, _ = pg
    source, index, _ = snapshot
    vector = (*values, *([0.0] * 1022))
    with sqlite3.connect(index) as db:
        db.execute("UPDATE embedding_vector SET vector_f32=?", (struct.pack("<1024f", *vector),))
    store = PostgresVectorStore(connection)
    store.import_sqlite(source_path=source, index_path=index)
    query = tuple([0.125, 0.875] + [0.0] * 1022)
    row = PostgresVectorIndex(connection).fetch_chunks(("one",), query)[0]
    if not any(values):
        assert row["cosine_similarity"] is None
    else:
        expected = sum(a*b for a, b in zip(vector, query)) / (
            math.sqrt(sum(a*a for a in vector)) * math.sqrt(sum(b*b for b in query))
        )
        assert row["cosine_similarity"] == pytest.approx(expected, abs=1e-6)
