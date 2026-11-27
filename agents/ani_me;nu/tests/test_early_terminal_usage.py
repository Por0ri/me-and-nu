"""Real CLI early returns retain costs without calling a model or service.

The producer JSON is read by the actual Celery controller cost validator, so a
held article cannot silently lose paid receipts or be mistaken for a free call.
"""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli
from ani_menu_agent.models import ContentPackage, ContentType, SourceEvidence
from ani_menu_agent.sources.models import SourceRegistry
from ani_menu_agent.sources.page_collector import SourcePageCapture


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "early_terminal_cost_validator", ROOT / "worker/menu_worker/bounded_generation.py"
)
CONTROLLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROLLER)
URL = "https://one-piece.com/news/83597/index.html"
REQUEST = "offline-expiry-extraction"
LINK = {"stage": "source_link_review", "input_tokens": 10, "output_tokens": 2,
        "total_tokens": 12, "estimated_cost_usd": 0.001}
OCR = {"stage": "event_detail_image_ocr", "input_tokens": 20, "output_tokens": 3,
       "total_tokens": 23, "estimated_cost_usd": 0.002}
FACT = {"stage": "fact_extraction", "input_tokens": 30, "output_tokens": 4,
        "total_tokens": 34, "estimated_cost_usd": 0.004}
REPOSITORY = cli.AgentRepository


def forbidden(*_args, **_kwargs):
    pytest.fail("An early-terminal accounting test reached external or later-stage work")


@pytest.fixture(autouse=True)
def offline_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("collect_official_page", "collect_reference_page", "resolve_unmatched_work_with_llm",
                 "extract_page_image_text", "extract_page_payload", "_run_generation_from_db",
                 "build_graph", "_export_rendered_cards", "AgentRepository"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setenv("ANI_MENU_DATABASE_PATH", str(tmp_path / "unused-agent.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Keep real source/work matching but make the test independent of unrelated
    # registry type additions. No source policy is written or changed on disk.
    registry = cli.load_source_registry()
    source = next(item for item in registry.sources if item.id == "one_piece_official")
    source = source.model_copy(update={"content_types": [ContentType.GOODS, ContentType.EVENT, ContentType.NEWS]})
    monkeypatch.setattr(cli, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))


def page(*, links=(), text="ONE PIECE 공식 상품 안내. 일정은 추후 공지."):
    return SourcePageCapture(source_id="one_piece_official", title="ONE PIECE 공식 안내", url=URL,
        fetched_at="2026-11-26T00:00:00Z", text=text, image_urls=[], link_review_usage=deepcopy(list(links)))


def invoke(tmp_path, *, kind="goods", candidate=False, bound=True, prepare_only=False):
    output = tmp_path / "result.json"
    arguments = ["crawl-generate", "--content-type", kind, "--output", str(output)]
    arguments += (["--candidate-id", "offline-candidate"] if candidate else
                  ["--source", "one_piece_official", "--url", URL])
    if bound and not candidate:
        arguments += ["--expected-work-id", "8"]
    if prepare_only:
        arguments += ["--prepare-only"]
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 0, (result.output, result.exception)
    assert output.is_file()
    return json.loads(output.read_text(encoding="utf-8"))


def status_for(temporal):
    return "stale_candidate_skipped" if temporal == "expired" else "expiry_unresolved_held"


def assert_receipts(saved, records, cost):
    assert saved["usage_records"] == records
    assert saved["usage_summary"]["llm_calls"] == len(records)
    assert Decimal(str(saved["usage_summary"]["estimated_cost_usd"])) == Decimal(cost)
    assert CONTROLLER.result_cost(saved) == Decimal(cost).quantize(Decimal("0.00000001"))


def set_temporal(monkeypatch, kind, statuses):
    iterator = iter(statuses)
    name = "immediate_news_action_expiry" if kind == "news" else "expiry_evidence"
    monkeypatch.setattr(cli, name, lambda *_a: {"status": next(iterator)})
    monkeypatch.setattr(cli, "extract_source_schedule", lambda *_a: {})


def set_candidate(monkeypatch, *, kind):
    candidate = {"id": "offline-candidate", "source_id": "one_piece_official", "url": URL,
                 "title": "ONE PIECE 공식 안내", "content_types": [kind]}
    monkeypatch.setattr(cli, "AgentRepository", lambda: SimpleNamespace(
        load_discovery_candidate=lambda value: deepcopy(candidate)))
    monkeypatch.setattr(cli, "_candidate_menu_work_ids", lambda *_a: [])


def test_actual_one_piece_missing_schedule_is_held_with_explicit_zero_usage(monkeypatch, tmp_path):
    """Reproduce the live result's genuine pre-extraction expiry branch."""
    def collect(*_a, review_ambiguous_links, **_kw):
        assert review_ambiguous_links is False
        return page()

    monkeypatch.setattr(cli, "collect_official_page", collect)
    # The production OCR module does not call models for ONE PIECE; this fake
    # returns that same no-OCR result while the temporal rules remain real.
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], []))
    saved = invoke(tmp_path)

    assert saved["status"] == "expiry_unresolved_held"
    assert saved["reason"] == "No unambiguous source-backed schedule was found."
    assert saved["expected_work_request"] == {
        "source_id": "one_piece_official", "source_url": URL, "content_type": "goods", "menu_work_id": 8}
    assert "request_id" not in saved and "package" not in saved
    assert_receipts(saved, [], "0")
    assert {p.name for p in tmp_path.iterdir()} == {"result.json"}


@pytest.mark.parametrize("kind", ["goods", "event", "news"])
@pytest.mark.parametrize("temporal", ["unresolved", "expired"])
@pytest.mark.parametrize("with_usage", [False, True])
def test_post_capture_hold_or_skip_keeps_previous_receipts(monkeypatch, tmp_path, kind, temporal, with_usage):
    records = [LINK, OCR] if with_usage else []
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(links=[LINK] if with_usage else []))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], [deepcopy(OCR)] if with_usage else []))
    set_temporal(monkeypatch, kind, [temporal])

    saved = invoke(tmp_path, kind=kind)

    assert saved["status"] == status_for(temporal)
    assert saved["expected_work_request"]["content_type"] == kind
    assert "request_id" not in saved
    assert_receipts(saved, records, "0.003" if with_usage else "0")


@pytest.mark.parametrize("kind", ["goods", "event", "news"])
@pytest.mark.parametrize("temporal", ["unresolved", "expired"])
@pytest.mark.parametrize("with_usage", [False, True])
def test_candidate_identity_preflight_keeps_link_receipt(monkeypatch, tmp_path, kind, temporal, with_usage):
    set_candidate(monkeypatch, kind=kind)
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(links=[LINK] if with_usage else []))
    set_temporal(monkeypatch, kind, [temporal])

    saved = invoke(tmp_path, kind=kind, candidate=True)

    assert saved["status"] == status_for(temporal)
    assert saved["candidate_id"] == "offline-candidate"
    assert_receipts(saved, [LINK] if with_usage else [], "0.001" if with_usage else "0")


def test_reused_preflight_capture_does_not_count_link_usage_twice(monkeypatch, tmp_path):
    set_candidate(monkeypatch, kind="goods")
    calls = []
    def collect(*_a, **_kw):
        calls.append(True)
        return page(links=[LINK])

    monkeypatch.setattr(cli, "collect_official_page", collect)
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], [deepcopy(OCR)]))
    matches = cli.resolve_work_matches(titles=["ONE PIECE"], content_type="goods", source_url=URL, keep_ambiguous=True)
    assert [item.menu_work_id for item in matches] == [8]
    monkeypatch.setattr(cli, "_resolve_captured_work", lambda *_a: matches)
    set_temporal(monkeypatch, "goods", ["active", "unresolved"])

    saved = invoke(tmp_path, candidate=True)

    assert saved["status"] == "expiry_unresolved_held"
    assert calls == [True]
    assert_receipts(saved, [LINK, OCR], "0.003")


@pytest.mark.parametrize("with_usage", [False, True])
def test_empty_body_result_keeps_zero_or_prior_usage(monkeypatch, tmp_path, with_usage):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(text="", links=[LINK] if with_usage else []))
    # Bound empty-body paths skip work matching and OCR; no paid operation is permitted.
    saved = invoke(tmp_path)

    assert saved["status"] == "needs_crawler_update"
    assert saved["qa_report"][0]["code"] == "SOURCE_BODY_EMPTY"
    assert_receipts(saved, [LINK] if with_usage else [], "0.001" if with_usage else "0")


def prepare_post_extraction(monkeypatch, *, kind, temporal, extraction):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(links=[LINK]))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], [deepcopy(OCR)]))
    monkeypatch.setattr(cli, "AgentRepository", object)
    package = ContentPackage(request_id=REQUEST, topic="원피스 공식 안내", content_kind="information",
        content_type=kind, menu_work_ids=[8], sources=[SourceEvidence(
            id="one_piece_official", title="공식 안내", url=URL, publisher="ONE PIECE", is_official=True)])
    monkeypatch.setattr(cli, "extract_page_payload", lambda *_a: (package, deepcopy(extraction)))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda *_a: kind)
    set_temporal(monkeypatch, kind, ["active", temporal])


@pytest.mark.parametrize("kind", ["goods", "event"])
@pytest.mark.parametrize("temporal", ["unresolved", "expired"])
def test_post_extraction_hold_keeps_extraction_link_ocr_once(monkeypatch, tmp_path, kind, temporal):
    prepare_post_extraction(monkeypatch, kind=kind, temporal=temporal, extraction=FACT)
    saved = invoke(tmp_path, kind=kind)

    assert saved["status"] == status_for(temporal)
    assert saved["request_id"] == REQUEST
    assert saved["menu_work_ids"] == [8]
    assert_receipts(saved, [FACT, LINK, OCR], "0.007")
    assert {p.name for p in tmp_path.iterdir()} == {"result.json"}


@pytest.mark.parametrize("extraction", [None, {}, {"stage": "fact_extraction", "total_tokens": 99}])
def test_post_extraction_missing_cost_is_incomplete_and_cannot_be_zero(monkeypatch, tmp_path, extraction):
    prepare_post_extraction(monkeypatch, kind="goods", temporal="unresolved", extraction=extraction)
    saved = invoke(tmp_path)

    assert saved["status"] == "expiry_unresolved_held"
    assert saved["usage_records"] == [extraction or {}, LINK, OCR]
    assert saved["usage_summary"]["llm_calls"] == 3
    assert saved["cost_accounting"]["complete"] is False
    with pytest.raises(CONTROLLER.StopRun, match="^incomplete_cost_evidence$"):
        CONTROLLER.result_cost(saved)


@pytest.mark.parametrize("missing", [
    {"stage": "source_link_review", "input_tokens": 11},
    {"stage": "source_link_review", "usage_complete": False, "estimated_cost_usd": 0},
])
def test_pre_extraction_incomplete_link_receipt_is_not_declared_free(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(links=[missing]))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], []))
    set_temporal(monkeypatch, "goods", ["unresolved"])
    saved = invoke(tmp_path)

    assert saved["usage_records"] == [missing]
    assert saved["usage_summary"]["llm_calls"] == 1
    assert saved["cost_accounting"]["complete"] is False
    with pytest.raises(CONTROLLER.StopRun, match="^incomplete_cost_evidence$"):
        CONTROLLER.result_cost(saved)


@pytest.mark.parametrize("with_usage", [False, True])
def test_unmatched_work_report_and_output_both_keep_cost_evidence(monkeypatch, tmp_path, with_usage):
    stored = []
    monkeypatch.setattr(cli, "AgentRepository", lambda: SimpleNamespace(
        upsert_record=lambda **values: stored.append(deepcopy(values["payload"]))))
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page(links=[LINK] if with_usage else []))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a: ([], [deepcopy(OCR)] if with_usage else []))
    monkeypatch.setattr(cli, "_resolve_captured_work", lambda *_a: [])
    set_temporal(monkeypatch, "goods", ["active"])

    saved = invoke(tmp_path, bound=False)

    assert saved["status"] == "work_id_unresolved_skipped"
    assert stored == [saved]
    assert_receipts(saved, [LINK, OCR] if with_usage else [], "0.003" if with_usage else "0")


@pytest.mark.parametrize("termination", ["stale", "duplicate"])
@pytest.mark.parametrize("mode", ["paid_receipts", "missing_extraction", "prepare_only"])
def test_persisted_evidence_skip_keeps_receipts_and_original_evidence(monkeypatch, tmp_path, termination, mode):
    """Both post-persistence exits keep their facts and never start generation."""
    prepare_only = mode == "prepare_only"
    db = tmp_path / "evidence.sqlite3"
    repository = REPOSITORY(path=db)
    prepare_post_extraction(monkeypatch, kind="goods", temporal="active",
                           extraction=None if mode == "missing_extraction" else FACT)
    monkeypatch.setattr(cli, "AgentRepository", lambda: repository)
    if prepare_only:
        monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page())
        monkeypatch.setattr(cli, "extract_page_image_text", forbidden)
        monkeypatch.setattr(cli, "extract_page_payload", forbidden)
    monkeypatch.setattr(cli, "research_information", lambda state: state)
    monkeypatch.setattr(cli, "candidate_is_stale", lambda *_a: termination == "stale")
    duplicate_calls = []
    def duplicate(package, **_kwargs):
        duplicate_calls.append(package.request_id)
        return "already-existing-article"
    monkeypatch.setattr(repository, "find_duplicate_content", duplicate)

    saved = invoke(tmp_path, prepare_only=prepare_only)

    assert saved["status"] == termination + "_evidence_persisted"
    assert saved["menu_work_ids"] == [8]
    assert duplicate_calls == ([] if termination == "stale" else [saved["request_id"]])
    if termination == "duplicate":
        assert saved["duplicate_of"] == "already-existing-article"
    # The actual repository stored the selected article in disposable SQLite;
    # accounting output does not remove it or enqueue a second generation.
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM raw_document WHERE url=?", (URL,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM pipeline_jobs").fetchone()[0] == 0
    if prepare_only:
        assert_receipts(saved, [], "0")
    elif mode == "paid_receipts":
        assert saved["request_id"] == REQUEST
        assert_receipts(saved, [FACT, LINK, OCR], "0.007")
    else:
        assert saved["usage_records"] == [{}, LINK, OCR]
        assert saved["cost_accounting"]["complete"] is False
        with pytest.raises(CONTROLLER.StopRun, match="^incomplete_cost_evidence$"):
            CONTROLLER.result_cost(saved)


def test_legacy_sealed_result_without_usage_still_fails_closed():
    original = {"status": "expiry_unresolved_held", "content_type": "goods",
                "source_url": URL, "reason": "No unambiguous source-backed schedule was found."}
    before = deepcopy(original)
    with pytest.raises(CONTROLLER.StopRun, match="^missing_cost_evidence$"):
        CONTROLLER.result_cost(original)
    assert original == before
