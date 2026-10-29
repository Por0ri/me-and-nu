"""Deterministic card-art composition and database BLOB persistence node."""

import logging
from time import perf_counter

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.agents.content_generation.image_editor import edit_background_only
from ani_menu_agent.agents.content_generation.renderer import render_package_cards
from ani_menu_agent.config import get_settings
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.policy import POLICY
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository

logger = logging.getLogger(__name__)


def render_cards(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    repository = AgentRepository()
    started = perf_counter()
    png_by_page = render_package_cards(package, get_image_bytes=repository.get_image_bytes)
    settings = get_settings()
    prior_results = dict(state.get("image_edit_results", {}))
    edit_results = dict(prior_results)
    usage_records = list(state.get("usage_records", []))
    requested_pages = {
        int(issue["page"])
        for issue in state.get("qa_report", [])
        if issue.get("code") == "IMAGE_COMPOSITION_REJECTED" and issue.get("page")
    }
    layout_revision = "layout_director" in (
        state.get("revision_owners") or [state.get("revision_owner")]
    )
    if (
        settings.image_edit_enabled
        and POLICY.image_model_cost_budget_usd > 0
        and layout_revision
        and settings.openai_api_key
    ):
        cards_by_page = {card.page: card for card in package.cards}
        for page in sorted(requested_pages):
            if str(page) in prior_results:
                continue
            card = cards_by_page.get(page)
            if card is None or page not in png_by_page:
                edit_results[str(page)] = "skipped_missing_card"
                continue
            try:
                edited_png, usage = edit_background_only(
                    png_by_page[page],
                    protected_boxes=[card.layout.image_box, card.layout.text_overlay_box],
                    api_key=settings.openai_api_key,
                    model=settings.image_edit_model,
                )
                png_by_page[page] = edited_png
                usage_records.append(usage.model_dump(mode="json"))
                edit_results[str(page)] = "edited_background_only"
            except Exception as exc:  # an optional visual enhancement must never block a card
                logger.warning(
                    "Guarded background edit failed for page %s (%s)",
                    page,
                    type(exc).__name__,
                )
                edit_results[str(page)] = f"fallback_original:{type(exc).__name__}"
    elif requested_pages and settings.image_edit_enabled and not settings.openai_api_key:
        for page in requested_pages:
            edit_results.setdefault(str(page), "skipped_missing_api_key")
    elif (
        requested_pages
        and settings.image_edit_enabled
        and POLICY.image_model_cost_budget_usd <= 0
    ):
        for page in requested_pages:
            edit_results.setdefault(str(page), "skipped_image_model_cost_budget")
    package = repository.persist_rendered_cards(package, png_by_page)
    # Persist copy and page metadata at the generation boundary, not only after
    # the independent reviewer runs. The reviewer can later update this draft.
    repository.persist_generated_content(package, "generated_pending_review")
    elapsed = perf_counter() - started
    return {
        "trace": append_trace(state, "card_renderer"),
        "package": package.model_dump(mode="json"),
        "rendered_card_count": len(png_by_page),
        "render_elapsed_seconds": elapsed,
        "image_edit_results": edit_results,
        "usage_records": usage_records,
        "rendered_image_keys": {str(card.page): card.rendered_image_key for card in package.cards},
        "status": "generated_pending_review",
    }
