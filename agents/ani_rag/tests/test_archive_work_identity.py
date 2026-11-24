import csv
import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ani_rag.archive_identity import official_works_path
from scripts.import_period_sqlite_images import _confirmed_archive_ids
from scripts.repair_menu_dict_work_links import _apply, _mapping, _populate_map


class ArchiveWorkIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.current = self.root / "catalog" / "menu_works.csv"
        self.current.parent.mkdir()
        self.current.write_text(
            "menu_work_id,title_english,anilist_ids\n1,Titan,700\n2327,Nukitashi,44\n15,Remake,20\n",
            encoding="utf-8",
        )
        self.current.with_name("menu_work_entries.csv").write_text(
            "anilist_id,menu_work_id\n700,1\n44,2327\n", encoding="utf-8"
        )
        (self.root / "external_id.csv").write_text(
            "source_id,id_kind,external_id,entity_id\n", encoding="utf-8"
        )

    def test_archive_images_require_matching_source_and_current_identity(self):
        historical = self.root / "historical.csv"
        historical.write_text(
            "menu_work_id,title_english,anilist_ids\n1,Titan,700\n2327,Precure,90\n15,Remake,21\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"ME_NU_WORK_CATALOG_DIR": str(self.current.parent)}):
            self.assertEqual(official_works_path(), self.current)
            self.assertEqual(_confirmed_archive_ids(historical, official_works_path()), {1})
        with self.assertRaises(ValueError):
            _confirmed_archive_ids(self.current, self.current)

    def test_repair_validates_archive_parent_title_and_follows_entity_to_work_id(self):
        entity = self.root / "entity.csv"
        entity.write_text(
            "entity_id,kind,menu_work_id,parent_id,name_romaji\n"
            "220,entry,,5,Titan OVA\n5,work,1,,Titan\n7,work,2327,,Precure\n221,entry,,7,Precure Season 2\n",
            encoding="utf-8",
        )
        direct, verified = _mapping(entity, self.current)
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.row_factory = sqlite3.Row
        database.executescript(
            "CREATE TABLE rag_document(document_id TEXT, metadata_json TEXT);"
            "CREATE TABLE rag_work_link(link_id TEXT, document_id TEXT, menu_work_id INTEGER, "
            "candidate_menu_work_id INTEGER, status TEXT);"
        )
        for entity_id, kind in ((220, "entry"), (5, "work"), (7, "work"), (221, "entry")):
            metadata = {"dataset": "menu_dict", "menu_dict_entity_id": entity_id, "menu_dict_entity_kind": kind}
            database.execute("INSERT INTO rag_document VALUES(?,?)", (str(entity_id), json.dumps(metadata)))
        result = _populate_map(database, direct, verified)
        self.assertEqual(result["parent_identity_unconfirmed"], 2)
        self.assertEqual(
            {tuple(row) for row in database.execute("SELECT document_id, menu_work_id FROM repair_map")},
            {("220", 1), ("5", 1)},
        )
        database.commit()
        backup = self.root / "prior-backup.csv.gz"
        backup.write_bytes(b"pre-existing backup")
        with self.assertRaises(FileExistsError):
            _apply(database, backup)
        self.assertEqual(backup.read_bytes(), b"pre-existing backup")

    def test_current_official_entry_owner_wins_over_an_old_franchise_parent(self):
        entity = self.root / "entity.csv"
        entity.write_text("entity_id,kind,menu_work_id,parent_id,name_romaji\n"
                          "5,work,1,,Titan\n220,entry,,5,New split\n221,entry,,5,Unknown\n", encoding="utf-8")
        entity.with_name("external_id.csv").write_text(
            "source_id,id_kind,external_id,entity_id\nanilist,anime,44,220\nanilist,anime,999,221\n",
            encoding="utf-8")
        mapping, verified = _mapping(entity,self.current)
        self.assertEqual(mapping[220], (2327, "menu_dict_official_anilist_entry"))
        self.assertIn(2327, verified)
        self.assertNotIn(221, mapping)

    def test_repair_preserves_ids_and_untouched_links_and_can_be_repeated(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE rag_document(document_id TEXT PRIMARY KEY,metadata_json TEXT);"
                         "CREATE TABLE rag_work_link(link_id TEXT PRIMARY KEY,document_id TEXT,menu_work_id INTEGER,"
                         "candidate_menu_work_id INTEGER,status TEXT,matched_on TEXT);")
        mapping = {22: (1, "menu_dict_entry_parent")}
        meta = {"dataset":"menu_dict", "menu_dict_entity_kind":"entry", "menu_dict_entity_id":"22"}
        for doc in ("stable","change","duplicate","missing","web"):
            db.execute("INSERT INTO rag_document VALUES(?,?)",
                       (doc,json.dumps(meta if doc != "web" else {})))
        db.executemany("INSERT INTO rag_work_link VALUES(?,?,?,NULL,'matched',?)", [
            ("w0","stable",1,"manual"), ("w1","change",5,"wrong"), ("w2","duplicate",5,"wrong"),
            ("w3","duplicate",1,"manual"), ("w4","web",5,"manual")])
        plan = _populate_map(db,mapping,{1})
        self.assertEqual(plan["documents_to_repair"],3)
        backup = self.root / "repair.csv.gz"
        self.assertEqual(_apply(db,backup)[0],4)
        with gzip.open(backup,"rt",encoding="utf-8",newline="") as stream:
            self.assertEqual({row["document_id"] for row in csv.DictReader(stream)}, {"change","duplicate","missing"})
        self.assertEqual([tuple(x) for x in db.execute("SELECT link_id,document_id,menu_work_id,matched_on "
                                                       "FROM rag_work_link ORDER BY link_id")], [
            ("w0","stable",1,"manual"), ("w1","change",1,"menu_dict_entry_parent"), ("w3","duplicate",1,"manual"),
            ("w4","web",5,"manual"), ("wl_parent_missing","missing",1,"menu_dict_entry_parent")])
        self.assertEqual(_populate_map(db,mapping,{1})["documents_to_repair"],0)
