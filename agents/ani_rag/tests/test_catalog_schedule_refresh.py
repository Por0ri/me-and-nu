import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ani_rag.automatic_plans import load_automatic_plans
from ani_rag.catalog import LocalWorkCatalog
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.settings import load_source_registry
from ani_rag.web_discovery import WebCandidateAssessmentWorker, WebDiscoveryWorker


class CatalogScheduleRefreshTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.official = root / "catalog"
        self.official.mkdir()
        self.registry = root / "registry"
        self.registry.mkdir()
        self.works = self.official / "menu_works.csv"
        self.works.write_text("menu_work_id,title_english\n1,Original\n2327,Old Title\n", encoding="utf-8")
        (self.official / "menu_work_entries.csv").write_text("menu_work_id,title_english\n", encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {
            "ME_NU_WORK_CATALOG_DIR": str(self.official),
            "ME_NU_WORK_REGISTRY_DIR": str(self.registry),
        }))
        self.enterContext(patch("ani_rag.catalog.DEFAULT_REFERENCE_DIR", self.registry))
        self.config = root / "settings.json"
        self.config.write_text(json.dumps({
            "version": 1, "enabled": True, "content_types": ["guide"],
            "query_templates": {"guide": "{title} guide"}, "reference_sources": {},
        }), encoding="utf-8")
        self.checkpoint = root / "state.json"
        self.start = datetime(2026, 11, 23, tzinfo=UTC)

    def _plans(self, now):
        return load_automatic_plans(
            load_source_registry(), config_path=self.config, state_path=self.checkpoint, now=now,
        )

    def test_catalog_changes_keep_rotation_and_config_guard(self):
        first = self._plans(self.start)
        self.works.write_text("menu_work_id,title_english\n1,Original\n2327,New Title\n", encoding="utf-8")
        (self.registry / "agent_works.csv").write_text("menu_work_id,title_english\n1050362,Temporary\n", encoding="utf-8")
        refreshed = self._plans(self.start + timedelta(days=2))
        self.assertEqual(first[0], refreshed[0])
        self.assertEqual(refreshed[1].request.expected_work_titles, ("New Title",))
        self.assertEqual(refreshed[2].request.menu_work_id, 1050362)
        payload = json.loads(self.checkpoint.read_bytes())
        self.assertEqual((payload["work_count"], payload["task_count"]), (3, 3))
        self.assertEqual(payload["started_at"], self.start.isoformat())
        self.assertEqual(self._plans(self.start + timedelta(weeks=1)), refreshed)
        original_checkpoint = self.checkpoint.read_bytes()
        config = json.loads(self.config.read_bytes())
        config["rotation_days"] = 5
        self.config.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "configuration"):
            self._plans(self.start)
        self.assertEqual(self.checkpoint.read_bytes(), original_checkpoint)

    def test_queued_old_seed_is_blocked_before_paid_search_or_read(self):
        self.works.write_text("menu_work_id,title_english\n2327,New Title\n", encoding="utf-8")
        catalog = LocalWorkCatalog()
        request = {"menu_work_id": 2327, "content_type": "guide", "query": "Old Title guide",
                   "expected_work_titles": ["Old Title"]}
        for kind in ("web_discovery", "web_candidate_assessment"):
            with self.subTest(kind=kind):
                payload = dict(request)
                if kind == "web_candidate_assessment":
                    payload["candidate"] = {"title": "Old Title guide", "url": "https://example.org/old"}
                queued = QueueItem("old", QueueName.COLLECTION, status=QueueStatus.RUNNING, payload=payload)
                queues = SimpleNamespace(claim=AsyncMock(return_value=queued), save=AsyncMock())
                adapter = SimpleNamespace(search_web=AsyncMock(), read_web_candidate=AsyncMock())
                if kind == "web_discovery":
                    worker = WebDiscoveryWorker(queues, adapter, catalog_titles=catalog.titles_for_work_id)
                else:
                    worker = WebCandidateAssessmentWorker(queues, adapter, catalog, None, None)
                self.assertTrue(asyncio.run(worker.run_once()))
                outcome = queues.save.await_args.args[0]
                self.assertEqual((outcome.status, outcome.last_error), (QueueStatus.BLOCKED, "stale_catalog_work_seed"))
                adapter.search_web.assert_not_awaited()
                adapter.read_web_candidate.assert_not_awaited()
