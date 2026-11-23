"""Intake validates completed artifacts and preserves the previous result."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from app.schemas import admin_recommendation_analytics as schema
from app.services.admin_recommendation_analytics_service import get_recommendation_performance
from test_admin_recommendation_analytics import NOW, artifact

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from model.eval import admin_performance_export as exporter
from model.eval import publish_admin_evaluation as publisher


def write_input(tmp_path, artifact):
    source = tmp_path / "completed.json"
    source.write_text(json.dumps(artifact), encoding="utf-8")
    return source


def test_publish_is_api_readable_and_next_request_uses_replacement(tmp_path, artifact):
    source = write_input(tmp_path, artifact)
    output = tmp_path / "published" / "evaluation.json"
    receipt = publisher.publish_evaluation(source, output)
    assert receipt["evaluationId"] == artifact["evaluationId"]
    assert receipt["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert receipt["modelCount"] == 2
    assert receipt["cohorts"] == [{"audience": "existing", "sampleSize": 3}, {"audience": "new", "sampleSize": 3}]
    assert "targetRanks" not in json.dumps(receipt)
    assert str(tmp_path) not in json.dumps(receipt)
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o644
    first = get_recommendation_performance(artifact_path=str(output), now=NOW)
    assert first.availability == "available"
    assert first.models[0].scores[0].hit_rate_at10 == pytest.approx(2 / 3)

    artifact["evaluationId"] = "next-completed-evaluation"
    artifact["cohorts"][0]["models"][0]["targetRanks"] = [1, 1, 1]
    source = write_input(tmp_path, artifact)
    second_receipt = publisher.publish_evaluation(source, output)
    second = get_recommendation_performance(artifact_path=str(output), now=NOW)
    assert second.evaluation_id == "next-completed-evaluation"
    assert second.models[0].scores[0].ndcg_at10 == 1
    assert second.models[0].scores[0].hit_rate_at10 == 1
    assert second_receipt["sha256"] != receipt["sha256"]
    assert not list(output.parent.glob(".admin-evaluation-*.tmp"))


@pytest.mark.parametrize("bad_payload", [
    b"not JSON",
    b'{"schemaVersion":1,"schemaVersion":1}',
    b'{"nested":{"value":1,"value":2}}',
    b'{"value":NaN}',
    b'{"value":Infinity}',
])
def test_invalid_json_preserves_last_result(tmp_path, artifact, bad_payload):
    source = write_input(tmp_path, artifact)
    output = tmp_path / "evaluation.json"
    publisher.publish_evaluation(source, output)
    before = output.read_bytes()
    source.write_bytes(bad_payload)
    with pytest.raises(ValueError):
        publisher.publish_evaluation(source, output)
    assert output.read_bytes() == before
    assert not list(tmp_path.glob(".admin-evaluation-*.tmp"))


def test_invalid_schema_and_size_preserve_last_result(tmp_path, artifact, monkeypatch):
    source = write_input(tmp_path, artifact)
    output = tmp_path / "evaluation.json"
    publisher.publish_evaluation(source, output)
    before = output.read_bytes()
    artifact["cohorts"][0]["models"][0]["targetRanks"] = [True, 1, 1]
    write_input(tmp_path, artifact)
    with pytest.raises(ValueError):
        publisher.publish_evaluation(source, output)
    assert output.read_bytes() == before
    monkeypatch.setattr(schema, "MAX_ARTIFACT_BYTES", 8)
    source.write_bytes(b" " * 9)
    with pytest.raises(ValueError, match="size limit"):
        publisher.publish_evaluation(source, output)
    assert output.read_bytes() == before


def test_permissions_are_set_before_atomic_replace_and_failure_preserves_last(tmp_path, artifact, monkeypatch):
    source = write_input(tmp_path, artifact)
    output = tmp_path / "evaluation.json"
    publisher.publish_evaluation(source, output)
    before = output.read_bytes()
    chmod, replace = exporter.os.chmod, exporter.os.replace
    events = []

    def set_mode(path, mode):
        events.append(("mode", mode))
        chmod(path, mode)

    def atomic_replace(source_path, target):
        assert events == [("mode", 0o644)]
        assert Path(source_path).parent == Path(target).parent
        events.append(("replace", None))
        replace(source_path, target)

    monkeypatch.setattr(exporter.os, "chmod", set_mode)
    monkeypatch.setattr(exporter.os, "replace", atomic_replace)
    publisher.publish_evaluation(source, output)
    assert events == [("mode", 0o644), ("replace", None)]

    def fail_chmod(path, mode):
        raise PermissionError("synthetic permission failure")

    monkeypatch.setattr(exporter.os, "chmod", fail_chmod)
    with pytest.raises(PermissionError):
        publisher.publish_evaluation(source, output)
    assert output.read_bytes() == before
    assert not list(tmp_path.glob(".admin-evaluation-*.tmp"))


def test_cli_publishes_without_db_or_model_runtime_and_redacts_failures(tmp_path, artifact):
    source = write_input(tmp_path, artifact)
    output = tmp_path / "public-evaluation.json"
    code = """
import importlib.abc, sys
class NoRuntimeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.services", "app.core.config", "model.algo", "numpy"}:
            raise AssertionError("publisher imported runtime dependencies")
sys.meta_path.insert(0, NoRuntimeImports())
from model.eval.publish_admin_evaluation import main
raise SystemExit(main(sys.argv[1:]))
"""
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"DATABASE_URL", "PYTHONPATH"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, "-c", code, "--input", str(source), "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["evaluationId"] == artifact["evaluationId"]
    before = output.read_bytes()
    artifact["private-unknown-field"] = "DO_NOT_PRINT_ME"
    write_input(tmp_path, artifact)
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DO_NOT_PRINT_ME" not in result.stderr
    assert "Evaluation artifact was not published" in result.stderr
    assert output.read_bytes() == before
