"""유머 후보와 참고 출처를 저장하는 LangGraph 노드."""

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository


def research_humor(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    saved = AgentRepository().persist_humor(package)
    return {
        "trace": append_trace(state, "research_humor"),
        "status": "humor_researched",
        "persisted_record_count": saved,
    }
