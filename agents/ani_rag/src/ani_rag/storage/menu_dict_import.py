"""Idempotent writer for approved facts from a menu_dict snapshot archive.

This writer requires an already-provisioned, caller-owned ani_rag SQLite
connection. It does not apply schema, create a DB, or schedule generation jobs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from ani_rag.adapter import canonical_source_url
from ani_rag.menu_dict_archive import MenuDictArchiveReader
from ani_rag.models import EvidenceDocument


@dataclass(frozen=True)
class MenuDictImportSummary:
    inserted: int
    already_present: int
    eligible_seen: int


class SQLiteMenuDictSnapshotImporter:
    """Persist snapshot documents into the RAG evidence tables, without queue writes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self._assert_schema()

    def _assert_schema(self) -> None:
        required = {"rag_source", "rag_document", "rag_excerpt", "rag_work_link"}
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rag_%'"
        ).fetchall()
        existing = {str(row["name"]) for row in rows}
        missing = required - existing
        if missing:
            raise RuntimeError(f"RAG schema must already be applied; missing tables: {sorted(missing)}")

    def register_sources(self, reader: MenuDictArchiveReader) -> int:
        """Record import provenance without approving MCP collection for these sources."""
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with self.connection:
            for source_id, policy in reader.allowed_sources.items():
                source = reader.sources.get(source_id)
                if source is None:
                    raise ValueError(f"menu_dict archive lacks source metadata for {source_id!r}")
                existing = self.connection.execute(
                    "SELECT configuration_json FROM rag_source WHERE source_id=?", (source_id,)
                ).fetchone()
                if existing:
                    configuration = json.loads(str(existing["configuration_json"]))
                    if not configuration.get("snapshot_import_only"):
                        raise ValueError(
                            f"Refusing to overwrite existing RAG source configuration for {source_id!r}"
                        )
                    if configuration.get("archive_sha256") != reader.archive_sha256:
                        raise ValueError(
                            f"Source {source_id!r} already has a different snapshot; explicit refresh is required"
                        )
                    continue
                content_types = list(policy["content_types"])
                configuration = {
                    "status": "snapshot_import_only",
                    "snapshot_import_only": True,
                    "mcp_collection_approved": False,
                    "dataset": "menu_dict",
                    "dataset_version": "2026-11-09",
                    "archive_sha256": reader.archive_sha256,
                    "source_name": source.name,
                    "source_license": source.license,
                    "source_attribution": source.attribution,
                    "source_notes": source.notes,
                    "import_policy": policy["license_basis"],
                    "permission_confirmation": policy.get("permission_confirmation", ""),
                }
                self.connection.execute(
                    """INSERT INTO rag_source(
                         source_id,topic_id,content_types_json,media_name,source_kind,collect_method,
                         site_url,feed_url,source_grade,copyright_status,license_basis,is_collecting,
                         configuration_json,created_at,updated_at)
                       VALUES(?,NULL,?,?, 'snapshot_reference','manual_import',?,NULL,'reference',?,?,0,?,?,?)""",
                    (source_id, _json(content_types), source.name, str(policy["site_url"]),
                     source.license, source.attribution or str(policy["license_basis"]),
                     _json(configuration), now, now),
                )
                inserted += 1
        return inserted

    def import_documents(
        self, reader: MenuDictArchiveReader, *, batch_size: int = 500,
    ) -> MenuDictImportSummary:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        inserted = already_present = eligible_seen = 0
        cursor = self.connection.cursor()
        try:
            for document in reader.iter_documents():
                eligible_seen += 1
                disposition = self._insert_document(cursor, document)
                if disposition:
                    inserted += 1
                else:
                    already_present += 1
                if eligible_seen % batch_size == 0:
                    self.connection.commit()
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()
        return MenuDictImportSummary(inserted, already_present, eligible_seen)

    def _insert_document(self, cursor: sqlite3.Cursor, document: EvidenceDocument) -> bool:
        canonical_url = canonical_source_url(document.source_url)
        content_hash = _document_hash(document)
        existing = cursor.execute(
            "SELECT document_id FROM rag_document WHERE source_id=? AND canonical_url=? AND content_hash=?",
            (document.source_id, canonical_url, content_hash),
        ).fetchone()
        if existing:
            return False
        source = cursor.execute(
            "SELECT source_site_id,configuration_json FROM rag_source WHERE source_id=?",
            (document.source_id,),
        ).fetchone()
        if source is None:
            raise ValueError(f"Snapshot source {document.source_id!r} must be registered first")
        config = json.loads(str(source["configuration_json"]))
        if not config.get("snapshot_import_only") or config.get("mcp_collection_approved"):
            raise PermissionError("Snapshot evidence cannot use an MCP-approved or unrelated source row")
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """INSERT INTO rag_document(
                 document_id,source_id,source_site_id,event_detail_id,external_id,content_type,evidence_type,
                 collection_method,title,raw_text,original_language,url,canonical_url,fetched_at,published_at,
                 valid_from,valid_until,author_name,author_attribution,curation_context_json,temporal_context,
                 metadata_json,raw_payload_json,parse_status,parse_error,purge_at,claims_json,content_hash,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (document.document_id, document.source_id, int(source["source_site_id"]), None,
             document.source_native_id, document.content_type.value, document.evidence_type.value,
             document.collection_method.value, document.title, document.body, document.language,
             document.source_url, canonical_url, document.retrieved_at.isoformat(), None, None, None,
             document.author_name, document.author_attribution.value, None, document.temporal_context,
             _json({**document.metadata, **({"publisher": document.publisher} if document.publisher else {})}),
             _json(document.raw_payload), document.parse_status.value, document.parse_error,
             document.purge_at.isoformat() if document.purge_at else None, _json(document.claims),
             content_hash, now),
        )
        for index, excerpt in enumerate(document.excerpts):
            excerpt_id = "mde_" + hashlib.sha256(f"{document.document_id}:{index}".encode()).hexdigest()[:28]
            cursor.execute(
                "INSERT INTO rag_excerpt(excerpt_id,document_id,locator,excerpt_text,supports_claim_index) VALUES(?,?,?,?,?)",
                (excerpt_id, document.document_id, excerpt.locator, excerpt.text, excerpt.supports_claim_index),
            )
        work_links = [("matched", work_id, None) for work_id in document.menu_work_ids]
        work_links.extend(("ambiguous", None, work_id) for work_id in document.candidate_menu_work_ids)
        if not work_links:
            work_links.append((document.work_link_status.value, None, None))
        for index, (status, work_id, candidate_id) in enumerate(work_links):
            link_id = "wl_" + hashlib.sha256(f"{document.document_id}:{index}".encode()).hexdigest()[:28]
            cursor.execute(
                """INSERT INTO rag_work_link(
                     link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)
                   VALUES(?,?,?,?,?,?)""",
                (link_id, document.document_id, work_id, candidate_id, status,
                 document.metadata.get("work_match_method")),
            )
        return True


def _document_hash(document: EvidenceDocument) -> str:
    excerpt_text = "\n".join(f"{item.locator or ''}\n{item.text}" for item in document.excerpts)
    return hashlib.sha256(f"{document.body or ''}\n{excerpt_text}".encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
