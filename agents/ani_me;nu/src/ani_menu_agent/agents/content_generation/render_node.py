"""Deterministic card-art composition and database BLOB persistence node."""

from time import perf_counter

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.agents.content_generation.renderer import render_package_cards
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository


def render_cards(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    repository = AgentRepository()
    started = perf_counter()
    png_by_page = render_package_cards(package, get_image_bytes=repository.get_image_bytes)
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
        "rendered_image_keys": {str(card.page): card.rendered_image_key for card in package.cards},
        "status": "generated_pending_review",
    }
