-- Run inside initialize()'s database-wide migration lock and transaction.
-- Existing fake jobs, inputs, attempts, indexes, and artifact hashes keep their identity.
ALTER TABLE public.content_generation_job
    ADD COLUMN job_kind VARCHAR(16) NOT NULL DEFAULT 'fake'
        CHECK (job_kind IN ('fake', 'anime')),
    ADD CONSTRAINT ck_content_job_anime_fixture CHECK (
        job_kind <> 'anime' OR canonical_url LIKE 'https://example.invalid/success/_%'
    ),
    DROP CONSTRAINT content_generation_job_outcome_check,
    ADD CONSTRAINT ck_content_job_kind_outcome CHECK (
        (job_kind = 'fake' AND (outcome IS NULL OR outcome IN ('ready_for_staging', 'held', 'skipped')))
        OR (job_kind = 'anime' AND (
            (execution_status = 'succeeded' AND outcome IS NOT NULL AND outcome = 'routing_verified'
                AND producer_status IS NOT NULL AND producer_status = 'anime_probe_completed')
            OR (execution_status <> 'succeeded' AND outcome IS NULL)
        ))
    );

CREATE FUNCTION public.reject_content_job_kind_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Content job kind is immutable' USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER immutable_content_job_kind
    BEFORE UPDATE ON public.content_generation_job
    FOR EACH ROW WHEN (OLD.job_kind IS DISTINCT FROM NEW.job_kind)
    EXECUTE FUNCTION public.reject_content_job_kind_change();

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 2 WHERE version = 1;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 2);
