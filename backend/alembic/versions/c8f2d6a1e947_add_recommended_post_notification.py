"""allow the recommended_post notification type

Revision ID: c8f2d6a1e947
Revises: 91c7a2e4b630
Create Date: 2026-11-11

Only widens the allowed notification types. No table or column changes.
Downgrade refuses while recommended_post rows exist, because the previous rule
rejects them and they are part of what members have received.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "c8f2d6a1e947"
down_revision: str | Sequence[str] | None = "91c7a2e4b630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = "'topic_new_post', 'creator_new_post', 'system_notice', 'follow', 'direct_message'"


def _allow(types: str) -> None:
    op.drop_constraint("ck_notification_type", "notification", type_="check")
    op.create_check_constraint("ck_notification_type", "notification", f"notification_type IN ({types})")


def upgrade() -> None:
    _allow(f"{PREVIOUS}, 'recommended_post'")


def downgrade() -> None:
    op.execute("LOCK TABLE notification IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM notification WHERE notification_type = 'recommended_post') THEN
                RAISE EXCEPTION 'Refusing to discard recommended_post notifications';
            END IF;
        END $$;
    """)
    _allow(PREVIOUS)
