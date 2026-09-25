from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.catalog import TopicsResponse
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.feed import FeedResponse
from app.services import catalog_service, feed_service

router = APIRouter(prefix="/topics")


@router.get(
    "",
    response_model=TopicsResponse,
    tags=["Topic"],
    summary="Topic 목록과 이름 검색",
    description="로그인 후 온보딩 전에도 사용 가능합니다. 응답의 ID를 온보딩과 피드 요청에 사용합니다.",
    responses=COMMON_ERRORS,
)
async def list_topics(
    _: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str | None, Query(description="Topic 이름 검색어")] = None,
) -> TopicsResponse:
    return await catalog_service.topics(db, q)


@router.get(
    "/{topicId}/feed",
    response_model=FeedResponse,
    tags=["피드"],
    summary="내 Topic의 콘텐츠 피드 조회",
    description="활성 Topic의 공개 콘텐츠를 최신순으로 반환합니다. 추천 식별자와 사유는 로컬 개발 범위에서 null입니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def get_topic_feed(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0, description="현재 내 Topic ID")],
    subtopic_id: Annotated[int | None, Query(alias="subtopicId", gt=0, description="필터할 Subtopic ID")] = None,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="카드 수, 기본 20")] = 20,
) -> FeedResponse:
    return await feed_service.get_feed(
        db, current_user.user_id, topic_id, subtopic_id=subtopic_id, cursor=cursor, limit=limit
    )
