-- Add the relational evidence links required by collection and image workers.
-- Apply only to an ani_rag database after the base schema and migrations 0002-0005.
-- This migration is idempotent and has not been applied to the live database.

CREATE TABLE IF NOT EXISTS rag_document_content_type (
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    content_type TEXT NOT NULL CHECK(content_type IN ('character','guide','people','music','humor','curation','review')),
    assigned_by TEXT NOT NULL CHECK(assigned_by IN ('source_rule','evidence_planner','operator')),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id, content_type)
);

CREATE TABLE IF NOT EXISTS rag_document_entity_link (
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    relation_type TEXT NOT NULL,
    source_chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id),
    link_status TEXT NOT NULL CHECK(link_status IN ('matched','unresolved','ambiguous')),
    confidence REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id, entity_id, relation_type, source_chunk_id)
);

CREATE TABLE IF NOT EXISTS rag_entity_work_link (
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    menu_work_id INTEGER NOT NULL REFERENCES rag_work_catalog(menu_work_id),
    relation_type TEXT NOT NULL,
    source_document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    source_chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id),
    link_status TEXT NOT NULL CHECK(link_status IN ('matched','unresolved','ambiguous')),
    confidence REAL,
    valid_from TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(entity_id, menu_work_id, relation_type, source_document_id, source_chunk_id)
);

CREATE TABLE IF NOT EXISTS rag_entity_fact (
    entity_fact_id TEXT PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    property_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    source_chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id),
    confidence REAL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, property_key, source_document_id, source_chunk_id)
);

CREATE TABLE IF NOT EXISTS rag_image_entity_link (
    asset_id TEXT NOT NULL REFERENCES rag_image_asset(asset_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    relation_type TEXT NOT NULL,
    source_document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    source_chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id),
    status TEXT NOT NULL CHECK(status IN ('matched','unresolved','ambiguous')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(asset_id, entity_id, relation_type, source_document_id, source_chunk_id)
);
