"""Server-owned permission boundary; admin_account integration is not approved yet."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


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


def get_admin_permission_provider() -> AdminPermissionProvider:
    # No configuration flag or built-in allowing provider. Tests override this
    # dependency only on their isolated app; production integration needs review.
    return DenyAdminPermissionProvider()
