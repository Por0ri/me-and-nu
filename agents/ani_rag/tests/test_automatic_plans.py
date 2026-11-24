import tempfile
import unittest
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ani_rag.automatic_plans import load_automatic_plans
from ani_rag.agents.mcp_crawler.agent import MCPCrawlerAgent, RecurringCollectionPlan
from ani_rag.models import RagContentType
from ani_rag.settings import load_source_registry
from ani_rag.web_discovery import WebDiscoveryRequest


class AutomaticPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        official = Path(temporary.name) / "catalog"
        registry = Path(temporary.name) / "registry"
        self.registry_dir = registry
        official.mkdir()
        registry.mkdir()
        (official / "menu_works.csv").write_text(
            "menu_work_id,title_english\n1,Work One\n2,Work Two\n", encoding="utf-8",
        )
        (official / "menu_work_entries.csv").write_text("menu_work_id,title_english\n", encoding="utf-8")
        (registry / "agent_works.csv").write_text(
            "menu_work_id,title_english\n1050362,New Work\n", encoding="utf-8",
        )
        self.enterContext(patch.dict(os.environ, {
            "ME_NU_WORK_CATALOG_DIR": str(official), "ME_NU_WORK_REGISTRY_DIR": str(registry),
        }))
        self.enterContext(patch("ani_rag.catalog.DEFAULT_REFERENCE_DIR", registry))

    def test_full_local_catalog_is_staggered_across_web_and_approved_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            plans = load_automatic_plans(
                load_source_registry(),
                state_path=Path(temporary) / "schedule.json",
                now=datetime(2026, 11, 23, tzinfo=timezone.utc),
            )

        self.assertEqual(len(plans), 3 * 19)
        self.assertEqual(plans[0].first_run_at, datetime(2026, 11, 23, tzinfo=timezone.utc))
        self.assertEqual(
            plans[-1].first_run_at,
            plans[0].first_run_at + timedelta(seconds=(7 * 86_400 * (len(plans) - 1)) // len(plans)),
        )
        self.assertEqual(len({plan.plan_id for plan in plans}), len(plans))
        self.assertTrue(all(plan.interval_seconds == 7 * 86_400 for plan in plans))
        structured_plans = [
            plan for plan in plans
            if getattr(plan.request, "source_id", None) == "wikidata_character_catalog"
        ]
        self.assertTrue(structured_plans)
        self.assertTrue(all(plan.request.seed_menu_work_id is not None for plan in structured_plans))
        web_plans = [plan for plan in plans if isinstance(plan.request, WebDiscoveryRequest)]
        self.assertEqual(len(web_plans), 3 * 7)
        self.assertEqual({plan.request.menu_work_id for plan in web_plans}, {1, 2, 1_050_362})
        self.assertTrue(all(plan.request.expected_work_titles for plan in web_plans))
        self.assertTrue(all(plan.plan_id.rsplit("-", 2)[-2] == "1" for plan in plans[:19]))
        self.assertEqual(plans[19].plan_id.rsplit("-", 2)[-2], "2")

    def test_checkpoint_prevents_silent_schedule_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "schedule.json"
            first = load_automatic_plans(
                load_source_registry(), state_path=state,
                now=datetime(2026, 11, 23, tzinfo=timezone.utc),
            )
            second = load_automatic_plans(
                load_source_registry(), state_path=state,
                now=datetime(2026, 12, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(first[0].first_run_at, second[0].first_run_at)
        self.assertEqual(first[-1].first_run_at, second[-1].first_run_at)

    def test_growing_registry_reuses_checkpoint_and_plans_the_new_temporary_work(self):
        state = self.registry_dir.parent / "schedule.json"
        registry = load_source_registry()
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        first = load_automatic_plans(registry, state_path=state, now=now)
        with (self.registry_dir / "agent_works.csv").open("a", encoding="utf-8") as stream:
            stream.write("1000077,Fourth Series\n")
        resumed = load_automatic_plans(registry, state_path=state, now=now + timedelta(hours=1))
        self.assertEqual(resumed[0].first_run_at, first[0].first_run_at)
        self.assertEqual(len(resumed), len(first) + 19)
        added = [p for p in resumed if isinstance(p.request, WebDiscoveryRequest) and p.request.menu_work_id == 1_000_077]
        self.assertEqual(len(added), 7)

    def test_recurring_web_plan_queues_one_idempotent_search_job(self):
        class Queue:
            def __init__(self):
                self.items = []

            async def enqueue(self, item):
                self.items.append(item)

        async def run():
            queue = Queue()
            now = datetime.now(timezone.utc)
            request = WebDiscoveryRequest(
                query="Attack on Titan anime character profile",
                content_type=RagContentType.CHARACTER, menu_work_id=1,
            )
            agent = MCPCrawlerAgent(
                url_worker=SimpleNamespace(queues=queue, registry=SimpleNamespace(sources=())),
                web_discovery_worker=object(),
                recurring_plans=(RecurringCollectionPlan(
                    "web-character-1-test", request, 600,
                    first_run_at=now - timedelta(seconds=1),
                ),),
            )
            first = await agent._enqueue_due_plans()
            second = await agent._enqueue_due_plans()
            return queue.items, first, second

        items, first, second = asyncio.run(run())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].handler_kind, "web_discovery")
        self.assertEqual(items[0].payload["menu_work_id"], 1)


if __name__ == "__main__":
    unittest.main()
