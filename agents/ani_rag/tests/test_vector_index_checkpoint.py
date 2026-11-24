"""Run the real SQLite indexers while a second connection appends evidence."""

import importlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]


class Embeddings:
    def __init__(self):
        self.before_response = None
        self.accepted = []

    def create(self, *, input, dimensions, **kwargs):
        if self.before_response:
            self.before_response()
        self.accepted.extend(input)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.125] * dimensions) for _ in input],
            usage=SimpleNamespace(prompt_tokens=len(input)),
        )


@pytest.fixture
def indexers(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    index = tmp_path / "vectors.sqlite3"
    with closing(sqlite3.connect(source)) as db, db:
        db.executescript((PROJECT / "src/ani_rag/storage/schema.sql").read_text(encoding="utf-8"))
        db.execute(
            """INSERT INTO rag_source(source_id,media_name,source_kind,collect_method,site_url,
               source_grade,copyright_status,license_basis,configuration_json,created_at,updated_at)
               VALUES('source','Source','official','mcp','https://example.test',
               'official','unknown','unknown','{}','2026-01-01','2026-01-01')"""
        )
    monkeypatch.syspath_prepend(str(PROJECT / "scripts"))
    build = importlib.import_module("build_vector_evidence_index")
    update = importlib.import_module("update_vector_evidence_index")
    provider = Embeddings()
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    for module in (build, update):
        # Keep the project's credential file outside this test.
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "OpenAI", lambda **_: SimpleNamespace(embeddings=provider))
    monkeypatch.setattr(sys, "argv", ["index", "--source-db", str(source), "--index-db", str(index)])
    return source, index, build, update, provider


def add_document(source, identity, text):
    url = "https://example.test/" + identity
    with closing(sqlite3.connect(source)) as db, db:
        db.execute(
            """INSERT INTO rag_document(document_id,source_id,source_site_id,content_type,evidence_type,
               collection_method,title,raw_text,original_language,url,canonical_url,fetched_at,
               author_attribution,metadata_json,content_hash,created_at)
               VALUES(?,'source',1,'guide','reference_fact','mcp_read',?,?,'en',?,?,'2026-01-01',
               'source','{}',?,'2026-01-01')""",
            (identity, identity, text, url, url, identity),
        )


def indexed(index):
    with closing(sqlite3.connect(index)) as db:
        checkpoint = int(db.execute(
            "SELECT value FROM index_metadata WHERE key='last_source_raw_document_id'"
        ).fetchone()[0])
        documents = dict(db.execute(
            """SELECT document_id,count(*) FROM evidence_chunk c JOIN embedding_vector v USING(text_hash)
               WHERE v.dimensions=1024 AND length(v.vector_f32)=4096 GROUP BY document_id"""
        ))
    return checkpoint, documents


@pytest.mark.parametrize("during_full", [True, False], ids=["full-build", "incremental"])
def test_concurrent_append_is_indexed_once_on_next_run(indexers, during_full):
    source, index, build, update, provider = indexers
    add_document(source, "one", "one episode")
    if not during_full:
        assert build.main() == 0
        add_document(source, "two", "two episodes")
    provider.before_response = lambda: add_document(source, "late", "late arrival")
    assert (build if during_full else update).main() == 0
    expected = {"one": 1} if during_full else {"one": 1, "two": 1}
    assert indexed(index) == (len(expected), expected)
    provider.before_response = None
    assert update.main() == 0
    expected["late"] = 1
    assert indexed(index) == (len(expected), expected)
    calls = provider.accepted.copy()
    assert update.main() == 0
    assert provider.accepted == calls
    assert calls.count("late arrival") == 1


def test_failed_batch_resumes_same_document_without_reembedding(indexers, monkeypatch):
    source, index, build, update, provider = indexers
    add_document(source, "one", "original evidence")
    assert build.main() == 0
    add_document(source, "long", "a" * 1200 + "b" * 1200 + "c" * 40)
    monkeypatch.setattr(update, "API_BATCH_SIZE", 1)

    def fail_after_first_chunk():
        if len(provider.accepted) == 2:
            raise TimeoutError("simulated temporary provider failure")

    provider.before_response = fail_after_first_chunk
    with pytest.raises(TimeoutError):
        update.main()
    assert indexed(index) == (1, {"one": 1, "long": 1})
    provider.before_response = None
    assert update.main() == 0
    assert indexed(index) == (2, {"one": 1, "long": 3})
    assert len(provider.accepted) == len(set(provider.accepted)) == 4


def test_selective_repair_preserves_checkpoint_and_reuses_identical_vector(indexers, monkeypatch):
    source, index, build, update, provider = indexers
    for doc in ("one", "two", "three"):
        add_document(source, doc, f"original {doc}")
    assert build.main() == 0
    with closing(sqlite3.connect(index)) as db, db:
        db.execute("DELETE FROM evidence_chunk WHERE document_id='two'")
    add_document(source, "new", "awaiting normal incremental run")
    with monkeypatch.context() as cmd:
        cmd.setattr(sys, "argv", [*sys.argv, "--repair-raw-id", "2"])
        assert update.main() == 0
        assert update.main() == 0
    assert indexed(index) == (3, {"one": 1, "two": 1, "three": 1})
    assert provider.accepted == ["original one", "original two", "original three"]
    assert update.main() == 0
    assert indexed(index) == (4, {"one": 1, "two": 1, "three": 1, "new": 1})
