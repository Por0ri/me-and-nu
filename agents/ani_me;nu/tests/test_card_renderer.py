import io
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from ani_menu_agent.agents.content_generation import render_node
from ani_menu_agent.agents.content_generation.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    OUTPUT_IMAGE_SIZE,
    _background,
    _bottom_scrim,
    _contained,
    _image_from_bytes,
    render_package_cards,
)
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, NormalizedBox
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
    assert contained.size == (1203, 602)
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
            "SELECT rendered_image_key, title_lines_json, body_json "
            "FROM content_cards WHERE request_id=? AND page=1",
            (package.request_id,),
        ).fetchone()
    assert row["rendered_image_key"] == card.rendered_image_key
    assert json.loads(row["title_lines_json"]) == card.title_lines
    assert json.loads(row["body_json"]) == card.body_sentences


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
