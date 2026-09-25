"""Read-only inspection of persisted movie Agent output on a local dev server."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import _LOOPBACK_HOSTS, get_current_user
from app.core.config import settings
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.models import AgentRun, AgentRunSource, AgentRunStep, Draft, JudgmentLog, UserAccount
from app.schemas.common import COMMON_ERRORS, CamelModel, ErrorResponse


def require_local_dev_request(request: Request) -> None:
    if not (
        settings.enable_dev_api
        and request.url.hostname in _LOOPBACK_HOSTS
        and request.client is not None
        and request.client.host in _LOOPBACK_HOSTS
    ):
        raise ApiError(403, "LOCAL_ONLY", "로컬 개발 환경에서만 조회할 수 있습니다.")


router = APIRouter(
    prefix="/agent-runs",
    tags=["개발용 Agent 결과"],
    dependencies=[Depends(require_local_dev_request)],
)


class FactcheckView(CamelModel):
    requires_review: bool
    items: list[str]


class AgentDraftView(CamelModel):
    draft_id: int
    title: str | None
    body: str | None
    status: str
    factcheck_result: FactcheckView


class JudgmentView(CamelModel):
    judgment_log_id: int
    attempt_no: int
    decision: str | None
    reason: str | None
    scores: dict[str, int]
    is_adopted: bool


class StepView(CamelModel):
    step_order: int
    step_name: str
    attempt_no: int
    result: str | None
    issues: list[str]
    reason: str | None


class AgentRunView(CamelModel):
    agent_run_id: int
    agent_code: str
    agent_version: str
    status: str
    outcome: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    draft: AgentDraftView | None
    source_urls: list[str]
    judgments: list[JudgmentView]
    steps: list[StepView]


class AgentRunList(CamelModel):
    items: list[AgentRunView]
    limit: int
    offset: int
    has_more: bool


def _view(
    run: AgentRun,
    drafts: dict[int, Draft],
    sources: dict[int, list[str]],
    judgments: dict[int, list[JudgmentView]],
    steps: dict[int, list[StepView]],
) -> AgentRunView:
    draft = drafts.get(run.agent_run_id)
    factcheck = draft.factcheck_result if draft is not None else None
    if not isinstance(factcheck, dict):
        factcheck = {}
    raw_items = factcheck.get("items")
    review_items = [item for item in raw_items if isinstance(item, str)] if isinstance(raw_items, list) else []
    return AgentRunView(
        agent_run_id=run.agent_run_id,
        agent_code=run.agent_code,
        agent_version=run.agent_version,
        status=run.status,
        outcome=run.outcome,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        draft=(
            AgentDraftView(
                draft_id=draft.draft_id,
                title=draft.title,
                body=draft.body,
                status=draft.status,
                factcheck_result=FactcheckView(
                    requires_review=bool(factcheck.get("requires_review")),
                    items=review_items,
                ),
            )
            if draft is not None
            else None
        ),
        source_urls=sources.get(run.agent_run_id, []),
        judgments=judgments.get(run.agent_run_id, []),
        steps=steps.get(run.agent_run_id, []),
    )


async def _related(
    db: AsyncSession, run_ids: list[int]
) -> tuple[
    dict[int, Draft],
    dict[int, list[str]],
    dict[int, list[JudgmentView]],
    dict[int, list[StepView]],
]:
    if not run_ids:
        return {}, {}, {}, {}
    drafts = {
        draft.agent_run_id: draft
        for draft in (
            await db.scalars(select(Draft).where(Draft.agent_run_id.in_(run_ids)))
        ).all()
    }
    sources: dict[int, list[str]] = {}
    rows = (
        await db.scalars(
            select(AgentRunSource)
            .where(AgentRunSource.agent_run_id.in_(run_ids))
            .order_by(
                AgentRunSource.agent_run_id,
                AgentRunSource.agent_run_source_id,
            )
        )
    ).all()
    for source in rows:
        sources.setdefault(source.agent_run_id, []).append(source.source_url)
    judgments: dict[int, list[JudgmentView]] = {}
    rows = (
        await db.scalars(
            select(JudgmentLog)
            .where(JudgmentLog.agent_run_id.in_(run_ids))
            .order_by(JudgmentLog.agent_run_id, JudgmentLog.attempt_no, JudgmentLog.judgment_log_id)
        )
    ).all()
    for judgment in rows:
        result = judgment.judgment_result if isinstance(judgment.judgment_result, dict) else {}
        raw_scores = result.get("점수")
        scores = (
            {key: value for key, value in raw_scores.items() if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)}
            if isinstance(raw_scores, dict)
            else {}
        )
        judgments.setdefault(judgment.agent_run_id, []).append(
            JudgmentView(
                judgment_log_id=judgment.judgment_log_id,
                attempt_no=judgment.attempt_no,
                decision=result.get("결과") if isinstance(result.get("결과"), str) else None,
                reason=result.get("이유") if isinstance(result.get("이유"), str) else None,
                scores=scores,
                is_adopted=judgment.is_adopted,
            )
        )
    steps: dict[int, list[StepView]] = {}
    rows = (
        await db.scalars(
            select(AgentRunStep)
            .where(AgentRunStep.agent_run_id.in_(run_ids))
            .order_by(AgentRunStep.agent_run_id, AgentRunStep.step_order)
        )
    ).all()
    for step in rows:
        payload = step.step_result if isinstance(step.step_result, dict) else {}
        raw_result = payload.get("결과")
        issues = [item for item in raw_result if isinstance(item, str)] if isinstance(raw_result, list) else []
        steps.setdefault(step.agent_run_id, []).append(
            StepView(
                step_order=step.step_order,
                step_name=step.step_name,
                attempt_no=step.attempt_no,
                result=raw_result if isinstance(raw_result, str) else None,
                issues=issues,
                reason=payload.get("이유") if isinstance(payload.get("이유"), str) else None,
            )
        )
    return drafts, sources, judgments, steps


@router.get(
    "",
    response_model=AgentRunList,
    summary="저장된 영화 Agent 실행 목록",
    description="로컬 개발용 읽기 전용 조회입니다. 탈락 초안과 판정 단계 요약을 포함하며 원본 입출력과 비밀값은 노출하지 않습니다.",
    responses=COMMON_ERRORS,
)
async def list_agent_runs(
    _current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentRunList:
    rows = (
        await db.scalars(
            select(AgentRun)
            .where(AgentRun.agent_code == "movie_review")
            .order_by(AgentRun.created_at.desc(), AgentRun.agent_run_id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    drafts, sources, judgments, steps = await _related(db, [run.agent_run_id for run in page])
    return AgentRunList(
        items=[_view(run, drafts, sources, judgments, steps) for run in page],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


@router.get(
    "/{agentRunId}",
    response_model=AgentRunView,
    summary="저장된 영화 Agent 실행 상세",
    description="로컬 개발용 읽기 전용 조회입니다. 실패 또는 탈락 결과도 확인할 수 있습니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "AGENT_RUN_NOT_FOUND"}},
)
async def get_agent_run(
    _current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_run_id: Annotated[int, Path(alias="agentRunId", gt=0)],
) -> AgentRunView:
    run = await db.scalar(
        select(AgentRun).where(
            AgentRun.agent_run_id == agent_run_id,
            AgentRun.agent_code == "movie_review",
        )
    )
    if run is None:
        raise ApiError(404, "AGENT_RUN_NOT_FOUND", "Agent 실행 기록을 찾을 수 없습니다.")
    drafts, sources, judgments, steps = await _related(db, [agent_run_id])
    return _view(run, drafts, sources, judgments, steps)
