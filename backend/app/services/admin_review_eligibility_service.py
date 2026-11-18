"""Read-only UI hint; every review POST revalidates permission and locked state."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Content, Draft
from app.schemas.admin_content import AdminContentReviewEligibility
from app.services.admin_review_permission_provider import AdminReviewPermissionProvider


async def get_review_eligibility(
    db: AsyncSession, *, user_id: int, content_id: int, permissions: AdminReviewPermissionProvider,
) -> AdminContentReviewEligibility:
    try:
        allowed = await permissions.can_review_content(db, user_id=user_id, content_id=content_id)
    except Exception:
        raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 쓰기 권한을 확인할 수 없습니다.") from None
    if allowed is not True:
        return AdminContentReviewEligibility(disabled_reason="permission_denied")
    result = await db.execute(select(
        Content.status, Content.judgment_status, Content.deleted_at,
        Draft.status.label("draft_status"),
    ).outerjoin(Draft, Content.source_draft_id == Draft.draft_id).where(Content.content_id == content_id))
    row = result.mappings().one_or_none()
    if row is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
    if row["deleted_at"] is not None:
        return AdminContentReviewEligibility(disabled_reason="deleted")
    if row["status"] not in {"active", "pending_review"} or row["judgment_status"] != "needs_review":
        return AdminContentReviewEligibility(disabled_reason="content_not_reviewable")
    if row["draft_status"] not in {"checking", "approved"}:
        return AdminContentReviewEligibility(disabled_reason="draft_not_reviewable")
    return AdminContentReviewEligibility(allowed_actions=["approve", "reject"], disabled_reason=None)
