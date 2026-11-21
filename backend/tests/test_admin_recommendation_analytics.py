"""Offline metrics are paired by case; missing evidence is never a zero score."""

import copy
import importlib.util
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.admin_recommendation_analytics import RecommendationEvaluationArtifact
from app.services import admin_recommendation_analytics_service as service

NOW = datetime(2026, 11, 21, 3, tzinfo=timezone.utc)


@pytest.fixture
def artifact():
    return {
        "schemaVersion": 1, "evaluationId": "paired-held-out-1", "evaluatedAt": (NOW - timedelta(hours=1)).isoformat(),
        "k": 10, "groundTruthPerCase": 1, "split": "held_out",
        "currentModelId": "v05", "baselineModelId": "popular",
        "conditions": {
            "evaluationData": "isolated test cases", "datasetSha256": "a" * 64,
            "inputConditions": "held-out item never passed to models", "aggregationUnit": "series",
            "commonRules": "same candidate set", "targetPeriod": "2020-01 through 2022-01",
            "groundTruth": "one completed series", "candidatePolicy": "top 10 distinct series",
            "recordSource": "test evaluator", "cohortInterpretation": "input_scenarios",
        },
        "models": [
            {"modelId": "v05", "label": "v0.5", "version": "rev1"},
            {"modelId": "popular", "label": "인기순", "version": "rev1"},
        ],
        "cohorts": [
            {"audience": "existing", "definition": "full history", "caseSetSha256": "b" * 64,
             "sampleSize": 3, "models": [
                 {"modelId": "v05", "targetRanks": [1, 2, None]},
                 {"modelId": "popular", "targetRanks": [10, None, None]},
             ]},
            {"audience": "new", "definition": "onboarding simulation", "caseSetSha256": "b" * 64,
             "sampleSize": 3, "models": [
                 {"modelId": "v05", "targetRanks": [None, None, None]},
                 {"modelId": "popular", "targetRanks": [1, None, None]},
             ]},
        ],
    }


def write_artifact(tmp_path, artifact):
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return str(path)


def test_exact_hit_ndcg_and_same_cohort_baseline(tmp_path, artifact):
    result = service.get_recommendation_performance(artifact_path=write_artifact(tmp_path, artifact), now=NOW)
    assert result.availability == "available"
    existing, new = result.models[0].scores
    assert existing.sample_size == 3
    assert existing.ndcg_at10 == pytest.approx((1 + 1 / math.log2(3)) / 3)
    assert existing.hit_rate_at10 == pytest.approx(2 / 3)
    assert existing.ndcg_delta_vs_baseline == pytest.approx(existing.ndcg_at10 - 1 / math.log2(11) / 3)
    assert existing.hit_rate_delta_vs_baseline == pytest.approx(1 / 3)
    assert new.ndcg_at10 == 0  # available measured zero, not unavailable
    assert new.hit_rate_at10 == 0
    assert new.ndcg_delta_vs_baseline == pytest.approx(-1 / 3)
    # 'all' preserves overlapping scenarios; there is no artificial pooled score.
    assert [row.audience for row in result.cohorts] == ["existing", "new"]
    public = result.model_dump_json(by_alias=True)
    assert "targetRanks" not in public
    assert '"ndcgAt10"' in public and '"hitRateAt10"' in public
    assert str(tmp_path) not in public


@pytest.mark.parametrize("audience", ["existing", "new"])
def test_audience_filters_every_model_consistently(tmp_path, artifact, audience):
    result = service.get_recommendation_performance(
        artifact_path=write_artifact(tmp_path, artifact), audience=audience, now=NOW,
    )
    assert [cohort.audience for cohort in result.cohorts] == [audience]
    assert all([score.audience for score in model.scores] == [audience] for model in result.models)


def test_audience_not_evaluated_is_not_zero(tmp_path, artifact):
    artifact["cohorts"] = artifact["cohorts"][:1]
    response = service.get_recommendation_performance(
        artifact_path=write_artifact(tmp_path, artifact), audience="new", now=NOW,
    )
    assert response.reason == "audience_not_evaluated"
    assert response.models == []


@pytest.mark.parametrize("period,days", [("7d", 7), ("30d", 30), ("3m", 90), ("6m", 180)])
def test_period_filters_completion_in_half_open_utc_window(tmp_path, artifact, period, days):
    for stamp, expected in ((NOW - timedelta(days=days), "available"),
                            (NOW - timedelta(days=days, microseconds=1), "unavailable"),
                            (NOW, "unavailable"), (NOW + timedelta(hours=1), "unavailable")):
        artifact["evaluatedAt"] = stamp.astimezone(timezone(timedelta(hours=9))).isoformat()
        response = service.get_recommendation_performance(
            artifact_path=write_artifact(tmp_path, artifact), period=period, now=NOW,
        )
        assert response.availability == expected
        assert response.time_basis == "evaluation_completed_at"
        assert response.period_start == NOW - timedelta(days=days)


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(schemaVersion=2),
    lambda a: a.update(schemaVersion=True),
    lambda a: a.update(k=10.0),
    lambda a: a.update(k=20),
    lambda a: a.update(groundTruthPerCase=2),
    lambda a: a.update(split="tuning"),
    lambda a: a.update(evaluatedAt="2026-11-20T12:00:00"),
    lambda a: a.update(baselineModelId="missing"),
    lambda a: a["models"].append(copy.deepcopy(a["models"][0])),
    lambda a: a["cohorts"].append(copy.deepcopy(a["cohorts"][0])),
    lambda a: a["cohorts"][0]["models"].append(copy.deepcopy(a["cohorts"][0]["models"][0])),
    lambda a: a["cohorts"][0]["models"][0].update(modelId="unknown"),
    lambda a: a["cohorts"][0].update(sampleSize=4),
    lambda a: a["cohorts"][0]["models"][0].update(targetRanks=[0, 2, None]),
    lambda a: a["cohorts"][0]["models"][0].update(targetRanks=[11, 2, None]),
    lambda a: a["cohorts"][0]["models"][0].update(targetRanks=[1.0, 2, None]),
    lambda a: a["cohorts"][0]["models"][0].update(targetRanks=[True, 2, None]),
    lambda a: a["cohorts"][0]["models"][0].update(targetRanks=[float("nan"), 2, None]),
    lambda a: a["cohorts"][0].update(sampleSize=True),
    lambda a: a["conditions"].update(datasetSha256="unknown"),
    lambda a: a.update(unreviewedExtra={"secret": "not-for-api"}),
])
def test_invalid_or_incomparable_artifact_is_never_partially_shown(tmp_path, artifact, mutate):
    mutate(artifact)
    result = service.get_recommendation_performance(artifact_path=write_artifact(tmp_path, artifact), now=NOW)
    assert result.reason == "artifact_invalid"
    assert result.availability == "unavailable"
    assert result.models == [] and result.conditions is None


def test_missing_unreadable_duplicate_keys_and_bounded_input(tmp_path, monkeypatch):
    assert service.get_recommendation_performance(artifact_path=None, now=NOW).reason == "not_configured"
    assert service.get_recommendation_performance(artifact_path=str(tmp_path / "missing"), now=NOW).reason == "artifact_missing"
    assert service.get_recommendation_performance(artifact_path=str(tmp_path), now=NOW).reason == "artifact_unreadable"
    path = tmp_path / "duplicate.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
    assert service.get_recommendation_performance(artifact_path=str(path), now=NOW).reason == "artifact_invalid"
    monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", 4)
    path.write_bytes(b" " * 5)
    assert service.get_recommendation_performance(artifact_path=str(path), now=NOW).reason == "artifact_invalid"


def _exporter():
    path = Path(__file__).resolve().parents[2] / "model" / "eval" / "admin_performance_export.py"
    spec = importlib.util.spec_from_file_location("admin_performance_export_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exporter_paired_series_ranks_and_target_never_given_to_model():
    exporter = _exporter()
    def current(user, ctx):
        assert "target" not in user and "user_id" not in user
        return [999, 20, 10]
    def baseline(user, ctx):
        return [30]
    def pick_top(ranked, user, ctx, one_per_series):
        assert one_per_series is True
        return list(ranked)[:10]
    result = exporter.collect_cohort(
        audience="existing", definition="test", cases=[
            {"user_id": "private-id", "target": 11, "t": 100, "history": "input"},
            {"user_id": "private-id2", "target": 30, "t": 101, "history": "input"},
        ], ctx={"series": {10: 1, 11: 1}},
        models=[("v05", "v05", current), ("popular", "popular", baseline)],
        candidates={10, 20, 30}, pick_top=pick_top,
    )
    assert result["models"][0]["targetRanks"] == [2, None]
    assert result["models"][1]["targetRanks"] == [None, 1]
    assert "private-id" not in json.dumps(result)
    assert result["sampleSize"] == 2


def test_publisher_keeps_last_valid_artifact_on_validation_failure(tmp_path, artifact):
    exporter = _exporter()
    output = tmp_path / "evaluation.json"
    exporter.publish_artifact(artifact, output)
    before = output.read_bytes()
    artifact["k"] = 5
    with pytest.raises(ValidationError):
        exporter.publish_artifact(artifact, output)
    assert output.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    validated = RecommendationEvaluationArtifact.model_validate_json(before)
    assert validated.evaluation_id == "paired-held-out-1"


def test_exporter_real_pick_top_filters_and_series_ground_truth():
    # Reuse the real shared ranking filter, with tiny input rather than MAL data.
    pytest.importorskip("numpy", reason="real model filter integration needs model/requirements.txt")
    import sys
    from types import SimpleNamespace
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from model.algo import anime_ranker as ranker
    exporter = _exporter()
    target_time = ranker.to_seconds(datetime(2021, 1, 1))
    history = SimpleNamespace(anime=SimpleNamespace(tolist=lambda: [1]))
    cases = [{"user_id": "not-exported", "history": history, "t": target_time, "target": 51}]
    ctx = {"start": {3: 20220101}, "adult": {2}, "series": {4: 40, 41: 40, 5: 50, 51: 50}}
    def model(user, context):
        assert set(user) == {"history", "t"}
        # Seen, adult, future, outside candidates, duplicate series, target series.
        return [1, 2, 3, 999, 4, 41, 5]
    cohort = exporter.collect_cohort(
        audience="existing", definition="real filter", cases=cases, ctx=ctx,
        models=[("v05", "v05", model), ("popular", "popular", lambda user, context: [6])],
        candidates={1, 2, 3, 4, 41, 5, 6}, pick_top=ranker.pick_top,
    )
    assert cohort["models"][0]["targetRanks"] == [2]
    assert cohort["models"][1]["targetRanks"] == [None]


def test_source_fingerprint_tracks_bytes_without_serializing_paths(tmp_path):
    exporter = _exporter()
    path = tmp_path / "source-data"
    path.write_bytes(b"first source")
    original = exporter._digest_paths([("interactions", path)])
    path.write_bytes(b"changed source")
    assert original != exporter._digest_paths([("interactions", path)])
    assert len(original) == 64
