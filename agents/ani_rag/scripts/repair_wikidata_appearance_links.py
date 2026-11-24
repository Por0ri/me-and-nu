"""Link unresolved Wikidata character documents in an explicit raw-ID range to verified anime appearances."""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ani_rag.models import WorkLinkStatus
from ani_rag.wikidata_appearance import appearance_owners, archived_character_cast, character_appearance


def reconcile(db, owners, first: int, last: int, backup: Path | None = None) -> list[dict]:
    db.row_factory = sqlite3.Row
    outcomes, changes = [], []
    with db:
        db.execute("BEGIN IMMEDIATE" if backup else "BEGIN")
        documents = db.execute(
            """SELECT raw_document_id,document_id,title,url,external_id,raw_payload_json,metadata_json
               FROM rag_document WHERE source_id='wikidata_character_catalog' AND evidence_type='reference_fact'
               AND content_type='character' AND raw_document_id BETWEEN ? AND ? ORDER BY raw_document_id""",
            (first, last),
        )
        for doc in documents:
            payload = json.loads(doc["raw_payload_json"])
            qid = doc["external_id"] or ""
            url = urlsplit(doc["url"])
            links = [dict(row) for row in db.execute("SELECT * FROM rag_work_link WHERE document_id=?", (doc["document_id"],))]
            result = {"raw_id": doc["raw_document_id"], "title": doc["title"], "status": "preserved"}
            outcomes.append(result)
            if (not re.fullmatch(r"Q[1-9][0-9]*", qid) or payload.get("item_id") != qid
                    or url.scheme != "https" or url.hostname not in {"wikidata.org", "www.wikidata.org"}
                    or url.path.rstrip("/") != "/wiki/" + qid):
                continue
            if (len(links) != 1 or links[0]["status"] != "unresolved" or links[0]["matched_on"]
                    or links[0]["menu_work_id"] is not None or links[0]["candidate_menu_work_id"] is not None):
                continue
            resolution, appearances = character_appearance(payload, owners)
            cast = archived_character_cast(db, payload) if resolution.status is WorkLinkStatus.UNRESOLVED else None
            if cast:
                resolution, appearances = cast.resolution, (cast.appearance_qid,)
                result["evidence_document_ids"] = cast.evidence_document_ids
            result.update(status=resolution.status.value, candidates=resolution.candidate_menu_work_ids)
            if resolution.status is WorkLinkStatus.MATCHED:
                result.update(menu_work_id=resolution.menu_work_ids[0], appearance_qids=appearances)
                changes.append((doc, links[0], resolution, appearances, cast))
        if backup is not None and changes:
            snapshots = [{"raw_id": doc["raw_document_id"], "document_id": doc["document_id"],
                          "old_metadata_json": doc["metadata_json"], "old_link": link}
                         for doc, link, _, _, _ in changes]
            with backup.open("x", encoding="utf-8") as stream:
                json.dump(snapshots, stream, ensure_ascii=False, indent=2)
            for doc, link, resolution, appearances, cast in changes:
                db.execute("UPDATE rag_work_link SET status='matched',menu_work_id=?,matched_on=? WHERE link_id=?",
                           (resolution.menu_work_ids[0], resolution.matched_on, link["link_id"]))
                metadata = json.loads(doc["metadata_json"])
                metadata.update(work_match_method=resolution.matched_on, work_match_appearance_qids=",".join(appearances))
                if cast:
                    metadata["work_match_evidence_document_ids"] = ",".join(cast.evidence_document_ids)
                if metadata.get("work_match_seed_menu_work_id"):
                    metadata["seed_match_status"] = ("confirmed" if str(resolution.menu_work_ids[0])
                        == str(metadata["work_match_seed_menu_work_id"]) else "unconfirmed")
                db.execute("UPDATE rag_document SET metadata_json=? WHERE document_id=?",
                           (json.dumps(metadata, ensure_ascii=False, sort_keys=True), doc["document_id"]))
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--first-raw-id", type=int, required=True)
    parser.add_argument("--last-raw-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.first_raw_id <= 0 or args.first_raw_id > args.last_raw_id:
        parser.error("Supply an inclusive, positive raw-ID range")
    if args.apply and not args.backup:
        parser.error("Applying links requires an unused --backup .json path")
    db = sqlite3.connect(args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro"), uri=True)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        print(json.dumps(reconcile(db, appearance_owners(), args.first_raw_id, args.last_raw_id,
                                   args.backup if args.apply else None), ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
