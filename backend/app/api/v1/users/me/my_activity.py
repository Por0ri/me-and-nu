"""마이페이지 '내 활동' 탭: 좋아요한 콘텐츠 · 최근 본 콘텐츠 (명세 V1.1 에 없는 새 API)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.my_activity import LikedContentsResponse, RecentViewsResponse
from app.services import my_activity_service

router = APIRouter(prefix="/users/me", tags=["내 활동"])

LIST_ERRORS = {
    **COMMON_ERRORS,
    404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"},
    422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 또는 INVALID_CURSOR"},
}


@router.get(
    "/likes",
    response_model=LikedContentsResponse,
    summary="좋아요한 콘텐츠 목록",
    description="본인이 지금 좋아요를 누르고 있는 글을 좋아요 누른 최신순으로 돌려줍니다. 취소한 글과 숨김 · 삭제되었거나 출처가 없는 글은 뺍니다. 카드 칸은 저장 목록(GET /users/me/bookmarks)과 같고 likedAt 이 더 있습니다. topicId 를 주면 그 분야만 봅니다. 같은 회원 · 같은 필터에서만 nextCursor 를 다시 쓸 수 있습니다.",
    responses=LIST_ERRORS,
)
async def list_liked_contents(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int | None, Query(alias="topicId", gt=0)] = None,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LikedContentsResponse:
    return await my_activity_service.get_liked_contents(
        db, current_user.user_id, topic_id=topic_id, cursor=cursor, limit=limit
    )


@router.get(
    "/recent-views",
    response_model=RecentViewsResponse,
    summary="최근 본 콘텐츠 목록",
    description="최근 30일 안에 상세 화면을 열었거나 홈 카드를 마지막 장까지 넘긴 글을, 글마다 마지막으로 본 시각(viewedAt) 최신순으로 한 번씩 돌려줍니다. 피드에서 보이기만 한 글 · 카드를 덜 넘긴 글 · 숨김 · 삭제된 글은 뺍니다. 카드 칸은 저장 목록과 같습니다. topicId 를 주면 그 분야만 봅니다. 같은 회원 · 같은 필터에서만 nextCursor 를 다시 쓸 수 있습니다.",
    responses=LIST_ERRORS,
)
async def list_recent_views(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int | None, Query(alias="topicId", gt=0)] = None,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RecentViewsResponse:
    return await my_activity_service.get_recent_views(
        db, current_user.user_id, topic_id=topic_id, cursor=cursor, limit=limit
    )
