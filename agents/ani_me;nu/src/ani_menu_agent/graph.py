"""Top-level coordinator for distinct generation and reviewer agents."""

from langgraph.graph import END, START, StateGraph

from ani_menu_agent.agents.content_generation.graph import build_content_generation_agent
from ani_menu_agent.agents.reviewer.graph import build_reviewer_agent
from ani_menu_agent.nodes import (
    archive_rejected,
    crawler_update_required,
    feed_queue,
    intake,
    revision_checkpoint,
    rights_preflight,
    route_intake,
    route_review,
    route_revision,
    route_rights_preflight,
)
from ani_menu_agent.state import ContentState


def build_graph():
    """Compose crawler handoff, isolated authoring/review subgraphs, and bounded retries."""
    graph = StateGraph(ContentState)
    graph.add_node("intake", intake)
    graph.add_node("rights_preflight", rights_preflight)
    graph.add_node("content_generation_agent", build_content_generation_agent())
    graph.add_node("reviewer_agent", build_reviewer_agent())
    graph.add_node("revision_checkpoint", revision_checkpoint)
    graph.add_node("archive_rejected", archive_rejected)
    graph.add_node("crawler_update_required", crawler_update_required)
    graph.add_node("feed_queue", feed_queue)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        route_intake,
        {"rights_preflight": "rights_preflight", "end": END},
    )
    graph.add_conditional_edges(
        "rights_preflight",
        route_rights_preflight,
        {
            "content_generation_agent": "content_generation_agent",
            "crawler_update_required": "crawler_update_required",
        },
    )
    graph.add_conditional_edges(
        "content_generation_agent",
        route_after_content_generation,
        {
            "reviewer_agent": "reviewer_agent",
            "crawler_update_required": "crawler_update_required",
        },
    )
    graph.add_conditional_edges(
        "reviewer_agent",
        route_review,
        {
            "feed_queue": "feed_queue",
            "crawler_update_required": "crawler_update_required",
            "revision_checkpoint": "revision_checkpoint",
            "archive_rejected": "archive_rejected",
            "end": END,
        },
    )
    graph.add_conditional_edges("revision_checkpoint", route_revision)
    graph.add_edge("archive_rejected", END)
    graph.add_edge("crawler_update_required", END)
    graph.add_edge("feed_queue", END)
    return graph.compile()


def route_after_content_generation(state: ContentState) -> str:
    """Do not render/review a deck when unique image material must be recollected."""
    return (
        "crawler_update_required"
        if state.get("status") == "needs_crawler_update"
        else "reviewer_agent"
    )
