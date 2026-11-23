"""Administrative notice reads and atomic, idempotent authoring transactions."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import case, func, insert, select, text, true, update
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.exceptions import ApiError
from app.models import AdminActionLog, Notice, Notification
from app.repositories.admin_content_review_repository import append_audit
from app.schemas.admin_notice import (
    AdminNoticeActions, AdminNoticeActionRequest, AdminNoticeCapabilities, AdminNoticeCreateRequest,
    AdminNoticeDetail, AdminNoticeItem, AdminNoticePage, AdminNoticeScope, AdminNoticeStatistics,
    AdminNoticeWriteResponse,
)
from app.services.admin_notice_permission_provider import AdminNoticeGrant
from app.services.admin_permission_provider import valid_id

WRITE_ACTIONS = ("notice.create_draft", "notice.create_publish", "notice.create_schedule",
                 "notice.save_draft", "notice.publish", "notice.schedule", "notice.cancel_schedule")


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _clock(now):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Notice clock requires an offset")
    return now.astimezone(timezone.utc)


def allowed_actions(status):
    return {"draft": ["save_draft", "publish", "schedule"], "scheduled": ["cancel_schedule"]}.get(status, [])


def _grant_matches(grant, user_id):
    return (isinstance(grant, AdminNoticeGrant) and valid_id(grant.user_id)
            and grant.user_id == user_id and valid_id(grant.admin_account_id))


async def capabilities(db, *, user_id, permissions, status=None):
    try:
        grant = await permissions.read_grant(db, user_id=user_id)
    except Exception:
        raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 공지 권한을 확인할 수 없습니다.") from None
    schema = AdminNoticeCapabilities if status is None else AdminNoticeActions
    if not _grant_matches(grant, user_id):
        return schema()
    actions = ["create"] if status is None else allowed_actions(status)
    return schema(allowed_actions=actions, disabled_reason=None if actions else "terminal_state",
                  scope=AdminNoticeScope(actor_user_id=str(user_id), admin_account_id=str(grant.admin_account_id)))


def _columns():
    # This is a count of members who marked the linked inbox item as read, not
    # page impressions or an invented view count. Duplicate rows cannot inflate it.
    reads = select(func.count(func.distinct(Notification.user_id))).where(
        Notification.notice_id == Notice.notice_id, Notification.read_at.is_not(None),
        Notification.notification_type == "system_notice").correlate(Notice).scalar_subquery()
    return (Notice.notice_id, Notice.title, Notice.status, Notice.scheduled_at,
            Notice.published_at, Notice.created_at, Notice.updated_at, reads.label("read_count"))


def _item(row):
    return AdminNoticeItem(id=str(row["notice_id"]), title=row["title"], status=row["status"],
        scheduled_at=_utc(row["scheduled_at"]), published_at=_utc(row["published_at"]),
        created_at=_utc(row["created_at"]), updated_at=_utc(row["updated_at"]), read_count=row["read_count"])


async def list_notices(db, query):
    scope = select(*_columns())
    if query.q.strip():
        scope = scope.where(Notice.title.icontains(query.q.strip(), autoescape=True))
    scope = scope.cte("notice_scope")
    filtered = select(scope)
    if query.status != "all":
        filtered = filtered.where(scope.c.status == query.status)
    filtered = filtered.cte("notice_filtered")
    statistics = select(func.count().label("all_count"),
        *(func.coalesce(func.sum(case((scope.c.status == state, 1), else_=0)), 0).label(state + "_count")
          for state in ("draft", "scheduled", "published")),
        select(func.count()).select_from(filtered).scalar_subquery().label("total"))\
        .select_from(scope).cte("notice_statistics")
    page = select(filtered).order_by(filtered.c.created_at.desc(), filtered.c.notice_id.desc())\
        .offset((query.page - 1) * query.page_size).limit(query.page_size).subquery("notice_page")
    rows = (await db.execute(select(statistics, page).select_from(statistics.outerjoin(page, true()))
        .order_by(page.c.created_at.desc(), page.c.notice_id.desc()))).mappings().all()
    totals = rows[0]
    return AdminNoticePage(items=[_item(row) for row in rows if row["notice_id"] is not None],
        page=query.page, page_size=query.page_size, total=totals["total"],
        statistics=AdminNoticeStatistics(**{name: totals[name + "_count"]
                                           for name in ("all", "draft", "scheduled", "published")}))


async def get_notice(db, notice_id):
    row = (await db.execute(select(*_columns(), Notice.body).where(Notice.notice_id == notice_id)))\
        .mappings().one_or_none()
    if row is None:
        raise ApiError(404, "ADMIN_NOTICE_NOT_FOUND", "공지를 찾을 수 없습니다.")
    return AdminNoticeDetail(**_item(row).model_dump(), body=row["body"])


def _fingerprint(payload, notice_id):
    value = payload.model_dump(mode="json")
    for name in ("scheduled_at", "expected_updated_at"):
        stamp = getattr(payload, name, None)
        if stamp is not None:
            value[name] = stamp.astimezone(timezone.utc).isoformat()
    value["target_id"] = notice_id
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _conflict():
    return ApiError(409, "NOTICE_STATE_CONFLICT", "공지 상태가 변경되었습니다. 최신 내용을 다시 확인해 주세요.")


def _response_values(notice):
    return {"notice_id": str(notice.notice_id), "status": notice.status,
            "scheduled_at": _utc(notice.scheduled_at).isoformat() if notice.scheduled_at else None,
            "published_at": _utc(notice.published_at).isoformat() if notice.published_at else None,
            "updated_at": _utc(notice.updated_at).isoformat()}


async def _write(db, payload, *, notice_id, user_id, permissions, expected_scope, now=None):
    if not valid_id(user_id) or (notice_id is not None and not valid_id(notice_id)):
        raise ValueError("Invalid notice or authenticated actor")
    expected_scope = AdminNoticeScope.model_validate(expected_scope)
    if db.in_transaction():
        raise RuntimeError("Notice writes require a fresh transaction")
    try:
        async with db.begin():
            if db.get_bind().dialect.name == "postgresql":
                # Bound both global-lock contention and delivery work. SET LOCAL
                # restores the connection defaults when this transaction ends.
                await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                await db.execute(text("SET LOCAL statement_timeout = '30s'"))
            from app.services.notice_delivery_service import lock_notice_publications
            await lock_notice_publications(db)
            # Serialize writes from one grant, including creates that have no row
            # yet. Retries can therefore discover the committed notice/audit.
            try:
                grant = await permissions.lock_grant(db, user_id=user_id)
            except Exception:
                raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 공지 권한을 확인할 수 없습니다.") from None
            if not _grant_matches(grant, user_id):
                raise ApiError(403, "ADMIN_WRITE_REQUIRED", "공지 작성 권한이 필요합니다.")
            if (expected_scope.actor_user_id != str(user_id)
                    or expected_scope.admin_account_id != str(grant.admin_account_id)):
                raise ApiError(409, "NOTICE_SCOPE_CHANGED", "계정 또는 권한이 변경되었습니다. 기존 요청의 처리 결과를 다시 확인해 주세요.")
            previous = (await db.execute(select(AdminActionLog.admin_action_log_id, AdminActionLog.after_value).where(
                AdminActionLog.admin_account_id == grant.admin_account_id, AdminActionLog.target_type == "notice",
                AdminActionLog.action_type.in_(WRITE_ACTIONS),
                AdminActionLog.after_value["request_id"].as_string() == str(payload.request_id))))\
                .mappings().one_or_none()
            digest = _fingerprint(payload, notice_id)
            if previous is not None:
                stored = previous["after_value"]
                if stored.get("request_digest") != digest:
                    raise ApiError(409, "NOTICE_REQUEST_CONFLICT", "같은 요청 ID로 다른 내용을 제출할 수 없습니다.")
                response = AdminNoticeWriteResponse(**stored["response"],
                    audit_log_id=str(previous["admin_action_log_id"]), replayed=True)
            else:
                moment = _clock(now)
                if payload.action == "schedule" and payload.scheduled_at <= moment:
                    raise ApiError(422, "NOTICE_SCHEDULE_INVALID", "예약 발송 시각은 현재보다 미래여야 합니다.")
                before = None
                if notice_id is None:
                    notice = (await db.execute(insert(Notice).values(title=payload.title, body=payload.body,
                        status="draft", scheduled_at=None, published_at=None,
                        created_by_admin_id=grant.admin_account_id, updated_by_admin_id=grant.admin_account_id,
                        created_at=moment, updated_at=moment).returning(Notice))).scalar_one()
                    action_type = "notice.create_" + {"save_draft": "draft", "publish": "publish", "schedule": "schedule"}[payload.action]
                else:
                    notice = (await db.execute(select(Notice).where(Notice.notice_id == notice_id)
                                                .with_for_update())).scalar_one_or_none()
                    if notice is None:
                        raise ApiError(404, "ADMIN_NOTICE_NOT_FOUND", "공지를 찾을 수 없습니다.")
                    if (notice.status != payload.expected_status or _utc(notice.updated_at) != payload.expected_updated_at
                            or payload.action not in allowed_actions(notice.status)):
                        raise _conflict()
                    before = {"status": notice.status, "updated_at": _utc(notice.updated_at).isoformat(),
                              "scheduled_at": _utc(notice.scheduled_at).isoformat() if notice.scheduled_at else None}
                    moment = max(moment, _utc(notice.updated_at) + timedelta(microseconds=1))
                    # CAS supplements the row lock, including lightweight local
                    # test engines, and never erases author fields by omission.
                    values = {"updated_by_admin_id": grant.admin_account_id, "updated_at": moment}
                    if payload.action != "cancel_schedule":
                        values.update(title=payload.title, body=payload.body)
                    changed = await db.execute(update(Notice).where(Notice.notice_id == notice_id,
                        Notice.status == notice.status, Notice.updated_at == notice.updated_at).values(**values)
                        .execution_options(synchronize_session=False))
                    if changed.rowcount != 1:
                        raise _conflict()
                    for name, value in values.items():
                        setattr(notice, name, value)
                    action_type = "notice." + payload.action
                if payload.action == "publish":
                    from app.services.notice_delivery_service import publish_notice
                    await publish_notice(db, notice, moment)
                    moment = _utc(notice.updated_at)
                elif payload.action == "schedule":
                    notice.status = "scheduled"
                    notice.scheduled_at = payload.scheduled_at.astimezone(timezone.utc)
                elif payload.action == "cancel_schedule":
                    notice.status = "draft"
                    notice.scheduled_at = None
                notice.updated_at = moment
                values = _response_values(notice)
                audit_id = await append_audit(db, admin_account_id=grant.admin_account_id,
                    action_type=action_type, target_type="notice", target_id=notice.notice_id,
                    before_value=before, after_value={"contract_version": 1, "request_id": str(payload.request_id),
                        "request_digest": digest, "response": values}, rationale={"save_draft": "공지 임시 저장",
                        "publish": "공지 즉시 게시", "schedule": "공지 예약 발송", "cancel_schedule": "공지 예약 취소"}[payload.action],
                    acted_at=moment)
                response = AdminNoticeWriteResponse(**values, audit_log_id=str(audit_id))
        return response
    except DBAPIError as error:
        if (getattr(error.orig, "sqlstate", None) in {"40001", "40P01", "55P03"}
                or getattr(error.orig, "sqlite_errorcode", None) in {5, 6, 517}):
            raise _conflict() from None
        raise ApiError(503, "ADMIN_NOTICE_WRITE_UNAVAILABLE", "처리 결과를 확인할 수 없습니다. 같은 요청으로 다시 확인해 주세요.") from None
    except SQLAlchemyError:
        raise ApiError(503, "ADMIN_NOTICE_WRITE_UNAVAILABLE", "처리 결과를 확인할 수 없습니다. 같은 요청으로 다시 확인해 주세요.") from None


async def create_notice(db, payload, *, user_id, permissions, expected_scope, now=None):
    payload = AdminNoticeCreateRequest.model_validate(payload.model_dump())
    return await _write(db, payload, notice_id=None, user_id=user_id, permissions=permissions,
                        expected_scope=expected_scope, now=now)


async def apply_notice_action(db, notice_id, payload, *, user_id, permissions, expected_scope, now=None):
    payload = AdminNoticeActionRequest.model_validate(payload.model_dump())
    return await _write(db, payload, notice_id=notice_id, user_id=user_id, permissions=permissions,
                        expected_scope=expected_scope, now=now)
