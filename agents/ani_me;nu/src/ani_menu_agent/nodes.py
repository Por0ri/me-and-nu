"""애니 콘텐츠 제작 파이프라인의 실행 노드."""

import logging
import os

from pydantic import ValidationError

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.agents.content_generation.art_director import plan_card_art
from ani_menu_agent.config import get_settings
from ani_menu_agent.generation import generate_copy_deck
from ani_menu_agent.image_selection import assign_distinct_images
from ani_menu_agent.layout_engine import build_cards, reflow_for_composition
from ani_menu_agent.models import ContentPackage, CopyCard, QualityIssue
from ani_menu_agent.policy import POLICY
from ani_menu_agent.quality import check_copy, check_sources
from ani_menu_agent.source_policy import (
    supported_content_types_for_sources,
    validate_generation_sources,
)
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.work_catalog import attach_menu_work_ids

logger = logging.getLogger(__name__)


def _merge_copy_revision(
    existing: list[dict], replacements: list[CopyCard], revision_pages: list[int]
) -> list[CopyCard]:
    """Apply only approved page-scoped edits; leave every other card untouched."""
    replacement_by_page = {card.page: card for card in replacements}
    missing = set(revision_pages) - replacement_by_page.keys()
    if missing:
        raise RuntimeError(f"수정 대상 카드가 생성 결과에서 누락되었습니다: {sorted(missing)}")
    cards_by_page = {int(card["page"]): CopyCard.model_validate(card) for card in existing}
    for page in revision_pages:
        cards_by_page[page] = replacement_by_page[page]
    return [cards_by_page[page] for page in sorted(cards_by_page)]


def _remove_copy_pages(cards: list[CopyCard], remove_pages: list[int]) -> list[CopyCard]:
    """Honor an explicit reviewer request to drop a redundant middle card."""
    removed = set(remove_pages)
    kept = [card for card in cards if card.page not in removed]
    if len(kept) < POLICY.min_cards:
        raise RuntimeError("카드 삭제 후 최소 장수에 미달해 자동 삭제할 수 없습니다.")
    return [card.model_copy(update={"page": index}) for index, card in enumerate(kept, start=1)]


def _normalize_structural_roles(cards: list[CopyCard]) -> list[CopyCard]:
    """Roles are positional structure, not free-form copywriter output."""
    ordered = sorted(cards, key=lambda item: item.page)
    last = len(ordered) - 1
    return [
        card.model_copy(
            update={
                "role": "cover" if index == 0 else "ending" if index == last else "detail"
            }
        )
        for index, card in enumerate(ordered)
    ]


def _cover_image_issue(package: ContentPackage) -> QualityIssue | None:
    cover = next((card for card in package.cards if card.role == "cover"), None)
    if cover is None or cover.visual_mode != "source_image" or cover.image_id not in {
        image.id for image in package.images
    }:
        return QualityIssue(
            code="COVER_SOURCE_IMAGE_MISSING",
            message=(
                "표지에 사용할 관련 출처 이미지가 없습니다. "
                "원문 또는 동일 작품의 공식 이미지를 다시 수집하세요."
            ),
            page=cover.page if cover else None,
            owner="image_curator",
        )
    return None


def intake(state: ContentState) -> ContentState:
    """입력을 구조화 모델로 고정해 뒤 노드의 암묵적 추정을 막습니다."""
    try:
        package = ContentPackage.model_validate(state.get("package", {}))
    except ValidationError as exc:
        return {
            "trace": append_trace(state, "intake"),
            "status": "invalid_input",
            "qa_report": [
                QualityIssue(code="INVALID_INPUT", message=str(exc), owner="intake").model_dump()
            ],
        }
    return {
        "trace": append_trace(state, "intake"),
        "status": "intake_complete",
        "content_kind": package.content_kind.value,
        "package": package.model_dump(mode="json"),
    }


def rights_preflight(state: ContentState) -> ContentState:
    """차단된 이미지뿐이면 LLM 호출 전에 크롤러로 되돌립니다."""
    package = ContentPackage.model_validate(state["package"])
    integration_profile = (
        os.environ.get("ANI_MENU_REVIEW_PROFILE", "integration").strip().casefold()
        == "integration"
    )
    if integration_profile and state.get("allow_llm", False):
        # Temporary backend-integration run: retain canonical work matching and
        # relevant source images, while skipping fact/source approval gates.
        attach_menu_work_ids(package)
        if not package.menu_work_ids:
            issue = QualityIssue(
                code="WORK_ID_UNRESOLVED",
                message="임시 통합 모드에서도 작품 데이터셋 연결은 필요합니다.",
                owner="fact_checker",
            )
            return {
                "trace": append_trace(state, "rights_preflight"),
                "package": package.model_dump(mode="json"),
                "qa_report": [issue.model_dump(mode="json")],
                "status": "needs_crawler_update",
            }
    elif state.get("allow_llm", False):
        try:
            eligible_source_ids = validate_generation_sources(
                [source.model_dump(mode="json") for source in package.sources],
                [fact.model_dump(mode="json") for fact in package.facts],
                package.content_kind,
            )
        except ValueError as exc:
            issue = QualityIssue(
                code="SOURCE_APPROVAL_REQUIRED",
                message=str(exc),
                owner="fact_checker",
            )
            return {
                "trace": append_trace(state, "rights_preflight"),
                "package": package.model_dump(mode="json"),
                "qa_report": [issue.model_dump(mode="json")],
                "status": "needs_crawler_update",
            }
        attach_menu_work_ids(package)
        if not package.menu_work_ids:
            issue = QualityIssue(
                code="WORK_ID_UNRESOLVED",
                message=(
                    "원문 제목과 등록 출처의 작품 매핑에서 menu_work_id를 확인하지 못했습니다. "
                    "작품 ID 없는 콘텐츠는 추천 피드에 적재하지 않습니다."
                ),
                owner="fact_checker",
            )
            return {
                "trace": append_trace(state, "rights_preflight"),
                "package": package.model_dump(mode="json"),
                "qa_report": [issue.model_dump(mode="json")],
                "status": "needs_crawler_update",
            }
        if not supported_content_types_for_sources(eligible_source_ids):
            issue = QualityIssue(
                code="CONTENT_TYPE_SOURCE_UNSUPPORTED",
                message="검증된 출처 조합에 연결된 콘텐츠 유형이 없습니다.",
                owner="fact_checker",
            )
            return {
                "trace": append_trace(state, "rights_preflight"),
                "package": package.model_dump(mode="json"),
                "qa_report": [issue.model_dump(mode="json")],
                "status": "needs_crawler_update",
            }
        verified_fact_count = sum(fact.verified for fact in package.facts)
        if package.content_kind.value == "information" and verified_fact_count == 0:
            issue = QualityIssue(
                code="FACT_DIVERSITY_LOW",
                message=(
                    f"서로 다른 검증 사실이 {verified_fact_count}개뿐이라 "
                    f"최소 {POLICY.min_cards}장으로 구성할 수 없습니다. "
                    "원문 근거를 더 수집하거나 이 후보를 건너뜁니다."
                ),
                owner="fact_checker",
            )
            return {
                "trace": append_trace(state, "rights_preflight"),
                "package": package.model_dump(mode="json"),
                "qa_report": [issue.model_dump(mode="json")],
                "status": "needs_crawler_update",
            }
    from ani_menu_agent.image_selection import (
        cover_usable_editorial_images,
        distinct_editorial_images,
    )

    cover_images = cover_usable_editorial_images(
        package.images, package.sources, package
    )
    if not cover_images:
        issue = QualityIssue(
            code="SOURCE_IMAGE_POOL_INSUFFICIENT",
            message=(
                "표지에 쓸 수 있는 출처 확인 이미지가 없습니다. "
                "원문 또는 동일 작품의 공식 이미지를 수집하세요."
            ),
            owner="image_curator",
        )
        return {
            "trace": append_trace(state, "rights_preflight"),
            "package": package.model_dump(mode="json"),
            "qa_report": [issue.model_dump(mode="json")],
            "status": "needs_crawler_update",
        }
    eligible_images = distinct_editorial_images(
        package.images, package.sources, package
    )
    if len(eligible_images) < POLICY.min_cards:
        issue = QualityIssue(
            code="SOURCE_IMAGE_POOL_INSUFFICIENT",
            message=(
                f"Only {len(eligible_images)} distinct relevant images are eligible, but at least "
                f"{POLICY.min_cards} are needed because every card requires real anime art. "
                "Collect more same-work images; never fill the gap with geometric placeholders."
            ),
            owner="image_curator",
        )
        return {
            "trace": append_trace(state, "rights_preflight"),
            "package": package.model_dump(mode="json"),
            "qa_report": [issue.model_dump(mode="json")],
            "status": "needs_crawler_update",
        }
    return {"trace": append_trace(state, "rights_preflight"), "status": "rights_cleared"}


def copy_writer(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    requested_content_type = state.get("requested_content_type")
    if requested_content_type == "goods":
        # A mixed shop roundup can contain a giveaway campaign beside products.
        # Do not let campaign facts leak into a goods carousel; those need their
        # own event-type package and complete participation details.
        package.content_type = "goods"
        package.facts = [
            fact
            for fact in package.facts
            if fact.fact_type.strip().lower() not in {"campaign", "promotion"}
        ]
        package.event_start = None
        package.event_end = None
    revising_copy = (
        state.get("allow_llm", False)
        and state.get("needs_revision", False)
        and "copy_writer"
        in (state.get("revision_owners") or [state.get("revision_owner")])
    )
    if package.cards and not revising_copy:
        return {"trace": append_trace(state, "copy_writer"), "status": "copy_supplied"}
    if state.get("offline_cards"):
        cards = [CopyCard.model_validate(card) for card in state["offline_cards"]]
        usage_records = [
            *state.get("usage_records", []),
            {
                "model": "offline-fixture",
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "elapsed_seconds": 0.0,
            },
        ]
        return {
            "trace": append_trace(state, "copy_writer"),
            "draft_cards": [card.model_dump(mode="json") for card in cards],
            "usage_records": usage_records,
            "status": "copy_loaded_from_offline_fixture",
        }
    if not state.get("allow_llm", False):
        return {"trace": append_trace(state, "copy_writer"), "status": "copy_missing"}
    issues = [QualityIssue.model_validate(issue) for issue in state.get("qa_report", [])]
    existing_cards = None
    revision_pages: list[int] = []
    remove_pages: list[int] = []
    remove_only = False
    if revising_copy and package.cards:
        existing_cards = [
            CopyCard.model_validate(card.model_dump(exclude={"layout"})).model_dump(mode="json")
            for card in package.cards
        ]
        copy_issues = [issue for issue in issues if issue.owner == "copy_writer"]
        removable_pages = {
            issue.page
            for issue in copy_issues
            if issue.page is not None
            and any(
                token in issue.message
                for token in ("덜어내세요", "카드를 제거", "카드를 삭제")
            )
        }
        removable_pages &= {
            card.page for card in package.cards if card.role not in {"cover", "ending"}
        }
        if len(package.cards) - len(removable_pages) >= POLICY.min_cards:
            remove_pages = sorted(removable_pages)
        copy_issues = [issue for issue in copy_issues if issue.page not in remove_pages]
        remove_only = bool(remove_pages) and not copy_issues
        page_issues = [issue.page for issue in copy_issues if issue.page is not None]
        # Global issues cannot safely be assigned to one page; in that case the LLM
        # may revise the whole deck. Page-scoped issues stay limited to their cards.
        if copy_issues and all(issue.page is not None for issue in copy_issues):
            revision_pages = sorted(set(page_issues))
    usage_records = list(state.get("usage_records", []))
    generation_issues = [issue for issue in issues if issue.page not in remove_pages]
    if revising_copy and existing_cards and remove_only:
        generated_cards = [CopyCard.model_validate(card) for card in existing_cards]
    else:
        generated = generate_copy_deck(
            package,
            issues=generation_issues,
            existing_cards=existing_cards,
            revision_pages=revision_pages,
            revision_attempt=state.get("review_failures", 0) if revising_copy else 0,
            requested_content_type=state.get("requested_content_type"),
        )
        generated_cards = generated.deck.cards
        if not existing_cards or not revision_pages:
            package.content_type = generated.deck.content_type
        if not existing_cards or not revision_pages:
            package.editorial_angle = generated.deck.editorial_angle
            package.cover_mode = generated.deck.cover_mode
            package.ending_mode = generated.deck.ending_mode
        if existing_cards and revision_pages:
            generated_cards = _merge_copy_revision(existing_cards, generated_cards, revision_pages)
        usage_records.append(generated.usage.model_dump(mode="json"))
    if remove_pages:
        generated_cards = _remove_copy_pages(generated_cards, remove_pages)
    for _attempt in range(2):
        oversized = [
            card
            for card in generated_cards
            if len(" ".join(card.body_sentences).strip()) > POLICY.max_body_chars
        ]
        if not oversized:
            break
        revision_pages = [card.page for card in oversized]
        size_issues = [
            QualityIssue(
                code="BODY_TOO_LONG",
                message=(
                    f"본문을 {POLICY.max_body_chars}자 이내로 줄이세요. 핵심 사실은 보존하고 "
                    "세부 사항은 덜어내며, 다른 카드는 수정하지 마세요."
                ),
                page=card.page,
                owner="copy_writer",
            )
            for card in oversized
        ]
        current_cards = [card.model_dump(mode="json") for card in generated_cards]
        revised = generate_copy_deck(
            package,
            issues=size_issues,
            existing_cards=current_cards,
            revision_pages=revision_pages,
            requested_content_type=state.get("requested_content_type"),
        )
        generated_cards = _merge_copy_revision(
            current_cards,
            revised.deck.cards,
            revision_pages,
        )
        usage_records.append(revised.usage.model_dump(mode="json"))
    generated_cards = _normalize_structural_roles(generated_cards)
    if requested_content_type == "goods":
        from ani_menu_agent.extraction import _date_in_quote

        used_fact_ids = {fact_id for card in generated_cards for fact_id in card.fact_ids}
        release_periods: set[tuple[str, str]] = set()
        release_types = {
            "release",
            "product_release",
            "release_date",
            "goods_release",
            "sales_start",
        }
        for fact in package.facts:
            if not fact.verified or fact.id not in used_fact_ids:
                continue
            if fact.fact_type.strip().lower() not in release_types:
                continue
            quote = str(fact.details.get("source_quote") or "")
            dates = sorted(set(_date_in_quote(quote)))
            if len(dates) == 1:
                release_periods.add((dates[0], dates[0]))
            elif len(dates) >= 2:
                release_periods.add((dates[0], dates[-1]))
        if len(release_periods) == 1:
            package.event_start, package.event_end = next(iter(release_periods))
        else:
            package.event_start = None
            package.event_end = None
    if not existing_cards:
        # Catch copy-only defects before layout and image rendering. These issues have
        # enough evidence to repair in the copy writer and do not require recrawling.
        preview_package = package.model_copy(
            update={"cards": build_cards(generated_cards, package.images)}
        )
        preflight_issues = [
            issue
            for issue in [*check_sources(preview_package), *check_copy(preview_package)]
            if issue.code in {"SOURCE_PUBLICATION_DATE_SHOWN", "PROTECTED_PHRASE_LOST"}
        ]
        if preflight_issues:
            revision_pages = sorted(
                {issue.page for issue in preflight_issues if issue.page is not None}
            )
            if revision_pages:
                current_cards = [card.model_dump(mode="json") for card in generated_cards]
                revised = generate_copy_deck(
                    package,
                    issues=preflight_issues,
                    existing_cards=current_cards,
                    revision_pages=revision_pages,
                    requested_content_type=requested_content_type,
                )
                generated_cards = _merge_copy_revision(
                    current_cards,
                    revised.deck.cards,
                    revision_pages,
                )
                usage_records.append(revised.usage.model_dump(mode="json"))
    if any(
        len(" ".join(card.body_sentences).strip()) > POLICY.max_body_chars
        for card in generated_cards
    ):
        raise RuntimeError(
            "본문 길이가 제한을 넘습니다. 렌더링을 중단해 잘린 PNG가 저장되지 않도록 했습니다."
        )
    return {
        "trace": append_trace(state, "copy_writer"),
        "draft_cards": [card.model_dump(mode="json") for card in generated_cards],
        "package": package.model_dump(mode="json"),
        "usage_records": usage_records,
        "status": "copy_generated",
    }


def layout_director(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    if (
        state.get("needs_revision")
        and "layout_director"
        in (state.get("revision_owners") or [state.get("revision_owner")])
        and package.cards
    ):
        copies = [
            CopyCard.model_validate(card.model_dump(exclude={"layout"}))
            for card in package.cards
        ]
        package.cards = build_cards(copies, package.images)
        composition_pages = {
            int(issue["page"])
            for issue in state.get("qa_report", [])
            if issue.get("code") == "IMAGE_COMPOSITION_REJECTED" and issue.get("page")
        }
        package.cards = reflow_for_composition(
            package.cards, package.images, composition_pages
        )
        return {
            "trace": append_trace(state, "layout_director"),
            "package": package.model_dump(mode="json"),
            "status": (
                "layout_rebuilt_for_composition"
                if composition_pages
                else "layout_rebuilt_without_crop"
            ),
        }
    if not state.get("draft_cards"):
        if (
            state.get("needs_revision")
            and "layout_director"
            in (state.get("revision_owners") or [state.get("revision_owner")])
            and package.cards
        ):
            copies = [
                CopyCard.model_validate(card.model_dump(exclude={"layout"}))
                for card in package.cards
            ]
            package.cards = build_cards(copies, package.images)
            return {
                "trace": append_trace(state, "layout_director"),
                "package": package.model_dump(mode="json"),
                "status": "layout_rebuilt_without_crop",
            }
        return {"trace": append_trace(state, "layout_director"), "status": "layout_supplied"}
    copies = [CopyCard.model_validate(card) for card in state["draft_cards"]]
    copies = assign_distinct_images(copies, package)
    missing_pages = [
        card.page
        for card in copies
        if card.visual_mode != "source_image"
        or card.image_id.startswith("missing-source-image-")
    ]
    if missing_pages:
        return {
            "trace": append_trace(state, "layout_director"),
            "package": package.model_dump(mode="json"),
            "qa_report": [
                QualityIssue(
                    code=(
                        "COVER_SOURCE_IMAGE_MISSING"
                        if missing_pages[0] == min(card.page for card in copies)
                        else "SOURCE_IMAGE_MISSING"
                    ),
                    message=(
                        "Real related anime images are missing for card(s): "
                        f"{', '.join(map(str, missing_pages))}."
                    ),
                    page=missing_pages[0],
                    owner="image_curator",
                ).model_dump(mode="json")
            ],
            "status": "needs_crawler_update",
        }
    baseline_cards = build_cards(copies, package.images)
    art_directions = {}
    usage_records = list(state.get("usage_records", []))
    settings = get_settings()
    visual_layout_plan_attempted = bool(state.get("visual_layout_plan_attempted", False))
    if (
        not visual_layout_plan_attempted
        and state.get("allow_llm", False)
        and settings.visual_layout_enabled
        and settings.openai_api_key
    ):
        visual_layout_plan_attempted = True
        try:
            art_directions, visual_usage = plan_card_art(
                package,
                baseline_cards,
                model_name=settings.visual_layout_model,
                api_key=settings.openai_api_key,
                get_image_bytes=AgentRepository().get_image_bytes,
            )
            if visual_usage is not None:
                usage_records.append(visual_usage.model_dump(mode="json"))
            if not art_directions:
                logger.warning(
                    "Vision layout planner returned no complete plan; using deterministic layouts."
                )
        except Exception:
            # This is a visual enhancement only; a model outage must not block
            # the content workflow or relax any reviewer checks.
            logger.exception("Vision layout planning failed; using deterministic layouts.")
    package.cards = (
        build_cards(copies, package.images, art_directions=art_directions)
        if art_directions
        else baseline_cards
    )
    cover_issue = _cover_image_issue(package)
    if cover_issue:
        return {
            "trace": append_trace(state, "layout_director"),
            "package": package.model_dump(mode="json"),
            "usage_records": usage_records,
            "visual_layout_plan_attempted": visual_layout_plan_attempted,
            "qa_report": [cover_issue.model_dump(mode="json")],
            "status": "needs_crawler_update",
        }
    return {
        "trace": append_trace(state, "layout_director"),
        "package": package.model_dump(mode="json"),
        "usage_records": usage_records,
        "visual_layout_plan_attempted": visual_layout_plan_attempted,
        "status": "layout_planned",
    }


def repair_visual_assignments(state: ContentState) -> ContentState:
    """Repair only the visual layer after review; copy and verified facts stay untouched."""
    package = ContentPackage.model_validate(state["package"])
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]
    repaired = assign_distinct_images(copies, package)
    missing_pages = [
        card.page
        for card in repaired
        if card.visual_mode != "source_image"
        or card.image_id.startswith("missing-source-image-")
    ]
    if missing_pages:
        return {
            "trace": append_trace(state, "visual_repair"),
            "package": package.model_dump(mode="json"),
            "qa_report": [
                QualityIssue(
                    code="SOURCE_IMAGE_MISSING",
                    message=(
                        "시각 검수에서 제외한 이미지를 대체할 자료가 부족합니다. "
                        f"페이지 {', '.join(map(str, missing_pages))}의 동일 작품 이미지를 "
                        "더 수집하세요."
                    ),
                    page=missing_pages[0],
                    owner="image_curator",
                ).model_dump(mode="json")
            ],
            "status": "needs_crawler_update",
        }
    package.cards = build_cards(repaired, package.images)
    cover_issue = _cover_image_issue(package)
    if cover_issue:
        return {
            "trace": append_trace(state, "visual_repair"),
            "package": package.model_dump(mode="json"),
            "qa_report": [cover_issue.model_dump(mode="json")],
            "status": "needs_crawler_update",
        }
    return {
        "trace": append_trace(state, "visual_repair"),
        "package": package.model_dump(mode="json"),
        "status": "visuals_repaired",
    }


def revision_checkpoint(state: ContentState) -> ContentState:
    count = state.get("revision_count", 0) + 1
    return {
        "trace": append_trace(state, f"revision_{count}"),
        "revision_count": count,
        "status": "revision_required",
    }


def archive_rejected(state: ContentState) -> ContentState:
    """Archive a draft only after the independent reviewer exhausted its retry budget."""
    package = ContentPackage.model_validate(state["package"])
    issues = state.get("qa_report", [])
    reason = "; ".join(issue.get("message", issue.get("code", "")) for issue in issues) or (
        f"검수 기준 미달: 총점 {state.get('quality_score', 0)}, "
        f"판정 {state.get('quality_report', {}).get('decision', 'revise')}"
    )
    if not issues:
        issues = [{"code": "QUALITY_SCORE_BELOW_THRESHOLD", "message": reason}]
    repository = AgentRepository()
    repository.archive_rejected(package, state.get("review_failures", 0), issues)
    repository.set_content_status(package.request_id, "rejected_archived")
    return {
        "trace": append_trace(state, "rejected_archive"),
        "status": "rejected_archived",
        "editorial_decision": "rejected",
        "rejection_reason": reason,
    }


def feed_queue(state: ContentState) -> ContentState:
    """편집국장 통과물을 게시 대기 피드에 적재합니다."""
    package = ContentPackage.model_validate(state["package"])
    AgentRepository().enqueue_for_feed(package)
    return {
        "trace": append_trace(state, "feed_queue"),
        "status": "ready_for_feed",
        "editorial_decision": "approved_for_feed",
    }


def crawler_update_required(state: ContentState) -> ContentState:
    """Return source/image defects to the crawler queue; generation cannot patch evidence."""
    request_id = state.get("request_id")
    if request_id:
        repository = AgentRepository()
        repository.mark_request_status(request_id, "needs_crawler_update")
        repository.set_content_status(request_id, "needs_crawler_update")
    return {
        "trace": append_trace(state, "crawler_update_required"),
        "status": "needs_crawler_update",
        "editorial_decision": "return_to_crawler",
    }


def route_intake(state: ContentState) -> str:
    return "end" if state.get("status") == "invalid_input" else "rights_preflight"


def route_rights_preflight(state: ContentState) -> str:
    return (
        "crawler_update_required"
        if state.get("status") == "needs_crawler_update"
        else "content_generation_agent"
    )


def route_review(state: ContentState) -> str:
    """Route independent reviewer decisions without allowing offline fixtures to publish."""
    decision = state.get("editorial_decision")
    if state.get("review_mode") == "offline_fixture":
        return "end"
    if decision == "approved_for_feed":
        return "feed_queue"
    if decision == "return_to_crawler":
        return "crawler_update_required"
    if decision == "review_required":
        return "end"
    if state.get("review_failures", 0) >= state.get("max_revision", POLICY.max_review_failures):
        return "archive_rejected"
    return "revision_checkpoint"


def route_revision(state: ContentState) -> str:
    return "content_generation_agent"
