"""토론방 표 추가

Revision ID: a67ddf3dc55f
Revises: 47c1a8d6b302
Create Date: 2026-11-19

새 표만 만든다. 주제가 투표 중 → 진행 중 → 종료·소멸로 가는 debate, 연결 작품, 개설 투표,
회원별 투표권, 진영 선택, 방 채팅, 개설 알림과 토론의 연결, 토론 검색 기록이다. 기존 표는 바꾸지
않는다. 알림 종류 규칙은 다음 revision(94d2da693c10)에서 따로 넓힌다.
토론 데이터가 하나라도 있으면 downgrade를 거부한다. 설계: docs/DEBATE_BACKEND_DESIGN_2026-11-19.md
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "a67ddf3dc55f"
down_revision = "47c1a8d6b302"
branch_labels = None
depends_on = None

# 지우는 순서 (다른 표를 가리키는 표부터)
TABLES = (
    "debate_search_log",
    "debate_notification",
    "debate_message",
    "debate_side",
    "debate_vote_ticket",
    "debate_vote",
    "debate_work",
    "debate",
)
# debate를 가리키지 않는 표는 따로 확인한다. 나머지는 debate가 비면 함께 비어 있다.
HAS_DATA = (
    "EXISTS (SELECT 1 FROM debate) OR EXISTS (SELECT 1 FROM debate_vote_ticket)"
    " OR EXISTS (SELECT 1 FROM debate_search_log)"
)


def upgrade() -> None:
    id_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "debate",
        sa.Column("debate_id", id_type, autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("author_user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("side_a_label", sa.String(20), nullable=False),
        sa.Column("side_b_label", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), server_default="voting", nullable=False),
        sa.Column("vote_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("side_a_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("side_b_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("voting_ends_at", timestamp, nullable=False),
        sa.Column("opened_at", timestamp, nullable=True),
        sa.Column("closes_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("debate_id", name="pk_debate"),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], name="fk_debate_topic_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["user_account.user_id"], name="fk_debate_author_user_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("debate_id", "topic_id", name="uq_debate_id_topic_id"),
        sa.CheckConstraint("status IN ('voting', 'open', 'closed', 'expired')", name="ck_debate_status"),
        sa.CheckConstraint(
            "vote_count >= 0 AND side_a_count >= 0 AND side_b_count >= 0 AND message_count >= 0",
            name="ck_debate_counts",
        ),
        sa.CheckConstraint(
            "title <> '' AND side_a_label <> '' AND side_b_label <> '' AND side_a_label <> side_b_label",
            name="ck_debate_text",
        ),
        sa.CheckConstraint(
            "(status IN ('voting', 'expired') AND opened_at IS NULL AND closes_at IS NULL)"
            " OR (status IN ('open', 'closed') AND opened_at IS NOT NULL AND closes_at IS NOT NULL"
            " AND closes_at > opened_at)",
            name="ck_debate_room_period",
        ),
    )
    op.create_index("idx_debate_status_voting_ends", "debate", ["status", "voting_ends_at"])
    op.create_index("idx_debate_status_closes", "debate", ["status", "closes_at"])
    op.create_index("idx_debate_author_status", "debate", ["author_user_id", "status"])

    op.create_table(
        "debate_work",
        sa.Column("debate_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("debate_id", "entity_id", name="pk_debate_work"),
        sa.ForeignKeyConstraint(
            ["debate_id", "topic_id"], ["debate.debate_id", "debate.topic_id"],
            name="fk_debate_work_debate_topic", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"],
            name="fk_debate_work_entity_topic", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("debate_id", "position", name="uq_debate_work_debate_position"),
        sa.CheckConstraint("position >= 0", name="ck_debate_work_position"),
    )
    op.create_index("idx_debate_work_entity_topic", "debate_work", ["entity_id", "topic_id"])

    op.create_table(
        "debate_vote",
        sa.Column("debate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("debate_id", "user_id", name="pk_debate_vote"),
        sa.ForeignKeyConstraint(["debate_id"], ["debate.debate_id"], name="fk_debate_vote_debate_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], name="fk_debate_vote_user_id", ondelete="CASCADE"),
    )
    op.create_index("idx_debate_vote_user_created", "debate_vote", ["user_id", "created_at"])

    op.create_table(
        "debate_vote_ticket",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.SmallInteger(), nullable=False),
        sa.Column("refilled_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_debate_vote_ticket"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], name="fk_debate_vote_ticket_user_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint("balance >= 0", name="ck_debate_vote_ticket_balance"),
    )

    op.create_table(
        "debate_side",
        sa.Column("debate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("chosen_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("debate_id", "user_id", name="pk_debate_side"),
        sa.ForeignKeyConstraint(["debate_id"], ["debate.debate_id"], name="fk_debate_side_debate_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], name="fk_debate_side_user_id", ondelete="CASCADE"),
        sa.CheckConstraint("side IN ('a', 'b')", name="ck_debate_side_side"),
    )

    op.create_table(
        "debate_message",
        sa.Column("message_id", id_type, autoincrement=True, nullable=False),
        sa.Column("debate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(1), nullable=True),
        sa.Column("body", sa.String(300), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("message_id", name="pk_debate_message"),
        sa.ForeignKeyConstraint(
            ["debate_id"], ["debate.debate_id"], name="fk_debate_message_debate_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], name="fk_debate_message_user_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint("side IN ('a', 'b')", name="ck_debate_message_side"),
        sa.CheckConstraint("body <> ''", name="ck_debate_message_body"),
    )
    op.create_index("idx_debate_message_debate_message", "debate_message", ["debate_id", "message_id"])
    op.create_index(
        "idx_debate_message_user_debate_created", "debate_message", ["user_id", "debate_id", "created_at"]
    )

    op.create_table(
        "debate_notification",
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("debate_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("notification_id", name="pk_debate_notification"),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["notification.notification_id"],
            name="fk_debate_notification_notification_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["debate_id"], ["debate.debate_id"], name="fk_debate_notification_debate_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], name="fk_debate_notification_user_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("debate_id", "user_id", name="uq_debate_notification_debate_user"),
    )

    op.create_table(
        "debate_search_log",
        sa.Column("search_log_id", id_type, autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("search_text", sa.String(50), nullable=False),
        sa.Column("picked_entity_id", sa.BigInteger(), nullable=True),
        sa.Column("matched_entity_ids", json_type, nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("searched_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("data_source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("search_log_id", name="pk_debate_search_log"),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topic.topic_id"], name="fk_debate_search_log_topic_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["picked_entity_id"], ["entity.entity_id"], name="fk_debate_search_log_picked_entity_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("result_count >= 0", name="ck_debate_search_log_result_count"),
        sa.CheckConstraint(
            "data_source IN ('production', 'local_demo', 'automated_test', 'imported')",
            name="ck_debate_search_log_data_source",
        ),
    )
    op.create_index("idx_debate_search_log_searched", "debate_search_log", ["searched_at"])


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        # 확인하기 전에 SQLite 쓰기 잠금부터 잡는다 (예전 방식의 sqlite3 트랜잭션에서도).
        op.execute("UPDATE debate SET status = status WHERE 0")
        if op.get_bind().scalar(sa.text(f"SELECT {HAS_DATA}")):
            raise RuntimeError("Refusing to discard debate data")
    else:
        op.execute(f"LOCK TABLE {', '.join(TABLES)} IN ACCESS EXCLUSIVE MODE")
        op.execute(f"""
            DO $$ BEGIN
                IF {HAS_DATA} THEN
                    RAISE EXCEPTION 'Refusing to discard debate data';
                END IF;
            END $$;
        """)
    for table in TABLES:
        op.drop_table(table)
