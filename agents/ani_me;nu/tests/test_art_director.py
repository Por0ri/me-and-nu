import base64
import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from ani_menu_agent.agents.content_generation import art_director
from ani_menu_agent.agents.content_generation.art_director import (
    ArtDirectionDeck,
    CardArtDirection,
    plan_card_art,
)
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, UsageRecord


def test_vision_art_director_sends_one_low_detail_request_and_returns_restricted_choices(
    monkeypatch,
):
    package = ContentPackage.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples" / "generation_input.json").read_text(
            encoding="utf-8"
        )
    )
    image = Image.new("RGB", (1600, 900), (64, 100, 160))
    binary = io.BytesIO()
    image.save(binary, format="PNG")
    package.images[0] = package.images[0].model_copy(
        update={"content_bytes": binary.getvalue(), "width": 1600, "height": 900}
    )
    copy = CopyCard(
        page=1,
        role="cover",
        title_lines=["애니 소식"],
        body_sentences=["새로운 소식입니다."],
        image_id=package.images[0].id,
    )
    cards = build_cards([copy], package.images)
    seen = {}

    class FakeStructured:
        def invoke(self, messages):
            seen["messages"] = messages
            return {
                "parsed": ArtDirectionDeck(
                    cards=[
                        CardArtDirection(
                            page=1,
                            copy_position="bottom",
                            background_style="gradient",
                        )
                    ]
                ),
                "raw": SimpleNamespace(
                    usage_metadata={"input_tokens": 300, "output_tokens": 30}
                ),
            }

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeStructured()

    def fake_chat_openai(**kwargs):
        seen["model"] = kwargs["model"]
        return FakeModel()

    monkeypatch.setattr(art_director, "ChatOpenAI", fake_chat_openai)
    directions, usage = plan_card_art(
        package,
        cards,
        model_name="gpt-6-luna",
        api_key="test-only",
        get_image_bytes=lambda _key: None,
    )

    content = seen["messages"][1][1]
    image_item = next(part for part in content if part["type"] == "image_url")
    assert image_item["image_url"]["detail"] == "low"
    assert seen["model"] == "gpt-6-luna"
    assert directions == {
        1: {
            "copy_position": "bottom",
            "background_style": "gradient",
            "image_emphasis": "dominant",
        }
    }
    assert usage is not None
    assert usage.stage == "visual_layout_direction"
    assert usage.input_tokens == 300
    assert usage.estimated_cost_usd > 0


def test_art_director_thumbnail_is_bounded_before_base64_submission():
    image = Image.new("RGB", (2400, 3000), (90, 110, 130))
    binary = io.BytesIO()
    image.save(binary, format="PNG")

    data_url = art_director._thumbnail_data_url(binary.getvalue())
    encoded = data_url.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as thumbnail:
        assert thumbnail.size == (512, 640)
        assert thumbnail.format == "JPEG"


def test_layout_node_calls_vision_planner_once_and_keeps_cover_copy_below(monkeypatch):
    from ani_menu_agent import nodes

    package = ContentPackage.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples" / "generation_input.json").read_text(
            encoding="utf-8"
        )
    )
    copies = [
        CopyCard(
            page=index,
            role="cover" if index == 1 else "detail",
            title_lines=[f"테스트 제목 {index}"],
            body_sentences=["테스트 본문입니다."],
            image_id=image.id,
        )
        for index, image in enumerate(package.images[:3], start=1)
    ]
    calls = []

    def fake_plan(_package, cards, **_kwargs):
        calls.append([card.page for card in cards])
        return (
            {1: {"copy_position": "right", "background_style": "palette"}},
            UsageRecord(model="gpt-6-luna", stage="visual_layout_direction"),
        )

    monkeypatch.setattr(
        nodes,
        "get_settings",
        lambda: SimpleNamespace(
            visual_layout_enabled=True,
            image_upscale_enabled=False,
            openai_api_key="test-only",
            visual_layout_model="gpt-6-luna",
        ),
    )
    monkeypatch.setattr(nodes, "plan_card_art", fake_plan)
    monkeypatch.setattr(
        nodes,
        "AgentRepository",
        lambda: SimpleNamespace(get_image_bytes=lambda _key: None),
    )
    state = {
        "package": package.model_dump(mode="json"),
        "draft_cards": [card.model_dump(mode="json") for card in copies],
        "allow_llm": True,
        "trace": [],
    }

    first = nodes.layout_director(state)
    second = nodes.layout_director({**state, **first, "draft_cards": state["draft_cards"]})
    assert "package" in first, first
    rebuilt = ContentPackage.model_validate(first["package"])

    assert calls == [[card.page for card in copies]]
    assert first["visual_layout_plan_attempted"] is True
    assert second["visual_layout_plan_attempted"] is True
    assert rebuilt.cards[0].layout.text_overlay_box.y >= 0.65
    assert first["usage_records"][0]["stage"] == "visual_layout_direction"
