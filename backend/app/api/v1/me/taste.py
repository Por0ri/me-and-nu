"""My Page taste edit (temporary, 2026-11 user test): the onboarding Q1~Q3 screens for an active user.

The onboarding search/chip APIs only accept onboarding_pending sessions, so the edit
screens use these copies. Remove this router together with the temporary My Page screen.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.schemas.onboarding import OnboardingWorksResponse, PreferenceOptionsRequest, PreferenceOptionsResponse
from app.schemas.taste import TasteHintsResponse, TasteResponse, TasteUpdateRequest
from app.services import onboarding_catalog_service, taste_hint_service, taste_service

router = APIRouter(prefix="/me/taste", tags=["내 취향 수정 (임시)"])


@router.get(
    "",
    response_model=TasteResponse,
    summary="내 온보딩 취향 조회",
    description="온보딩을 마친 회원의 Q1 작품 · Q2 장르·소재 칩 · Q3 소비 유형을 돌려줍니다. 마이페이지 취향 수정 화면의 처음 값입니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}},
)
async def my_taste(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
) -> TasteResponse:
    return await taste_service.get_taste(db, current_user.user_id, topic_id)


@router.get(
    "/hints",
    response_model=TasteHintsResponse,
    summary="취향 수정 힌트 (행동 기록)",
    description="최근 30일 동안 반응한 글(카드 넘김 · 본문 읽기 · 좋아요 · 저장 · 더볼래요. 보기만 한 글과 그만볼래요 한 글은 빼고)로 "
                "Q1 작품 상위 3개와 작품 중 비율, Q2 장르 · 소재 상위 3개씩, Q3 답 6개 모두의 비율(합 100)을 돌려줍니다. "
                "점수는 추천 계산과 같은 세기입니다. 반응한 글이 하나도 없으면 enough=false 이고 목록은 비어 있습니다. "
                "1편만 있어도 힌트를 주므로 비율이 100%·0%로 나올 수 있습니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}},
)
async def my_taste_hints(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
) -> TasteHintsResponse:
    return await taste_hint_service.get_taste_hints(db, current_user.user_id, topic_id)


@router.get(
    "/works",
    response_model=OnboardingWorksResponse,
    summary="취향 수정용 작품 검색",
    description="GET /onboarding/works 와 같은 검색을 온보딩을 마친 회원에게 제공합니다. 검색은 취향을 저장하지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}},
)
async def taste_works(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UserAccount, Depends(require_onboarding)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> OnboardingWorksResponse:
    return await onboarding_catalog_service.works(db, topic_id, q=q, cursor=cursor, limit=limit)


@router.post(
    "/preference-options",
    response_model=PreferenceOptionsResponse,
    summary="취향 수정용 장르·소재 칩 목록",
    description="POST /onboarding/preference-options 와 같은 칩 목록을 온보딩을 마친 회원에게 제공합니다. X-CSRF-Token 이 필요하고 저장하지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}},
)
async def taste_preference_options(
    payload: PreferenceOptionsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UserAccount, Depends(require_onboarding)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> PreferenceOptionsResponse:
    return await onboarding_catalog_service.preference_options(db, payload.topic_id, payload.entity_ids)


@router.put(
    "",
    response_model=TasteResponse,
    summary="내 온보딩 취향 바꾸기",
    description=(
        "X-CSRF-Token 이 필요합니다. 온보딩 완료와 같은 규칙(작품 1~5개, 장르·소재 각각 1~7개, 소비 유형 1개)으로 검사한 뒤 "
        "답을 통째로 바꾸고, 이 회원의 오늘·어제 추천을 지워 추천 계산기가 몇 초 안에 새로 만들게 합니다. 행동 기록은 지우지 않습니다."
    ),
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}},
)
async def update_my_taste(
    payload: TasteUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> TasteResponse:
    return await taste_service.update_taste(db, current_user, payload.topic_id, payload.answers)
