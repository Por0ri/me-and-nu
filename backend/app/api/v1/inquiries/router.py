"""마이페이지 '문의 및 신고' 화면의 문의 접수 (명세 V1.1 에 없는 새 API)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Security
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding, session_cookie_scheme
from app.api.session_store import get_session_store
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.repositories.auth_session_repository import find_auth_session
from app.repositories.redis_session_store import RedisSessionStore
from app.repositories.user_repository import get_user_by_id
from app.schemas.report import InquiryCreateRequest, InquiryResponse, OwnInquiryDetail, OwnInquiryPage
from app.services.report_service import create_inquiry
from app.services import inquiry_read_service

router = APIRouter(prefix="/inquiries", tags=["신고·문의"])


@router.post(
    "",
    status_code=201,
    response_model=InquiryResponse,
    summary="마이페이지 문의 접수",
    description="마이페이지 '문의 및 신고' 화면용입니다. inquiryType 은 service(서비스 문의) · bug(버그/오류 신고)이고, detail 은 앞뒤 공백을 지운 1~500자로 꼭 필요합니다. 특정 글 신고는 POST /reports 를 씁니다. 접수 내역과 답변은 GET /inquiries에서 확인합니다. 탈퇴하면 문의와 답변도 같이 지워집니다. 온보딩을 마친 회원만 쓸 수 있고 X-CSRF-Token이 필요합니다.",
    responses=COMMON_ERRORS,
)
async def submit_inquiry(
    payload: InquiryCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> InquiryResponse:
    return await create_inquiry(db, user_id=user.user_id, payload=payload)


async def require_inquiry_reader(
    request: Request,
    raw_token: Annotated[str | None, Security(session_cookie_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[RedisSessionStore | None, Depends(get_session_store)],
) -> UserAccount:
    """Never expose private history through the development authentication bypass."""
    if not raw_token:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    try:
        session = await find_auth_session(db, raw_token, store=store)
        user = await get_user_by_id(db, session.user_id) if session and session.user_id else None
    except Exception:
        raise ApiError(503, "INQUIRY_AUTH_UNAVAILABLE", "로그인 상태를 확인할 수 없습니다.") from None
    if (session is None or user is None or user.account_status != "active" or user.withdrawn_at is not None
            or session.session_state not in {"active", "onboarding_pending"}):
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    if session.session_state != "active" or user.onboarding_completed_at is None:
        raise ApiError(403, "ONBOARDING_REQUIRED", "온보딩을 완료해 주세요.")
    expected_actors = request.headers.getlist("X-Inquiry-Actor-User-Id")
    if expected_actors and (len(expected_actors) != 1 or expected_actors[0] != str(user.user_id)):
        # Optional read-scope binding is not an authorization claim. Ownership
        # still comes from the current session, never this header.
        raise ApiError(403, "INQUIRY_SESSION_CHANGED", "로그인 계정이 변경되었습니다. 다시 확인해 주세요.")
    return user


@router.get("", response_model=OwnInquiryPage, summary="내 문의 내역", responses=COMMON_ERRORS)
async def own_inquiries(
    db: Annotated[AsyncSession, Depends(get_db)], user: Annotated[UserAccount, Depends(require_inquiry_reader)],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=50)] = 10,
):
    try:
        return await inquiry_read_service.list_own_inquiries(db, user_id=user.user_id, page=page, page_size=page_size)
    except SQLAlchemyError:
        raise ApiError(503, "INQUIRY_READ_UNAVAILABLE", "문의 내역을 불러올 수 없습니다. 다시 시도해 주세요.") from None


@router.get("/{inquiryId}", response_model=OwnInquiryDetail, summary="내 문의와 답변", responses=COMMON_ERRORS)
async def own_inquiry(
    inquiryId: Annotated[int, Path(gt=0, le=9223372036854775807)],
    db: Annotated[AsyncSession, Depends(get_db)], user: Annotated[UserAccount, Depends(require_inquiry_reader)],
):
    try:
        return await inquiry_read_service.get_own_inquiry(db, user_id=user.user_id, inquiry_id=inquiryId)
    except SQLAlchemyError:
        raise ApiError(503, "INQUIRY_READ_UNAVAILABLE", "문의 내역을 불러올 수 없습니다. 다시 시도해 주세요.") from None
