from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context, require_csrf, require_onboarding
from app.core.exceptions import ApiError
from app.core.security import delete_session_cookie
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.onboarding import NICKNAME_TAKEN, NicknameCheckResponse
from app.schemas.profile import ProfilePatchRequest, ProfileResponse
from app.schemas.withdrawal import WithdrawalResponse
from app.services.onboarding_service import check_nickname, reject_taken_nickname
from app.services.withdrawal_service import withdraw_now

router = APIRouter(prefix="/users/me", tags=["사용자"])


def profile_response(user: UserAccount) -> ProfileResponse:
    return ProfileResponse(
        user_id=user.user_id,
        nickname=user.nickname,
        email=user.email,
        profile_image_id=None,
        profile_image_url=None,
        account_type=user.account_type,
    )


@router.get(
    "",
    response_model=ProfileResponse,
    summary="내 프로필 조회",
    description="완료된 로그인 세션의 본인 프로필만 반환합니다. 온보딩 전에는 403입니다. 생년월일(birthDate)은 응답에 포함하지 않습니다.",
    responses=COMMON_ERRORS,
)
async def get_profile(
    user: Annotated[UserAccount, Depends(require_onboarding)],
) -> ProfileResponse:
    return profile_response(user)


@router.get(
    "/nickname",
    response_model=NicknameCheckResponse,
    summary="닉네임 바꾸기 전 사용 가능 여부 확인",
    description="온보딩을 마친 회원이 마이페이지에서 닉네임을 바꾸기 전에 확인합니다. GET /onboarding/nickname 과 같은 규칙(앞뒤 공백을 지운 2~20자 한글·영문·숫자, 띄어쓰기·특수문자 제외)과 중복 검사(온보딩을 마친 다른 회원, 영문 대소문자 구분 없이)입니다. 규칙 위반은 available=false·reason=invalid, 중복은 reason=taken이고, 본인의 지금 닉네임은 사용 가능입니다. 확인만 하고 닉네임을 맡아 두지 않으므로 PATCH /users/me 에서 다시 검사합니다.",
    responses=COMMON_ERRORS,
)
async def check_profile_nickname(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    nickname: Annotated[str, Query(min_length=1, max_length=100)],
) -> NicknameCheckResponse:
    return await check_nickname(db, nickname, user_id=user.user_id)


@router.patch(
    "",
    response_model=ProfileResponse,
    summary="내 프로필 정정",
    description="nickname과 profileImageId만 수정합니다. nickname은 온보딩과 같은 규칙(앞뒤 공백을 지운 2~20자 한글·영문·숫자, 띄어쓰기·특수문자 제외)이며, 온보딩을 마친 다른 회원이 이미 쓰면 409 NICKNAME_TAKEN입니다. 본인의 지금 닉네임은 그대로 저장할 수 있습니다. 이미지 업로드 API가 아직 없으므로 profileImageId는 null만 사용할 수 있습니다. birthDate는 null을 포함해 전송하면 422이며 응답에도 포함하지 않습니다. X-CSRF-Token이 필요합니다.",
    responses={
        **COMMON_ERRORS,
        404: {"description": "프로필 이미지 없음", "content": {"application/json": {"example": {"code": "IMAGE_NOT_FOUND", "message": "프로필 이미지 업로드 기능이 아직 제공되지 않습니다."}}}},
        409: {"description": "닉네임 중복(NICKNAME_TAKEN)", "content": {"application/json": {"example": {"code": "NICKNAME_TAKEN", "message": NICKNAME_TAKEN}}}},
    },
)
async def patch_profile(
    payload: ProfilePatchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> ProfileResponse:
    if payload.profile_image_id is not None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "프로필 이미지 업로드 기능이 아직 제공되지 않습니다.")
    if "nickname" in payload.model_fields_set:
        assert payload.nickname is not None
        await reject_taken_nickname(db, payload.nickname, user_id=user.user_id)
        user.nickname = payload.nickname
    if "profile_image_id" in payload.model_fields_set:
        user.profile_image_url = None
    await db.commit()
    return profile_response(user)


@router.delete(
    "",
    status_code=202,
    response_model=WithdrawalResponse,
    summary="회원 탈퇴 (지금은 즉시 삭제만)",
    description="API-009. mode=immediate 이면 회원과 연결 데이터(관심 분야 · 온보딩 답 · 좋아요 · 저장 · O/X · 행동 기록 · 알림 · 챗봇 대화 · 추천 결과 · 문의)를 바로 지우고(콘텐츠 신고는 글 검토용으로 남기고 신고자만 비움), 지금 세션을 끝내고 쿠키를 지웁니다. 다른 기기의 세션도 회원이 없어져 더는 인증되지 않습니다. 명세의 소셜 재인증과 mode=grace(3개월 유예 · API-010 복구)는 아직 없으며 grace 는 422 WITHDRAWAL_MODE_UNSUPPORTED 입니다. 같은 소셜 계정으로 다시 로그인하면 새 회원으로 온보딩부터 시작합니다. 온보딩을 마친 회원만 쓸 수 있고 X-CSRF-Token이 필요합니다.",
    responses={
        **COMMON_ERRORS,
        409: {"model": ErrorResponse, "description": "크리에이터 채널이 연결된 계정(WITHDRAWAL_BLOCKED)", "content": {"application/json": {"example": {"code": "WITHDRAWAL_BLOCKED", "message": "연결된 크리에이터 채널이 있어 바로 탈퇴할 수 없습니다."}}}},
        422: {"model": ErrorResponse, "description": "mode 없음 · 잘못된 값(VALIDATION_ERROR) 또는 아직 없는 유예 탈퇴(WITHDRAWAL_MODE_UNSUPPORTED)", "content": {"application/json": {"example": {"code": "WITHDRAWAL_MODE_UNSUPPORTED", "message": "유예 탈퇴는 아직 지원하지 않습니다. mode=immediate 로 요청해 주세요."}}}},
    },
)
async def withdraw(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    context: Annotated[AuthContext, Depends(get_auth_context)],
    _: Annotated[None, Depends(require_csrf)],
    mode: Annotated[Literal["grace", "immediate"], Query(description="immediate 만 지원합니다. grace(3개월 유예)는 후속입니다.")],
) -> WithdrawalResponse:
    if mode != "immediate":
        raise ApiError(422, "WITHDRAWAL_MODE_UNSUPPORTED", "유예 탈퇴는 아직 지원하지 않습니다. mode=immediate 로 요청해 주세요.")
    deleted_at = await withdraw_now(db, user_id=user.user_id, session=context.session, store=context.session_store)
    delete_session_cookie(response)
    return WithdrawalResponse(status="withdrawn", mode="immediate", scheduled_delete_at=deleted_at)
