"""A failed generation keeps evidence and partial receipts without a paid retry."""
import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ani_menu_agent import cli
from ani_menu_agent.models import ContentPackage, Fact, SourceEvidence
from ani_menu_agent.sources.page_collector import SourcePageCapture


URL = "https://one-piece.com/news/82812/index.html"
REQUEST_ID = "failure-receipt-news"
SECRET_ERROR = "provider error must not enter receipt: sk-private-placeholder"
USAGE = [
    {"stage": "fact_extraction", "total_tokens": 30, "estimated_cost_usd": 0.003},
    {"stage": "link_review", "total_tokens": 10, "estimated_cost_usd": 0.001},
    {"stage": "image_text_extraction", "total_tokens": 20, "estimated_cost_usd": 0.002},
]


def install_offline_input(monkeypatch, outcome):
    """Keep the actual SQLite persistence/load path; replace all external work."""
    def forbidden(*_args, **_kwargs):
        pytest.fail("External I/O must not occur in the offline receipt regression")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(cli, "resolve_unmatched_work_with_llm", forbidden)
    capture = SourcePageCapture(
        source_id="one_piece_official", title="ONE PIECE 공식 소식", url=URL,
        fetched_at="2026-11-26T00:00:00Z", text="ONE PIECE 공식 소식 안내",
        image_urls=[], link_review_usage=[USAGE[1]],
    )
    package = ContentPackage(
        request_id=REQUEST_ID, topic="원피스 소식", content_kind="information",
        content_type="news", menu_work_ids=[8],
        sources=[SourceEvidence(id="one_piece_official", title="공식 소식", url=URL,
                                publisher="ONE PIECE", is_official=True)],
        facts=[Fact(id="fact-one", statement="원피스 공식 소식", verified=True,
                    source_ids=["one_piece_official"])],
    )
    monkeypatch.setattr(cli, "collect_official_page", lambda *_a, **_kw: capture)
    monkeypatch.setattr(cli, "extract_page_image_text", lambda *_a, **_kw: ([], [USAGE[2]]))
    monkeypatch.setattr(cli, "extract_page_payload", lambda _payload: (package, USAGE[0]))
    monkeypatch.setattr(cli, "infer_strong_content_type", lambda _package: "news")
    monkeypatch.setattr(cli, "immediate_news_action_expiry", lambda _p: {"status": "not_applicable"})
    monkeypatch.setattr(cli, "_add_same_work_catalog_images", lambda *_a, **_kw: 0)
    monkeypatch.setattr(cli, "image_curator", lambda state: state)
    monkeypatch.setattr(cli, "_supplement_goods_art_after_download", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "_export_rendered_cards", lambda *_a, **_kw: [])

    def invoke_provider(state):
        # Real repository reconstruction happens before the substituted provider.
        assert state["package"]["request_id"] == REQUEST_ID
        assert state["package"]["facts"][0]["id"] == "fact-one"
        assert state["usage_records"] == USAGE
        if isinstance(outcome, BaseException):
            raise outcome
        return {"status": outcome, "package": state["package"], "usage_records": USAGE}

    monkeypatch.setattr(cli, "build_graph", lambda: SimpleNamespace(invoke=invoke_provider))


def invoke(output):
    return CliRunner().invoke(cli.app, [
        "crawl-generate", "--source", "one_piece_official", "--url", URL,
        "--content-type", "news", "--expected-work-id", "8", "--output", str(output),
    ])


@pytest.mark.parametrize("exception_type", [ValueError, TypeError])
def test_failed_provider_retains_known_usage_and_sqlite_evidence(monkeypatch, tmp_path, exception_type):
    failure = exception_type(SECRET_ERROR)
    install_offline_input(monkeypatch, failure)
    output = tmp_path / "attempt" / "result.json"

    result = invoke(output)

    assert result.exception is failure
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "generation_failed"
    assert saved["request_id"] == REQUEST_ID
    assert saved["expected_work_request"] == {
        "source_id": "one_piece_official", "source_url": URL,
        "content_type": "news", "menu_work_id": 8,
    }
    assert saved["failure"] == {"stage": "content_generation", "error_type": exception_type.__name__}
    assert SECRET_ERROR not in output.read_text(encoding="utf-8")
    assert saved["usage_records"] == USAGE
    assert saved["usage_summary"]["estimated_cost_usd"] == 0.006
    assert saved["usage_summary"]["scope"] == "known_usage_only"
    assert saved["usage_summary"]["complete"] is False
    assert saved["cost_accounting"] == {
        "complete": False, "reason": "downstream_usage_unknown",
        "known_estimated_cost_usd": 0.006, "estimated_total_cost_usd": None,
    }
    assert not list(output.parent.glob(".generation-failure-*"))
    assert "package" not in saved and "quality_report" not in saved
    with sqlite3.connect(tmp_path / "agent-test.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM raw_document").fetchone()[0] == 1
        rows = connection.execute(
            "SELECT record_kind FROM agent_records WHERE request_id=?", (REQUEST_ID,)
        ).fetchall()
        assert {row[0] for row in rows} >= {"fact", "source"}
        assert connection.execute("SELECT count(*) FROM content_drafts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM content").fetchone()[0] == 0


def test_existing_attempt_result_is_never_replaced(monkeypatch, tmp_path):
    failure = ValueError(SECRET_ERROR)
    install_offline_input(monkeypatch, failure)
    output = tmp_path / "result.json"
    previous = b'{"status":"held","old_review_history":["preserve"]}\n'
    output.write_bytes(previous)

    assert invoke(output).exception is failure
    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(".generation-failure-*"))


def test_receipt_write_error_does_not_hide_original_provider_error(monkeypatch, tmp_path):
    failure = TypeError(SECRET_ERROR)
    install_offline_input(monkeypatch, failure)

    def unavailable(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(cli.os, "link", unavailable)
    output = tmp_path / "result.json"
    assert invoke(output).exception is failure
    assert not output.exists()
    assert not list(tmp_path.glob(".generation-failure-*"))


def test_process_interrupt_is_not_reclassified_as_generation_failure(monkeypatch, tmp_path):
    install_offline_input(monkeypatch, KeyboardInterrupt())
    output = tmp_path / "result.json"

    def forbidden(*_args, **_kwargs):
        pytest.fail("BaseException must bypass the generation failure handler")

    monkeypatch.setattr(cli, "_write_generation_failure", forbidden)
    assert invoke(output).exit_code != 0
    assert not output.exists()


def test_normal_generation_contract_is_unchanged(monkeypatch, tmp_path):
    install_offline_input(monkeypatch, "ready_for_feed")
    output = tmp_path / "result.json"

    result = invoke(output)

    assert result.exit_code == 0, result.exception
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "ready_for_feed"
    assert saved["package"]["request_id"] == REQUEST_ID
    assert saved["usage_records"] == USAGE
    assert "failure" not in saved and "cost_accounting" not in saved
    assert "complete" not in saved["usage_summary"]


@pytest.mark.parametrize("malformed_field", [
    "stage_timings_seconds", "usage_records", "unserializable", "nan_cost", "unserializable_receipt",
    "mixed_receipts",
])
def test_result_finalization_failure_retains_partial_receipts(monkeypatch, tmp_path, malformed_field):
    install_offline_input(monkeypatch, "ready_for_feed")
    real_generation = cli._run_generation_from_db
    downstream = {"stage": "review", "total_tokens": 40, "estimated_cost_usd": 0.004}

    def malformed(*args, **kwargs):
        package, result = real_generation(*args, **kwargs)
        result["usage_records"] = [*USAGE, downstream]
        if malformed_field == "stage_timings_seconds":
            result[malformed_field] = None
        elif malformed_field == "usage_records":
            result[malformed_field] = [*USAGE, {"estimated_cost_usd": "not-a-cost"}]
        elif malformed_field in {"nan_cost", "unserializable_receipt"}:
            invalid = {"estimated_cost_usd": float("nan")} if malformed_field == "nan_cost" else {
                "estimated_cost_usd": 0.004, "nonserializable": object(),
            }
            result["usage_records"] = [*USAGE, invalid]
        elif malformed_field == "mixed_receipts":
            result["usage_records"] = [
                *USAGE, downstream, None, {"estimated_cost_usd": float("nan")},
                {"estimated_cost_usd": "bad"}, {"nonserializable": object()},
            ]
            result["stage_timings_seconds"] = None
        else:
            result[malformed_field] = object()
        return package, result

    monkeypatch.setattr(cli, "_run_generation_from_db", malformed)
    output = tmp_path / "result.json"

    result = invoke(output)

    expected_error = ValueError if malformed_field in {"usage_records", "nan_cost"} else TypeError
    assert isinstance(result.exception, expected_error)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "generation_failed"
    assert saved["failure"]["error_type"] == expected_error.__name__
    expected_usage = USAGE if malformed_field in {
        "usage_records", "nan_cost", "unserializable_receipt",
    } else [*USAGE, downstream]
    assert saved["usage_records"] == expected_usage
    assert saved["cost_accounting"]["complete"] is False
    assert saved["cost_accounting"]["estimated_total_cost_usd"] is None
    assert not list(tmp_path.glob(".generation-failure-*"))


def test_cli_process_exits_nonzero_with_receipt_and_exception_type(tmp_path):
    # Exercise the real Typer exception renderer outside CliRunner. The worker
    # can capture this original type while the persisted receipt stays redacted.
    output = tmp_path / "subprocess-result.json"
    script = """
import importlib.util
import sys
import pytest
spec = importlib.util.spec_from_file_location('failure_test', sys.argv.pop(1))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with pytest.MonkeyPatch.context() as patch:
    module.install_offline_input(patch, TypeError(module.SECRET_ERROR))
    module.cli.app()
"""
    result = subprocess.run([
        sys.executable, "-c", script, str(Path(__file__).resolve()),
        "crawl-generate", "--source", "one_piece_official", "--url", URL,
        "--content-type", "news", "--expected-work-id", "8", "--output", str(output),
    ], capture_output=True, text=True, timeout=30, check=False)

    assert result.returncode != 0
    assert "TypeError" in result.stderr
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["failure"]["error_type"] == "TypeError"
    assert saved["usage_records"] == USAGE
    assert SECRET_ERROR not in output.read_text(encoding="utf-8")
