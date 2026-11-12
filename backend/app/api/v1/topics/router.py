from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.catalog import TopicsResponse
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.feed import FeedResponse, FeedSearchResponse, WorkSuggestionsResponse
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
    description="활성 Topic의 공개 콘텐츠를 오늘(없으면 어제) 추천 순서로 반환하고, 추천이 없으면 최신순을 사용합니다. 추천 카드는 식별자와 사유를 포함합니다. nextCursor는 그대로 전달하며 INVALID_CURSOR이면 첫 페이지부터 다시 조회하세요.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def get_topic_feed(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0, description="현재 내 Topic ID")],
    subtopic_id: Annotated[int | None, Query(alias="subtopicId", gt=0, description="필터할 Subtopic ID")] = None,
    cursor: Annotated[str | None, Query(max_length=feed_service.MAX_FEED_CURSOR_LENGTH, description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="카드 수, 기본 20")] = 20,
) -> FeedResponse:
    return await feed_service.get_feed(
        db, current_user.user_id, topic_id, subtopic_id=subtopic_id, cursor=cursor, limit=limit
    )


@router.get(
    "/{topicId}/search",
    response_model=FeedSearchResponse,
    tags=["피드"],
    summary="작품 이름으로 콘텐츠 검색",
    description="작품 이름이나 별칭에 검색어가 들어간 작품(띄어쓰기 무시)의 공개 콘텐츠를 오늘(없으면 어제) 추천 순서, 그다음 최신순으로 최대 20개 반환합니다. workId를 주면(검색어 아래 추천 작품을 고른 경우) 그 작품의 콘텐츠만 반환합니다. works는 맞는 작품의 정식 이름입니다(글이 없어도 채움). 홈은 검색 결과 화면에 이 카드만 보여 주고 노출 기록을 남기지 않습니다. 검색 카드는 recommendationId 없이 recommendationReason에 검색 결과 문구를 담습니다. 실행된 검색은 서버에 기록합니다(저장에 실패해도 결과는 반환).",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def search_topic_contents(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0, description="현재 내 Topic ID")],
    q: Annotated[str, Query(min_length=1, max_length=50, description="작품 이름 검색어")],
    work_id: Annotated[int | None, Query(alias="workId", gt=0, le=9223372036854775807, description="고른 작품 ID (search/works 의 entityId)")] = None,
) -> FeedSearchResponse:
    return await feed_service.search_feed(db, current_user.user_id, topic_id, q=q, work_id=work_id)


@router.get(
    "/{topicId}/search/works",
    response_model=WorkSuggestionsResponse,
    tags=["피드"],
    summary="검색어 아래에 띄울 작품",
    description="홈 검색창에 쓰는 동안 이름이나 별칭에 검색어가 들어간 작품(띄어쓰기 무시)을 온보딩 Q1 작품 검색과 같은 규칙·순서로 최대 limit개(기본 3) 반환합니다. 글이 없는 작품도 포함합니다. 고른 작품의 entityId를 검색의 workId로 씁니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def suggest_topic_works(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0, description="현재 내 Topic ID")],
    q: Annotated[str, Query(min_length=1, max_length=50, description="작품 이름 검색어")],
    limit: Annotated[int, Query(ge=1, le=10, description="작품 수, 기본 3")] = feed_service.SUGGEST_WORKS,
) -> WorkSuggestionsResponse:
    return await feed_service.suggest_works(db, current_user.user_id, topic_id, q=q, limit=limit)
