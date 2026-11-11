"""add search_log: Home searches that actually ran

Revision ID: d7b3e9a1c452
Revises: c8f2d6a1e947
Create Date: 2026-11-11

Additive: one new table, nothing existing changes. Downgrade refuses while rows
exist, because the search history collected from members cannot be rebuilt.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d7b3e9a1c452"
down_revision: str | Sequence[str] | None = "c8f2d6a1e947"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_log",
        sa.Column("search_log_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("search_text", sa.String(50), nullable=False),
        sa.Column("picked_work_id", sa.BigInteger(), nullable=True),
        sa.Column("matched_work_ids", postgresql.JSONB(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("searched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("data_source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("search_log_id", name="pk_search_log"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], name="fk_search_log_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tap_id", "user_id"], ["tap.tap_id", "tap.user_id"], name="fk_search_log_tap_owner", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["picked_work_id"], ["entity.entity_id"], name="fk_search_log_picked_work_id", ondelete="SET NULL"),
        sa.CheckConstraint("result_count >= 0", name="ck_search_log_result_count"),
        sa.CheckConstraint(
            "data_source IN ('production', 'local_demo', 'automated_test', 'imported')",
            name="ck_search_log_data_source",
        ),
    )
    op.create_index("idx_search_log_user_searched", "search_log", ["user_id", "searched_at"])
    op.create_index("idx_search_log_tap_searched", "search_log", ["tap_id", "searched_at"])


def downgrade() -> None:
    op.execute("LOCK TABLE search_log IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM search_log) THEN
                RAISE EXCEPTION 'Refusing to discard stored search history';
            END IF;
        END $$;
    """)
    op.drop_table("search_log")
