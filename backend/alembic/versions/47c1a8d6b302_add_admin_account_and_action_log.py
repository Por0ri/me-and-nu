"""Add administrator grants and action history from ERD V1.2.

Revision ID: 47c1a8d6b302
Revises: d7b3e9a1c452
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "47c1a8d6b302"
down_revision = "d7b3e9a1c452"
branch_labels = None
depends_on = None


def upgrade() -> None:
    primary_key_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    json_type = postgresql.JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite")
    op.create_table(
        "admin_account",
        sa.Column("admin_account_id", primary_key_type, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("granted_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("admin_account_id", name="pk_admin_account"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], name="fk_admin_account_user_id"),
        sa.ForeignKeyConstraint(
            ["granted_by_admin_id"], ["admin_account.admin_account_id"], name="fk_admin_account_granted_by_admin_id"
        ),
    )
    op.create_index(
        "uq_admin_account_user_id", "admin_account", ["user_id"], unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"), sqlite_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "admin_action_log",
        sa.Column("admin_action_log_id", primary_key_type, autoincrement=True, nullable=False),
        sa.Column("admin_account_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(40), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("before_value", json_type, nullable=True),
        sa.Column("after_value", json_type, nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("is_reverted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("acted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("admin_action_log_id", name="pk_admin_action_log"),
        sa.ForeignKeyConstraint(
            ["admin_account_id"], ["admin_account.admin_account_id"], name="fk_admin_action_log_admin_account_id"
        ),
    )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        # Take SQLite's write lock before checking both tables, including with legacy sqlite3 transaction control.
        op.execute("UPDATE admin_account SET role = role WHERE 0")
        has_history = op.get_bind().scalar(sa.text(
            "SELECT EXISTS(SELECT 1 FROM admin_account) OR EXISTS(SELECT 1 FROM admin_action_log)"
        ))
        if has_history:
            raise RuntimeError("Refusing to discard administrator grants or action history")
    else:
        op.execute("LOCK TABLE admin_account, admin_action_log IN ACCESS EXCLUSIVE MODE")
        op.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM admin_account) OR EXISTS (SELECT 1 FROM admin_action_log) THEN
                    RAISE EXCEPTION 'Refusing to discard administrator grants or action history';
                END IF;
            END $$;
        """)
    op.drop_table("admin_action_log")
    op.drop_table("admin_account")
