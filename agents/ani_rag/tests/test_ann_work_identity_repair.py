import csv
import json
import sqlite3

from ani_rag.ann_identity import AnnTitleIndex
from scripts.repair_ann_work_links import _changes


ANN = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=1"


def _titles(tmp_path):
    fields = ("menu_work_id", "title_english", "format", "year")
    entries = [
        (6, "Hunter x Hunter (2011)", "TV", "2011"),
        (346, "Hunter x Hunter", "TV", "1999"),
        (8, "One Piece", "TV", "1999"),
        (2413, "Koisuru One Piece", "ONA", "2025"),
        (236, "Wistoria: Wand and Sword", "TV", "2024"),
        (236, "Wistoria: Wand and Sword Season 2", "TV", "2026"),
    ]
    for name in ("menu_works.csv", "menu_work_entries.csv"):
        with (tmp_path / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(entries)
    return AnnTitleIndex(tmp_path, tmp_path)


def test_ann_distinguishes_remakes_seasons_source_media_and_mentions(tmp_path):
    index = _titles(tmp_path)
    for heading, year, expected in (
        ("Hunter × Hunter (TV 2011)", 2011, 6),
        ("Hunter × Hunter (TV)", 1999, 346),
        ("Wistoria: Wand and Sword (TV 2)", 2026, 236),
    ):
        body = f"# {heading}\nVintage: {year}\nRelated: One Piece"
        assert index.resolve("ANN", body, ANN).confirmed == expected
    for heading in (
        "Hunter x Hunter (TV)",
        "Wistoria: Wand and Sword (TV 3)",
        "One Piece (manga)",
        "One Piece (movie, live-action)",
    ):
        assert index.resolve(heading, f"# {heading}", ANN).confirmed is None
    assert index.resolve("Koisuru One Piece (ONA)", "# Koisuru One Piece (ONA)", ANN).confirmed == 2413
    assert index.resolve("One Piece (TV)", "", ANN).confirmed is None


def test_repair_demotes_inherited_links_and_keeps_verified_multiple(tmp_path):
    index = _titles(tmp_path)
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE rag_document(document_id,title,raw_text,url,source_id,metadata_json,external_id)"
        )
        db.execute(
            "CREATE TABLE rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)"
        )
        for doc, title in (
            ("spin", "Koisuru One Piece (ONA)"),
            ("manga", "One Piece (manga)"),
            ("manual", "Koisuru One Piece (ONA)"),
        ):
            db.execute(
                "INSERT INTO rag_document VALUES(?,?,?,?,?,?,NULL)",
                (doc, title, f"# {title}", ANN, "ann_encyclopedia", "{}"),
            )
        rows = [
            ("w1", "spin", 8, "llm_source_verified"),
            ("w2", "manga", 8, "ann_explicit_relationship_graph"),
            ("w3", "manual", 6, "manual"),
            ("w4", "manual", 8, "manual"),
        ]
        db.executemany("INSERT INTO rag_work_link VALUES(?,?,?,NULL,'matched',?)", rows)
        changes, _ = _changes(db, index)
        assert {change[0] for change in changes} == {"spin", "manga"}
        for doc, _, _, removed, links, metadata in changes:
            assert json.loads(metadata)["historical_ann_work_match"]["menu_work_ids"] == [8]
            db.executemany("DELETE FROM rag_work_link WHERE link_id=?", [(key,) for key in removed])
            db.executemany("INSERT INTO rag_work_link VALUES(?,?,?,?,?,?)", links)
            db.execute("UPDATE rag_document SET metadata_json=? WHERE document_id=?", (metadata, doc))
        assert tuple(
            db.execute("SELECT menu_work_id,status FROM rag_work_link WHERE document_id='spin'").fetchone()
        ) == (2413, "matched")
        assert tuple(
            db.execute(
                "SELECT menu_work_id,candidate_menu_work_id,status FROM rag_work_link WHERE document_id='manga'"
            ).fetchone()
        ) == (None, 8, "ambiguous")
        assert not _changes(db, index)[0]


def test_correct_namespace_native_id_restores_a_previously_demoted_candidate(tmp_path):
    index = _titles(tmp_path)
    index.external_owners["1"] = 236
    title = "Wistoria: Wand and Sword (TV 3)"
    assert index.resolve(title, title, ANN, stored_id="1").confirmed == 236
    assert index.resolve(title, title, ANN, stored_id="99").confirmed is None
    assert index.resolve(title, title, ANN.replace("anime.php", "manga.php")).confirmed is None
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE rag_document(document_id,title,raw_text,url,source_id,metadata_json,external_id)"
        )
        db.execute(
            "CREATE TABLE rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)"
        )
        db.execute(
            "INSERT INTO rag_document VALUES('s',?,?,?,'ann_encyclopedia','{}','1')", (title, title, ANN)
        )
        db.execute(
            "INSERT INTO rag_work_link VALUES('w','s',NULL,1,'ambiguous','ann_relationship_candidate')"
        )
        changes, _ = _changes(db, index)
        assert changes[0][4] == [("w", "s", 236, None, "matched", "ann_anilist_season_id")]
