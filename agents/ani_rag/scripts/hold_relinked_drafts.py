"""Compare two RAG DBs and hold publishable drafts that lost cited work links.

Run against the corrected, offline database. A blocked collection item is the
review worker's normal evidence-request contract. Resolving it requires fresh
work-matched sources and another independent review.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.quality import web_gate_document_ids
from ani_rag.storage.sqlite_core import SQLiteRagStore


def document_ids(connection, draft):
    draft_id = draft["draft_id"]
    rows = connection.execute(
        """SELECT document_id FROM rag_draft_evidence WHERE draft_id=?
           UNION SELECT e.document_id FROM rag_claim c JOIN rag_claim_evidence e
             ON e.claim_id=c.claim_id WHERE c.draft_id=?""", (draft_id, draft_id),
    )
    referenced = {row[0] for row in rows}
    basis = json.loads(draft["ai_judgment_basis_json"])
    referenced.update(basis.get("evidence_ids", ()))
    referenced.update(web_gate_document_ids(basis))
    return {doc for doc in referenced if isinstance(doc, str) and doc}


def work_links(connection, reference, *, image=False):
    table, column = ("rag_image_work_link", "asset_id") if image else ("rag_work_link", "document_id")
    return {row[0] for row in connection.execute(
        f"SELECT menu_work_id FROM {table} WHERE {column}=? AND status='matched' "
        "AND menu_work_id IS NOT NULL", (reference,),
    )}


def lost_references(old, current, draft):
    works = set(json.loads(draft["menu_work_ids_json"]))
    sources = [(doc, False) for doc in document_ids(current, draft)]
    sources.extend((row[0], True) for row in current.execute(
        """SELECT i.asset_id FROM rag_card_image i JOIN rag_content_card c USING(card_id)
           WHERE c.draft_id=? AND i.asset_id IS NOT NULL""", (draft["draft_id"],),
    ))
    problems = []
    for ref, image in sorted(set(sources)):
        previous = work_links(old, ref, image=image)
        present = work_links(current, ref, image=image)
        if works & previous and not works & present:
            problems.append({"id": ref, "kind": "image" if image else "document",
                             "before": sorted(previous), "after": sorted(present)})
    return problems


def hold(store, draft, problems, now):
    db = store._connection
    draft_id = draft["draft_id"]
    active = db.execute(
        """SELECT job_id FROM rag_queue_job WHERE queue_name IN ('generation','review')
           AND status IN ('running','retry_wait','pending')
           AND (json_extract(payload_json,'$.draft_id')=? OR dedupe_key LIKE ?)""",
        (draft_id, f"revision:{draft_id}:%"),
    ).fetchone()
    if active:
        raise RuntimeError(f"Draft {draft_id} has an active job {active[0]}. Quiesce it first.")
    reason = "작품 연결 교정으로 이 초안의 인용 또는 이미지가 작품을 뒷받침하지 못합니다."
    request = "같은 작품에 연결된 자료로 해당 주장을 다시 대조하고, 이미지와 초안을 확인한 뒤 독립 검수를 요청하세요."
    reason_code = "CITED_WORK_LINK_CHANGED"
    event_key = hashlib.sha256(json.dumps([draft_id, problems], sort_keys=True).encode()).hexdigest()[:32]
    job_key = f"relinked_evidence:{draft_id}:{event_key}"
    payload = {"draft_id": draft_id, "menu_work_ids": json.loads(draft["menu_work_ids_json"]),
               "evidence_requests": [request], "reason": reason_code, "affected_references": problems}
    decision = {"draft_id": draft_id, "decision": "request_evidence",
                "issues": [{"code": reason_code, "message": reason, "blocking": True}],
                "evidence_requests": [request], "revision_instructions": [], "headline_edits": [],
                "reviewer_id": "work_link_audit", "scorecard": {}, "affected_references": problems}
    revision = db.execute(
        "SELECT COALESCE(MAX(revision_count),0) FROM rag_review_history WHERE draft_id=?", (draft_id,),
    ).fetchone()[0]
    stamp = now.isoformat()
    db.execute("UPDATE rag_content_draft SET status='evidence_requested',updated_at=? WHERE draft_id=?",
               (stamp, draft_id))
    serialized = json.dumps(decision, ensure_ascii=False)
    db.execute(
        """INSERT INTO rag_review_history(review_id,draft_id,reviewer_id,decision,revision_count,
           result_json,reviewed_at) VALUES(?,?,'work_link_audit','request_evidence',?,?,?)""",
        (f"rv_{event_key}", draft_id, revision, serialized, stamp),
    )
    db.execute(
        """INSERT INTO rag_judgment_log(draft_id,agent_code,stage,attempt_no,input_summary,
           judgment_result_json,verdict,judged_at) VALUES(?,'work_link_audit','review',?,?,?,
           'request_evidence',?)""", (draft_id, revision, reason, serialized, stamp),
    )
    store._enqueue_in_transaction(QueueItem(
        job_id=f"job_{event_key}", queue=QueueName.COLLECTION, status=QueueStatus.BLOCKED,
        created_at=now, updated_at=now, dedupe_key=job_key, payload=payload,
        last_error=reason_code,
    ))


def audit_and_hold(before: Path, corrected: Path, *, apply: bool = False) -> list[dict]:
    if before.samefile(corrected):
        raise ValueError("Supply the pre-repair snapshot and a separate corrected database.")
    before_uri = before.resolve().as_uri() + "?mode=ro"
    corrected_uri = corrected.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with closing(sqlite3.connect(before_uri, uri=True)) as old, closing(
        sqlite3.connect(corrected_uri, uri=True, timeout=15)
    ) as current:
        store = SQLiteRagStore(current)
        current.execute("PRAGMA foreign_keys=ON")
        result = []
        with store._transaction(immediate=apply):
            rows = current.execute(
                """SELECT draft_id,title,status,menu_work_ids_json,ai_judgment_basis_json,updated_at
                   FROM rag_content_draft WHERE status IN ('ready_for_downstream','pending_review')
                   ORDER BY draft_id"""
            ).fetchall()
            now = datetime.now(timezone.utc)
            for draft in rows:
                problems = lost_references(old, current, draft)
                if problems:
                    result.append({"draft_id": draft["draft_id"], "title": draft["title"],
                                   "old_status": draft["status"], "old_updated_at": draft["updated_at"],
                                   "work_ids": json.loads(draft["menu_work_ids_json"]), "lost": problems})
                    if apply:
                        hold(store, draft, problems, now)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--corrected", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = audit_and_hold(args.before, args.corrected, apply=args.apply)
    print(json.dumps({"applied": args.apply, "count": len(result), "affected": result},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
