import csv
import os
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ani_rag.adapter import ReadResult, SearchHit
from ani_rag.catalog import LLMWorkCatalogResolver, LocalWorkCatalog
from ani_rag.collector import CollectionRequest, collect_from_source
from ani_rag.interfaces import WorkIdentity
from ani_rag.models import EvidenceType, RagContentType, WorkLinkStatus, WorkRegistration
from ani_rag.queues import QueueName
from ani_rag.retrieval import DatabaseEvidenceRetriever
from ani_rag.runtime import compose_crawler_agent
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker, WebCandidateHit, WebCandidatePage, WebDiscoveryRequest,
    WebDiscoveryWorker, enqueue_web_discovery,
)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def source_identity(title, *, kitsu_id=None):
    text = f"{title} is a TV anime. Its opening theme is Moon Parade."
    source_id = "kitsu_anime_catalog" if kitsu_id else "ann_encyclopedia"
    url = (f"https://kitsu.io/anime/{kitsu_id}" if kitsu_id else
           "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=1200")
    return WorkIdentity(
        titles=(title,), external_ids={source_id: kitsu_id} if kitsu_id else {},
        source_url=url, source_id=source_id, source_text=text,
    )


def register(catalog, identity):
    return catalog.register_confirmed_work(identity, WorkRegistration(
        name=identity.titles[0], external_ids=dict(identity.external_ids),
        evidence_quote=identity.source_text,
    ))


def new_anime_response(title):
    quote = f"{title} is a TV anime."
    return {
        "decision": "new", "confidence": "high", "menu_work_id": None,
        "canonical_name": title, "aliases": [], "external_ids": [],
        "subject_kind": "anime_work", "subject_relation": "primary_subject",
        "evidence_quote": quote, "entry_format": "tv", "entry_relation": "main",
        "relation_evidence_quote": quote, "entry_year": None, "reason": "Original names the new anime.",
    }


class TemporaryWorkIdTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.official = self.root / "catalog"
        self.registry_dir = self.root / "registry"
        self.reference = self.root / "reference"
        for directory in (self.official, self.registry_dir, self.reference):
            directory.mkdir()
        self.works_path = self.official / "menu_works.csv"
        self.works_path.write_text("menu_work_id,title_english\n7,Other Work\n", encoding="utf-8")
        (self.official / "menu_work_entries.csv").write_text(
            "menu_work_id,title_english\n7,Other Work The Movie\n", encoding="utf-8",
        )
        (self.reference / "work_characters.csv").write_text("name,menu_work_id\n", encoding="utf-8")

    def catalog(self):
        return LocalWorkCatalog(
            reference_dir=self.reference, catalog_dir=self.official, registry_dir=self.registry_dir,
        )

    def test_root_catalog_holds_both_agent_files_but_external_ids_stay_in_reference(self):
        with patch.dict(os.environ, {"ME_NU_WORK_CATALOG_DIR": str(self.official), "ME_NU_WORK_REGISTRY_DIR": ""}):
            catalog = LocalWorkCatalog(reference_dir=self.reference)
        originals = {path: path.read_bytes() for path in self.official.glob("menu_*.csv")}
        identity = source_identity("Starlit Parade", kitsu_id="50362")
        self.assertEqual(register(catalog, identity).menu_work_ids, (1_050_362,))
        catalog.record_discovery("Ambiguous Work", identity.source_url)

        self.assertEqual(read_csv(self.official / "agent_works.csv")[0]["menu_work_id"], "1050362")
        self.assertEqual(read_csv(self.official / "discovered_works.csv")[0]["canonical_title"], "Ambiguous Work")
        self.assertEqual(set(self.official.glob("menu_*.csv")), set(originals))
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertEqual(read_csv(self.reference / "menu_work_external_entries.csv")[0]["external_id"], "50362")
        self.assertFalse((self.reference / "agent_works.csv").exists())
        self.assertFalse((self.reference / "discovered_works.csv").exists())

        mounted = LocalWorkCatalog(reference_dir=self.reference, catalog_dir=self.official, registry_dir=self.official)
        self.assertEqual(mounted.resolve_sync(WorkIdentity((), {"kitsu": "50362"})).menu_work_ids, (1_050_362,))

    def test_model_only_kitsu_id_is_not_assigned(self):
        identity = source_identity("Starlit Parade")
        model_guess = WorkRegistration(
            name=identity.titles[0], external_ids={"kitsu": "50362"}, evidence_quote=identity.source_text,
        )
        result = self.catalog().register_confirmed_work(identity, model_guess)
        self.assertFalse(result.menu_work_ids)
        self.assertFalse((self.registry_dir / "agent_works.csv").exists())
        self.assertEqual(read_csv(self.registry_dir / "discovered_works.csv")[0]["canonical_title"], "Starlit Parade")

    async def test_existing_seed_cannot_override_a_quoted_remake_or_an_unclear_subject(self):
        self.works_path.write_text(
            "menu_work_id,title_english\n8,One Piece\n1518,The One Piece\n", encoding="utf-8",
        )
        quote = "The One Piece is an anime remake."
        identity = WorkIdentity(
            titles=("Meet the team behind the remake",), external_ids={}, source_text=quote,
            source_url="https://example.org/remake", seed_menu_work_ids=(8,),
        )
        response = {**new_anime_response("The One Piece"), "decision": "existing",
                    "menu_work_id": 8, "evidence_quote": quote, "relation_evidence_quote": quote}
        for relation, expected in (("primary_subject", WorkLinkStatus.AMBIGUOUS),
                                   ("unclear", WorkLinkStatus.UNRESOLVED)):
            with self.subTest(relation=relation):
                model = SimpleNamespace(generate_json=AsyncMock(return_value={**response, "subject_relation": relation}))
                result = await LLMWorkCatalogResolver(self.catalog(), model).resolve(identity)
                self.assertEqual(result.status, expected)
                self.assertFalse(result.menu_work_ids)
                if relation == "primary_subject":
                    self.assertEqual(set(result.candidate_menu_work_ids), {8, 1518})
        self.assertFalse((self.registry_dir / "agent_works.csv").exists())

    async def test_sqlite_only_number_cannot_override_official_or_temporary_catalogs(self):
        identity = source_identity("Unlisted Series")
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE rag_work_catalog(menu_work_id INTEGER, canonical_name TEXT, "
            "aliases_json TEXT, external_ids_json TEXT)"
        )
        connection.execute("INSERT INTO rag_work_catalog VALUES (?, ?, '[]', '{}')", (2327, identity.titles[0]))
        model = SimpleNamespace(generate_json=AsyncMock(return_value={"decision": "unknown", "confidence": "low"}))
        with patch.dict(os.environ, {"ME_NU_WORK_CATALOG_DIR": str(self.official),
                                     "ME_NU_WORK_REGISTRY_DIR": str(self.registry_dir)}), \
             patch("ani_rag.catalog.DEFAULT_REFERENCE_DIR", self.reference):
            crawler = compose_crawler_agent(
                connection, registry=load_source_registry(), url_mcp=SimpleNamespace(), work_match_model=model,
            )
            result = await crawler.url_worker.work_catalog.resolve(identity)
        self.assertFalse(result.menu_work_ids)
        self.assertFalse((self.registry_dir / "agent_works.csv").exists())

    async def test_same_title_on_two_kitsu_works_is_held_and_a_unique_lookup_reuses_its_number(self):
        title = "Starlit Parade"
        identity = source_identity(title)
        model = SimpleNamespace(generate_json=AsyncMock(return_value=new_anime_response(title)))
        ambiguous = LLMWorkCatalogResolver(
            self.catalog(), model, kitsu_lookup=AsyncMock(return_value=("50362", "50363")),
        )
        self.assertFalse((await ambiguous.resolve(identity)).menu_work_ids)
        self.assertFalse((self.registry_dir / "agent_works.csv").exists())
        self.assertEqual(read_csv(self.registry_dir / "discovered_works.csv")[0]["source_url"], identity.source_url)
        unique = LLMWorkCatalogResolver(self.catalog(), model, kitsu_lookup=AsyncMock(return_value=("50362",)))
        self.assertEqual((await unique.resolve(identity)).menu_work_ids, (1_050_362,))

    def test_kitsu_id_is_stable_and_confirmed_official_id_wins(self):
        identity = source_identity("Starlit Parade", kitsu_id="50362")
        initial = self.works_path.read_bytes()
        registered = register(self.catalog(), identity)
        self.assertEqual(registered.menu_work_ids, (1_050_362,))
        self.assertEqual(register(self.catalog(), identity).menu_work_ids, registered.menu_work_ids)
        self.assertEqual([row["menu_work_id"] for row in read_csv(self.registry_dir / "agent_works.csv")],
                         ["1050362"])
        self.assertEqual(self.works_path.read_bytes(), initial)
        with self.works_path.open("a", encoding="utf-8") as stream:
            stream.write("2569,Starlit Parade\n")
        self.assertEqual(self.catalog().resolve_sync(identity).menu_work_ids, (2569,))
        self.assertEqual(self.catalog().resolve_sync(source_identity("Other Work The Movie")).menu_work_ids,
                         (7,))

    def test_title_hash_and_snapshot_reopen_match_period_policy(self):
        expected = 2_000_000 + zlib.crc32(b"starlitparade") % 1_000_000_000
        for title in ("Ｓｔａｒｌｉｔ－Ｐａｒａｄｅ", "StarLit: Parade"):
            with self.subTest(title=title):
                self.assertEqual(register(self.catalog(), source_identity(title)).menu_work_ids, (expected,))
        self.assertEqual(len(read_csv(self.registry_dir / "agent_works.csv")), 1)
        self.assertEqual(self.catalog().resolve_sync(source_identity("Starlit-Parade")).menu_work_ids,
                         (expected,))

    def test_kitsu_collision_and_ambiguous_title_remain_discoveries(self):
        registered_path = self.registry_dir / "agent_works.csv"
        registered_path.write_text("menu_work_id,title_english\n1050362,Earlier Work\n", encoding="utf-8")
        with self.works_path.open("a", encoding="utf-8") as stream:
            stream.write("9,Shared Title\n10,Shared Title\n")
        previous = registered_path.read_bytes()
        for identity in (source_identity("Starlit Parade", kitsu_id="50362"), source_identity("Shared Title")):
            for _ in range(2):
                self.assertFalse(register(self.catalog(), identity).menu_work_ids)
        self.assertEqual(registered_path.read_bytes(), previous)
        rows = read_csv(self.registry_dir / "discovered_works.csv")
        self.assertCountEqual([r["canonical_title"] for r in rows], ["Starlit Parade", "Shared Title"])
        self.assertTrue(all(r["source_url"] and r["status"] == "pending_team_review" for r in rows))

    async def test_ambiguous_edition_preserves_source_and_records_discovery_without_generation(self):
        with self.works_path.open("a", encoding="utf-8") as stream:
            stream.write("9,Shared Title\n10,Shared Title\n")
        title = "Shared Title (TV 2)"
        body = f"# {title}\nThe page describes a second television production."
        source_id = "ann_encyclopedia"
        url = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=99999999"
        page = ReadResult(source_id, title, url, body, "firecrawl", "firecrawl_scrape", redirects_verified=True)
        reader = SimpleNamespace(
            list_tools=AsyncMock(return_value={"firecrawl_search", "firecrawl_scrape"}),
            search=AsyncMock(return_value=(SearchHit(source_id, title, url, "firecrawl", "firecrawl_search"),)),
            read=AsyncMock(return_value=page),
        )
        model = SimpleNamespace(generate_json=AsyncMock())
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        connection.executescript(schema.read_text(encoding="utf-8"))
        result = await collect_from_source(
            CollectionRequest(source_id, RagContentType.GUIDE, EvidenceType.REFERENCE_FACT, title, "en"),
            registry=load_source_registry(), adapter=reader, repository=SQLiteRagStore(connection),
            work_catalog=LLMWorkCatalogResolver(self.catalog(), model),
        )
        self.assertEqual(len(result.stored), 1)
        model.generate_json.assert_not_awaited()
        self.assertEqual(tuple(connection.execute("SELECT raw_text,url FROM rag_document").fetchone()), (body, url))
        links = connection.execute("SELECT menu_work_id,candidate_menu_work_id,status FROM rag_work_link")
        self.assertCountEqual([tuple(row) for row in links], [(None, 9, "ambiguous"), (None, 10, "ambiguous")])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM rag_generation_work").fetchone()[0], 0)
        self.assertFalse((self.registry_dir / "agent_works.csv").exists())
        self.assertEqual(
            [(row["canonical_title"], row["source_url"]) for row in read_csv(self.registry_dir / "discovered_works.csv")],
            [(title, url)],
        )

    async def test_reference_collection_then_independent_web_assessment_keeps_original_and_work_id(self):
        title = "Starlit Parade"
        expected = 2_000_000 + zlib.crc32(b"starlitparade") % 1_000_000_000
        now = datetime.now(timezone.utc)
        statements = [
            "Moon Parade is the opening theme of Starlit Parade.",
            "The theme is performed by Glass Bird.",
            "The composer is Aya Sora.",
        ]
        original = f"{title} is a TV anime. " + " ".join(statements)
        source_id = "flyingdog_music"
        url = "https://www.jvcmusic.co.jp/flyingdog/starlit-parade"
        page = ReadResult(source_id, f"{title} opening theme announced", url, original,
                          "firecrawl", "firecrawl_scrape", redirects_verified=True)
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.execute("PRAGMA foreign_keys=ON")
        store = SQLiteRagStore(connection)
        registry = load_source_registry()
        reader = SimpleNamespace(
            list_tools=AsyncMock(return_value={"firecrawl_search", "firecrawl_scrape"}),
            search=AsyncMock(return_value=(SearchHit(source_id, page.title, url, "firecrawl", "firecrawl_search"),)),
            read=AsyncMock(return_value=page),
        )
        model = SimpleNamespace(generate_json=AsyncMock(return_value=new_anime_response(title)))
        await collect_from_source(
            CollectionRequest(source_id, RagContentType.MUSIC, EvidenceType.OFFICIAL_FACT, title, "en"),
            registry=registry, adapter=reader, repository=store,
            work_catalog=LLMWorkCatalogResolver(self.catalog(), model, kitsu_lookup=AsyncMock(return_value=())),
        )
        document = connection.execute(
            "SELECT d.raw_text, d.url, l.menu_work_id, l.status FROM rag_document d "
            "JOIN rag_work_link l USING (document_id)"
        ).fetchone()
        self.assertIsNotNone(document)
        self.assertEqual(tuple(document), (original, url, expected, WorkLinkStatus.MATCHED.value))
        self.assertEqual(connection.execute(
            "SELECT COUNT(*) FROM rag_queue_job WHERE queue_name='generation'"
        ).fetchone()[0], 0)

        article_url = "https://example.org/interviews/starlit-parade"
        article = original + " The interview was recorded at the studio."
        hit = WebCandidateHit(title, article_url, original)
        adapter = SimpleNamespace(
            search_web=AsyncMock(return_value=(hit,)),
            read_web_candidate=AsyncMock(return_value=WebCandidatePage(hit, title, article_url, article, "en", None, now)),
        )
        await enqueue_web_discovery(store, WebDiscoveryRequest(
            query=title, content_type=RagContentType.MUSIC, menu_work_id=expected,
            expected_work_titles=(title,),
        ))
        self.assertTrue(await WebDiscoveryWorker(store, adapter, registry=registry).run_once())
        verifier = SimpleNamespace(generate_json=AsyncMock(return_value={"claims": [
            {"claim": claim, "status": "supported", "evidence_refs": ["R1"]} for claim in statements
        ]}))
        assess = WebCandidateAssessmentWorker(
            store, adapter, self.catalog(), DatabaseEvidenceRetriever(store), verifier, registry=registry,
        )
        self.assertTrue(await assess.run_once())
        verifier.generate_json.assert_awaited_once()
        generation = await store.claim(QueueName.GENERATION, now=datetime.now(timezone.utc))
        self.assertIsNotNone(generation)
        self.assertEqual(tuple(generation.payload["menu_work_ids"]), (expected,))
        self.assertEqual(generation.payload["web_candidate"]["url"], article_url)
        self.assertEqual(generation.payload["web_candidate"]["original_text"], article)
        self.assertEqual(len(generation.payload["web_candidate"]["verified_claims"]), 3)
