from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.catalog import SubtopicItem, SubtopicsResponse
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import catalog_service

router = APIRouter()


@router.get(
    "/topics/{topicId}/subtopics",
    response_model=SubtopicsResponse,
    tags=["Subtopic"],
    summary="Topic의 Subtopic 목록과 검색",
    description="로그인 후 온보딩 전에도 조회할 수 있습니다. 같은 필터에서만 nextCursor를 재사용하세요.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "TOPIC_NOT_FOUND"}},
)
async def list_subtopics(
    _: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_id: Annotated[int, Path(alias="topicId", gt=0)],
    q: Annotated[str | None, Query(description="Subtopic 이름 검색어")] = None,
    parent_subtopic_id: Annotated[int | None, Query(alias="parentSubtopicId", gt=0)] = None,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SubtopicsResponse:
    return await catalog_service.subtopics(
        db, topic_id, q=q, parent_subtopic_id=parent_subtopic_id, cursor=cursor, limit=limit
    )


@router.get(
    "/subtopics/{subtopicId}",
    response_model=SubtopicItem,
    tags=["Subtopic"],
    summary="Subtopic 상세 조회",
    description="온보딩에서 선택할 Subtopic의 소속 Topic과 이름을 확인합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "NOT_FOUND"}},
)
async def get_subtopic(
    _: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    subtopic_id: Annotated[int, Path(alias="subtopicId", gt=0)],
) -> SubtopicItem:
    return await catalog_service.subtopic_detail(db, subtopic_id)
