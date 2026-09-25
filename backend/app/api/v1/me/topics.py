from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.catalog import (
    CreateMyTopicRequest,
    MySubtopicsResponse,
    MyTopicResponse,
    MyTopicsResponse,
    SubtopicSubscriptionResponse,
)
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import catalog_service

router = APIRouter(prefix="/me/topics", tags=["내 관심사"])


@router.get(
    "",
    response_model=MyTopicsResponse,
    summary="내 Topic 목록 조회",
    description="현재 사용자의 활성 Topic을 반환합니다. 삭제 상태는 includeDeleted=true에서 포함합니다.",
    responses=COMMON_ERRORS,
)
async def list_my_topics(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> MyTopicsResponse:
    return await catalog_service.my_topics(db, current_user.user_id, include_deleted)


@router.post(
    "",
    response_model=MyTopicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="내 Topic 추가",
    description="Topic과 그 소속 Subtopic을 선택합니다. 중복 활성화는 409입니다. CSRF 토큰이 필요합니다.",
    responses={**COMMON_ERRORS, 409: {"model": ErrorResponse, "description": "TOPIC_ALREADY_ACTIVE"}},
)
async def create_my_topic(
    request: CreateMyTopicRequest,
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MyTopicResponse:
    return await catalog_service.create_my_topic(db, current_user.user_id, request)


@router.get(
    "/{topicId}/subtopics",
    response_model=MySubtopicsResponse,
    summary="내 Topic의 구독 Subtopic 조회",
    description="구독 시각 순으로 반환합니다. orderingBasis에 정렬 근거를 표시합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def get_my_subtopics(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0)],
) -> MySubtopicsResponse:
    return await catalog_service.my_subtopics(db, current_user.user_id, topic_id)


@router.put(
    "/{topicId}/subtopics/{subtopicId}",
    response_model=SubtopicSubscriptionResponse,
    summary="Subtopic 구독 설정",
    description="반복 호출해도 하나의 구독만 유지합니다. CSRF 토큰이 필요합니다.",
    responses=COMMON_ERRORS,
)
async def set_subtopic(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0)],
    subtopic_id: Annotated[int, Path(alias="subtopicId", gt=0)],
) -> SubtopicSubscriptionResponse:
    return await catalog_service.set_subtopic_subscription(
        db, current_user.user_id, topic_id, subtopic_id
    )


@router.delete(
    "/{topicId}/subtopics/{subtopicId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Subtopic 구독 해제",
    description="이미 해제된 구독에도 204를 반환합니다. CSRF 토큰이 필요합니다.",
    responses=COMMON_ERRORS,
)
async def remove_subtopic(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0)],
    subtopic_id: Annotated[int, Path(alias="subtopicId", gt=0)],
) -> Response:
    await catalog_service.clear_subtopic_subscription(
        db, current_user.user_id, topic_id, subtopic_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
