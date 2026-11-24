import json
from types import SimpleNamespace

import pytest

from ani_menu_agent import cli
from ani_menu_agent.agents import crawlers
from ani_menu_agent.config import get_settings
from ani_menu_agent.discovery import DiscoveryItem
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.pipelines.workers import runtime
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import SourcePageCapture
from ani_menu_agent.storage import AgentRepository


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("ANI_MENU_DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("ANI_MENU_DATABASE_PATH", str(tmp_path / "period.sqlite3"))
    get_settings.cache_clear()
    repo = AgentRepository()
    repo.initialize()
    yield repo
    get_settings.cache_clear()


def test_crawler_alternates_types_even_with_a_full_news_backlog(monkeypatch, repository):
    scans = []
    turns = []

    def crawler(kind):
        def run(**kwargs):
            scans.append((kind, kwargs["limit"]))
            return SimpleNamespace(items=[], persisted_count=0, errors=[])
        return SimpleNamespace(run=run)

    def crawl_type(**kwargs):
        turns.append(kwargs["content_type"])
        assert kwargs["limit"] == 1
        assert not kwargs["retry_prior_failures"] and not kwargs["retry_work_identity_failures"]
        return {"funnel": {"attempted": 1}}

    monkeypatch.setattr(crawlers, "get_type_crawler", crawler)
    monkeypatch.setattr(cli, "crawl_type", crawl_type)
    runtime.run_crawler_worker(
        content_types=("news", "event", "goods"), batch_limit=200,
        stop_requested=lambda: len(turns) >= 6,
    )
    assert turns == ["news", "event", "goods"] * 2
    assert scans == [("news", 200), ("event", 200), ("goods", 200)]


def test_unchanged_holds_wait_for_new_evidence_or_explicit_retry(repository, tmp_path, monkeypatch):
    item = DiscoveryItem(id="lead", title="Festival", summary="Details soon",
                         url="https://example.com/fest", source_id="fixture", content_types=["event"])

    def store(item):
        repository.persist_discovery([item.model_dump(mode="json")], region_policy="mixed")

    def held(*, candidate_id, output, **kwargs):
        output.write_text(json.dumps({"status": "expiry_unresolved_held"}), encoding="utf-8")

    monkeypatch.setattr(cli, "crawl_generate", held)
    monkeypatch.setattr(cli, "run_source_discovery", lambda *_a, **_kw: {})
    def drain(**opts):
        return cli.crawl_type("event", limit=1, stored_candidates_only=True,
                              output_dir=tmp_path / "reports", enqueue_only=True, **opts)["outcomes"]

    store(item)
    assert drain()[0]["attempt_count"] == 1
    assert not drain()
    item = item.model_copy(update={"summary": "Closing date announced"})
    store(item)
    changed = drain()[0]
    assert changed["attempt_count"] == 2
    assert changed["retry_note"] == "candidate_updated_since_previous_attempt"
    assert not drain()

    # A separate, explicitly requested retry is still possible for a held lead.
    store(item.model_copy(update={"id": "other", "url": item.url}))
    assert drain()[0]["candidate_id"] == "other"
    manual = drain(retry_candidate_id="other", retry_note="rechecked parser")
    assert manual[0]["retry_note"] == "rechecked parser"


@pytest.mark.parametrize("hold_reason", [
    "new_work_logged_pending_review", "temporary_id_collision", "kitsu_match_ambiguous",
])
def test_held_title_rechecked_once_when_catalog_gains_the_title(
    repository, tmp_path, monkeypatch, hold_reason,
):
    item = DiscoveryItem(id="lead", title="未登録アニメ", url="https://example.com/work",
                         source_id="fixture", content_types=["event"]).model_dump(mode="json")
    outcome = {"status": "work_id_unresolved_skipped", "work_match_audit": {
        "status": hold_reason, "evidence_title": "未登録アニメ",
    }}
    repository.persist_discovery([item], region_policy="mixed")
    repository.persist_candidate_attempt("event", item, outcome)
    monkeypatch.setattr(cli, "run_source_discovery", lambda *_a, **_kw: {})
    monkeypatch.setattr(cli, "crawl_generate", lambda output, **kw:
                        output.write_text(json.dumps(outcome), encoding="utf-8"))
    def drain():
        return cli.crawl_type("event", limit=1, stored_candidates_only=True,
                              output_dir=tmp_path / "reports", enqueue_only=True)["outcomes"]
    monkeypatch.setattr(cli, "exact_catalog_work_id", lambda *_a: None)
    assert not drain()
    monkeypatch.setattr(cli, "exact_catalog_work_id", lambda *_a: 7)
    assert drain()[0]["retry_note"] == "catalog_title_now_resolved"
    assert not drain()


def test_generation_return_keeps_card_reason_and_does_not_count_as_completed(repository, monkeypatch):
    package = ContentPackage(request_id="missing-image", topic="감독 인터뷰", content_kind="information",
                             content_type="news")
    job = repository.enqueue_pipeline_job(lane="time_sensitive", stage="generate", request_id=package.request_id,
                                          idempotency_key="generate-missing", payload={"content_type": "news"})
    monkeypatch.setattr(AgentRepository, "load_content_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(runtime, "immediate_news_action_expiry", lambda _: {"status": "active"})
    issue = {"code": "SOURCE_IMAGE_MISSING", "page": 2, "message": "Missing product image"}
    monkeypatch.setattr(runtime, "rights_preflight",
                        lambda _: {"status": "needs_crawler_update", "qa_report": [issue]})
    checks = iter([False, True])
    runtime.run_queue_worker(stage="generate", stop_requested=lambda: next(checks))
    with repository.connect() as db:
        result = db.execute("SELECT status,last_error FROM pipeline_jobs WHERE job_id=?", (job.job_id,)).fetchone()
        record = db.execute("SELECT request_id,payload_json FROM agent_records WHERE record_kind='crawler_return'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM feed_queue").fetchone()[0] == 0
    assert result["status"] == "returned"
    assert result["last_error"] == "needs_crawler_update: SOURCE_IMAGE_MISSING: Missing product image"
    assert record["request_id"] == package.request_id
    assert json.loads(record["payload_json"])["issues"] == [issue]


def test_direct_page_without_a_work_id_only_records_the_resolution(repository, tmp_path, monkeypatch):
    source = Source(
        id="fixture", name="Fixture", url="https://example.com/", category="fixture",
        content_types=["news"], role="primary", collection="html", status="active", region="global",
    )
    capture = SourcePageCapture(
        source_id=source.id, title="Unidentified animation", url="https://example.com/new",
        fetched_at="2026-11-23T00:00:00Z", text="A teaser with an unconfirmed title.", image_urls=[],
    )
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
    monkeypatch.setattr(cli, "collect_official_page", lambda *args, **kwargs: capture)
    monkeypatch.setattr(cli, "resolve_work_matches", lambda **kwargs: [])
    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", lambda **kwargs: [])
    output = tmp_path / "unmatched.json"
    cli.crawl_generate(source_id=source.id, url=str(capture.url), content_type="news",
                       output=output, prepare_only=True)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "work_id_unresolved_skipped"
    with repository.connect() as db:
        rows = db.execute("SELECT record_kind,request_id FROM agent_records").fetchall()
        assert [tuple(row) for row in rows] == [("work_resolution", report["request_id"])]
        assert db.execute("SELECT count(*) FROM pipeline_jobs").fetchone()[0] == 0
