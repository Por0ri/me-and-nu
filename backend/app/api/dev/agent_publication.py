"""Explicit local publishing controls for persisted movie Agent drafts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, local_dev_auth_bypass_enabled, require_csrf
from app.api.dev.agent_runs import require_local_dev_request
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.models import Content, UserAccount
from app.schemas.common import COMMON_ERRORS, CamelModel, ErrorResponse
from app.services.movie_agent_publication import MovieAgentPublicationError, publish_movie_agent_run


router = APIRouter(
    prefix="/agent-runs",
    tags=["개발용 Agent 결과"],
    dependencies=[Depends(require_local_dev_request)],
)


class PublicationResponse(CamelModel):
    content_id: int
    source_draft_id: int
    topic_id: int
    status: str
    judgment_status: str
    preview: bool
    feed_url: str
    detail_url: str


def _response(content: Content) -> PublicationResponse:
    topic_id = content.topic_id
    content_id = content.content_id
    basis = content.ai_judgment_basis if isinstance(content.ai_judgment_basis, dict) else {}
    return PublicationResponse(
        content_id=content_id,
        source_draft_id=content.source_draft_id,
        topic_id=topic_id,
        status=content.status,
        judgment_status=content.judgment_status,
        preview=bool(basis.get("preview")),
        feed_url=f"/api/v1/topics/{topic_id}/feed",
        detail_url=f"/api/v1/contents/{content_id}?topicId={topic_id}",
    )


async def _publish(db: AsyncSession, agent_run_id: int, *, force_preview: bool) -> PublicationResponse:
    try:
        content = await publish_movie_agent_run(db, agent_run_id, force_preview=force_preview)
    except MovieAgentPublicationError as exc:
        raise ApiError(
            404 if exc.code == "AGENT_RUN_NOT_FOUND" else 409,
            exc.code,
            exc.message,
        ) from exc
    return _response(content)


@router.post(
    "/{agentRunId}/publish",
    response_model=PublicationResponse,
    summary="검증 완료된 Agent 초안을 공개 피드에 발행",
    description="성공·승인 판정이고 사실 확인 항목이 없는 초안만 발행합니다. 같은 실행을 다시 호출해도 기존 콘텐츠를 반환합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "Agent 실행 없음"}, 409: {"model": ErrorResponse, "description": "발행 조건 미충족"}},
)
async def publish_approved_agent_run(
    agent_run_id: Annotated[int, Path(alias="agentRunId", gt=0)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[UserAccount, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> PublicationResponse:
    return await _publish(db, agent_run_id, force_preview=False)


@router.post(
    "/{agentRunId}/publish-preview",
    response_model=PublicationResponse,
    summary="Agent 탈락 초안을 로컬 피드 테스트용으로 게시",
    description="명시적으로 켠 로컬 인증 생략 모드에서만 사용합니다. 공개 피드에 테스트 표식과 needs_review 판정으로 표시됩니다.",
    responses={**COMMON_ERRORS, 403: {"model": ErrorResponse, "description": "로컬 테스트 모드 필요"}, 404: {"model": ErrorResponse, "description": "Agent 실행 없음"}, 409: {"model": ErrorResponse, "description": "게시 조건 미충족"}},
)
async def publish_agent_preview(
    request: Request,
    agent_run_id: Annotated[int, Path(alias="agentRunId", gt=0)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[UserAccount, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> PublicationResponse:
    if not local_dev_auth_bypass_enabled(request):
        raise ApiError(403, "DEV_PREVIEW_DISABLED", "로컬 인증 생략 모드에서만 테스트 게시를 할 수 있습니다.")
    return await _publish(db, agent_run_id, force_preview=True)
