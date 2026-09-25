from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.feed import ContentDetail
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.reaction import (
    BookmarkResponse,
    LikeResponse,
    PreferenceRequest,
    PreferenceResponse,
    TopicAction,
)
from app.services import feed_service, reaction_service

router = APIRouter(prefix="/contents")


@router.get(
    "/{contentId}",
    response_model=ContentDetail,
    tags=["콘텐츠"],
    summary="공개 콘텐츠 상세 조회",
    description="내 활성 Topic의 공개 콘텐츠와 본인 반응을 반환합니다. Agent Draft와 내부 판정 정보는 반환하지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND 또는 TOPIC_NOT_FOUND"}},
)
async def get_content(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0, description="현재 활성 Topic ID")],
) -> ContentDetail:
    return await feed_service.get_content_detail(db, current_user.user_id, topic_id, content_id)


@router.put(
    "/{contentId}/preference",
    response_model=PreferenceResponse,
    tags=["O/X 선호도"],
    summary="콘텐츠 O/X 선호도 설정",
    description="positive는 O, negative는 X로 저장합니다. 반복 PUT은 취소가 아닙니다. 세션과 CSRF 토큰이 필요합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND"}},
)
async def put_preference(
    request: Annotated[PreferenceRequest, Body(openapi_examples={"positive": {"summary": "O 선호", "value": {"topicId": 1, "value": "positive"}}})],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
) -> PreferenceResponse:
    return await reaction_service.set_preference(
        db, current_user.user_id, request.topic_id, content_id, request.value
    )


@router.delete(
    "/{contentId}/preference",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["O/X 선호도"],
    summary="콘텐츠 O/X 선호도 취소",
    description="좋아요와 북마크 상태는 유지합니다. 이미 취소한 상태도 204를 반환합니다.",
    responses=COMMON_ERRORS,
)
async def delete_preference(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0)],
) -> Response:
    await reaction_service.clear_preference(db, current_user.user_id, topic_id, content_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{contentId}/like",
    response_model=LikeResponse,
    tags=["좋아요"],
    summary="콘텐츠 좋아요 설정",
    description="본인 활성 Topic의 콘텐츠에 좋아요를 설정합니다. 반복 PUT은 같은 상태를 유지합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND"}},
)
async def put_like(
    request: Annotated[TopicAction, Body(openapi_examples={"topic": {"summary": "Topic 지정", "value": {"topicId": 1}}})],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
) -> LikeResponse:
    return await reaction_service.set_like(db, current_user.user_id, request.topic_id, content_id)


@router.delete(
    "/{contentId}/like",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["좋아요"],
    summary="콘텐츠 좋아요 취소",
    description="O/X와 북마크 상태는 유지합니다. 이미 취소한 상태도 204를 반환합니다.",
    responses=COMMON_ERRORS,
)
async def delete_like(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0)],
) -> Response:
    await reaction_service.clear_like(db, current_user.user_id, topic_id, content_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{contentId}/bookmark",
    response_model=BookmarkResponse,
    tags=["북마크"],
    summary="콘텐츠 북마크 저장",
    description="현재 Topic에 북마크를 저장하고 Subtopic 태그를 자동 부여합니다. 반복 PUT은 중복을 만들지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND"}},
)
async def put_bookmark(
    request: Annotated[TopicAction, Body(openapi_examples={"topic": {"summary": "Topic 지정", "value": {"topicId": 1}}})],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
) -> BookmarkResponse:
    return await reaction_service.set_bookmark(db, current_user.user_id, request.topic_id, content_id)


@router.delete(
    "/{contentId}/bookmark",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["북마크"],
    summary="콘텐츠 북마크 취소",
    description="원본 콘텐츠가 비공개로 바뀌어도 본인 북마크는 취소할 수 있습니다. 이미 취소한 상태도 204입니다.",
    responses=COMMON_ERRORS,
)
async def delete_bookmark(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    content_id: Annotated[int, Path(alias="contentId", gt=0)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0)],
) -> Response:
    await reaction_service.clear_bookmark(db, current_user.user_id, topic_id, content_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
