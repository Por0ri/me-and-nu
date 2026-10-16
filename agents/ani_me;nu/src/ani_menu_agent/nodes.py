"""애니 콘텐츠 제작 파이프라인의 실행 노드."""

from pydantic import ValidationError

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.generation import generate_copy_deck
from ani_menu_agent.image_selection import assign_distinct_images, image_coverage_issue
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, QualityIssue
from ani_menu_agent.policy import POLICY
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository


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
    coverage_issue = image_coverage_issue(package)
    if coverage_issue is None:
        return {
            "trace": append_trace(state, "rights_preflight"),
            "status": "rights_cleared",
        }
    issue = QualityIssue.model_validate(coverage_issue)
    if not package.images:
        issue = issue.model_copy(
            update={
                "code": "IMAGE_RIGHTS_REVIEW",
                "message": "이용 범위와 출처가 확인된 공식 이미지가 없습니다.",
            }
        )
    return {
        "trace": append_trace(state, "rights_preflight"),
        "status": "needs_crawler_update",
        "qa_report": [issue.model_dump(mode="json")],
        "revision_owner": "image_curator",
    }


def copy_writer(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    revising_copy = (
        state.get("allow_llm", False)
        and state.get("needs_revision", False)
        and state.get("revision_owner") == "copy_writer"
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
    if revising_copy and package.cards:
        existing_cards = [
            CopyCard.model_validate(card.model_dump(exclude={"layout"})).model_dump(mode="json")
            for card in package.cards
        ]
        copy_issues = [issue for issue in issues if issue.owner == "copy_writer"]
        page_issues = [issue.page for issue in copy_issues if issue.page is not None]
        # Global issues cannot safely be assigned to one page; in that case the LLM
        # may revise the whole deck. Page-scoped issues stay limited to their cards.
        if copy_issues and all(issue.page is not None for issue in copy_issues):
            revision_pages = sorted(set(page_issues))
    generated = generate_copy_deck(
        package,
        issues=issues,
        existing_cards=existing_cards,
        revision_pages=revision_pages,
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
    usage_records = [*state.get("usage_records", []), generated.usage.model_dump(mode="json")]
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
    if not state.get("draft_cards"):
        if (
            state.get("needs_revision")
            and state.get("revision_owner") == "layout_director"
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
    copies, images_assigned = assign_distinct_images(copies, package)
    if not images_assigned:
        issue = QualityIssue.model_validate(
            image_coverage_issue(package, card_count=len(copies))
            or {
                "code": "IMAGE_COVERAGE_INSUFFICIENT",
                "message": "각 카드에 서로 다른 공식 이미지를 배정할 수 없습니다.",
                "owner": "image_curator",
            }
        )
        return {
            "trace": append_trace(state, "layout_director"),
            "package": package.model_dump(mode="json"),
            "qa_report": [issue.model_dump(mode="json")],
            "revision_owner": "image_curator",
            "status": "needs_crawler_update",
        }
    package.cards = build_cards(copies, package.images)
    return {
        "trace": append_trace(state, "layout_director"),
        "package": package.model_dump(mode="json"),
        "status": "layout_planned",
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
