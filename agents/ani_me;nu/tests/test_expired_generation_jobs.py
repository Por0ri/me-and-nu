from __future__ import annotations

import pytest

from ani_menu_agent.config import get_settings
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.pipelines.common.jobs import PipelineJobStatus, PipelineLane, PipelineStage
from ani_menu_agent.pipelines.workers import runtime
from ani_menu_agent.storage import AgentRepository, ExpiredFeedContentError


@pytest.mark.parametrize("stage", ["generate", "review"])
def test_legacy_product_character_mapping_holds_claimed_job_before_model_call(tmp_path, monkeypatch, stage):
    from ani_menu_agent.models import Fact, SourceEvidence, WorkMatchEvidence

    repo = AgentRepository(path=tmp_path / "legacy-character.sqlite3")
    source = SourceEvidence(id="inside_goods", role="editorial", publisher="Inside",
                           url="https://www.inside-games.jp/article/2026/11/24/191443.html",
                           title="ファミマで『ドラゴンクエストモンスターズ4』コラボ開催！「スライムまん」")
    package = ContentPackage(
        request_id="legacy-goods", topic=source.title, content_kind="information", content_type="goods",
        sources=[source], menu_work_ids=[419],
        work_match_evidence=[WorkMatchEvidence(menu_work_id=419, basis="headline_character",
                                              matched_value="スライム", confidence=0.92)],
        facts=[Fact(id="capture", source_ids=[source.id], fact_type="source_page_capture",
                    statement=source.title)],
    )
    repo.persist_information(package)
    # Reproduce an earlier worker's draft; new writers validate the identity.
    with repo.connect() as db:
        db.execute("INSERT INTO content_drafts(request_id,topic,content_kind,content_type,status,package_json,"
                   "created_at,updated_at) VALUES(?,?,'information','goods','generated_pending_review',?,'2000','2000')",
                   (package.request_id, package.topic, package.model_dump_json()))
    job = repo.enqueue_pipeline_job(lane="time_sensitive", stage=stage, idempotency_key="legacy-hold",
                                    request_id=package.request_id, payload={"content_type": "goods"})
    monkeypatch.setattr(runtime, "AgentRepository", lambda: repo)
    monkeypatch.setattr(runtime, "build_content_generation_agent", lambda: pytest.fail("Unexpected generation"))
    monkeypatch.setattr(runtime, "build_reviewer_agent", lambda: pytest.fail("Unexpected review"))
    checks = iter([False, True])
    runtime.run_queue_worker(stage=stage, stop_requested=lambda: next(checks))
    with repo.connect() as db:
        disposition = db.execute("SELECT status,attempts,last_error FROM pipeline_jobs WHERE job_id=?",
                                 (job.job_id,)).fetchone()
        assert tuple(disposition) == ("cancelled", 1, "work_id_unresolved_skipped")
        assert db.execute("SELECT status FROM content_drafts").fetchone()[0] == "work_id_unresolved_skipped"
        assert db.execute("SELECT count(*) FROM feed_queue").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("stage", "load_method"),
    [(PipelineStage.GENERATE, "load_content_package"), (PipelineStage.REVIEW, "load_generated_draft")],
)
def test_expired_feed_job_is_cancelled_without_retry(tmp_path, monkeypatch, stage, load_method):
    database = tmp_path / "expired-feed.sqlite3"
    monkeypatch.setenv("ANI_MENU_DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("ANI_MENU_DATABASE_PATH", str(database))
    get_settings.cache_clear()

    repository = AgentRepository(path=database)
    job = repository.enqueue_pipeline_job(
        lane=PipelineLane.TIME_SENSITIVE,
        stage=stage,
        idempotency_key="expired-feed-smoke",
        request_id="expired-request",
        payload={"content_type": "event"},
    )

    def expired_package(*_args, **_kwargs):
        raise ExpiredFeedContentError("source_sale_ended")

    monkeypatch.setattr(AgentRepository, load_method, expired_package)
    if stage == PipelineStage.REVIEW:
        with repository.connect() as connection:
            connection.execute(
                "INSERT INTO feed_queue (request_id,topic,content_type,package_json,queued_at,status,valid_until,purge_at) "
                "VALUES ('expired-request','행사','event',?,'2026-01-01','ready','2026-02-01','2026-03-01')",
                (ContentPackage(request_id="expired-request", topic="행사", content_kind="information", content_type="event").model_dump_json(),),
            )
    checks = 0

    def stop_after_one_claim():
        nonlocal checks
        checks += 1
        return checks >= 2

    runtime.run_queue_worker(
        stage=stage,
        lane=PipelineLane.TIME_SENSITIVE,
        stop_requested=stop_after_one_claim,
    )

    with repository.connect() as connection:
        row = connection.execute(
            "SELECT status, attempts, last_error FROM pipeline_jobs WHERE job_id=?",
            (job.job_id,),
        ).fetchone()

    assert row["status"] == PipelineJobStatus.CANCELLED.value
    assert row["attempts"] == 1
    assert row["last_error"] == "stale_source_sale_ended"
    if stage == PipelineStage.REVIEW:
        with repository.connect() as connection:
            assert connection.execute(
                "SELECT status FROM feed_queue WHERE request_id='expired-request'"
            ).fetchone()[0] == "ready"
    get_settings.cache_clear()
