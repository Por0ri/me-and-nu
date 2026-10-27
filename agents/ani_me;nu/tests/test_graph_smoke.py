import json
from pathlib import Path

from ani_menu_agent.agents.reviewer.agent import _score_language
from ani_menu_agent.graph import build_graph, route_after_content_generation
from ani_menu_agent.models import (
    CopyCard,
    CopyDeck,
    GenerationResult,
    LanguageReview,
    UsageRecord,
)
from ani_menu_agent.nodes import _merge_copy_revision, _remove_copy_pages, route_review

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
GENERATION_SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_content_generation_routes_sparse_image_decks_back_to_crawler() -> None:
    assert route_after_content_generation({"status": "needs_crawler_update"}) == (
        "crawler_update_required"
    )
    assert route_after_content_generation({"status": "generated_pending_review"}) == (
        "reviewer_agent"
    )


def _review_fixture(pages: int) -> dict:
    return {
        "summary": "오프라인 구조 시험용 평가",
        "content_type_fit": 5,
        "content_type_feedback": "행사 유형이 실제 근거와 일치합니다.",
        "cards": [
            {
                "page": page,
                "korean_naturalness": 5,
                "translation_naturalness": 5,
                "reader_clarity": 5,
                "fan_engagement": 5,
                "card_flow": 5,
                "safety_respect": 5,
                "safety_blocker": False,
                "safety_category": None,
                "feedback": "오프라인 시험 fixture",
            }
            for page in range(1, pages + 1)
        ],
    }


def test_graph_uses_independent_reviewer_without_approving_fixture_for_feed(monkeypatch) -> None:
    from ani_menu_agent.agents.reviewer import agent as reviewer_agent

    monkeypatch.setattr(
        reviewer_agent,
        "AgentRepository",
        lambda: (_ for _ in ()).throw(AssertionError("offline fixture must not access a DB")),
    )
    package = json.loads(SAMPLE.read_text(encoding="utf-8"))
    result = build_graph().invoke(
        {
            "request_id": package["request_id"],
            "topic": package["topic"],
            "content_kind": package["content_kind"],
            "package": package,
            "trace": [],
            "revision_count": 0,
            "max_revision": 2,
            "review_fixture": _review_fixture(len(package["cards"])),
        }
    )

    assert result["status"] == "offline_review_only"
    assert result["qa_report"] == []
    assert result["trace"] == [
        "intake",
        "rights_preflight",
        "copy_writer",
        "layout_director",
        "review_agent",
    ]


def test_invalid_input_stops_without_human_review() -> None:
    result = build_graph().invoke({"package": {}, "trace": []})
    assert result["status"] == "invalid_input"
    assert result["qa_report"][0]["code"] == "INVALID_INPUT"


def test_low_translation_naturalness_fails_language_gate() -> None:
    review = LanguageReview.model_validate(
        {
            "content_type_fit": 5,
            "summary": "일본어 행사 문구 번역을 자연스럽게 고쳐야 함",
            "cards": [
                {
                    "page": 1,
                    "korean_naturalness": 5,
                    "translation_naturalness": 2,
                    "reader_clarity": 5,
                    "fan_engagement": 5,
                    "card_flow": 5,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                    "feedback": "직역투 표현을 한국어 어순으로 바꿔야 함",
                },
                {
                    "page": 2,
                    "korean_naturalness": 5,
                    "translation_naturalness": 5,
                    "reader_clarity": 5,
                    "fan_engagement": 5,
                    "card_flow": 5,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                },
                {
                    "page": 3,
                    "korean_naturalness": 5,
                    "translation_naturalness": 5,
                    "reader_clarity": 5,
                    "fan_engagement": 5,
                    "card_flow": 5,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                },
            ],
        }
    )
    score, safety_score, card_scores, issues = _score_language(review)

    assert score < 20
    assert safety_score == 10
    assert card_scores[1] >= 75
    assert any(issue.code == "LANGUAGE_TRANSLATION_NATURALNESS" for issue in issues)


def test_low_fan_engagement_fails_language_gate() -> None:
    review = LanguageReview.model_validate(
        {
            "content_type_fit": 5,
            "cards": [
                {
                    "page": page,
                    "korean_naturalness": 5,
                    "translation_naturalness": 5,
                    "reader_clarity": 5,
                    "fan_engagement": 2 if page == 1 else 5,
                    "card_flow": 5,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                }
                for page in range(1, 4)
            ],
        }
    )

    _, _, _, issues = _score_language(review)

    assert any(issue.code == "LANGUAGE_FAN_ENGAGEMENT" for issue in issues)


def test_cover_and_ending_accept_adequate_fan_hook_score() -> None:
    review = LanguageReview.model_validate(
        {
            "content_type_fit": 5,
            "cards": [
                {
                    "page": page,
                    "korean_naturalness": 5,
                    "translation_naturalness": 5,
                    "reader_clarity": 5,
                    "fan_engagement": 3,
                    "card_flow": 5,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                }
                for page in range(1, 4)
            ],
        }
    )

    _, _, _, issues = _score_language(review, hook_pages={1, 3})

    assert not any(issue.code == "LANGUAGE_FAN_ENGAGEMENT" for issue in issues)


def test_safety_issue_is_a_non_compensable_reviewer_blocker() -> None:
    review = LanguageReview.model_validate(
        {
            "content_type_fit": 5,
            "cards": [
                {
                    "page": page,
                    "korean_naturalness": 5,
                    "translation_naturalness": 5,
                    "reader_clarity": 5,
                    "fan_engagement": 5,
                    "card_flow": 5,
                    "safety_respect": 1 if page == 1 else 5,
                    "safety_blocker": page == 1,
                    "safety_category": "hate_or_discrimination" if page == 1 else None,
                    "feedback": (
                        "집단 비하 표현을 제거하고 소재를 상황 중심으로 바꾸세요."
                        if page == 1
                        else ""
                    ),
                }
                for page in range(1, 4)
            ],
        }
    )
    _, safety_score, _, issues = _score_language(review)

    assert safety_score < 10
    assert {issue.code for issue in issues} >= {
        "SAFETY_RESPECT_LOW",
        "SAFETY_HARMFUL_CONTENT",
    }


def test_third_review_failure_archives_instead_of_retrying_again() -> None:
    assert route_review({"review_failures": 1, "editorial_decision": "revise"}) == (
        "revision_checkpoint"
    )
    assert route_review({"review_failures": 2, "editorial_decision": "revise"}) == (
        "revision_checkpoint"
    )
    assert route_review({"review_failures": 3, "editorial_decision": "revise"}) == (
        "archive_rejected"
    )


def test_copy_revision_replaces_only_flagged_page_and_preserves_other_cards() -> None:
    existing = [
        {
            "page": 1,
            "role": "cover",
            "title_lines": ["기존 표지"],
            "image_id": "image-1",
        },
        {
            "page": 2,
            "role": "body",
            "title_lines": ["수정 전"],
            "image_id": "image-2",
        },
        {
            "page": 3,
            "role": "ending",
            "title_lines": ["기존 마무리"],
            "image_id": "image-3",
        },
    ]
    replacements = [
        CopyCard(
            page=1,
            role="cover",
            title_lines=["표지도 바꾸지 않음"],
            image_id="image-other-1",
        ),
        CopyCard(
            page=2,
            role="body",
            title_lines=["수정 후"],
            image_id="image-2-new",
        ),
        CopyCard(
            page=3,
            role="ending",
            title_lines=["마무리도 바꾸지 않음"],
            image_id="image-other-3",
        ),
    ]

    merged = _merge_copy_revision(existing, replacements, [2])

    assert [card.title_lines for card in merged] == [
        ["기존 표지"],
        ["수정 후"],
        ["기존 마무리"],
    ]
    assert [card.image_id for card in merged] == ["image-1", "image-2-new", "image-3"]


def test_explicit_reviewer_card_removal_reindexes_remaining_cards() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    cards = [CopyCard.model_validate(card) for card in payload["cards"]]

    kept = _remove_copy_pages(cards, [2])

    assert [card.page for card in kept] == [1, 2, 3]
    assert [card.role for card in kept] == ["cover", "detail", "ending"]


def test_visual_repair_preserves_copy_and_uses_each_source_image_only_once() -> None:
    from ani_menu_agent.agents.content_generation.graph import build_content_generation_agent
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import ContentPackage

    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]
    copies[2] = copies[2].model_copy(update={"image_id": copies[1].image_id})
    package.cards = build_cards(copies, package.images)
    titles_before = [card.title_lines for card in package.cards]

    result = build_content_generation_agent().invoke(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "needs_revision": True,
            "revision_owner": "image_curator",
            "render_required": False,
        }
    )
    repaired = ContentPackage.model_validate(result["package"])
    source_images = [card.image_id for card in repaired.cards if card.visual_mode == "source_image"]

    assert result["status"] == "visuals_repaired"
    assert result["trace"] == ["visual_repair"]
    assert len(source_images) == len(set(source_images))
    assert [card.title_lines for card in repaired.cards] == titles_before


def test_empty_cards_use_structured_generation_path(monkeypatch) -> None:
    package = json.loads(GENERATION_SAMPLE.read_text(encoding="utf-8"))
    deck = CopyDeck.model_validate(
        {
            "content_type": "event",
            "editorial_angle": "팬들이 기다린 공식 전시 소식과 방문 준비를 한눈에 정리",
            "cover_mode": "surprising_fact",
            "ending_mode": "fan_empathy",
            "cards": [
                {
                    "page": 1,
                    "role": "cover",
                    "title_lines": ["11월 8일", "행사가 열린다"],
                    "protected_phrases": ["11월 8일"],
                    "fact_ids": ["f1"],
                    "image_id": "i1",
                },
                {
                    "page": 2,
                    "role": "detail",
                    "title_lines": ["공식 전시와 상품 판매"],
                    "fact_ids": ["f2"],
                    "image_id": "i2",
                },
                {
                    "page": 3,
                    "role": "detail",
                    "title_lines": ["입장 전, 예매부터"],
                    "fact_ids": ["f3"],
                    "image_id": "i3",
                },
                {
                    "page": 4,
                    "role": "ending",
                    "title_lines": ["특전은 한정이다", "시간도 같은 편이다"],
                    "fact_ids": ["f4"],
                    "image_id": "i4",
                    "ending_joke": True,
                },
            ],
        }
    )
    generated = GenerationResult(deck=deck, usage=UsageRecord(model="test"))
    monkeypatch.setattr(
        "ani_menu_agent.nodes.generate_copy_deck", lambda package, **kwargs: generated
    )
    result = build_graph().invoke(
        {
            "request_id": package["request_id"],
            "topic": package["topic"],
            "content_kind": package["content_kind"],
            "package": package,
            "trace": [],
            "revision_count": 0,
            "max_revision": 2,
            "allow_llm": True,
            "review_fixture": _review_fixture(4),
        }
    )
    assert result["qa_report"] == []
    assert len(result["package"]["cards"]) == 4
    assert result["editorial_decision"] == "offline_fixture_passed"
    assert result["package"]["editorial_angle"].startswith("팬들이 기다린")


def test_offline_fixture_uses_no_llm(monkeypatch) -> None:
    package = json.loads(GENERATION_SAMPLE.read_text(encoding="utf-8"))
    cards = [
        {
            "page": 1,
            "role": "cover",
            "title_lines": ["11월 8일", "행사가 열린다"],
            "protected_phrases": ["11월 8일"],
            "fact_ids": ["f1"],
            "image_id": "i1",
        },
        {
            "page": 2,
            "role": "detail",
            "title_lines": ["공식 전시와 상품 판매"],
            "fact_ids": ["f2"],
            "image_id": "i2",
        },
        {
            "page": 3,
            "role": "detail",
            "title_lines": ["입장 전, 예매부터"],
            "fact_ids": ["f3"],
            "image_id": "i3",
        },
        {
            "page": 4,
            "role": "ending",
            "title_lines": ["특전은 한정이다", "시간도 같은 편이다"],
            "fact_ids": ["f4"],
            "image_id": "i4",
            "ending_joke": True,
        },
    ]
    monkeypatch.setattr(
        "ani_menu_agent.nodes.generate_copy_deck",
        lambda package, issues: (_ for _ in ()).throw(AssertionError("LLM called")),
    )
    result = build_graph().invoke(
        {
            "request_id": package["request_id"],
            "topic": package["topic"],
            "content_kind": package["content_kind"],
            "package": package,
            "offline_cards": cards,
            "trace": [],
            "revision_count": 0,
            "max_revision": 2,
            "allow_llm": False,
            "review_fixture": _review_fixture(4),
        }
    )
    assert result["usage_records"][0]["model"] == "offline-fixture"
    assert result["usage_records"][0]["estimated_cost_usd"] == 0
    assert len(result["package"]["cards"]) == 4
    assert all(card["visual_mode"] == "source_image" for card in result["package"]["cards"])


def test_unverified_image_pool_returns_for_recollection_before_copywriting() -> None:
    package = json.loads(GENERATION_SAMPLE.read_text(encoding="utf-8"))
    for image in package["images"]:
        image["rights_status"] = "needs_review"
    cards = [
        {
            "page": index,
            "role": "cover" if index == 1 else "ending" if index == 4 else "detail",
            "title_lines": [f"카드 {index}"],
            "fact_ids": [f"f{index}"],
            "image_id": f"i{index}",
        }
        for index in range(1, 5)
    ]
    result = build_graph().invoke(
        {
            "request_id": package["request_id"],
            "topic": package["topic"],
            "content_kind": package["content_kind"],
            "package": package,
            "offline_cards": cards,
            "trace": [],
            "revision_count": 0,
            "max_revision": 2,
            "allow_llm": False,
            "review_fixture": _review_fixture(4),
        }
    )
    assert "copy_writer" not in result["trace"]
    assert "usage_records" not in result
    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "SOURCE_IMAGE_POOL_INSUFFICIENT"
    assert "crawler_update_required" in result["trace"]
