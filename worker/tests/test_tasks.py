"""Offline task-contract checks; live broker delivery is verified separately."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from menu_worker.tasks import probe


class ProbeTests(unittest.TestCase):
    def run_probe(self, **kwargs):
        # apply() executes locally; it does not establish broker delivery.
        return probe.apply(kwargs=kwargs, task_id="unit-probe").get(propagate=True)

    def test_success_keeps_task_identity_and_label(self):
        result = self.run_probe(label="unit-manual")
        self.assertEqual(result["label"], "unit-manual")
        self.assertEqual(result["task_id"], "unit-probe")
        self.assertEqual(result["retries"], 0)

    def test_retry_budget_converges_without_real_broker(self):
        for failures in (1, 2):
            with self.subTest(failures=failures):
                result = self.run_probe(
                    label="unit-retry", failures_before_success=failures
                )
                self.assertEqual(result["retries"], failures)
                self.assertEqual(result["task_id"], "unit-probe")

    def test_invalid_delay_does_not_run(self):
        for delay in (-1, 11, float("inf"), float("nan"), "1", True):
            with self.subTest(delay=delay):
                with self.assertRaises((TypeError, ValueError)):
                    self.run_probe(delay_seconds=delay)

    def test_invalid_retry_count_does_not_run(self):
        for failures in (-1, 3, 0.5, "1", True):
            with self.subTest(failures=failures):
                with self.assertRaises((TypeError, ValueError)):
                    self.run_probe(failures_before_success=failures)

    def test_invalid_labels_are_rejected(self):
        for label in ("", "x" * 81, "line\nbreak", None, 123):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.run_probe(label=label)

    def test_bounded_delay_is_honored(self):
        with patch("menu_worker.tasks.time.sleep") as sleep:
            self.run_probe(delay_seconds=10)
        sleep.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()
