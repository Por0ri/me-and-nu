from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_csrf, require_pending_onboarding
from app.core.security import set_session_cookie
from app.db.session import get_db
from app.schemas.common import COMMON_ERRORS
from app.schemas.onboarding import (
    OnboardingRequest, OnboardingResponse, OnboardingWorksResponse,
    PreferenceOptionsRequest, PreferenceOptionsResponse,
)
from app.services import onboarding_catalog_service
from app.services.onboarding_service import complete_onboarding

router = APIRouter(prefix="/onboarding", tags=["온보딩"])


@router.get(
    "/works",
    response_model=OnboardingWorksResponse,
    summary="온보딩 작품 검색",
    description="onboarding_pending 세션으로 이름·별칭을 검색합니다. 빈 검색어는 빈 목록입니다. 작품 ID 오름차순 cursor 페이지이며 limit은 선택 개수와 별개입니다. 검색된 활성 작품은 모두 selectable=true입니다. 검색 결과를 사용자 취향으로 저장하지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}, 409: {"description": "온보딩 완료 또는 세션 상태 충돌"}},
)
async def onboarding_works(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_pending_onboarding)],
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> OnboardingWorksResponse:
    return await onboarding_catalog_service.works(db, topic_id, q=q, cursor=cursor, limit=limit)


@router.post(
    "/preference-options",
    response_model=PreferenceOptionsResponse,
    summary="장르·소재 칩 목록과 선택 작품 힌트 조회",
    description="onboarding_pending 세션과 X-CSRF-Token이 필요합니다. 같은 Topic의 활성 장르·소재 칩을 모두 반환합니다. 선택 작품 1~5개에 연결된 칩은 sourceEntityIds에 출처 작품 ID가 들어 있어 힌트로 쓸 수 있습니다. 활성 칩이면 무엇이든 선택할 수 있고 장르와 소재 각각 최소 1개(총 최소 minSelections=2개), 종류별 최대 maxSelectionsPerKind(7)개이며 완료 요청에서 검사합니다. 조회는 선택·동의·온보딩 답변을 저장하지 않습니다.",
    responses={**COMMON_ERRORS, 404: {"description": "Topic 없음"}, 409: {"description": "온보딩 완료 또는 세션 상태 충돌"}},
)
async def onboarding_preference_options(
    payload: PreferenceOptionsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_pending_onboarding)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> PreferenceOptionsResponse:
    return await onboarding_catalog_service.preference_options(db, payload.topic_id, payload.entity_ids)


@router.post(
    "/complete",
    status_code=201,
    response_model=OnboardingResponse,
    summary="회원 온보딩 일괄 완료",
    description="onboarding_pending 세션과 X-CSRF-Token이 필요합니다. 닉네임·동의·최초 Topic·답변·알림 설정을 하나의 트랜잭션으로 저장하고 세션을 교체합니다. answers.version=2는 favoriteEntityIds 1~5개, preferenceTagIds 장르와 소재 각각 최소 1개(총 최소 2개, 종류별 최대 7개), consumptionType 1개를 받습니다. 현재 카탈로그로 재검증하며 Q2는 같은 Topic의 활성 장르·소재 칩이어야 합니다. 버전 없는 기존 answers:{q1,q2}는 v1로 유지하고 두 형식의 혼합은 거절합니다. 생년월일 정책은 별도 결정 예정이며 이번 변경은 기존 birthDate 거절 계약을 유지합니다. birthDate, profileImageId, subtopicIds는 받지 않고 세부 관심사를 자동 구독하지 않습니다. 완료 후 GET /auth/session을 다시 호출하세요.",
    responses={
        **COMMON_ERRORS,
        201: {"description": "온보딩 완료"},
        404: {"description": "Topic 없음", "content": {"application/json": {"example": {"code": "TOPIC_NOT_FOUND", "message": "Topic을 찾을 수 없습니다."}}}},
        409: {"description": "중복 온보딩 또는 정책 버전 충돌", "content": {"application/json": {"example": {"code": "POLICY_VERSION_CONFLICT", "message": "정책 버전이 변경되었습니다. 정책을 다시 조회해 주세요."}}}},
    },
)
async def onboarding(
    payload: OnboardingRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_pending_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> OnboardingResponse:
    result, raw_token = await complete_onboarding(db, context=context, payload=payload)
    set_session_cookie(response, raw_token)
    return result
