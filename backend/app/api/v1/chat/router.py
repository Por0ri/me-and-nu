from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.chat import (
    ChatJobResponse,
    ChatMessageAccepted,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionResponse,
)
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["AI 대화"])


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=ChatSessionResponse,
    summary="AI 대화 생성 (API-048)",
    description="현재 분야의 AI 대화를 만듭니다. 콘텐츠 상세에서 시작하면 anchorContentId를 보냅니다.",
    responses={**COMMON_ERRORS, 422: {"model": ErrorResponse, "description": "CHAT_TOPIC_MISMATCH"}},
)
async def create_chat_session(
    request: ChatSessionCreate,
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionResponse:
    return await chat_service.create_session(
        db, current_user.user_id, request.topic_id, request.anchor_content_id
    )


@router.post(
    "/sessions/{sessionId}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChatMessageAccepted,
    summary="질문 전송·AI 답변 생성 (API-052)",
    description="질문을 접수하고 jobId를 돌려줍니다. 답은 GET /chat/jobs/{jobId}로 조회합니다. 같은 clientMessageId는 같은 작업을 돌려줍니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CHAT_SESSION_NOT_FOUND"}},
)
async def post_chat_message(
    request: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    session_id: Annotated[int, Path(alias="sessionId", gt=0)],
) -> ChatMessageAccepted:
    accepted, is_new = await chat_service.post_message(db, current_user.user_id, session_id, request)
    if is_new:
        background_tasks.add_task(chat_service.generate_answer, accepted.job_id)
    return accepted


@router.get(
    "/jobs/{jobId}",
    response_model=ChatJobResponse,
    summary="AI 답변 생성 상태·결과 (API-053)",
    description="status는 processing, completed, failed 중 하나입니다. completed면 result에 답변과 근거 콘텐츠가 있습니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CHAT_JOB_NOT_FOUND"}},
)
async def get_chat_job(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: Annotated[int, Path(alias="jobId", gt=0)],
) -> ChatJobResponse:
    return await chat_service.get_job(db, current_user.user_id, job_id)
