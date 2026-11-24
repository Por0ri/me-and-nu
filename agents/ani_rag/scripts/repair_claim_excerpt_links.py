"""Repair missing excerpt references introduced by the historical dictionary import."""

import argparse
import csv
import gzip
import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ani_rag.storage.excerpt_links import stored_excerpt_id


def plan(db):
    rows = db.execute(
        """SELECT e.claim_evidence_id, e.excerpt_id, e.document_id, k.source_index,
                  k.locator, k.start_offset, k.end_offset, k.chunk_text
           FROM rag_claim_evidence e JOIN rag_chunk k ON k.chunk_id=e.chunk_id AND k.document_id=e.document_id
           WHERE k.source_kind='excerpt' AND k.source_index IS NOT NULL AND e.excerpt_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM rag_excerpt x WHERE x.excerpt_id=e.excerpt_id)"""
    )
    changes = []
    for link, old, doc, index, locator, start, end, text in rows:
        digest = hashlib.sha256(f"{doc}:{index}".encode()).hexdigest()[:28]
        if old != f"ex_{digest}":
            continue
        new = stored_excerpt_id(db, doc, index, locator, start, end, text)
        if new == f"mde_{digest}":
            changes.append((link, old, new))
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires an unused --backup .csv.gz path")
    uri = args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            db.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
            changes = plan(db)
            print(f"verified_excerpt_refs={len(changes)}")
            if not (changes and args.apply):
                return
            args.backup.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(args.backup, "xt", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("claim_evidence_id", "previous_excerpt_id", "restored_excerpt_id"))
                writer.writerows(changes)
            updated = db.executemany(
                "UPDATE rag_claim_evidence SET excerpt_id=? WHERE claim_evidence_id=? AND excerpt_id=?",
                [(new, link, old) for link, old, new in changes],
            )
            if updated.rowcount != len(changes) or plan(db):
                raise RuntimeError("Excerpt repair did not converge")


if __name__ == "__main__":
    main()
