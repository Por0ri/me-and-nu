"""카드 그림을 만들어 DB 에 저장한다."""

from time import perf_counter

from ani_rag.content.agents.common import append_trace
from ani_rag.content.agents.content_generation.renderer import render_package_cards
from ani_rag.content.models import ContentPackage
from ani_rag.content.state import ContentState
from ani_rag.content.storage import AgentRepository


def render_cards(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    repository = AgentRepository()
    started = perf_counter()
    png_by_page = render_package_cards(package, get_image_bytes=repository.get_image_bytes)
    package = repository.persist_rendered_cards(package, png_by_page)
    # 검수 전에 초안을 저장한다 (떨어진 글도 나중에 눈으로 볼 수 있게)
    repository.persist_generated_content(package, "generated_pending_review")
    return {
        "trace": append_trace(state, "card_renderer"),
        "package": package.model_dump(mode="json"),
        "rendered_card_count": len(png_by_page),
        "render_elapsed_seconds": perf_counter() - started,
        "usage_records": list(state.get("usage_records", [])),
        "rendered_image_keys": {str(card.page): card.rendered_image_key for card in package.cards},
        "status": "generated_pending_review",
    }
