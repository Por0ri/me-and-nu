"""Audited manual grant operations; never infer administrator identity from email.

Each mutation owns a fresh transaction. Lock order is the grant-management
advisory lock, sorted UserAccount rows, then AdminAccount rows. Reviews use
UserAccount -> AdminAccount -> Content -> Draft. Historical grants are immutable
except for revoked_at; a role change is revoke + grant with a new grant ID.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import AdminAccount, AdminActionLog, UserAccount
from app.schemas.admin_content_input import clean_text
from app.services.admin_permission_provider import READ_CONTENT_ROLES, valid_id


def _id(value: int) -> None:
    if not valid_id(value):
        raise ValueError("Expected a positive int64 ID")


def _rationale(value: str) -> str:
    value = clean_text(value)
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        raise ValueError("Rationale must contain 1 to 2,000 characters")
    return value


def _snapshot(row) -> dict:
    return {
        key: (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
        if isinstance(value, datetime) else value
        for key, value in row.items()
    }


@asynccontextmanager
async def _mutation(db: AsyncSession):
    if db.in_transaction():
        raise RuntimeError("Grant management requires a fresh transaction")
    try:
        async with db.begin():
            dialect = db.get_bind().dialect.name
            if dialect == "postgresql":
                # Serializes bootstrap, last-admin checks and competing mutations.
                await db.execute(text("SELECT pg_advisory_xact_lock(1296387669, 1)"))
            elif dialect == "sqlite":
                # Disposable test DB only; acquire the write lock before reads.
                await db.execute(update(AdminAccount).where(text("1 = 0")).values(role=AdminAccount.role))
            else:
                raise RuntimeError("Unsupported grant-management database")
            yield
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None)
        if code in {"23505", "40001", "40P01", "55P03"}:
            raise ApiError(409, "ADMIN_GRANT_CONFLICT", "관리자 권한이 변경되었습니다. 다시 조회해 주세요.") from None
        # Commit acknowledgement may be lost. Do not claim it was not committed.
        raise ApiError(503, "ADMIN_GRANT_UNAVAILABLE", "권한 처리 결과를 확인하지 못했습니다. 권한과 감사 기록을 확인해 주세요.") from None


async def _lock_users(db: AsyncSession, user_ids: set[int]):
    result = await db.execute(select(UserAccount.user_id, UserAccount.account_status).where(
        UserAccount.user_id.in_(user_ids),
    ).order_by(UserAccount.user_id).with_for_update())
    return {row.user_id: row.account_status for row in result}


async def _require_manager(db: AsyncSession, actor_user_id: int, users: dict):
    if users.get(actor_user_id) != "active":
        raise ApiError(403, "ADMIN_MANAGE_REQUIRED", "활성 관리자 계정이 필요합니다.")
    result = await db.execute(select(AdminAccount.__table__).where(
        AdminAccount.user_id == actor_user_id,
        AdminAccount.revoked_at.is_(None),
        AdminAccount.role == "admin",
    ).with_for_update())
    actor = result.mappings().one_or_none()
    if actor is None:
        raise ApiError(403, "ADMIN_MANAGE_REQUIRED", "관리자 권한 관리 권한이 필요합니다.")
    return actor


async def _audit(db, *, actor_id: int, action: str, target_id: int,
                 before: dict | None, after: dict, rationale: str, now: datetime) -> int:
    result = await db.execute(insert(AdminActionLog).values(
        admin_account_id=actor_id, action_type=action, target_type="admin_account",
        target_id=target_id, before_value=before, after_value=after,
        rationale=rationale, acted_at=now,
    ).returning(AdminActionLog.admin_action_log_id))
    audit_id = result.scalar_one()
    if not valid_id(audit_id):
        raise RuntimeError("Invalid grant audit ID")
    return audit_id


async def bootstrap_admin(db: AsyncSession, *, target_user_id: int, rationale: str) -> dict:
    """Explicit operator bootstrap, allowed only when no grant history exists.

    The trusted local CLI is the boundary for this one-time operation. It is
    intentionally not exposed as an unauthenticated HTTP endpoint.
    """
    _id(target_user_id)
    rationale = _rationale(rationale)
    async with _mutation(db):
        users = await _lock_users(db, {target_user_id})
        if users.get(target_user_id) != "active":
            raise ApiError(409, "ADMIN_TARGET_INACTIVE", "활성 회원을 지정해 주세요.")
        existing = (await db.execute(select(AdminAccount.admin_account_id).limit(1))).first()
        if existing is not None:
            raise ApiError(409, "ADMIN_BOOTSTRAP_CLOSED", "관리자 권한 이력이 있어 초기 부여를 실행할 수 없습니다.")
        now = datetime.now(timezone.utc)
        grant = (await db.execute(insert(AdminAccount).values(
            user_id=target_user_id, role="admin", granted_at=now,
        ).returning(*AdminAccount.__table__.c))).mappings().one()
        grant_id = grant["admin_account_id"]
        audit_id = await _audit(db, actor_id=grant_id, action="admin.bootstrap",
            target_id=grant_id, before=None, after=_snapshot(grant), rationale=rationale, now=now)
        result = {"adminAccountId": grant_id, "userId": target_user_id,
                  "role": "admin", "auditLogId": audit_id}
    return result


async def grant_admin(db: AsyncSession, *, actor_user_id: int, target_user_id: int,
                      role: str, rationale: str) -> dict:
    _id(actor_user_id)
    _id(target_user_id)
    if not isinstance(role, str) or role not in READ_CONTENT_ROLES:
        raise ValueError("Role must be viewer, operator or admin")
    rationale = _rationale(rationale)
    async with _mutation(db):
        users = await _lock_users(db, {actor_user_id, target_user_id})
        actor = await _require_manager(db, actor_user_id, users)
        if users.get(target_user_id) != "active":
            raise ApiError(409, "ADMIN_TARGET_INACTIVE", "활성 회원을 지정해 주세요.")
        existing = (await db.execute(select(AdminAccount.admin_account_id).where(
            AdminAccount.user_id == target_user_id, AdminAccount.revoked_at.is_(None),
        ).with_for_update())).first()
        if existing is not None:
            raise ApiError(409, "ADMIN_GRANT_EXISTS", "이미 활성 권한이 있습니다. 역할 변경은 회수 후 새로 부여해 주세요.")
        now = datetime.now(timezone.utc)
        grant = (await db.execute(insert(AdminAccount).values(
            user_id=target_user_id, role=role,
            granted_by_admin_id=actor["admin_account_id"], granted_at=now,
        ).returning(*AdminAccount.__table__.c))).mappings().one()
        audit_id = await _audit(db, actor_id=actor["admin_account_id"], action="admin.grant",
            target_id=grant["admin_account_id"], before=None, after=_snapshot(grant), rationale=rationale, now=now)
        result = {"adminAccountId": grant["admin_account_id"], "userId": target_user_id,
                  "role": role, "auditLogId": audit_id}
    return result


async def revoke_admin(db: AsyncSession, *, actor_user_id: int, admin_account_id: int,
                       rationale: str) -> dict:
    _id(actor_user_id)
    _id(admin_account_id)
    rationale = _rationale(rationale)
    async with _mutation(db):
        # The mutation lock prevents grant changes between this lookup and locks.
        target = (await db.execute(select(AdminAccount.__table__).where(
            AdminAccount.admin_account_id == admin_account_id,
        ))).mappings().one_or_none()
        users = await _lock_users(db, {actor_user_id} | ({target["user_id"]} if target else set()))
        actor = await _require_manager(db, actor_user_id, users)
        if target is None:
            raise ApiError(404, "ADMIN_GRANT_NOT_FOUND", "관리자 권한을 찾을 수 없습니다.")
        target = (await db.execute(select(AdminAccount.__table__).where(
            AdminAccount.admin_account_id == admin_account_id,
        ).with_for_update())).mappings().one()
        if target["revoked_at"] is not None:
            raise ApiError(409, "ADMIN_GRANT_REVOKED", "이미 회수한 권한입니다.")
        if target["role"] == "admin":
            remaining = (await db.execute(select(func.count()).select_from(AdminAccount).join(
                UserAccount, UserAccount.user_id == AdminAccount.user_id,
            ).where(AdminAccount.role == "admin", AdminAccount.revoked_at.is_(None),
                    AdminAccount.admin_account_id != admin_account_id,
                    UserAccount.account_status == "active"))).scalar_one()
            if remaining == 0:
                raise ApiError(409, "ADMIN_LAST_MANAGER", "마지막 활성 관리자는 회수할 수 없습니다. 다른 관리자를 먼저 지정해 주세요.")
        now = datetime.now(timezone.utc)
        await db.execute(update(AdminAccount).where(
            AdminAccount.admin_account_id == admin_account_id,
        ).values(revoked_at=now))
        after = dict(target) | {"revoked_at": now}
        audit_id = await _audit(db, actor_id=actor["admin_account_id"], action="admin.revoke",
            target_id=admin_account_id, before=_snapshot(target), after=_snapshot(after), rationale=rationale, now=now)
        result = {"adminAccountId": admin_account_id, "userId": target["user_id"],
                  "role": target["role"], "revokedAt": now.isoformat(), "auditLogId": audit_id}
    return result


async def list_grants(db: AsyncSession, *, actor_user_id: int, target_user_id: int | None = None) -> list[dict]:
    """Trusted admin CLI read; includes revoked history, without account PII."""
    _id(actor_user_id)
    if target_user_id is not None:
        _id(target_user_id)
    actor = (await db.execute(select(AdminAccount.admin_account_id).join(
        UserAccount, UserAccount.user_id == AdminAccount.user_id,
    ).where(AdminAccount.user_id == actor_user_id, AdminAccount.role == "admin",
            AdminAccount.revoked_at.is_(None), UserAccount.account_status == "active"))).one_or_none()
    if actor is None:
        raise ApiError(403, "ADMIN_MANAGE_REQUIRED", "관리자 권한 관리 권한이 필요합니다.")
    statement = select(AdminAccount.__table__).order_by(AdminAccount.admin_account_id)
    if target_user_id is not None:
        statement = statement.where(AdminAccount.user_id == target_user_id)
    return [_snapshot(row) for row in (await db.execute(statement)).mappings()]
