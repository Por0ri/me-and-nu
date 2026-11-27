-- RAG work identities are not article URLs. Preserve every existing article,
-- attempt, review and selection while adding a separate, durable admission ledger.
CREATE TABLE public.content_rag_generation_job (
    job_id UUID PRIMARY KEY,
    namespace TEXT NOT NULL,
    work_id TEXT NOT NULL,
    identity_key CHAR(64) NOT NULL UNIQUE CHECK (identity_key ~ '^[0-9a-f]{64}$'),
    input_sha256 CHAR(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    request JSONB NOT NULL CHECK (jsonb_typeof(request)='object'),
    reservation_id UUID NOT NULL UNIQUE,
    admission_receipt JSONB NOT NULL CHECK (jsonb_typeof(admission_receipt)='object'),
    admission_sha256 CHAR(64) NOT NULL CHECK (admission_sha256 ~ '^[0-9a-f]{64}$'),
    execution_status TEXT NOT NULL DEFAULT 'queued'
        CHECK (execution_status IN ('queued','running','succeeded','failed','needs_attention')),
    generation_attempt_id UUID,
    claim_token UUID,
    lease_expires_at TIMESTAMPTZ,
    generation_started_at TIMESTAMPTZ,
    outcome TEXT CHECK (outcome IN ('ready_for_staging','held','skipped','rejected')),
    producer_status TEXT,
    result_sha256 CHAR(64) CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_path TEXT,
    usage_records JSONB,
    cost_complete BOOLEAN,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    UNIQUE(namespace, work_id),
    CHECK ((execution_status='running') = (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (execution_status<>'succeeded' OR (outcome IS NOT NULL AND result_sha256 IS NOT NULL
        AND artifact_path IS NOT NULL AND producer_status IS NOT NULL AND usage_records IS NOT NULL
        AND cost_complete IS NOT NULL))
);

CREATE TABLE public.content_rag_generation_attempt (
    attempt_id UUID PRIMARY KEY,
    job_id UUID NOT NULL UNIQUE REFERENCES public.content_rag_generation_job(job_id),
    execution_status TEXT NOT NULL CHECK (execution_status IN ('running','succeeded','failed','needs_attention')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    generation_started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    outcome TEXT,
    producer_status TEXT,
    result_sha256 CHAR(64),
    artifact_path TEXT,
    usage_records JSONB,
    cost_complete BOOLEAN,
    error_code TEXT,
    UNIQUE(job_id, attempt_id)
);
ALTER TABLE public.content_rag_generation_job ADD CONSTRAINT fk_rag_generation_attempt
    FOREIGN KEY(job_id, generation_attempt_id)
    REFERENCES public.content_rag_generation_attempt(job_id, attempt_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version=8 WHERE version=7;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version=8);
