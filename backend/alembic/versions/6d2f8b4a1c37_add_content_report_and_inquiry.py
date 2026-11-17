"""add content report (API-070) and My Page inquiry tables

Revision ID: 6d2f8b4a1c37
Revises: 150a601074af
Create Date: 2026-11-06

A withdrawn member's reports stay for content review with the reporter cleared;
their inquiries are deleted with the account. Downgrade refuses to discard
stored reports or inquiries.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "6d2f8b4a1c37"
down_revision: str | Sequence[str] | None = "150a601074af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_report",
        sa.Column("report_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("reason_code", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("status", sa.String(length=20), server_default="received", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "reason_code IN ('wrong_info', 'spoiler', 'rights', 'inappropriate', 'other')",
            name="ck_content_report_reason_code",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'reviewing', 'resolved', 'rejected')", name="ck_content_report_status"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], name="fk_content_report_user_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["content_id"], ["content.content_id"], name="fk_content_report_content_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("report_id", name="pk_content_report"),
        sa.UniqueConstraint("user_id", "content_id", name="uq_content_report_user_content"),
    )
    op.create_index("idx_content_report_content", "content_report", ["content_id"])
    op.create_index(
        "idx_content_report_status_created_at", "content_report", ["status", sa.text("created_at DESC")]
    )

    op.create_table(
        "inquiry",
        sa.Column("inquiry_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("inquiry_type", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="received", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("inquiry_type IN ('service', 'bug')", name="ck_inquiry_type"),
        sa.CheckConstraint("status IN ('received', 'answered', 'closed')", name="ck_inquiry_status"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], name="fk_inquiry_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("inquiry_id", name="pk_inquiry"),
    )
    op.create_index("idx_inquiry_user_created_at", "inquiry", ["user_id", sa.text("created_at DESC")])
    op.create_index("idx_inquiry_status_created_at", "inquiry", ["status", sa.text("created_at DESC")])


def downgrade() -> None:
    # Reports and inquiries are what members sent us. Block concurrent writes,
    # then refuse before any schema mutation.
    op.execute("LOCK TABLE content_report, inquiry IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM content_report) OR EXISTS (SELECT 1 FROM inquiry) THEN
                RAISE EXCEPTION 'Refusing to discard stored content reports or inquiries';
            END IF;
        END
        $$;
    """)
    op.drop_table("inquiry")
    op.drop_table("content_report")
