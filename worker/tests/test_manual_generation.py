"""CLI admission checks for the manual, potentially billable generation command."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
import unittest
from unittest.mock import Mock, patch

from menu_worker import jobs
from menu_worker.job_checks import smoke


ARTICLE_URL = "https://one-piece.com/news/82812/index.html"
ARTICLE_ARGS = ["--idempotency-key", "manual-check", "--source", "one_piece_official",
                "--url", ARTICLE_URL, "--content-type", "goods",
                "--menu-work-id", "8"]


class ManualGenerationCliTests(unittest.TestCase):
    def invoke(self, role, command):
        store = Mock()
        store.submit.return_value = {"created": True}
        output, error = StringIO(), StringIO()
        env = {} if role is None else {"CONTENT_JOB_KIND": role}
        with (patch.dict(os.environ, env, clear=True), patch.object(jobs, "JobStore", return_value=store),
              redirect_stdout(output), redirect_stderr(error)):
            code = jobs.main(command)
        return code, store, json.loads(error.getvalue() if code else output.getvalue())

    def test_generate_requires_explicit_live_role(self):
        for role in (None, "fake", "anime"):
            with self.subTest(role=role):
                code, store, result = self.invoke(role, ["generate", *ARTICLE_ARGS])
                self.assertEqual(code, 1)
                self.assertEqual(result["error"], "generation_profile_required")
                store.submit.assert_not_called()

        code, store, _ = self.invoke("anime_generate", ["generate", *ARTICLE_ARGS])
        self.assertEqual(code, 0)
        store.submit.assert_called_once_with(
            {"idempotency_key": "manual-check", "source_id": "one_piece_official", "source_url": ARTICLE_URL,
             "content_type": "goods", "menu_work_id": 8}, job_kind="anime_generate",
        )

    def test_probe_commands_cannot_register_a_live_job(self):
        for command in (["smoke"], ["submit", *ARTICLE_ARGS], ["submit", *ARTICLE_ARGS, "--kind", "anime"]):
            with self.subTest(command=command):
                code, store, result = self.invoke("anime_generate", command)
                self.assertEqual(code, 1)
                self.assertEqual(result["error"], "probe_command_not_available")
                store.submit.assert_not_called()
        fixture_args = ["--idempotency-key", "fixture-check", "--url", "https://example.invalid/success/fixture-check"]
        for role, args in (("fake", fixture_args), ("anime", ARTICLE_ARGS)):
            with self.subTest(role=role):
                code, store, _ = self.invoke(role, ["submit", *args])
                self.assertEqual(code, 0)
                self.assertEqual(store.submit.call_args.kwargs["job_kind"], role)

    def test_submit_rejects_live_kind_before_store_or_broker_access(self):
        with (patch.object(jobs, "JobStore") as store, redirect_stderr(StringIO()),
              self.assertRaises(SystemExit) as rejected):
            jobs.main(["submit", *ARTICLE_ARGS, "--kind", "anime_generate"])
        self.assertEqual(rejected.exception.code, 2)
        store.assert_not_called()

    def test_smoke_helper_rejects_live_role_before_submitting(self):
        store = Mock()
        with self.assertRaises(ValueError):
            smoke(store, kind="anime_generate")
        store.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
