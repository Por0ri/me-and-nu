"""API-033 activity events used by recommendation learning and evaluation."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.activity import ActivityEventsRequest, ActivityEventsResponse
from app.schemas.common import COMMON_ERRORS
from app.services import activity_service

router = APIRouter(prefix="/users/me", tags=["활동 기록"])


@router.post(
    "/activity-events",
    response_model=ActivityEventsResponse,
    summary="활동 기록 저장",
    description=(
        "노출·열람·좋아요 등 콘텐츠 활동을 최대 100개까지 한 번에 저장합니다. "
        "같은 clientEventId는 한 번만 저장하고 나머지는 duplicated로 셉니다. "
        "본인 활성 Tap·공개 콘텐츠·본인 추천 결과가 아닌 이벤트는 그 이벤트만 저장하지 않고 rejected로 셉니다. "
        "occurredAt이 서버 시각보다 5분 넘게 미래이거나 7일 넘게 과거이면 받은 시각으로 저장합니다."
    ),
    responses=COMMON_ERRORS,
)
async def post_activity_events(
    body: ActivityEventsRequest,
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActivityEventsResponse:
    return await activity_service.record_events(db, current_user.user_id, body)
