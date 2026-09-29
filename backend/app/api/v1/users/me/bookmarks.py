"""API-041 saved content list."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.bookmark import SavedBookmarksResponse
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import bookmark_service

router = APIRouter(prefix="/users/me", tags=["북마크"])


@router.get(
    "/bookmarks",
    response_model=SavedBookmarksResponse,
    summary="내 북마크 목록 조회",
    description=(
        "본인 활성 Topic의 공개 콘텐츠만 저장 시각 역순으로 반환합니다. "
        "숨김·삭제되었거나 출처가 없는 콘텐츠는 목록에서 제외합니다. "
        "q는 전체 저장 목록의 제목·요약·저장 태그를 검색합니다. "
        "같은 사용자와 필터에서만 nextCursor를 재사용할 수 있습니다."
    ),
    responses={
        **COMMON_ERRORS,
        404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"},
        422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 또는 INVALID_CURSOR"},
    },
)
async def list_bookmarks(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str | None, Query(description="제목·요약·저장 태그 검색어")] = None,
    topic_id: Annotated[int | None, Query(alias="topicId", gt=0)] = None,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SavedBookmarksResponse:
    return await bookmark_service.get_saved_bookmarks(
        db, current_user.user_id, topic_id=topic_id, q=q, cursor=cursor, limit=limit
    )
