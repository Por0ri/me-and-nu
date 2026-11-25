"""에이전트 노드 공통 유틸리티."""

from rag_local.content.state import ContentState


def append_trace(state: ContentState, name: str) -> list[str]:
    return [*state.get("trace", []), name]
