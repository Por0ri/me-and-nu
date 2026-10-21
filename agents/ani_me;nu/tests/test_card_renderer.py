import io
import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from ani_menu_agent.agents.content_generation import render_node
from ani_menu_agent.agents.content_generation.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    OUTPUT_IMAGE_SIZE,
    _artwork_accent,
    _background,
    _bottom_scrim,
    _contained,
    _content_badge,
    _contrast_ratio,
    _editorial_graphic_background,
    _font,
    _image_from_bytes,
    _text_width,
    _wrap_line,
    render_card_png,
    render_package_cards,
)
from ani_menu_agent.layout_engine import _detail_layout, _image_led_cover_layout, build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, ImageAsset, NormalizedBox
from ani_menu_agent.storage import AgentRepository

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def _package_with_renderable_card() -> ContentPackage:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    source = Image.new("RGB", (240, 120), (34, 40, 48))
    source.paste((255, 0, 0), (0, 0, 36, 36))
    source.paste((0, 255, 0), (204, 0, 240, 36))
    source.paste((0, 0, 255), (0, 84, 36, 120))
    source.paste((255, 255, 0), (204, 84, 240, 120))
    binary = io.BytesIO()
    source.save(binary, format="PNG")
    package.images[0] = package.images[0].model_copy(
        update={
            "content_bytes": binary.getvalue(),
            "media_type": "image/png",
            "width": 240,
            "height": 120,
        }
    )
    copy = CopyCard(
        page=1,
        role="행사 안내",
        eyebrow="일정 안내",
        title_lines=["하이큐 페어", "12월 19일 시작"],
        body_sentences=["공식 이벤트가 전국 애니메이트에서 열립니다."],
        fact_ids=["f1"],
        image_id="i1",
    )
    package.cards = build_cards([copy], package.images)
    return package


def test_renderer_exports_full_size_png_and_keeps_source_image_uncropped(tmp_path: Path) -> None:
    package = _package_with_renderable_card()
    rendered = render_package_cards(package, get_image_bytes=lambda _key: None)
    png = rendered[1]
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == OUTPUT_IMAGE_SIZE == (1308, 1632)
    # The foreground fit preserves the entire source aspect and its four corners.
    original = _image_from_bytes(package.images[0].content_bytes or b"")
    box = package.cards[0].layout.image_box
    contained = _contained(
        original,
        int(box.width * CARD_WIDTH),
        int(box.height * CARD_HEIGHT),
    )
    assert contained.size == (1255, 628)
    assert contained.getpixel((10, 10))[0] > contained.getpixel((10, 10))[1]
    assert contained.getpixel((1193, 10))[1] > contained.getpixel((1193, 10))[0]
    assert contained.getpixel((10, 441))[2] > contained.getpixel((10, 441))[0]
    assert contained.getpixel((1193, 592))[0] > contained.getpixel((1193, 592))[2]

    repository = AgentRepository(path=tmp_path / "cards.sqlite3")
    package = repository.persist_rendered_cards(package, rendered)
    card = package.cards[0]
    assert card.rendered_image_key is not None
    assert (card.rendered_width, card.rendered_height) == OUTPUT_IMAGE_SIZE
    assert repository.get_image_bytes(card.rendered_image_key) == (png, "image/png")


    repository.persist_generated_content(package, "ready_for_feed", {"total": 95})
    repository.enqueue_for_feed(package)
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT rendered_image_key, title_lines_json, body_json, visual_mode "
            "FROM content_cards WHERE request_id=? AND page=1",
            (package.request_id,),
        ).fetchone()
    assert row["rendered_image_key"] == card.rendered_image_key
    assert json.loads(row["title_lines_json"]) == card.title_lines
    assert json.loads(row["body_json"]) == card.body_sentences
    assert row["visual_mode"] == "source_image"


def test_square_image_led_cover_keeps_headline_below_source_artwork() -> None:
    image = ImageAsset(
        id="square-art",
        url="https://example.com/art.png",
        source_url="https://example.com/news",
        width=1920,
        height=1920,
    )

    layout = _image_led_cover_layout(image)

    assert layout.image_box.bottom <= layout.copy_box.y
    assert layout.copy_box.y == 0.59


def test_editorial_graphic_card_renders_without_reusing_or_fabricating_source_art() -> None:
    package = _package_with_renderable_card()
    copy = CopyCard(
        page=2,
        role="detail",
        eyebrow="한눈에 보기",
        title_lines=["남은 장은 정보로 승부"],
        body_sentences=[
            "공식 이미지가 부족하면 원본 그림을 반복하지 않고 자체 그래픽으로 구성한다."
        ],
        image_id="editorial-graphic-2",
        visual_mode="editorial_graphic",
        image_reuse_mode="editorial_graphic",
    )
    package.cards = build_cards([copy], package.images)

    rendered = render_package_cards(package, get_image_bytes=lambda _key: None)

    with Image.open(io.BytesIO(rendered[2])) as card_image:
        assert card_image.format == "PNG"
        assert card_image.size == OUTPUT_IMAGE_SIZE
        assert (
            len(card_image.convert("RGB").getcolors(maxcolors=CARD_WIDTH * CARD_HEIGHT) or []) > 50
        )


def test_feed_badge_uses_content_taxonomy_not_pipeline_kind() -> None:
    package = _package_with_renderable_card()
    package.content_type = "event"

    assert _content_badge(package) == "행사"


def test_old_database_schema_gets_visual_mode_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "old-schema.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE content_cards (
                request_id TEXT NOT NULL,
                page INTEGER NOT NULL,
                role TEXT NOT NULL,
                eyebrow TEXT NOT NULL,
                title_lines_json TEXT NOT NULL,
                body_json TEXT NOT NULL,
                fact_ids_json TEXT NOT NULL,
                image_id TEXT NOT NULL,
                image_explanation TEXT NOT NULL,
                layout_json TEXT NOT NULL,
                rendered_image_key TEXT,
                PRIMARY KEY (request_id, page)
            )
            """
        )

    AgentRepository(path=database_path)

    with AgentRepository(path=database_path).connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(content_cards)")}
    assert "visual_mode" in columns


def test_renderer_composites_transparent_cutout_and_keeps_full_subject() -> None:
    package = _package_with_renderable_card()
    source = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    ImageDraw.Draw(source).ellipse((40, 15, 160, 285), fill=(30, 180, 245, 255))
    binary = io.BytesIO()
    source.save(binary, format="PNG")
    image = package.images[0].model_copy(
        update={
            "content_bytes": binary.getvalue(),
            "media_type": "image/png",
            "width": 200,
            "height": 300,
            "has_alpha": True,
            "subject_hint": "right",
            "subject_box": NormalizedBox(x=0.2, y=0.05, width=0.6, height=0.9),
        }
    )
    package.images[0] = image
    copy = CopyCard(
        page=2,
        role="character_profile",
        title_lines=["이 장면을 기억해"],
        body_sentences=["투명 배경 피사체를 카드 위에 자연스럽게 합성한다."],
        image_id=image.id,
    )
    package.cards = build_cards(
        [
            copy.model_copy(update={"page": 1}),
            copy,
            copy.model_copy(update={"page": 3}),
        ],
        package.images,
    )

    rendered = render_package_cards(package, get_image_bytes=lambda _key: None)

    assert package.cards[1].layout.template == "cutout_right"
    with Image.open(io.BytesIO(rendered[2])) as output:
        assert output.size == OUTPUT_IMAGE_SIZE
        colors = output.convert("RGB").getcolors(maxcolors=CARD_WIDTH * CARD_HEIGHT)
        assert colors is not None and len(colors) > 100


def test_backdrop_has_no_hard_horizontal_seam() -> None:
    source = Image.new("RGB", (1200, 900), (20, 35, 90))
    ImageDraw.Draw(source).rectangle((0, 450, 1199, 899), fill=(210, 170, 95))

    backdrop = _background(source)

    before = backdrop.getpixel((CARD_WIDTH // 2, CARD_HEIGHT // 2 - 1))
    at_seam = backdrop.getpixel((CARD_WIDTH // 2, CARD_HEIGHT // 2))
    after = backdrop.getpixel((CARD_WIDTH // 2, CARD_HEIGHT // 2 + 1))
    assert max(abs(a - b) for a, b in zip(before, at_seam, strict=True)) <= 2
    assert max(abs(a - b) for a, b in zip(at_seam, after, strict=True)) <= 2


def test_blurred_backdrop_uses_artwork_colors_while_palette_backdrop_stays_smooth() -> None:
    source = Image.new("RGB", (800, 500), (20, 35, 90))
    ImageDraw.Draw(source).rectangle((0, 250, 799, 499), fill=(210, 170, 95))

    blurred = _background(source, "blur")
    palette = _background(source, "palette")
    flat = Image.new("RGBA", palette.size, palette.getpixel((0, 0)))

    blurred_flat = Image.new("RGBA", blurred.size, blurred.getpixel((0, 0)))

    assert ImageChops.difference(blurred.convert("RGB"), blurred_flat.convert("RGB")).getbbox()
    assert ImageChops.difference(palette.convert("RGB"), flat.convert("RGB")).getbbox() is None


def test_bottom_scrim_is_continuous_through_card_middle() -> None:
    scrim = _bottom_scrim()
    before = scrim.getpixel((0, CARD_HEIGHT // 2 - 1))[3]
    at_middle = scrim.getpixel((0, CARD_HEIGHT // 2))[3]
    after = scrim.getpixel((0, CARD_HEIGHT // 2 + 1))[3]
    assert abs(at_middle - before) <= 1
    assert abs(after - at_middle) <= 1


def test_korean_word_wrap_keeps_counters_and_loanwords_intact() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (600, 100), "black"))
    font = _font(42, bold=True)
    width = _text_width(draw, "여섯 명", font) + 1

    lines = _wrap_line(draw, "성우 여섯 명 출연", font, width)
    loanword_lines = _wrap_line(draw, "플레이리스트 감상", font, width)

    assert all(not line.endswith("여섯") for line in lines)
    assert all(not line.startswith("명") for line in lines)
    assert "플레이리스트" in loanword_lines
    assert all(line not in {"플레이리스", "트 감상"} for line in loanword_lines)


def test_korean_title_wrap_avoids_a_dangling_one_word_last_line() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (600, 100), "black"))
    font = _font(42, bold=True)
    title = "나루토와 사스케 성우가 함께 선다"
    width = _text_width(draw, "성우가 함께 선다", font) + 2

    lines = _wrap_line(draw, title, font, width)

    assert lines[-1] != "선다"
    assert "성우가 함께 선다" in lines


def test_artwork_accent_adapts_to_art_and_meets_dark_panel_contrast() -> None:
    red = Image.new("RGB", (128, 128), (220, 32, 35))
    blue = Image.new("RGB", (128, 128), (36, 74, 220))

    red_accent = _artwork_accent(red)
    blue_accent = _artwork_accent(blue)
    # Worst case: the translucent text matte sits over a pure white image area.
    alpha = 210 / 255
    worst_backdrop = tuple(
        round(foreground * (1 - alpha) + matte * alpha)
        for foreground, matte in zip((255, 255, 255), (9, 12, 18), strict=True)
    )

    assert red_accent != blue_accent
    assert _contrast_ratio(red_accent, (9, 12, 18)) >= 4.5
    assert _contrast_ratio(blue_accent, (9, 12, 18)) >= 4.5
    assert _contrast_ratio(red_accent, worst_backdrop) >= 4.5
    assert _contrast_ratio(blue_accent, worst_backdrop) >= 4.5


def test_editorial_graphic_has_no_empty_image_well() -> None:
    graphic = _editorial_graphic_background(3, "detail").convert("RGB")
    region = graphic.crop((25 * 4, 35 * 4, 300 * 4, 190 * 4))
    vivid_pixels = sum(
        1 for red, green, blue in region.get_flattened_data() if max(red, green, blue) > 90
    )

    assert vivid_pixels > 1000


def test_final_text_card_uses_one_clean_background_regardless_of_copy_topic() -> None:
    voice_art = _editorial_graphic_background(3, "detail", "성우 여섯 명 출연")
    generic_art = _editorial_graphic_background(3, "detail", "행사 일정과 공식 정보")

    assert ImageChops.difference(voice_art, generic_art).getbbox() is None


def test_unrenderable_title_fails_instead_of_clipping_or_shrinking_to_a_glyph() -> None:
    package = _package_with_renderable_card()
    card = package.cards[0].model_copy(update={"title_lines": ["가" * 40]})

    try:
        render_card_png(package, card, package.images[0], package.images[0].content_bytes)
    except ValueError as exc:
        assert "제목" in str(exc)
    else:
        raise AssertionError("an unrenderable title must stop card creation")


def test_missing_assigned_source_image_fails_instead_of_rendering_an_empty_slot() -> None:
    package = _package_with_renderable_card()
    package.images[0] = package.images[0].model_copy(
        update={"content_bytes": None, "local_path": None, "storage_key": None}
    )

    try:
        render_package_cards(package, get_image_bytes=lambda _key: None)
    except ValueError as exc:
        assert "원본 이미지" in str(exc)
    else:
        raise AssertionError("a missing assigned source image must stop card creation")


def test_store_rejects_wrong_card_png_dimensions(tmp_path: Path) -> None:
    package = _package_with_renderable_card()
    invalid = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(invalid, format="PNG")

    repository = AgentRepository(path=tmp_path / "invalid.sqlite3")
    try:
        repository.persist_rendered_cards(package, {1: invalid.getvalue()})
    except ValueError as exc:
        assert "1308×1632" in str(exc)
    else:
        raise AssertionError("wrong dimensions should not be stored")


def test_protected_headline_phrase_is_not_split_by_renderer() -> None:
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    font = _font(20, bold=True)

    lines = _wrap_line(
        draw,
        "원피스 한정 아이템 부산에서도?",
        font,
        _text_width(draw, "원피스 한정 아이템", font),
        ["원피스 한정 아이템"],
    )

    assert "원피스 한정 아이템" in lines


def test_portrait_layout_shrinks_low_resolution_art_instead_of_rejecting_it() -> None:
    image = ImageAsset(
        id="portrait-low-res",
        url="https://example.com/art.jpg",
        source_url="https://example.com/news/1",
        width=500,
        height=750,
    )
    copy = CopyCard(
        page=2,
        role="detail",
        title_lines=["컬렉션 공개"],
        body_sentences=["작품의 세계관을 일상 패션으로 즐길 수 있는 협업 상품이 공개됐다."],
        fact_ids=[],
        image_id=image.id,
    )

    layout = _detail_layout(image, 2, copy)

    assert layout.template == "side_left"
    assert layout.image_box.width == 0.43


def test_generation_renderer_persists_copy_and_png_before_review(
    tmp_path: Path, monkeypatch
) -> None:
    package = _package_with_renderable_card()
    repository = AgentRepository(path=tmp_path / "generation.sqlite3")
    repository.persist_images(package)
    monkeypatch.setattr(render_node, "AgentRepository", lambda: repository)

    result = render_node.render_cards(
        {
            "request_id": package.request_id,
            "trace": [],
            "package": package.model_dump(mode="json"),
        }
    )

    assert result["status"] == "generated_pending_review"
    with repository.connect() as connection:
        draft = connection.execute(
            "SELECT status, package_json FROM content_drafts WHERE request_id=?",
            (package.request_id,),
        ).fetchone()
        card = connection.execute(
            "SELECT rendered_image_key, title_lines_json FROM content_cards "
            "WHERE request_id=? AND page=1",
            (package.request_id,),
        ).fetchone()
    assert draft["status"] == "generated_pending_review"
    assert json.loads(draft["package_json"])["cards"][0]["rendered_image_key"]
    assert card["rendered_image_key"] == result["package"]["cards"][0]["rendered_image_key"]
    assert json.loads(card["title_lines_json"]) == package.cards[0].title_lines
    assert repository.get_image_bytes(card["rendered_image_key"])[1] == "image/png"
