"""Remove the obsolete consumer/creator account classification.

Revision ID: a8c3d1f6e902
Revises: e7a4c2d9b812
Create Date: 2026-10-20

ERD V1.1 has no signup_channel column. Account IDs and relationships are
retained. Downgrading restores the legacy column with every account classified
as consumer; the former creator classification cannot be recovered after removal.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "a8c3d1f6e902"
down_revision: str | Sequence[str] | None = "e7a4c2d9b812"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_account_signup_channel", "user_account", type_="check")
    op.drop_column("user_account", "signup_channel")


def downgrade() -> None:
    # Removal discards the former consumer/creator distinction. Rollback
    # intentionally restores every account with the legacy consumer default.
    op.add_column(
        "user_account",
        sa.Column("signup_channel", sa.String(length=20), nullable=False, server_default="consumer"),
    )
    op.create_check_constraint(
        "ck_user_account_signup_channel", "user_account",
        "signup_channel IN ('consumer', 'creator')",
    )
