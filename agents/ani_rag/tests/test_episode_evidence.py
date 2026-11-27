import math
import sqlite3
import struct
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ani_rag.episode_evidence import episode_reference_matches, episode_targets, is_episode_guide_title
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import DatabaseEvidenceRetriever, RetrievalRequest, VectorEvidenceRetriever, chunk_document
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore


class EpisodeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_targets_require_a_numbered_entry_and_preserve_season(self):
        self.assertEqual(episode_targets("작품/에피소드 가이드", "## 1기\n- 11화 – 전학생\n## 2기\n- 11화 – 기말고사"), ((1, 11), (2, 11)))
        self.assertEqual(episode_targets("第2期 第12話", ""), ((2, 12),))
        self.assertEqual(episode_targets("第12話", ""), ((None, 12),))
        self.assertEqual(episode_targets("A Silent Voice: Episode Guide & Ratings", "The film was released in 2016."), ())
        for title in ("전체 12화", "全12話", "Number of episodes: 12", "2015년 공개"):
            self.assertFalse(is_episode_guide_title(title))

    def test_source_must_contain_episode_material_in_the_requested_scope(self):
        targets = ((1, 11),)
        recap = "A new transfer student unexpectedly joins the class and challenges their teacher."
        self.assertTrue(episode_reference_matches(targets, "Series Season 1 Episode 11", recap))
        self.assertTrue(episode_reference_matches(targets, "Series", "Season 1\nEpisode 11\n" + recap))
        for title, body in (
            ("Series Season 2 Episode 11", recap),
            ("Series Season 1 Episode 1", "Unlike Episode 11, " + recap),
            ("Series Season 1 Episode 12", "Unlike Episode 11, " + recap),
            ("Series the Movie", "Season 1 Episode 11\n" + recap),
            ("Series Season 1", "[Series – Episode 11](https://example.org/review/episode-11)(Mar 28, 2015)"),
            ("Series Season 1", "총 11화\n" + recap),
        ):
            with self.subTest(title=title, body=body):
                self.assertFalse(episode_reference_matches(targets, title, body))

    async def test_episode_title_is_considered_before_candidate_limit(self):
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        connection.executescript(schema.read_text(encoding="utf-8"))
        store = SQLiteRagStore(connection)
        source = next(s for s in load_source_registry().approved_sources if s.source_id == "anime_corner")
        await store.register_source(source)
        for key, title, work in (
            ("a-source-itself", "Series Season 1 Episode 11", 17),
            ("b-other-work", "Series Season 1 Episode 11", 18),
            ("c-other-season", "Series Season 2 Episode 11", 17),
            ("d-unrelated", "Series overview", 17),
            ("e-right-episode", "Series Season 1 Episode 11", 17),
        ):
            document = EvidenceDocument(
                document_id=key, content_type=RagContentType.GUIDE, source_id=source.source_id,
                source_url=f"https://news.test/{key}", title=title, menu_work_ids=(work,),
                body="A transfer student challenges the teacher during class.", retrieved_at=datetime.now(timezone.utc),
                language="en", evidence_type=EvidenceType.REFERENCE_FACT,
                collection_method=CollectionMethod.MANUAL_IMPORT, work_link_status=WorkLinkStatus.MATCHED,
            )
            await store.upsert_and_schedule_generation(document, schedule_generation=False)
        request = RetrievalRequest("Series", RagContentType.GUIDE, (17,), (source.source_id,),
                                   exclude_source_urls=("https://news.test/a-source-itself",),
                                   top_k=1, candidate_limit=1, episode_targets=((1, 11),))
        self.assertEqual([d.document_id for d in await store.fetch_candidates(request)], ["e-right-episode"])
        result = await DatabaseEvidenceRetriever(store).retrieve(replace(request, candidate_limit=10))
        self.assertEqual([c.evidence_id for c in result.chunks], ["e-right-episode"])

        model = AsyncMock()
        model.embed_query.return_value = (1.,) + (0.,) * 1023
        vector_retriever = VectorEvidenceRetriever(store, connection, model)
        wide = replace(request, candidate_limit=10, min_retrieval_score=.5)
        by_id = {d.document_id: d for d in await store.fetch_candidates(wide)}
        def indexed(keys):
            rows = []
            for key in keys:
                chunk = chunk_document(by_id[key])[0]
                cosine = .65 if key == "e-right-episode" else .95
                vector = (cosine, math.sqrt(1 - cosine ** 2)) + (0.,) * 1022
                rows.append({**vars(chunk), "document_id": key, "chunk_text": chunk.text,
                             "content_type": chunk.content_type.value, "published_at": None,
                             "vector_f32": struct.pack("<1024f", *vector)})
            return rows
        with patch.object(vector_retriever, "_fetch_index_chunks", side_effect=indexed):
            result = await vector_retriever.retrieve(wide)
            self.assertEqual([c.evidence_id for c in result.chunks], ["e-right-episode"])
            self.assertAlmostEqual(result.chunks[0].score, .65)
            above = await vector_retriever.retrieve(replace(wide, min_retrieval_score=.8))
            self.assertNotEqual(above.chunks[0].evidence_id, "e-right-episode")
