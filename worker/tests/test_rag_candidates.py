import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from menu_worker.rag_candidates import DictionaryCandidates
from menu_worker.rag_contract import RagContractError, RagRequest


class DictionaryCandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "facts.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
            CREATE TABLE rag_source(source_id TEXT PRIMARY KEY);
            CREATE TABLE rag_document(document_id TEXT PRIMARY KEY,source_id TEXT,canonical_url TEXT,
                content_hash TEXT,title TEXT,content_type TEXT,parse_status TEXT,evidence_type TEXT,raw_text TEXT);
            CREATE TABLE rag_work_link(document_id TEXT,menu_work_id INTEGER,status TEXT);
            CREATE TABLE rag_document_content_type(document_id TEXT,content_type TEXT);
            CREATE TABLE rag_draft_evidence(document_id TEXT);
            INSERT INTO rag_source VALUES ('kitsu_anime_catalog');
            """)
            for i in range(45):
                db.execute("INSERT INTO rag_document VALUES (?,?,?,?,?,?,?,?,?)", (
                    f"doc:{i:03}", "kitsu_anime_catalog", f"https://kitsu.io/characters/{i}",
                    f"{i:064x}", f"Character {i}", "character", "parsed", "reference_fact", "Stored evidence"))
                db.execute("INSERT INTO rag_work_link VALUES (?,?,?)", (f"doc:{i:03}", 8, "matched"))
        self.route = {"namespace": "dictionary", "menu_work_id": 8, "content_type": "character",
                      "allowed_source_ids": ["kitsu_anime_catalog"]}
        self.provider = DictionaryCandidates(self.path, catalog_work_ids={8, 9})

    def test_source_unchanged_and_same_topics_keep_ids(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        first = self.provider(self.route)
        self.assertEqual(first, self.provider(self.route))
        self.assertEqual(len(first), 20)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_processed_prefix_does_not_starve_following_facts(self):
        first = self.provider(self.route)
        excluded = [seed["document_id"] for item in first for seed in item["seed_evidence"]]
        second = self.provider(self.route, excluded_document_ids=excluded)
        self.assertEqual(len(second), 20)
        self.assertTrue({row["work_id"] for row in first}.isdisjoint(row["work_id"] for row in second))

    def test_unknown_work_wrong_type_and_already_drafted_are_excluded(self):
        with self.assertRaises(RagContractError):
            self.provider({**self.route, "menu_work_id": 999})
        self.assertEqual(self.provider({**self.route, "menu_work_id": 9}), [])
        self.assertEqual(self.provider({**self.route, "content_type": "music"}), [])
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO rag_draft_evidence VALUES ('doc:000')")
            db.execute("INSERT INTO rag_work_link VALUES ('doc:001',NULL,'ambiguous')")
        picked = {seed["document_id"] for row in self.provider(self.route) for seed in row["seed_evidence"]}
        self.assertNotIn("doc:000", picked)
        self.assertNotIn("doc:001", picked)

    def test_changed_fact_cannot_create_new_topic_identity(self):
        before = RagRequest.parse(self.provider(self.route)[0])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE rag_document SET content_hash=? WHERE document_id=?", ("f" * 64, before.seed_evidence[0][0]))
        after = RagRequest.parse(self.provider(self.route)[0])
        self.assertEqual(before.identity_key, after.identity_key)
        self.assertNotEqual(before.input_sha256, after.input_sha256)
