import csv
import gzip
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from scripts.repair_kitsu_poster_work_links import archived_appearances, reconcile
from scripts.repair_menu_dict_entity_links import _apply, _plan
from ani_rag.kitsu_artwork_identity import poster_owner, poster_owners


def test_repairs_only_differing_entity_sets_and_preserves_empty_backup_marker(tmp_path: Path):
    archive = SimpleNamespace(
        entities={n: SimpleNamespace(kind="character", entity_id=n) for n in (7, 8, 9, 10)},
        archive_sha256="snapshot",
        derived_work_ids=lambda n: {7: (42, 44), 8: (44,), 9: (), 10: (42,)}[n],
    )
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE rag_document(document_id TEXT PRIMARY KEY,metadata_json TEXT);
        CREATE TABLE rag_work_link(link_id TEXT PRIMARY KEY,document_id TEXT,menu_work_id INTEGER,
            candidate_menu_work_id INTEGER,status TEXT,matched_on TEXT);
        CREATE INDEX by_doc ON rag_work_link(document_id);
    """)
    for n in archive.entities:
        db.execute("INSERT INTO rag_document VALUES(?,?)", (str(n), json.dumps({
            "dataset":"menu_dict","menu_dict_entity_id":n,"menu_dict_entity_kind":"character","archive_sha256":"snapshot",
        })))
    for doc, work in ((7, 42), (7, 99), (8, 44), (9, 99)):
        db.execute("INSERT INTO rag_work_link VALUES(?,?,?,?,?,?)",
                   (f"original-{doc}-{work}",str(doc),work,None,"matched","menu_dict_old"))
    plan = _plan(db, archive)
    assert (plan["documents_to_repair"],plan["old_rows"],plan["backup_rows"]) == (3,3,4)
    backup = tmp_path / "links.csv.gz"
    _apply(db, backup, plan)
    with gzip.open(backup, "rt", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4 and any(row["document_id"] == "10" and not row["link_id"] for row in rows)
    assert db.execute("SELECT link_id FROM rag_work_link WHERE document_id='8'").fetchone()[0] == "original-8-44"
    assert db.execute("SELECT menu_work_id,status FROM rag_work_link WHERE document_id='9'").fetchone() == (None,"unresolved")
    for temp in ("repair_changes","desired","derived_docs"):
        db.execute(f"DROP TABLE temp.{temp}")
    assert _plan(db, archive)["documents_to_repair"] == 0
    db.close()


def test_only_exact_season_kitsu_artwork_is_corrected(tmp_path: Path):
    entries, external = tmp_path/"seasons.csv", tmp_path/"external.csv"
    entries.write_text("anilist_id,menu_work_id\n11061,6\n", encoding="utf-8")
    external.write_text("source_id,id_kind,external_id,entity_id\nanilist,anime,11061,2377\nkitsu,anime,6448,2377\n", encoding="utf-8")
    owners = poster_owners(entries, external)
    page = "https://kitsu.io/api/edge/anime/6448"
    poster = "https://media.kitsu.app/anime/poster_images/6448/original.png"
    assert poster_owner("kitsu_anime_catalog",poster,page,owners) == 6
    assert poster_owner("kitsu_anime_catalog",poster,page+"0",owners) is None
    assert poster_owner("kitsu_anime_catalog",poster.replace("/anime/poster_images/", "/characters/images/"),page,owners) is None
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE rag_image_asset(asset_id TEXT,source_id TEXT,source_url TEXT,source_page_url TEXT,content_sha256 TEXT);
        CREATE TABLE rag_image_work_link(image_work_link_id TEXT,asset_id TEXT,menu_work_id INTEGER,
               candidate_menu_work_id INTEGER,status TEXT);
    """)
    db.execute("INSERT INTO rag_image_asset VALUES('one','kitsu_anime_catalog',?,?,'digest')", (poster,page))
    db.execute("INSERT INTO rag_image_work_link VALUES('original','one',346,NULL,'matched')")
    backup = tmp_path/"images.csv"
    assert reconcile(db, owners, backup)["assets_to_repair"] == 1
    assert db.execute("SELECT image_work_link_id,menu_work_id FROM rag_image_work_link").fetchone() == ("original",6)
    assert reconcile(db, owners)["assets_to_repair"] == 0
    assert '346' in backup.read_text(encoding="utf-8")
    db.close()


def test_character_keeps_only_appearances_supported_by_each_archived_relationship():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE agent_records(payload_json TEXT,agent_type TEXT,record_kind TEXT,status TEXT)")
    image = "https://media.kitsu.app/characters/images/2815/original.jpg"
    for kitsu in ("6448","24"):
        page = f"https://kitsu.io/api/edge/anime/{kitsu}/characters?page[limit]=12"
        item = {"url":image,"storage_key":"digest","source_url":page,
                "provenance_source":{"id":"kitsu_anime_catalog","url":page}}
        db.execute("INSERT INTO agent_records VALUES(?,'image','image_asset','ok')",(json.dumps(item),))
    assert archived_appearances(db, {"6448":6,"24":346}) == {(image,"digest"):{6,346}}
    assert archived_appearances(db, {"6448":6}) == {}
    db.close()
