"""add notification table (API-107~109)

Revision ID: e7a4c2d9b812
Revises: b5d2c8f1a7e3
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "e7a4c2d9b812"
down_revision: str | Sequence[str] | None = "b5d2c8f1a7e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("notification_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("notification_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("topic_id", sa.BigInteger()),
        sa.Column("content_id", sa.BigInteger()),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "notification_type IN ('topic_new_post', 'creator_new_post', 'system_notice', 'follow', 'direct_message')",
            name="ck_notification_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["content_id"], ["content.content_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("notification_id", name="pk_notification"),
    )
    op.create_index(
        "idx_notification_user_created_at", "notification", ["user_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "uq_notification_user_type_content",
        "notification",
        ["user_id", "notification_type", "content_id"],
        unique=True,
        postgresql_where=sa.text("content_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notification_user_type_content", table_name="notification")
    op.drop_index("idx_notification_user_created_at", table_name="notification")
    op.drop_table("notification")
