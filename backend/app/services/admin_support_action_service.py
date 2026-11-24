"""Atomic support transitions and answer publication, with bounded safe retries.

Order: actor account -> current grant -> requester (inquiries) -> record -> audit.
The audit stores an input digest, never an inquiry/answer body or reporter identity.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.exceptions import ApiError
from app.models import AdminActionLog, ContentReport, Inquiry, InquiryAnswer, UserAccount
from app.repositories.admin_content_review_repository import append_audit
from app.schemas.admin_support_action import (
    AdminInquiryAnswerRequest, AdminReportActionRequest, AdminSupportActionResponse,
    AdminSupportActions, AdminSupportScope,
)
from app.services.admin_permission_provider import valid_id
from app.services.admin_support_permission_provider import AdminSupportGrant


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _conflict():
    return ApiError(409, "SUPPORT_STATE_CONFLICT", "처리 상태가 변경되었거나 이미 완료되었습니다. 최신 내용을 다시 확인해 주세요.")


def allowed_actions(record_type, raw_status):
    if record_type == "inquiry":
        return ["answer"] if raw_status == "received" else []
    return {"received": ["start_review", "resolve", "reject"], "reviewing": ["resolve", "reject"]}.get(raw_status, [])


async def get_actions(db, *, user_id, record_type, raw_status, permissions):
    try:
        grant = await permissions.read_grant(db, user_id=user_id)
    except Exception:
        raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 처리 권한을 확인할 수 없습니다.") from None
    if (not isinstance(grant, AdminSupportGrant) or not valid_id(grant.user_id)
            or grant.user_id != user_id or not valid_id(grant.admin_account_id)):
        return AdminSupportActions()
    actions = allowed_actions(record_type, raw_status)
    return AdminSupportActions(allowed_actions=actions, disabled_reason=None if actions else "terminal_state",
        scope=AdminSupportScope(actor_user_id=str(user_id), admin_account_id=str(grant.admin_account_id)))


async def _lock_record(db, record_type, record_id):
    if record_type == "report":
        return (await db.execute(select(ContentReport.report_id.label("record_id"),
            ContentReport.status, ContentReport.updated_at).where(ContentReport.report_id == record_id)
            .with_for_update())).mappings().one_or_none()
    # Lock the requester before their inquiry, as the withdrawal flow does.
    requester = (await db.execute(select(Inquiry.user_id).where(Inquiry.inquiry_id == record_id))).scalar_one_or_none()
    if requester is None:
        return None
    retained = (await db.execute(select(UserAccount.user_id).where(
        UserAccount.user_id == requester, UserAccount.withdrawn_at.is_(None),
        UserAccount.account_status != "withdrawn").with_for_update())).scalar_one_or_none()
    if retained is None:
        return None
    return (await db.execute(select(Inquiry.inquiry_id.label("record_id"), Inquiry.status, Inquiry.updated_at)
        .where(Inquiry.inquiry_id == record_id, Inquiry.user_id == requester).with_for_update())).mappings().one_or_none()


def _fingerprint(payload):
    value = payload.model_dump(mode="json")
    value["expected_updated_at"] = payload.expected_updated_at.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


async def _apply(db, record_id, request, *, record_type, user_id, permissions, expected_scope):
    if not valid_id(record_id) or not valid_id(user_id):
        raise ValueError("Invalid support record or authenticated actor")
    expected_scope = AdminSupportScope.model_validate(expected_scope)
    if db.in_transaction():
        raise RuntimeError("Support writes require a fresh transaction")
    action = request.action if record_type == "report" else "answer"
    try:
        async with db.begin():
            try:
                grant = await permissions.lock_grant(db, user_id=user_id)
            except Exception:
                raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 처리 권한을 확인할 수 없습니다.") from None
            if (not isinstance(grant, AdminSupportGrant) or not valid_id(grant.user_id)
                    or grant.user_id != user_id or not valid_id(grant.admin_account_id)):
                raise ApiError(403, "ADMIN_WRITE_REQUIRED", "신고·문의 처리 권한이 필요합니다.")
            if (expected_scope.actor_user_id != str(user_id)
                    or expected_scope.admin_account_id != str(grant.admin_account_id)):
                raise ApiError(409, "SUPPORT_SCOPE_CHANGED", "계정 또는 권한이 변경되었습니다. 기존 요청의 처리 결과를 다시 확인해 주세요.")
            row = await _lock_record(db, record_type, record_id)
            if row is None:
                code = "ADMIN_REPORT_NOT_FOUND" if record_type == "report" else "ADMIN_INQUIRY_NOT_FOUND"
                raise ApiError(404, code, "신고·문의 내역을 찾을 수 없습니다.")
            previous = (await db.execute(select(AdminActionLog.admin_action_log_id, AdminActionLog.after_value).where(
                AdminActionLog.admin_account_id == grant.admin_account_id,
                AdminActionLog.target_type == record_type, AdminActionLog.target_id == record_id,
                AdminActionLog.action_type.in_(["report.start_review", "report.resolve", "report.reject", "inquiry.answer"]),
                AdminActionLog.after_value["request_id"].as_string() == str(request.request_id),
            ))).mappings().one_or_none()
            fingerprint = _fingerprint(request)
            if previous is not None:
                stored = previous["after_value"]
                if stored.get("request_digest") != fingerprint:
                    raise ApiError(409, "SUPPORT_REQUEST_CONFLICT", "같은 요청 ID로 다른 내용을 제출할 수 없습니다.")
                response = AdminSupportActionResponse(**stored["response"],
                    audit_log_id=str(previous["admin_action_log_id"]), replayed=True)
            else:
                if (row["status"] != request.expected_status or _utc(row["updated_at"]) != request.expected_updated_at
                        or action not in allowed_actions(record_type, row["status"])):
                    raise _conflict()
                now = max(datetime.now(timezone.utc), _utc(row["updated_at"]) + timedelta(microseconds=1))
                status = {"start_review": "reviewing", "resolve": "resolved", "reject": "rejected", "answer": "answered"}[action]
                model = ContentReport if record_type == "report" else Inquiry
                identifier = model.report_id if record_type == "report" else model.inquiry_id
                changed = await db.execute(update(model).where(identifier == record_id,
                    model.status == row["status"], model.updated_at == row["updated_at"])
                    .values(status=status, updated_at=now).execution_options(synchronize_session=False))
                if changed.rowcount != 1:
                    raise _conflict()
                if record_type == "inquiry":
                    existing = (await db.execute(select(InquiryAnswer.inquiry_id).where(
                        InquiryAnswer.inquiry_id == record_id))).scalar_one_or_none()
                    if existing is not None:
                        raise _conflict()
                    await db.execute(insert(InquiryAnswer).values(inquiry_id=record_id, body=request.answer,
                        admin_account_id=grant.admin_account_id, answered_at=now))
                result = dict(record_id=str(record_id), record_type=record_type, action=action,
                              raw_status=status, updated_at=now.isoformat())
                audit_id = await append_audit(db, admin_account_id=grant.admin_account_id,
                    action_type=f"{record_type}.{action}", target_type=record_type, target_id=record_id,
                    before_value={"status": row["status"], "updated_at": _utc(row["updated_at"]).isoformat()},
                    after_value={"contract_version": 1, "request_id": str(request.request_id),
                        "request_digest": fingerprint, "response": result},
                    rationale=request.rationale if record_type == "report" else "문의 답변 등록",
                    acted_at=now)
                response = AdminSupportActionResponse(**result, audit_log_id=str(audit_id))
        return response  # Commit has finished; no success can escape a failed audit.
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None)
        if code in {"40001", "40P01", "55P03"} or getattr(error.orig, "sqlite_errorcode", None) in {5, 6, 517}:
            raise _conflict() from None
        raise ApiError(503, "ADMIN_SUPPORT_WRITE_UNAVAILABLE", "처리 결과를 확인할 수 없습니다. 같은 요청으로 다시 확인해 주세요.") from None
    except SQLAlchemyError:
        # Do not let SQL parameter exceptions log a private answer body.
        raise ApiError(503, "ADMIN_SUPPORT_WRITE_UNAVAILABLE", "처리 결과를 확인할 수 없습니다. 같은 요청으로 다시 확인해 주세요.") from None


async def apply_report_action(db, record_id, request, *, user_id, permissions, expected_scope):
    request = AdminReportActionRequest.model_validate(request.model_dump())
    return await _apply(db, record_id, request, record_type="report", user_id=user_id,
                        permissions=permissions, expected_scope=expected_scope)


async def answer_inquiry(db, record_id, request, *, user_id, permissions, expected_scope):
    request = AdminInquiryAnswerRequest.model_validate(request.model_dump())
    return await _apply(db, record_id, request, record_type="inquiry", user_id=user_id,
                        permissions=permissions, expected_scope=expected_scope)
