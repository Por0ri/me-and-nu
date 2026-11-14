"""Prepare validated input only. No write router, ORM mutation or worker call.

Future HTTP callers must reuse require_admin and separately enforce write
permission, CSRF, concurrency and transactional audit policy before persistence.
Read permission alone does not authorize saving this proposal.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.schemas.admin_content import AdminContentDetail
from app.schemas.admin_content_input import AdminContentCreateRequest, AdminContentUpdateRequest
from app.services.admin_content_service import get_content


@dataclass(frozen=True)
class AdminContentInputPlan:
    operation: Literal["create", "update"]
    content_id: int | None
    author_input: AdminContentCreateRequest | AdminContentUpdateRequest
    original: AdminContentDetail | None = None


def prepare_create(request: AdminContentCreateRequest) -> AdminContentInputPlan:
    # No Content/Draft is constructed: required topic/owner and initial-state
    # policies are unresolved. In particular, never inherit Content.active.
    validated = AdminContentCreateRequest.model_validate(request.model_dump())
    return AdminContentInputPlan(operation="create", content_id=None, author_input=validated)


async def prepare_update(db: AsyncSession, content_id: int, request: AdminContentUpdateRequest) -> AdminContentInputPlan:
    if type(content_id) is not int or not 1 <= content_id <= 9_007_199_254_740_991:
        raise ApiError(422, "VALIDATION_ERROR", "콘텐츠 ID를 확인해 주세요.")
    validated = AdminContentUpdateRequest.model_validate(request.model_dump())
    # Reuses the existing read repository/service and its validated stored enums.
    original = await get_content(db, content_id)
    # This is author intent, NOT a source replacement/upsert instruction. Existing
    # provenance, roles, citation order, slides and all states remain untouched.
    return AdminContentInputPlan(operation="update", content_id=content_id, author_input=validated, original=original)
