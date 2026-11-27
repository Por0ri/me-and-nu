import math
import sqlite3
import struct
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ani_rag.article_credits import article_credits, chunk_credit_matches
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import RetrievalRequest, VectorEvidenceRetriever, chunk_document
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore


class ArticleCreditTests(unittest.IsolatedAsyncioTestCase):
    def test_only_take_names_from_article_credits(self):
        text = ("[Distributor](https://news.test/d) interviewed the director [Yuichiro Hayashi](https://news.test/h) "
                "and CG producer [Yusuke Tannawa](https://news.test/t).")
        credits = article_credits(RagContentType.PEOPLE, text)
        self.assertEqual([(c.role, c.name) for c in credits], [
            ("director", "Yuichiro Hayashi"), ("producer", "Yusuke Tannawa"),
        ])
        self.assertEqual(chunk_credit_matches(credits, "CG Producer: Yūsuke Tannawa"), 1)
        self.assertEqual(chunk_credit_matches(credits, "Featuring Yūsuke Tannawa"), 0)
        self.assertEqual(article_credits(RagContentType.MUSIC, text), ())
        self.assertEqual(article_credits(RagContentType.GUIDE, text), ())
        self.assertFalse(article_credits(RagContentType.PEOPLE,
                                         "Interview With Attack on Titan The Final Season Director, CG Producer Streamed"))
        music = 'Season 2 was composed by Hiroyuki Sawano. The opening theme is “Red Swan”.'
        credits = article_credits(RagContentType.MUSIC, music)
        self.assertEqual([(c.name, c.role) for c in credits], [
            ("Hiroyuki Sawano", "composer"), ("Red Swan", "theme"),
        ])

    async def test_one_embedding_and_no_promotion_past_scope_or_score(self):
        db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        store = SQLiteRagStore(db)
        source = next(s for s in load_source_registry().approved_sources if s.source_id == "anime_corner")
        await store.register_source(source)
        credits = article_credits(RagContentType.PEOPLE, "CG producer [Yusuke Tannawa](https://news.test/byline).")
        request = RetrievalRequest("Series staff", RagContentType.PEOPLE, (1,), (source.source_id,),
                                   exclude_source_urls=("https://news.test/article",), top_k=1, candidate_limit=5,
                                   min_retrieval_score=.5, article_credits=credits)
        rows = []
        for key, work, cosine in (("wrong-work", 2, .96), ("article", 1, .95), ("synopsis", 1, .90),
                                  ("credits", 1, .70), ("low-score", 1, .42)):
            doc = EvidenceDocument(
                document_id=key, content_type=RagContentType.GUIDE, source_id=source.source_id,
                source_url=f"https://news.test/{key}", title="Series", menu_work_ids=(work,),
                body="Original story." if key == "synopsis" else "CG Producer: Yūsuke Tannawa",
                retrieved_at=datetime.now(timezone.utc), language="en", evidence_type=EvidenceType.REFERENCE_FACT,
                collection_method=CollectionMethod.MANUAL_IMPORT, work_link_status=WorkLinkStatus.MATCHED,
            )
            await store.upsert_and_schedule_generation(doc, schedule_generation=False)
            chunk = chunk_document(doc)[0]
            vector = (cosine, math.sqrt(1 - cosine ** 2)) + (0.,) * 1022
            rows.append({**vars(chunk), "document_id": key, "chunk_text": chunk.text, "published_at": None,
                         "content_type": chunk.content_type.value, "vector_f32": struct.pack("<1024f", *vector)})
        model = AsyncMock()
        model.embed_query.return_value = (1.,) + (0.,) * 1023
        retriever = VectorEvidenceRetriever(store, db, model)
        with patch.object(retriever, "_fetch_index_chunks", return_value=rows):
            result = await retriever.retrieve(request)
            self.assertEqual([c.evidence_id for c in result.chunks], ["credits"])
            self.assertAlmostEqual(result.chunks[0].score, .70)
            model.embed_query.assert_awaited_once_with(request.query)
            for altered in (replace(request, min_retrieval_score=.80), replace(request, article_credits=())):
                self.assertEqual([c.evidence_id for c in (await retriever.retrieve(altered)).chunks], ["synopsis"])
            self.assertEqual((await retriever.retrieve(replace(request, source_ids=("unapproved",)))).chunks, ())
