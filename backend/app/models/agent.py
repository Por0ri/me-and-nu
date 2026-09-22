from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRun(Base):
    __tablename__ = "agent_run"

    agent_run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="SET NULL"),
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id"), nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    queue_task_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default="queued"
    )
    outcome: Mapped[str | None] = mapped_column(String(30))
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    node_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        Index(
            "uq_agent_run_queue_task_id",
            "queue_task_id",
            unique=True,
            postgresql_where=queue_task_id.is_not(None),
        ),
        Index(
            "idx_agent_run_requested_by_user_id",
            "requested_by_user_id",
        ),
        Index(
            "idx_agent_run_topic_status_created_at",
            "topic_id",
            "status",
            created_at.desc(),
        ),
        CheckConstraint(
            "status IN "
            "('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_run_status",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('not_target', 'insufficient_material', 'on_hold', "
            "'publish_candidate', 'rejected_archive')",
            name="ck_agent_run_outcome",
        ),
        CheckConstraint(
            "node_count >= 0",
            name="ck_agent_run_node_count",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL "
            "OR finished_at >= started_at",
            name="ck_agent_run_time_order",
        ),
    )


class AgentRunStep(Base):
    __tablename__ = "agent_run_step"

    agent_run_step_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_run.agent_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    step_name: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    model_name: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    step_input: Mapped[dict | None] = mapped_column(JSONB)
    step_result: Mapped[dict | None] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "step_order",
            name="uq_agent_run_step_agent_run_id_step_order",
        ),
        Index("idx_agent_run_step_agent_run_id", "agent_run_id"),
        CheckConstraint(
            "step_order >= 1",
            name="ck_agent_run_step_order",
        ),
        CheckConstraint(
            "attempt_no >= 1",
            name="ck_agent_run_step_attempt_no",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_agent_run_step_time_order",
        ),
    )


class AgentRunSource(Base):
    __tablename__ = "agent_run_source"

    agent_run_source_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_run.agent_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_site_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_site.source_site_id", ondelete="SET NULL"),
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(300))
    external_id: Mapped[str | None] = mapped_column(String(100))
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "source_url",
            name="uq_agent_run_source_agent_run_id_source_url",
        ),
        Index("idx_agent_run_source_agent_run_id", "agent_run_id"),
        CheckConstraint(
            "source_type IN ('metadata', 'context', 'article', 'rating')",
            name="ck_agent_run_source_type",
        ),
    )


class JudgmentLog(Base):
    __tablename__ = "judgment_log"

    judgment_log_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_run.agent_run_id")
    )
    draft_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("draft.draft_id")
    )
    content_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("content.content_id")
    )
    stage_name: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_code: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(80))
    input_summary: Mapped[str | None] = mapped_column(Text)
    judgment_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_adopted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    judged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    __table_args__ = (
        Index("idx_judgment_log_agent_run_id", "agent_run_id"),
        Index("idx_judgment_log_draft_id", "draft_id"),
        Index("idx_judgment_log_content_id", "content_id"),
        CheckConstraint(
            "agent_run_id IS NOT NULL OR draft_id IS NOT NULL "
            "OR content_id IS NOT NULL",
            name="ck_judgment_log_target",
        ),
        CheckConstraint(
            "stage_name IN "
            "('screening', 'editor', 'shape', 'judge', 'factcheck')",
            name="ck_judgment_log_stage_name",
        ),
        CheckConstraint(
            "attempt_no >= 1",
            name="ck_judgment_log_attempt_no",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_judgment_log_confidence",
        ),
    )
