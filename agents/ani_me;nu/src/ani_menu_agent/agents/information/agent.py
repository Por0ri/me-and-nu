"""정보 수집 결과와 근거를 저장하는 LangGraph 노드."""

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository


def research_information(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    saved = AgentRepository().persist_information(package)
    return {
        "trace": append_trace(state, "research_information"),
        "status": "information_researched",
        "persisted_record_count": saved,
    }
