"""토론 개설 알림 종류(debate_opened) 허용

Revision ID: 94d2da693c10
Revises: a67ddf3dc55f
Create Date: 2026-11-19

알림 종류 규칙만 넓힌다. 표나 칼럼은 바꾸지 않는다. 어느 토론의 알림인지는 앞 revision에서 만든
debate_notification 표에 둔다. debate_opened 알림이 남아 있으면 downgrade를 거부한다. 이전 규칙은
그 알림을 받아들이지 않고, 회원이 이미 받은 알림이기 때문이다.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "94d2da693c10"
down_revision: str | Sequence[str] | None = "a67ddf3dc55f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = (
    "'topic_new_post', 'creator_new_post', 'system_notice', 'follow', 'direct_message', 'recommended_post'"
)


def _allow(types: str) -> None:
    op.drop_constraint("ck_notification_type", "notification", type_="check")
    op.create_check_constraint("ck_notification_type", "notification", f"notification_type IN ({types})")


def upgrade() -> None:
    _allow(f"{PREVIOUS}, 'debate_opened'")


def downgrade() -> None:
    op.execute("LOCK TABLE notification IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM notification WHERE notification_type = 'debate_opened') THEN
                RAISE EXCEPTION 'Refusing to discard debate_opened notifications';
            END IF;
        END $$;
    """)
    _allow(PREVIOUS)
