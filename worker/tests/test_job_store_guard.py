"""Database isolation is checked before any network connection is attempted."""
import os
from unittest.mock import patch
import unittest
from uuid import uuid4

from menu_worker.job_store import JobStore, JobStoreError, validate_database_url


class JobStoreGuardTests(unittest.TestCase):
    def test_service_database_and_remote_database_are_rejected(self):
        urls = ("postgresql://u:p@jobs-db/me_nu_checking", "postgresql://u:p@jobs-db/me_nu_labs",
                "postgresql://u:p@db.example.org/menu_content_jobs", "postgresql:///menu_content_jobs",
                "postgresql://u:p@jobs-db/menu_content_jobs?hostaddr=203.0.113.1",
                "postgresql://u:p@jobs-db/menu_content_jobs?options=-csearch_path%3Dother")
        with patch("menu_worker.job_store.psycopg.connect") as connect:
            for value in urls:
                with self.subTest(value=value), self.assertRaises(JobStoreError):
                    JobStore(value)
            connect.assert_not_called()

    def test_test_database_needs_explicit_expected_name(self):
        value = "postgresql://u:p@jobs-db/menu_content_jobs_test"
        with self.assertRaises(JobStoreError):
            JobStore(value)
        store = JobStore(value, expected_database="menu_content_jobs_test")
        self.assertEqual(store.expected_database, "menu_content_jobs_test")

    def test_application_database_environment_is_never_used(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://private@service/private"}, clear=True):
            with self.assertRaises(JobStoreError):
                JobStore()

    def test_invalid_connection_errors_do_not_echo_credentials(self):
        value = "not-a-url private-secret"
        with self.assertRaises(JobStoreError) as caught:
            validate_database_url(value)
        self.assertNotIn("private-secret", str(caught.exception))

    def test_completion_rejects_unowned_artifact_paths_before_connecting(self):
        store = JobStore("postgresql://u:p@jobs-db/menu_content_jobs")
        job, token, attempt = str(uuid4()), str(uuid4()), str(uuid4())
        invalid = ("/var/lib/other/result.json", "jobs/other/attempts/one/result.json",
                   f"jobs/{job}/attempts/{attempt}/../result.json")
        with patch("menu_worker.job_store.psycopg.connect") as connect:
            for path in invalid:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    store.complete(job, token, execution_status="succeeded", outcome="ready_for_staging",
                                   producer_status="ready_for_feed", artifact_path=path, result_sha256="a" * 64)
            connect.assert_not_called()

    def test_success_requires_preserved_result_metadata(self):
        store = JobStore("postgresql://u:p@jobs-db/menu_content_jobs")
        with patch("menu_worker.job_store.psycopg.connect") as connect:
            with self.assertRaises(ValueError):
                store.complete(str(uuid4()), str(uuid4()), execution_status="succeeded")
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
