"""Add browser-bound, one-time OAuth attempts.

Revision ID: b9d4e2a7f013
Revises: a8c3d1f6e902
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "b9d4e2a7f013"
down_revision: str | Sequence[str] | None = "a8c3d1f6e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_attempt",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_session_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('kakao', 'naver')", name="ck_oauth_attempt_provider"),
        sa.CheckConstraint("expires_at > created_at", name="ck_oauth_attempt_expiry"),
        sa.PrimaryKeyConstraint("state_hash", name="pk_oauth_attempt"),
    )
    op.create_index("idx_oauth_attempt_browser_created", "oauth_attempt", ["browser_session_hash", "created_at"])
    op.create_index("idx_oauth_attempt_expires_at", "oauth_attempt", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_oauth_attempt_expires_at", table_name="oauth_attempt")
    op.drop_index("idx_oauth_attempt_browser_created", table_name="oauth_attempt")
    op.drop_table("oauth_attempt")
