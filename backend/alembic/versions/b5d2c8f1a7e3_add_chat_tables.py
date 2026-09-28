"""add consumer AI chat tables

Revision ID: b5d2c8f1a7e3
Revises: 7c8e1a9b4d2f
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "b5d2c8f1a7e3"
down_revision: str | Sequence[str] | None = "7c8e1a9b4d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_session",
        sa.Column("chat_session_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("anchor_content_id", sa.BigInteger()),
        sa.Column("session_title", sa.String(length=30)),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'deleting')", name="ck_chat_session_status"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_chat_session_tap_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_content_id"], ["content.content_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("chat_session_id", name="pk_chat_session"),
    )
    op.create_index(
        "idx_chat_session_user_status_last_message",
        "chat_session",
        ["user_id", "status", sa.text("last_message_at DESC")],
    )
    op.create_index("idx_chat_session_expires_at", "chat_session", ["expires_at"])

    op.create_table(
        "chat_message",
        sa.Column("chat_message_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_role", sa.String(length=20), nullable=False),
        sa.Column("message_body", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column(
            "written_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("answer_sources", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("client_message_id", postgresql.UUID(as_uuid=True)),
        sa.Column("selected_text", sa.Text()),
        sa.Column("agent_run_id", sa.BigInteger()),
        sa.Column("result_type", sa.String(length=30)),
        sa.Column("notices", postgresql.JSONB(astext_type=sa.Text())),
        sa.CheckConstraint(
            "sender_role IN ('user', 'assistant', 'system')",
            name="ck_chat_message_sender_role",
        ),
        sa.CheckConstraint(
            "result_type IS NULL OR result_type IN "
            "('answer', 'topic_switch_suggested', 'needs_clarification', 'blocked')",
            name="ck_chat_message_result_type",
        ),
        sa.CheckConstraint(
            "sender_role <> 'user' OR result_type IS NULL",
            name="ck_chat_message_user_has_no_result",
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["chat_session.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_run.agent_run_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("chat_message_id", name="pk_chat_message"),
    )
    op.create_index(
        "uq_chat_message_session_client_message",
        "chat_message",
        ["chat_session_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "idx_chat_message_session_written_at",
        "chat_message",
        ["chat_session_id", "written_at"],
    )
    op.create_index(
        "idx_chat_message_agent_run_id", "chat_message", ["agent_run_id"]
    )

    op.create_table(
        "long_term_memory",
        sa.Column(
            "long_term_memory_id", sa.BigInteger(), autoincrement=True, nullable=False
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger()),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_chat_session_id", sa.BigInteger()),
        sa.Column("purge_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tap_id"], ["tap.tap_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_chat_session_id"],
            ["chat_session.chat_session_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("long_term_memory_id", name="pk_long_term_memory"),
    )
    op.create_index(
        "idx_long_term_memory_user_tap", "long_term_memory", ["user_id", "tap_id"]
    )
    op.create_index(
        "idx_long_term_memory_purge_at", "long_term_memory", ["purge_at"]
    )

    op.create_table(
        "user_operation",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="queued", nullable=False
        ),
        sa.Column("error_code", sa.String(length=50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "operation_type IN ('chat_deletion')", name="ck_user_operation_type"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_user_operation_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("operation_id", name="pk_user_operation"),
    )
    op.create_index(
        "idx_user_operation_user_created_at",
        "user_operation",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "chat_fact_cache",
        sa.Column(
            "chat_fact_cache_id", sa.BigInteger(), autoincrement=True, nullable=False
        ),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("lookup_key", sa.String(length=300), nullable=False),
        sa.Column("topic_id", sa.BigInteger()),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attribution", sa.String(length=200)),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "provider IN ('tmdb', 'musicbrainz', 'kobis', 'kma')",
            name="ck_chat_fact_cache_provider",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_fact_cache_id", name="pk_chat_fact_cache"),
        sa.UniqueConstraint(
            "provider", "lookup_key", name="uq_chat_fact_cache_provider_key"
        ),
    )
    op.create_index(
        "idx_chat_fact_cache_expires_at", "chat_fact_cache", ["expires_at"]
    )

    op.create_table(
        "chat_unanswered_question",
        sa.Column(
            "chat_unanswered_question_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("chat_message_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ('no_evidence', 'fact_lookup_failed', 'turn_limit')",
            name="ck_chat_unanswered_question_reason",
        ),
        sa.ForeignKeyConstraint(
            ["chat_message_id"],
            ["chat_message.chat_message_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "chat_unanswered_question_id", name="pk_chat_unanswered_question"
        ),
        sa.UniqueConstraint(
            "chat_message_id", name="uq_chat_unanswered_question_message"
        ),
    )
    op.create_index(
        "idx_chat_unanswered_question_topic_created_at",
        "chat_unanswered_question",
        ["topic_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chat_unanswered_question_topic_created_at",
        table_name="chat_unanswered_question",
    )
    op.drop_table("chat_unanswered_question")
    op.drop_index("idx_chat_fact_cache_expires_at", table_name="chat_fact_cache")
    op.drop_table("chat_fact_cache")
    op.drop_index("idx_user_operation_user_created_at", table_name="user_operation")
    op.drop_table("user_operation")
    op.drop_index("idx_long_term_memory_purge_at", table_name="long_term_memory")
    op.drop_index("idx_long_term_memory_user_tap", table_name="long_term_memory")
    op.drop_table("long_term_memory")
    op.drop_index("idx_chat_message_agent_run_id", table_name="chat_message")
    op.drop_index("idx_chat_message_session_written_at", table_name="chat_message")
    op.drop_index(
        "uq_chat_message_session_client_message", table_name="chat_message"
    )
    op.drop_table("chat_message")
    op.drop_index("idx_chat_session_expires_at", table_name="chat_session")
    op.drop_index(
        "idx_chat_session_user_status_last_message", table_name="chat_session"
    )
    op.drop_table("chat_session")
