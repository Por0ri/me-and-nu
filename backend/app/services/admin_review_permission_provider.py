"""Fresh review capability, locked against account changes and grant revocation."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAccount, UserAccount
from app.services.admin_permission_provider import (
    REVIEW_CONTENT_ROLES, active_grant_query, valid_id,
)


@dataclass(frozen=True)
class AdminReviewGrant:
    admin_account_id: int
    user_id: int


class AdminReviewPermissionProvider(Protocol):
    async def read_grant(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> AdminReviewGrant | None:
        """Read current grant identity for binding an action; POST still locks it."""
        ...

    async def can_review_content(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> bool:
        """Read-only current capability; not a substitute for lock_grant on POST."""
        ...

    async def lock_grant(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> AdminReviewGrant | None:
        """Read fresh trusted write permission for this actor AND target.

        Lock the active admin_account grant in this transaction until commit;
        revocation must take the same row lock. Never commit, cache permissions,
        or infer write access from can_read_contents. Return None to deny.
        Current scope is all service contents; eligibility is checked separately.
        """
        ...


class DenyAdminReviewPermissionProvider:
    async def read_grant(self, db: AsyncSession, *, user_id: int, content_id: int):
        return None

    async def can_review_content(self, db: AsyncSession, *, user_id: int, content_id: int):
        return False

    async def lock_grant(self, db: AsyncSession, *, user_id: int, content_id: int):
        return None


class DatabaseAdminReviewPermissionProvider:
    async def read_grant(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> AdminReviewGrant | None:
        if not valid_id(user_id) or not valid_id(content_id):
            return None
        result = await db.execute(active_grant_query(user_id, REVIEW_CONTENT_ROLES))
        grant = result.mappings().one_or_none()
        return AdminReviewGrant(**grant) if grant is not None else None

    async def can_review_content(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> bool:
        return await self.read_grant(db, user_id=user_id, content_id=content_id) is not None

    async def lock_grant(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> AdminReviewGrant | None:
        if not valid_id(user_id) or not valid_id(content_id):
            return None
        # All grant changes and withdrawal use UserAccount -> AdminAccount.
        # Column queries avoid stale ORM identity-map state after waiting on a lock.
        user = (await db.execute(select(UserAccount.account_status).where(
            UserAccount.user_id == user_id,
        ).with_for_update())).one_or_none()
        if user is None or user.account_status != "active":
            return None
        grant = (await db.execute(select(
            AdminAccount.admin_account_id, AdminAccount.user_id,
        ).where(
            AdminAccount.user_id == user_id,
            AdminAccount.revoked_at.is_(None),
            AdminAccount.role.in_(REVIEW_CONTENT_ROLES),
        ).with_for_update())).mappings().one_or_none()
        return AdminReviewGrant(**grant) if grant is not None else None


def get_admin_review_permission_provider() -> AdminReviewPermissionProvider:
    return DatabaseAdminReviewPermissionProvider()
