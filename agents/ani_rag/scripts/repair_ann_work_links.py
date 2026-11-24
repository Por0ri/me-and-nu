"""Audit ANN work links against the archived page's own title without network access."""

import argparse
import gzip
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ani_rag.ann_identity import AnnTitleIndex
from ani_rag.catalog import LocalWorkCatalog


def _changes(db, index):
    counts = Counter()
    changes = []
    for doc in db.execute(
        "SELECT document_id,title,raw_text,url,external_id,metadata_json FROM rag_document WHERE source_id='ann_encyclopedia'"
    ):
        links = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM rag_work_link WHERE document_id=? ORDER BY link_id", (doc["document_id"],)
            )
        ]
        matched = [row for row in links if row["status"] == "matched" and row["menu_work_id"] is not None]
        weak = [row for row in matched if (row["matched_on"] or "").startswith("ann_explicit_")]
        independent_ids = {row["menu_work_id"] for row in matched if row not in weak}
        identity = index.resolve(
            doc["title"], doc["raw_text"] or "", doc["url"], stored_id=doc["external_id"] or ""
        )
        targets = weak
        if not matched:
            if identity.method != "ann_anilist_season_id" or identity.confirmed is None:
                continue
            targets = [
                row
                for row in links
                if row["status"] == "ambiguous" and row["matched_on"] == "ann_relationship_candidate"
            ]
            if not targets:
                continue
        if not targets:
            if (
                len(independent_ids) != 1
                or identity.confirmed is None
                or identity.confirmed in independent_ids
                or (
                    identity.primary_matches != (identity.confirmed,)
                    and identity.method != "ann_anilist_season_id"
                )
                or any((row["matched_on"] or "").startswith("manual") for row in matched)
            ):
                continue
            targets = matched
            independent_ids = set()
        previous = {row["menu_work_id"] or row["candidate_menu_work_id"] for row in targets}
        retained = [row for row in links if row not in targets]
        if identity.confirmed is not None and (not independent_ids or identity.confirmed in independent_ids):
            status, ids, method = "matched", {identity.confirmed}, identity.method
        else:
            status, ids, method = (
                "ambiguous",
                set(identity.candidates) or previous,
                "ann_relationship_candidate",
            )
        retained_ids = {row["menu_work_id"] for row in retained if row["status"] == "matched"}
        retained_candidates = {
            row["candidate_menu_work_id"] for row in retained if row["status"] == "ambiguous"
        }
        new_rows = []
        for i, identifier in enumerate(
            sorted(ids - retained_ids - (retained_candidates if status == "ambiguous" else set()))
        ):
            link_id = (
                targets[i]["link_id"] if i < len(targets) else f"wl_ann_{doc['document_id']}_{identifier}"
            )
            new_rows.append(
                (
                    link_id,
                    doc["document_id"],
                    identifier if status == "matched" else None,
                    identifier if status == "ambiguous" else None,
                    status,
                    method,
                )
            )
        metadata = json.loads(doc["metadata_json"])
        metadata["work_match_method"] = next(
            (row["matched_on"] for row in retained if row["status"] == "matched"), method
        )
        metadata.setdefault(
            "historical_ann_work_match",
            {
                "method": json.loads(doc["metadata_json"]).get("work_match_method"),
                "menu_work_ids": sorted(previous),
            },
        )
        old_parents = metadata.pop("franchise_parent_work_ids", None)
        if old_parents is not None:
            metadata["historical_ann_work_match"]["franchise_parent_work_ids"] = old_parents
        if metadata.get("seed_match_status") in {"confirmed", "unconfirmed"}:
            active_ids = retained_ids | (ids if status == "matched" else set())
            metadata["seed_match_status"] = (
                "confirmed"
                if str(metadata.get("work_match_seed_menu_work_id")) in {str(i) for i in active_ids}
                else "unconfirmed"
            )
        changes.append(
            (
                doc["document_id"],
                doc["metadata_json"],
                links,
                [r["link_id"] for r in targets],
                new_rows,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            )
        )
        counts["documents"] += 1
        counts[status] += 1
        counts["work_id_changed"] += int(status == "matched" and ids != previous)
    return changes, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires an unused --backup .jsonl.gz path")
    catalog = LocalWorkCatalog()
    index = AnnTitleIndex(catalog.catalog_dir, catalog.registry_dir)
    db = sqlite3.connect(
        args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro"), uri=True
    )
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        changes, counts = _changes(db, index)
        print(json.dumps(dict(counts), sort_keys=True))
        if not args.apply or not changes:
            return
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(args.backup, "xt", encoding="utf-8", newline="\n") as backup:
            for document_id, old_metadata, links, _, _, _ in changes:
                backup.write(
                    json.dumps(
                        {"document_id": document_id, "metadata_json": old_metadata, "links": links},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        for document_id, _, _, removed, rows, metadata in changes:
            db.executemany("DELETE FROM rag_work_link WHERE link_id=?", ((k,) for k in removed))
            db.executemany(
                "INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on) "
                "VALUES(?,?,?,?,?,?)",
                rows,
            )
            db.execute("UPDATE rag_document SET metadata_json=? WHERE document_id=?", (metadata, document_id))
        if _changes(db, index)[0]:
            raise RuntimeError("ANN work links are not idempotent; rolling back")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
