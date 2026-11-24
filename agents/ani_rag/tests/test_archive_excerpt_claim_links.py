import hashlib
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository
from scripts.repair_claim_excerpt_links import plan


def test_archive_excerpt_repair_and_new_claim_write_use_exact_saved_excerpt():
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            CREATE TABLE rag_claim(claim_id PRIMARY KEY,draft_id,card_id,sequence_no,claim_text,attribution);
            CREATE TABLE rag_claim_evidence(claim_evidence_id PRIMARY KEY,claim_id,document_id,excerpt_id,chunk_id);
            CREATE TABLE rag_excerpt(excerpt_id PRIMARY KEY,document_id,locator,excerpt_text);
            CREATE TABLE rag_chunk(chunk_id PRIMARY KEY,document_id,source_kind,source_index,locator,start_offset,end_offset,chunk_text);
        """)
        digest = hashlib.sha256(b"d:0").hexdigest()[:28]
        old, new = f"ex_{digest}", f"mde_{digest}"
        db.execute("INSERT INTO rag_excerpt VALUES(?,'d','story','본문 속 인용')", (new,))
        db.execute("INSERT INTO rag_chunk VALUES('c','d','excerpt',0,'story:5-7',5,7,'인용')")
        db.execute("INSERT INTO rag_chunk VALUES('other','d','excerpt',0,'story:5-7',5,7,'오류')")
        for link, chunk in (("ok", "c"), ("wrong", "other")):
            db.execute("INSERT INTO rag_claim_evidence VALUES(?,'claim','d',?,?)", (link, old, chunk))
        assert plan(db) == [("ok", old, new)]
        db.execute("UPDATE rag_claim_evidence SET excerpt_id=? WHERE claim_evidence_id='ok'", (new,))
        assert not plan(db)
        chunk = SimpleNamespace(chunk_id="c", evidence_id="d", source_kind="excerpt", source_index=0,
                                locator="story:5-7", start_offset=5, end_offset=7, text="인용")
        claim = SimpleNamespace(text="한 문장", attribution=None, evidence_chunk_ids=("c",), evidence_ids=("d",))
        repo = SQLiteDraftRepository(SimpleNamespace(_connection=db))
        repo._store_claims(SimpleNamespace(draft_id="draft"), (claim,), None, {"c": chunk}, 0)
        assert db.execute("SELECT excerpt_id FROM rag_claim_evidence WHERE claim_id!='claim'").fetchone() == (new,)
