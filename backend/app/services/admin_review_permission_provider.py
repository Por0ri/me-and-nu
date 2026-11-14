"""Separate write capability. No role policy or production DB adapter yet."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class AdminReviewGrant:
    admin_account_id: int
    user_id: int


class AdminReviewPermissionProvider(Protocol):
    async def lock_grant(
        self, db: AsyncSession, *, user_id: int, content_id: int,
    ) -> AdminReviewGrant | None:
        """Read fresh trusted write permission for this actor AND target.

        Lock the active admin_account grant in this transaction until commit;
        revocation must take the same row lock. Never commit, cache permissions,
        or infer write access from can_read_contents. Return None to deny.
        PR #71 role names/scope policy are deliberately not guessed here.
        """
        ...


class DenyAdminReviewPermissionProvider:
    async def lock_grant(self, db: AsyncSession, *, user_id: int, content_id: int):
        return None


def get_admin_review_permission_provider() -> AdminReviewPermissionProvider:
    return DenyAdminReviewPermissionProvider()
