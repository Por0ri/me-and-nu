"""Paid-call boundaries and recovery of the local carousel batch orchestrator."""
import importlib.util
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_carousel_batch.py"
spec = importlib.util.spec_from_file_location("carousel_batch_runner", SCRIPT)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "src" / "ani_menu_agent").mkdir(parents=True)
    (agent / "src" / "ani_menu_agent" / "cli.py").write_text("# isolated unchanged source", encoding="utf-8")
    executable = tmp_path / "python"
    executable.write_text("test executable", encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": [
        {"source": "official", "url": f"https://example.com/news/{i}", "content_type": "news"}
        for i in range(4)], "excludedSourceUrls": []}), encoding="utf-8")
    calls, persisted = [], {}
    known_urls = set()

    async def preflight(url, name):
        calls.append("preflight")
        return set(known_urls)

    def generate(**kwargs):
        calls.append(("generate", kwargs["url"]))
        folder = kwargs["output"]
        folder.mkdir()
        (folder / "result.json").write_text(json.dumps({
            "package": {"request_id": folder.parent.name}, "status": "held",
            "usage_summary": {"estimated_cost_usd": 0.01}}), encoding="utf-8")

    def stage(result_path, destination):
        result = json.loads(result_path.read_text())
        identity = result["package"]["request_id"]
        return SimpleNamespace(items=[SimpleNamespace(digest=identity, images={1: b"1", 2: b"2", 3: b"3"})])

    async def import_run(**kwargs):
        assert kwargs["local_test_preview"] is True
        identity = kwargs["bundle"].items[0].digest
        calls.append(("import", kwargs["apply"], identity))
        created = identity not in persisted
        if kwargs["apply"]:
            persisted.setdefault(identity, len(persisted) + 1)
        return {"created": int(created), "unchanged": int(not created),
                "items": [{"content_id": persisted.get(identity), "digest": identity}]}

    monkeypatch.setattr(batch, "preflight", preflight)
    monkeypatch.setattr(batch.generator, "credential", lambda *args: "test-only-placeholder")
    monkeypatch.setattr(batch.generator, "run", generate)
    monkeypatch.setattr(batch, "stage_result", stage)
    monkeypatch.setattr(batch.importer, "run", import_run)
    kwargs = dict(agent_root=agent, agent_python=executable, credentials_file=tmp_path / "explicit.env",
                  candidates_file=candidates, output=tmp_path / "batch", database_url="postgresql+psycopg://test@127.0.0.1:55435/checking_live_e2e",
                  expected_database="checking_live_e2e", target=2, max_attempts=3, apply=True)
    return SimpleNamespace(kwargs=kwargs, calls=calls, persisted=persisted, known_urls=known_urls,
                           generate=generate, import_run=import_run, stage=stage)


def paid_calls(harness):
    return [call for call in harness.calls if isinstance(call, tuple) and call[0] == "generate"]


def test_target_then_resume_never_makes_extra_paid_calls(harness):
    first = batch.run_batch(**harness.kwargs)
    assert first["status"] == "complete" and first["imported"] == 2
    assert first["slideCount"] == 6 and first["operatorApprovalCreated"] is False
    assert harness.calls[0] == "preflight"
    assert len(paid_calls(harness)) == 2
    again = batch.run_batch(**harness.kwargs)
    assert again == first and len(paid_calls(harness)) == 2 and len(harness.persisted) == 2


def test_plan_never_reads_credentials_or_generates(harness, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Paid/credential operation in a plan")
    monkeypatch.setattr(batch.generator, "credential", forbidden)
    monkeypatch.setattr(batch.generator, "run", forbidden)
    result = batch.run_batch(**{**harness.kwargs, "apply": False})
    assert result["mode"] == "plan" and result["paidCallsMade"] is False
    assert not harness.kwargs["output"].exists()


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://test@prod.example.com/checking_live_e2e",
    "postgresql+psycopg://test@127.0.0.1/production",
    "postgresql+psycopg://test@127.0.0.1/checking_live_e2e?host=prod.example.com",
])
def test_unsafe_target_rejected_before_paid_call(harness, url):
    with pytest.raises(batch.importer.CarouselImportError):
        batch.run_batch(**{**harness.kwargs, "database_url": url})
    assert harness.calls == [] and not harness.kwargs["output"].exists()


def test_resume_commit_with_lost_acknowledgement_does_not_regenerate(harness, monkeypatch):
    failure = [True]
    async def commit_then_fail(**kwargs):
        result = await harness.import_run(**kwargs)
        if kwargs["apply"] and failure.pop():
            raise RuntimeError("injected lost acknowledgement")
        return result
    monkeypatch.setattr(batch.importer, "run", commit_then_fail)
    options = {**harness.kwargs, "target": 1}
    with pytest.raises(RuntimeError):
        batch.run_batch(**options)
    assert len(harness.persisted) == 1 and len(paid_calls(harness)) == 1
    monkeypatch.setattr(batch.importer, "run", harness.import_run)
    result = batch.run_batch(**options)
    assert result["status"] == "complete" and result["contentIds"] == [1]
    assert len(paid_calls(harness)) == 1 and len(harness.persisted) == 1


def test_incomplete_generation_not_retried_on_resume(harness, monkeypatch):
    def incomplete(**kwargs):
        harness.calls.append(("generate", kwargs["url"]))
        kwargs["output"].mkdir()
        raise batch.generator.GenerationError("Stopped")
    monkeypatch.setattr(batch.generator, "run", incomplete)
    result = batch.run_batch(**{**harness.kwargs, "target": 1, "max_attempts": 2})
    assert result["status"] == "bounded_stop" and result["attempts"] == 2
    assert len(paid_calls(harness)) == 2
    again = batch.run_batch(**{**harness.kwargs, "target": 1, "max_attempts": 2})
    assert again["attempts"] == 2 and len(paid_calls(harness)) == 2
    assert result["budgetUsedIncludingUnknownReservesUsd"] == 0.08


def test_estimated_cost_threshold_stops_before_next_candidate(harness, monkeypatch):
    def expensive(**kwargs):
        harness.generate(**kwargs)
        path = kwargs["output"] / "result.json"
        value = json.loads(path.read_text())
        value["usage_summary"]["estimated_cost_usd"] = 0.08
        path.write_text(json.dumps(value))
    monkeypatch.setattr(batch.generator, "run", expensive)
    result = batch.run_batch(**{**harness.kwargs, "max_estimated_cost_usd": 0.1})
    assert result["imported"] == 1 and result["status"] == "bounded_stop"
    assert len(paid_calls(harness)) == 1


def test_existing_source_urls_do_not_spend_or_count_as_new(harness):
    harness.known_urls.add("https://example.com/news/0")
    result = batch.run_batch(**harness.kwargs)
    assert result["imported"] == 2
    assert all(url != "https://example.com/news/0" for _, url in paid_calls(harness))


def test_duplicate_tracking_urls_and_exclusions_are_one_candidate(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"candidates": [
        {"source": "official", "url": "https://example.com/a?utm_source=x", "content_type": "news"},
        {"source": "official", "url": "https://example.com/a", "content_type": "event"},
        {"source": "official", "url": "https://example.com/b", "content_type": "news"}],
        "excludedSourceUrls": ["https://example.com/b"]}))
    result = batch.load_candidates(path)
    assert len(result) == 1 and result[0]["canonical_url"] == "https://example.com/a"


def test_changed_source_cannot_silently_resume(harness):
    batch.run_batch(**harness.kwargs)
    (harness.kwargs["agent_root"] / "src" / "ani_menu_agent" / "cli.py").write_text("changed")
    with pytest.raises(batch.BatchError, match="configuration differs"):
        batch.run_batch(**harness.kwargs)
    assert len(paid_calls(harness)) == 2


def test_existing_batch_lock_prevents_parallel_charges(harness):
    root = harness.kwargs["output"]
    root.mkdir()
    with batch.batch_lock(root), pytest.raises(batch.BatchError, match="Another worker"):
        batch.run_batch(**harness.kwargs)
    assert not paid_calls(harness)


def test_helper_explicit_source_overrides_old_editable_install(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "src" / "ani_menu_agent").mkdir(parents=True)
    (agent / "src" / "ani_menu_agent" / "cli.py").write_text("")
    executable = tmp_path / "python"
    executable.touch()
    output = tmp_path / "output"
    def launch(command, **kwargs):
        assert kwargs["env"]["PYTHONPATH"] == str(agent / "src")
        assert kwargs["env"]["ANI_MENU_DATABASE_PATH"] == str(output / "agent.sqlite3")
        assert kwargs["env"]["ANI_MENU_REVIEW_PROFILE"] == "standard"
        assert "DATABASE_URL" not in kwargs["env"]
        (output / "result.json").write_text(json.dumps({"package": {"request_id": "actual"}, "status": "held"}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(batch.generator.subprocess, "run", launch)
    monkeypatch.setattr(batch.generator, "credential", lambda *args: "test-only-placeholder")
    result = batch.generator.run(agent_root=agent, agent_python=executable, output=output,
                                 source="official", url="https://example.com/a", content_type="news",
                                 inherited={"PYTHONPATH": "wrong-checkout", "DATABASE_URL": "secret"})
    assert result["readyForStaging"] is False


@pytest.fixture
def actual_result(tmp_path):
    image = BytesIO()
    Image.new("RGB", (1308, 1632), "white").save(image, format="PNG")
    binary = image.getvalue()
    sha = hashlib.sha256(binary).hexdigest()
    cards = tmp_path / "cards" / "real-output"
    cards.mkdir(parents=True)
    box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    package = {"request_id": "real-output", "content_type": "news", "menu_work_ids": [1],
               "feed_valid_until": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
               "sources": [{"id": "source", "title": "Source", "url": "https://example.com/news", "role": "primary"}],
               "facts": [{"id": "fact", "statement": "Source text", "source_ids": ["source"]}],
               "detail_body": "Unreviewed source-based body", "detail_fact_ids": ["fact"], "detail_source_ids": ["source"],
               "cards": []}
    for page in range(1, 4):
        (cards / f"card_{page:02d}.png").write_bytes(binary)
        package["cards"].append({"page": page, "role": "cover" if page == 1 else "body", "title_lines": ["Title"],
                                 "body_sentences": ["Body"], "fact_ids": ["fact"], "rendered_image_key": sha,
                                 "layout": {"image_box": box, "copy_box": box, "text_overlay_box": box}})
    result = {"package": package, "status": "rejected_archived", "review_mode": "llm",
              "quality_report": {"total": 20, "hard_blockers": ["IMAGE_VISION_MISMATCH"]},
              "qa_report": [{"severity": "error", "code": "IMAGE_VISION_MISMATCH"}]}
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path, result


def test_local_staging_preserves_failed_qc_without_approval(actual_result, tmp_path):
    path, original = actual_result
    prepared = batch.stage_result(path, tmp_path / "staged")
    assert prepared.reviews == {}
    item = prepared.items[0]
    assert item.raw.package == original["package"]
    assert item.raw.reviewEvidence["quality_report"] == original["quality_report"]
    assert item.raw.reviewEvidence["status"] == "rejected_archived"
    assert item.raw.reviewEvidence["operatorReviewCreated"] is False
    again = batch.stage_result(path, tmp_path / "staged")
    assert again.items[0].digest == item.digest


def test_missing_or_changed_rendered_assets_never_stage(actual_result, tmp_path):
    path, _ = actual_result
    card = tmp_path / "cards" / "real-output" / "card_02.png"
    card.write_bytes(b"changed bytes")
    with pytest.raises(batch.BatchError, match="hash does not match"):
        batch.stage_result(path, tmp_path / "staged")
    assert not (tmp_path / "staged").exists()
