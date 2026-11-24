"""Store published inquiry answers without retaining their bodies in audit logs.

Revision ID: 83a2f9c7d104
Revises: 47c1a8d6b302
"""

import sqlalchemy as sa
from alembic import op

revision = "83a2f9c7d104"
down_revision = "47c1a8d6b302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inquiry_answer",
        sa.Column("inquiry_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("admin_account_id", sa.BigInteger(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("inquiry_id", name="pk_inquiry_answer"),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiry.inquiry_id"],
                                name="fk_inquiry_answer_inquiry_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["admin_account_id"], ["admin_account.admin_account_id"],
                                name="fk_inquiry_answer_admin_account_id"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 5000", name="ck_inquiry_answer_body_length"),
    )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        op.execute("UPDATE inquiry_answer SET body = body WHERE 0")
        if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM inquiry_answer)")):
            raise RuntimeError("Refusing to discard published inquiry answers")
    else:
        op.execute("LOCK TABLE inquiry_answer IN ACCESS EXCLUSIVE MODE")
        op.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM inquiry_answer) THEN
                    RAISE EXCEPTION 'Refusing to discard published inquiry answers';
                END IF;
            END $$;
        """)
    op.drop_table("inquiry_answer")
