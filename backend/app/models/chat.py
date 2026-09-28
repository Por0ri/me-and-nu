"""Consumer AI chat sessions, messages, memory and supporting records.

ERD V0.4.5 section 7 (chat_session, chat_message, long_term_memory) plus the
records the V1.0 chat API and the chatbot agent design need but the ERD does
not have yet (answer job links, deletion operations, fact lookup cache,
unanswered questions).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSession(Base):
    __tablename__ = "chat_session"

    chat_session_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 토픽 없는 대화는 받지 않는다(지침서 §5-1). ERD는 NULL 허용이지만 NOT NULL로 둔다.
    tap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    anchor_content_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="SET NULL")
    )
    session_title: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # 보관 만료 시각. LangGraph 체크포인터 정리 배치의 기준일이다.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_chat_session_tap_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active', 'deleting')", name="ck_chat_session_status"
        ),
        Index(
            "idx_chat_session_user_status_last_message",
            "user_id",
            "status",
            last_message_at.desc(),
        ),
        Index("idx_chat_session_expires_at", "expires_at"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    chat_message_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_session.chat_session_id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_body: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    written_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 답변 근거 출처: [{"contentId": 301, "url": "..."}]
    answer_sources: Mapped[list | None] = mapped_column(JSONB)
    # 같은 질문을 두 번 보내면 같은 작업을 돌려준다(API-052).
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # 사용자가 본문에서 고른 문단(FR-603-2).
    selected_text: Mapped[str | None] = mapped_column(Text)
    # 이 질문에 답을 만든 작업. 답변 작업은 agent_run을 같이 쓴다(agent_code='consumer_chat').
    agent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_run.agent_run_id", ondelete="SET NULL")
    )
    # 결과 종류(API-053): answer | topic_switch_suggested | needs_clarification | blocked
    result_type: Mapped[str | None] = mapped_column(String(30))
    # 고정 문구 안내 목록(민감 분야 안내, 시간 경고 등)
    notices: Mapped[list | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "sender_role IN ('user', 'assistant', 'system')",
            name="ck_chat_message_sender_role",
        ),
        CheckConstraint(
            "result_type IS NULL OR result_type IN "
            "('answer', 'topic_switch_suggested', 'needs_clarification', 'blocked')",
            name="ck_chat_message_result_type",
        ),
        CheckConstraint(
            "sender_role <> 'user' OR result_type IS NULL",
            name="ck_chat_message_user_has_no_result",
        ),
        Index(
            "uq_chat_message_session_client_message",
            "chat_session_id",
            "client_message_id",
            unique=True,
            postgresql_where=client_message_id.is_not(None),
        ),
        Index(
            "idx_chat_message_session_written_at",
            "chat_session_id",
            "written_at",
        ),
        Index("idx_chat_message_agent_run_id", "agent_run_id"),
    )


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"

    long_term_memory_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    tap_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tap.tap_id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_chat_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chat_session.chat_session_id", ondelete="SET NULL"),
    )
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_long_term_memory_user_tap", "user_id", "tap_id"),
        Index("idx_long_term_memory_purge_at", "purge_at"),
    )


class UserOperation(Base):
    """Async user-requested operations such as chat deletion (API-054, API-104)."""

    __tablename__ = "user_operation"

    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default="queued"
    )
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('chat_deletion')",
            name="ck_user_operation_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_user_operation_status",
        ),
        Index("idx_user_operation_user_created_at", "user_id", created_at.desc()),
    )


class ChatFactCache(Base):
    """Official API lookups (TMDB, MusicBrainz, KOBIS, 기상청) kept for reuse."""

    __tablename__ = "chat_fact_cache"

    chat_fact_cache_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(300), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="CASCADE")
    )
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 출처 표시 문구. 예: "JustWatch 제공"
    attribution: Mapped[str | None] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "provider", "lookup_key", name="uq_chat_fact_cache_provider_key"
        ),
        CheckConstraint(
            "provider IN ('tmdb', 'musicbrainz', 'kobis', 'kma')",
            name="ck_chat_fact_cache_provider",
        ),
        Index("idx_chat_fact_cache_expires_at", "expires_at"),
    )


class ChatUnansweredQuestion(Base):
    """Questions the chatbot could not answer from stored content."""

    __tablename__ = "chat_unanswered_question"

    chat_unanswered_question_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # 질문 원문은 메시지에 있다. 대화를 지우면 이 기록도 같이 지워진다.
    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "chat_message_id", name="uq_chat_unanswered_question_message"
        ),
        CheckConstraint(
            "reason IN ('no_evidence', 'fact_lookup_failed', 'turn_limit')",
            name="ck_chat_unanswered_question_reason",
        ),
        Index(
            "idx_chat_unanswered_question_topic_created_at",
            "topic_id",
            created_at.desc(),
        ),
    )
