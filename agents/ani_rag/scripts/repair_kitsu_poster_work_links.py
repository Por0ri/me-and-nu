"""Repair Kitsu artwork from its anime ID and the original source records for shared images."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ani_rag.archive_identity import official_works_path
from ani_rag.kitsu_artwork_identity import collection_artwork_owner, poster_owner, poster_owners


def archived_appearances(archive: sqlite3.Connection, owners: dict[str, int]) -> dict[tuple[str,str],set[int]]:
    proven = defaultdict(set)
    incomplete = set()
    for raw, in archive.execute(
        """SELECT payload_json FROM agent_records WHERE agent_type='image' AND record_kind='image_asset'
           AND status NOT IN ('rejected','expired')"""
    ):
        image = json.loads(raw)
        origin = image.get("provenance_source") or {}
        if origin.get("id") != "kitsu_anime_catalog":
            continue
        key = (image.get("url") or "", image.get("storage_key") or "")
        if not all(key):
            continue
        page = image.get("source_url") or ""
        work = collection_artwork_owner(origin["id"],key[0],page,owners)
        if work is not None and page == origin.get("url"):
            proven[key].add(work)
        else:
            incomplete.add(key)
    return {k: v for k,v in proven.items() if k not in incomplete}


def reconcile(db: sqlite3.Connection, owners: dict[str, int], backup: Path | None = None,
              *, appearances: dict[tuple[str,str],set[int]] | None = None) -> dict[str, int]:
    changes = []
    verified = 0
    for asset, source, image_url, page_url, digest in db.execute(
        "SELECT asset_id,source_id,source_url,source_page_url,content_sha256 FROM rag_image_asset WHERE source_id='kitsu_anime_catalog'"
    ):
        work = poster_owner(source, image_url, page_url, owners)
        if work is not None:
            expected = {work}
        else:
            expected = (appearances or {}).get((image_url,digest), set())
            if collection_artwork_owner(source,image_url,page_url,owners) not in expected:
                continue
        if not expected:
            continue
        verified += 1
        links = db.execute(
            """SELECT image_work_link_id,menu_work_id,candidate_menu_work_id,status FROM rag_image_work_link
               WHERE asset_id=? ORDER BY image_work_link_id""", (asset,),
        ).fetchall()
        if {tuple(row[1:]) for row in links} == {(w,None,"matched") for w in expected}:
            continue
        if not links or any(row[1] is None or row[2] is not None or row[3] != "matched" for row in links):
            raise ValueError(f"Image {asset} needs manual adjudication")
        changes.append((asset, expected, links))
    if backup is not None and changes:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("asset_id","image_work_link_id","menu_work_id","candidate_menu_work_id","status","correct_menu_work_id"))
            for asset, expected, links in changes:
                for link in links:
                    writer.writerow((asset,*link,";".join(map(str,sorted(expected)))))
        for asset, expected, links in changes:
            retained = set()
            reusable = []
            for link_id, current, _, _ in links:
                if current in expected and current not in retained:
                    retained.add(current)
                else:
                    reusable.append(link_id)
            for work in sorted(expected - retained):
                if reusable:
                    db.execute("UPDATE rag_image_work_link SET menu_work_id=? WHERE image_work_link_id=?", (work,reusable.pop()))
                else:
                    db.execute("""INSERT INTO rag_image_work_link(image_work_link_id,asset_id,menu_work_id,candidate_menu_work_id,status)
                                  VALUES(?,?,?,NULL,'matched')""",(f"iw_verified_{asset}_{work}",asset,work))
            db.executemany("DELETE FROM rag_image_work_link WHERE image_work_link_id=?",((link,) for link in reusable))
    return {"matched_provider_artworks": verified, "assets_to_repair": len(changes)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--source-archive", type=Path, help="Period SQLite archive containing the original Kitsu image records")
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires a new CSV --backup")
    owners = poster_owners(official_works_path().with_name("menu_work_entries.csv"))
    appearances = {}
    if args.source_archive is not None:
        with sqlite3.connect(args.source_archive.resolve().as_uri()+"?mode=ro",uri=True) as archive:
            appearances = archived_appearances(archive,owners)
    uri = args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    with sqlite3.connect(uri, uri=True, timeout=120) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        print(json.dumps(reconcile(db, owners, args.backup if args.apply else None, appearances=appearances)), flush=True)
        if args.apply and reconcile(db, owners, appearances=appearances)["assets_to_repair"]:
            raise RuntimeError("Unrepaired Kitsu artwork links remain")


if __name__ == "__main__":
    main()
