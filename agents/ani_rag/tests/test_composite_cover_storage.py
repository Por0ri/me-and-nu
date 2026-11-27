from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

from ani_rag.agents.content_generation.renderer import RenderedCard
from ani_rag.content import CardDraft, CardLayout, ContentDraft, DuplicateContentError, ImageAsset, SupportedClaim
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import DatabaseEvidenceRetriever, RetrievalRequest
from ani_rag.settings import load_source_registry
from ani_rag.quality import validate_draft
from ani_rag.source_evidence import WebSourceWorkConflict, attach_web_quotes, web_quote_chunk_id
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository, SQLiteImageReader, store_primary_web_source
from ani_rag.image_ingest import WebImageSearchHit
from ani_rag.web_images import WebSearchImageReader


class CompositeCoverStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.conn.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.conn.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.conn)
        self.drafts = SQLiteDraftRepository(self.store)
        self.images = SQLiteImageReader(self.store)
        self.now = datetime.now(timezone.utc)
        self.source = "kitsu_anime_catalog"
        await self.store.register_source(next(
            source for source in load_source_registry().sources if source.source_id == self.source
        ))
        document = EvidenceDocument(
            document_id="reference", title="Cast interview", source_url="https://example.org/article",
            source_id=self.source, content_type=RagContentType.CHARACTER, evidence_type=EvidenceType.REFERENCE_FACT,
            body="The cast of the series shares their stories.", language="en",
            collection_method=CollectionMethod.MCP_READ, retrieved_at=self.now,
            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(1,),
        )
        await self.store.upsert_and_schedule_generation(document, schedule_generation=False)
        self.retrieval = await DatabaseEvidenceRetriever(self.store).retrieve(RetrievalRequest(
            query="Cast interview", content_type=document.content_type, seed_evidence_ids=("reference",), menu_work_ids=(1,),
        ))
        for identifier in ("a", "b", "c", "d", "e", "b-resized"):
            await self.images.register_asset(ImageAsset(
                asset_id=identifier, source_url=f"https://example.org/image/{identifier}", source_id=self.source,
                work_link_status="matched", menu_work_ids=(1,), rights_basis="permitted",
                rights_evidence_url="https://example.org/terms", local_path="fixture.png",
                source_native_id="b" if identifier == "b-resized" else identifier,
                image_role="character:large" if identifier == "b-resized" else "character:original",
                source_page_url=f"https://example.org/credits/{identifier}",
            ))

    async def save(self, key, primary, components=(), inside="d", web_source=None, revision_of=None):
        template = "cast_intro_grid" if components else "image_top_copy_bottom"
        cards = (
            CardDraft(key + "-cover", "cover", "Headline", "Body", (), primary,
                      layout=CardLayout(template=template), component_image_asset_ids=components),
            CardDraft(key + "-detail", "detail", "Detail", "Another body", (), inside),
        )
        retrieval = replace(self.retrieval, retrieval_id="retrieval-" + key)
        draft = ContentDraft(key, RagContentType.CHARACTER, key, cards, "Article", (),
                             ("reference",), (1,), retrieval.retrieval_id, self.now, "Credits",
                             retrieved_chunk_ids=tuple(chunk.chunk_id for chunk in retrieval.chunks),
                             ai_judgment_basis={"revision_of_draft_id": revision_of} if revision_of else {})
        plates = [RenderedCard(card.card_id, b"saved plate", {"headline": card.headline}, asdict(card.layout))
                  for card in cards]
        await self.drafts.save(draft, retrieval, plates, web_source=web_source)

    async def test_web_article_is_primary_and_reference_stays_independent(self):
        web_source = {
            "title": "Original cast interview", "url": "https://news.example.org/people/interview",
            "original_text": "Our interview with the cast of the series.", "language": "en",
            "fetched_at": self.now.isoformat(),
        }
        await self.save("new-web-result", "a", web_source=web_source)
        # An existing snapshot imported under an earlier document ID is still the same raw article.
        self.conn.execute("UPDATE rag_document SET document_id='prior-snapshot' WHERE url=?", (web_source["url"],))
        existing = await self.drafts.existing_web_snapshot(web_source)
        self.assertEqual(existing.document_id, "prior-snapshot")
        store_primary_web_source(self.conn, "new-web-result", "character", web_source, self.now.isoformat())
        rows = self.conn.execute(
            """SELECT s.source_url,s.is_primary,d.raw_text,typeof(s.raw_document_id),typeof(s.source_site_id)
               FROM rag_content_source s JOIN rag_document d USING(raw_document_id,source_site_id)
               WHERE draft_id='new-web-result' ORDER BY s.is_primary DESC"""
        ).fetchall()
        self.assertEqual(tuple(rows[0]), (web_source["url"], 1, web_source["original_text"], "integer", "integer"))
        self.assertEqual(tuple(rows[1])[:2], ("https://example.org/article", 0))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT document_id FROM rag_draft_evidence WHERE draft_id='new-web-result'"
        )], ["reference"])
        self.assertEqual(self.conn.execute(
            "SELECT is_collecting FROM rag_source WHERE source_id='web_discovery:news.example.org'"
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    async def test_revision_copy_loads_only_reader_text_from_the_same_work(self):
        await self.save("before", "a")
        previous = await self.drafts.revision_copy("before", (1,))
        self.assertEqual((previous["title"], previous["detail_body"]), ("before", "Article"))
        self.assertEqual([card["headline"] for card in previous["cards"]], ["Headline", "Detail"])
        with self.assertRaises(ValueError):
            await self.drafts.revision_copy("before", (2,))

    async def test_interview_claim_keeps_exact_source_and_speaker_through_review(self):
        title, url = "Meet the director", "https://example.org/interview"
        quote = "We rewrote the ending together."
        context = f"Director Ira Lee: {quote}"
        body = f"{quote}\nInterview transcript\n{context}"
        quote_id = web_quote_chunk_id(url, title, body, quote)
        verified = {"kind": "attributed_quote", "speaker": "Ira Lee", "claim": quote,
                    "attribution_excerpt": context, "chunk_ids": [quote_id]}
        source = {"url": url, "title": title, "fetched_at": self.now.isoformat(), "original_text": body,
                  "verified_claims": [verified]}
        retrieval = attach_web_quotes(self.retrieval, source)
        chunk = retrieval.chunks[-1]
        self.assertEqual((chunk.chunk_id, chunk.start_offset), (quote_id, body.rindex(quote)))
        with self.assertRaises(ValueError):
            attach_web_quotes(self.retrieval, {**source, "original_text": body + " (revised)"})
        claim = SupportedClaim("감독은 결말을 함께 다시 썼다고 말했다.", (chunk.evidence_id,), (quote_id,), "Ira Lee")
        cards = tuple(CardDraft(role, role, "Headline", "Body", (claim,), asset)
                      for role, asset in (("cover", "a"), ("ending", "b")))
        draft = ContentDraft("interview", RagContentType.CHARACTER, title, cards, "Body", (claim,),
                             ("reference", chunk.evidence_id), (1,), retrieval.retrieval_id, self.now, url,
                             retrieved_chunk_ids=(quote_id,),
                             ai_judgment_basis={"web_discovery_candidate": {"verified_claims": [verified]}})
        plates = [RenderedCard(c.card_id, b"plate", {"headline": c.headline}, asdict(c.layout)) for c in cards]
        await self.drafts.save(draft, retrieval, plates, web_source=source)
        reviewed = await self.drafts.load_for_review("interview")
        self.assertEqual(reviewed.draft.detail_claims, (claim,))
        self.assertEqual(reviewed.evidence[chunk.evidence_id].body, body)
        with self.assertRaises(WebSourceWorkConflict):
            attach_web_quotes(replace(self.retrieval, menu_work_ids=(2,)), source, await self.drafts.existing_web_snapshot(source))
        stored = reviewed.retrieved_chunks[quote_id]
        self.assertEqual((stored.source_url, stored.text, stored.menu_work_ids), (url, quote, (1,)))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rag_content_source WHERE draft_id='interview' AND is_primary=1").fetchone()[0], 1)
        self.assertFalse(self.conn.execute("PRAGMA foreign_key_check").fetchall())
        def issues_for(draft):
            return {i.code for i in validate_draft(draft, reviewed.evidence, reviewed.images, reviewed.retrieved_chunks)}
        self.assertFalse({"INTERVIEW_SPEAKER_UNCONFIRMED", "RETRIEVED_CHUNK_ORIGIN_MISMATCH",
                          "CITED_WORK_LINK_UNCONFIRMED"} & issues_for(reviewed.draft))
        changed = replace(reviewed.draft, detail_claims=(replace(claim, attribution="Somebody Else"),))
        self.assertIn("INTERVIEW_SPEAKER_UNCONFIRMED", issues_for(changed))
        request = RetrievalRequest(query=quote, content_type=RagContentType.CHARACTER,
                                   menu_work_ids=(1,), seed_evidence_ids=(chunk.evidence_id,))
        self.assertNotIn(chunk.evidence_id, {d.document_id for d in await self.store.fetch_candidates(request)})

    async def test_round_trip_and_reordered_originals(self):
        await self.save("first", "a", ("c", "b"))
        context = await self.drafts.load_for_review("first")
        self.assertEqual(context.draft.cards[0].source_asset_ids, ("a", "c", "b"))
        self.assertEqual(context.draft.cards[0].layout.template, "cast_intro_grid")
        self.assertEqual(set(context.images), {"a", "b", "c", "d"})
        self.assertEqual(context.images["b"].source_page_url, "https://example.org/credits/b")
        candidates = {image.asset_id: image for image in await self.images.candidates_for(self.retrieval.documents)}
        self.assertTrue(candidates["a"].used_as_cover_before)
        self.assertFalse(candidates["c"].used_as_cover_before)
        self.assertEqual(candidates["c"].recent_use_count, 1)

        # All sources are the same, although crop/size, primary, and order differ.
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("reordered", "b-resized", ("a", "c"))

        # It is fine to use the original cover on an inside page in another article.
        await self.save("second", "e", inside="a")
        self.assertEqual((await self.drafts.load_for_review("second")).draft.cards[0].source_asset_ids, ("e",))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rag_retrieval").fetchone()[0], 2)

    async def test_component_variant_cannot_reappear_in_same_deck(self):
        with self.assertRaisesRegex(ValueError, "reuse the same original"):
            await self.save("shared", "a", ("b", "c"), inside="b-resized")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rag_content_draft").fetchone()[0], 0)

    async def test_saved_single_cover_blocks_new_main_image_in_another_size(self):
        await self.save("single", "b")
        # Simulate a database populated before the component migration existed.
        self.conn.executescript("DROP VIEW rag_card_image_source; DROP TABLE rag_card_image_component;")
        migration = Path(__file__).resolve().parents[1] / "migrations/0007_card_image_components.sql"
        self.conn.executescript(migration.read_text(encoding="utf-8"))
        self.assertEqual((await self.drafts.load_for_review("single")).draft.cards[0].source_asset_ids, ("b",))
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("duplicate", "b-resized", ("a", "c"))

    async def test_cross_site_crop_is_cover_only_duplicate_and_available_for_own_revision(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        artwork = Image.new("RGB", (180, 160), "navy")
        draw = ImageDraw.Draw(artwork)
        draw.ellipse((40, 12, 116, 100), fill="gold")
        draw.polygon([(20, 130), (85, 43), (143, 147)], fill="crimson")
        original, cropped = folder / "poster.png", folder / "resized.jpg"
        artwork.save(original)
        artwork.crop((20, 0, 160, 160)).resize((210, 240)).save(cropped, quality=82)
        with self.conn:
            self.conn.executemany("UPDATE rag_image_asset SET local_path=? WHERE asset_id=?",
                                  [(str(original), "a"), (str(cropped), "e")])
        await self.save("first", "a")
        candidates = {a.asset_id: a for a in await self.images.candidates_for(self.retrieval.documents)}
        self.assertTrue(candidates["e"].used_as_cover_before)
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("another", "e")
        await self.save("different-cover", "b", inside="e")

        # A new, not-yet-registered download must receive the same flag.
        adapter = AsyncMock()
        adapter.search_images.return_value = [WebImageSearchHit("https://another.com/art.jpg",
                                                               "https://another.com/page", "Art")]
        web = WebSearchImageReader(self.images, adapter, asset_dir=folder)
        self.addCleanup(web.discard_staged)
        variant = replace(candidates["e"], asset_id="live", source_url="https://another.com/art.jpg",
                          content_sha256="a-new-web-hash", used_as_cover_before=False)
        with patch("ani_rag.web_images._download_candidate", return_value=variant):
            staged = await web.search_for_generation(self.retrieval.documents, query="art",
                                                      content_type=RagContentType.CHARACTER)
        self.assertTrue(staged[0].used_as_cover_before)
        await self.drafts.set_status("first", "revision_requested")
        pool = await self.images.candidates_for(self.retrieval.documents, revision_of="first")
        self.assertFalse(next(a for a in pool if a.asset_id == "e").used_as_cover_before)
        await self.save("first-corrected", "e", revision_of="first")
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("new-content", "a")

    async def test_legacy_and_current_cdn_urls_share_cover_identity(self):
        with self.conn:
            self.conn.executemany(
                """UPDATE rag_image_asset SET source_native_id=?, source_url=?, content_sha256=?, width=?, height=?
                   WHERE asset_id=?""",
                (("legacy-import-hash", "https://media.kitsu.io/characters/images/44/small.jpg", "old", 100, 100, "b"),
                 ("44", "https://media.kitsu.app/character/44/image/large.jpg", "current", 600, 600, "b-resized")),
            )
        await self.save("legacy", "b", ("a", "c"))
        candidates = {image.asset_id: image for image in await self.images.candidates_for(self.retrieval.documents)}
        self.assertNotIn("b", candidates)
        self.assertTrue(candidates["b-resized"].used_as_cover_before)
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("new", "b-resized")
        with self.assertRaisesRegex(DuplicateContentError, "cover_image_already_used"):
            await self.save("reordered", "c", ("a", "b-resized"))
