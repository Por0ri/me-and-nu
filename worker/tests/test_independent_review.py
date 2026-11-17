"""Offline registration/export of a reviewed copy preserves rejected generation history."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from menu_worker import jobs, real_generation
from menu_worker.job_artifacts import sha256
from menu_worker.real_generation import RealGenerationError
from menu_worker.review_evidence import read_staging_review, register_staging_review
from menu_worker.staging_export import export_staging_bundle
from test_staging_export import seal_ready_attempt


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Run in the generation image for PNG decoding")
class IndependentReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = temporary / "artifacts"
        self.root.mkdir()
        self.output = temporary / "bundle"
        self.review_file = temporary / "independent-review.json"
        attempt = str(uuid4())
        self.job = {
            "job_id": str(uuid4()), "generation_attempt_id": attempt, "current_attempt_id": attempt,
            "job_kind": "anime_generate", "execution_status": "succeeded", "content_type": "goods",
            "source_id": "one_piece_official", "menu_work_id": 8, "idempotency_key": "independent-review-offline",
            "source_url": "https://one-piece.com/news/83393/index.html",
            "canonical_url": "https://one-piece.com/news/83393/index.html",
        }
        identity = {**real_generation._request(self.job), "job_kind": "anime_generate"}
        self.job["input_sha256"] = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
        _, self.folder, _, self.images = seal_ready_attempt(self.root, self.job)
        # Only this new synthetic fixture is resealed; no existing user artifact
        # or historical seal is edited by registration or export.
        result_file = self.folder / "result.json"
        self.original = json.loads(result_file.read_bytes())
        self.original.update(status="rejected_archived", editorial_decision="rejected")
        self.original["package"]["status"] = "rejected_archived"
        self.original["quality_report"] = {"decision": "reject", "hard_blockers": ["original rejection"]}
        result_file.write_text(json.dumps(self.original), encoding="utf-8")
        (self.folder / "manifest.json").unlink()
        evidence = real_generation.validate_real_artifacts(self.root, self.job)
        self.job.update(evidence)
        self.job["attempts"] = [{
            "attempt_id": attempt, "job_id": self.job["job_id"], "mode": "generation",
            "attempt_no": 1, "execution_status": "succeeded", **evidence,
        }]
        self.original_job = deepcopy(self.job)
        self.original_files = {
            path.relative_to(self.folder): path.read_bytes()
            for path in self.folder.rglob("*") if path.is_file()
        }
        self.review = deepcopy(self.original)
        self.review.update(
            status="review_passed", review_mode="llm", editorial_decision="approved_for_feed",
            quality_report={"decision": "pass_candidate", "hard_blockers": [], "total": 95,
                            "card_scores": {"1": 95, "2": 95}},
            qa_report=[], needs_revision=False,
            usage_records=[{"stage": "review", "model": "offline-test-model", "input_tokens": 120,
                            "output_tokens": 30, "total_tokens": 150, "estimated_cost_usd": 0.0}],
            language_review={
                "content_type_fit": 5,
                "cards": [{
                    "page": page, "korean_naturalness": 5, "translation_naturalness": 5,
                    "reader_clarity": 5, "fan_engagement": 5, "card_flow": 5,
                    "safety_respect": 5, "safety_blocker": False,
                    "image_relevance": 5, "image_composition": 5,
                    "feedback": "Synthetic language review.", "visual_feedback": "Synthetic image review.",
                } for page in (1, 2)],
                "detail_body_review": {"naturalness": 5, "factual_support": 5, "coherence": 5,
                                       "completeness": 5, "feedback": "Synthetic detail review."},
            },
        )
        self.review["package"]["status"] = "review_passed"
        self.write_review(self.review)
        self.rows = {}
        self.store = Mock(spec_set=["get", "register_staging_review", "get_staging_review", "select_staging_result"])
        self.store.get.return_value = self.job

        def register(job, review):
            row = {**deepcopy(review), "job_id": job["job_id"],
                   "generation_attempt_id": job["generation_attempt_id"],
                   "source_result_sha256": job["result_sha256"]}
            previous = self.rows.setdefault(row["review_id"], row)
            self.assertEqual(previous, row)
            return deepcopy(previous)

        self.store.register_staging_review.side_effect = register
        self.store.get_staging_review.side_effect = lambda review_id: deepcopy(self.rows.get(review_id))

    def write_review(self, result):
        self.review_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    def register(self, source_hash=None):
        return register_staging_review(
            self.root, self.store, self.job["job_id"], self.review_file,
            source_hash if source_hash is not None else self.job["result_sha256"],
        )

    def export(self, review_id=None):
        return export_staging_bundle(self.root, self.store, self.job["job_id"], self.output, review_id=review_id)

    def assert_source_unchanged(self):
        self.assertEqual(self.job, self.original_job)
        actual = {path.relative_to(self.folder): path.read_bytes()
                  for path in self.folder.rglob("*") if path.is_file()}
        self.assertEqual(actual, self.original_files)
        self.assertEqual((self.job["outcome"], self.job["producer_status"]), ("rejected", "rejected_archived"))

    def test_register_replay_and_export_preserve_rejected_source_and_exact_package(self):
        row = self.register()
        review_id = row["review_id"]
        envelope = self.root / row["artifact_path"]
        self.assertEqual(envelope, self.root / "reviews" / self.job["job_id"] / f"{review_id}.json")
        sealed = envelope.read_bytes()
        self.assertEqual(sha256(sealed), row["artifact_sha256"])
        self.assertEqual(row["source_result_sha256"], self.job["result_sha256"])
        self.assertEqual(self.register(), row)
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(envelope.read_bytes(), sealed)
        reviewed, saved = read_staging_review(self.root, self.store, self.job, review_id, self.original)
        self.assertEqual(reviewed, self.review)
        self.assertEqual(saved, row)
        self.export(review_id)
        self.store.select_staging_result.assert_called_once_with(self.job, review_id=review_id)
        item, = json.loads((self.output / "bundle.json").read_bytes())["items"]
        self.assertEqual(item["package"], self.original["package"])
        self.assertEqual(item["package"]["status"], "rejected_archived")
        self.assertFalse(item["reviewEvidence"]["operatorReviewCreated"])
        self.assertNotIn("operator_review", item["reviewEvidence"])
        for asset in item["assets"]:
            self.assertEqual((self.output / asset["path"]).read_bytes(), self.images[asset["page"]])
        self.assert_source_unchanged()

    def test_default_export_still_rejects_even_after_approved_review_registration(self):
        self.register()
        with self.assertRaises(RealGenerationError):
            self.export()
        self.store.select_staging_result.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()

    def test_equivalent_job_uuid_spellings_reuse_the_same_review_identity(self):
        row = self.register()
        artifact = self.root / row["artifact_path"]
        sealed = artifact.read_bytes()
        canonical = self.job["job_id"]
        for alias in (canonical.upper(), canonical.replace("-", ""), "{" + canonical + "}"):
            with self.subTest(alias=alias):
                repeated = register_staging_review(
                    self.root, self.store, alias, self.review_file, self.job["result_sha256"]
                )
                self.assertEqual(repeated, row)
                self.store.get.assert_called_with(alias)
                self.assertEqual(artifact.read_bytes(), sealed)
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(list((self.root / "reviews" / canonical).iterdir()), [artifact])
        self.assert_source_unchanged()

    def test_rejects_changed_package_even_when_review_says_approved(self):
        for field, value in (("detail_body", "Changed content."), ("menu_work_ids", [9]),
                             ("request_id", "different-article"), ("feed_valid_until", "2098-01-01T00:00:00Z")):
            with self.subTest(field=field):
                changed = deepcopy(self.review)
                changed["package"][field] = value
                self.write_review(changed)
                with self.assertRaises(RealGenerationError):
                    self.register()
        changed = deepcopy(self.review)
        changed["package"]["cards"][0]["title_lines"] = ["Changed card copy."]
        self.write_review(changed)
        with self.assertRaises(RealGenerationError):
            self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_rejects_source_hash_mismatch_without_registering_or_rewriting(self):
        with self.assertRaises(RealGenerationError):
            self.register("0" * 64)
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_requires_completed_rejected_source_generation(self):
        for change in ({"execution_status": "queued"}, {"execution_status": "failed"},
                       {"outcome": "ready_for_staging"}, {"producer_status": "ready_for_feed"},
                       {"job_kind": "anime"}, {"job_kind": "fake"}):
            with self.subTest(change=change):
                self.store.get.return_value = {**self.job, **change}
                with self.assertRaises(RealGenerationError):
                    self.register()
        self.store.get.return_value = None
        with self.assertRaises(RealGenerationError):
            self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_rejects_non_llm_or_unapproved_review(self):
        for field, value in (("review_mode", "offline_fixture"), ("review_mode", None),
                             ("status", "ready_for_feed"), ("editorial_decision", "rejected")):
            with self.subTest(field=field, value=value):
                changed = deepcopy(self.review)
                changed[field] = value
                self.write_review(changed)
                with self.assertRaises(RealGenerationError):
                    self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_rejects_quality_qa_or_card_safety_blockers(self):
        variants = []
        blocked = deepcopy(self.review)
        blocked["quality_report"]["hard_blockers"] = ["missing evidence"]
        variants.append(blocked)
        blocked = deepcopy(self.review)
        blocked["qa_report"] = [{"severity": "blocker", "message": "Unsafe."}]
        variants.append(blocked)
        blocked = deepcopy(self.review)
        blocked["language_review"]["cards"][0]["safety_blocker"] = True
        variants.append(blocked)
        for index, changed in enumerate(variants):
            with self.subTest(index=index):
                self.write_review(changed)
                with self.assertRaises(RealGenerationError):
                    self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_requires_complete_unique_card_language_image_and_detail_reviews(self):
        variants = []
        changed = deepcopy(self.review)
        changed["language_review"]["cards"].pop()
        variants.append(changed)
        changed = deepcopy(self.review)
        changed["language_review"]["cards"][1]["page"] = 1
        variants.append(changed)
        for field in ("korean_naturalness", "image_relevance", "image_composition", "visual_feedback", "safety_blocker"):
            changed = deepcopy(self.review)
            del changed["language_review"]["cards"][0][field]
            variants.append(changed)
        for score in (0, 6, True, "5"):
            changed = deepcopy(self.review)
            changed["language_review"]["cards"][0]["reader_clarity"] = score
            variants.append(changed)
        changed = deepcopy(self.review)
        del changed["language_review"]["detail_body_review"]["factual_support"]
        variants.append(changed)
        for index, changed in enumerate(variants):
            with self.subTest(index=index):
                self.write_review(changed)
                with self.assertRaises(RealGenerationError):
                    self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_requires_positive_real_review_token_usage(self):
        for usage in ([], [{"stage": "generate", "total_tokens": 100}],
                      *[[{"stage": "review", "input_tokens": value, "output_tokens": value, "total_tokens": value}]
                        for value in (0, -1, None, True, "100")]):
            with self.subTest(usage=usage):
                changed = deepcopy(self.review)
                changed["usage_records"] = usage
                self.write_review(changed)
                with self.assertRaises(RealGenerationError):
                    self.register()
        self.store.register_staging_review.assert_not_called()
        self.assert_source_unchanged()

    def test_unregistered_review_cannot_be_read_or_exported(self):
        review_id = str(uuid4())
        with self.assertRaises(RealGenerationError):
            read_staging_review(self.root, self.store, self.job, review_id, self.original)
        with self.assertRaises(RealGenerationError):
            self.export(review_id)
        self.store.select_staging_result.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()

    def test_corrupt_envelope_is_not_resealed_or_exported(self):
        row = self.register()
        envelope = self.root / row["artifact_path"]
        modified = envelope.read_bytes() + b"\n"
        envelope.write_bytes(modified)
        with self.assertRaises(RealGenerationError):
            read_staging_review(self.root, self.store, self.job, row["review_id"], self.original)
        with self.assertRaises(RealGenerationError):
            self.export(row["review_id"])
        self.assertEqual(envelope.read_bytes(), modified)
        self.assertFalse(self.output.exists())
        self.store.select_staging_result.assert_not_called()
        self.assert_source_unchanged()

    def test_review_row_cannot_be_rebound_to_another_generation(self):
        row = self.register()
        for field, value in (("job_id", str(uuid4())), ("generation_attempt_id", str(uuid4())),
                             ("source_result_sha256", "0" * 64)):
            with self.subTest(field=field):
                self.rows[row["review_id"]] = {**row, field: value}
                with self.assertRaises(RealGenerationError):
                    self.export(row["review_id"])
                self.assertFalse(self.output.exists())
        self.store.select_staging_result.assert_not_called()
        self.assert_source_unchanged()

    def test_reviewed_export_cli_requires_generation_role(self):
        row = self.register()
        with patch.object(jobs, "JobStore", return_value=self.store):
            for role in ("fake", "anime"):
                with self.subTest(role=role):
                    stdout, stderr = StringIO(), StringIO()
                    with (patch.dict(os.environ, {"CONTENT_JOB_KIND": role, "CONTENT_JOB_ARTIFACT_ROOT": str(self.root)}, clear=True),
                          redirect_stdout(stdout), redirect_stderr(stderr)):
                        code = jobs.main(["export-staging", "--job-id", self.job["job_id"],
                                          "--output", str(self.output), "--review-id", row["review_id"]])
                    self.assertNotEqual(code, 0)
                    self.assertIn("generation_profile_required", stderr.getvalue())
                    self.assertFalse(self.output.exists())
        self.store.select_staging_result.assert_not_called()
        self.assert_source_unchanged()

    def invoke_register_cli(self, role="anime_generate", review_file=None):
        stdout, stderr = StringIO(), StringIO()
        with (patch.object(jobs, "JobStore", return_value=self.store),
              patch.dict(os.environ, {"CONTENT_JOB_KIND": role, "CONTENT_JOB_ARTIFACT_ROOT": str(self.root)}, clear=True),
              redirect_stdout(stdout), redirect_stderr(stderr)):
            code = jobs.main([
                "register-staging-review", "--job-id", self.job["job_id"],
                "--review-result", str(review_file or self.review_file),
                "--source-result-sha256", self.job["result_sha256"],
            ])
        return code, json.loads(stderr.getvalue() if code else stdout.getvalue())

    def test_register_cli_accepts_generation_role_and_blocks_fake_and_probe(self):
        for role in ("fake", "anime"):
            with self.subTest(role=role):
                code, report = self.invoke_register_cli(role)
                self.assertNotEqual(code, 0)
                self.assertEqual(report["error"], "generation_profile_required")
                self.store.register_staging_review.assert_not_called()
                self.assertFalse((self.root / "reviews").exists())
        code, report = self.invoke_register_cli()
        self.assertEqual(code, 0, report)
        self.assertEqual(report, self.rows[report["review_id"]])
        self.assertEqual(report["job_id"], self.job["job_id"])
        self.assertEqual(report["source_result_sha256"], self.job["result_sha256"])
        self.assertEqual(self.invoke_register_cli(), (0, report))
        self.assertEqual(len(self.rows), 1)
        self.store.select_staging_result.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()

    def test_register_cli_rejects_duplicate_json_fields_before_creating_evidence(self):
        encoded = json.dumps(self.review, ensure_ascii=False)
        self.review_file.write_text(encoded[:-1] + ', "status": "review_passed"}', encoding="utf-8")
        code, report = self.invoke_register_cli()
        self.assertNotEqual(code, 0, report)
        self.store.register_staging_review.assert_not_called()
        self.store.select_staging_result.assert_not_called()
        self.assertFalse((self.root / "reviews").exists())
        self.assert_source_unchanged()

    def test_register_rejects_symlinked_review_input(self):
        link = self.review_file.with_name("linked-review.json")
        try:
            link.symlink_to(self.review_file)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"Symbolic link creation unavailable: {type(error).__name__}")
        code, report = self.invoke_register_cli(review_file=link)
        self.assertNotEqual(code, 0, report)
        self.assertEqual(report["error"], "unsafe_artifact")
        self.store.register_staging_review.assert_not_called()
        self.assertFalse((self.root / "reviews").exists())
        self.assertEqual(json.loads(self.review_file.read_bytes()), self.review)
        self.assert_source_unchanged()
