"""Own one fresh transaction: grant lock -> content -> draft -> audit -> commit.

Only call with a verified, non-bypass session actor and CSRF-checked request.
HTTP auth reads use a different session so their autobegin cannot commit here.
"""

from datetime import datetime, timezone

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import admin_content_review_repository as repo
from app.schemas.admin_content_review import AdminContentReviewRequest, AdminContentReviewResponse
from app.services.admin_review_permission_provider import AdminReviewGrant, AdminReviewPermissionProvider

PREVIEW_KEYS = frozenset({"preview", "local_test_preview", "checking_test_preview"})


def _utc(value):
    # SQLite fixtures return naive timestamps; production columns are timestamptz.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _snapshot(row):
    return {key: _utc(value).isoformat() if isinstance(value, datetime) else value for key, value in row.items()}


def _without_preview(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ApiError(409, "REVIEW_STATE_CONFLICT", "판정 메타데이터를 확인해 주세요.")
    return {key: item for key, item in value.items() if key not in PREVIEW_KEYS}


def _conflict():
    return ApiError(409, "REVIEW_STATE_CONFLICT", "콘텐츠 상태가 변경되었거나 검수 가능한 상태가 아닙니다. 다시 조회해 주세요.")


async def review_content(
    db: AsyncSession, content_id: int, request: AdminContentReviewRequest, *,
    user_id: int, permissions: AdminReviewPermissionProvider,
) -> AdminContentReviewResponse:
    if type(content_id) is not int or not 0 < content_id <= 9223372036854775807:
        raise ValueError("Invalid content ID")
    if type(user_id) is not int or user_id <= 0:
        raise ValueError("Invalid authenticated actor")
    request = AdminContentReviewRequest.model_validate(request.model_dump())
    # Never silently commit another operation's transaction or use a savepoint
    # that could return success before the outer transaction actually commits.
    if db.in_transaction():
        raise RuntimeError("Review requires a fresh transaction")
    try:
        async with db.begin():
            try:
                grant = await permissions.lock_grant(db, user_id=user_id, content_id=content_id)
            except Exception:
                raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 쓰기 권한을 확인할 수 없습니다.") from None
            if (not isinstance(grant, AdminReviewGrant) or type(grant.user_id) is not int or grant.user_id != user_id
                    or type(grant.admin_account_id) is not int or grant.admin_account_id <= 0):
                raise ApiError(403, "ADMIN_WRITE_REQUIRED", "콘텐츠 검수 쓰기 권한이 필요합니다.")
            content = await repo.lock_content(db, content_id)
            if content is None:
                raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
            previous = await repo.previous_action(db, content_id=content_id,
                admin_account_id=grant.admin_account_id, request_id=request.request_id)
            fingerprint = request.model_dump(mode="json")
            if previous is not None:
                saved = previous["after_value"]
                if saved.get("request") != fingerprint or previous["rationale"] != request.rationale:
                    raise ApiError(409, "REVIEW_REQUEST_CONFLICT", "같은 요청 ID로 다른 조치를 요청할 수 없습니다.")
                # A replay describes the original committed action, not a new write.
                response = AdminContentReviewResponse(**saved["response"],
                    audit_log_id=previous["admin_action_log_id"], replayed=True)
            else:
                if (content["deleted_at"] is not None or content["status"] != request.expected_status
                        or content["judgment_status"] != request.expected_judgment_status
                        or _utc(content["updated_at"]) != request.expected_updated_at
                        or content["source_draft_id"] is None):
                    raise _conflict()
                draft = await repo.lock_draft(db, content["source_draft_id"])
                if draft is None or draft["status"] not in {"checking", "approved"}:
                    raise _conflict()
                now = datetime.now(timezone.utc)
                approve = request.action == "approve"
                content_values = dict(status="active" if approve else "hidden",
                    judgment_status="confirmed" if approve else "failed", updated_at=now,
                    published_at=(content["published_at"] or now) if approve else content["published_at"])
                draft_values = dict(status="published" if approve else "discarded", updated_at=now)
                if approve:
                    content_values["ai_judgment_basis"] = _without_preview(content["ai_judgment_basis"])
                    draft_values["factcheck_result"] = _without_preview(draft["factcheck_result"])
                if not await repo.update_content(db, content, content_values):
                    raise _conflict()
                if not await repo.update_draft(db, draft, draft_values):
                    raise _conflict()
                result = dict(content_id=content_id, draft_id=draft["draft_id"], action=request.action,
                    status=content_values["status"], judgment_status=content_values["judgment_status"],
                    draft_status=draft_values["status"],
                    published_at=content_values["published_at"], updated_at=now)
                audit_id = await repo.append_audit(db, admin_account_id=grant.admin_account_id,
                    action_type=f"content.{request.action}", target_type="content", target_id=content_id,
                    before_value={"content": _snapshot(content), "draft": _snapshot(draft)},
                    after_value={"contract_version": 1, "request_id": str(request.request_id),
                        "request": fingerprint, "actor_user_id": user_id, "response": _snapshot(result),
                        "content": _snapshot(dict(content) | content_values),
                        "draft": _snapshot(dict(draft) | draft_values)},
                    rationale=request.rationale, acted_at=now)
                response = AdminContentReviewResponse(**result, audit_log_id=audit_id)
        # The transaction context has committed successfully before any success.
        return response
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None)
        if code in {"40001", "40P01", "55P03"} or getattr(error.orig, "sqlite_errorcode", None) in {5, 6, 517}:
            raise _conflict() from None
        raise ApiError(503, "ADMIN_REVIEW_UNAVAILABLE", "검수 저장에 실패했습니다. 같은 요청 ID로 다시 시도해 주세요.") from None
