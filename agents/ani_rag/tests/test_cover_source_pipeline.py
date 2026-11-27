import asyncio
import base64
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from ani_rag.adapter import SearchRequest
from ani_rag.agents.content_generation.layout_director import ImageCopyMismatchError, direct_card_layouts
from ani_rag.content import CardDraft, CardLayout, ContentDraft, ImageAsset, SupportedClaim
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter
from ani_rag.image_ingest import kitsu_image_references
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.quality import validate_draft
from ani_rag.retrieval import RetrievedEvidence, chunk_document
from ani_rag.workers import _generation_image_packet


class CoverSourcePipelineTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "data": {"type": "anime", "id": "1", "attributes": {"canonicalTitle": "Test series",
                     "posterImage": {"original": "https://media.kitsu.app/anime/poster_images/1/original.jpg"},
                     "coverImage": {"original": "https://media.kitsu.app/anime/cover_images/1/original.jpg"}}},
            "included": [
                {"type": "animeCharacters", "attributes": {"role": "main"},
                 "relationships": {"character": {"data": {"id": "20", "type": "characters"}}}},
                {"id": "20", "type": "characters", "attributes": {"name": "Kiki", "description": "A young witch",
                 "image": {"original": "https://media.kitsu.app/characters/images/20/original.jpg"}}},
            ],
        }
        self.document = EvidenceDocument(
            "doc", RagContentType.CHARACTER, "Test series", "https://kitsu.io/api/edge/anime/1",
            "kitsu_anime_catalog", datetime.now(timezone.utc), EvidenceType.REFERENCE_FACT,
            CollectionMethod.MANUAL_IMPORT, "en", body=json.dumps(self.payload), menu_work_ids=(1,),
            work_link_status=WorkLinkStatus.MATCHED,
        )

    def test_item_query_and_preserved_assets_distinguish_poster_banner_and_lead(self):
        request = SearchRequest(source_id="kitsu_anime_catalog", query="Test series", mcp_server_alias="firecrawl",
                                tool_name="firecrawl_scrape", allowed_hosts=frozenset({"kitsu.io"}), limit=1)
        client = Mock()
        client.call_tool.return_value = {"structuredContent": {"markdown": json.dumps(self.payload)}}
        adapter = FirecrawlMCPAdapter()
        with patch.object(adapter, "_client", return_value=client):
            hit, = adapter._search_kitsu_api(request)
        fields = parse_qs(urlsplit(hit.url).query)
        self.assertIn("posterImage", fields["fields[anime]"][0].split(","))
        self.assertIn("character", fields["fields[animeCharacters]"][0].split(","))
        self.assertEqual([img["role"] for img in kitsu_image_references(self.payload)],
                         ["poster:original", "cover:original", "character:main:original"])

    def test_verify_cited_cast_record_using_exact_preserved_fields(self):
        chunk, = chunk_document(self.document)
        claim = SupportedClaim("Kiki is a witch", ("doc",), (chunk.chunk_id,))
        card = CardDraft("c1", "cover", "Kiki", "The young witch", (claim,), "p1")
        draft = ContentDraft("draft", RagContentType.CHARACTER, "Kiki", (card,), "Intro", (claim,),
                             ("doc",), (1,), "r", self.document.retrieved_at, self.document.source_url,
                             retrieved_chunk_ids=(chunk.chunk_id,))
        def codes(record):
            return {issue.code for issue in validate_draft(
                draft, {"doc": self.document}, {}, {chunk.chunk_id: record})}
        self.assertNotIn("RETRIEVED_CHUNK_ORIGIN_MISMATCH", codes(chunk))
        self.assertIn("RETRIEVED_CHUNK_ORIGIN_MISMATCH", codes(replace(chunk, text="A different character")))
        self.assertIn("RETRIEVED_CHUNK_ORIGIN_MISMATCH", codes(replace(chunk, source_index=99)))

    def test_image_packet_keeps_a_verified_poster_even_among_many_search_hits(self):
        retrieval = RetrievedEvidence("r", (), (), "Test series", RagContentType.GUIDE, (1,), (),
                                      self.document.retrieved_at)
        image = ImageAsset("poster", "https://media.kitsu.app/anime/poster_images/1/original.jpg",
                           "kitsu_anime_catalog", "matched", (1,), None, None, "art.png",
                           image_role="poster:original", source_native_id="1", width=800, height=1000)
        distractors = [replace(image, asset_id=f"hit{i}", source_id="firecrawl_web_images",
                               source_url=f"https://images.example/{i}", image_role="web_search_candidate",
                               width=1400, height=1800) for i in range(30)]
        packet = _generation_image_packet(retrieval, [*distractors, image])
        self.assertEqual(len(packet), 20)
        self.assertIn(image, packet)

    def test_vision_checks_the_composed_cover_in_one_image_and_retains_the_design(self):
        with TemporaryDirectory() as folder:
            assets = {}
            for index, color in enumerate(("red", "green", "blue", "white")):
                key = str(index)
                path = Path(folder) / f"{key}.png"
                Image.new("RGB", (40, 50), color).save(path)
                assets[key] = ImageAsset(key, f"https://example.org/{key}", "catalog", "matched",
                                         (1,), None, None, str(path))
            cards = [
                CardDraft("cover", "cover", "The cast", "Three leads", (), "0",
                          component_image_asset_ids=("1", "2"), layout=CardLayout(template="cast_intro_grid")),
                CardDraft("end", "ending", "Last", "End", (), "3"),
            ]
            rows = [dict(card_id=card.card_id, copy_position="bottom", image_matches_copy=True,
                         image_quality_acceptable=True, image_emphasis="dominant", background_style="blur")
                    for card in cards]
            model = AsyncMock()
            model.generate_json_with_images.return_value = {"cards": rows}
            directed = asyncio.run(direct_card_layouts(model, cards, assets))
            self.assertEqual(directed[0].layout.template, "cast_intro_grid")
            payload = model.generate_json_with_images.await_args.kwargs
            self.assertEqual(len(payload["image_data_urls"]), len(cards))
            data = base64.b64decode(payload["image_data_urls"][0].partition(",")[2])
            with Image.open(BytesIO(data)) as reviewed_image:
                pixels = [reviewed_image.getpixel((round(reviewed_image.width * x), 50))
                          for x in (0.17, 0.5, 0.83)]
                self.assertEqual([pixel.index(max(pixel)) for pixel in pixels], [0, 1, 2])
            rows[0]["image_matches_copy"] = False
            with self.assertRaises(ImageCopyMismatchError):
                asyncio.run(direct_card_layouts(model, cards, assets))

    def test_ordinary_cards_require_their_featured_character_or_season(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "art.png"
            Image.new("RGB", (160, 240)).save(path)
            art = ImageAsset("art", "https://example.org/art.png", "catalog", "matched",
                             (1,), None, None, str(path), width=160, height=240)
            overview = CardDraft("cover", "cover", "스파이 패밀리 입문", "시즌 2까지 어떤 가족을 만나게 될까?", (), "cover")
            cover_art = replace(art, asset_id="cover", alt_text="Spy × Family Season 1 key visual")
            for headline, caption, target in (
                ("로이드 포저는 누구?", "Anya Forger, solo portrait", "Spy × Family Loid Forger"),
                ("스파이 패밀리 시즌 2", "Spy × Family Season 1 key visual", "Spy × Family Season 2"),
            ):
                with self.subTest(headline=headline):
                    page = CardDraft("end", "ending", headline, "자세히 보자.", (), "art")
                    assets = {"art": replace(art, alt_text=caption), "cover": cover_art}
                    accepted = dict(copy_position="bottom", image_matches_copy=True, image_quality_acceptable=True,
                                    image_emphasis="dominant", background_style="blur", image_search_subject="")
                    rows = [dict(accepted, card_id="cover"), dict(accepted, card_id="end", image_matches_copy=False,
                            image_match_reason=f"The pictured subject is {caption}", image_search_subject=target)]
                    model = AsyncMock()
                    model.generate_json_with_images.return_value = {"cards": rows}
                    with self.assertRaises(ImageCopyMismatchError) as failure:
                        asyncio.run(direct_card_layouts(model, [overview, page], assets))
                    self.assertEqual((failure.exception.card_id, failure.exception.image_asset_id,
                                      failure.exception.search_subject), ("end", "art", target))
                    packet = model.generate_json_with_images.await_args.kwargs
                    self.assertEqual(packet["user_payload"]["pages"][1]["image_alt_text"], caption)
                    self.assertIn("Incidental mentions", packet["system_prompt"])
                    assets["art"] = replace(art, alt_text=target)
                    rows[1] = dict(accepted, card_id="end")
                    directed = asyncio.run(direct_card_layouts(model, [overview, page], assets))
                    self.assertEqual([card.image_asset_id for card in directed], ["cover", "art"])
