"""Publish a paired, held-out evaluation for the administrative read API.

Run from the repository root with backend and model dependencies installed:
  python -m model.eval.admin_performance_export --evaluation-id mal-20261121 --output /data/admin-evaluation.json

Uses next_anime_test's data preparation and pick_top's scoring rules. This is
a new evaluation, not a parser for rounded historical EXPERIMENTS.md results.
The same held-out people are simulated twice (full history / onboarding).
Those scenarios must not be pooled as disjoint live user populations.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone


def _digest_paths(paths):
    digest = hashlib.sha256()
    for name, path in paths:
        digest.update(name.encode("utf-8") + b"\0")
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def collect_cohort(*, audience, definition, cases, ctx, models, candidates, pick_top):
    """Record aligned ranks, without serializing account ids or watch histories."""
    ranks = {model_id: [] for model_id, _, fn in models}
    fingerprint = hashlib.sha256()
    for case in cases:
        # The digest ties alignment to the original case, not to a model's hits.
        fingerprint.update(json.dumps(
            [str(case["user_id"]), int(case["target"]), int(case["t"])],
            separators=(",", ":"),
        ).encode("utf-8") + b"\n")
        user = {"history": case["history"], "t": case["t"]}
        user.update({key: case[key] for key in ("genres", "themes") if key in case})
        series = ctx["series"]
        target = series.get(case["target"], case["target"])
        for model_id, _, model in models:
            ranked = (item for item in model(user, ctx) if item in candidates)
            recs = pick_top(ranked, user, ctx, one_per_series=True)
            rec_series = [series.get(item, item) for item in recs[:10]]
            ranks[model_id].append(rec_series.index(target) + 1 if target in rec_series else None)
    return {
        "audience": audience, "definition": definition,
        "caseSetSha256": fingerprint.hexdigest(), "sampleSize": len(cases),
        "models": [{"modelId": model_id, "targetRanks": ranks[model_id]} for model_id, _, _ in models],
    }


def publish_artifact(artifact, output, *, mode=0o600):
    """Validate first; replace within the target directory only when complete."""
    backend = Path(__file__).resolve().parents[2] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.schemas.admin_recommendation_analytics import MAX_ARTIFACT_BYTES, RecommendationEvaluationArtifact

    validated = RecommendationEvaluationArtifact.model_validate(artifact)
    payload = validated.model_dump_json(by_alias=True).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("evaluation artifact exceeds API size limit")
    if mode not in (0o600, 0o644):
        raise ValueError("evaluation artifact mode must be private or API-readable")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".admin-evaluation-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Set access before replacement: the API must never observe a new file
        # that it cannot read. Offline exports remain private unless requested.
        os.chmod(temp_path, mode)
        os.replace(temp_path, output)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return validated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    from model.algo import anime_ranker as ranker
    from model.eval import next_anime_test as evaluation

    if ranker.K != 10:
        raise ValueError("this exporter requires K=10")
    if not evaluation.ANILIST.is_file():
        raise ValueError("AniList feature data is required to label the tag model correctly")
    source_csv = evaluation.DATA / "interactions.csv"
    source_cache = evaluation.DATA / "interactions.npz"
    # Match load_interactions' source selection before it may create a cache.
    interaction_source = source_cache if source_cache.exists() and (
        not source_csv.exists() or source_cache.stat().st_mtime >= source_csv.stat().st_mtime
    ) else source_csv
    source_paths = [
        ("anime", evaluation.DATA / "anime.csv"),
        ("relations", evaluation.DATA / "anime_anime.csv"),
        ("interactions", interaction_source),
        ("anilist", evaluation.ANILIST),
    ]
    if evaluation.DETAIL.exists():
        source_paths.append(("people", evaluation.DETAIL))
    dataset_digest = _digest_paths(source_paths)
    root = Path(__file__).resolve().parents[2]
    code_paths = [(name, root / name) for name in (
        "model/algo/anime_ranker.py", "model/algo/series.py",
        "model/eval/next_anime_test.py", "model/eval/admin_performance_export.py",
    )]
    code_digest = _digest_paths(code_paths)
    version = "sha256:" + code_digest
    anime = evaluation.load_anime()
    data = evaluation.load_interactions()
    all_cases = evaluation.make_cases(data)
    held_out = all_cases[evaluation.TUNE_USERS:]
    if not held_out:
        raise ValueError("no held-out cases after the tuning split")
    anilist = evaluation.load_anilist()
    if not anilist:
        raise ValueError("AniList feature data must contain matched works")
    stats = evaluation.stats_histories(data, all_cases)
    ctx = evaluation.build_context(all_cases, anime, evaluation.load_series(anime), anilist, data=data, stats=stats)
    ranker.build_cowatch(ctx, stats)
    ranker.build_ease(ctx)
    # A single explicit candidate universe for all four comparison models.
    ctx["cand_rows"] = ctx["cw_rows"].copy()
    candidates = set(ctx["ids"][ctx["cw_rows"]].tolist())
    models = [
        ("v05", "v0.5", ranker.next_auto),
        ("ease", "EASE 단독", lambda user, context: ranker.cowatch(user, context, kind="ease")),
        ("tag", "태그 취향", ranker.genre),
        ("popular", "인기순", ranker.popular),
    ]
    cohorts = [collect_cohort(
        audience="existing", definition="동일 실전 표본의 정답 이전 전체 시청 기록 입력 장면; 가입 일수 기준이 아님",
        cases=held_out, ctx=ctx, models=models, candidates=candidates, pick_top=ranker.pick_top,
    ), collect_cohort(
        audience="new", definition="동일 실전 표본의 작품 1개 + 장르 최대 3개 + 소재 최대 3개 입력 장면; 학습에 없던 사용자 집단이 아님",
        cases=[evaluation.onboarding(case, anime, 1, 3, anilist, 3) for case in held_out],
        ctx=ctx, models=models, candidates=candidates, pick_top=ranker.pick_top,
    )]
    # Do not publish a run under a fingerprint from files changed during evaluation.
    if dataset_digest != _digest_paths(source_paths) or code_digest != _digest_paths(code_paths):
        raise ValueError("evaluation inputs changed during the run; output was not published")
    artifact = {
        "schemaVersion": 1, "evaluationId": args.evaluation_id,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
        "k": 10, "groundTruthPerCase": 1, "split": "held_out",
        "baselineModelId": "popular", "currentModelId": "v05",
        "conditions": {
            "evaluationData": f"MAL 시청 기록 표본 + AniList 장르·소재 특징, 앞 {evaluation.TUNE_USERS:,}명 튜닝 제외",
            "datasetSha256": dataset_digest,
            "inputConditions": (
                "모든 모델에 동일한 정답 이전 기록/온보딩 칩; 정답은 모델 입력에서 제외. "
                "공통 학습 통계에는 평가 사용자 자신의 정답 이전 전체 기록도 포함된다. "
                "신규 장면은 입력 축소 모사이며 완전 미관측 사용자 평가가 아니고, 전 사용자 공통 시간 컷오프 평가도 아니다"
            ),
            "aggregationUnit": "series",
            "commonRules": "이미 본 작품·성인·정답 시점 이후 방영 제외; 시리즈당 1개; 정답이 후보 밖이어도 표본에 포함",
            "targetPeriod": (
                f"{datetime.fromtimestamp(evaluation.TARGET_AFTER, timezone.utc).date()} 이상 "
                f"{datetime.fromtimestamp(evaluation.TARGET_BEFORE, timezone.utc).date()} 미만 (MAL 평가 설정)"
            ),
            "groundTruth": "유저별 마지막 completed 작품 1개와 동일 시리즈; 동시 완료 정답은 제외",
            "candidatePolicy": f"모든 모델 공통: 같이 본 기록의 인기 상위 {len(candidates):,}개 후보, 추천 최대 10개",
            "recordSource": (
                "model.eval.admin_performance_export / next_anime_test / anime_ranker; 코드 해시는 model.version. "
                "currentModelId는 이 평가의 기준 조합 모델이며 배포된 서비스 모델/학습 자료와의 일치를 보증하지 않음"
            ),
            "cohortInterpretation": "input_scenarios",
        },
        "models": [{"modelId": model_id, "label": label, "version": version} for model_id, label, _ in models],
        "cohorts": cohorts,
    }
    publish_artifact(artifact, args.output)
    print(f"Published evaluation {args.evaluation_id}: {len(held_out)} held-out cases per scenario")


if __name__ == "__main__":
    main()
