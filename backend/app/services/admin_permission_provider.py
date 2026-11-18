"""Current database grants, never client role claims or cached session roles.

Phase 1 scope is all service contents. These roles do not grant unrelated
member/report writes; those APIs need their own capability checks.
"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAccount, UserAccount

READ_CONTENT_ROLES = frozenset({"viewer", "operator", "admin"})
REVIEW_CONTENT_ROLES = frozenset({"operator", "admin"})


def valid_id(value: int) -> bool:
    return type(value) is int and 0 < value <= 9223372036854775807


def active_grant_query(user_id: int, roles: frozenset[str]):
    return select(AdminAccount.admin_account_id, AdminAccount.user_id).join(
        UserAccount, UserAccount.user_id == AdminAccount.user_id,
    ).where(
        AdminAccount.user_id == user_id,
        AdminAccount.revoked_at.is_(None),
        AdminAccount.role.in_(roles),
        UserAccount.account_status == "active",
    )


class AdminPermissionProvider(Protocol):
    async def can_read_contents(self, db: AsyncSession, *, user_id: int) -> bool:
        """Read current permission on every request, including revocation.

        Implementations must consult trusted server storage, never session role
        snapshots, client claims, email allowlists, or development auth bypasses.
        Raise on verification failure; only an explicit True grants access.
        """
        ...


class DenyAdminPermissionProvider:
    async def can_read_contents(self, db: AsyncSession, *, user_id: int) -> bool:
        return False


class DatabaseAdminPermissionProvider:
    async def can_read_contents(self, db: AsyncSession, *, user_id: int) -> bool:
        if not valid_id(user_id):
            return False
        # scalar_one_or_none also fails closed if broken data contains duplicate
        # active grants. Never translate a storage failure into an allowing value.
        result = await db.execute(active_grant_query(user_id, READ_CONTENT_ROLES))
        return result.one_or_none() is not None


def get_admin_permission_provider() -> AdminPermissionProvider:
    return DatabaseAdminPermissionProvider()
