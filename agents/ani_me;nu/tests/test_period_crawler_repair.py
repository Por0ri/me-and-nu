import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from pydantic import HttpUrl

from ani_menu_agent import cli
from ani_menu_agent.agents import crawlers
from ani_menu_agent.agents.image import download
from ani_menu_agent.config import get_settings
from ani_menu_agent.image_selection import image_visual_keys
from ani_menu_agent.period_repair import evidence_snapshot, has_repaired_evidence
from ani_menu_agent.pipelines.workers import runtime
from ani_menu_agent.sources.page_collector import SourcePageCapture
from ani_menu_agent.storage import AgentRepository


@pytest.fixture
def article(monkeypatch, tmp_path):
    monkeypatch.setenv("ANI_MENU_DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("ANI_MENU_DATABASE_PATH", str(tmp_path / "isolated.sqlite3"))
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "duplicate_baseline_path", None)
    repo = AgentRepository()
    url = "https://one-piece.com/news/test"
    capture = SourcePageCapture(
        source_id="one_piece_official",
        url=url,
        title="ONE PIECE 전시 행사",
        published_at="2026-01-01",
        fetched_at="2026-01-01",
        text="행사 기간: 2090년 3월 7일부터. 종료일 미정.",
        image_urls=[url + "/a.png"],
    )

    def no_network(*args, **kwargs):
        pytest.fail("Unexpected network or paid generation call")

    def image_bytes(url, *args):
        buf = io.BytesIO()
        Image.new("RGB", (1400, 1100), "red" if url.endswith("b.png") else "blue").save(buf, "PNG")
        return buf.getvalue(), "image/png"

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", no_network)
    monkeypatch.setattr(runtime, "build_content_generation_agent", no_network)
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture)
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_a: 0)
    monkeypatch.setattr(download, "_fetch_bytes", image_bytes)
    output = tmp_path / "prepared.json"
    cli.crawl_generate(
        source_id=capture.source_id, url=url, content_type="event", output=output, prepare_only=True
    )
    request = json.loads(output.read_text(encoding="utf-8"))["request_id"]
    job = repo.claim_pipeline_job(stage="generate", worker_id="generator")
    outcome = runtime._process_generation(repo, job)
    assert outcome.status == "returned"
    assert outcome.detail.startswith("needs_crawler_update:")
    repo.finish_pipeline_job(
        job.job_id, worker_id="generator", status=outcome.status, error=outcome.detail
    )
    yield repo, capture, request
    get_settings.cache_clear()


def jobs(repo):
    with repo.connect() as db:
        return [
            (row["stage"], row["status"], row["last_error"])
            for row in db.execute(
                "SELECT * FROM pipeline_jobs ORDER BY created_at,job_id"
            ).fetchall()
        ]


def test_normal_crawler_consumes_return_without_regenerating_on_cdn_alias(article, monkeypatch):
    repo, capture, request = article
    turns = []
    monkeypatch.setattr(
        crawlers,
        "get_type_crawler",
        lambda _: SimpleNamespace(
            run=lambda **_kw: SimpleNamespace(items=[], persisted_count=0, errors=[])
        ),
    )
    monkeypatch.setattr(cli, "crawl_type", lambda **kw: turns.append(kw["content_type"]) or {})
    capture.image_urls.append(HttpUrl(str(capture.url) + "/different-url-same-bytes.png"))
    runtime.run_crawler_worker(content_types=("event",), stop_requested=lambda: bool(turns))
    assert turns == ["event"]
    generation, recollection = jobs(repo)
    assert generation[:2] == ("generate", "returned")
    assert generation[2].startswith("needs_crawler_update:")
    assert recollection == ("crawl", "retry", "no_new_usable_evidence")
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM image_blobs").fetchone()[0] == 1
    runtime._drain_period_repair(repo, "event", "crawler")
    assert len(jobs(repo)) == 2  # Durable backoff survives a worker restart.
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    with repo.connect() as db:
        db.execute("UPDATE pipeline_jobs SET available_at='2000' WHERE stage='crawl'")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda owner: runtime._drain_period_repair(repo, "event", owner),
                ["crawler-a", "crawler-b"],
            )
        )
    assert jobs(repo)[-2:] == [("crawl", "completed", None), ("generate", "pending", None)]
    generated = repo.claim_pipeline_job(stage="generate", worker_id="generator")
    assert generated.request_id == request and generated.payload["repair_round"] == 1
    regenerated = repo.load_content_package(request, "information", content_type="event")
    assert len({im.fingerprint for im in regenerated.images}) == 2
    runtime._enqueue_review(
        repo,
        generated,
        {
            "request_id": request,
            "revision_count": 0,
            "requested_content_type": "event",
        },
    )
    review = repo.claim_pipeline_job(stage="review", worker_id="review")
    assert review.idempotency_key.endswith(generated.payload["repair_id"])
    assert review.payload["repair_id"] == generated.payload["repair_id"]
    repo.finish_pipeline_job(generated.job_id, worker_id="generator", status="completed")
    repo.finish_pipeline_job(
        review.job_id, worker_id="review", status="cancelled", error="needs_crawler_update"
    )
    runtime._drain_period_repair(repo, "event", "crawler")
    assert jobs(repo)[-1] == ("crawl", "cancelled", "period_repair_limit_reached")
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM feed_queue").fetchone()[0] == 0


def test_date_hold_requires_new_source_date_and_reuses_existing_request(article):
    repo, capture, request = article
    package = repo.load_content_package(request, "information", content_type="event")
    baseline = evidence_snapshot(package)
    issue = [{"code": "EVENT_PERIOD_MISSING"}]
    assert not has_repaired_evidence(package, baseline, issue)
    prior = {"content_type": "event", "revision_count": 1, "max_revision": 3, "review_failures": 1}
    repo.enqueue_pipeline_job(
        lane="time_sensitive",
        stage="review",
        idempotency_key=f"{request}:review:1",
        request_id=request,
        payload=prior,
    )
    review = repo.claim_pipeline_job(stage="review", worker_id="review")
    repo.persist_crawler_return(
        package,
        issues=issue,
        trace=["review"],
        revision=1,
        usage_records=[{"cost_usd": 0.15}],
        review_failures=2,
    )
    repo.finish_pipeline_job(
        review.job_id, worker_id="review", status="cancelled", error="needs_crawler_update"
    )
    capture.event_end = "2090-03-15"
    capture.event_date_evidence = "행사 종료일 2090년 3월 15일"
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    capture.text = "행사 기간: 2090년 3월 7일부터 2090년 3월 15일까지."
    runtime._drain_period_repair(repo, "event", "crawler")
    generated = repo.claim_pipeline_job(stage="generate", worker_id="generator")
    assert generated and generated.request_id == request
    assert generated.payload["review_failures"] == 2
    assert generated.payload["usage_records"] == [{"cost_usd": 0.15}]
    refreshed = repo.load_content_package(request, "information", content_type="event")
    assert evidence_snapshot(refreshed)["dates"]["event_end"] == "2090-03-15"


def test_no_evidence_stops_after_bounded_attempts_even_for_legacy_return(article):
    repo, _, _ = article
    with repo.connect() as db:
        # A return persisted before snapshots were introduced.
        db.execute(
            "UPDATE agent_records SET payload_json=json_remove(payload_json,'$.evidence_snapshot') "
            "WHERE record_kind='crawler_return'"
        )
    for _ in range(runtime.MAX_RECOLLECTION_ATTEMPTS + 1):
        runtime._drain_period_repair(repo, "event", "crawler")
        with repo.connect() as db:
            db.execute("UPDATE pipeline_jobs SET available_at='2000' WHERE stage='crawl'")
    assert jobs(repo)[-1] == ("crawl", "cancelled", "no_new_usable_evidence")
    assert len(jobs(repo)) == 2


def test_new_detail_photo_previously_used_as_cover_cannot_repair_a_missing_cover(article):
    repo, capture, request = article
    package = repo.load_content_package(request, "information", content_type="event")
    baseline = evidence_snapshot(package)
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    refreshed, _ = cli.recollect_period_evidence(repo, package)
    fresh_image = next(image for image in refreshed.images if str(image.url).endswith("b.png"))
    refreshed.prior_image_fingerprints = list(image_visual_keys(fresh_image))
    assert not has_repaired_evidence(
        refreshed, baseline, [{"code": "COVER_SOURCE_IMAGE_MISSING", "page": 1}]
    )
    assert has_repaired_evidence(
        refreshed, baseline, [{"code": "SOURCE_IMAGE_POOL_INSUFFICIENT", "page": 2}]
    )


def test_expired_crawler_lease_and_failed_write_leave_original_evidence_intact(
    article, monkeypatch
):
    repo, capture, request = article
    repo.schedule_period_repair("event")
    claim = repo.claim_pipeline_job(stage="crawl", worker_id="old", repair_content_type="event")
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    package = repo.load_content_package(request, "information", content_type="event")
    refreshed, captured = cli.recollect_period_evidence(repo, package)
    with repo.connect() as db:
        db.execute(
            "UPDATE pipeline_jobs SET lease_expires_at='2000' WHERE job_id=?", (claim.job_id,)
        )
    assert not repo.publish_period_repair(claim, "old", refreshed, captured, {})
    assert not repo.renew_pipeline_job_lease(claim.job_id, worker_id="old")
    with pytest.raises(RuntimeError, match="lease"):
        repo.finish_pipeline_job(claim.job_id, worker_id="old", status="cancelled")
    repo.release_period_repair(claim, "old")
    assert jobs(repo)[-1][:2] == ("crawl", "leased")
    next_claim = repo.claim_pipeline_job(
        stage="crawl", worker_id="new", repair_content_type="event"
    )
    assert not repo.publish_period_repair(claim, "old", refreshed, captured, {})

    def disk_error(*_a, **_kw):
        raise RuntimeError("storage failure")

    monkeypatch.setattr(repo, "enqueue_pipeline_job", disk_error)
    with pytest.raises(RuntimeError, match="storage failure"):
        repo.publish_period_repair(next_claim, "new", refreshed, captured, {})
    assert all(image.content_bytes for image in refreshed.images)
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM image_blobs").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM agent_records WHERE record_kind='image_asset'"
            ).fetchone()[0]
            == 1
        )
    assert len(jobs(repo)) == 2


@pytest.mark.parametrize("feed_status", ["ready", "held_for_quality_review"])
def test_approval_during_recollection_preserves_published_evidence(article, feed_status):
    repo, capture, request = article
    repo.schedule_period_repair("event")
    claim = repo.claim_pipeline_job(stage="crawl", worker_id="crawler", repair_content_type="event")
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    package = repo.load_content_package(request, "information", content_type="event")
    refreshed, captured = cli.recollect_period_evidence(repo, package)
    with repo.connect() as db:
        db.execute(
            "INSERT INTO feed_queue (request_id,topic,content_type,package_json,queued_at,status) "
            "VALUES (?,?,?,?,'2026-01-01',?)",
            (request, package.topic, "event", '{"approved":"original"}', feed_status),
        )
    assert not repo.publish_period_repair(claim, "crawler", refreshed, captured, {})
    with repo.connect() as db:
        approved = db.execute("SELECT status,package_json FROM feed_queue").fetchone()
        assert tuple(approved) == (feed_status, '{"approved":"original"}')
        assert db.execute("SELECT count(*) FROM image_blobs").fetchone()[0] == 1


def test_rerouted_news_return_is_only_claimed_by_its_effective_type(article):
    repo, _, _ = article
    with repo.connect() as db:
        db.execute(
            "UPDATE pipeline_jobs SET payload_json=json_set(payload_json,'$.content_type','news')"
        )
    repo.schedule_period_repair("news")
    assert len(jobs(repo)) == 1
    repo.schedule_period_repair("event")
    claim = repo.claim_pipeline_job(stage="crawl", worker_id="event", repair_content_type="event")
    assert claim.payload["content_type"] == "event"


def test_stop_releases_repair_without_consuming_an_attempt(article):
    repo, _, _ = article
    runtime._drain_period_repair(repo, "event", "stopping", stop_requested=lambda: True)
    with repo.connect() as db:
        assert tuple(
            db.execute(
                "SELECT status, attempts, lease_owner FROM pipeline_jobs WHERE stage='crawl'"
            ).fetchone()
        ) == ("retry", 0, None)


def test_legacy_temporal_gate_recovers_after_article_announces_dates(article):
    repo, capture, request = article
    with repo.connect() as db:
        db.execute("DELETE FROM agent_records WHERE record_kind='crawler_return'")
        db.execute(
            "DELETE FROM agent_records WHERE record_kind='fact' "
            "AND record_id LIKE '%-source-schedule'"
        )
        db.execute(
            "UPDATE agent_records SET event_start=NULL,event_end=NULL WHERE request_id=?",
            (request,),
        )
        db.execute(
            "UPDATE agent_records SET payload_json=json_set("
            "payload_json,'$.statement','일정 추후 공개',"
            "'$.details.event_date_evidence',NULL) WHERE record_kind='fact'"
        )
        db.execute("UPDATE pipeline_jobs SET last_error='expiry_unresolved_held'")
    package = repo.load_content_package(request, "information", content_type="event")
    assert (
        runtime._hold_period_package(repo, package.model_dump(mode="json"), "event", request)
        == "expiry_unresolved_held"
    )
    with repo.connect() as db:
        db.execute(
            "UPDATE agent_records SET payload_json=json_remove(payload_json,'$.evidence_snapshot') "
            "WHERE record_kind='temporal_gate'"
        )
    capture.image_urls.append(HttpUrl(str(capture.url) + "/b.png"))
    capture.text = "행사 기간: 2090년 3월 7일부터 2090년 3월 15일까지."
    runtime._drain_period_repair(repo, "event", "crawler")
    assert jobs(repo)[-1] == ("generate", "pending", None)
