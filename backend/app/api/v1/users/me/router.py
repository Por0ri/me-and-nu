from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.schemas.profile import ProfilePatchRequest, ProfileResponse

router = APIRouter(prefix="/users/me", tags=["사용자"])


def profile_response(user: UserAccount) -> ProfileResponse:
    return ProfileResponse(
        user_id=user.user_id,
        nickname=user.nickname,
        birth_date=user.birth_date,
        email=user.email,
        profile_image_id=None,
        profile_image_url=None,
        account_type=user.signup_channel,
    )


@router.get(
    "",
    response_model=ProfileResponse,
    summary="내 프로필 조회",
    description="완료된 로그인 세션의 본인 프로필만 반환합니다. 온보딩 전에는 403입니다.",
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
    description="nickname과 profileImageId만 수정합니다. 이미지 업로드 API가 아직 없으므로 profileImageId는 null만 사용할 수 있습니다. X-CSRF-Token이 필요합니다.",
    responses={**COMMON_ERRORS, 404: {"description": "프로필 이미지 없음", "content": {"application/json": {"example": {"code": "IMAGE_NOT_FOUND", "message": "프로필 이미지 업로드 기능이 아직 제공되지 않습니다."}}}}},
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
        user.nickname = payload.nickname
    if "profile_image_id" in payload.model_fields_set:
        user.profile_image_url = None
    await db.commit()
    return profile_response(user)
