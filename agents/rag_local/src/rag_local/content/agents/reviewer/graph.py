"""독립 평가·검수 전용 LangGraph."""

from langgraph.graph import END, START, StateGraph

from rag_local.content.agents.reviewer.agent import review_content
from rag_local.content.state import ContentState


def build_reviewer_agent():
    graph = StateGraph(ContentState)
    graph.add_node("review_agent", review_content)
    graph.add_edge(START, "review_agent")
    graph.add_edge("review_agent", END)
    return graph.compile()
