from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.schemas.onboarding import NICKNAME_TAKEN
from app.schemas.profile import ProfilePatchRequest, ProfileResponse
from app.services.onboarding_service import reject_taken_nickname

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
