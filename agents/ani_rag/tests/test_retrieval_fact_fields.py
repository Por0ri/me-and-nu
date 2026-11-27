import math
import sqlite3
import struct
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ani_rag.fact_subject import Edition, _edition_ids, _character_titles
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import (
    DatabaseEvidenceRetriever, RetrievalRequest, VectorEvidenceRetriever, chunk_document,
    requested_dictionary_fields,
)
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore


class FactFieldRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.db)
        source = next(source for source in load_source_registry().approved_sources if source.source_id == "anime_corner")
        await self.store.register_source(source)
        self.docs = {}
        for index, (key, field, work) in enumerate([
            ("right", "episodes", 1), ("newer", "staff", 1), ("unrelated", "episodes", 20),
        ]):
            doc = EvidenceDocument(
                document_id=key, content_type=RagContentType.GUIDE, source_id=source.source_id,
                source_url=f"https://example.org/{key}",
                title="Attack on Titan (TV) - Anime News Network" if key == "newer" else f"架空 — {field}",
                body="몇 화야? 등장인물 이야기" if key == "newer" else "全24話",
                retrieved_at=datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
                evidence_type=EvidenceType.REFERENCE_FACT, collection_method=CollectionMethod.MANUAL_IMPORT,
                language="ja", work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work,),
                metadata=({"dataset": "menu_dict", "menu_dict_field": field, "menu_dict_entity_id": "2327"}
                          if key != "newer" else {}),
            )
            self.docs[key] = doc
            await self.store.upsert_and_schedule_generation(doc, schedule_generation=False)
        self.request = RetrievalRequest("몇 화야?", RagContentType.GUIDE, menu_work_ids=(1,),
                                        candidate_limit=1, top_k=1)

    def test_focused_questions_and_airtime(self):
        for query, expected in (
            ("몇 부작이야?", ("episodes",)), ("How many episodes are there?", ("episodes",)),
            ("한 편 몇 분이야?", ("duration_min",)), ("방영 시간", ("duration_min",)),
            ("방영 시간은 몇 시야?", ()), ("기억나는 몇 화에서 나왔어?", ()),
            ("누가 한국어 성우야?", ("voice_actor_ko",)), ("Japanese voice actor", ("voice_actor", "jp_cast")),
            ("작품의 주요 인물", ()),
        ):
            with self.subTest(query=query):
                self.assertEqual(requested_dictionary_fields(replace(self.request, query=query)), expected)
        article = replace(self.request, query="등장인물에 대한 긴 본문 " * 30, subject_title="첫 시즌은 몇 화야?")
        self.assertEqual(requested_dictionary_fields(article), ("episodes",))
        self.assertEqual(requested_dictionary_fields(replace(self.request, menu_work_ids=())), ())

    async def test_before_limit_and_lexical_reranking(self):
        for limit in (1, 2):
            result = await DatabaseEvidenceRetriever(self.store).retrieve(replace(self.request, candidate_limit=limit))
            self.assertEqual(result.chunks[0].evidence_id, "right")
        seeded = replace(self.request, seed_evidence_ids=("newer",))
        self.assertEqual((await self.store.fetch_candidates(seeded))[0].document_id, "newer")
        self.assertEqual((await self.store.fetch_candidates(replace(self.request, semantic_candidate_scan=True)))[0].document_id, "right")

    async def test_vector_reranking_preserves_original_scores_and_floor(self):
        model = AsyncMock()
        model.embed_query.return_value = (1.0, 0.0) + (0.0,) * 1022
        index = sqlite3.connect(":memory:")
        self.addCleanup(index.close)
        retriever = VectorEvidenceRetriever(self.store, index, model)
        rows = []
        for key, cosine in (("right", 0.6), ("newer", 0.9)):
            doc = self.docs[key]
            chunk = chunk_document(doc)[0]
            vector = (cosine, math.sqrt(1.0 - cosine**2)) + (0.0,) * 1022
            rows.append({**vars(chunk), "document_id": key, "chunk_text": chunk.text, "published_at": None,
                         "content_type": doc.content_type.value, "vector_f32": struct.pack("<1024f", *vector)})
        request = replace(self.request, candidate_limit=2)
        with patch.object(retriever, "_fetch_index_chunks", return_value=rows):
            result = await retriever.retrieve(request)
            self.assertEqual(result.chunks[0].evidence_id, "right")
            self.assertAlmostEqual(result.chunks[0].score, 0.6)
            result = await retriever.retrieve(replace(request, min_retrieval_score=0.8))
            self.assertEqual(result.chunks[0].evidence_id, "newer")

    async def test_verified_chunk_precedes_top_k_only_if_source_text_scope_and_score_still_match(self):
        model = AsyncMock()
        model.embed_query.return_value = (1.,) + (0.,) * 1023
        retriever = VectorEvidenceRetriever(self.store, self.db, model)
        chunks = [chunk_document(self.docs[k])[0] for k in ("right", "newer")]
        rows = []
        for chunk, cosine in zip(chunks, (.95, .60), strict=True):
            vector = (cosine, math.sqrt(1 - cosine**2)) + (0.,) * 1022
            rows.append({**vars(chunk), "document_id": chunk.evidence_id, "chunk_text": chunk.text,
                         "published_at": None, "content_type": chunk.content_type.value,
                         "vector_f32": struct.pack("<1024f", *vector)})
        request = replace(self.request, query="catalog", candidate_limit=2, top_k=1,
                          source_ids=("anime_corner",), seed_evidence_ids=("newer",),
                          corroborating_chunks=(chunks[1].chunk_id,))
        with patch.object(retriever, "_fetch_index_chunks", return_value=rows):
            eligible = await retriever.retrieve(request)
            self.assertEqual(eligible.chunks[0].chunk_id, chunks[1].chunk_id)
            self.assertAlmostEqual(eligible.chunks[0].score, .60)
            for changed in (replace(request, min_retrieval_score=.8), replace(request, menu_work_ids=(20,)),
                            replace(request, source_ids=("wikidata",)),
                            replace(request, exclude_source_urls=(chunks[1].source_url,))):
                self.assertNotIn(chunks[1].chunk_id, [c.chunk_id for c in (await retriever.retrieve(changed)).chunks])
            self.db.execute("UPDATE rag_document SET raw_text='revised claims' WHERE document_id='newer'")
            self.assertNotIn(chunks[1].chunk_id, [c.chunk_id for c in (await retriever.retrieve(request)).chunks])

    def test_edition_and_character_specificity(self):
        editions = (
            Edition("series", ("작품", "Sample"), "TV", 2010),
            Edition("season2", ("작품 2기", "Sample Season 2"), "TV", 2015),
            Edition("movie", ("극장판 작품 겨울편", "Sample Winter"), "MOVIE", 2017),
            Edition("tv_cut", ("작품 겨울편", "Sample Winter (TV)"), "TV", 2018),
        )
        for query, expected in (
            ("작품은 몇 화?", {"series"}), ("작품 한 화의 방영 시간", {"series"}),
            ("작품 2기 몇 화?", {"season2"}), ("작품 3기 몇 화?", set()),
            ("작품 모든 시즌 합치면 몇 화?", set()),
            ("작품 겨울편 러닝타임", set()), ("작품 겨울편 극장판 러닝타임", {"movie"}),
            ("작품 겨울편 TV판 러닝타임", {"tv_cut"}),
        ):
            fields = ("duration_min",) if any(term in query for term in ("시간", "러닝")) else ("episodes",)
            with self.subTest(query=query):
                self.assertEqual(_edition_ids(query, editions[0].names, editions, fields), expected)
        characters = {"Q1": {"가나다", "John Smith"}, "Q2": {"다른 이름", "Jane Doe"}}
        titles = _character_titles("작품 가나다 한국어 성우는 누구야?", ("작품",), characters, ("voice_actor_ko",))
        self.assertEqual(titles, {"가나다 — voice_actor_ko", "John Smith — voice_actor_ko"})
        self.assertEqual(_character_titles("작품 없는이름 성우", ("작품",), characters, ("voice_actor",)), set())
        self.assertIsNone(_character_titles("작품 성우는 누구야?", ("작품",),
                                            {**characters, "Q3": {"작품"}}, ("voice_actor",)))

    async def test_work_cast_is_language_scoped_and_returns_distinct_named_roles(self):
        for key, name, entity, field in (
            ("actor_source_a", "エレン・イェーガー", "36326", "voice_actor_ko"),
            ("actor_source_b", "エレン・イェーガー", "36326", "voice_actor_ko"),
            ("actor_other", "ミカサ・アッカーマン", "36327", "voice_actor_ko"),
            ("actor_ja", "ミカサ・アッカーマン", "36327", "voice_actor"),
        ):
            doc = replace(self.docs["right"], document_id=key, source_url=f"https://example.org/{key}",
                          title=f"{name} — {field}", body="Cast credit", metadata={
                              "dataset": "menu_dict", "menu_dict_field": field, "menu_dict_entity_id": entity,
                          })
            await self.store.upsert_and_schedule_generation(doc, schedule_generation=False)
        retriever = DatabaseEvidenceRetriever(self.store)
        request = replace(self.request, query="진격의 거인 한국어 더빙 성우는 누구야?", top_k=3, candidate_limit=6)
        cast = (await retriever.retrieve(request)).documents
        self.assertEqual({doc.metadata["menu_dict_entity_id"] for doc in cast}, {"36326", "36327"})
        self.assertEqual(len(cast), 2)
        self.assertTrue(all(doc.metadata["menu_dict_field"] == "voice_actor_ko" for doc in cast))
        cast = (await retriever.retrieve(replace(request, query="진격의 거인 일본 성우는 누구야?"))).documents
        self.assertEqual([doc.document_id for doc in cast], ["actor_ja"])
        cast = (await retriever.retrieve(replace(request, query="가짜이름의 한국어 성우는 누구야?"))).documents
        self.assertFalse(cast)

    async def test_general_candidate_limit_prioritises_topic_over_date_in_both_paths(self):
        for key, age, title, body in (
            ("old", 10, "음악 제작", "재즈 작곡가의 녹음 인터뷰"),
            ("recent", 0, "음악", "축전 소개"),
        ):
            doc = replace(self.docs["newer"], document_id=key, source_url=f"https://example.org/{key}",
                          title=title, body=body, retrieved_at=datetime.now(timezone.utc) - timedelta(days=age))
            await self.store.upsert_and_schedule_generation(doc, schedule_generation=False)
        for semantic in (False, True):
            request = replace(self.request, query="음악 제작 재즈 작곡가", semantic_candidate_scan=semantic)
            self.assertEqual((await self.store.fetch_candidates(request))[0].document_id, "old")
