"""The optional queue boundary accepts an existing work before any paid fact extraction."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli, work_catalog
from ani_menu_agent import source_freshness
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.sources.page_collector import SourcePageCapture

URL = "https://one-piece.com/news/82812/index.html"


def invoke(tmp_path, work=8, *, bound=True):
    path = tmp_path / "result.json"
    arguments = ["crawl-generate", "--source", "one_piece_official", "--url", URL,
        "--content-type", "goods", "--output", str(path)]
    if bound:
        arguments += ["--expected-work-id", str(work)]
    result = CliRunner().invoke(cli.app, arguments)
    return result, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def capture(title="무관한 공지", url=URL, **evidence):
    return SourcePageCapture(source_id="one_piece_official", title=title, url=url, fetched_at="2026-11-02T00:00:00Z",
                             text="공식 페이지 본문", image_urls=[], **evidence)


def forbidden(*args, **kwargs):
    pytest.fail("This input must stop before this external or paid operation")


@pytest.fixture(autouse=True)
def block_external_operations(monkeypatch):
    monkeypatch.setattr(cli, "collect_official_page", forbidden)
    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", forbidden)
    monkeypatch.setattr(cli, "extract_page_image_text", forbidden)
    monkeypatch.setattr(cli, "extract_page_payload", forbidden)
    monkeypatch.setattr(cli, "AgentRepository", forbidden)


@pytest.mark.parametrize("work", [5, 2147483647])
def test_expected_work_must_already_belong_to_source_before_collection(tmp_path, work):
    result, saved = invoke(tmp_path, work=work)
    assert result.exit_code == 2 and saved is None
    assert "does not match the registered source" in result.output


def test_agent_only_work_cannot_be_used_as_service_work(monkeypatch, tmp_path):
    fields = "menu_work_id,title_english\n"
    (tmp_path / "menu_works.csv").write_text(fields + "1,Other title\n", encoding="utf-8")
    (tmp_path / "agent_works.csv").write_text(fields + "8,ONE PIECE\n", encoding="utf-8")
    monkeypatch.setattr(work_catalog, "_WORK_LIST_DIRECTORY", tmp_path)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    monkeypatch.setattr(work_catalog, "load_work_catalog", work_catalog.load_work_catalog.__wrapped__)
    assert {row["menu_work_id"] for row in work_catalog.load_work_catalog()} == {1, 8}
    result, saved = invoke(tmp_path)
    assert result.exit_code == 2 and saved is None
    assert "does not match the registered source" in result.output


@pytest.mark.parametrize("title", ["무관한 공지", "주술회전 신상품 안내"])
def test_absent_or_conflicting_article_identity_cannot_reach_registration_or_llm(monkeypatch, tmp_path, title):
    def collect(*_args, review_ambiguous_links, **_kwargs):
        assert review_ambiguous_links is False
        return capture(title)

    monkeypatch.setattr(cli, "collect_official_page", collect)
    result, saved = invoke(tmp_path)
    assert result.exit_code == 0, result.exception
    assert saved["status"] == "work_id_unresolved_skipped"
    assert saved["expected_work_request"] == {"source_id": "one_piece_official", "source_url": URL,
                                               "content_type": "goods", "menu_work_id": 8}


def test_capture_redirect_to_another_article_is_skipped_before_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture("ONE PIECE", URL.replace("82812", "12345")))
    result, saved = invoke(tmp_path)
    assert result.exit_code == 0 and saved["status"] == "source_redirect_skipped"
    assert saved["usage_records"] == []
    assert saved["usage_summary"]["llm_calls"] == 0
    assert saved["usage_summary"]["estimated_cost_usd"] == 0


@pytest.mark.parametrize(("title", "url", "status"), [
    ("ONE PIECE", URL.replace("82812", "12345"), "source_redirect_skipped"),
    ("무관한 공지", URL, "work_id_unresolved_skipped"),
])
def test_boundary_skip_preserves_usage_already_in_capture(monkeypatch, tmp_path, title, url, status):
    usage = {"stage": "link_review", "input_tokens": 10, "output_tokens": 2,
             "total_tokens": 12, "estimated_cost_usd": 0.001}
    page = capture(title, url, link_review_usage=[usage])
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page)

    result, saved = invoke(tmp_path)

    assert result.exit_code == 0, result.exception
    assert saved["status"] == status
    assert saved["usage_records"] == [usage]
    assert saved["usage_summary"]["llm_calls"] == 1
    assert saved["usage_summary"]["estimated_cost_usd"] == 0.001


@pytest.mark.parametrize(("page", "matched_ids"), [
    (capture("ONE PIECE / 주술회전 신상품 안내"), {8, 3}),
    (capture("ONE PIECE", structured_work_references=[{"relation": "about", "name": "주술회전"}]), {8, 3}),
    (capture(outbound_links=[{"url": "https://one-piece.com/", "text": "공식 홈페이지"}]), set()),
])
def test_ambiguous_evidence_or_site_navigation_skips_before_ocr(monkeypatch, tmp_path, page, matched_ids):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page)
    result, saved = invoke(tmp_path)
    assert result.exit_code == 0, result.exception
    assert saved["status"] == "work_id_unresolved_skipped"
    assert set(saved["matched_work_ids"]) == matched_ids


@pytest.mark.parametrize("page", [
    capture("ONE PIECE 가을 신상품"),
    capture(structured_work_references=[{"relation": "about", "name": "ONE PIECE"}]),
])
def test_verified_article_reaches_extraction_with_the_single_work(monkeypatch, tmp_path, page):
    observed = []
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page)
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    monkeypatch.setattr(cli, "AgentRepository", object)
    monkeypatch.setattr(cli, "expiry_evidence", lambda *_: {"status": "active", "end_date": "2099-12-31"})

    def record(payload):
        observed.append(payload["menu_work_ids"])
        raise ValueError("stop at substituted extraction")

    monkeypatch.setattr(cli, "extract_page_payload", record)
    result, _ = invoke(tmp_path)
    assert result.exit_code != 0
    assert observed == [[8]]


def test_missing_goods_end_date_preserves_bound_request(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture("ONE PIECE 가을 신상품"))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    monkeypatch.setattr(cli, "AgentRepository", object)
    result, saved = invoke(tmp_path)

    assert result.exit_code == 0, result.exception
    assert saved["status"] == "expiry_unresolved_held"
    assert saved["expected_work_request"]["menu_work_id"] == 8


@pytest.mark.parametrize("extraction_usage", [{}, None])
def test_reclassified_article_with_missing_usage_does_not_claim_zero_calls(monkeypatch, tmp_path, extraction_usage):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture("ONE PIECE 가을 신상품"))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    monkeypatch.setattr(cli, "AgentRepository", object)
    monkeypatch.setattr(cli, "expiry_evidence", lambda *_: {"status": "active", "end_date": "2099-12-31"})
    monkeypatch.setattr(cli, "extract_page_payload", lambda _: (SimpleNamespace(request_id="test-request"), extraction_usage))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda _: "event")
    result, saved = invoke(tmp_path)

    assert result.exit_code == 0, result.exception
    assert saved["status"] == "content_type_mismatch_skipped"
    assert saved["evidence_content_type"] == "event"
    assert saved["expected_work_request"]["content_type"] == "goods"
    assert saved["usage_records"] == [{}]
    assert saved["usage_summary"]["llm_calls"] == 1
    assert saved["cost_accounting"]["complete"] is False


@pytest.mark.parametrize("bound", [True, False])
def test_reclassified_article_preserves_all_paid_usage_without_generation(monkeypatch, tmp_path, bound):
    # Include distinct receipts so a later extraction result cannot overwrite
    # earlier link-review/OCR spending or count a receipt twice.
    link_usage = {"stage": "link_review", "input_tokens": 10, "output_tokens": 2,
                  "total_tokens": 12, "estimated_cost_usd": 0.001}
    ocr_usage = {"stage": "image_ocr", "input_tokens": 20, "cached_input_tokens": 5,
                 "output_tokens": 3, "total_tokens": 23, "estimated_cost_usd": 0.002}
    fact_usage = {"stage": "fact_extraction", "input_tokens": 30, "output_tokens": 4,
                  "total_tokens": 34, "estimated_cost_usd": 0.004}
    page = capture("ONE PIECE 가을 신상품", link_review_usage=[link_usage])
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: page)
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], [ocr_usage]))
    monkeypatch.setattr(cli, "AgentRepository", object)
    monkeypatch.setattr(cli, "_run_generation_from_db", forbidden)
    monkeypatch.setattr(cli, "expiry_evidence", lambda *_: {"status": "active", "end_date": "2099-12-31"})
    monkeypatch.setattr(cli, "extract_page_payload", lambda _: (
        SimpleNamespace(request_id="paid-type-mismatch", topic="ONE PIECE", sources=[]), fact_usage))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda _: "event")
    if not bound:
        registry = cli.load_source_registry()
        source = next(item for item in registry.sources if item.id == "one_piece_official")
        goods_only = source.model_copy(update={"content_types": [item for item in source.content_types if item.value == "goods"]})
        monkeypatch.setattr(cli, "load_source_registry", lambda: SimpleNamespace(sources=[goods_only]))

    result, saved = invoke(tmp_path, bound=bound)

    assert result.exit_code == 0, result.exception
    assert saved["status"] == "content_type_mismatch_skipped"
    assert saved["request_id"] == "paid-type-mismatch"
    assert saved["requested_content_type"] == "goods"
    assert saved["evidence_content_type"] == "event"
    assert saved["usage_records"] == [fact_usage, link_usage, ocr_usage]
    summary = saved["usage_summary"]
    assert summary["llm_calls"] == 3
    assert summary["input_tokens"] == 60
    assert summary["cached_input_tokens"] == 5
    assert summary["output_tokens"] == 9
    assert summary["total_tokens"] == 69
    assert summary["estimated_cost_usd"] == 0.007
    assert saved.get("cost_accounting", {}).get("complete") is not False
    assert list(tmp_path.iterdir()) == [tmp_path / "result.json"]
    if bound:
        assert saved["expected_work_request"]["menu_work_id"] == 8
    else:
        assert saved["supported_source_types"] == ["goods"]


def freeze_release_check(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 16, 12, tzinfo=tz)

    monkeypatch.setattr(source_freshness, "datetime", FixedDatetime)
    monkeypatch.setattr(cli, "datetime", FixedDatetime)


def one_piece_mid_month_capture():
    return capture("TVアニメ『ONE PIECE』withCAT グッズ").model_copy(update={
        "published_at": "2026-11-13",
        "text": "グッズは2026年11月13日より予約受付が開始しています。\n発売日：\n2027年2月中旬頃発売予定",
    })


def test_mid_month_goods_reaches_extraction_without_fabricating_a_day(monkeypatch, tmp_path):
    freeze_release_check(monkeypatch)
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: one_piece_mid_month_capture())
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    monkeypatch.setattr(cli, "AgentRepository", object)
    observed = []

    def record(payload):
        observed.append(payload)
        raise ValueError("stop at substituted extraction")

    monkeypatch.setattr(cli, "extract_page_payload", record)
    result, saved = invoke(tmp_path)
    assert result.exit_code != 0 and saved is None
    assert len(observed) == 1
    temporal = observed[0]["temporal_expiry"]
    assert observed[0]["menu_work_ids"] == [8]
    assert temporal["status"] == "active"
    assert temporal["release_precision"] == "mid_month"
    assert temporal["release_window_start"] == "2027-02-11"
    assert temporal["release_window_end"] == "2027-02-20"
    assert temporal.get("release_date") is None
    assert observed[0].get("release_date") is None
    assert observed[0].get("sale_end") is None


def test_mid_month_evidence_survives_cli_recheck_and_storage_boundary(monkeypatch, tmp_path):
    freeze_release_check(monkeypatch)
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: one_piece_mid_month_capture())
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    package = ContentPackage(
        request_id="period-goods", topic="원피스 굿즈",
        content_kind="information", content_type="goods",
    )
    monkeypatch.setattr(cli, "extract_page_payload", lambda _: (package, {}))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda _: "goods")
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_a, **_kw: 0)
    persisted = []

    class StopAtPersistence(RuntimeError):
        pass

    class Repository:
        def persist_page_capture(self, payload, *args, package):
            persisted.append((payload, package.model_dump(mode="json")))
            raise StopAtPersistence()

    monkeypatch.setattr(cli, "AgentRepository", Repository)
    result, _ = invoke(tmp_path)
    assert isinstance(result.exception, StopAtPersistence)
    assert len(persisted) == 1
    payload, stored = persisted[0]
    fact = next(item for item in stored["facts"] if item["details"].get("evidence_kind") == "source_release_window")
    assert fact["verified"] is False
    assert fact["details"]["release_precision"] == "mid_month"
    assert "2027年2月中旬" in fact["details"]["source_quote"]
    assert "release_date" not in fact["details"]
    assert "sale_end" not in fact["details"]
    assert "release_window_start" not in fact["details"]
    assert stored["event_start"] is None and stored["event_end"] is None
    assert stored["feed_valid_until"] == "2026-11-18"
    assert payload["temporal_expiry"]["release_precision"] == "mid_month"
    assert source_freshness.expiry_evidence(stored, "goods")["status"] == "active"
