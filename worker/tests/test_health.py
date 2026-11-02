"""Health evidence must identify this worker and a recently executed Beat task."""

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from menu_worker import health
from menu_worker.tasks import HEARTBEAT_KEY, heartbeat


class HeartbeatTests(unittest.TestCase):
    def test_records_task_identity_and_expiring_timestamp(self):
        client = MagicMock()
        client.__enter__.return_value = client
        with patch("menu_worker.tasks.result_store", return_value=client):
            with patch("menu_worker.tasks.settings", SimpleNamespace(beat_interval_seconds=10)):
                heartbeat.apply(task_id="unit-heartbeat").get(propagate=True)
        args, kwargs = client.set.call_args
        self.assertEqual(args[0], HEARTBEAT_KEY)
        self.assertEqual(kwargs, {"ex": 60})
        evidence = json.loads(args[1])
        self.assertEqual(evidence["task_id"], "unit-heartbeat")
        stamp = datetime.fromisoformat(evidence["timestamp"])
        self.assertIsNotNone(stamp.utcoffset())
        self.assertLess(abs((datetime.now(timezone.utc) - stamp).total_seconds()), 5)

    def test_storage_failure_is_a_failed_task(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.set.side_effect = ConnectionError("storage unavailable")
        with patch("menu_worker.tasks.result_store", return_value=client):
            with self.assertRaises(ConnectionError):
                heartbeat.apply(task_id="unit-failed-heartbeat").get(propagate=True)


class HealthTests(unittest.TestCase):
    def beat_health(self, age):
        client = MagicMock()
        client.__enter__.return_value = client
        stamp = datetime.now(timezone.utc) - timedelta(seconds=age)
        client.get.return_value = json.dumps({"timestamp": stamp.isoformat(), "task_id": "test"}).encode()
        with patch("menu_worker.health.result_store", return_value=client):
            with patch("menu_worker.health.settings", SimpleNamespace(beat_interval_seconds=10)):
                return health.check("beat")

    def test_fresh_heartbeat_is_healthy(self):
        self.assertTrue(self.beat_health(5))

    def test_stale_or_future_heartbeat_is_unhealthy(self):
        self.assertFalse(self.beat_health(90))
        self.assertFalse(self.beat_health(-30))

    def test_missing_heartbeat_is_unhealthy(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = None
        with patch("menu_worker.health.result_store", return_value=client):
            self.assertFalse(health.check("beat"))

    def test_worker_probe_requires_this_worker_reply(self):
        with patch("menu_worker.health.socket.gethostname", return_value="this-worker"):
            with patch.object(health.app.control, "ping", return_value=[{"runtime@other-worker": {"ok": "pong"}}]) as ping:
                self.assertFalse(health.check("worker"))
                ping.assert_called_once_with(destination=["runtime@this-worker"], timeout=3)
            with patch.object(health.app.control, "ping", return_value=[{"runtime@this-worker": {"ok": "pong"}}]):
                self.assertTrue(health.check("worker"))

    def test_probe_errors_exit_nonzero_without_sensitive_error_text(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["health", "beat"]):
            with patch("menu_worker.health.check", side_effect=RuntimeError("redis://private-secret@host/0")):
                with patch.object(sys, "stderr", stderr):
                    self.assertEqual(health.main(), 1)
        self.assertNotIn("private-secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
