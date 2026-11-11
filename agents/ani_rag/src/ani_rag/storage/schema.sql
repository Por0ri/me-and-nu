-- SQLite-compatible relational source of truth. This file is a design artifact only;
-- it has not been applied to any database. Vector indexes must reference these IDs.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS rag_source (
    source_site_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    topic_id INTEGER,
    content_types_json TEXT NOT NULL DEFAULT '[]',
    media_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    collect_method TEXT NOT NULL,
    site_url TEXT NOT NULL,
    feed_url TEXT,
    source_grade TEXT NOT NULL,
    copyright_status TEXT NOT NULL,
    license_basis TEXT NOT NULL,
    is_collecting INTEGER NOT NULL DEFAULT 0,
    last_collected_at TEXT,
    last_error TEXT,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_document (
    raw_document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES rag_source(source_id),
    source_site_id INTEGER NOT NULL REFERENCES rag_source(source_site_id),
    event_detail_id INTEGER,
    external_id TEXT,
    content_type TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    collection_method TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_text TEXT,
    original_language TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    published_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    author_name TEXT,
    author_attribution TEXT NOT NULL,
    claims_json TEXT NOT NULL DEFAULT '[]',
    curation_context_json TEXT,
    temporal_context TEXT,
    metadata_json TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    parse_status TEXT NOT NULL DEFAULT 'parsed',
    parse_error TEXT,
    purge_at TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, canonical_url, content_hash)
);

-- One immutable source document can support more than one editorial format.
-- The primary rag_document.content_type remains the original ingest lane;
-- additional formats require an evidence-backed planner decision.
CREATE TABLE IF NOT EXISTS rag_document_content_type (
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    content_type TEXT NOT NULL CHECK(content_type IN ('character','guide','people','music','humor','curation','review')),
    assigned_by TEXT NOT NULL CHECK(assigned_by IN ('source_rule','evidence_planner','operator')),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id, content_type)
);

CREATE TABLE IF NOT EXISTS rag_excerpt (
    excerpt_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    locator TEXT,
    excerpt_text TEXT NOT NULL,
    supports_claim_index INTEGER
);

-- ERD entity master and relation shape. IDs are supplied only after an explicit
-- catalog mapping; this schema does not mint work/entity identities from titles.
CREATE TABLE IF NOT EXISTS rag_entity (
    entity_id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    external_id_json TEXT NOT NULL DEFAULT '{}',
    attributes_json TEXT NOT NULL DEFAULT '{}',
    entity_profile TEXT,
    needs_refresh INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_entity_relation (
    relation_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    related_entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    score REAL,
    model_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(relation_type,entity_id,related_entity_id)
);

-- RAG menu_work_id catalog. This is distinct from rag_entity.entity_id until
-- an authoritative topic/entity mapping is available; never infer topic_id.
CREATE TABLE IF NOT EXISTS rag_work_catalog (
    menu_work_id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    external_ids_json TEXT NOT NULL DEFAULT '{}',
    topic_id INTEGER,
    identity_source_document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    identity_source_url TEXT NOT NULL,
    identity_evidence_quote TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rag_work_catalog_name
    ON rag_work_catalog(canonical_name COLLATE NOCASE);

-- Person/character ↔ work links preserve who made or appeared in which work.
-- Every relationship must point back to the source chunk that supports it.
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

-- Attribute facts such as birthdays retain provenance independently of the
-- entity's display profile, so curation can cite the exact supporting chunk.
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

CREATE TABLE IF NOT EXISTS rag_work_link (
    link_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    menu_work_id INTEGER,
    candidate_menu_work_id INTEGER,
    status TEXT NOT NULL CHECK(status IN ('matched','unresolved','ambiguous','not_applicable')),
    matched_on TEXT,
    CHECK(menu_work_id IS NULL OR candidate_menu_work_id IS NULL)
);

CREATE TABLE IF NOT EXISTS rag_chunk (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('body','excerpt')),
    source_index INTEGER,
    chunk_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS rag_retrieval (
    retrieval_id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    content_type TEXT NOT NULL,
    work_ids_json TEXT NOT NULL,
    seed_evidence_ids_json TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    backend TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_retrieval_hit (
    retrieval_id TEXT NOT NULL REFERENCES rag_retrieval(retrieval_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id),
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    PRIMARY KEY(retrieval_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS rag_generation_work (
    work_id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    retrieval_query TEXT NOT NULL,
    seed_evidence_ids_json TEXT NOT NULL,
    menu_work_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','enqueued','drafted','waiting_for_evidence','blocked','failed')),
    queue_job_id TEXT,
    last_error TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_content_draft (
    draft_id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    topic_id INTEGER,
    topic_label TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    summary TEXT,
    keyword_json TEXT NOT NULL DEFAULT '[]',
    thumbnail_url TEXT,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    is_changed_notice INTEGER NOT NULL DEFAULT 0,
    ai_judgment_basis_json TEXT NOT NULL DEFAULT '{}',
    event_detail_id INTEGER,
    published_at TEXT,
    valid_until TEXT,
    embedding_json TEXT,
    source_footer TEXT NOT NULL,
    ai_disclosure TEXT NOT NULL,
    menu_work_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieved_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieval_id TEXT REFERENCES rag_retrieval(retrieval_id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_content_card (
    card_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    card_image_url TEXT,
    source_image_url TEXT,
    source_page_url TEXT,
    image_rights_status TEXT,
    image_credit TEXT,
    UNIQUE(draft_id, page_no)
);

-- One text-free PNG plate per page. Card copy/layout stays as separate structured data for HTML.
CREATE TABLE IF NOT EXISTS rag_rendered_card (
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    card_id TEXT NOT NULL REFERENCES rag_content_card(card_id) ON DELETE CASCADE,
    image_png BLOB NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    frontend_copy_json TEXT NOT NULL,
    frontend_layout_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(draft_id, card_id)
);

CREATE TABLE IF NOT EXISTS rag_claim (
    claim_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    card_id TEXT REFERENCES rag_content_card(card_id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    claim_text TEXT NOT NULL,
    attribution TEXT
);

CREATE TABLE IF NOT EXISTS rag_draft_evidence (
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    relation TEXT NOT NULL,
    PRIMARY KEY(draft_id, document_id, relation)
);

CREATE TABLE IF NOT EXISTS rag_content_source (
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    raw_document_id INTEGER NOT NULL REFERENCES rag_document(raw_document_id),
    source_site_id INTEGER NOT NULL REFERENCES rag_source(source_site_id),
    publisher TEXT,
    published_at TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(draft_id, raw_document_id)
);

CREATE TABLE IF NOT EXISTS rag_content_entity_link (
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES rag_entity(entity_id),
    link_method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK(sort_order > 0),
    PRIMARY KEY(draft_id, entity_id),
    UNIQUE(draft_id, sort_order)
);

CREATE TABLE IF NOT EXISTS rag_content_tag (
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id) ON DELETE CASCADE,
    subtopic_id INTEGER NOT NULL,
    tagged_by TEXT NOT NULL,
    tagged_at TEXT NOT NULL,
    PRIMARY KEY(draft_id, subtopic_id)
);

CREATE TABLE IF NOT EXISTS rag_claim_evidence (
    claim_evidence_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES rag_claim(claim_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES rag_document(document_id),
    excerpt_id TEXT REFERENCES rag_excerpt(excerpt_id),
    chunk_id TEXT NOT NULL REFERENCES rag_chunk(chunk_id)
);

CREATE TABLE IF NOT EXISTS rag_image_asset (
    asset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES rag_source(source_id),
    source_url TEXT NOT NULL,
    source_page_url TEXT,
    source_native_id TEXT,
    image_role TEXT NOT NULL DEFAULT 'artwork',
    alt_text TEXT,
    local_path TEXT NOT NULL,
    content_sha256 TEXT,
    media_type TEXT,
    byte_size INTEGER,
    width INTEGER,
    height INTEGER,
    rights_basis TEXT,
    rights_evidence_url TEXT,
    work_link_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_image_work_link (
    image_work_link_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES rag_image_asset(asset_id) ON DELETE CASCADE,
    menu_work_id INTEGER,
    candidate_menu_work_id INTEGER,
    status TEXT NOT NULL CHECK(status IN ('matched','unresolved','ambiguous')),
    CHECK(menu_work_id IS NULL OR candidate_menu_work_id IS NULL)
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

CREATE TABLE IF NOT EXISTS rag_card_image (
    card_id TEXT PRIMARY KEY REFERENCES rag_content_card(card_id) ON DELETE CASCADE,
    asset_id TEXT REFERENCES rag_image_asset(asset_id)
);

CREATE TABLE IF NOT EXISTS rag_queue_job (
    job_id TEXT PRIMARY KEY,
    queue_name TEXT NOT NULL CHECK(queue_name IN ('collection','generation','review')),
    handler_kind TEXT,
    dedupe_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','retry_wait','blocked','failed','cancelled')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(queue_name, dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_rag_document_type_date ON rag_document(content_type, published_at);
CREATE INDEX IF NOT EXISTS ix_rag_work_link_work ON rag_work_link(menu_work_id, status);
CREATE INDEX IF NOT EXISTS ix_rag_queue_claim ON rag_queue_job(queue_name, status, created_at);
CREATE INDEX IF NOT EXISTS ix_rag_generation_pending ON rag_generation_work(status, created_at);
CREATE INDEX IF NOT EXISTS ix_rag_chunk_document ON rag_chunk(document_id, start_offset);

CREATE TABLE IF NOT EXISTS rag_job_event (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES rag_queue_job(job_id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    event_at TEXT NOT NULL,
    error TEXT,
    detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_review_history (
    review_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    revision_count INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_judgment_log (
    judgment_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL REFERENCES rag_content_draft(draft_id),
    agent_code TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 0),
    prompt_version TEXT,
    model_name TEXT,
    input_summary TEXT,
    judgment_result_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL,
    is_adopted INTEGER,
    token_cost_json TEXT,
    judged_at TEXT NOT NULL
);
