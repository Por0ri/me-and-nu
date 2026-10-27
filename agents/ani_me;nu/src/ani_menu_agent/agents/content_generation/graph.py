"""콘텐츠 생성만 담당하는 하위 LangGraph."""

from langgraph.graph import END, START, StateGraph

from ani_menu_agent.agents.content_generation.render_node import render_cards
from ani_menu_agent.nodes import copy_writer, layout_director, repair_visual_assignments
from ani_menu_agent.state import ContentState


def _route_after_layout(state: ContentState) -> str:
    if state.get("status") == "needs_crawler_update":
        return "end"
    return "render" if state.get("render_required", False) else "end"


def _route_after_visual_repair(state: ContentState) -> str:
    if state.get("status") == "needs_crawler_update":
        return "end"
    return "render" if state.get("render_required", False) else "end"


def _route_generation_entry(state: ContentState) -> str:
    if state.get("needs_revision") and state.get("revision_owner") == "image_curator":
        return "visual_repair"
    return "copy_writer"


def build_content_generation_agent():
    """Create a graph that writes copy and assigns image/layout IDs, without judging it."""
    graph = StateGraph(ContentState)
    graph.add_node("copy_writer", copy_writer)
    graph.add_node("layout_director", layout_director)
    graph.add_node("visual_repair", repair_visual_assignments)
    graph.add_node("card_renderer", render_cards)
    graph.add_conditional_edges(
        START,
        _route_generation_entry,
        {"copy_writer": "copy_writer", "visual_repair": "visual_repair"},
    )
    graph.add_edge("copy_writer", "layout_director")
    graph.add_conditional_edges(
        "layout_director",
        _route_after_layout,
        {"render": "card_renderer", "end": END},
    )
    graph.add_conditional_edges(
        "visual_repair",
        _route_after_visual_repair,
        {"render": "card_renderer", "end": END},
    )
    graph.add_edge("card_renderer", END)
    return graph.compile()
