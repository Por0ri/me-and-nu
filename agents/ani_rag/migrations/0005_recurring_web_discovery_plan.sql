-- Apply only to the dedicated ani_rag database after migrations 0002-0004.
-- Plans are opt-in and no plan rows are created by this migration.
-- This migration artifact has not been applied to any database.
CREATE TABLE IF NOT EXISTS rag_web_discovery_plan (
    plan_id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 60 AND 31536000),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0,1)),
    next_run_at TEXT NOT NULL,
    lease_expires_at TEXT,
    last_enqueued_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_web_discovery_plan_due
    ON rag_web_discovery_plan(is_enabled, next_run_at, lease_expires_at);
