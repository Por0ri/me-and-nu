"""Exercise the operator export CLI using a sealed attempt and a job ledger double."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from menu_worker import jobs, real_generation
from menu_worker.job_artifacts import artifact_directory, sha256


def seal_ready_attempt(root, job):
    """Build the same reviewed two-card result for unit and PostgreSQL CLI tests."""
    from PIL import Image

    folder = artifact_directory(root, job, create=True)
    request_id = "crawl-" + sha256(job["canonical_url"].encode())[:14]
    card_dir = folder / "cards" / request_id
    card_dir.mkdir(parents=True)
    cards = []
    images = {}
    box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    for page in (1, 2):
        image = BytesIO()
        Image.new("L", (1308, 1632), color=page).save(image, "PNG")
        images[page] = image.getvalue()
        (card_dir / f"card_{page:02d}.png").write_bytes(images[page])
        cards.append({"page": page, "rendered_image_key": sha256(images[page]),
                      "role": "cover" if page == 1 else "ending", "title_lines": [f"Test page {page}"],
                      "layout": {"image_box": box, "copy_box": box, "text_overlay_box": box}, "fact_ids": ["f1"]})
    source_id = job["source_id"]
    package = {
        "request_id": request_id, "content_type": "goods", "menu_work_ids": [8], "cards": cards,
        "detail_body": "Synthetic source-backed detail.", "feed_valid_until": "2099-01-01T00:00:00Z",
        "sources": [{"id": source_id, "title": "Official source", "url": job["canonical_url"]}],
        "facts": [{"id": "f1", "statement": "Test fact.", "source_ids": [source_id]}],
        "detail_fact_ids": ["f1"], "detail_source_ids": [source_id],
    }
    result = {
        "package": package, "expected_work_request": real_generation._request(job),
        "status": "ready_for_feed", "review_mode": "llm", "editorial_decision": "approved_for_feed",
        "quality_report": {"decision": "pass_candidate", "hard_blockers": []}, "qa_report": [],
        "language_review": {"tone": "pass"}, "usage_records": [{"stage": "review", "total_tokens": 0}],
    }
    (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (folder / "launch.json").write_text(json.dumps(real_generation._identity(job)), encoding="utf-8")
    (folder / "exit.json").write_text('{"returncode": 0}', encoding="utf-8")
    evidence = real_generation.validate_real_artifacts(root, job)
    return evidence, folder, package, images


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Run in the generation image for PNG decoding")
class StagingExportTests(unittest.TestCase):
    def setUp(self):
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = temporary / "artifacts"
        self.root.mkdir()
        self.output = temporary / "bundle"
        attempt = str(uuid4())
        self.job = {
            "job_id": str(uuid4()), "generation_attempt_id": attempt, "current_attempt_id": attempt,
            "job_kind": "anime_generate", "execution_status": "succeeded", "content_type": "goods",
            "source_id": "one_piece_official", "menu_work_id": 8, "idempotency_key": "export-offline",
            "source_url": "https://one-piece.com/news/82812/index.html",
            "canonical_url": "https://one-piece.com/news/82812/index.html",
        }
        identity = {**real_generation._request(self.job), "job_kind": "anime_generate"}
        self.job["input_sha256"] = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
        evidence, self.folder, self.package, self.images = seal_ready_attempt(self.root, self.job)
        self.job.update(evidence)
        self.job["attempts"] = [{"attempt_id": attempt, "job_id": self.job["job_id"], "mode": "generation",
                                 "attempt_no": 1, "execution_status": "succeeded", **evidence}]
        self.store = Mock(spec_set=["get", "select_staging_result"])
        self.store.get.return_value = self.job
        self.enterContext(patch.object(jobs, "JobStore", return_value=self.store))

    def invoke(self, role="anime_generate"):
        stdout, stderr = StringIO(), StringIO()
        with (patch.dict(os.environ, {"CONTENT_JOB_ARTIFACT_ROOT": str(self.root), "CONTENT_JOB_KIND": role}, clear=True),
              redirect_stdout(stdout), redirect_stderr(stderr)):
            code = jobs.main(["export-staging", "--job-id", self.job["job_id"], "--output", str(self.output)])
        return code, json.loads(stderr.getvalue() if code else stdout.getvalue())

    def test_exports_exact_package_and_images_with_original_attempt_identity(self):
        manifest = self.folder / "manifest.json"
        sealed = manifest.read_bytes()
        code, report = self.invoke()
        self.assertEqual(code, 0, report)
        self.store.get.assert_called_once_with(self.job["job_id"])
        self.store.select_staging_result.assert_called_once_with(self.job)
        document = json.loads((self.output / "bundle.json").read_bytes())
        self.assertEqual((document["schemaVersion"], document["producer"], document["topicCode"]),
                         (1, "ani_menu_agent", "anime"))
        item, = document["items"]
        self.assertEqual(item["package"], self.package)
        self.assertEqual([asset["page"] for asset in item["assets"]], [1, 2])
        for asset in item["assets"]:
            data = (self.output / asset["path"]).read_bytes()
            self.assertEqual(data, self.images[asset["page"]])
            self.assertEqual(asset["sha256"], sha256(data))
        evidence = item["reviewEvidence"]
        self.assertEqual(evidence["jobId"], self.job["job_id"])
        self.assertEqual(evidence["generationAttemptId"], self.job["generation_attempt_id"])
        self.assertEqual(evidence["resultSha256"], sha256((self.folder / "result.json").read_bytes()))
        self.assertIs(evidence["operatorReviewCreated"], False)
        self.assertNotIn("local_test_preview", json.dumps(document))
        self.assertNotIn("operator_review", evidence)
        self.assertEqual(manifest.read_bytes(), sealed)
        files = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(files, {"bundle.json", *(asset["path"] for asset in item["assets"])})

    def test_selected_result_can_retry_after_bundle_publication_fails(self):
        def publication_failure(*args):
            self.store.select_staging_result.assert_called_once_with(self.job)
            raise OSError("simulated rename failure")

        with patch.object(Path, "rename", side_effect=publication_failure):
            self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.invoke()[0], 0)
        self.assertTrue((self.output / "bundle.json").is_file())

    def test_completed_recovery_exports_the_original_generation_attempt(self):
        first, = self.job["attempts"]
        recovery_id = str(uuid4())
        self.job["current_attempt_id"] = recovery_id
        self.job["attempts"] = [
            {**first, "execution_status": "needs_attention", "outcome": None},
            {**first, "mode": "recovery", "attempt_id": recovery_id, "attempt_no": 2},
        ]
        code, report = self.invoke()
        self.assertEqual(code, 0, report)
        item, = json.loads((self.output / "bundle.json").read_bytes())["items"]
        self.assertEqual(item["reviewEvidence"]["generationAttemptId"], first["attempt_id"])

    def test_only_completed_generation_is_exported(self):
        for change in ({"execution_status": "queued"}, {"execution_status": "failed"}, {"outcome": "held"},
                       {"outcome": "skipped"}, {"outcome": "rejected"}, {"job_kind": "fake"}, {"job_kind": "anime"},
                       {"producer_status": "rejected_archived"}, {"result_sha256": "0" * 64},
                       {"artifact_path": "other/manifest.json"}, {"generation_attempt_id": str(uuid4())}):
            with self.subTest(change=change):
                self.store.get.return_value = {**self.job, **change}
                self.assertNotEqual(self.invoke()[0], 0)
                self.assertFalse(self.output.exists())
        self.store.get.return_value = None
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(self.output.exists())

    def test_refuses_unsealed_or_changed_source_without_regenerating_it(self):
        manifest = self.folder / "manifest.json"
        sealed = manifest.read_bytes()
        manifest.unlink()
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(manifest.exists())
        self.assertFalse(self.output.exists())
        original = json.loads(sealed)
        for field, value in (("job_id", str(uuid4())), ("generation_attempt_id", str(uuid4())),
                             ("outcome", "held"), ("input_sha256", "0" * 64)):
            with self.subTest(field=field):
                modified = {**original, field: value}
                manifest.write_text(json.dumps(modified), encoding="utf-8")
                self.assertNotEqual(self.invoke()[0], 0)
                self.assertEqual(json.loads(manifest.read_bytes()), modified)
                self.assertFalse(self.output.exists())
        manifest.write_bytes(sealed)
        result = self.folder / "result.json"
        result.write_bytes(result.read_bytes() + b"\n")
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(self.output.exists())
        result.write_bytes(result.read_bytes()[:-1])
        card = self.folder / "cards" / self.package["request_id"] / "card_02.png"
        card.write_bytes(self.images[1])
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(self.output.exists())

    def test_rejects_other_roles_and_does_not_overwrite_an_existing_directory(self):
        for role in ("anime", "fake"):
            with self.subTest(role=role):
                self.assertNotEqual(self.invoke(role)[0], 0)
                self.assertFalse(self.output.exists())
        self.output.mkdir()
        existing = self.output / "bundle.json"
        existing.write_bytes(b"existing file")
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(existing.read_bytes(), b"existing file")
        self.assertEqual(list(self.output.iterdir()), [existing])
