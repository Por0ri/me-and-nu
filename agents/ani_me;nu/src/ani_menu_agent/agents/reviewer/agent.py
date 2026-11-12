"""Independent evaluator for code guardrails and Korean-language quality."""

import base64
import json
import re
from time import perf_counter
from urllib.parse import urlsplit

from langchain_openai import ChatOpenAI

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.agents.content_generation.renderer import render_review_preview
from ani_menu_agent.agents.reviewer.prompt import REVIEWER_PROMPT
from ani_menu_agent.config import get_settings
from ani_menu_agent.generation import _infer_copy_mode, estimate_gpt_6_luna_cost
from ani_menu_agent.image_selection import image_visual_keys
from ani_menu_agent.models import (
    CONTENT_TYPE_GUIDANCE,
    ContentPackage,
    LanguageReview,
    QualityIssue,
    UsageRecord,
)
from ani_menu_agent.policy import POLICY
from ani_menu_agent.quality import (
    SCORE_THRESHOLDS,
    run_quality_checks,
    score_quality,
)
from ani_menu_agent.review_rules import get_rule, make_issue
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository

LANGUAGE_THRESHOLDS = {
    # Keep language gates aligned with the shared, deliberately permissive policy.
    "korean_naturalness": POLICY.language_korean_minimum,
    "translation_naturalness": POLICY.language_translation_minimum,
    "reader_clarity": POLICY.language_clarity_minimum,
    "fan_engagement": POLICY.language_fan_engagement_minimum,
    "card_flow": POLICY.language_card_flow_minimum,
}
SAFETY_THRESHOLD = int(SCORE_THRESHOLDS["safety_minimum_per_card"])


def _score_quality(package: ContentPackage, issues: list[QualityIssue], **kwargs):
    return score_quality(package, issues, thresholds=SCORE_THRESHOLDS, **kwargs)


def _review_minimal_editorial(state: ContentState, package: ContentPackage) -> ContentState:
    """Review copy readability and retain a hard visual relevance gate."""
    cards = sorted(package.cards, key=lambda card: card.page)
    failures: list[str] = []
    if not 2 <= len(cards) <= 10:
        failures.append("Carousel must contain 2 to 10 cards.")
    if any(not (card.title_lines or card.body_sentences) for card in cards):
        failures.append("A card has neither a title nor body copy.")
    image_ids = [card.image_id for card in cards if card.image_id]
    if len(image_ids) != len(set(image_ids)):
        failures.append("An image is repeated within the carousel.")
    images_by_id = {image.id: image for image in package.images}
    recal_schedule_pages = [
        card.page
        for card in cards
        if package.content_type == "event"
        and card.image_id in images_by_id
        and (urlsplit(str(images_by_id[card.image_id].url or "")).hostname or "").lower()
        in {"recal.kr", "www.recal.kr"}
    ]
    if recal_schedule_pages:
        failures.append(
            "Event cards use RE:CAL schedule/listing graphics as artwork; replace them with distinct exact-work images."
        )
    event_copy = " ".join(
        [*(line for card in cards for line in card.title_lines),
         *(sentence for card in cards for sentence in card.body_sentences),
         package.detail_body]
    )
    schedule_copy_failure = False
    if package.content_type == "event" and (package.event_start or package.event_end):
        schedule_caveat_pattern = re.compile(
            r"(?:행사\s*정보|세부\s*(?:정보|내용)|실제\s*일정|일정|날짜|장소|"
            r"개최(?:\s*(?:정보|일정))?).{0,28}"
            r"(?:미확인|확인되지|검증되지|미검증|검증\s*전|확정되지)"
            r"|(?:미확인|확인되지|검증되지|미검증|검증\s*전|확정되지).{0,28}"
            r"(?:행사|일정|날짜|장소|개최)",
            re.IGNORECASE,
        )
        schedule_copy_failure = bool(schedule_caveat_pattern.search(event_copy))
        if schedule_copy_failure:
            failures.append(
                "Event copy says its schedule is unknown even though structured event dates are supplied; "
                "rewrite it by stating the supplied dates directly in natural Korean. Keep source-page "
                "and website narration out of the card and article copy; the source link is shown separately."
            )

    settings = get_settings()
    if not settings.openai_api_key:
        report = {
            "mode": "minimal_editorial",
            "decision": "pending_language_review",
            "total": 0,
            "reason": "Reviewer model API is not configured; copy and image relevance were not evaluated.",
        }
        AgentRepository().persist_generated_content(
            package, "review_api_required", report,
            usage_records=state.get("usage_records", []),
        )
        return {
            "trace": append_trace(state, "minimal_editorial_review"),
            "quality_report": report,
            "quality_score": 0,
            "qa_report": [],
            "review_mode": "not_evaluated",
            "needs_revision": False,
            "review_failures": state.get("review_failures", 0),
            "editorial_decision": "review_required",
            "status": "review_api_required",
            "usage_records": state.get("usage_records", []),
        }
    if failures:
        report = {
            "mode": "minimal_editorial",
            "decision": "revise",
            "total": 0,
            "issues": failures,
        }
        issues = [
            {
                "code": (
                    "EVENT_SCHEDULE_GRAPHIC_AS_ART"
                    if "RE:CAL schedule/listing graphics" in item
                    else "CAROUSEL_SHAPE"
                ),
                "message": item,
            }
            for item in failures
        ]
        AgentRepository().persist_generated_content(
            package, "review_returned", report,
            usage_records=state.get("usage_records", []),
        )
        return {
            "trace": append_trace(state, "minimal_editorial_review"),
            "quality_report": report,
            "quality_score": 0,
            "qa_report": issues,
            "review_mode": "minimal_editorial",
            "needs_revision": True,
            "revision_owner": "copy_writer" if schedule_copy_failure else "layout_director",
            "revision_owners": ["copy_writer"] if schedule_copy_failure else ["layout_director"],
            "review_failures": int(state.get("review_failures", 0)) + 1,
            "editorial_decision": "revise",
            "status": "review_returned",
            "usage_records": state.get("usage_records", []),
        }

    model = ChatOpenAI(model=settings.model_name, api_key=settings.openai_api_key)
    structured = model.with_structured_output(
        LanguageReview, method="function_calling", include_raw=True
    )
    message_content, attached_pages, missing_image_pages = _review_message_content(
        package, minimal_editorial=True
    )
    started = perf_counter()
    response = structured.invoke(
        [
            (
                "system",
                "You are a concise Korean carousel copy editor and image relevance reviewer. "
                "Judge attached artwork itself, not its source metadata.",
            ),
            ("human", message_content),
        ]
    )
    elapsed = perf_counter() - started
    review = response.get("parsed")
    usage_records = list(state.get("usage_records", []))
    raw = response.get("raw")
    usage = getattr(raw, "usage_metadata", None) or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    input_details = usage.get("input_token_details") or {}
    cached_tokens = int(input_details.get("cache_read", 0) or 0)
    cache_write_tokens = int(input_details.get("cache_write", input_details.get("cache_creation", 0)) or 0)
    usage_records.append(
        UsageRecord(
            model=settings.model_name,
            stage="review",
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("total_tokens", input_tokens + output_tokens) or 0),
            estimated_cost_usd=estimate_gpt_6_luna_cost(
                input_tokens, cached_tokens, cache_write_tokens, output_tokens
            ),
            elapsed_seconds=elapsed,
        ).model_dump(mode="json")
    )

    expected_pages = {card.page for card in cards}
    reviewed_pages = {card.page for card in review.cards} if review else set()
    failed_pages: list[int] = []
    unsafe_pages: list[int] = []
    visual_issues: list[QualityIssue] = []
    if review is None or expected_pages != reviewed_pages:
        failures.append("Copy and image review is missing or has mismatched card numbers.")
    else:
        for card_review in review.cards:
            if card_review.safety_blocker:
                unsafe_pages.append(card_review.page)
            if min(
                card_review.korean_naturalness,
                card_review.translation_naturalness,
                card_review.reader_clarity,
                card_review.fan_engagement,
                card_review.card_flow,
            ) < 2:
                failed_pages.append(card_review.page)
        if failed_pages:
            failures.append("Cards need copy revision: " + ", ".join(map(str, failed_pages)))
        if unsafe_pages:
            failures.append("Cards contain a clear safety issue: " + ", ".join(map(str, unsafe_pages)))
        if missing_image_pages:
            visual_issues.append(
                make_issue(
                    code="IMAGE_VISION_REVIEW_MISSING",
                    message=(
                        "A card preview could not be loaded for image review; passing is blocked. Cards: "
                        + ", ".join(map(str, missing_image_pages))
                    ),
                    page=missing_image_pages[0],
                )
            )
        required_visual_fields = {"image_relevance", "image_composition", "visual_feedback"}
        for card_review in review.cards:
            absent = required_visual_fields - card_review.model_fields_set
            if absent:
                visual_issues.append(
                    make_issue(
                        code="IMAGE_VISION_REVIEW_MISSING",
                        message=(
                            f"Card {card_review.page} lacks explicit visual review fields: "
                            f"{', '.join(sorted(absent))}. Passing is blocked."
                        ),
                        page=card_review.page,
                    )
                )
        if not visual_issues:
            visual_issues.extend(_visual_issues(review, package))
        for card in cards:
            if card.visual_mode != "source_image" or not card.image_id:
                visual_issues.append(
                    make_issue(
                        code="IMAGE_VISION_REVIEW_MISSING",
                        message=f"Card {card.page} has no source image to review; passing is blocked.",
                        page=card.page,
                    )
                )

    all_failures = [*failures, *(issue.message for issue in visual_issues)]
    decision = "revise" if all_failures else "pass_candidate"
    score_values = [
        getattr(card, field)
        for card in (review.cards if review else [])
        for field in (
            "korean_naturalness",
            "translation_naturalness",
            "reader_clarity",
            "fan_engagement",
            "card_flow",
        )
    ]
    score = round(sum(score_values) / max(1, len(score_values)) * 20) if score_values else 0
    report = {
        "mode": "minimal_editorial",
        "decision": decision,
        "total": score,
        "issues": [*failures, *(issue.model_dump(mode="json") for issue in visual_issues)],
        "reviewed_pages": sorted(reviewed_pages),
        "image_reviewed_pages": sorted(set(attached_pages)),
        "summary": review.summary if review else "Review result was invalid.",
        "dimensions": {
            "carousel_shape": "pass",
            "copy_readability": score,
            "image_relevance": "fail" if any(i.code == "IMAGE_VISION_MISMATCH" for i in visual_issues) else "pass",
            "image_composition": "fail" if any(i.code == "IMAGE_COMPOSITION_REJECTED" for i in visual_issues) else "pass",
            "clear_safety_blockers": "pass" if not unsafe_pages else unsafe_pages,
            "fact_checking": "skipped_by_policy",
            "copyright_review": "skipped_by_policy",
        },
    }
    status = "review_returned" if all_failures else "review_passed"
    AgentRepository().persist_generated_content(
        package, status, report, usage_records=usage_records
    )
    qa_report = [
        {"code": "COPY_READABILITY", "message": item} for item in failures
    ] + [issue.model_dump(mode="json") for issue in visual_issues]
    visual_revise = bool(visual_issues)
    return {
        "trace": append_trace(state, "minimal_editorial_review"),
        "quality_report": report,
        "quality_score": score,
        "qa_report": qa_report,
        "review_mode": "minimal_editorial",
        "needs_revision": bool(all_failures),
        "revision_owner": ("layout_director" if visual_revise else "copy_writer") if all_failures else "",
        "revision_owners": (["layout_director"] if visual_revise else []) + (["copy_writer"] if failures else []),
        "review_failures": int(state.get("review_failures", 0)) + int(bool(all_failures)),
        "editorial_decision": "revise" if all_failures else "approved_for_feed",
        "status": status,
        "usage_records": usage_records,
    }

def _review_payload(package: ContentPackage) -> dict:
    images = {image.id: image for image in package.images}
    recent_copy = AgentRepository().recent_copy_examples(
        exclude_request_id=package.request_id,
        limit=6,
    )
    content_types = {
        item["code"]: item["label_ko"] for item in AgentRepository().active_content_types()
    }
    cards = []
    for card in package.cards:
        image = images.get(card.image_id)
        cards.append(
            {
                "page": card.page,
                "role": card.role,
                "eyebrow": card.eyebrow,
                "title": card.title_lines,
                "body": card.body_sentences,
                "fact_ids": card.fact_ids,
                "image_id": card.image_id,
                "rendered_image_key": card.rendered_image_key,
                "visual_mode": card.visual_mode,
                "text_overlay_box": card.layout.text_overlay_box.model_dump(mode="json"),
                "image_explanation": card.image_explanation,
                "image_metadata": (
                    {
                        "source_description": image.source_description,
                        "asset_kind": image.asset_kind,
                        "depicted_entities": image.depicted_entities,
                        "asset_metadata_confidence": image.asset_metadata_confidence,
                        "visible_text": image.visible_text,
                        "korean_explanation": image.korean_explanation,
                        "detected_languages": image.detected_languages,
                        "related_fact_ids": image.related_fact_ids,
                        "image_fact_link_required_for_exact_match": True,
                        "visual_match": image.visual_match,
                    }
                    if image
                    else None
                ),
            }
        )
    return {
        "topic": package.topic,
        "content_type": {
            "code": package.content_type,
            "label_ko": content_types.get(package.content_type or "", "알 수 없음"),
        },
        "available_content_types": [
            {"code": code, "label_ko": label} for code, label in content_types.items()
        ],
        "content_type_guidance": CONTENT_TYPE_GUIDANCE,
        "publication_date_policy": (
            "Do not require or recommend showing the source/article publication date. "
            "When a verified event/popup period is supplied, check that both endpoints appear."
        ),
        "editorial_plan": {
            "angle": package.editorial_angle,
            "cover_mode": package.cover_mode,
            "ending_mode": package.ending_mode,
        },
        "term_glossary": [term.model_dump(mode="json") for term in package.term_glossary],
        "content_kind": package.content_kind.value,
        "recent_style_modes": [
            {
                "topic": item["topic"],
                "editorial_angle": item.get("editorial_angle", ""),
                "cover_mode": item.get("cover_mode") or _infer_copy_mode(item["cover"]),
                "ending_mode": item.get("ending_mode") or _infer_copy_mode(item["ending"]),
            }
            for item in recent_copy
        ],
        "source_facts": [
            fact.model_dump(mode="json")
            for fact in package.facts
        ],
        "cards": cards,
        "detail_body": package.detail_body,
        "detail_fact_ids": package.detail_fact_ids,
        "detail_source_ids": package.detail_source_ids,
    }


def _review_message_content(
    package: ContentPackage, *, minimal_editorial: bool = False
) -> tuple[list[dict], list[int], list[int]]:
    """Attach temporary text-overlay previews without changing persisted card PNGs."""
    repository = AgentRepository()
    if minimal_editorial:
        payload = {
            "content_type": package.content_type,
            "event_schedule": {
                "start_date": package.event_start,
                "end_date": package.event_end,
                "venue": package.event_location,
            } if package.content_type == "event" else None,
            "cards": [
                {
                    "page": card.page,
                    "role": card.role,
                    "title": card.title_lines,
                    "body": card.body_sentences,
                }
                for card in sorted(package.cards, key=lambda item: item.page)
            ],
        }
        instructions = (
            "Evaluate Korean readability and image relevance only. Each attached image follows the "
            "card with the matching page number in the JSON. Inspect the visible artwork itself: "
            "if it depicts a different anime, character, event, or unrelated recommendation, give "
            "image_relevance 0 or 1 and explain why in visual_feedback. Do not infer relevance "
            "from metadata. Check that the source image is legible and composed well enough for a card. "
            "Do not fact-check, research sources, or assess copyright. For event copy, compare it "
            "with the supplied event_schedule: when a start/end date is present, reject generic "
            "claims that the schedule or event information is unknown. A missing price or entry "
            "method does not make the listed dates or venue unknown. Ignore the temporary text overlay "
            "when judging the original image. Return all page numbers and explicit image_relevance, "
            "image_composition, and visual_feedback fields for every card.\n"
        )
    else:
        payload = _review_payload(package)
        instructions = (
            "The attached previews follow the JSON page order. Each preview temporarily overlays "
            "copy on the text-free stored PNG. Judge image_relevance from the original artwork, "
            "and check whether it fits the card topic and remains identifiable and well composed. "
            "Do not mistake the temporary overlay for text in the original image.\n"
        )
    parts: list[dict] = [
        {"type": "text", "text": instructions + json.dumps(payload, ensure_ascii=False)}
    ]
    attached_pages: list[int] = []
    missing_pages: list[int] = []
    for card in sorted(package.cards, key=lambda item: item.page):
        if not card.rendered_image_key:
            missing_pages.append(card.page)
            continue
        stored = repository.get_image_bytes(card.rendered_image_key)
        if not stored:
            missing_pages.append(card.page)
            continue
        binary, media_type = stored
        try:
            binary = render_review_preview(binary, card, package)
            media_type = "image/png"
        except Exception:
            # Review the original image plate if the temporary overlay cannot be rendered.
            pass
        encoded = base64.b64encode(binary).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{encoded}",
                    "detail": "original",
                },
            }
        )
        attached_pages.append(card.page)
    return parts, attached_pages, missing_pages

def _visual_issues(review: LanguageReview, package: ContentPackage) -> list[QualityIssue]:
    """Turn pixel-level reviewer findings into repairable, page-scoped image issues."""
    issues: list[QualityIssue] = []
    cards_by_page = {card.page: card for card in package.cards}
    images_by_id = {image.id: image for image in package.images}
    for item in review.cards:
        card = cards_by_page.get(item.page)
        if card is None or card.visual_mode != "source_image":
            continue
        reject_asset = False
        relevance_minimum = POLICY.image_relevance_minimum
        if item.image_relevance < relevance_minimum:
            reject_asset = True
            feedback = item.visual_feedback or "작품·인물·사건에 맞는 이미지를 선택하세요."
            issues.append(
                make_issue(
                    code="IMAGE_VISION_MISMATCH",
                    message=(
                        f"{item.page}페이지 이미지 주제 적합도 {item.image_relevance}/5. {feedback}"
                    ),

                    page=item.page,

                )
            )
        composition_minimum = POLICY.image_composition_minimum
        if item.image_composition < composition_minimum:
            feedback = item.visual_feedback or (
                "잘림·깨짐·심한 분리·피사체 식별 곤란 등 확인된 시각 결함을 구체적으로 설명하세요."
            )
            issues.append(
                make_issue(
                    code="IMAGE_COMPOSITION_REJECTED",
                    message=(
                        f"{item.page}페이지 이미지 구성 {item.image_composition}/5. {feedback}"
                    ),

                    page=item.page,

                )
            )
        if reject_asset:
            image = images_by_id.get(card.image_id)
            if image is not None:
                rejected_keys = image_visual_keys(image)
                for candidate in package.images:
                    if rejected_keys.intersection(image_visual_keys(candidate)):
                        candidate.vision_review_rejected = True
    return issues


_REVISION_OWNER_ORDER = (
    "copy_writer",
    "image_curator",
    "layout_director",
    "detail_writer",
)


def _select_revision_owners(issues: list[QualityIssue]) -> list[str]:
    """Return every actionable owner so a review pass cannot hide sibling issues."""
    owners = {
        get_rule(issue.code).owner
        for issue in issues
        if get_rule(issue.code).on_fail == "revise_owner"
        and get_rule(issue.code).owner != "crawler"
    }
    selected = [owner for owner in _REVISION_OWNER_ORDER if owner in owners]
    return selected


def _select_revision_owner(issues: list[QualityIssue], prior_failures: int) -> str:
    """Choose a useful primary owner for older consumers; routing uses all owners."""
    owners = {
        get_rule(issue.code).owner
        for issue in issues
        if get_rule(issue.code).on_fail == "revise_owner"
        and get_rule(issue.code).owner != "crawler"
    }
    if "image_curator" in owners and (prior_failures == 0 or "copy_writer" not in owners):
        return "image_curator"
    if any(issue.code == "IMAGE_COMPOSITION_REJECTED" for issue in issues) and not (
        {"image_curator", "copy_writer"} & owners
    ):
        return "layout_director"
    for owner in ("copy_writer", "detail_writer", "layout_director", "image_curator"):
        if owner in owners:
            return owner
    if any(get_rule(issue.code).on_fail == "return_to_crawler" for issue in issues):
        return "crawler"
    return ""


def _score_language(
    review: LanguageReview,
    *,
    hook_pages: set[int] | None = None,
) -> tuple[int, int, dict[int, int], list[QualityIssue]]:
    language_thresholds = LANGUAGE_THRESHOLDS
    dimensions = tuple(language_thresholds)
    all_scores = [getattr(card, key) for card in review.cards for key in dimensions]
    score = round(sum(all_scores) / max(1, len(all_scores)) * 4)
    safety_score = round(
        sum(card.safety_respect for card in review.cards) / max(1, len(review.cards)) * 2
    )
    card_scores: dict[int, int] = {}
    issues: list[QualityIssue] = []
    for card in review.cards:
        values = [getattr(card, key) for key in dimensions]
        card_scores[card.page] = round((sum(values) + card.safety_respect) / (len(values) + 1) * 20)
        for criterion, threshold in language_thresholds.items():
            value = getattr(card, criterion)
            if value < threshold:
                feedback = (
                    card.feedback or review.summary or "해당 문장을 자연스럽고 명확하게 수정하세요."
                )
                issues.append(
                    make_issue(
                        code=f"LANGUAGE_{criterion.upper()}",
                        message=f"{card.page}장 {criterion} {value}/5: {feedback}",

                        page=card.page,

                    )
                )
        if card.safety_respect < SAFETY_THRESHOLD:
            feedback = card.feedback or review.summary or "모욕·성적 대상화·차별 표현을 제거하세요."
            issues.append(
                make_issue(
                    code="SAFETY_RESPECT_LOW",
                    message=f"{card.page}장 안전·존중 점수 {card.safety_respect}/5: {feedback}",

                    page=card.page,

                )
            )
        if card.safety_blocker:
            category = card.safety_category or "unspecified"
            feedback = (
                card.feedback
                or review.summary
                or "유해 표현을 제거하고 중립적으로 다시 작성하세요."
            )
            issues.append(
                make_issue(
                    code="SAFETY_HARMFUL_CONTENT",
                    message=f"{card.page}장 안전 차단({category}): {feedback}",

                    page=card.page,

                )
            )
    return score, safety_score, card_scores, issues


def review_content(state: ContentState) -> ContentState:
    """Review a generated package independently; never use fixture approval for publishing."""
    package = ContentPackage.model_validate(state["package"])
    if state.get("minimal_review"):
        return _review_minimal_editorial(state, package)
    issues = run_quality_checks(package)
    if state.get("allow_llm", False) and not package.detail_body.strip():
        issues.append(
            make_issue(
                code="DETAIL_BODY_MISSING",
                message="자세히 보기용 독립 상세글이 생성되지 않았습니다.",

            )
        )
    code_report = _score_quality(package, issues)
    offline_fixture = state.get("review_fixture") is not None
    hard_codes = set(code_report["hard_blockers"])
    if hard_codes:
        decision = code_report["decision"]
        failures = state.get("review_failures", 0) + (1 if decision == "revise" else 0)
        status = (
            "offline_review_only"
            if offline_fixture
            else "needs_crawler_update"
            if decision == "return_to_crawler"
            else "review_returned"
        )
        if not offline_fixture:
            AgentRepository().persist_generated_content(
                package, status, code_report, usage_records=state.get("usage_records", [])
            )
        return {
            "trace": append_trace(state, "review_agent"),
            "qa_report": [issue.model_dump(mode="json") for issue in issues],
            "quality_report": code_report,
            "quality_score": int(code_report["total"]),
            "review_failures": failures,
            "needs_revision": decision == "revise",
            "review_mode": "offline_fixture" if offline_fixture else "code",
            "revision_owner": _select_revision_owner(
                issues, int(state.get("review_failures", 0))
            ),
            "revision_owners": _select_revision_owners(issues),
            "editorial_decision": (
                "offline_fixture_failed"
                if offline_fixture
                else "return_to_crawler"
                if decision == "return_to_crawler"
                else "revise"
            ),
            "status": status,
        }

    fixture = state.get("review_fixture")
    if fixture is not None:
        review = LanguageReview.model_validate(fixture)
        review_mode = "offline_fixture"
    else:
        settings = get_settings()
        if not settings.openai_api_key:
            pending = _score_quality(package, issues)
            status = (
                "review_api_required"
                if pending["decision"] == "pending_language_review"
                else "review_returned"
            )
            AgentRepository().persist_generated_content(
                package, status, pending, usage_records=state.get("usage_records", [])
            )
            return {
                "trace": append_trace(state, "review_agent"),
                "qa_report": [issue.model_dump(mode="json") for issue in issues],
                "quality_report": pending,
                "quality_score": int(pending["total"]),
                "review_mode": "not_evaluated",
                "needs_revision": False,
                "status": status,
                "editorial_decision": "review_required",
            }

        model = ChatOpenAI(model=settings.model_name, api_key=settings.openai_api_key)
        structured = model.with_structured_output(
            LanguageReview,
            method="function_calling",
            include_raw=True,
        )
        message_content, attached_pages, missing_image_pages = _review_message_content(package)
        started = perf_counter()
        response = structured.invoke(
            [
                ("system", REVIEWER_PROMPT),
                ("human", message_content),
            ]
        )
        elapsed = perf_counter() - started
        raw = response.get("raw")
        usage = getattr(raw, "usage_metadata", None) or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        input_details = usage.get("input_token_details") or {}
        cached_tokens = int(input_details.get("cache_read", 0) or 0)
        cache_write_tokens = int(
            input_details.get("cache_write", input_details.get("cache_creation", 0)) or 0
        )
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        usage_records = [*state.get("usage_records", [])]
        usage_records.append(
            UsageRecord(
                model=settings.model_name,
                stage="review",
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                cache_write_tokens=cache_write_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimate_gpt_6_luna_cost(
                    input_tokens, cached_tokens, cache_write_tokens, output_tokens
                ),
                elapsed_seconds=elapsed,
            ).model_dump(mode="json")
        )
        state = {**state, "usage_records": usage_records}
        review = response.get("parsed")
        if review is None:
            issue = make_issue(
                code="REVIEW_OUTPUT_INVALID",
                message="독립 검수 결과를 구조화하지 못해 통과를 보류했습니다.",

            )
            issues.append(issue)
            report = _score_quality(package, issues)
            AgentRepository().persist_generated_content(
                package, "review_output_invalid", report, usage_records=usage_records
            )
            decision = str(report["decision"])
            return {
                "trace": append_trace(state, "review_agent"),
                "qa_report": [item.model_dump(mode="json") for item in issues],
                "quality_report": report,
                "quality_score": int(report["total"]),
                "needs_revision": decision == "revise",
                "revision_owner": "crawler" if decision == "return_to_crawler" else "",
                "revision_owners": [],
                "review_failures": state.get("review_failures", 0) + 1,
                "review_mode": "llm",
                "status": "review_returned",
                "editorial_decision": decision,
                "usage_records": usage_records,
            }
        review_mode = "llm"

    expected_pages = {card.page for card in package.cards}
    reviewed_pages = {card.page for card in review.cards}
    if expected_pages != reviewed_pages:
        issues.append(
            make_issue(
                code="REVIEW_CARD_SET_MISMATCH",
                message="검수 결과의 카드 번호가 초안과 일치하지 않아 평가를 다시 요청해야 합니다.",

            )
        )
        # A malformed offline review is still a fixture; it must never write
        # production review state or enter the live crawler/publication routes.
        review_mode = "offline_fixture" if offline_fixture else "invalid"
        language_score = 0
        safety_score = 0
        card_scores = {page: 0 for page in expected_pages}
        language_issues: list[QualityIssue] = []
    else:
        page_numbers = [card.page for card in package.cards]
        hook_pages = {min(page_numbers), max(page_numbers)} if page_numbers else set()
        language_score, safety_score, card_scores, language_issues = _score_language(
            review,
            hook_pages=hook_pages,
        )
        issues.extend(language_issues)
        if review_mode == "llm":
            issues.extend(_visual_issues(review, package))
            required_visual_fields = {
                "image_relevance",
                "image_composition",
                "visual_feedback",
            }
            for item in review.cards:
                absent = required_visual_fields - item.model_fields_set
                if absent:
                    issues.append(
                        make_issue(
                            code="IMAGE_VISION_REVIEW_MISSING",
                            message=(
                                f"{item.page}페이지 시각 검수 필드가 빠졌습니다: "
                                f"{', '.join(sorted(absent))}. "
                                "명시적인 이미지 검수 없이는 통과할 수 없습니다."
                            ),

                            page=item.page,

                        )
                    )
            attached_page_set = set(attached_pages)
            for page in missing_image_pages:
                issues.append(
                    make_issue(
                        code="IMAGE_RENDER_MISSING",
                        message=(
                            f"{page}페이지 렌더 이미지를 픽셀 검수에 첨부하지 못했습니다. "
                            "이미지 검수 없이 통과시킬 수 없습니다."
                        ),

                        page=page,

                    )
                )
            for page in sorted(expected_pages - attached_page_set - set(missing_image_pages)):
                issues.append(
                    make_issue(
                        code="IMAGE_RENDER_MISSING",
                        message=f"{page}페이지 이미지가 검수 요청에서 누락됐습니다.",

                        page=page,

                    )
                )
        if package.detail_body and review_mode != "offline_fixture":
            detail_review = review.detail_body_review
            if detail_review is None:
                issues.append(
                    make_issue(
                        code="DETAIL_BODY_REVIEW_MISSING",
                        message="상세글의 자연스러움·사실 근거·문단 흐름 검수가 누락됐습니다.",

                    )
                )
            else:
                for dimension in ("naturalness", "factual_support", "coherence", "completeness"):
                    if (
                        dimension == "completeness"
                        and package.content_type not in {"event", "goods"}
                    ):
                        continue
                    value = getattr(detail_review, dimension)
                    minimum = (
                        POLICY.detail_factual_support_minimum
                        if dimension == "factual_support"
                        else POLICY.detail_completeness_minimum
                        if dimension == "completeness"
                        else POLICY.language_korean_minimum
                    )
                    if value < minimum:
                        issues.append(
                            make_issue(
                                code=f"DETAIL_BODY_{dimension.upper()}",
                                message=(
                                    f"상세글 {dimension} {value}/5: "
                                    f"{detail_review.feedback or review.summary}"
                                ),

                            )
                        )
        content_type_minimum = POLICY.content_type_fit_minimum
        if review.content_type_fit < content_type_minimum:
            issues.append(
                make_issue(
                    code="CONTENT_TYPE_MISMATCH",
                    message=(
                        f"콘텐츠 유형 적합도 {review.content_type_fit}/5: "
                        f"{review.content_type_feedback or '대표 유형과 실제 소재를 맞추세요.'}"
                    ),

                )
            )

    report = _score_quality(
        package,
        issues,
        language_score=language_score,
        safety_score=safety_score,
        card_language_scores=card_scores,
    )
    failures = state.get("review_failures", 0)
    if report["decision"] == "revise":
        failures += 1
    decision = report["decision"]
    if review_mode == "offline_fixture":
        status = "offline_review_only"
        editorial_decision = "offline_fixture_passed" if decision == "pass_candidate" else decision
    elif decision == "pass_candidate":
        status = "review_passed"
        editorial_decision = "approved_for_feed"
    elif decision == "return_to_crawler":
        status = "needs_crawler_update"
        editorial_decision = "return_to_crawler"
    else:
        status = "review_returned"
        editorial_decision = decision

    if review_mode != "offline_fixture":
        AgentRepository().persist_generated_content(
            package, status, report, usage_records=state.get("usage_records", [])
        )
    return {
        "trace": append_trace(state, "review_agent"),
        "package": package.model_dump(mode="json"),
        "qa_report": [issue.model_dump(mode="json") for issue in issues],
        "quality_report": report,
        "quality_score": int(report["total"]),
        "language_review": review.model_dump(mode="json"),
        "review_mode": review_mode,
        "needs_revision": decision == "revise",
        "revision_owner": _select_revision_owner(
            issues, int(state.get("review_failures", 0))
        ),
        "revision_owners": _select_revision_owners(issues),
        "review_failures": failures,
        "editorial_decision": editorial_decision,
        "status": status,
        "usage_records": state.get("usage_records", []),
    }
