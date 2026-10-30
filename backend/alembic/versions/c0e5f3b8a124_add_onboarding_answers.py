"""Add Q1/Q2 onboarding preferences without changing existing subscriptions.

Revision ID: c0e5f3b8a124
Revises: b9d4e2a7f013
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "c0e5f3b8a124"
down_revision: str | Sequence[str] | None = "b9d4e2a7f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_answers",
        sa.Column("tap_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("q1_choice", sa.String(length=32), nullable=False),
        sa.Column("q2_choice", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "q1_choice IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
            name="ck_onboarding_answers_q1_choice",
        ),
        sa.CheckConstraint(
            "q2_choice IN ('releases', 'characters', 'restock', 'events')",
            name="ck_onboarding_answers_q2_choice",
        ),
        sa.ForeignKeyConstraint(
            ["tap_id"],
            ["tap.tap_id"],
            name="fk_onboarding_answers_tap_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tap_id", name="pk_onboarding_answers"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_answers")
