"""Validate and publish a completed evaluation for the administrative API.

Run from the repository root, or with this package on PYTHONPATH:
  python -m model.eval.publish_admin_evaluation --input completed.json --output deploy/checking/admin-evaluation/evaluation.json

This accepts rank-level evaluation artifacts, not raw recommendation lists or
rounded historical scores. It does not run models, connect to a DB, or collect
labels. A successful replacement becomes visible on the API's next request.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from model.eval.admin_performance_export import publish_artifact


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON constant")


def publish_evaluation(source, output):
    # On an evaluator PC the schema lives in backend/. In the Checking image
    # app.schemas is already available at /app, without importing DB settings.
    backend = Path(__file__).resolve().parents[2] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.schemas.admin_recommendation_analytics import MAX_ARTIFACT_BYTES

    with Path(source).open("rb") as stream:
        payload = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("evaluation artifact exceeds API size limit")
    data = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    validated = publish_artifact(data, output, mode=0o644)
    canonical = validated.model_dump_json(by_alias=True).encode("utf-8")
    return {
        "evaluationId": validated.evaluation_id,
        "evaluatedAt": validated.evaluated_at.isoformat(),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "modelCount": len(validated.models),
        "cohorts": [{"audience": cohort.audience, "sampleSize": cohort.sample_size}
                    for cohort in validated.cohorts],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = publish_evaluation(args.input, args.output)
    except (OSError, ValueError, RecursionError):
        # ValidationError can include the supplied values. Keep raw ranks and
        # candidate contents out of terminal/deployment logs even on failure.
        print("Evaluation artifact was not published: invalid or unreadable input/output.", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
