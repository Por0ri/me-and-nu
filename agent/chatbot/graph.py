"""그래프 조립과 한 턴 실행."""

import asyncio
from dataclasses import dataclass, field

from langgraph.graph import END, START, StateGraph

from agent.chatbot import limits, nodes, phrases
from agent.chatbot.state import ChatDeps, ChatState


@dataclass
class TurnInput:
    topic_id: int
    topic_code: str
    topic_name: str
    question: str
    other_topics: list[dict] = field(default_factory=list)
    selected_text: str | None = None
    anchor: dict | None = None
    history: list[dict] = field(default_factory=list)
    memory_summary: str | None = None
    fact_providers: list[str] = field(default_factory=list)


@dataclass
class TurnOutcome:
    result_type: str  # answer | topic_switch_suggested | needs_clarification | blocked | failed
    message: str
    sources: list[dict]
    notices: list[dict]
    unanswered_reason: str | None
    steps: list[dict]
    llm_calls: int
    route: str | None
    blocked_by: str | None = None


def build_graph():
    graph = StateGraph(ChatState)
    graph.add_node("front", nodes.front)
    graph.add_node("gate", nodes.gate)
    graph.add_node("planner", nodes.planner)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("fact", nodes.fact)
    graph.add_node("write", nodes.write)
    graph.add_node("exit_check", nodes.exit_check)

    graph.add_edge(START, "front")
    graph.add_conditional_edges("front", nodes.route_stop_or("gate"), {"gate": "gate", "end": END})
    graph.add_conditional_edges("gate", nodes.route_stop_or("planner"), {"planner": "planner", "end": END})
    graph.add_conditional_edges(
        "planner",
        nodes.route_after_planner,
        {"retrieve": "retrieve", "fact": "fact", "write": "write"},
    )
    graph.add_conditional_edges(
        "retrieve", nodes.route_after_retrieve, {"fact": "fact", "planner": "planner"}
    )
    graph.add_edge("fact", "planner")
    graph.add_conditional_edges("write", nodes.route_after_write, {"exit_check": "exit_check", "end": END})
    graph.add_conditional_edges("exit_check", nodes.route_after_exit, {"write": "write", "end": END})
    return graph.compile()


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_turn(turn: TurnInput, deps: ChatDeps) -> TurnOutcome:
    state: ChatState = {
        "topic_id": turn.topic_id,
        "topic_code": turn.topic_code,
        "topic_name": turn.topic_name,
        "other_topics": turn.other_topics,
        "question": turn.question,
        "selected_text": turn.selected_text,
        "anchor": turn.anchor,
        "history": turn.history[-limits.HISTORY_TURNS * 2 :],
        "memory_summary": turn.memory_summary,
        "fact_providers": turn.fact_providers,
        "started_at": deps.clock(),
    }
    config = {**deps.as_config(), "recursion_limit": limits.RECURSION_LIMIT}
    # 루프 E의 시간 상한은 총괄이 본다. 여기서는 멈춘 호출을 끊는 마지막 벽만 둔다.
    final = await asyncio.wait_for(
        _graph().ainvoke(state, config=config), limits.TURN_SECONDS * 3
    )
    outcome = final.get("outcome") or {
        "result_type": "failed",
        "message": phrases.LLM_DOWN,
        "sources": [],
        "notices": [],
        "unanswered_reason": None,
    }
    return TurnOutcome(
        result_type=outcome["result_type"],
        message=outcome["message"],
        sources=outcome.get("sources", []),
        notices=outcome.get("notices", []),
        unanswered_reason=outcome.get("unanswered_reason"),
        steps=final.get("steps", []),
        llm_calls=final.get("llm_calls", 0),
        route=final.get("route"),
        blocked_by=outcome.get("blocked_by"),
    )
