"""Read one atomically published evaluation artifact and calculate paired metrics.

The configured path is server-owned, never accepted from a request. No raw case
arrays or filesystem paths leave this provider. Deployments may mount a file
read-only; the offline exporter is the only writer.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from app.schemas.admin_recommendation_analytics import (
    MAX_ARTIFACT_BYTES, AdminRecommendationPerformanceResponse, Audience, Period,
    RecommendationCohortMetadata, RecommendationEvaluationArtifact,
    RecommendationModelScores, RecommendationScore,
)

PERIOD_DAYS = {"7d": 7, "30d": 30, "3m": 90, "6m": 180}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_artifact(path: str) -> RecommendationEvaluationArtifact:
    # Bound the actual read, including a file growing between stat and read.
    with Path(path).open("rb") as stream:
        payload = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("evaluation artifact too large")
    data = json.loads(payload, object_pairs_hook=_unique_object)
    return RecommendationEvaluationArtifact.model_validate(data)


def get_recommendation_performance(
    *, artifact_path: str | None, period: Period = "7d", audience: Audience = "all",
    now: datetime | None = None,
) -> AdminRecommendationPerformanceResponse:
    """``all`` returns separate scenarios, not a pooled or traffic-weighted score.

    Period filters the completed evaluation timestamp, not online activity or
    the historical label window. Use a threadpool from async HTTP handlers.
    """
    if period not in PERIOD_DAYS or audience not in {"all", "existing", "new"}:
        raise ValueError("invalid recommendation analytics query")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    end = end.astimezone(timezone.utc)
    response = AdminRecommendationPerformanceResponse(
        availability="unavailable", audience=audience, period=period,
        period_start=end - timedelta(days=PERIOD_DAYS[period]), period_end=end,
    )
    if not artifact_path:
        response.reason = "not_configured"
        return response
    try:
        artifact = _load_artifact(artifact_path)
    except FileNotFoundError:
        response.reason = "artifact_missing"
        return response
    except (OSError, RuntimeError):
        response.reason = "artifact_unreadable"
        return response
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        response.reason = "artifact_invalid"
        return response
    if not response.period_start <= artifact.evaluated_at < end:
        response.reason = "outside_period"
        return response
    cohorts = [c for c in artifact.cohorts if audience == "all" or c.audience == audience]
    if not cohorts:
        response.reason = "audience_not_evaluated"
        return response
    response.availability = "available"
    response.evaluation_id = artifact.evaluation_id
    response.evaluated_at = artifact.evaluated_at
    response.baseline_model_id = artifact.baseline_model_id
    response.current_model_id = artifact.current_model_id
    response.conditions = artifact.conditions
    response.cohorts = [RecommendationCohortMetadata(
        audience=c.audience, definition=c.definition, case_set_sha256=c.case_set_sha256,
        sample_size=c.sample_size,
    ) for c in cohorts]
    scores = {}
    for cohort in cohorts:
        metrics = {}
        for model in cohort.models:
            ranks = model.target_ranks
            ndcg = math.fsum(1 / math.log2(rank + 1) for rank in ranks if rank is not None) / len(ranks)
            hit = sum(rank is not None for rank in ranks) / len(ranks)
            metrics[model.model_id] = (ndcg, hit)
        base_ndcg, base_hit = metrics[artifact.baseline_model_id]
        for model_id, (ndcg, hit) in metrics.items():
            scores.setdefault(model_id, []).append(RecommendationScore(
                audience=cohort.audience, sample_size=cohort.sample_size,
                ndcg_at10=ndcg, hit_rate_at10=hit,
                ndcg_delta_vs_baseline=ndcg - base_ndcg,
                hit_rate_delta_vs_baseline=hit - base_hit,
            ))
    response.models = [RecommendationModelScores(
        model_id=model.model_id, label=model.label, version=model.version,
        scores=scores[model.model_id],
    ) for model in artifact.models]
    return response
