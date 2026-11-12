"""The optional queue boundary accepts an existing work before any paid fact extraction."""
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli, work_catalog
from ani_menu_agent.sources.page_collector import SourcePageCapture

URL = "https://one-piece.com/news/82812/index.html"


def invoke(tmp_path, work=8):
    path = tmp_path / "result.json"
    result = CliRunner().invoke(cli.app, ["crawl-generate", "--source", "one_piece_official", "--url", URL,
        "--content-type", "goods", "--expected-work-id", str(work), "--output", str(path)])
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


def test_reclassified_article_stops_before_generating_another_type(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture("ONE PIECE 가을 신상품"))
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_: ([], []))
    monkeypatch.setattr(cli, "AgentRepository", object)
    monkeypatch.setattr(cli, "expiry_evidence", lambda *_: {"status": "active", "end_date": "2099-12-31"})
    monkeypatch.setattr(cli, "extract_page_payload", lambda _: (SimpleNamespace(request_id="test-request"), {}))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda _: "event")
    result, saved = invoke(tmp_path)

    assert result.exit_code == 0, result.exception
    assert saved["status"] == "content_type_mismatch_skipped"
    assert saved["evidence_content_type"] == "event"
    assert saved["expected_work_request"]["content_type"] == "goods"
