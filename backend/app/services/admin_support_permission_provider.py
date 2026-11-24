"""Support writes have their own capability and lock current account/grant rows."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAccount, UserAccount
from app.services.admin_permission_provider import active_grant_query, valid_id

WRITE_SUPPORT_ROLES = frozenset({"operator", "admin"})


@dataclass(frozen=True)
class AdminSupportGrant:
    admin_account_id: int
    user_id: int


class AdminSupportPermissionProvider(Protocol):
    async def read_grant(self, db: AsyncSession, *, user_id: int) -> AdminSupportGrant | None: ...
    async def lock_grant(self, db: AsyncSession, *, user_id: int) -> AdminSupportGrant | None: ...


class DenyAdminSupportPermissionProvider:
    async def read_grant(self, db, *, user_id): return None
    async def lock_grant(self, db, *, user_id): return None


class DatabaseAdminSupportPermissionProvider:
    async def read_grant(self, db, *, user_id):
        if not valid_id(user_id):
            return None
        row = (await db.execute(active_grant_query(user_id, WRITE_SUPPORT_ROLES)
                               .where(UserAccount.withdrawn_at.is_(None)))).mappings().one_or_none()
        return AdminSupportGrant(**row) if row else None

    async def lock_grant(self, db, *, user_id):
        if not valid_id(user_id):
            return None
        # Same order as grant changes and withdrawal. Do not use ORM cached rows.
        user = (await db.execute(select(UserAccount.account_status, UserAccount.withdrawn_at).where(
            UserAccount.user_id == user_id).with_for_update())).one_or_none()
        if user is None or user.account_status != "active" or user.withdrawn_at is not None:
            return None
        row = (await db.execute(select(AdminAccount.admin_account_id, AdminAccount.user_id).where(
            AdminAccount.user_id == user_id, AdminAccount.revoked_at.is_(None),
            AdminAccount.role.in_(WRITE_SUPPORT_ROLES)).with_for_update())).mappings().one_or_none()
        return AdminSupportGrant(**row) if row else None


def get_admin_support_permission_provider() -> AdminSupportPermissionProvider:
    return DatabaseAdminSupportPermissionProvider()
