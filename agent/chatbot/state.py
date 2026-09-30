"""LangGraph 상태와 에이전트가 쓰는 도구 묶음."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    # 입력 — 코드가 채운다. LLM은 바꾸지 못한다.
    topic_id: int
    topic_code: str
    topic_name: str
    other_topics: list[dict]  # [{"code", "name", "topicId"}]
    question: str
    selected_text: str | None
    anchor: dict | None  # {"contentId", "title", "excerpt", "url"}
    history: list[dict]  # [{"role": "user"|"assistant", "text"}]
    memory_summary: str | None
    fact_providers: list[str]
    started_at: float

    # 횟수 — 코드가 센다.
    llm_calls: int
    sub_calls: int
    replans: int
    planner_runs: int
    retrieve_tries: int
    fact_tries: int
    exit_retries: int

    # 진행
    route: str  # work | chitchat | unrelated | other_topic | unclear
    target_topic_code: str | None
    next_step: str  # retrieve | fact | write
    pending_fact: bool
    retrieve_query: str
    fact_query: dict | None
    evidence: list[dict]  # [{"contentId", "title", "url", "excerpt"}]
    evidence_enough: bool
    facts: list[dict]  # [{"text", "provider", "attribution", "checkedAt"}]
    fact_failed: bool
    clarify: str | None
    limit_hit: str | None  # calls | time
    draft: dict | None
    exit_feedback: list[str]

    # 끝
    outcome: dict | None
    steps: list[dict]


@dataclass
class ChatDeps:
    """에이전트가 밖과 닿는 곳. 테스트에서는 가짜로 바꿔 끼운다."""

    # (지시문, 입력, 추론 강도) → JSON dict
    llm_json: Callable[[str, str, str], Awaitable[dict]]
    # 글 → {"flagged": bool, "categories": {이름: bool}}
    moderate: Callable[[str], Awaitable[dict]]
    # (토픽 ID, 검색어, 뺄 글 ID들) → 후보 글 [{"contentId", "title", "url", "excerpt"}]
    search: Callable[[int, str, set[int]], Awaitable[list[dict]]]
    # (조회처, 검색어, 토픽 ID) → {"provider", "attribution", "checkedAt", "data"} 또는 None
    fact_lookup: Callable[[str, str, int], Awaitable[dict | None]]
    clock: Callable[[], float] = field(default=time.monotonic)
    model_name: str = "gpt-6-luna"

    def as_config(self) -> dict[str, Any]:
        return {"configurable": {"deps": self}}
