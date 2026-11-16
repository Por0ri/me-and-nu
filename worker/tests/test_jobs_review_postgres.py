"""Independent-review ledger tests in a database created by this invocation.

These exercise verified metadata admission, not the review validator. No agent,
network, artifact export or service database is used by this test module.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import os
import threading
import unittest
from uuid import uuid4

import test_jobs_migration_postgres as migration_tests


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV),
                     "Run tests/run_postgres.py on the isolated jobs-db service")
class IndependentReviewPostgresTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing dedicated-database fixture without inheriting
        # and collecting its migration test methods a second time.
        self.fixture = migration_tests.JobSchemaMigrationTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.store = self.fixture.store()
        storage = importlib.import_module("menu_worker.job_store")
        self.JobConflict = storage.JobConflict
        self.JobInputError = storage.JobInputError

    def cleanup_fixture(self):
        self.assertTrue(self.fixture.doCleanups(), "Dedicated migration database cleanup failed")

    def finished_pair(self, outcomes=("rejected", "rejected"), *, same_article=True):
        self.fixture.install_v4()
        base = "https://one-piece.com/news/82812/index.html"
        with self.fixture.connect() as connection:
            first, _ = self.fixture.seed_v4_job(connection, "review-first", base, "running", True)
            second, _ = self.fixture.seed_v4_job(
                connection, "review-second", base + "/" if same_article
                else "https://one-piece.com/news/82813/index.html", "running", True)
        self.store.initialize()
        rows = []
        for job_id, outcome in zip((first, second), outcomes, strict=True):
            job = self.store.get(job_id)
            self.assertTrue(self.store.complete(
                job_id, job["claim_token"], expected_kind="anime_generate", execution_status="succeeded",
                outcome=outcome, producer_status={"rejected": "rejected_archived", "ready_for_staging": "ready_for_feed",
                                                  "held": "needs_crawler_update"}[outcome],
                artifact_path=f"jobs/{job_id}/attempts/{job['generation_attempt_id']}/manifest.json",
                result_sha256=hashlib.sha256(job_id.encode()).hexdigest(),
            ))
            rows.append(self.store.get(job_id))
        return rows

    @staticmethod
    def review(job):
        review_id = str(uuid4())
        return {"review_id": review_id, "review_result_sha256": hashlib.sha256(review_id.encode()).hexdigest(),
                "artifact_path": f"reviews/{job['job_id']}/{review_id}.json",
                "artifact_sha256": hashlib.sha256((review_id + "seal").encode()).hexdigest()}

    def rows(self, table):
        self.assertIn(table, {"content_generation_review", "content_staging_selection"})
        with self.fixture.connect() as connection:
            return connection.execute(f"SELECT * FROM public.{table}").fetchall()

    @staticmethod
    def concurrently(actions):
        barrier = threading.Barrier(len(actions), timeout=10)

        def run(action):
            barrier.wait()
            return action()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(run, actions, timeout=25))

    def test_concurrent_registration_and_selection_replay_preserve_rejection(self):
        job, _ = self.finished_pair()
        review = self.review(job)
        before = self.fixture.snapshot()
        registered = self.concurrently([lambda: self.store.register_staging_review(job, review)] * 2)
        self.assertEqual(registered[0], registered[1])
        self.assertEqual(len(self.rows("content_generation_review")), 1)
        self.assertEqual(self.store.get_staging_review(review["review_id"]), registered[0])
        self.assertEqual(registered[0]["source_result_sha256"], job["result_sha256"])
        self.concurrently([lambda: self.store.select_staging_result(job, review_id=review["review_id"])] * 2)
        selection, = self.rows("content_staging_selection")
        self.assertEqual(str(selection["review_id"]), review["review_id"])
        self.assertEqual(selection["result_sha256"], job["result_sha256"])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_registration_cannot_replace_evidence_or_rebind_identity(self):
        job, _ = self.finished_pair()
        review = self.review(job)
        original = self.store.register_staging_review(job, review)
        before = self.fixture.snapshot()
        for changes in ({"artifact_sha256": "0" * 64}, {"artifact_path": "other/manifest.json"},
                        {"review_id": str(uuid4())}, {"review_result_sha256": "1" * 64}):
            with self.subTest(changes=changes), self.assertRaises(self.JobInputError) as error:
                self.store.register_staging_review(job, {**review, **changes})
            self.assertEqual(error.exception.code, "staging_review_mismatch")
        self.assertEqual(self.store.get_staging_review(review["review_id"]), original)
        self.assertEqual(len(self.rows("content_generation_review")), 1)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_registration_requires_matching_rejected_source_and_safe_metadata(self):
        rejected, ready = self.finished_pair(("rejected", "ready_for_staging"))
        before = self.fixture.snapshot()
        for changes in ({"result_sha256": "0" * 64}, {"generation_attempt_id": ready["generation_attempt_id"]},
                        {"artifact_path": "jobs/changed/manifest.json"}):
            with self.subTest(changes=changes), self.assertRaises(self.JobInputError) as error:
                self.store.register_staging_review({**rejected, **changes}, self.review(rejected))
            self.assertEqual(error.exception.code, "staging_ledger_mismatch")
        with self.assertRaises(self.JobInputError) as error:
            self.store.register_staging_review(ready, self.review(ready))
        self.assertEqual(error.exception.code, "staging_ledger_mismatch")
        for changes in ({"artifact_path": "../outside.json"}, {"artifact_path": "/outside.json"},
                        {"artifact_path": "jobs\\outside.json"}, {"artifact_sha256": "not-a-digest"}):
            with self.subTest(changes=changes), self.assertRaises(self.JobInputError) as error:
                self.store.register_staging_review(rejected, {**self.review(rejected), **changes})
            self.assertEqual(error.exception.code, "staging_review_mismatch")
        self.assertEqual(self.rows("content_generation_review"), [])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_unregistered_or_foreign_review_cannot_select_rejected_result(self):
        first, second = self.finished_pair(same_article=False)
        review = self.review(first)
        self.assertIsNone(self.store.get_staging_review(review["review_id"]))
        with self.assertRaises(self.JobInputError) as error:
            self.store.select_staging_result(first, review_id=review["review_id"])
        self.assertEqual(error.exception.code, "staging_review_mismatch")
        self.store.register_staging_review(first, review)
        with self.assertRaises(self.JobInputError) as error:
            self.store.select_staging_result(second, review_id=review["review_id"])
        self.assertEqual(error.exception.code, "staging_review_mismatch")
        with self.assertRaises(self.JobInputError) as error:
            self.store.select_staging_result(first)
        self.assertEqual(error.exception.code, "staging_ledger_mismatch")
        self.assertEqual(self.rows("content_staging_selection"), [])

    def test_concurrent_different_reviews_pin_one_review_without_removing_history(self):
        job, _ = self.finished_pair()
        reviews = [self.review(job) for _ in range(2)]
        for review in reviews:
            self.store.register_staging_review(job, review)
        before = self.fixture.snapshot()

        def select(review):
            try:
                self.store.select_staging_result(job, review_id=review["review_id"])
                return "selected"
            except self.JobInputError as error:
                return error.code

        result = self.concurrently([lambda review=review: select(review) for review in reviews])
        self.assertCountEqual(result, ["selected", "staging_review_already_selected"])
        selection, = self.rows("content_staging_selection")
        chosen = str(selection["review_id"])
        self.store.select_staging_result(job, review_id=chosen)
        self.assertEqual(len(self.rows("content_generation_review")), 2)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_reviewed_and_original_ready_siblings_compete_for_one_article(self):
        rejected, ready = self.finished_pair(("rejected", "ready_for_staging"))
        review = self.review(rejected)
        self.store.register_staging_review(rejected, review)
        before = self.fixture.snapshot()

        def select(job, review_id=None):
            try:
                self.store.select_staging_result(job, review_id=review_id)
                return "selected"
            except self.JobConflict as error:
                return error.code

        result = self.concurrently([lambda: select(rejected, review["review_id"]), lambda: select(ready)])
        self.assertCountEqual(result, ["selected", "staging_result_already_selected"])
        selection, = self.rows("content_staging_selection")
        winner = rejected if str(selection["selected_job_id"]) == rejected["job_id"] else ready
        chosen_review = review["review_id"] if winner is rejected else None
        self.store.select_staging_result(winner, review_id=chosen_review)
        self.assertEqual(self.fixture.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
