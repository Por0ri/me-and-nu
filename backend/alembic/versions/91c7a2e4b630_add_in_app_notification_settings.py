"""Add in-app preferences under the PM implementation baseline, not team sign-off.

Revision ID: 91c7a2e4b630
Revises: b3e7a5c19d42
"""

import sqlalchemy as sa
from alembic import op

revision = "91c7a2e4b630"
down_revision = "b3e7a5c19d42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Constant defaults backfill existing rows without rewriting legacy choices/timestamps.
    op.add_column("notification_setting", sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("notification_setting", sa.Column("night_in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("notification_setting", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_notification_setting_version", "notification_setting", "version >= 1")
    # Pending members still get their row through the existing onboarding transaction.
    # Never overwrite a row or reinterpret report_result/creator_new_post/saved_resurface/marketing.
    op.execute("""
        INSERT INTO notification_setting (user_id)
        SELECT user_id FROM user_account WHERE onboarding_completed_at IS NOT NULL
        ON CONFLICT (user_id) DO NOTHING
    """)


def downgrade() -> None:
    # Do not silently discard an explicitly saved preference, even if toggled back.
    op.execute("LOCK TABLE notification_setting IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM notification_setting
                       WHERE version <> 1 OR NOT in_app_enabled OR night_in_app_enabled) THEN
                RAISE EXCEPTION 'Refusing to discard saved in-app notification preferences';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_notification_setting_version", "notification_setting", type_="check")
    op.drop_column("notification_setting", "version")
    op.drop_column("notification_setting", "night_in_app_enabled")
    op.drop_column("notification_setting", "in_app_enabled")
