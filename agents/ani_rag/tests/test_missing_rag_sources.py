import sqlite3
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from ani_rag.models import EvidenceType, RagContentType
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.structured_mcp import (
    StructuredCollectionRequest, StructuredItemHit, StatementValuesResult, collect_structured_items,
)
from scripts.repair_missing_rag_sources import reconcile


class SourceParentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.registry = load_source_registry()
        self.store = SQLiteRagStore(self.db)

    async def test_structured_read_registers_only_its_approved_source_before_claim(self):
        self.db.execute("PRAGMA foreign_keys=ON")
        key = "wikidata_character_catalog"
        source = next(source for source in self.registry.approved_sources if source.source_id == key)
        tools = dict(source.tool_bindings)
        adapter = AsyncMock()
        adapter.list_tools.return_value = set(tools.values())
        adapter.search_items.return_value = [StructuredItemHit(
            key, "Q42", "Example", "https://www.wikidata.org/wiki/Q42", "en", source.mcp_server, tools["search"],
        )]
        adapter.get_statement_values.return_value = StatementValuesResult(key, source.mcp_server, tools["read"], "Q42", ())
        request = StructuredCollectionRequest(key, RagContentType.CHARACTER, EvidenceType.REFERENCE_FACT, "example", "en", ("P1",))
        await collect_structured_items(request, registry=self.registry, adapter=adapter, repository=self.store)
        self.assertEqual([r[0] for r in self.db.execute("SELECT source_id FROM rag_source")], [key])
        self.assertEqual(self.db.execute("SELECT status FROM rag_source_read_ledger").fetchone()[0], "complete")
        self.assertEqual(list(self.db.execute("PRAGMA foreign_key_check")), [])

    async def test_restore_only_missing_approved_source_preserves_children_and_source_metadata(self):
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("""INSERT INTO rag_source_read_ledger(claim_id,source_id,canonical_url,status,updated_at)
                           VALUES('c','anime_corner_interviews','https://animecorner.me/test','fetching','before')""")
        self.db.executemany("""INSERT INTO rag_image_asset(asset_id,source_id,source_url,local_path,work_link_status,created_at)
                               VALUES(?,?, 'https://example.org/img.png','img.png','matched','before')""",
                            [("approved", "anime_corner_interviews"), ("unapproved", "example_unapproved")])
        self.db.commit()
        self.db.execute("PRAGMA foreign_keys=ON")
        before = list(map(tuple, self.db.execute("SELECT * FROM rag_source_read_ledger")))
        self.assertEqual((await reconcile(self.db, self.registry))["missing_approved"], ["anime_corner_interviews"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM rag_source").fetchone()[0], 0)
        result = await reconcile(self.db, self.registry, apply=True)
        self.assertEqual(result["missing_unapproved"], ["example_unapproved"])
        self.assertEqual(len(list(self.db.execute("PRAGMA foreign_key_check"))), 1)
        self.assertEqual(before, list(map(tuple, self.db.execute("SELECT * FROM rag_source_read_ledger"))))
        self.db.execute("UPDATE rag_source SET last_error='retained',updated_at='before'")
        self.db.commit()
        source = next(s for s in self.registry.approved_sources if s.source_id == "anime_corner_interviews")
        await self.store.register_source(source, only_if_missing=True)
        self.assertEqual((await reconcile(self.db, self.registry, apply=True))["missing_approved"], [])
        self.assertEqual(tuple(self.db.execute("SELECT last_error,updated_at FROM rag_source").fetchone()), ("retained","before"))
