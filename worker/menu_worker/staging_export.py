"""Export a sealed manual-generation result for the service carousel importer."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from menu_worker.job_store import JobStore
from menu_worker.real_generation import RealGenerationError, read_sealed_staging_result


def export_staging_bundle(root: Path, store: JobStore, job_id: str, output: Path, *, review_id: str | None = None) -> dict:
    job = store.get(job_id)
    if job is None:
        raise RealGenerationError("job_not_found")
    if job["job_kind"] != "anime_generate":
        raise RealGenerationError("unexpected_generation_kind")
    if review_id is None and (job["execution_status"], job["outcome"], job["producer_status"]) != (
        "succeeded", "ready_for_staging", "ready_for_feed"
    ):
        raise RealGenerationError("job_not_ready_for_staging")

    output = output.parent.resolve() / output.name
    if output.is_symlink() or output.exists():
        raise RealGenerationError("staging_output_exists")
    if not output.parent.is_dir() or output.is_relative_to(root.resolve() / "jobs"):
        raise RealGenerationError("invalid_staging_output")

    # A sibling temporary directory keeps partial copies out of the published bundle.
    with TemporaryDirectory(dir=output.parent, prefix=".carousel-export-") as temporary:
        staged = Path(temporary) / "bundle"
        cards = staged / "cards"
        cards.mkdir(parents=True)
        result = read_sealed_staging_result(root, job, cards)
        package = result["package"]
        original_result = result
        review_row = None
        if review_id is not None:
            from menu_worker.review_evidence import read_staging_review
            result, review_row = read_staging_review(root, store, job, review_id, original_result)
        assets = [{"page": card["page"], "path": f"cards/card_{card['page']:02d}.png",
                   "sha256": card["rendered_image_key"]} for card in package["cards"]]
        evidence = {key: result[key] for key in (
            "status", "review_mode", "editorial_decision", "quality_report", "qa_report", "language_review",
            "usage_records", "cost_accounting", "stage_timings", "stage_timings_seconds", "operator_correction",
        ) if key in result}
        evidence.update(jobId=job["job_id"], generationAttemptId=job["generation_attempt_id"],
                        resultSha256=job["result_sha256"], operatorReviewCreated=False)
        if review_row is not None:
            evidence.update(reviewId=review_row["review_id"], reviewResultSha256=review_row["review_result_sha256"],
                            reviewArtifactSha256=review_row["artifact_sha256"],
                            originalGeneration={"status": original_result["status"], "outcome": job["outcome"],
                                                "resultSha256": job["result_sha256"]})
        document = {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime",
                    "items": [{"package": package, "assets": assets, "reviewEvidence": evidence}]}
        (staged / "bundle.json").write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8"
        )
        if review_id is None:
            store.select_staging_result(job)
        else:
            store.select_staging_result(job, review_id=review_id)
        # POSIX rename can replace an empty directory; reserve the name first.
        # Windows rename already refuses any existing destination.
        reserved = False
        try:
            if os.name == "posix":
                output.mkdir()
                reserved = True
            staged.rename(output)
        except OSError:
            if reserved:
                output.rmdir()
                raise
            if output.exists() or output.is_symlink():
                raise RealGenerationError("staging_output_exists") from None
            raise

    return {"job_id": job["job_id"], "generation_attempt_id": job["generation_attempt_id"],
            **({"review_id": review_id} if review_id is not None else {}),
            "result_sha256": job["result_sha256"], "bundle_path": str(output / "bundle.json"),
            "cards": len(assets)}
