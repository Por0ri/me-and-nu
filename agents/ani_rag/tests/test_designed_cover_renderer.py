from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw

from ani_rag.agents.content_generation.renderer import HEIGHT, WIDTH, compose_cover_art, render_text_free_cards
from ani_rag.content import CardDraft, CardLayout, ContentDraft, ImageAsset
from ani_rag.models import RagContentType


class DesignedCoverRendererTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.assets = {}
        self.colors = [(240, 40, 30), (35, 215, 50), (35, 70, 240), (210, 160, 35), (190, 40, 190)]
        for i, color in enumerate(self.colors):
            asset_id = str(i)
            path = Path(temp.name) / f"{i}.png"
            Image.new("RGB", (500, 700), color).save(path)
            self.assets[asset_id] = ImageAsset(
                asset_id, f"https://example.org/{i}.png", "fixture", "matched", (i + 1,), None, None, str(path),
            )

    def cover(self, template, count):
        return CardDraft(
            "cover", "cover", "읽어볼까?", "읽을거리", (), "0",
            layout=CardLayout(template=template), component_image_asset_ids=tuple(str(i) for i in range(1, count)),
        )

    def draft(self, *cards):
        return ContentDraft(
            "draft", RagContentType.GUIDE, "제목", cards, "본문", (), ("doc",), (1,), "retrieval",
            datetime.now(timezone.utc), "출처", retrieved_chunk_ids=("chunk",),
        )

    def test_three_and_four_portrait_grids(self):
        for count in (3, 4):
            with self.subTest(count=count):
                art = compose_cover_art(self.cover("cast_intro_grid", count), self.assets)
                self.assertEqual(art.size, (1308, 1632))
                coords = ([(WIDTH * (2 * i + 1) // 6, HEIGHT // 4) for i in range(3)] if count == 3 else
                          [(WIDTH * (1 + 2 * (i % 2)) // 4, HEIGHT * (1 + 2 * (i // 2)) // 4) for i in range(4)])
                for i, xy in enumerate(coords):
                    self.assertEqual(art.getpixel(xy)[:3], self.colors[i])
                if count == 3:
                    self.assertLess(art.getpixel((WIDTH // 6, HEIGHT - 20))[0], 160)

    def test_fan_cover_centers_source_above_copy_with_four_dimmed_recommendations(self):
        card = self.cover("fan_recommendation", 5)
        art = compose_cover_art(card, self.assets)
        self.assertEqual(art.getpixel((WIDTH // 2, round(HEIGHT * 0.42)))[:3], self.colors[0])
        # Left and right edges show the recommendation, outside the centre poster.
        for i, (x, y) in enumerate(((110, 330), (WIDTH - 110, 330), (110, 1300), (WIDTH - 110, 1300))):
            actual = art.getpixel((x, y))[:3]
            for channel, source in zip(actual, self.colors[i + 1]):
                self.assertAlmostEqual(channel, source * 0.55, delta=1)
        self.assertNotEqual(art.getpixel((WIDTH // 2, round(HEIGHT * card.layout.text_overlay_box.y)))[:3], self.colors[0])

    def test_guide_keeps_full_poster_and_copy_separate(self):
        card = self.cover("beginner_guide", 1)
        # An ultra-wide source contains distinct details near both ends.
        poster = Image.new("RGB", (600, 130), "black")
        draw = ImageDraw.Draw(poster)
        draw.rectangle((15, 20, 70, 110), fill="red")
        draw.rectangle((530, 20, 584, 110), fill="blue")
        poster.save(self.assets["0"].local_path)
        plate, = render_text_free_cards(self.draft(card), self.assets)
        other, = render_text_free_cards(self.draft(replace(card, headline="다른 문구", body="새로운 설명")), self.assets)
        self.assertEqual(plate.image_png, other.image_png)
        self.assertNotEqual(plate.frontend_copy, other.frontend_copy)
        self.assertFalse(plate.frontend_layout["panorama_crop"])
        with Image.open(BytesIO(plate.image_png)) as image:
            self.assertEqual(image.size, (1308, 1632))
            y = round(HEIGHT * card.layout.text_overlay_box.y) // 2
            self.assertGreater(image.getpixel((WIDTH // 2 - 500, y))[0], 200)
            self.assertGreater(image.getpixel((WIDTH // 2 + 500, y))[2], 200)

    def test_composite_components_cannot_repeat_on_other_cards(self):
        cover = self.cover("cast_intro_grid", 3)
        detail = CardDraft("detail", "detail", "다음", "본문", (), "alias")
        for duplicate in (
            replace(self.assets["2"], asset_id="alias", source_url="https://example.org/duplicate-bytes"),
            replace(self.assets["4"], asset_id="alias", source_url=self.assets["2"].source_url),
        ):
            with self.subTest(duplicate=duplicate.source_url), self.assertRaisesRegex(ValueError, "reuse"):
                render_text_free_cards(self.draft(cover, detail), {**self.assets, "alias": duplicate})
