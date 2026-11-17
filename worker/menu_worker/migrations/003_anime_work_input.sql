-- The registry and work catalogue are validated by the producer before INSERT.
-- Historical v2 anime probes have no catalogue work; their results stay readable.
ALTER TABLE public.content_generation_job
    ADD COLUMN menu_work_id BIGINT,
    DROP CONSTRAINT content_generation_job_source_id_check,
    DROP CONSTRAINT ck_content_job_anime_fixture;

-- An already claimed v2 probe keeps its lease and evidence. An undispatched or
-- unclaimed probe must never get picked up by the new anime consumer.
UPDATE public.content_generation_job
   SET execution_status = CASE WHEN generation_started_at IS NULL THEN 'cancelled' ELSE 'needs_attention' END,
       error_code = 'LEGACY_ANIME_FIXTURE_RETIRED',
       finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
 WHERE job_kind = 'anime' AND execution_status = 'queued';

ALTER TABLE public.content_generation_job
    ADD CONSTRAINT ck_content_job_work_source CHECK (
        (job_kind = 'fake' AND menu_work_id IS NULL AND source_id = 'fixture')
        OR (job_kind = 'anime' AND (
            (menu_work_id IS NULL AND source_id = 'fixture'
                AND canonical_url LIKE 'https://example.invalid/success/_%'
                AND execution_status <> 'queued')
            OR (menu_work_id IS NOT NULL AND menu_work_id > 0 AND source_id NOT IN ('', 'fixture')
                AND source_url ~* '^https://[^/?#[:space:]]+[/?]' AND canonical_url ~ '^https://[^/?#[:space:]]+/'
                AND source_url !~* '^https://example[.]invalid([:/?#]|$)'
                AND canonical_url !~* '^https://example[.]invalid([:/?#]|$)')
        ))
    );

CREATE FUNCTION public.require_new_anime_work() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'New anime jobs require a catalogue work' USING ERRCODE = '23514';
END;
$$;

-- The historical NULL shape is for existing rows only, even for direct SQL writers.
CREATE TRIGGER require_new_anime_work
    BEFORE INSERT OR UPDATE OF menu_work_id ON public.content_generation_job
    FOR EACH ROW WHEN (NEW.job_kind = 'anime' AND NEW.menu_work_id IS NULL)
    EXECUTE FUNCTION public.require_new_anime_work();

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 3 WHERE version = 2;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 3);
