"""Read existing dictionary facts into stable, evidence-bound generation requests.

No crawler, model, embedding, enqueue or database update runs here. A new day
does not create a new identity for an already considered source topic.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from menu_worker.rag_contract import RagRequest, RagContractError, digest


class DictionaryCandidates:
    def __init__(self, path, *, catalog_work_ids):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            raise RagContractError("rag_dictionary_unavailable")
        self.catalog_work_ids = frozenset(catalog_work_ids)
        if not self.catalog_work_ids or any(type(value) is not int or value <= 0 for value in self.catalog_work_ids):
            raise RagContractError("invalid_rag_catalog_scope")

    def __call__(self, route, limit=20, *, excluded_document_ids=()):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise RagContractError("invalid_rag_candidate_limit")
        if route["menu_work_id"] not in self.catalog_work_ids:
            raise RagContractError("rag_work_not_in_service_catalog")
        prototype = RagRequest.parse({**route, "work_id": "validation", "query": "validation",
            "seed_evidence": [{"document_id": "validation", "content_sha256": "0" * 64}]})
        sources = prototype.allowed_source_ids
        placeholders = ",".join("?" for _ in sources)
        excluded = tuple(sorted(set(excluded_document_ids)))
        if len(excluded) > 20000 or any(not isinstance(value, str) or len(value) > 300 for value in excluded):
            raise RagContractError("invalid_rag_candidate_exclusions")
        exclusion_sql = ("AND d.canonical_url NOT IN (SELECT canonical_url FROM rag_document WHERE document_id IN ("
                         + ",".join("?" for _ in excluded) + "))") if excluded else ""
        # mode=ro observes the real SQLite snapshot including its WAL. Never use
        # immutable=1 against a database which may still be receiving evidence.
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(f"""
                SELECT DISTINCT d.document_id,d.source_id,d.canonical_url,d.content_hash,d.title
                FROM rag_document d JOIN rag_source s ON s.source_id=d.source_id
                JOIN rag_work_link w ON w.document_id=d.document_id
                WHERE w.menu_work_id=? AND w.status='matched'
                  AND d.source_id IN ({placeholders}) AND d.parse_status='parsed'
                  AND d.evidence_type IN ('official_fact','reference_fact')
                  AND length(trim(coalesce(d.raw_text,'')))>0
                  AND (d.content_type=? OR EXISTS (
                    SELECT 1 FROM rag_document_content_type t WHERE t.document_id=d.document_id AND t.content_type=?))
                  AND NOT EXISTS (SELECT 1 FROM rag_work_link other WHERE other.document_id=d.document_id
                    AND other.status IN ('unresolved','ambiguous'))
                  AND NOT EXISTS (SELECT 1 FROM rag_draft_evidence used WHERE used.document_id=d.document_id)
                  {exclusion_sql}
                ORDER BY d.source_id,d.canonical_url,d.document_id LIMIT ?
            """, (prototype.menu_work_id, *sources, prototype.content_type, prototype.content_type, *excluded, limit)).fetchall()
            output, identities = [], set()
            for row in rows:
                # Topic identity is independent of retrieval ordering, date and
                # document revisions. Changed evidence causes admission conflict,
                # not a new paid attempt under a new daily identifier.
                identity = digest([prototype.menu_work_id, prototype.content_type,
                                   row["source_id"], row["canonical_url"]])
                if identity in identities:
                    continue
                identities.add(identity)
                request = RagRequest.parse({**route, "work_id": "dictionary:" + identity,
                    "query": row["title"].strip(),
                    "seed_evidence": [{"document_id": row["document_id"], "content_sha256": row["content_hash"]}],
                    "allowed_source_ids": [row["source_id"]]})
                output.append(request.as_dict())
            return output
