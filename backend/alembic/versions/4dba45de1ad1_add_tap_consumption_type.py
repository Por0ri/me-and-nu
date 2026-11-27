"""add tap_consumption_type: onboarding Q3 consumption types in ranked order

Revision ID: 4dba45de1ad1
Revises: 94d2da693c10
Create Date: 2026-11-24

Additive: one new table, nothing existing changes. onboarding_answers.consumption_type
stays rank 1 (the production main code and the admin screens read only that column),
so existing answers are not copied here; a missing ranking reads as that one answer.
Downgrade refuses while rows exist, because the rankings members chose cannot be rebuilt.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "4dba45de1ad1"
down_revision: str | Sequence[str] | None = "94d2da693c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_consumption_type",
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("consumption_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tap_id", "rank", name="pk_tap_consumption_type"),
        sa.ForeignKeyConstraint(["tap_id"], ["tap.tap_id"], name="fk_tap_consumption_type_tap_id", ondelete="CASCADE"),
        sa.UniqueConstraint("tap_id", "consumption_type", name="uq_tap_consumption_type_tap_type"),
        sa.CheckConstraint("rank BETWEEN 1 AND 6", name="ck_tap_consumption_type_rank"),
        sa.CheckConstraint(
            "consumption_type IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
            name="ck_tap_consumption_type_consumption_type",
        ),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE tap_consumption_type IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM tap_consumption_type) THEN
                RAISE EXCEPTION 'Refusing to discard saved onboarding Q3 rankings';
            END IF;
        END $$;
    """)
    op.drop_table("tap_consumption_type")
