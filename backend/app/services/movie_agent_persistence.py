from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStep,
    Draft,
    JudgmentLog,
)


OUTCOME_BY_AGENT_STATUS = {
    "대상아님": "not_target",
    "재료부족": "insufficient_material",
    "판단보류": "on_hold",
    "올림": "publish_candidate",
    "탈락보관": "rejected_archive",
}

DRAFT_STATUS_BY_OUTCOME = {
    "publish_candidate": "approved",
    "rejected_archive": "discarded",
}

STEP_NAME_BY_AGENT_STAGE = {
    "형태검사": "shape",
    "판정관": "judge",
}


@dataclass(frozen=True)
class MovieAgentPersistenceResult:
    agent_run: AgentRun
    draft: Draft | None
    step_count: int
    source_count: int
    judgment_count: int


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _mapping(value: Any) -> dict[str, Any]:
    converted = _jsonable(value)
    if not isinstance(converted, dict):
        raise TypeError("에이전트 결과는 dict 또는 Pydantic 모델이어야 합니다.")
    return converted


def _source_site_id(
    url: str,
    source_site_ids: Mapping[str, int] | None,
) -> int | None:
    if not source_site_ids:
        return None

    host = urlparse(url).netloc.lower()
    if "themoviedb.org" in host:
        return source_site_ids.get("TMDB")
    if "wikipedia.org" in host:
        return source_site_ids.get("한국어 위키백과")
    return None


def _source_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "themoviedb.org" in host:
        return "metadata"
    if "wikipedia.org" in host:
        return "context"
    return "article"


def _collect_source_urls(
    input_payload: Mapping[str, Any],
    article: Mapping[str, Any] | None,
) -> list[str]:
    candidates: list[Any] = []
    candidates.extend(input_payload.get("source_links") or [])
    if article:
        candidates.extend(article.get("sources") or [])

    unique_urls: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if not isinstance(value, str):
            continue
        url = value.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)
    return unique_urls


async def start_movie_agent_run(
    db: AsyncSession,
    *,
    topic_id: int,
    input_payload: Any,
    agent_code: str = "movie_review",
    agent_version: str = "V1.8",
    requested_by_user_id: int | None = None,
    queue_task_id: str | None = None,
) -> AgentRun:
    """에이전트 호출 전에 running 상태의 실행 기록을 생성합니다."""
    agent_run = AgentRun(
        requested_by_user_id=requested_by_user_id,
        topic_id=topic_id,
        agent_code=agent_code,
        agent_version=agent_version,
        queue_task_id=queue_task_id,
        status="running",
        input_payload=_mapping(input_payload),
        node_count=0,
        started_at=datetime.now(timezone.utc),
    )
    db.add(agent_run)

    try:
        await db.commit()
        await db.refresh(agent_run)
    except Exception:
        await db.rollback()
        raise

    return agent_run


async def complete_movie_agent_run(
    db: AsyncSession,
    *,
    agent_run: AgentRun,
    result: Any,
    source_site_ids: Mapping[str, int] | None = None,
) -> MovieAgentPersistenceResult:
    """완료 결과와 로그·출처·초안을 하나의 트랜잭션으로 저장합니다."""
    output = _mapping(result)
    agent_status = output.get("상태")
    try:
        outcome = OUTCOME_BY_AGENT_STATUS[agent_status]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 에이전트 상태입니다: {agent_status!r}") from exc

    logs = output.get("로그") or []
    if not isinstance(logs, list):
        raise TypeError("에이전트 결과의 '로그'는 list여야 합니다.")

    article_value = output.get("글")
    article = _mapping(article_value) if article_value is not None else None
    order_value = output.get("주문")
    order = _mapping(order_value) if order_value is not None else None
    factcheck_items = _jsonable(output.get("확인필요") or [])

    agent_run.status = "succeeded"
    agent_run.outcome = outcome
    agent_run.output_payload = output
    agent_run.error_message = None
    agent_run.node_count = len(logs)
    agent_run.finished_at = datetime.now(timezone.utc)

    steps: list[AgentRunStep] = []
    for step_order, row_value in enumerate(logs, start=1):
        row = _mapping(row_value)
        attempt_no = int(row.get("회차", 0)) + 1
        stage = str(row.get("단계") or "unknown")
        step = AgentRunStep(
            agent_run_id=agent_run.agent_run_id,
            step_order=step_order,
            step_name=STEP_NAME_BY_AGENT_STAGE.get(stage, stage[:30]),
            attempt_no=attempt_no,
            prompt_version=agent_run.agent_version,
            step_result=row,
            decision=(
                str(row["결과"])[:30]
                if row.get("결과") is not None
                else None
            ),
            started_at=agent_run.started_at or datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(step)
        steps.append(step)

    sources: list[AgentRunSource] = []
    for url in _collect_source_urls(agent_run.input_payload, article):
        source = AgentRunSource(
            agent_run_id=agent_run.agent_run_id,
            source_site_id=_source_site_id(url, source_site_ids),
            source_type=_source_type(url),
            source_url=url,
        )
        db.add(source)
        sources.append(source)

    draft: Draft | None = None
    if article is not None and outcome in DRAFT_STATUS_BY_OUTCOME:
        draft = Draft(
            agent_run_id=agent_run.agent_run_id,
            topic_id=agent_run.topic_id,
            production_type="ai",
            content_type="movie_review",
            title=article.get("title"),
            body=article.get("body"),
            structure_template=order,
            factcheck_result={
                "requires_review": bool(factcheck_items),
                "items": factcheck_items,
            },
            status=DRAFT_STATUS_BY_OUTCOME[outcome],
        )
        db.add(draft)
        await db.flush()

    judgments: list[JudgmentLog] = []
    for row_value in logs:
        row = _mapping(row_value)
        if row.get("단계") != "판정관" or row.get("점수") is None:
            continue
        judgment = JudgmentLog(
            agent_run_id=agent_run.agent_run_id,
            draft_id=draft.draft_id if draft is not None else None,
            stage_name="judge",
            agent_code=agent_run.agent_code,
            attempt_no=int(row.get("회차", 0)) + 1,
            prompt_version=agent_run.agent_version,
            judgment_result=row,
            is_adopted=row.get("결과") == "올림",
        )
        db.add(judgment)
        judgments.append(judgment)

    try:
        await db.commit()
        await db.refresh(agent_run)
        if draft is not None:
            await db.refresh(draft)
    except Exception:
        await db.rollback()
        raise

    return MovieAgentPersistenceResult(
        agent_run=agent_run,
        draft=draft,
        step_count=len(steps),
        source_count=len(sources),
        judgment_count=len(judgments),
    )


async def fail_movie_agent_run(
    db: AsyncSession,
    *,
    agent_run: AgentRun,
    error: BaseException | str,
) -> AgentRun:
    """에이전트 실행 예외를 failed 상태로 기록합니다."""
    agent_run.status = "failed"
    agent_run.outcome = None
    agent_run.error_message = str(error)[:4000]
    agent_run.finished_at = datetime.now(timezone.utc)

    try:
        await db.commit()
        await db.refresh(agent_run)
    except Exception:
        await db.rollback()
        raise

    return agent_run
