"""Exercise subject-bound cover selection with no external services."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ani_rag.content import CardDraft, ImageAsset, SupportedClaim
from ani_rag.cover_design import cast_identity, is_work_poster, plan_cover, select_cast_detail
from ani_rag.model_services import EvidenceBoundGenerationService
from ani_rag.models import CollectionMethod, CurationContext, EvidenceDocument, EvidenceExcerpt, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.retrieval import EvidenceChunk, RetrievedEvidence
from ani_rag.workers import GenerationWorker
from ani_rag.agents.content_generation.layout_director import ImageCopyMismatchError


def asset(key, work=1, role="artwork", **kwargs):
    return ImageAsset(key, f"https://images.example/{key}", "kitsu_anime_catalog", "matched",
                      (work,), None, None, f"{key}.png", image_role=role, **kwargs)


def evidence(kind, works, characters=False):
    now = datetime.now(timezone.utc)
    docs, chunks = [], []
    for index, work in enumerate(works, start=1):
        doc = EvidenceDocument(
            f"doc{index}", kind, f"Title {work}", f"https://example.org/{index}",
            "kitsu_anime_catalog", now, EvidenceType.REFERENCE_FACT, CollectionMethod.MANUAL_IMPORT,
            "en", body="Sample", menu_work_ids=(work,), work_link_status=WorkLinkStatus.MATCHED,
        )
        chunk = EvidenceChunk(f"chunk{index}", doc.document_id, kind, doc.title,
                              "Anime character role: main" if characters else f"Work: Title {work}",
                              f"kitsu:characters/{index}/Name {index}" if characters else "body", "body", 0, 0,
                              20, doc.source_id, doc.source_url, "en", None, (work,))
        docs.append(doc)
        chunks.append(chunk)
    return RetrievedEvidence("r", tuple(docs), tuple(chunks), "query", kind,
                             tuple(dict.fromkeys(works)), (docs[0].document_id,), now)


def cards_for(keys, retrieval):
    return [CardDraft(
        f"card{index}", "cover" if index == 0 else "detail", "headline", "body",
        (SupportedClaim("fact", (chunk.evidence_id,), (chunk.chunk_id,)),), key,
    ) for index, (key, chunk) in enumerate(zip(keys, retrieval.chunks))]


class CoverDesignTests(unittest.TestCase):
    def test_cast_uses_only_cited_leads_excludes_inner_art_and_size_variants(self):
        retrieval = evidence(RagContentType.CHARACTER, [1, 1, 1, 1], characters=True)
        portraits = [asset(f"p{i}", role="character:main:original", source_native_id=str(i))
                     for i in range(1, 6)]
        assets = {a.asset_id: a for a in (*portraits, asset("inner1"), asset("inner2"), asset("p5small",
                  role="character:main:small", source_native_id="5"))}
        cards = cards_for(["p1", "inner1", "inner2", "p4"], retrieval)
        cover = plan_cover(cards, "cast_intro_grid", retrieval, assets, None)
        self.assertEqual(cover.layout.template, "cast_intro_grid")
        self.assertEqual(set(cover.source_asset_ids), {"p1", "p2", "p3"})
        assets["p2"] = replace(assets["p2"], used_as_cover_before=True)
        self.assertIn("p2", plan_cover(cards, "cast_intro_grid", retrieval, assets, None).component_image_asset_ids)
        assets["p2"] = replace(assets["p2"], menu_work_ids=(2,))
        cover = plan_cover(cards, "cast_intro_grid", retrieval, assets, None)
        self.assertEqual(cover.layout.template, "image_top_copy_bottom")
        self.assertEqual(cover.source_asset_ids, ("p1",))

    def test_only_four_article_recommendations_get_the_collage(self):
        retrieval = evidence(RagContentType.CURATION, [1, 2, 3, 4, 5])
        sources = [asset(f"poster{i}", i, "catalog_poster", alt_text=f"Title {i}") for i in range(1, 7)]
        sources += [asset(f"still{i}", i) for i in range(2, 6)]
        assets = {a.asset_id: a for a in sources}
        cards = cards_for(["poster1", "still2", "still3", "still4", "still5"], retrieval)
        source = {"title": "Recommendations for fans of Title 1",
                  "original_text": "Try Title 2, Title 3, Title 4, and Title 5.",
                  "verified_claims": [{"claim": "This article recommends Title 2, Title 3, Title 4 and Title 5."}]}
        cover = plan_cover(cards, "fan_recommendation", retrieval, assets, source)
        self.assertEqual(cover.source_asset_ids, tuple(f"poster{i}" for i in range(1, 6)))
        missing_title = {**source, "verified_claims": [{"claim": "It recommends Title 2, Title 3, Title 4 and Title 50."},
                                                       {"claim": "Title 5 celebrated its anniversary yesterday."}]}
        self.assertEqual(plan_cover(cards, "fan_recommendation", retrieval, assets, missing_title)
                         .source_asset_ids, ("poster1",))
        assets["poster3"] = replace(assets["poster3"], content_sha256="duplicate")
        assets["still3"] = replace(assets["still3"], content_sha256="duplicate")
        self.assertEqual(plan_cover(cards, "fan_recommendation", retrieval, assets, source)
                         .source_asset_ids, ("poster1",))

    def test_cast_detail_repairs_wrong_portrait_using_its_own_cited_chunk(self):
        retrieval = evidence(RagContentType.CHARACTER, [1, 1, 1], characters=True)
        assets = {f"p{i}": asset(f"p{i}", role="character:main:large", source_native_id=str(i))
                  for i in range(1, 4)}
        page = cards_for(["p1", "p3"], retrieval)[1]
        chunks = {c.chunk_id: c for c in retrieval.chunks}
        page = select_cast_detail(page, chunks, assets, set(), set())
        self.assertEqual(page.image_asset_id, "p2")
        chunks["chunk2"] = replace(chunks["chunk2"], menu_work_ids=(2,))
        with self.assertRaisesRegex(ValueError, "portrait"):
            select_cast_detail(page, chunks, assets, set(), set())

    def test_legacy_cdn_art_uses_record_identity_and_confirmed_main_role(self):
        retrieval = evidence(RagContentType.CHARACTER, [1], characters=True)
        chunk = replace(retrieval.chunks[0], locator="kitsu:characters/1/Name 1:0-150")
        legacy = replace(asset("hashed", role="character_art:context", source_native_id="legacy-id"),
                         source_url="https://media.kitsu.app/character/1/image/large.jpg")
        self.assertEqual(cast_identity(legacy, {chunk.chunk_id: chunk}), {("kitsu", "1"), ("name", "name 1")})
        supporting = replace(chunk, text="Anime character role: supporting")
        self.assertFalse(cast_identity(legacy, {chunk.chunk_id: supporting}))
        mislabeled_banner = replace(legacy, image_role="catalog_poster:context",
                                    source_url="https://media.kitsu.app/anime/1/cover_image/large.jpg")
        self.assertFalse(is_work_poster(mislabeled_banner))

    def test_new_guide_prefers_actual_work_poster_only_on_cover(self):
        retrieval = evidence(RagContentType.GUIDE, [1, 1])
        sources = [asset("banner", role="cover:original"), asset("still"),
                   asset("poster", role="poster:original"), asset("another", work=2, role="poster")]
        cards = cards_for(["banner", "still"], retrieval)
        assets = {a.asset_id: a for a in sources}
        cover = plan_cover(cards, "beginner_guide", retrieval, assets, None)
        self.assertEqual((cover.layout.template, cover.image_asset_id), ("beginner_guide", "poster"))
        self.assertIs(plan_cover(cards, "fan_recommendation", retrieval, assets, None), cards[0])


class GenerationContractTests(unittest.IsolatedAsyncioTestCase):
    def web_generation_worker(self, sources, *, revision=False):
        result = evidence(RagContentType.GUIDE, [1])
        chunk = replace(result.chunks[0], score=1.0)
        web = {"url": "https://example.org/interview", "title": "Interview",
               "corroborating_chunk_ids": [chunk.chunk_id], "verified_claims": [
                   {"kind": "rag_core", "standalone": True, "claim": "One fact", "chunk_ids": [chunk.chunk_id]}]}
        payload = {"content_type": "guide", "query": "Title 1", "menu_work_ids": [1],
                   "source_ids": [chunk.source_id], "seed_evidence_ids": [chunk.evidence_id], "web_candidate": web}
        if revision:
            payload.update(revision_count=1, revision_of_draft_id="earlier", revision_instructions=["제목 수정"])
        queues = AsyncMock()
        queues.claim.return_value = QueueItem("job", QueueName.GENERATION, QueueStatus.RUNNING, payload=payload)
        retriever = AsyncMock()
        retriever.retrieve.return_value = replace(result, chunks=(chunk,))
        images = SimpleNamespace(candidates_for=AsyncMock(return_value=sources),
                                 search_for_generation=AsyncMock(return_value=()),
                                 get_many=AsyncMock(return_value={image.asset_id: image for image in sources}))
        drafts = AsyncMock()
        drafts.recent_topics.return_value = []
        drafts.revision_copy.return_value = {"title": "수정 전"}
        drafts.find_duplicate_topic.return_value = None
        model = AsyncMock()
        worker = GenerationWorker(queues, None, retriever, images, drafts, EvidenceBoundGenerationService(model),
                                  registry=SimpleNamespace(reference_source_ids=lambda _: [chunk.source_id]))
        return worker, model

    async def test_two_originals_reach_review_including_after_targeted_revision_image_retry(self):
        claims = [{"text": "근거", "evidence_refs": ["C1"], "attribution": None}]
        cover = asset("cover")
        ending = asset("ending", alt_text="Anya Forger solo portrait")
        replacement = asset("new-ending", alt_text="Loid Forger portrait")
        deck = {"title": "제목", "summary": "요약", "keywords": ["소개"], "editorial_angle": "질문",
                "cover_mode": "regular", "ending_mode": "ending",
                "cards": [{"role": role, "image_asset_id": image.asset_id, "headline": "포저 가족의 세계", "body": "설명",
                           "claims": claims} for role, image in (("cover", cover), ("ending", ending))]}
        article = {"body": "상세 본문", "claims": claims}
        for revision in (False, True):
            with self.subTest(revision=revision):
                worker, model = self.web_generation_worker((cover, ending), revision=revision)
                attempt_deck = final_deck = deck
                if revision:
                    loid_page = {**deck["cards"][1], "headline": "로이드 포저"}
                    attempt_deck = {**deck, "cards": [deck["cards"][0], loid_page]}
                    final_deck = {**deck, "cards": [deck["cards"][0], {**loid_page, "image_asset_id": replacement.asset_id}]}
                    worker.images.search_for_generation.side_effect = [(), (replacement,)]
                    worker.images.get_many.return_value = {img.asset_id: img for img in (cover, replacement)}
                model.generate_json.side_effect = [attempt_deck, final_deck, article] if revision else [deck, article]

                def layout(_, cards, assets):
                    if revision and cards[-1].image_asset_id == ending.asset_id:
                        raise ImageCopyMismatchError("Anya-only portrait on a Loid feature",
                                                     image_asset_id=ending.asset_id, headline=cards[-1].headline,
                                                     search_subject="Spy × Family Loid Forger")
                    return cards

                with patch("ani_rag.model_services.direct_card_layouts", side_effect=layout), \
                     patch("ani_rag.workers.render_text_free_cards", return_value=()):
                    self.assertTrue(await worker.run_once())
                worker.drafts.save.assert_awaited_once()
                draft = worker.drafts.save.await_args.args[0]
                self.assertEqual([card.role for card in draft.cards], ["cover", "ending"])
                self.assertEqual(draft.cards[1].image_asset_id, replacement.asset_id if revision else ending.asset_id)
                self.assertEqual(worker.queues.enqueue.await_args.args[0].queue, QueueName.REVIEW)
                self.assertEqual(worker.queues.save.await_args.args[0].status, QueueStatus.SUCCEEDED)
                self.assertEqual(model.generate_json.await_args_list[0].kwargs["schema"]["properties"]["cards"]["maxItems"], 2)
                if revision:
                    self.assertEqual(worker.images.search_for_generation.await_args.kwargs["query"],
                                     "Spy × Family Loid Forger")
                    self.assertEqual(worker.images.search_for_generation.await_count, 2)

    async def test_one_original_including_size_variants_stops_before_model_and_review(self):
        poster = asset("poster", role="poster:original", source_native_id="1")
        thumbnail = asset("small", role="poster:small", source_native_id="1")
        for sources in ((poster,), (poster, thumbnail)):
            with self.subTest(assets=len(sources)):
                worker, model = self.web_generation_worker(sources)
                self.assertTrue(await worker.run_once())
                self.assertEqual(worker.queues.save.await_args.args[0].status, QueueStatus.BLOCKED)
                self.assertEqual(worker.queues.save.await_args.args[0].last_error,
                                 "waiting_for_distinct_work_linked_image_assets")
                worker.queues.enqueue.assert_not_awaited()
                with self.assertRaisesRegex(ValueError, "at least 2 distinct"):
                    await worker.generation.generate(evidence(RagContentType.GUIDE, [1]), image_candidates=sources)
                model.generate_json.assert_not_awaited()

    async def test_image_revision_excludes_rejected_originals_and_searches_for_the_intended_subject(self):
        rejected = asset("other-season", role="poster:original", source_native_id="101")
        variant = asset("other-season-thumb", role="poster:small", source_native_id="101")
        wrong = asset("wrong-portrait", content_sha256="badportrait")
        repeated = asset("wrong-portrait-mirror", content_sha256=wrong.content_sha256)
        cover, spare = asset("cover"), asset("spare")
        worker, _ = self.web_generation_worker((cover, rejected, variant, wrong, spare), revision=True)
        reviewed = {"asset_id": rejected.asset_id, "source_id": rejected.source_id,
                    "source_native_id": rejected.source_native_id, "image_role": rejected.image_role,
                    "source_url": rejected.source_url, "search_subject": "Spy × Family Season 2"}
        job = worker.queues.claim.return_value
        worker.queues.claim.return_value = replace(job, payload={**job.payload, "review_rejected_images": [reviewed]})
        worker.images.search_for_generation.side_effect = [(), (variant, repeated), RuntimeError("offline")]
        wrong_subject = ImageCopyMismatchError("Anya-only portrait", image_asset_id=wrong.asset_id,
                                              headline="로이드 소개", search_subject="Spy × Family Loid Forger")
        no_spare = ImageCopyMismatchError("Unrelated poster", image_asset_id=spare.asset_id,
                                         headline="로이드 소개", search_subject="Spy × Family Loid Forger")
        worker.generation.generate = AsyncMock(side_effect=[wrong_subject, no_spare])

        self.assertTrue(await worker.run_once())
        calls = worker.generation.generate.await_args_list
        self.assertEqual([{a.asset_id for a in call.kwargs["image_candidates"]} for call in calls],
                         [{"cover", "wrong-portrait", "spare"}, {"cover", "spare"}])
        self.assertIn("로이드 소개", calls[1].kwargs["revision_instructions"][-1])
        self.assertEqual([call.kwargs["query"] for call in worker.images.search_for_generation.await_args_list],
                         ["Spy × Family Season 2", "Spy × Family Loid Forger", "Spy × Family Loid Forger"])
        for call in (worker.images.candidates_for.await_args, *worker.images.search_for_generation.await_args_list):
            self.assertEqual(call.kwargs["revision_of"], "earlier")
        self.assertEqual(worker.queues.save.await_args.args[0].status, QueueStatus.BLOCKED)
        worker.drafts.save.assert_not_awaited()
        worker.queues.enqueue.assert_not_awaited()

    async def test_generation_and_revision_deliver_component_credits_and_model_inputs(self):
        retrieval = evidence(RagContentType.CHARACTER, [1] * 3, characters=True)
        sources = [asset(f"p{i}", role="character:main:original", source_native_id=str(i)) for i in (1, 2, 3)]
        sources += [asset("inside1", role="key_art"), asset("inside2", role="key_art")]
        deck = {"title": "주연 소개", "summary": "세 주연 소개", "keywords": ["주연"],
                "editorial_angle": "세 주연의 이야기", "cover_mode": "cast_intro_grid", "ending_mode": "ending",
                "cards": [{"role": role, "image_asset_id": image, "headline": "제목", "body": "본문",
                           "claims": [{"text": "근거", "evidence_refs": [f"C{i}"], "attribution": None}]}
                          for i, (role, image) in enumerate(zip(("cover", "detail", "ending"),
                                                               ("p1", "inside1", "inside2")), 1)]}
        article = {"body": "소개", "claims": deck["cards"][0]["claims"]}
        model = AsyncMock()
        model.generate_json.side_effect = [deck, article, deck, article]
        service = EvidenceBoundGenerationService(model)
        web = {"url": "https://example.org/interview", "title": "Cast interview", "original_text": "Cast interview transcript",
               "verified_claims": [
                   {"claim": f"Passage {i}", "evidence_refs": ["R2"], "chunk_ids": ["chunk3"]}
                   for i in range(3)
               ]}
        with patch("ani_rag.model_services.direct_card_layouts", side_effect=lambda _, cards, assets: cards):
            for instructions in ((), ("카드 제목 수정",)):
                draft = await service.generate(retrieval, image_candidates=sources, revision_instructions=instructions,
                                               primary_web_source=web)
                self.assertEqual(draft.cards[0].source_asset_ids, ("p1", "p2", "p3"))
                for image in sources:
                    self.assertIn(image.source_url, draft.source_footer)
        for call in model.generate_json.await_args_list:
            packet = call.kwargs["user_payload"]
            writer_input = packet.get("materials", packet)
            self.assertEqual(writer_input["primary_web_source"]["original_text"], web["original_text"])
            self.assertEqual([c["text"] for c in writer_input["retrieved_chunks"]], [c.text for c in retrieval.chunks])
            for reference in writer_input["evidence"]:
                self.assertTrue({"body", "claims", "excerpts"}.isdisjoint(reference))
            verified = writer_input["primary_web_source"]["verified_claims"]
            self.assertEqual([row["evidence_refs"] for row in verified], [["C3"]] * 3)
        web["verified_claims"][0]["chunk_ids"] = ["no-longer-available"]
        with self.assertRaisesRegex(ValueError, "insufficient currently cited material"):
            await service.generate(retrieval, image_candidates=sources, primary_web_source=web)
        self.assertEqual(model.generate_json.await_count, 4)
        # A revision about an interview must give both writers the same source wording and correction.
        from ani_rag.agents.content_generation.prompts import INTERVIEW_QUOTE_POLICY
        speaker, quote = "Director A", "I wanted to make a short film."
        context = f"{speaker}: {quote}"
        web["title"] = "Interview: The director on the format"
        answer = {"speaker": speaker, "subject": "a short film", "detail": "I wanted to make a short film."}
        web["verified_claims"] = [
            {"kind": "rag_context", "claim": "Director A led the production.", "chunk_ids": ["chunk1"]},
            {"kind": "attributed_quote", "claim": quote, "speaker": speaker,
             "attribution_excerpt": context, "chunk_ids": ["chunk3"], "interview_answer": answer},
        ]
        instructions = ("'보고 싶었다'를 '만들고 싶었다'로 고치고 지어낸 속마음은 빼줘.",)
        previous = {"title": "감독의 첫 만남", "detail_body": "감독은 애니로 보고 싶었다고 했어."}
        prior_blockers = [{"code": "UNSUPPORTED_QUANTITY", "message": "원문은 만화의 권수를 밝히지 않았다."}]
        model.generate_json.side_effect = [deck, article]
        with patch("ani_rag.model_services.direct_card_layouts", side_effect=lambda _, cards, assets: cards):
            await service.generate(retrieval, image_candidates=sources, primary_web_source=web,
                                   revision_instructions=instructions,
                                   previous_copy={**previous, "prior_blocking_issues": prior_blockers})
        for call in model.generate_json.await_args_list[-2:]:
            packet = call.kwargs["user_payload"]
            materials = packet.get("materials", packet)
            self.assertIn(INTERVIEW_QUOTE_POLICY, materials["source_use_policy"])
            self.assertEqual(materials["revision_instructions"], list(instructions))
            self.assertEqual(materials["draft_to_revise"], previous)
            self.assertEqual(materials["prior_blocking_issues"], prior_blockers)
            self.assertNotIn("prior_blocking_issues", materials["primary_web_source"])
            verified_quote = materials["primary_web_source"]["verified_claims"][1]
            self.assertEqual(verified_quote["interview_answer"], answer)
            self.assertIn("subject and detail together", materials["primary_web_source"]["interview_copy_rule"])
            self.assertEqual((verified_quote["speaker"], verified_quote["attribution_excerpt"],
                              verified_quote["evidence_refs"]), (speaker, context, ["C3"]))

    def test_web_writer_references_keep_attribution_period_and_survey_context(self):
        doc = replace(
            evidence(RagContentType.CURATION, [1]).documents[0], source_id="survey",
            author_name="Survey editor", body="Long survey", claims=("All results",),
            excerpts=(EvidenceExcerpt("All results", "p. 1"),), temporal_context="2025 season",
            curation_context=CurationContext(date(2025, 1, 1), date(2025, 2, 1), 400, "count", "fan poll"),
            metadata={"work_entry_year": "2025", "source_notes": "Large unrelated metadata"},
        )
        service = EvidenceBoundGenerationService
        full = service._document_payload(doc)
        compact = service._document_payload(doc, reference_only=True)
        for key in ("document_id", "source_id", "source_url", "language", "author_name", "curation_context"):
            self.assertEqual(compact[key], full[key])
        self.assertEqual(compact["temporal_context"], "2025 season")
        self.assertEqual(compact["metadata"], {"work_entry_year": "2025"})
        self.assertTrue({"body", "claims", "excerpts"}.isdisjoint(compact))
        self.assertEqual(full["metadata"], doc.metadata)
        self.assertEqual(full["body"], "Long survey")
        self.assertEqual(full["excerpts"], [{"text": "All results", "locator": "p. 1"}])
        self.assertEqual(full["claims"], ["All results"])
