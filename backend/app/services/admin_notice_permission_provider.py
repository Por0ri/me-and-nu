"""Notice authoring has an explicit current database capability."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAccount, UserAccount
from app.services.admin_permission_provider import active_grant_query, valid_id

WRITE_NOTICE_ROLES = frozenset({"operator", "admin"})


@dataclass(frozen=True)
class AdminNoticeGrant:
    admin_account_id: int
    user_id: int


class AdminNoticePermissionProvider(Protocol):
    async def read_grant(self, db: AsyncSession, *, user_id: int) -> AdminNoticeGrant | None: ...
    async def lock_grant(self, db: AsyncSession, *, user_id: int) -> AdminNoticeGrant | None: ...


class DatabaseAdminNoticePermissionProvider:
    async def read_grant(self, db, *, user_id):
        if not valid_id(user_id):
            return None
        row = (await db.execute(active_grant_query(user_id, WRITE_NOTICE_ROLES)
                               .where(UserAccount.withdrawn_at.is_(None)))).mappings().one_or_none()
        return AdminNoticeGrant(**row) if row else None

    async def lock_grant(self, db, *, user_id):
        if not valid_id(user_id):
            return None
        user = (await db.execute(select(UserAccount.account_status, UserAccount.withdrawn_at)
            .where(UserAccount.user_id == user_id).with_for_update())).one_or_none()
        if user is None or user.account_status != "active" or user.withdrawn_at is not None:
            return None
        row = (await db.execute(select(AdminAccount.admin_account_id, AdminAccount.user_id).where(
            AdminAccount.user_id == user_id, AdminAccount.revoked_at.is_(None),
            AdminAccount.role.in_(WRITE_NOTICE_ROLES)).with_for_update())).mappings().one_or_none()
        return AdminNoticeGrant(**row) if row else None


def get_admin_notice_permission_provider() -> AdminNoticePermissionProvider:
    return DatabaseAdminNoticePermissionProvider()
