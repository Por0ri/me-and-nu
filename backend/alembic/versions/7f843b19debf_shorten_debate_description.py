"""토론 설명을 100자로 줄이기

Revision ID: 7f843b19debf
Revises: 4dba45de1ad1
Create Date: 2026-11-25

2026-11-25 결정: 토론 카드의 더보기를 없애면서 설명은 100자까지만 받는다 (카드에는 3줄까지 보이고 넘으면 '…').
옛 규칙(200자)으로 저장된 긴 설명은 앞 99자(끝 공백·줄바꿈은 지움)에 '…'를 붙여 100자 안으로 자른 뒤
칼럼을 varchar(100)으로 줄인다. 먼저 잘라야 ALTER가 긴 값 때문에 거부되지 않는다.
downgrade는 칼럼만 200자로 넓힌다. 잘린 글은 되돌릴 수 없다.
되돌릴 때: 이 revision 전 이미지(설명 200자를 받는 코드)로 API를 돌리려면 먼저 이 revision이 있는 새 이미지의
일회성 컨테이너에서 `alembic downgrade 4dba45de1ad1`을 한다(칼럼만 넓혀서 데이터가 있어도 된다). 옛 이미지는
이 revision을 몰라 downgrade를 못 하고, 칼럼이 줄어든 채로 옛 API를 켜면 101~200자 설명을 저장할 때 500이 난다.
선택지 이름 입력(8자)과 관련 작품 수(2개)는 코드에서만 줄였다. 칼럼은 그대로이고 이미 있는 주제도 그대로 둔다.
PostgreSQL 전용이다 (SQLite 테스트는 a67ddf3dc55f까지만 만든다).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7f843b19debf"
down_revision: str | Sequence[str] | None = "4dba45de1ad1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MAX = 200
NEW_MAX = 100
# 운영 API가 토론 표를 오래 붙잡고 있으면 기다리지 않고 실패한다 (API가 멈춰 있지 않게). 다시 돌리면 된다.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    # 자르기와 칼럼 줄이기 사이에 새 긴 설명이 끼지 않게 먼저 잠근다. 표가 작아서 잠깐이다.
    op.execute("LOCK TABLE debate IN ACCESS EXCLUSIVE MODE")
    op.execute(rf"""
        UPDATE debate
        SET description = regexp_replace(left(description, {NEW_MAX - 1}), '\s+$', '') || '…'
        WHERE char_length(description) > {NEW_MAX}
    """)
    op.alter_column(
        "debate", "description",
        existing_type=sa.String(OLD_MAX), type_=sa.String(NEW_MAX), existing_nullable=True,
    )


def downgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    # 칼럼만 넓힌다. upgrade가 자른 설명은 그대로 남는다.
    op.alter_column(
        "debate", "description",
        existing_type=sa.String(NEW_MAX), type_=sa.String(OLD_MAX), existing_nullable=True,
    )
