import json
from pathlib import Path
from types import SimpleNamespace

from ani_menu_agent.agents.content_generation.graph import (
    _route_after_layout,
    _route_generation_entry,
)
from ani_menu_agent.agents.reviewer.agent import (
    _review_message_content,
    _score_language,
    _select_revision_owner,
    _select_revision_owners,
    _visual_issues,
    review_content,
)
from ani_menu_agent.graph import build_graph, route_after_content_generation
from ani_menu_agent.models import (
    ContentPackage,
    CopyCard,
    CopyDeck,
    DetailArticleDraft,
    Fact,
    GenerationResult,
    LanguageReview,
    QualityIssue,
    SourceEvidence,
    UsageRecord,
)
from ani_menu_agent.nodes import (
    _merge_copy_revision,
    _remove_copy_pages,
    rights_preflight,
    route_review,
)
from ani_menu_agent.policy import POLICY

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
GENERATION_SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_revision_owner_prioritizes_visual_then_copy_and_layout_repairs() -> None:
    issues = [
        QualityIssue(code="COVER_EMPTY_GAP", message="cover gap", owner="layout_director"),
        QualityIssue(code="LANGUAGE_KOREAN_NATURALNESS", message="copy", owner="copy_writer"),
        QualityIssue(
            code="IMAGE_VISION_MISMATCH", message="relevance", owner="image_curator"
        ),
    ]

    assert _select_revision_owner(issues, prior_failures=0) == "image_curator"
    assert _select_revision_owner(issues, prior_failures=1) == "copy_writer"
    assert _select_revision_owners(issues) == [
        "copy_writer",
        "image_curator",
    ]


def test_multi_owner_revision_starts_with_copy_then_repairs_images_and_detail() -> None:
    issues = [
        QualityIssue(code="LANGUAGE_KOREAN_NATURALNESS", message="copy", owner="copy_writer"),
        QualityIssue(code="IMAGE_VISION_MISMATCH", message="image", owner="image_curator"),
        QualityIssue(code="DETAIL_BODY_FACTUAL_SUPPORT", message="detail", owner="detail_writer"),
    ]
    state = {
        "needs_revision": True,
        "revision_owner": "image_curator",
        "revision_owners": _select_revision_owners(issues),
    }

    assert _route_generation_entry(state) == "copy_writer"
    assert _route_after_layout(state) == "visual_repair"


def test_composition_feedback_routes_to_image_repair_without_rejecting_relevant_art() -> None:
    package = ContentPackage.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    review_data = _review_fixture(len(package.cards))
    review_data["cards"][0].update(
        {"image_relevance": 5, "image_composition": POLICY.image_composition_minimum - 1}
    )

    issues = _visual_issues(LanguageReview.model_validate(review_data), package)

    assert [issue.code for issue in issues] == ["IMAGE_COMPOSITION_REJECTED"]
    assert issues[0].owner == "image_curator"
    assert package.images[0].vision_review_rejected is False
    assert _select_revision_owner(issues, prior_failures=0) == "image_curator"
    assert _route_generation_entry(
        {"needs_revision": True, "revision_owners": _select_revision_owners(issues)}
    ) == "visual_repair"


def test_copy_revision_handles_composition_when_both_need_revision() -> None:
    issues = [
        QualityIssue(
            code="IMAGE_COMPOSITION_REJECTED",
            message="relevant cover is too small",
            owner="layout_director",
        ),
        QualityIssue(
            code="LANGUAGE_KOREAN_NATURALNESS",
            message="rewrite awkward phrase",
            owner="copy_writer",
        ),
    ]

    assert _select_revision_owner(issues, prior_failures=1) == "copy_writer"


def test_revision_owner_uses_registry_owner_for_cover_layout_feedback() -> None:
    issues = [QualityIssue(code="COVER_EMPTY_GAP", message="cover gap", owner="layout_director")]

    # Legacy issue payloads can carry a stale owner; the shared registry is authoritative.
    assert _select_revision_owner(issues, prior_failures=2) == "image_curator"


def test_content_generation_routes_sparse_image_decks_back_to_crawler() -> None:
    assert route_after_content_generation({"status": "needs_crawler_update"}) == (
        "crawler_update_required"
    )


def test_live_generation_preflight_blocks_discovery_only_source_before_llm() -> None:
    package = ContentPackage(
        request_id="discovery-only-preflight",
        topic="새 애니메이션 발표",
        content_kind="information",
        sources=[
            SourceEvidence(
                id="prtimes_rss",
                title="새 애니메이션 발표",
                url="https://prtimes.jp/main/html/rd/p/000000001.000000001.html",
                publisher="PR TIMES",
                is_official=False,
                role="discovery",
            )
        ],
        facts=[
            Fact(
                id="discovery-fact",
                statement="새 애니메이션 발표가 있었다.",
                source_ids=["prtimes_rss"],
                verified=True,
            )
        ],
    )

    result = rights_preflight(
        {
            "package": package.model_dump(mode="json"),
            "trace": [],
            "allow_llm": True,
        }
    )

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "SOURCE_APPROVAL_REQUIRED"
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
                "image_relevance": 5,
                "image_composition": 5,
                "visual_feedback": "이미지가 주제에 맞고 장면 자막이 없습니다.",
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
        "detail_writer",
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
                    "translation_naturalness": POLICY.language_translation_minimum - 1,
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


def test_visual_reviewer_ignores_subtitles_but_keeps_other_checks(monkeypatch) -> None:
    package = ContentPackage.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    original = package.images[0]
    package.images.append(
        original.model_copy(
            update={
                "id": f"{original.id}-thumbnail-variant",
                "url": f"{original.url}?width=580",
            }
        )
    )
    review_data = _review_fixture(len(package.cards))
    review_data["cards"][0].update(
        {
            # Backward-compatible payloads may still contain this retired field.
            "contains_anime_subtitles": True,
            "image_relevance": POLICY.image_relevance_minimum - 1,
            "image_composition": POLICY.image_composition_minimum - 1,
            "visual_feedback": "장면에 영어 대사가 박혀 있고 행사 주제와도 직접 맞지 않습니다.",
        }
    )
    review = LanguageReview.model_validate(review_data)

    issues = _visual_issues(review, package)

    assert {issue.code for issue in issues} == {
        "IMAGE_VISION_MISMATCH",
        "IMAGE_COMPOSITION_REJECTED",
    }
    assert package.images[0].vision_review_rejected is True
    assert package.images[-1].vision_review_rejected is True


def test_visual_rejections_survive_reviewer_state_transition(monkeypatch) -> None:
    package = ContentPackage.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    review_data = _review_fixture(len(package.cards))
    review_data["cards"][0].update(
        {
            "image_relevance": POLICY.image_relevance_minimum - 1,
            "visual_feedback": "다른 주제 이미지",
        }
    )

    class StructuredModel:
        def invoke(self, _messages):
            return {"parsed": LanguageReview.model_validate(review_data), "raw": None}

    class ChatModel:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, *_args, **_kwargs):
            return StructuredModel()

    class RepositoryStub:
        def persist_generated_content(self, *_args):
            return None

        def get_image_bytes(self, _key):
            return None

        def recent_copy_examples(self, **_kwargs):
            return []

        def active_content_types(self):
            return []

    monkeypatch.setattr("ani_menu_agent.agents.reviewer.agent.ChatOpenAI", ChatModel)
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.get_settings",
        lambda: SimpleNamespace(openai_api_key="test-key", model_name="test-model"),
    )
    monkeypatch.setattr("ani_menu_agent.agents.reviewer.agent.AgentRepository", RepositoryStub)

    result = review_content(
        {"package": package.model_dump(mode="json"), "trace": [], "usage_records": []}
    )

    assert result["package"]["images"][0]["vision_review_rejected"] is True


def test_vision_review_attaches_rendered_image_bytes(monkeypatch) -> None:
    package = ContentPackage.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    for card in package.cards:
        card.rendered_image_key = f"rendered-{card.page}"

    class RepositoryStub:
        def get_image_bytes(self, key):
            return (b"png-bytes-" + key.encode(), "image/png")

        def recent_copy_examples(self, **_kwargs):
            return []

        def active_content_types(self):
            return []

    monkeypatch.setattr("ani_menu_agent.agents.reviewer.agent.AgentRepository", RepositoryStub)

    parts, attached_pages, missing_pages = _review_message_content(package)

    assert attached_pages == [1, 2, 3, 4]
    assert missing_pages == []
    assert [part["type"] for part in parts[1:]] == ["image_url"] * 4
    assert parts[1]["image_url"]["detail"] == "original"


def test_adequate_korean_and_card_flow_do_not_create_language_issues() -> None:
    review = LanguageReview.model_validate(
        {
            "content_type_fit": 5,
            "cards": [
                {
                    "page": 1,
                    "korean_naturalness": 3,
                    "translation_naturalness": 4,
                    "reader_clarity": 4,
                    "fan_engagement": 4,
                    "card_flow": 3,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                },
                {
                    "page": 2,
                    "korean_naturalness": 4,
                    "translation_naturalness": 4,
                    "reader_clarity": 4,
                    "fan_engagement": 4,
                    "card_flow": 4,
                    "safety_respect": 5,
                    "safety_blocker": False,
                    "safety_category": None,
                },
            ],
        }
    )

    _, _, _, issues = _score_language(review)

    assert issues == []


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
                    "fan_engagement": (
                        POLICY.language_fan_engagement_minimum - 1 if page == 1 else 5
                    ),
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


def test_fourth_review_failure_archives_after_one_targeted_extra_repair() -> None:
    assert route_review({"review_failures": 1, "editorial_decision": "revise"}) == (
        "revision_checkpoint"
    )
    assert route_review({"review_failures": 2, "editorial_decision": "revise"}) == (
        "revision_checkpoint"
    )
    assert route_review({"review_failures": 3, "editorial_decision": "revise"}) == (
        "revision_checkpoint"
    )
    assert route_review({"review_failures": 4, "editorial_decision": "revise"}) == (
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
    assert result["trace"] == ["visual_repair", "detail_writer"]
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
        "ani_menu_agent.nodes.validate_generation_sources",
        lambda *_args: {"official_event"},
    )
    monkeypatch.setattr(
        "ani_menu_agent.nodes.supported_content_types_for_sources",
        lambda _source_ids: {"event"},
    )

    def assign_test_work_id(test_package):
        test_package.menu_work_ids = [123]
        return test_package

    monkeypatch.setattr("ani_menu_agent.nodes.attach_menu_work_ids", assign_test_work_id)
    monkeypatch.setattr(
        "ani_menu_agent.nodes.generate_copy_deck", lambda package, **kwargs: generated
    )

    class FakeDetailStructured:
        def invoke(self, _messages):
            return {
                "parsed": DetailArticleDraft(
                    body="행사 정보를 바탕으로 준비할 내용을 차근차근 살펴볼 수 있습니다. " * 12,
                    fact_ids=["f1", "f2", "f3", "f4"],
                ),
                "raw": SimpleNamespace(usage_metadata={"input_tokens": 12, "output_tokens": 10}),
            }

    class FakeDetailModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeDetailStructured()

    monkeypatch.setattr(
        "ani_menu_agent.agents.content_generation.detail_writer.ChatOpenAI",
        lambda **_kwargs: FakeDetailModel(),
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.content_generation.detail_writer.get_settings",
        lambda: SimpleNamespace(openai_api_key="test-key", model_name="test-model"),
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
    assert len(result["package"]["detail_body"]) >= 180
    assert result["package"]["detail_fact_ids"] == ["f1", "f2", "f3", "f4"]
    assert result["package"]["detail_source_ids"] == ["official_event"]


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
