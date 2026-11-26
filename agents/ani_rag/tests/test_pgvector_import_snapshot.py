"""Initial imports require fixed snapshots; live incremental reads still use WAL."""

from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
import sqlite3

import pytest

from ani_rag.storage.postgres_vector import (
    CHUNK_FIELDS, PostgresVectorIndex, PostgresVectorStore, file_sha256,
    open_sqlite_readonly,
)
from test_postgres_vector_store import add_document, pg, snapshot


@contextmanager
def _wal_writers(source, index):
    # Keep writers open so committed records remain in WAL rather than being
    # checkpointed automatically when the final SQLite connection closes.
    with ExitStack() as stack:
        writers = [stack.enter_context(closing(sqlite3.connect(path))) for path in (source, index)]
        for writer in writers:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        yield writers


def _append_saved_evidence(source, index_writer, build):
    add_document(source, "two", "original evidence")
    with closing(open_sqlite_readonly(source)) as reader:
        chunk = next(build._iter_evidence_chunks(reader, after_raw_document_id=1))
    index_writer.execute(
        "INSERT INTO evidence_chunk VALUES(" + ",".join("?" for _ in CHUNK_FIELDS) + ")",
        tuple(chunk[key] for key in CHUNK_FIELDS),
    )
    index_writer.execute("UPDATE index_metadata SET value='2' WHERE key='last_source_raw_document_id'")
    index_writer.commit()


@pytest.mark.parametrize("immutable", [False, True])
@pytest.mark.parametrize("target", ["source", "index"])
@pytest.mark.parametrize("recovery", ["wal", "journal"])
def test_initial_import_rejects_recovery_before_target_creation(pg, snapshot, immutable, target, recovery):
    connection, _, _ = pg
    source, index, _ = snapshot
    path = source if target == "source" else index
    with _wal_writers(source, index) as writers:
        before = file_sha256(path)
        if recovery == "wal":
            writer = writers[0 if target == "source" else 1]
            table = "rag_document" if target == "source" else "evidence_chunk"
            writer.execute(f"UPDATE {table} SET title='WAL-only change'")
            writer.commit()
        else:
            Path(str(path) + "-journal").write_bytes(b"uncheckpointed recovery data")
        assert file_sha256(path) == before
        try:
            with pytest.raises(ValueError, match="recovery|WAL|snapshot|input"):
                PostgresVectorStore(connection).import_sqlite(
                    source_path=source, index_path=index, immutable_snapshot=immutable,
                )
            assert connection.execute("SELECT to_regnamespace('rag_vector') AS schema").fetchone()["schema"] is None
            assert file_sha256(path) == before
        finally:
            if recovery == "journal":
                Path(str(path) + "-journal").unlink()


@pytest.mark.parametrize("immutable", [False, True])
def test_completed_import_does_not_skip_new_wal_commits(pg, snapshot, immutable):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    with _wal_writers(source, index) as writers:
        assert store.import_sqlite(source_path=source, index_path=index,
                                   immutable_snapshot=immutable)["status"] == "imported"
        before = (file_sha256(source), file_sha256(index))
        metadata_before = connection.execute("SELECT * FROM rag_vector.index_metadata ORDER BY key").fetchall()
        _append_saved_evidence(source, writers[1], build)
        assert before == (file_sha256(source), file_sha256(index))
        assert writers[1].execute("SELECT count(*) FROM evidence_chunk").fetchone()[0] == 2
        with pytest.raises(ValueError, match="recovery|WAL|snapshot|input"):
            store.import_sqlite(source_path=source, index_path=index, immutable_snapshot=immutable)
        assert store.counts() == {"chunks": 1, "vectors": 1}
        assert connection.execute("SELECT * FROM rag_vector.index_metadata ORDER BY key").fetchall() == metadata_before

        # A later checkpoint does not authorize silently adopting a different
        # snapshot either: the original destination and its history stay intact.
        for writer in writers:
            assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        assert before != (file_sha256(source), file_sha256(index))
        with pytest.raises(ValueError, match="different.*snapshot"):
            store.import_sqlite(source_path=source, index_path=index, immutable_snapshot=immutable)
        assert store.counts() == {"chunks": 1, "vectors": 1}
        assert connection.execute("SELECT * FROM rag_vector.index_metadata ORDER BY key").fetchall() == metadata_before


@pytest.mark.parametrize("immutable", [False, True])
def test_checkpointed_snapshot_imports_both_documents_once(pg, snapshot, immutable):
    connection, _, _ = pg
    source, index, build = snapshot
    with _wal_writers(source, index) as writers:
        _append_saved_evidence(source, writers[1], build)
        for writer in writers:
            assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    before = (file_sha256(source), file_sha256(index))
    store = PostgresVectorStore(connection)
    first = store.import_sqlite(source_path=source, index_path=index, immutable_snapshot=immutable)
    repeated = store.import_sqlite(source_path=source, index_path=index, immutable_snapshot=immutable)
    assert first["status"] == "imported" and repeated["status"] == "already_imported"
    assert store.counts() == {"chunks": 2, "vectors": 1}
    rows = connection.execute(
        "SELECT document_id,source_id,source_url,chunk_text FROM rag_vector.evidence_chunk ORDER BY document_id"
    ).fetchall()
    assert rows == [
        {"document_id": identity, "source_id": "source", "source_url": "https://example.test/" + identity,
         "chunk_text": "original evidence"} for identity in ("one", "two")
    ]
    assert before == (file_sha256(source), file_sha256(index))


@pytest.mark.parametrize("immutable", [False, True])
@pytest.mark.parametrize("target", ["source", "index"])
def test_mid_import_wal_change_cannot_publish_a_complete_index(pg, snapshot, immutable, target):
    connection, _, _ = pg
    source, index, _ = snapshot
    store = PostgresVectorStore(connection)
    with _wal_writers(source, index) as writers:
        originals = (source.read_bytes(), index.read_bytes())
        changed = False

        def change_after_committed_batch(_):
            nonlocal changed
            if changed:
                return
            changed = True
            writer = writers[0 if target == "source" else 1]
            table = "rag_document" if target == "source" else "evidence_chunk"
            writer.execute(f"UPDATE {table} SET title='changed during import'")
            writer.commit()

        with pytest.raises(ValueError, match="recovery|WAL|snapshot|input"):
            store.import_sqlite(source_path=source, index_path=index, batch_size=1,
                                immutable_snapshot=immutable, progress=change_after_committed_batch)
        assert changed
        status = connection.execute(
            "SELECT value FROM rag_vector.index_metadata WHERE key='migration_status'"
        ).fetchone()["value"]
        assert status == "input_changed"
        with pytest.raises(RuntimeError, match="not completed"):
            PostgresVectorIndex(connection).fetch_chunks(("one",), (1.0,) + (0.0,) * 1023)
    # A partially copied, changed input must not become resumable just because
    # the original files are restored later. Its committed rows may be mixed.
    source.write_bytes(originals[0])
    index.write_bytes(originals[1])
    with pytest.raises((ValueError, RuntimeError), match="changed|invalid|snapshot|input"):
        store.import_sqlite(source_path=source, index_path=index, immutable_snapshot=immutable)


@pytest.mark.parametrize("target", ["source", "index"])
def test_immutable_import_detects_main_file_change(pg, snapshot, target):
    connection, _, _ = pg
    source, index, _ = snapshot
    for path in (source, index):
        with closing(sqlite3.connect(path)) as writer:
            writer.execute("PRAGMA journal_mode=DELETE")
    path = source if target == "source" else index
    original = file_sha256(path)
    changed = False

    def change_after_committed_batch(_):
        nonlocal changed
        if changed:
            return
        changed = True
        table = "rag_document" if target == "source" else "evidence_chunk"
        with closing(sqlite3.connect(path)) as writer, writer:
            writer.execute(f"UPDATE {table} SET title='new main-file version'")
        assert file_sha256(path) != original

    with pytest.raises(ValueError, match="snapshot|input|changed"):
        PostgresVectorStore(connection).import_sqlite(
            source_path=source, index_path=index, immutable_snapshot=True,
            batch_size=1, progress=change_after_committed_batch,
        )
    assert changed
    assert connection.execute(
        "SELECT value FROM rag_vector.index_metadata WHERE key='migration_status'"
    ).fetchone()["value"] == "input_changed"
    with pytest.raises(RuntimeError, match="not completed"):
        PostgresVectorIndex(connection).fetch_chunks(("one",), (1.0,) + (0.0,) * 1023)


def test_input_change_is_recorded_when_progress_callback_also_raises(pg, snapshot):
    connection, _, _ = pg
    source, index, _ = snapshot
    store = PostgresVectorStore(connection)
    with _wal_writers(source, index) as writers:
        def change_and_interrupt(_):
            writers[1].execute("UPDATE evidence_chunk SET title='WAL change before callback failure'")
            writers[1].commit()
            raise TimeoutError("interrupted after source change")

        with pytest.raises((ValueError, TimeoutError)):
            store.import_sqlite(source_path=source, index_path=index, batch_size=1,
                                progress=change_and_interrupt)
        assert connection.execute(
            "SELECT value FROM rag_vector.index_metadata WHERE key='migration_status'"
        ).fetchone()["value"] == "input_changed"


def test_live_incremental_update_still_reads_committed_wal(pg, snapshot):
    connection, _, _ = pg
    source, index, build = snapshot
    store = PostgresVectorStore(connection)
    with _wal_writers(source, index):
        store.import_sqlite(source_path=source, index_path=index)
        before = file_sha256(source)
        add_document(source, "two", "original evidence")
        assert file_sha256(source) == before
        with closing(open_sqlite_readonly(source)) as reader:
            first = store.update_from_source(reader, chunk_iterator=build._iter_evidence_chunks)
            repeated = store.update_from_source(reader, chunk_iterator=build._iter_evidence_chunks)
        assert first["chunks_added"] == 1 and first["checkpoint_after"] == 2
        assert first["vectors_added"] == first["provider_calls"] == 0
        assert repeated["chunks_added"] == repeated["vectors_added"] == repeated["provider_calls"] == 0
        assert store.counts() == {"chunks": 2, "vectors": 1}
