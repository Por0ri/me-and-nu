"""Bind a previously performed independent review to an unchanged rejected result.

This operator-only path consumes evidence; it never invokes the agent or a model.
An LLM approval is producer evidence, not an operator publication approval.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid5

from menu_worker.job_artifacts import artifact_directory, sha256
from menu_worker.real_generation import (
    MAX_JSON, RealGenerationError, _bytes, _check_cards, _dump, _json, _request,
    _result, read_sealed_staging_result,
)

REVIEW_NAMESPACE = UUID("cd30f6cb-fb03-4c3c-a972-d1322868ce87")


def _eligible(job: dict) -> None:
    if (job.get("job_kind"), job.get("execution_status"), job.get("outcome"), job.get("producer_status")) != (
        "anime_generate", "succeeded", "rejected", "rejected_archived"
    ):
        raise RealGenerationError("job_not_independently_reviewable")


def _score(value) -> bool:
    return type(value) is int and 1 <= value <= 5


def _validate_review(review: dict, original: dict, job: dict) -> None:
    if review.get("status") != "review_passed" or review.get("needs_revision") is not False:
        raise RealGenerationError("independent_review_not_approved")
    package = review.get("package")
    if not isinstance(package, dict) or package.get("status") != "review_passed":
        raise RealGenerationError("independent_review_not_approved")
    # The review operation changes its bookkeeping status, never generated content.
    if ({k: v for k, v in package.items() if k != "status"}
            != {k: v for k, v in original["package"].items() if k != "status"}):
        raise RealGenerationError("reviewed_package_changed")
    # Reuse the normal approval/source/work/test-data checks without promoting the
    # original ledger or persisting this validation-only disposition.
    _result({**review, "status": "ready_for_feed", "expected_work_request": _request(job)}, job)
    language = review["language_review"]
    cards = language.get("cards")
    fields = ("korean_naturalness", "translation_naturalness", "reader_clarity", "fan_engagement",
              "card_flow", "safety_respect", "image_relevance", "image_composition")
    if (not isinstance(cards, list) or len(cards) != len(package["cards"])
            or not _score(language.get("content_type_fit"))):
        raise RealGenerationError("independent_review_incomplete")
    for page, card in enumerate(cards, 1):
        if (not isinstance(card, dict) or type(card.get("page")) is not int or card["page"] != page
                or any(not _score(card.get(field)) for field in fields)
                or card.get("safety_blocker") is not False
                or not isinstance(card.get("visual_feedback"), str) or not card["visual_feedback"].strip()):
            raise RealGenerationError("independent_review_incomplete")
    detail = language.get("detail_body_review")
    if (not isinstance(detail, dict)
            or any(not _score(detail.get(k)) for k in ("naturalness", "factual_support", "coherence", "completeness"))):
        raise RealGenerationError("independent_review_incomplete")
    if not any(u.get("stage") == "review" and isinstance(u.get("model"), str) and u["model"].strip()
               and type(u.get("total_tokens")) is int and u["total_tokens"] > 0
               for u in review["usage_records"] if isinstance(u, dict)):
        raise RealGenerationError("independent_review_usage_missing")


def _review_path(root: Path, job: dict, review_id: str, *, create=False) -> Path:
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise RealGenerationError("unsafe_review_artifact")
    directory = root
    for part in ("reviews", str(UUID(str(job["job_id"])))):
        directory = directory / part
        if directory.is_symlink():
            raise RealGenerationError("unsafe_review_artifact")
        if create:
            directory.mkdir(exist_ok=True)
        if not directory.is_dir() or directory.resolve() != directory:
            raise RealGenerationError("unsafe_review_artifact")
    path = directory / f"{UUID(str(review_id))}.json"
    if path.is_symlink():
        raise RealGenerationError("unsafe_review_artifact")
    return path


def _write_once(path: Path, data: bytes) -> None:
    # A hard link publishes a fully fsynced file without replacing another writer's
    # evidence. A crash before DB registration leaves only a replayable orphan.
    from uuid import uuid4
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _bytes(path, path.parent, MAX_JSON) != data:
                raise RealGenerationError("review_artifact_conflict") from None
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def register_staging_review(root, store, job_id, review_result_path, source_result_sha256) -> dict:
    job = store.get(job_id)
    if job is None:
        raise RealGenerationError("job_not_found")
    _eligible(job)
    if source_result_sha256 != job["result_sha256"]:
        raise RealGenerationError("review_source_digest_mismatch")
    original = read_sealed_staging_result(root, job)
    _check_cards(artifact_directory(root, job), original["package"], required=True)
    # Deliberately do not resolve symlinks before the reader validates the path.
    path = Path(os.path.abspath(review_result_path))
    review, _ = _json(path, path.parent, MAX_JSON)
    _validate_review(review, original, job)
    # Canonical JSON identity ignores whitespace but includes all review evidence.
    digest = sha256(_dump(review))
    identity = f"{job['job_id']}:{job['generation_attempt_id']}:{job['result_sha256']}:{digest}"
    review_id = str(uuid5(REVIEW_NAMESPACE, identity))
    envelope = {
        "schema_version": 1, "kind": "independent_llm_review", "review_id": review_id,
        "job_id": job["job_id"], "generation_attempt_id": job["generation_attempt_id"],
        "source_result_sha256": job["result_sha256"], "review_result_sha256": digest,
        "review_result": review,
    }
    sealed = _dump(envelope)
    if len(sealed) > MAX_JSON:
        raise RealGenerationError("artifact_too_large")
    artifact = _review_path(root, job, review_id, create=True)
    _write_once(artifact, sealed)
    return store.register_staging_review(job, {
        "review_id": review_id, "review_result_sha256": digest,
        "artifact_path": artifact.relative_to(root).as_posix(), "artifact_sha256": sha256(sealed),
    })


def read_staging_review(root, store, job, review_id, original_result) -> tuple[dict, dict]:
    _eligible(job)
    row = store.get_staging_review(review_id)
    if row is None:
        raise RealGenerationError("staging_review_not_found")
    expected = {"review_id": str(UUID(str(review_id))), "job_id": job["job_id"],
                "generation_attempt_id": job["generation_attempt_id"],
                "source_result_sha256": job["result_sha256"]}
    if any(row.get(k) != v for k, v in expected.items()):
        raise RealGenerationError("staging_review_mismatch")
    artifact = _review_path(root, job, review_id)
    if row.get("artifact_path") != artifact.relative_to(root).as_posix():
        raise RealGenerationError("staging_review_mismatch")
    envelope, raw = _json(artifact, root, MAX_JSON)
    if (sha256(raw) != row.get("artifact_sha256") or envelope.get("schema_version") != 1
            or envelope.get("kind") != "independent_llm_review"
            or any(envelope.get(k) != v for k, v in expected.items())
            or envelope.get("review_result_sha256") != row.get("review_result_sha256")
            or not isinstance(envelope.get("review_result"), dict)
            or sha256(_dump(envelope["review_result"])) != row.get("review_result_sha256")):
        raise RealGenerationError("staging_review_mismatch")
    review = envelope["review_result"]
    _validate_review(review, original_result, job)
    _check_cards(artifact_directory(root, job), original_result["package"], required=True)
    return review, row
