"""소비자 챗봇 에이전트(agent/chatbot) 연결.

에이전트 본체는 저장소 루트의 agent/chatbot에 있다. 여기서는 루트를 찾을 수 있게 하고,
DB를 쓰는 도구(검색 · 사실 조회)와 LLM 호출을 묶어 에이전트에 넘긴다.
"""

import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agent.chatbot import limits, phrases, prompts  # noqa: E402
from agent.chatbot.graph import TurnInput, TurnOutcome, run_turn  # noqa: E402
from agent.chatbot.state import ChatDeps  # noqa: E402

from app.integrations import chatbot_facts, chatbot_search, llm  # noqa: E402

__all__ = [
    "ChatDeps",
    "TurnInput",
    "TurnOutcome",
    "build_deps",
    "limits",
    "phrases",
    "prompts",
    "run_turn",
]


def build_deps(db: AsyncSession, topic_code: str) -> ChatDeps:
    async def llm_json(instructions: str, prompt: str, effort: str) -> dict:
        return await llm.ask_json(
            instructions, prompt, timeout=limits.CALL_TIMEOUT_SECONDS, reasoning_effort=effort
        )

    async def search(topic_id: int, query: str, exclude: set[int]) -> list[dict]:
        return await chatbot_search.search(db, topic_id, query, exclude)

    async def fact_lookup(provider: str, query: str, topic_id: int) -> dict | None:
        return await chatbot_facts.lookup(db, provider, query, topic_id, topic_code)

    return ChatDeps(
        llm_json=llm_json,
        moderate=llm.moderate,
        search=search,
        fact_lookup=fact_lookup,
        model_name=llm.CHAT_MODEL,
    )
