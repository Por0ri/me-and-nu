import json
from copy import deepcopy
from pathlib import Path

import pytest

from ani_menu_agent.classification import infer_strong_content_type
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentKind, ContentPackage, CopyCard, ImageAsset, NormalizedBox
from ani_menu_agent.nodes import (
    _normalize_structural_roles,
    layout_director,
    rights_preflight,
)
from ani_menu_agent.quality import run_quality_checks, score_quality
from ani_menu_agent.review_rules import score_penalty

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


def test_source_publication_date_is_not_shown_as_card_copy() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.sources[0].published_at = "2026-10-20"
    package.event_start = None
    package.event_end = None
    package.cards[0].body_sentences = ["공식 발표 당시인 10월 20일에 사전 등록이 시작됐다."]

    assert "SOURCE_PUBLICATION_DATE_SHOWN" in {
        issue.code for issue in run_quality_checks(package)
    }


def test_event_date_may_match_source_publication_date() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.sources[0].published_at = "2026-10-20"
    package.event_start = "2026-10-20"
    package.event_end = "2026-10-20"
    package.content_type = "event"
    package.cards[0].body_sentences = ["행사는 10월 20일 하루 열린다."]

    assert "SOURCE_PUBLICATION_DATE_SHOWN" not in {
        issue.code for issue in run_quality_checks(package)
    }


def test_event_that_has_already_ended_is_rejected() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.content_type = "event"
    package.event_start = "2020-10-20"
    package.event_end = "2020-10-20"

    assert "EVENT_ALREADY_ENDED" in {
        issue.code for issue in run_quality_checks(package)
    }


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
    package.facts[0].fact_type = "community_debate"
    package.facts[0].statement = "팬덤 토론 소재: 나루토와 사스케, 누가 더 강할까?"
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


def test_information_deck_rejects_reused_interior_evidence() -> None:
    payload = load_sample()
    shared_fact_ids = payload["cards"][0]["fact_ids"]
    payload["cards"][0]["role"] = "detail"
    payload["cards"][1]["role"] = "detail"
    payload["cards"][2]["role"] = "ending"
    payload["cards"][0]["fact_ids"] = shared_fact_ids
    payload["cards"][1]["fact_ids"] = shared_fact_ids
    package = ContentPackage.model_validate(payload)

    assert "CARD_FACT_REUSED" in {issue.code for issue in run_quality_checks(package)}


def test_stale_source_blocks_time_sensitive_information() -> None:
    payload = load_sample()
    payload["sources"][0]["published_at"] = "2025-01-01T09:00:00+09:00"
    payload["facts"][0]["time_sensitive"] = True
    package = ContentPackage.model_validate(payload)

    assert "SOURCE_STALE" in {issue.code for issue in run_quality_checks(package)}


def test_missing_source_publication_date_does_not_block_content() -> None:
    payload = load_sample()
    payload["sources"][0]["published_at"] = None

    assert "SOURCE_PUBLISHED_AT_MISSING" not in issue_codes(payload)


def test_event_period_dates_from_verified_range_must_be_structured() -> None:
    payload = load_sample()
    payload["content_type"] = "event"
    payload["facts"][0]["fact_type"] = "event_schedule"
    payload["facts"][0].setdefault("details", {})["source_quote"] = (
        "개최 기간은 2026년 10월 23일부터 10월 27일까지"
    )

    assert "EVENT_PERIOD_MISSING" in issue_codes(payload)


def test_single_day_goods_date_is_validated_for_non_event_content() -> None:
    payload = load_sample()
    payload["content_type"] = "goods"
    payload["facts"][0]["fact_type"] = "goods_release"
    payload["facts"][0].setdefault("details", {})["source_quote"] = "発売日：2026年10月23日"

    assert "SINGLE_DAY_PERIOD_NOT_MIRRORED" in issue_codes(payload)


def test_event_start_fact_does_not_force_longer_period_to_become_single_day() -> None:
    payload = load_sample()
    payload["content_type"] = "event"
    payload["event_start"] = "2026-10-15"
    payload["event_end"] = "2026-10-28"
    payload["facts"][0]["fact_type"] = "event_schedule"
    payload["facts"][0]["statement"] = "행사는 2026년 10월 15일 시작한다."
    payload["facts"][0].setdefault("details", {})["source_quote"] = (
        "2026年10月15日 10時よりスタート"
    )

    assert "SINGLE_DAY_PERIOD_NOT_MIRRORED" not in issue_codes(payload)


def test_music_release_date_is_not_mistaken_for_an_event_period() -> None:
    payload = load_sample()
    payload["content_type"] = "music"
    payload["event_start"] = "2026-10-30"
    payload["event_end"] = "2026-11-17"
    payload["facts"][0]["fact_type"] = "release"
    payload["facts"][0].setdefault("details", {})["source_quote"] = (
        "2026年10月30日先行配信"
    )

    assert "SINGLE_DAY_PERIOD_NOT_MIRRORED" not in issue_codes(payload)


def test_unused_product_date_does_not_override_selected_goods_period() -> None:
    payload = load_sample()
    payload["content_type"] = "goods"
    payload["event_start"] = payload["event_end"] = "2026-10-23"
    payload["facts"][0]["fact_type"] = "goods_release"
    payload["facts"][0].setdefault("details", {})["source_quote"] = (
        "発売日：2026年10月23日"
    )
    payload["facts"].append(
        {
            **payload["facts"][0],
            "id": "unused-product-date",
            "details": {"source_quote": "発売日：2026年10月24日"},
        }
    )
    payload["cards"][0]["fact_ids"] = [payload["facts"][0]["id"]]

    assert "SINGLE_DAY_PERIOD_NOT_MIRRORED" not in issue_codes(payload)


def test_visible_date_must_be_backed_by_a_fact_cited_on_the_same_card() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"].append("2026년 11월 14일부터 발매.")

    assert "UNSUPPORTED_DATE_CLAIM" in issue_codes(payload)


def test_yearless_fact_date_uses_the_cited_source_publication_year() -> None:
    payload = load_sample()
    payload["facts"][1]["details"] = {"source_quote": "10月2日から販売"}
    payload["sources"][0]["published_at"] = "2026-10-01"
    payload["cards"][1]["fact_ids"] = ["f2"]
    payload["cards"][1]["body_sentences"] = ["2026년 10월 2일부터 판매된다."]

    assert "UNSUPPORTED_DATE_CLAIM" not in issue_codes(payload)


def test_goods_with_verified_release_date_must_persist_structured_period() -> None:
    payload = load_sample()
    payload["content_type"] = "goods"
    payload["facts"][0]["fact_type"] = "release_date"
    payload["facts"][0].setdefault("details", {})["source_quote"] = "2026年11月14日より発売"
    payload["cards"][1]["fact_ids"] = [payload["facts"][0]["id"]]

    assert "GOODS_RELEASE_PERIOD_MISSING" in issue_codes(payload)


def test_event_venue_requires_country_and_domestic_overseas_region() -> None:
    payload = load_sample()
    payload["content_type"] = "event"
    payload["facts"][0]["fact_type"] = "location"
    payload["facts"][0].setdefault("details", {})["source_quote"] = "開催場所：東京・平和島"
    package = ContentPackage.model_validate(payload)

    assert {"EVENT_COUNTRY_MISSING", "EVENT_REGION_MISSING"}.issubset(
        {issue.code for issue in run_quality_checks(package)}
    )


def test_event_venue_with_country_still_requires_domestic_overseas_region() -> None:
    payload = load_sample()
    payload["content_type"] = "event"
    payload["event_country"] = "Japan"
    payload["event_region"] = "unknown"
    payload["facts"][0]["fact_type"] = "location"
    payload["facts"][0].setdefault("details", {})["source_quote"] = "開催場所：東京・平和島"
    package = ContentPackage.model_validate(payload)

    assert "EVENT_COUNTRY_MISSING" not in {issue.code for issue in run_quality_checks(package)}
    assert "EVENT_REGION_MISSING" in {issue.code for issue in run_quality_checks(package)}


def test_used_campaign_fact_with_expired_deadline_is_blocked() -> None:
    from datetime import date, timedelta

    payload = load_sample()
    yesterday = date.today() - timedelta(days=1)
    payload["facts"][0]["fact_type"] = "promotion"
    payload["facts"][0]["time_sensitive"] = True
    payload["facts"][0].setdefault("details", {})["source_quote"] = (
        f"{yesterday.year}年{yesterday.month}月{yesterday.day}日まで開催"
    )
    payload["cards"][0]["fact_ids"] = [payload["facts"][0]["id"]]
    package = ContentPackage.model_validate(payload)

    assert "CAMPAIGN_EXPIRED" in {issue.code for issue in run_quality_checks(package)}


def test_three_card_information_deck_does_not_require_three_fact_ids() -> None:
    payload = load_sample()
    payload["cards"][0]["fact_ids"] = ["f1"]
    payload["cards"][1]["fact_ids"] = ["f1"]
    payload["cards"][2]["fact_ids"] = ["f1"]
    package = ContentPackage.model_validate(payload)

    assert "FACT_DIVERSITY_LOW" not in {issue.code for issue in run_quality_checks(package)}


def test_two_card_deck_can_meet_fact_diversity_with_multiple_supported_facts_per_card() -> None:
    payload = load_sample()
    payload["cards"] = payload["cards"][:2]
    payload["cards"][0]["fact_ids"] = ["f1", "f2"]
    payload["cards"][1]["fact_ids"] = ["f3"]
    package = ContentPackage.model_validate(payload)

    assert "FACT_DIVERSITY_LOW" not in {issue.code for issue in run_quality_checks(package)}


def test_verified_product_facts_override_wrong_news_taxonomy() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.content_type = "news"
    package.facts[0].fact_type = "product_description"
    package.facts[1].fact_type = "price"

    assert infer_strong_content_type(package) == "goods"
    assert "CONTENT_TYPE_MISMATCH" in {issue.code for issue in run_quality_checks(package)}


def test_information_cover_without_official_source_art_returns_for_recollection() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.cards[0].visual_mode = "editorial_graphic"

    assert "COVER_SOURCE_IMAGE_MISSING" in {issue.code for issue in run_quality_checks(package)}


def test_layout_returns_for_cover_art_before_render_when_source_image_pool_is_empty() -> None:
    payload = load_sample()
    payload["images"] = []
    package = ContentPackage.model_validate(payload)
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})).model_dump(mode="json")
        for card in package.cards
    ]

    result = layout_director(
        {"package": package.model_dump(mode="json"), "draft_cards": copies, "trace": []}
    )

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "COVER_SOURCE_IMAGE_MISSING"


def test_preflight_blocks_when_unique_art_cannot_cover_minimum_three_cards() -> None:
    payload = load_sample()
    payload["images"] = [payload["images"][0]]
    package = ContentPackage.model_validate(payload)
    package.cards = []

    preflight = rights_preflight({"package": package.model_dump(mode="json"), "trace": []})
    assert preflight["status"] == "needs_crawler_update"
    assert preflight["qa_report"][0]["code"] == "SOURCE_IMAGE_POOL_INSUFFICIENT"


def test_structural_card_roles_are_normalized_by_position() -> None:
    package = ContentPackage.model_validate(load_sample())
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]
    copies[0] = copies[0].model_copy(update={"role": "detail"})
    copies[-1] = copies[-1].model_copy(update={"role": "goods_ending"})

    normalized = _normalize_structural_roles(copies)

    assert normalized[0].role == "cover"
    assert normalized[-1].role == "ending"


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


def test_korean_translation_with_parenthesized_japanese_original_is_allowed() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"] = [
        "신작 제목은 《그림자의 부상》(『NARUTO-ナルト- SHADOW RISING』)으로 공개됐다."
    ]
    package = ContentPackage.model_validate(payload)

    codes = {issue.code for issue in run_quality_checks(package)}

    assert "JAPANESE_COPY_UNTRANSLATED" not in codes


def test_glossary_does_not_exempt_unpaired_japanese_fragment() -> None:
    payload = load_sample()
    payload["term_glossary"] = [
        {"original": "クール では", "korean": "쿨에서", "category": "other"}
    ]
    payload["cards"][1]["body_sentences"] = ["최종 쿨 では 다시 만난다."]

    assert "JAPANESE_COPY_UNTRANSLATED" in issue_codes(payload)


def test_japanese_cour_translation_corrects_kuru_to_cool() -> None:
    payload = load_sample()
    payload["term_glossary"] = [
        {"original": "最終クール", "korean": "최종 쿠르", "category": "other"}
    ]
    package = ContentPackage.model_validate(payload)

    assert package.term_glossary[0].korean == "최종 쿨"


def test_kuru_mistranslation_in_copy_is_blocked() -> None:
    payload = load_sample()
    payload["term_glossary"] = [
        {"original": "最終クール", "korean": "최종 쿨", "category": "other"}
    ]
    payload["cards"][1]["body_sentences"] = ["최종 쿠르 편을 기념한다."]

    assert "JAPANESE_TERM_MISTRANSLATED" in issue_codes(payload)


def test_multiple_event_dates_cannot_be_packed_into_headline() -> None:
    payload = load_sample()
    payload["content_type"] = "event"
    payload["cards"][0]["title_lines"] = [
        "10월 18일~11월 9일 / 11월 20일~12월 20일"
    ]

    assert "EVENT_SCHEDULE_IN_TITLE" in issue_codes(payload)


def test_japanese_punctuation_in_korean_copy_is_flagged() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"] = ["무대가 열린다。팬들이 함께 듣는다."]

    assert "JAPANESE_COPY_UNTRANSLATED" in issue_codes(payload)


def test_japanese_punctuation_in_internal_image_explanation_is_not_copy_error() -> None:
    payload = load_sample()
    payload["cards"][1]["image_explanation"] = (
        "공식 키비주얼을 사용하며、인터뷰 장면이라고 단정하지 않는다."
    )

    assert "JAPANESE_COPY_UNTRANSLATED" not in issue_codes(payload)


def test_untranslated_japanese_kanji_with_number_is_flagged() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"] = ["約4시간 동안 함께 듣는다."]

    assert "JAPANESE_COPY_UNTRANSLATED" in issue_codes(payload)


def test_translated_label_allows_necessary_official_japanese_hashtag() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"] = ["태그는 ‘블리치 완결’(#BLEACH完結)로 안내된다."]

    assert "JAPANESE_COPY_UNTRANSLATED" not in issue_codes(payload)


def test_japanese_suffix_in_korean_labeled_official_hashtag_is_not_prose() -> None:
    payload = load_sample()
    payload["cards"][1]["body_sentences"] = ["블리치 완결 이벤트 해시태그는 #BLEACH完結."]

    assert "JAPANESE_COPY_UNTRANSLATED" not in issue_codes(payload)


def test_protected_fact_phrase_allows_natural_korean_particle() -> None:
    payload = load_sample()
    payload["cards"][1]["protected_phrases"] = ["\ucfe8 \ubc30\uc1a1\ube44 \ubcc4\ub3c4"]
    payload["cards"][1]["body_sentences"] = [
        "\ucfe8 \ubc30\uc1a1\ube44\ub294 \ubcc4\ub3c4\uc785\ub2c8\ub2e4."
    ]

    assert "PROTECTED_PHRASE_LOST" not in issue_codes(payload)


def test_unpaired_japanese_copy_reaches_language_review() -> None:
    package = ContentPackage.model_validate(load_sample())
    package.cards[1].body_sentences = ["무대는 コート에서 열린다."]
    issues = run_quality_checks(package)
    report = score_quality(package, issues, language_score=20, safety_score=10)

    assert "JAPANESE_COPY_UNTRANSLATED" not in report["hard_blockers"]
    assert "JAPANESE_COPY_UNTRANSLATED" in report["actionable_issues"]
    assert report["revision_deductions"] >= score_penalty("JAPANESE_COPY_UNTRANSLATED")
    # Editorial findings deduct points; they do not bypass the independent review.
    assert score_quality(package, issues)["decision"] == "pending_language_review"
    assert report["decision"] == "pass_candidate"


def test_long_ending_deducts_points_without_becoming_a_hard_blocker() -> None:
    payload = load_sample()
    payload["cards"][-1]["body_sentences"] = ["가" * 100]
    package = ContentPackage.model_validate(payload)

    issues = run_quality_checks(package)
    ending_issue = next(issue for issue in issues if issue.code == "ENDING_TOO_LONG")
    report = score_quality(
        package,
        issues,
        language_score=20,
        safety_score=10,
        card_language_scores={page: 90 for page in range(1, 5)},
    )

    assert ending_issue.severity == "revise"
    assert "ENDING_TOO_LONG" not in report["hard_blockers"]
    assert "ENDING_TOO_LONG" in report["actionable_issues"]
    assert report["revision_deductions"] >= score_penalty("ENDING_TOO_LONG")
    assert report["decision"] == "pass_candidate"


def test_cover_and_ending_may_recap_the_same_verified_fact() -> None:
    payload = load_sample()
    payload["cards"][-1]["fact_ids"] = payload["cards"][0]["fact_ids"]

    assert "CARD_REDUNDANT" not in issue_codes(payload)


def test_raw_image_reuse_is_rejected() -> None:
    payload = load_sample()
    payload["cards"][2]["image_id"] = payload["cards"][1]["image_id"]
    assert "IMAGE_REUSED_RAW" in issue_codes(payload)


def test_low_resolution_records_targeted_image_feedback_and_score_deduction() -> None:
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
    assert issue.severity == "revise"
    assert "IMAGE_LOW_RESOLUTION" in report["actionable_issues"]
    assert report["revision_deductions"] == score_penalty("IMAGE_LOW_RESOLUTION")
    assert report["decision"] == "pass_candidate"
    poor_review = score_quality(
        package, [issue], language_score=0, safety_score=10,
        card_language_scores={1: 0},
    )
    assert poor_review["decision"] == "revise"


def test_low_res_spotlight_uses_its_separate_upscale_limit() -> None:
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import CopyCard

    package = ContentPackage.model_validate(load_sample())
    image = package.images[0].model_copy(update={"width": 768, "height": 477})
    card = package.cards[0]
    copy_card = CopyCard.model_validate(card.model_dump(exclude={"layout"}))
    layout = build_cards([copy_card], [image])[0].layout
    # Legacy stored spotlight layouts retain their separate limit even though
    # the new planner chooses immersive/top-image layouts for new cards.
    layout = layout.model_copy(update={"template": "low_res_spotlight"})
    package.images[0] = image
    package.cards[0] = card.model_copy(update={"layout": layout})

    assert not any(
        issue.code == "IMAGE_LOW_RESOLUTION" and issue.page == card.page
        for issue in run_quality_checks(package)
    )


def test_small_official_portrait_uses_immersive_layout_without_cropping() -> None:
    from ani_menu_agent.layout_engine import build_cards
    from ani_menu_agent.models import CopyCard

    package = ContentPackage.model_validate(load_sample())
    image = package.images[0].model_copy(update={"width": 296, "height": 390})
    card = package.cards[0]
    copy_card = CopyCard.model_validate(card.model_dump(exclude={"layout"}))
    layout = build_cards([copy_card], [image])[0].layout

    assert layout.template == "cover_immersive"
    assert layout.image_box.width >= 0.44
    assert layout.image_box.height >= 0.47
    assert layout.fit_mode == "contain_blur"
    assert layout.image_scale == 1.0
    assert layout.image_offset_y == 0.0


def test_raw_image_reuse_remains_actionable_and_uses_shared_score_penalty() -> None:
    payload = load_sample()
    payload["cards"][2]["image_id"] = payload["cards"][1]["image_id"]
    package = ContentPackage.model_validate(payload)
    issues = run_quality_checks(package)
    reuse_issue = next(issue for issue in issues if issue.code == "IMAGE_REUSED_RAW")
    report = score_quality(package, issues, language_score=20, safety_score=10)

    assert reuse_issue.owner == "image_curator"
    assert reuse_issue.severity == "revise"
    assert report["decision"] == "pass_candidate"
    assert "IMAGE_REUSED_RAW" not in report["hard_blockers"]
    assert "IMAGE_REUSED_RAW" in report["actionable_issues"]
    assert report["revision_deductions"] >= score_penalty("IMAGE_REUSED_RAW")
    assert score_quality(
        package, issues, language_score=20, safety_score=10,
        card_language_scores={3: 54},
    )["decision"] == "revise"


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


def test_sparse_unique_image_pool_blocks_instead_of_filling_with_placeholder_graphics() -> None:
    from ani_menu_agent.image_selection import assign_distinct_images
    from ani_menu_agent.layout_engine import build_cards

    payload = load_sample()
    payload["images"] = payload["images"][:2]
    package = ContentPackage.model_validate(payload)
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]
    assigned = assign_distinct_images(copies, package)
    package.cards = build_cards(assigned, package.images)
    issues = run_quality_checks(package)
    report = score_quality(package, issues)

    assert report["decision"] == "return_to_crawler"
    assert package.cards[0].visual_mode == "source_image"
    assert all(card.visual_mode == "source_image" for card in package.cards)
    assert "SOURCE_IMAGE_MISSING" in {issue.code for issue in issues}


def test_exact_image_requires_a_link_to_a_verified_card_fact() -> None:
    payload = load_sample()
    payload["images"][0]["visual_match"] = "exact"
    payload["images"][0]["related_fact_ids"] = []

    assert "IMAGE_FACT_LINK_MISSING" in issue_codes(payload)


def test_protected_multiword_headline_phrase_cannot_be_split_across_lines() -> None:
    payload = load_sample()
    payload["cards"][0]["title_lines"] = ["가전", "양판점 한정 피규어"]
    payload["cards"][0]["protected_phrases"] = ["가전 양판점"]

    assert "PROTECTED_PHRASE_SPLIT" in issue_codes(payload)


def test_foreign_text_inside_artwork_requires_a_verified_korean_explanation() -> None:
    payload = load_sample()
    payload["images"][0]["visible_text"] = ["公式イベント情報"]
    payload["images"][0]["detected_languages"] = ["ja"]
    payload["images"][0]["korean_explanation"] = ""
    payload["cards"][0]["image_explanation"] = "공식 행사 이미지"

    assert "FOREIGN_IMAGE_UNEXPLAINED" in issue_codes(payload)


def test_article_relevant_images_can_fill_cards_when_fact_links_are_sparse() -> None:
    from ani_menu_agent.image_selection import assign_distinct_images

    payload = load_sample()
    payload["images"] = payload["images"][:3]
    for image in payload["images"]:
        image["visual_match"] = "exact"
        image["related_fact_ids"] = ["fact-from-another-card"]
    package = ContentPackage.model_validate(payload)
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]

    assigned = assign_distinct_images(copies, package)

    source_cards = [card for card in assigned if card.visual_mode == "source_image"]
    assert len({card.image_id for card in source_cards}) == len(source_cards)
    assert all(
        card.image_id in {image.id for image in package.images}
        or card.image_id.startswith("missing-source-image-")
        for card in source_cards
    )
    assert not any(card.visual_mode == "editorial_graphic" for card in assigned)


def test_low_coverage_transparent_sprite_is_not_selected_as_a_full_card_image() -> None:
    from ani_menu_agent.image_selection import assign_distinct_images

    payload = load_sample()
    payload["images"][0]["has_alpha"] = True
    payload["images"][0]["alpha_coverage"] = 0.06
    package = ContentPackage.model_validate(payload)
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]

    assigned = assign_distinct_images(copies, package)

    assert payload["images"][0]["id"] not in {card.image_id for card in assigned}


def test_ultrawide_schedule_and_decorative_title_assets_are_not_editorial_cards() -> None:
    from ani_menu_agent.image_selection import distinct_editorial_images

    payload = load_sample()
    payload["images"][0]["url"] = "https://example.com/assets/img_date_onair_pc.webp"
    payload["images"][0]["width"] = 2246
    payload["images"][0]["height"] = 143
    payload["images"][1]["url"] = "https://example.com/assets/common/title/ttl_intro.webp"
    payload["images"][1]["source_description"] = "intro"
    package = ContentPackage.model_validate(payload)

    selected = distinct_editorial_images(package.images, package.sources)

    assert all("img_date_onair" not in str(image.url) for image in selected)
    assert all("ttl_intro" not in str(image.url) for image in selected)


def test_image_with_long_embedded_copy_description_is_not_used_as_card_visual() -> None:
    from ani_menu_agent.image_selection import distinct_editorial_images

    payload = load_sample()
    payload["images"][0]["source_description"] = "작품 줄거리 " * 50
    package = ContentPackage.model_validate(payload)

    selected = distinct_editorial_images(package.images, package.sources)

    assert payload["images"][0]["id"] not in {image.id for image in selected}


def test_responsive_resized_variants_count_as_the_same_source_image() -> None:
    from ani_menu_agent.image_selection import image_visual_key

    payload = load_sample()
    image = ImageAsset.model_validate(payload["images"][0])
    resized = image.model_copy(
        update={
            "url": str(image.url).replace(".webp", "-768x432.webp"),
            "fingerprint": "different-rendition-bytes",
        }
    )

    assert image_visual_key(image) == image_visual_key(resized)


def test_width_path_renditions_count_as_the_same_source_image() -> None:
    from ani_menu_agent.image_selection import image_visual_key

    image = ImageAsset(
        id="official-large",
        url="https://anime.example/news/SYS/CONTENTS/abc123/w1200",
        source_url="https://anime.example/news/123",
    )
    resized = image.model_copy(
        update={
            "id": "official-small",
            "url": "https://anime.example/news/SYS/CONTENTS/abc123/w707",
            "fingerprint": "different-rendition-bytes",
        }
    )

    assert image_visual_key(image) == image_visual_key(resized)


def test_distinct_image_selection_keeps_the_largest_width_path_rendition() -> None:
    from ani_menu_agent.image_selection import distinct_editorial_images

    payload = load_sample()
    base = ImageAsset.model_validate(payload["images"][0])
    small = base.model_copy(
        update={
            "id": "same-image-small",
            "url": "https://anime.example/news/SYS/CONTENTS/abc123/w707",
            "width": 707,
            "height": 396,
            "fingerprint": "small-rendition",
        }
    )
    large = base.model_copy(
        update={
            "id": "same-image-large",
            "url": "https://anime.example/news/SYS/CONTENTS/abc123/w1200",
            "width": 1200,
            "height": 672,
            "fingerprint": "large-rendition",
        }
    )
    package = ContentPackage.model_validate({**payload, "images": [small, large]})

    selected = distinct_editorial_images(package.images, package.sources)

    assert [image.id for image in selected] == ["same-image-large"]


def test_api_query_parameter_identifying_an_image_is_preserved_for_deduplication() -> None:
    from ani_menu_agent.image_selection import image_visual_key

    first = ImageAsset(
        id="naruto-product-a",
        url="https://cms.example.com/image?path=%2Fproducts%2Fa.jpeg",
        source_url="https://official.example.com/news/1",
    )
    second = first.model_copy(
        update={
            "id": "naruto-product-b",
            "url": "https://cms.example.com/image?path=%2Fproducts%2Fb.jpeg",
        }
    )
    resized = first.model_copy(
        update={
            "id": "naruto-product-a-small",
            "url": "https://cms.example.com/image?path=%2Fproducts%2Fa.jpeg&w=640&utm_source=page",
        }
    )

    assert image_visual_key(first) != image_visual_key(second)
    assert image_visual_key(first) == image_visual_key(resized)


def test_same_downloaded_image_under_different_urls_is_only_selected_once() -> None:
    from ani_menu_agent.image_selection import distinct_editorial_images

    payload = load_sample()
    payload["images"][0]["fingerprint"] = "same-bytes"
    payload["images"][1]["url"] = "https://cdn.example.com/alternate-copy.webp"
    payload["images"][1]["fingerprint"] = "same-bytes"
    package = ContentPackage.model_validate(payload)

    selected = distinct_editorial_images(package.images, package.sources)

    assert len([image for image in selected if image.fingerprint == "same-bytes"]) == 1


def test_same_site_image_from_an_unrelated_page_is_excluded() -> None:
    from ani_menu_agent.image_selection import distinct_editorial_images

    payload = load_sample()
    payload["images"][0]["source_url"] = "https://example.com/official/goods"
    package = ContentPackage.model_validate(payload)

    selected = distinct_editorial_images(package.images, package.sources)

    assert payload["images"][0]["id"] not in {image.id for image in selected}

    package.cards[0].image_id = payload["images"][0]["id"]
    issue_codes_found = issue_codes(package.model_dump(mode="json"))
    assert "IMAGE_SOURCE_PAGE_MISMATCH" in issue_codes_found


def test_localized_official_article_route_is_same_source_page() -> None:
    from ani_menu_agent.image_selection import image_belongs_to_package_sources
    from ani_menu_agent.models import ImageAsset, SourceEvidence

    source = SourceEvidence(
        id="naruto_official",
        title="Naruto article",
        url="https://naruto-official.com/news/01_2690",
        publisher="Naruto Official",
        is_official=True,
        role="primary",
    )
    image = ImageAsset(
        id="localized-article-image",
        url="https://naruto-official.com/assets/item.jpg",
        source_url="https://naruto-official.com/de/news/01_2690",
        official=True,
        width=1200,
        height=1200,
    )

    assert image_belongs_to_package_sources(image, [source])


def test_preflight_returns_to_crawler_when_cover_has_no_source_image() -> None:
    from ani_menu_agent.nodes import rights_preflight

    payload = load_sample()
    payload["images"] = []
    result = rights_preflight({"package": payload, "trace": []})

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "SOURCE_IMAGE_POOL_INSUFFICIENT"


def test_information_with_one_verified_fact_can_reach_generation_preflight(monkeypatch) -> None:
    import ani_menu_agent.nodes as nodes

    payload = load_sample()
    payload["facts"] = payload["facts"][:1]
    payload["menu_work_ids"] = [1]
    package = ContentPackage.model_validate(payload)
    monkeypatch.setattr(nodes, "validate_generation_sources", lambda *_args: ["source"])
    monkeypatch.setattr(nodes, "attach_menu_work_ids", lambda _package: None)
    monkeypatch.setattr(nodes, "supported_content_types_for_sources", lambda _ids: ["news"])

    result = nodes.rights_preflight(
        {"package": package.model_dump(mode="json"), "trace": [], "allow_llm": True}
    )

    assert result["status"] == "rights_cleared"
    assert "FACT_DIVERSITY_LOW" not in {
        issue["code"] for issue in result.get("qa_report", [])
    }


def test_empty_image_pool_blocks_every_card_without_graphic_placeholders() -> None:
    from ani_menu_agent.image_selection import assign_distinct_images
    from ani_menu_agent.layout_engine import build_cards

    payload = load_sample()
    payload["images"] = []
    package = ContentPackage.model_validate(payload)
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]

    package.cards = build_cards(assign_distinct_images(copies, package), package.images)
    issues = run_quality_checks(package)
    report = score_quality(package, issues)

    assert package.cards[0].visual_mode == "source_image"
    assert all(card.visual_mode == "source_image" for card in package.cards)
    assert report["visual_mode_counts"]["source_image"] == len(package.cards)
    assert report["dimensions"]["image_suitability_and_rights"] == 0
    assert "SOURCE_IMAGE_MISSING" in report["hard_blockers"]
    assert report["decision"] == "return_to_crawler"
    assert "SOURCE_IMAGE_MISSING" in {issue.code for issue in issues}


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


def test_layout_engine_does_not_silently_reuse_an_image_for_unknown_ids() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copy_cards = [CopyCard.model_validate(card) for card in payload["cards"]]
    copy_cards[0] = copy_cards[0].model_copy(update={"image_id": "hallucinated-image-id"})

    cards = build_cards(copy_cards, package.images)

    assert cards[0].image_id == "hallucinated-image-id"
    assert "IMAGE_MISSING" in {
        issue.code for issue in run_quality_checks(package.model_copy(update={"cards": cards}))
    }


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


def test_layout_director_blocks_when_image_pool_is_smaller_than_deck() -> None:
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
    assert result["qa_report"][0]["code"] == "SOURCE_IMAGE_MISSING"


def test_layouts_follow_image_shape_and_keep_copy_in_the_lower_band() -> None:
    payload = load_sample()
    package = ContentPackage.model_validate(payload)
    copies = [CopyCard.model_validate(card) for card in payload["cards"]]

    cards = build_cards(copies, package.images)

    assert cards[0].layout.template == "cover_wide_top"
    assert {card.layout.template for card in cards[1:]} == {"immersive_full_bleed"}
    assert all(card.layout.text_overlay_box.y >= 0.6 for card in cards)
    assert all(card.layout.text_overlay_box.bottom <= 1 for card in cards)


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

    assert card.layout.template == "immersive_full_bleed"
    assert card.layout.fit_mode in {"contain", "contain_blur"}
    assert card.layout.image_scale == 1.0
    assert card.layout.image_offset_y == 0.0
    assert card.layout.text_overlay_box.y >= 0.6


def test_portrait_layouts_keep_lower_copy_band_instead_of_side_splits() -> None:
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

    assert all(card.layout.template == "immersive_full_bleed" for card in cards[1:3])
    assert all(card.layout.fit_mode in {"contain", "contain_blur"} for card in cards[1:3])
    assert all(card.layout.image_scale == 1.0 for card in cards[1:3])
    assert all(card.layout.text_overlay_box == card.layout.copy_box for card in cards[1:3])
    assert all(card.layout.text_overlay_box.y >= 0.6 for card in cards[1:3])
    assert all(card.layout.image_offset_y == 0 for card in cards[1:3])


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

    assert poster.layout.template == "immersive_full_bleed"
    assert poster.layout.image_box.height >= 0.9
    assert poster.layout.image_box.height <= 0.98
    assert poster.layout.fit_mode in {"contain", "contain_blur"}
    assert poster.layout.image_scale == 1.0
    assert cards[0].layout.template.startswith("cover_")
    assert cards[0].layout.image_box.width > 0.6
    assert cards[0].layout.fit_mode in {"contain", "contain_blur"}


def test_landscape_cover_keeps_art_above_lower_copy_band_without_crop() -> None:
    package = ContentPackage.model_validate(load_sample())
    image = package.images[0].model_copy(update={"width": 1920, "height": 1080})
    cover = CopyCard(
        page=1,
        role="cover",
        title_lines=["작품 소식이 돌아왔다"],
        image_id=image.id,
    )

    layout = build_cards([cover], [image])[0].layout

    assert layout.template == "cover_wide_top"
    assert layout.image_box.bottom <= layout.copy_box.y
    assert layout.copy_box.y >= 0.6
    assert layout.text_overlay_box == layout.copy_box
    assert layout.fit_mode in {"contain", "contain_blur"}
    assert layout.background_style in {"blur", "palette", "gradient"}


def test_portrait_infographic_preserves_full_art_and_lower_html_copy_band() -> None:
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

    assert cards[1].layout.template == "immersive_full_bleed"
    assert cards[1].layout.fit_mode == "contain_blur"
    assert cards[1].layout.image_scale == 1.0
    assert cards[1].layout.image_offset_y == 0.0
    assert cards[1].layout.text_overlay_box.y >= 0.6
    assert cards[1].layout.text_overlay_box.bottom <= 1.0


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
    assert all(card.layout.fit_mode in {"contain", "contain_blur"} for card in rebuilt.cards)
    assert all(card.layout.image_scale == 1.0 for card in rebuilt.cards)


def test_layout_revision_raises_composition_rejected_art_over_blurred_backdrop() -> None:
    payload = load_sample()
    result = layout_director(
        {
            "package": payload,
            "needs_revision": True,
            "revision_owner": "layout_director",
            "qa_report": [{"code": "IMAGE_COMPOSITION_REJECTED", "page": 1}],
            "trace": [],
        }
    )
    rebuilt = ContentPackage.model_validate(result["package"])

    assert result["status"] == "layout_replanned_for_composition"
    assert rebuilt.cards[0].layout.image_box.width > 0
    assert rebuilt.cards[0].layout.image_box.height > 0
    assert rebuilt.cards[0].layout.background_style == "blur"
    assert rebuilt.cards[0].layout.text_overlay_box.y == pytest.approx(0.66)
    assert rebuilt.cards[0].layout.fit_mode == "contain"
    assert rebuilt.cards[0].layout.image_scale == 1.0
    assert rebuilt.cards[0].layout.image_offset_y == 0.0
