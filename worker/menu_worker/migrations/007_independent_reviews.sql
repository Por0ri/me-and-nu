-- Preserve the sealed producer rejection; independent review evidence is a
-- separate append-only record bound to that exact generation and result.
CREATE TABLE public.content_generation_review (
    review_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES public.content_generation_job(job_id),
    generation_attempt_id UUID NOT NULL REFERENCES public.content_generation_attempt(attempt_id),
    source_result_sha256 CHAR(64) NOT NULL CHECK (source_result_sha256 ~ '^[0-9a-f]{64}$'),
    review_result_sha256 CHAR(64) NOT NULL CHECK (review_result_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_path TEXT NOT NULL CHECK (length(artifact_path) > 0),
    artifact_sha256 CHAR(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (job_id, generation_attempt_id, source_result_sha256, review_result_sha256)
);

-- Existing selections retain NULL and their original result identity.
ALTER TABLE public.content_staging_selection
    ADD COLUMN review_id UUID REFERENCES public.content_generation_review(review_id);

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 7 WHERE version = 6;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 7);
