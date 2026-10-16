import json
from copy import deepcopy
from pathlib import Path

from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentKind, ContentPackage, CopyCard, NormalizedBox
from ani_menu_agent.nodes import layout_director
from ani_menu_agent.quality import run_quality_checks, score_quality

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def issue_codes(payload: dict) -> set[str]:
    package = ContentPackage.model_validate(payload)
    return {issue.code for issue in run_quality_checks(package)}


def test_sample_passes_all_code_guardrails() -> None:
    payload = load_sample()
    assert issue_codes(payload) == set()
    package = ContentPackage.model_validate(payload)
    report = score_quality(package, [])
    assert report["dimensions"]["image_full_visibility"] == 10
    assert sum(report["dimensions"].values()) == 70
    evaluated = score_quality(
        package, [], language_score=20, safety_score=10, card_language_scores={1: 100}
    )
    assert sum(evaluated["dimensions"].values()) == 100
    assert evaluated["dimensions"]["korean_naturalness_and_translation"] == 20
    assert evaluated["dimensions"]["safety_and_respect"] == 10


def test_safety_blocker_cannot_be_offset_by_other_scores() -> None:
    from ani_menu_agent.models import QualityIssue

    package = ContentPackage.model_validate(load_sample())
    report = score_quality(
        package,
        [
            QualityIssue(
                code="SAFETY_HARMFUL_CONTENT",
                message="차별적 표현",
                page=1,
                owner="copy_writer",
            )
        ],
        language_score=20,
        safety_score=10,
        card_language_scores={1: 100},
    )

    assert report["total"] == 100
    assert "SAFETY_HARMFUL_CONTENT" in report["hard_blockers"]
    assert report["decision"] == "revise"


def test_humor_may_use_unverified_trend_material_as_inspiration() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.content_kind = ContentKind.HUMOR
    package.sources[0].is_official = False
    package.sources[0].role = "trend"
    package.facts[0].verified = False
    codes = {issue.code for issue in run_quality_checks(package)}
    assert "SOURCE_NO_OFFICIAL" not in codes
    assert "FACT_UNVERIFIED" not in codes


def test_image_only_page_is_rejected() -> None:
    payload = load_sample()
    payload["cards"][1]["title_lines"] = [""]
    payload["cards"][1]["body_sentences"] = []
    assert "IMAGE_ONLY_PAGE" in issue_codes(payload)


def test_cover_and_ending_roles_are_required_for_story_flow() -> None:
    payload = load_sample()
    payload["cards"][0]["role"] = "detail"
    payload["cards"][-1]["role"] = "detail"

    assert {"COVER_ROLE_MISSING", "ENDING_ROLE_MISSING"}.issubset(issue_codes(payload))


def test_split_protected_phrase_returns_to_copy_writer() -> None:
    payload = load_sample()
    payload["cards"][0]["title_lines"] = ["2026년 10월 6일", "정오부터 예약"]
    payload["cards"][0]["protected_phrases"] = ["10월 6일 정오"]
    package = ContentPackage.model_validate(payload)

    issue = next(
        item for item in run_quality_checks(package) if item.code == "PROTECTED_PHRASE_SPLIT"
    )

    assert issue.page == 1
    assert issue.owner == "copy_writer"


def test_japanese_terms_require_korean_work_title_and_translation_pair() -> None:
    payload = load_sample()
    payload["term_glossary"] = [
        {"original": "夏目友人帳", "korean": "나츠메 우인장", "category": "work_title"},
        {"original": "ニャンコ先生", "korean": "냥코 선생님", "category": "character"},
    ]
    payload["cards"][0]["title_lines"] = ["「夏目友人帳」의 니ャン코 선생님"]
    package = ContentPackage.model_validate(payload)

    codes = {issue.code for issue in run_quality_checks(package)}

    assert "WORK_TITLE_NOT_KOREAN" in codes
    assert "JAPANESE_TERM_UNPAIRED" in codes
    assert "JAPANESE_COPY_UNTRANSLATED" in codes


def test_korean_first_bilingual_title_passes_translation_guardrail() -> None:
    payload = load_sample()
    payload["term_glossary"] = [
        {"original": "夏目友人帳", "korean": "나츠메 우인장", "category": "work_title"},
        {"original": "ニャンコ先生", "korean": "냥코 선생님", "category": "character"},
    ]
    payload["cards"][0]["title_lines"] = ["나츠메 우인장(夏目友人帳)", "냥코 선생님 출동"]
    package = ContentPackage.model_validate(payload)

    codes = {issue.code for issue in run_quality_checks(package)}

    assert "WORK_TITLE_NOT_KOREAN" not in codes
    assert "JAPANESE_TERM_UNPAIRED" not in codes
    assert "JAPANESE_COPY_UNTRANSLATED" not in codes


def test_raw_image_reuse_is_rejected() -> None:
    payload = load_sample()
    payload["cards"][2]["image_id"] = payload["cards"][1]["image_id"]
    assert "IMAGE_REUSED_RAW" in issue_codes(payload)


def test_image_that_needs_excessive_upscaling_returns_to_image_collection() -> None:
    payload = load_sample()
    payload["images"][0]["width"] = 180
    payload["images"][0]["height"] = 100
    package = ContentPackage.model_validate(payload)

    issue = next(
        item for item in run_quality_checks(package) if item.code == "IMAGE_LOW_RESOLUTION"
    )
    report = score_quality(package, [issue], language_score=20, safety_score=10)

    assert issue.owner == "image_curator"
    assert issue.page == 1
    assert report["decision"] == "return_to_crawler"


def test_raw_image_reuse_is_sent_back_to_crawler_not_copy_revision() -> None:
    payload = load_sample()
    payload["cards"][2]["image_id"] = payload["cards"][1]["image_id"]
    package = ContentPackage.model_validate(payload)
    issues = run_quality_checks(package)
    reuse_issue = next(issue for issue in issues if issue.code == "IMAGE_REUSED_RAW")
    report = score_quality(package, issues, language_score=20, safety_score=10)

    assert reuse_issue.owner == "image_curator"
    assert report["decision"] == "return_to_crawler"
    assert "IMAGE_REUSED_RAW" in report["hard_blockers"]


def test_unused_incomplete_campaign_fact_does_not_block_other_content() -> None:
    payload = load_sample()
    payload["facts"].append(
        {
            "id": "unused-campaign",
            "statement": "게임 내 캠페인 응모 정보",
            "source_ids": [payload["sources"][0]["id"]],
            "verified": True,
            "fact_type": "campaign",
            "details": {"source_quote": "キャンペーン"},
        }
    )
    package = ContentPackage.model_validate(payload)

    assert "CAMPAIGN_DETAILS_MISSING" not in {issue.code for issue in run_quality_checks(package)}


def test_blurred_background_does_not_excuse_repeating_the_same_foreground() -> None:
    payload = load_sample()
    payload["cards"][2]["image_id"] = payload["cards"][1]["image_id"]
    payload["cards"][2]["image_reuse_mode"] = "blur_background"
    assert "IMAGE_REUSED_RAW" in issue_codes(payload)


def test_insufficient_unique_image_pool_is_a_crawler_blocker() -> None:
    payload = load_sample()
    payload["images"] = payload["images"][:2]
    package = ContentPackage.model_validate(payload)
    issues = run_quality_checks(package)
    report = score_quality(package, issues)

    assert "IMAGE_COVERAGE_INSUFFICIENT" in {issue.code for issue in issues}
    assert report["decision"] == "return_to_crawler"


def test_preflight_returns_to_crawler_before_copy_generation_for_sparse_images() -> None:
    from ani_menu_agent.nodes import rights_preflight

    payload = load_sample()
    payload["images"] = payload["images"][:2]
    result = rights_preflight({"package": payload, "trace": []})

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "IMAGE_COVERAGE_INSUFFICIENT"


def test_blurred_fallback_is_not_allowed_when_four_unique_images_exist() -> None:
    payload = load_sample()
    duplicate = deepcopy(payload["cards"][1])
    duplicate["page"] = 5
    duplicate["image_id"] = payload["cards"][0]["image_id"]
    duplicate["image_reuse_mode"] = "blur_background"
    payload["cards"].append(duplicate)
    assert "IMAGE_REUSED_RAW" in issue_codes(payload)


def test_large_cover_gap_is_rejected() -> None:
    payload = load_sample()
    payload["cards"][0]["layout"]["copy_box"]["y"] = 0.82
    assert "COVER_EMPTY_GAP" in issue_codes(payload)


def test_generic_ending_joke_is_rejected() -> None:
    payload = deepcopy(load_sample())
    payload["cards"][-1]["title_lines"] = ["지갑이 열린다"]
    assert "ENDING_GENERIC" in issue_codes(payload)


def test_official_image_without_reuse_permission_is_rejected() -> None:
    payload = load_sample()
    payload["images"][0]["rights_status"] = "needs_review"
    payload["images"][0].pop("rights_evidence_url", None)
    assert "IMAGE_RIGHTS_REVIEW" in issue_codes(payload)


def test_official_public_image_risk_acceptance_is_not_misreported_as_permission() -> None:
    payload = load_sample()
    image = payload["images"][0]
    image["rights_status"] = "official_public_risk_accepted"
    image["rights_evidence_url"] = image["source_url"]
    image["rights_checked_at"] = "2026-10-12T00:00:00+09:00"
    image["attribution_text"] = "공식 이미지 출처: 샘플 제작위원회"
    package = ContentPackage.model_validate(payload)
    assert not package.images[0].cleared_for_mennu
    assert not any(issue.code == "IMAGE_RIGHTS_REVIEW" for issue in run_quality_checks(package))


def test_official_public_risk_is_rejected_if_asset_page_is_not_from_primary_source() -> None:
    payload = load_sample()
    image = payload["images"][0]
    image["rights_status"] = "official_public_risk_accepted"
    image["source_url"] = "https://unrelated.invalid/reupload"
    image["rights_evidence_url"] = image["source_url"]
    image["rights_checked_at"] = "2026-10-12T00:00:00+09:00"
    image["attribution_text"] = "공식 이미지 출처: 샘플 제작위원회"
    assert "IMAGE_RIGHTS_REVIEW" in issue_codes(payload)


def test_subject_crop_is_rejected_after_scaling() -> None:
    payload = load_sample()
    payload["images"][1]["has_alpha"] = True
    payload["images"][1]["subject_box"] = {
        "x": 0.15,
        "y": 0.01,
        "width": 0.70,
        "height": 0.94,
    }
    payload["cards"][1]["layout"]["image_scale"] = 1.25
    payload["cards"][1]["layout"]["image_offset_y"] = -0.05
    assert "SUBJECT_CLIPPED" in issue_codes(payload)


def test_crop_risk_is_a_hard_blocker_even_when_total_score_is_high() -> None:
    payload = load_sample()
    payload["cards"][0]["layout"]["fit_mode"] = "cover_safe"
    package = ContentPackage.model_validate(payload)
    issues = run_quality_checks(package)
    report = score_quality(package, issues)

    assert "IMAGE_CROP_RISK" in report["hard_blockers"]
    assert report["decision"] == "revise"
    assert report["dimensions"]["image_full_visibility"] == 0


def test_image_zoom_or_offset_is_blocked() -> None:
    payload = load_sample()
    payload["cards"][1]["layout"]["image_scale"] = 1.01
    payload["cards"][2]["layout"]["image_offset_y"] = -0.01

    codes = issue_codes(payload)

    assert "IMAGE_CROP_RISK" in codes


def test_image_box_overflow_is_blocked() -> None:
    payload = load_sample()
    payload["cards"][0]["layout"]["image_box"]["width"] = 1.0

    assert "BOX_OVERFLOW" in issue_codes(payload)


def test_layout_engine_uses_full_image_without_zoom_for_every_aspect_ratio() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copy_cards = [CopyCard.model_validate(card) for card in payload["cards"]]

    cards = build_cards(copy_cards, package.images)

    assert all(card.layout.fit_mode in {"contain", "contain_blur"} for card in cards)
    assert all(card.layout.image_scale == 1.0 for card in cards)
    assert all(card.layout.image_offset_y == 0.0 for card in cards)


def test_layout_engine_maps_unknown_image_ids_to_a_real_asset() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copy_cards = [CopyCard.model_validate(card) for card in payload["cards"]]
    copy_cards[0] = copy_cards[0].model_copy(update={"image_id": "hallucinated-image-id"})

    cards = build_cards(copy_cards, package.images)

    assert cards[0].image_id in {image.id for image in package.images}


def test_layout_director_reassigns_repeated_image_choices_to_unique_assets() -> None:
    from ani_menu_agent.nodes import layout_director

    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copies = [CopyCard.model_validate(card) for card in payload["cards"]]
    copies[1] = copies[1].model_copy(update={"image_id": copies[0].image_id})
    result = layout_director(
        {
            "package": package.model_dump(mode="json"),
            "draft_cards": [card.model_dump(mode="json") for card in copies],
            "trace": [],
        }
    )
    rebuilt = ContentPackage.model_validate(result["package"])

    assert len({card.image_id for card in rebuilt.cards}) == len(rebuilt.cards)
    assert {card.image_reuse_mode for card in rebuilt.cards} == {"unique"}


def test_layout_director_returns_for_more_images_when_card_count_exceeds_pool() -> None:
    from ani_menu_agent.nodes import layout_director

    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    package.images = package.images[:3]
    copies = [CopyCard.model_validate(card) for card in payload["cards"]]
    result = layout_director(
        {
            "package": package.model_dump(mode="json"),
            "draft_cards": [card.model_dump(mode="json") for card in copies],
            "trace": [],
        }
    )

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "IMAGE_COVERAGE_INSUFFICIENT"


def test_layouts_vary_by_card_shape_and_role() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copies = [CopyCard.model_validate(card) for card in payload["cards"]]

    cards = build_cards(copies, package.images)

    assert len({card.layout.template for card in cards}) >= 3


def test_layout_engine_uses_subject_position_and_alpha_for_image_led_layouts() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    image = package.images[0].model_copy(
        update={
            "width": 600,
            "height": 1200,
            "subject_hint": "left",
            "has_alpha": True,
            "subject_box": NormalizedBox(x=0.2, y=0.1, width=0.6, height=0.8),
        }
    )
    copy = CopyCard(
        page=2,
        role="character_profile",
        title_lines=["소대의 숨은 조커"],
        body_sentences=["쿠루루는 작전과 통신을 맡는다."],
        image_id=image.id,
    )

    cards = build_cards(
        [
            copy.model_copy(update={"page": 1}),
            copy,
            copy.model_copy(update={"page": 3}),
        ],
        [image],
    )
    card = cards[1]

    assert card.layout.template == "cutout_left"
    assert card.layout.fit_mode == "contain_blur"
    assert card.layout.image_scale == 1.0


def test_layout_engine_alternates_portrait_image_sides_without_cropping() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    image = package.images[0].model_copy(
        update={"width": 600, "height": 1200, "subject_hint": "center"}
    )
    copies = [
        CopyCard(
            page=page,
            role="character_profile",
            title_lines=["대장을 지키는 소대원"],
            body_sentences=["케로로 소대에서 맡은 역할을 살펴본다."],
            image_id=image.id,
        )
        for page in (1, 2, 3, 4)
    ]

    cards = build_cards(copies, [image])

    assert [card.layout.template for card in cards[1:3]] == [
        "portrait_left",
        "portrait_right",
    ]
    assert all(card.layout.fit_mode == "contain_blur" for card in cards[1:3])
    assert all(card.layout.image_scale == 1.0 for card in cards[1:3])
    assert all(card.layout.image_box.width == 0.58 for card in cards[1:3])
    assert all(card.layout.image_box.height == 0.90 for card in cards[1:3])


def test_information_dense_poster_uses_nearly_full_height_without_cropping() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    image = package.images[0].model_copy(update={"width": 500, "height": 2000})
    copies = [
        CopyCard(
            page=page,
            role="event_detail",
            title_lines=["행사 정보 한눈에"],
            body_sentences=["일정과 특전을 확인해 보자."],
            image_id=image.id,
        )
        for page in (1, 2, 3)
    ]

    cards = build_cards(copies, [image])
    poster = cards[1]

    assert poster.layout.template in {"poster_left", "poster_right"}
    assert poster.layout.image_box.height == 0.94
    assert poster.layout.fit_mode == "contain_blur"
    assert poster.layout.image_scale == 1.0
    assert cards[0].layout.template in {"poster_left", "poster_right"}
    assert cards[0].layout.image_box.height == 0.94


def test_ocr_density_routes_portrait_infographic_to_poster_layout() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    image = package.images[0].model_copy(
        update={
            "width": 900,
            "height": 1400,
            "visible_text": [f"line {index} campaign detail" for index in range(8)],
            "text_confidence": 0.8,
        }
    )
    copies = [
        CopyCard(
            page=page,
            role="event_detail",
            title_lines=["행사 안내"],
            body_sentences=["핵심 일정만 빠르게 확인하자."],
            image_id=image.id,
        )
        for page in (1, 2, 3)
    ]

    cards = build_cards(copies, [image])

    assert cards[1].layout.template in {"poster_left", "poster_right"}
    assert cards[1].layout.image_box.height == 0.94


def test_layout_revision_rebuilds_legacy_cropped_layout_without_llm() -> None:
    payload = load_sample()
    payload["cards"][0]["layout"]["fit_mode"] = "cover_safe"
    result = layout_director(
        {
            "package": payload,
            "needs_revision": True,
            "revision_owner": "layout_director",
            "trace": [],
        }
    )
    rebuilt = ContentPackage.model_validate(result["package"])

    assert result["status"] == "layout_rebuilt_without_crop"
    assert all(card.layout.fit_mode == "contain_blur" for card in rebuilt.cards)
    assert all(card.layout.image_scale == 1.0 for card in rebuilt.cards)
