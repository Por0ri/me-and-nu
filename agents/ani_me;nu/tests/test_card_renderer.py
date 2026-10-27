import io
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

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
    _copy_scrim,
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
    assert abs(contained.width / contained.height - original.width / original.height) < 0.01
    corners = [
        contained.getpixel((5, 5)),
        contained.getpixel((contained.width - 6, 5)),
        contained.getpixel((5, contained.height - 6)),
        contained.getpixel((contained.width - 6, contained.height - 6)),
    ]
    assert len(set(corners)) == 4

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
            "SELECT rendered_image_key, title_lines_json, body_json, visual_mode, overlay_json "
            "FROM content_cards WHERE request_id=? AND page=1",
            (package.request_id,),
        ).fetchone()
    assert row["rendered_image_key"] == card.rendered_image_key
    assert json.loads(row["title_lines_json"]) == card.title_lines
    assert json.loads(row["body_json"]) == card.body_sentences
    assert row["visual_mode"] == "source_image"
    overlay = json.loads(row["overlay_json"])
    assert overlay["page"] == 1
    assert overlay["image_key"] == card.rendered_image_key
    assert overlay["title_lines"] == card.title_lines
    assert overlay["text_region"] == {
        "x": 0.06,
        "y": 0.68,
        "width": 0.88,
        "height": 0.28,
    }
    assert overlay["image_region"] == card.layout.image_box.model_dump(mode="json")
    assert overlay["copy_region"] == overlay["text_region"]


def test_layout_director_choice_is_constrained_and_cover_copy_stays_below() -> None:
    package = _package_with_renderable_card()
    cover = package.cards[0]
    assert cover.layout.text_overlay_box.y >= 0.65

    detail = CopyCard(
        page=2,
        role="detail",
        title_lines=["짧은 제목"],
        body_sentences=["간단한 본문입니다."],
        image_id=package.images[0].id,
    )
    side_image = package.images[0].model_copy(update={"width": 240, "height": 400})
    laid_out = build_cards(
        [detail],
        [side_image],
        art_directions={2: {"copy_position": "left", "background_style": "gradient"}},
    )[0].layout
    assert laid_out.text_overlay_box.x < 0.5
    assert laid_out.image_box.x > 0.5
    assert laid_out.background_style == "gradient"


def test_artwork_edge_mask_fades_into_backdrop_without_a_frame() -> None:
    from ani_menu_agent.agents.content_generation.renderer import _paste_uncropped

    canvas = Image.new("RGBA", (400, 400), (20, 30, 40, 255))
    source = Image.new("RGBA", (240, 240), (220, 30, 40, 255))
    _paste_uncropped(canvas, source, (80, 80, 240, 240))

    edge = canvas.getpixel((80, 200))
    center = canvas.getpixel((200, 200))
    assert edge[0] < center[0]
    assert center[:3] == (220, 30, 40)


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


def test_low_resolution_landscape_cover_uses_crisp_side_by_side_layout() -> None:
    image = ImageAsset(
        id="small-landscape-cover",
        url="https://example.com/story.jpg",
        source_url="https://example.com/story",
        width=600,
        height=385,
    )

    layout = _image_led_cover_layout(image)

    assert layout.template == "side_left"
    assert layout.image_box.width < 0.5


def test_low_resolution_square_cover_uses_small_inset_instead_of_upscaling() -> None:
    image = ImageAsset(
        id="square-character-cover",
        url="https://example.com/character.jpg",
        source_url="https://example.com/character",
        width=320,
        height=320,
    )

    layout = _image_led_cover_layout(image)

    assert layout.template == "cover_inset_left"
    assert layout.image_box.width == 0.24


def test_editorial_graphic_placeholder_is_rejected_instead_of_rendered() -> None:
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
    with pytest.raises(ValueError, match="cannot use a decorative placeholder"):
        build_cards([copy], package.images)


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
    assert "overlay_json" in columns


def test_renderer_composites_transparent_cutout_and_keeps_full_subject() -> None:
    package = _package_with_renderable_card()
    source = Image.new("RGBA", (500, 750), (0, 0, 0, 0))
    ImageDraw.Draw(source).ellipse((100, 38, 400, 712), fill=(30, 180, 245, 255))
    binary = io.BytesIO()
    source.save(binary, format="PNG")
    image = package.images[0].model_copy(
        update={
            "content_bytes": binary.getvalue(),
            "media_type": "image/png",
            "width": 500,
            "height": 750,
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

    assert package.cards[1].layout.template.startswith("editorial_")
    assert package.cards[1].layout.fit_mode in {"contain", "contain_blur"}
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


def test_blurred_and_palette_backdrops_both_echo_source_artwork() -> None:
    source = Image.new("RGB", (800, 500), (20, 35, 90))
    ImageDraw.Draw(source).rectangle((0, 250, 799, 499), fill=(210, 170, 95))

    blurred = _background(source, "blur")
    palette = _background(source, "palette")
    blurred_flat = Image.new("RGBA", blurred.size, blurred.getpixel((0, 0)))
    palette_flat = Image.new("RGBA", palette.size, palette.getpixel((0, 0)))

    assert ImageChops.difference(blurred.convert("RGB"), blurred_flat.convert("RGB")).getbbox()
    assert ImageChops.difference(palette.convert("RGB"), palette_flat.convert("RGB")).getbbox()


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


def test_png_is_independent_of_copy_because_frontend_owns_all_text() -> None:
    package = _package_with_renderable_card()
    card = package.cards[0]
    alternate = card.model_copy(
        update={"eyebrow": "다른 문구", "title_lines": ["가" * 40], "body_sentences": ["다른 본문"]}
    )
    first = render_card_png(package, card, package.images[0], package.images[0].content_bytes)
    second = render_card_png(package, alternate, package.images[0], package.images[0].content_bytes)
    assert first == second


def test_cover_html_copy_area_is_bottom_and_darkens_gradually() -> None:
    package = _package_with_renderable_card()
    layout = package.cards[0].layout
    assert layout.copy_box == layout.text_overlay_box
    assert layout.text_overlay_box.y == 0.68
    rendered = render_package_cards(package, get_image_bytes=lambda _key: None)[1]
    with Image.open(io.BytesIO(rendered)) as output:
        image = output.convert("RGB")
        upper = image.crop((0, int(CARD_HEIGHT * 0.32), CARD_WIDTH, int(CARD_HEIGHT * 0.42)))
        lower = image.crop((0, int(CARD_HEIGHT * 0.90), CARD_WIDTH, CARD_HEIGHT))
        assert ImageStat.Stat(lower.convert("L")).mean[0] < ImageStat.Stat(
            upper.convert("L")
        ).mean[0]


def test_directional_scrims_follow_left_and_top_html_regions() -> None:
    left = _copy_scrim(
        _package_with_renderable_card().cards[0].layout.model_copy(
            update={
                "text_overlay_box": NormalizedBox(x=0.04, y=0.16, width=0.43, height=0.68)
            }
        )
    )
    top = _copy_scrim(
        _package_with_renderable_card().cards[0].layout.model_copy(
            update={
                "text_overlay_box": NormalizedBox(x=0.07, y=0.055, width=0.86, height=0.30)
            }
        )
    )
    assert left.getpixel((int(CARD_WIDTH * 0.1), CARD_HEIGHT // 2))[3] > left.getpixel(
        (int(CARD_WIDTH * 0.8), CARD_HEIGHT // 2)
    )[3]
    assert top.getpixel((CARD_WIDTH // 2, 0))[3] > top.getpixel(
        (CARD_WIDTH // 2, CARD_HEIGHT // 2)
    )[3]


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


def test_multitoken_brand_phrase_stays_on_one_line_when_it_fits() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "white"))
    font = _font(20, bold=True)
    phrase = "BANDAI SPIRITS"

    lines = _wrap_line(
        draw,
        f"{phrase} 피규어 출시",
        font,
        _text_width(draw, phrase, font) + 1,
        [phrase],
    )

    assert phrase in lines


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


def test_small_transparent_portrait_uses_compact_inset_instead_of_upscaling() -> None:
    image = ImageAsset(
        id="small-cutout",
        url="https://example.com/small.png",
        source_url="https://example.com/news/1",
        width=376,
        height=536,
        has_alpha=True,
        subject_box=NormalizedBox(x=0.1, y=0.1, width=0.8, height=0.8),
    )
    copy = CopyCard(
        page=2,
        role="detail",
        title_lines=["작은 그림도", "선명하게"],
        body_sentences=["이미지 원본을 무리하게 확대하지 않는다."],
        image_id=image.id,
    )

    layout = _detail_layout(image, 2, copy)

    assert layout.template == "portrait_inset_left"
    assert layout.image_box.width == 0.32


def test_low_resolution_landscape_uses_text_wide_inset_layout() -> None:
    image = ImageAsset(
        id="landscape-low-res",
        url="https://example.com/arc.jpg",
        source_url="https://example.com/guide/1",
        width=600,
        height=385,
    )
    copy = CopyCard(
        page=2,
        role="detail",
        title_lines=["공식 공개 범위", "작품의 시작과 전환"],
        body_sentences=["원작과 애니메이션의 해당 구간을 차례로 살펴본다."],
        fact_ids=[],
        image_id=image.id,
    )

    layout = _detail_layout(image, 2, copy)

    assert layout.template == "wide_inset_top"
    assert layout.image_box.width == 0.48
    assert layout.copy_box.width == 0.86
    assert layout.copy_box.height == 0.62


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
