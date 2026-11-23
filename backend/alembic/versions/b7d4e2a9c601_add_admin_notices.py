"""Persist administrator notices, schedules and per-recipient delivery identity.

Revision ID: b7d4e2a9c601
Revises: 83a2f9c7d104
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d4e2a9c601"
down_revision = "83a2f9c7d104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notice",
        sa.Column("notice_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_account.admin_account_id"],
                                name="fk_notice_created_by_admin_id"),
        sa.ForeignKeyConstraint(["updated_by_admin_id"], ["admin_account.admin_account_id"],
                                name="fk_notice_updated_by_admin_id"),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 100", name="ck_notice_title_length"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 5000", name="ck_notice_body_length"),
        sa.CheckConstraint(
            "(status = 'draft' AND scheduled_at IS NULL AND published_at IS NULL) OR "
            "(status = 'scheduled' AND scheduled_at IS NOT NULL AND published_at IS NULL) OR "
            "(status = 'published' AND published_at IS NOT NULL "
            "AND (scheduled_at IS NULL OR published_at >= scheduled_at))",
            name="ck_notice_publication_state",
        ),
    )
    op.create_index("idx_notice_status_schedule", "notice", ["status", "scheduled_at", "notice_id"])
    op.create_index("idx_notice_created", "notice", [sa.text("created_at DESC"), sa.text("notice_id DESC")])
    # Batch mode also permits a focused empty-table SQLite migration rehearsal.
    with op.batch_alter_table("notification") as batch:
        batch.add_column(sa.Column("notice_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key("fk_notification_notice_id", "notice", ["notice_id"], ["notice_id"])
        batch.create_check_constraint("ck_notification_notice_type",
                                      "notice_id IS NULL OR notification_type = 'system_notice'")
    op.create_index("uq_notification_user_notice", "notification", ["user_id", "notice_id"],
                    unique=True, postgresql_where=sa.text("notice_id IS NOT NULL"),
                    sqlite_where=sa.text("notice_id IS NOT NULL"))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("LOCK TABLE notice, notification IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM notice)")):
        raise RuntimeError("Refusing to discard saved or published administrator notices")
    op.drop_index("uq_notification_user_notice", table_name="notification")
    with op.batch_alter_table("notification") as batch:
        batch.drop_constraint("ck_notification_notice_type", type_="check")
        batch.drop_constraint("fk_notification_notice_id", type_="foreignkey")
        batch.drop_column("notice_id")
    op.drop_index("idx_notice_created", table_name="notice")
    op.drop_index("idx_notice_status_schedule", table_name="notice")
    op.drop_table("notice")
