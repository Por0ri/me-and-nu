"""add movie agent v0.4.5 tables

Revision ID: 9f3d8b2a7c41
Revises: d1c61ade4657
Create Date: 2026-09-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "9f3d8b2a7c41"
down_revision: str | Sequence[str] | None = "d1c61ade4657"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Vector(sa.types.UserDefinedType):
    """Migration-local pgvector type."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return "VECTOR"


def _upgrade_user_account() -> None:
    op.rename_table("users", "user_account")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'user_account'::regclass
                  AND conname = 'users_pkey'
            ) THEN
                ALTER TABLE user_account
                    RENAME CONSTRAINT users_pkey TO pk_user_account;
            ELSIF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'user_account'::regclass
                  AND conname = 'pk_users'
            ) THEN
                ALTER TABLE user_account
                    RENAME CONSTRAINT pk_users TO pk_user_account;
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_users_role",
        "user_account",
        type_="check",
    )
    op.drop_index(
        "ix_users_email",
        table_name="user_account",
    )

    op.alter_column(
        "user_account",
        "id",
        new_column_name="user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "user_account",
        "role",
        new_column_name="signup_channel",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        existing_server_default=sa.text("'consumer'"),
    )
    op.alter_column(
        "user_account",
        "created_at",
        new_column_name="joined_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column(
        "user_account",
        "email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.alter_column(
        "user_account",
        "hashed_password",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.drop_column("user_account", "updated_at")

    op.add_column(
        "user_account",
        sa.Column("provider_user_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_account",
        sa.Column(
            "account_status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_account",
        sa.Column("birth_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "user_account",
        sa.Column(
            "auth_provider",
            sa.String(length=20),
            server_default=sa.text("'local'"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_account",
        sa.Column("profile_image_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "user_account",
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "user_account",
        sa.Column(
            "withdrawn_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "user_account",
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
    )

    # 이전 테스트 모델에는 admin 역할이 허용됐다. 새 컬럼은 최초 가입 경로이므로
    # 기존 admin 값이 있으면 consumer로 보존 변환한 뒤 CHECK를 적용한다.
    op.execute(
        "UPDATE user_account "
        "SET signup_channel = 'consumer' "
        "WHERE signup_channel = 'admin'"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS users_id_seq "
        "RENAME TO user_account_user_id_seq"
    )

    op.create_unique_constraint(
        "uq_user_account_auth_provider_provider_user_id",
        "user_account",
        ["auth_provider", "provider_user_id"],
    )
    op.create_index(
        "uq_user_account_email",
        "user_account",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_user_account_signup_channel",
        "user_account",
        "signup_channel IN ('consumer', 'creator')",
    )
    op.create_check_constraint(
        "ck_user_account_status",
        "user_account",
        "account_status IN "
        "('active', 'warned', 'suspended', 'banned', 'withdrawn')",
    )
    op.create_check_constraint(
        "ck_user_account_auth_provider",
        "user_account",
        "auth_provider IN ('local', 'kakao', 'naver', 'apple')",
    )
    op.create_check_constraint(
        "ck_user_account_auth_identity",
        "user_account",
        "(auth_provider = 'local' AND email IS NOT NULL "
        "AND hashed_password IS NOT NULL "
        "AND provider_user_id IS NULL) "
        "OR (auth_provider <> 'local' "
        "AND provider_user_id IS NOT NULL)",
    )


def _create_catalog_tables() -> None:
    op.create_table(
        "domain",
        sa.Column("domain_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("domain_code", sa.String(length=50), nullable=False),
        sa.Column("domain_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("domain_id", name="pk_domain"),
        sa.UniqueConstraint("domain_code", name="uq_domain_domain_code"),
    )
    op.create_table(
        "subtopic",
        sa.Column(
            "subtopic_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_subtopic_id", sa.BigInteger(), nullable=True),
        sa.Column("subtopic_name", sa.String(length=100), nullable=False),
        sa.Column(
            "depth_level",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("linked_content_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_subtopic_domain_id",
        ),
        sa.ForeignKeyConstraint(
            ["parent_subtopic_id"],
            ["subtopic.subtopic_id"],
            name="fk_subtopic_parent_subtopic_id",
        ),
        sa.PrimaryKeyConstraint("subtopic_id", name="pk_subtopic"),
    )
    op.create_table(
        "source_site",
        sa.Column(
            "source_site_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("media_name", sa.String(length=150), nullable=False),
        sa.Column("collect_method", sa.String(length=20), nullable=False),
        sa.Column("site_url", sa.String(length=500), nullable=True),
        sa.Column("feed_url", sa.String(length=500), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_grade",
            sa.String(length=20),
            server_default=sa.text("'없음'"),
            nullable=False,
        ),
        sa.Column(
            "copyright_status",
            sa.String(length=20),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
        sa.Column("license_basis", sa.String(length=500), nullable=True),
        sa.Column(
            "is_collecting",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "last_collected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_source_site_domain_id",
        ),
        sa.PrimaryKeyConstraint("source_site_id", name="pk_source_site"),
    )
    op.create_index(
        "uq_source_site_feed_url",
        "source_site",
        ["feed_url"],
        unique=True,
        postgresql_where=sa.text("feed_url IS NOT NULL"),
    )
    op.create_table(
        "creator_channel",
        sa.Column(
            "creator_channel_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=100), nullable=False),
        sa.Column("channel_intro", sa.String(length=1000), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "follower_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "channel_status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "dm_receive_scope",
            sa.String(length=20),
            server_default=sa.text("'all'"),
            nullable=False,
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("style_card", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "channel_review_status",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column("channel_review_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_creator_channel_domain_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_creator_channel_user_id",
        ),
        sa.PrimaryKeyConstraint(
            "creator_channel_id",
            name="pk_creator_channel",
        ),
        sa.UniqueConstraint(
            "user_id",
            "domain_id",
            name="uq_creator_channel_user_id_domain_id",
        ),
    )
    op.create_table(
        "topic_cluster",
        sa.Column(
            "topic_cluster_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "representative_content_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column("subtopic_id", sa.BigInteger(), nullable=True),
        sa.Column("cluster_title", sa.String(length=300), nullable=True),
        sa.Column("cluster_summary", sa.Text(), nullable=True),
        sa.Column(
            "member_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("centroid_vector", Vector(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subtopic_id"],
            ["subtopic.subtopic_id"],
            name="fk_topic_cluster_subtopic_id",
        ),
        sa.PrimaryKeyConstraint(
            "topic_cluster_id",
            name="pk_topic_cluster",
        ),
    )


def _create_agent_tables() -> None:
    op.create_table(
        "agent_run",
        sa.Column(
            "agent_run_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_code", sa.String(length=50), nullable=False),
        sa.Column("agent_version", sa.String(length=40), nullable=False),
        sa.Column("queue_task_id", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=30), nullable=True),
        sa.Column(
            "input_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "output_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "node_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "node_count >= 0",
            name="ck_agent_run_node_count",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('not_target', 'insufficient_material', 'on_hold', "
            "'publish_candidate', 'rejected_archive')",
            name="ck_agent_run_outcome",
        ),
        sa.CheckConstraint(
            "status IN "
            "('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_run_status",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL "
            "OR finished_at >= started_at",
            name="ck_agent_run_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_agent_run_domain_id",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user_account.user_id"],
            name="fk_agent_run_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("agent_run_id", name="pk_agent_run"),
    )
    op.create_index(
        "uq_agent_run_queue_task_id",
        "agent_run",
        ["queue_task_id"],
        unique=True,
        postgresql_where=sa.text("queue_task_id IS NOT NULL"),
    )
    op.create_index(
        "idx_agent_run_requested_by_user_id",
        "agent_run",
        ["requested_by_user_id"],
        unique=False,
    )
    op.create_index(
        "idx_agent_run_domain_status_created_at",
        "agent_run",
        ["domain_id", "status", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "agent_run_step",
        sa.Column(
            "agent_run_step_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_order", sa.SmallInteger(), nullable=False),
        sa.Column("step_name", sa.String(length=30), nullable=False),
        sa.Column(
            "attempt_no",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column(
            "step_input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "step_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("decision", sa.String(length=30), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_no >= 1",
            name="ck_agent_run_step_attempt_no",
        ),
        sa.CheckConstraint(
            "step_order >= 1",
            name="ck_agent_run_step_order",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_agent_run_step_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.agent_run_id"],
            name="fk_agent_run_step_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "agent_run_step_id",
            name="pk_agent_run_step",
        ),
        sa.UniqueConstraint(
            "agent_run_id",
            "step_order",
            name="uq_agent_run_step_agent_run_id_step_order",
        ),
    )
    op.create_index(
        "idx_agent_run_step_agent_run_id",
        "agent_run_step",
        ["agent_run_id"],
        unique=False,
    )
    op.create_table(
        "agent_run_source",
        sa.Column(
            "agent_run_source_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_site_id", sa.BigInteger(), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("source_title", sa.String(length=300), nullable=True),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("evidence_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('metadata', 'context', 'article', 'rating')",
            name="ck_agent_run_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.agent_run_id"],
            name="fk_agent_run_source_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_site.source_site_id"],
            name="fk_agent_run_source_source_site_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "agent_run_source_id",
            name="pk_agent_run_source",
        ),
        sa.UniqueConstraint(
            "agent_run_id",
            "source_url",
            name="uq_agent_run_source_agent_run_id_source_url",
        ),
    )
    op.create_index(
        "idx_agent_run_source_agent_run_id",
        "agent_run_source",
        ["agent_run_id"],
        unique=False,
    )


def _create_content_tables() -> None:
    op.create_table(
        "draft",
        sa.Column(
            "draft_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("creator_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "production_type",
            sa.String(length=20),
            server_default=sa.text("'human'"),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(length=30), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "structure_template",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "structure_template_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "factcheck_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'writing'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("similar_draft_warning", sa.Text(), nullable=True),
        sa.Column("similar_draft_score", sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "agent_run_id IS NOT NULL OR creator_channel_id IS NOT NULL",
            name="ck_draft_owner",
        ),
        sa.CheckConstraint(
            "production_type IN ('human', 'ai', 'hybrid')",
            name="ck_draft_production_type",
        ),
        sa.CheckConstraint(
            "similar_draft_score IS NULL "
            "OR similar_draft_score BETWEEN 0 AND 1",
            name="ck_draft_similarity_score",
        ),
        sa.CheckConstraint(
            "status IN "
            "('writing', 'checking', 'approved', 'published', 'discarded')",
            name="ck_draft_status",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.agent_run_id"],
            name="fk_draft_agent_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["creator_channel_id"],
            ["creator_channel.creator_channel_id"],
            name="fk_draft_creator_channel_id",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_draft_domain_id",
        ),
        sa.PrimaryKeyConstraint("draft_id", name="pk_draft"),
    )
    op.create_index(
        "uq_draft_agent_run_id",
        "draft",
        ["agent_run_id"],
        unique=True,
        postgresql_where=sa.text("agent_run_id IS NOT NULL"),
    )
    op.create_index(
        "idx_draft_domain_status_updated_at",
        "draft",
        ["domain_id", "status", sa.text("updated_at DESC")],
        unique=False,
    )
    op.create_table(
        "content",
        sa.Column(
            "content_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("source_draft_id", sa.BigInteger(), nullable=True),
        sa.Column("creator_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "production_type",
            sa.String(length=20),
            server_default=sa.text("'human'"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "keyword",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("content_type", sa.String(length=30), nullable=True),
        sa.Column("difficulty_level", sa.SmallInteger(), nullable=True),
        sa.Column("promotional_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("harmful_score", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "judgment_status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "ai_judgment_basis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "is_sponsored",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "harmful_score IS NULL OR harmful_score BETWEEN 0 AND 1",
            name="ck_content_harmful_score",
        ),
        sa.CheckConstraint(
            "judgment_status IN "
            "('pending', 'confirmed', 'needs_review', 'failed')",
            name="ck_content_judgment_status",
        ),
        sa.CheckConstraint(
            "production_type IN ('human', 'ai', 'hybrid')",
            name="ck_content_production_type",
        ),
        sa.CheckConstraint(
            "promotional_score IS NULL "
            "OR promotional_score BETWEEN 0 AND 1",
            name="ck_content_promotional_score",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'hidden', 'pending_review', 'deleted')",
            name="ck_content_status",
        ),
        sa.ForeignKeyConstraint(
            ["creator_channel_id"],
            ["creator_channel.creator_channel_id"],
            name="fk_content_creator_channel_id",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_content_domain_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["draft.draft_id"],
            name="fk_content_source_draft_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_cluster_id"],
            ["topic_cluster.topic_cluster_id"],
            name="fk_content_topic_cluster_id",
        ),
        sa.PrimaryKeyConstraint("content_id", name="pk_content"),
    )
    op.create_index(
        "uq_content_source_draft_id",
        "content",
        ["source_draft_id"],
        unique=True,
        postgresql_where=sa.text("source_draft_id IS NOT NULL"),
    )
    op.create_index(
        "idx_content_domain_status_published_at",
        "content",
        ["domain_id", "status", sa.text("published_at DESC")],
        unique=False,
    )
    op.create_foreign_key(
        "fk_topic_cluster_representative_content_id",
        "topic_cluster",
        "content",
        ["representative_content_id"],
        ["content_id"],
    )
    op.create_table(
        "judgment_log",
        sa.Column(
            "judgment_log_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("draft_id", sa.BigInteger(), nullable=True),
        sa.Column("content_id", sa.BigInteger(), nullable=True),
        sa.Column("stage_name", sa.String(length=30), nullable=False),
        sa.Column("agent_code", sa.String(length=50), nullable=False),
        sa.Column(
            "attempt_no",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column(
            "judgment_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "is_adopted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "judged_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.CheckConstraint(
            "attempt_no >= 1",
            name="ck_judgment_log_attempt_no",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_judgment_log_confidence",
        ),
        sa.CheckConstraint(
            "stage_name IN "
            "('screening', 'editor', 'shape', 'judge', 'factcheck')",
            name="ck_judgment_log_stage_name",
        ),
        sa.CheckConstraint(
            "agent_run_id IS NOT NULL OR draft_id IS NOT NULL "
            "OR content_id IS NOT NULL",
            name="ck_judgment_log_target",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.agent_run_id"],
            name="fk_judgment_log_agent_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["content.content_id"],
            name="fk_judgment_log_content_id",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["draft.draft_id"],
            name="fk_judgment_log_draft_id",
        ),
        sa.PrimaryKeyConstraint(
            "judgment_log_id",
            name="pk_judgment_log",
        ),
    )
    op.create_index(
        "idx_judgment_log_agent_run_id",
        "judgment_log",
        ["agent_run_id"],
        unique=False,
    )
    op.create_index(
        "idx_judgment_log_content_id",
        "judgment_log",
        ["content_id"],
        unique=False,
    )
    op.create_index(
        "idx_judgment_log_draft_id",
        "judgment_log",
        ["draft_id"],
        unique=False,
    )
    op.create_table(
        "content_source",
        sa.Column(
            "content_source_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_run_source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_site_id", sa.BigInteger(), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("source_title", sa.String(length=300), nullable=True),
        sa.Column(
            "source_role",
            sa.String(length=20),
            server_default=sa.text("'material'"),
            nullable=False,
        ),
        sa.Column("citation_order", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "citation_order IS NULL OR citation_order >= 1",
            name="ck_content_source_citation_order",
        ),
        sa.CheckConstraint(
            "source_role IN ('primary', 'material', 'citation')",
            name="ck_content_source_role",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_source_id"],
            ["agent_run_source.agent_run_source_id"],
            name="fk_content_source_agent_run_source_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["content.content_id"],
            name="fk_content_source_content_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_site_id"],
            ["source_site.source_site_id"],
            name="fk_content_source_source_site_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "content_source_id",
            name="pk_content_source",
        ),
        sa.UniqueConstraint(
            "content_id",
            "source_url",
            name="uq_content_source_content_id_source_url",
        ),
    )
    op.create_index(
        "idx_content_source_content_id",
        "content_source",
        ["content_id"],
        unique=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    _upgrade_user_account()
    _create_catalog_tables()
    _create_agent_tables()
    _create_content_tables()


def _downgrade_user_account() -> None:
    op.drop_constraint(
        "ck_user_account_auth_identity",
        "user_account",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_account_auth_provider",
        "user_account",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_account_status",
        "user_account",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_account_signup_channel",
        "user_account",
        type_="check",
    )
    op.drop_index(
        "uq_user_account_email",
        table_name="user_account",
    )
    op.drop_constraint(
        "uq_user_account_auth_provider_provider_user_id",
        "user_account",
        type_="unique",
    )

    # 소셜 계정이나 NULL 인증 값이 남아 있으면 이 변경은 실패하여 데이터
    # 손실 없이 중단된다. downgrade에서 해당 행을 자동 삭제하지 않는다.
    op.alter_column(
        "user_account",
        "email",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "user_account",
        "hashed_password",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    op.drop_column("user_account", "withdrawal_reason")
    op.drop_column("user_account", "withdrawn_at")
    op.drop_column("user_account", "last_login_at")
    op.drop_column("user_account", "profile_image_url")
    op.drop_column("user_account", "auth_provider")
    op.drop_column("user_account", "birth_date")
    op.drop_column("user_account", "account_status")
    op.drop_column("user_account", "provider_user_id")
    op.add_column(
        "user_account",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.alter_column(
        "user_account",
        "joined_at",
        new_column_name="created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column(
        "user_account",
        "signup_channel",
        new_column_name="role",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        existing_server_default=sa.text("'consumer'"),
    )
    op.alter_column(
        "user_account",
        "user_id",
        new_column_name="id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS user_account_user_id_seq "
        "RENAME TO users_id_seq"
    )
    op.rename_table("user_account", "users")
    op.execute(
        "ALTER TABLE users "
        "RENAME CONSTRAINT pk_user_account TO pk_users"
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('consumer', 'creator', 'admin')",
    )
    op.create_index(
        "ix_users_email",
        "users",
        ["email"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_content_source_content_id",
        table_name="content_source",
    )
    op.drop_table("content_source")

    op.drop_index(
        "idx_judgment_log_draft_id",
        table_name="judgment_log",
    )
    op.drop_index(
        "idx_judgment_log_content_id",
        table_name="judgment_log",
    )
    op.drop_index(
        "idx_judgment_log_agent_run_id",
        table_name="judgment_log",
    )
    op.drop_table("judgment_log")

    op.drop_constraint(
        "fk_topic_cluster_representative_content_id",
        "topic_cluster",
        type_="foreignkey",
    )
    op.drop_index(
        "idx_content_domain_status_published_at",
        table_name="content",
    )
    op.drop_index(
        "uq_content_source_draft_id",
        table_name="content",
    )
    op.drop_table("content")

    op.drop_index(
        "idx_draft_domain_status_updated_at",
        table_name="draft",
    )
    op.drop_index(
        "uq_draft_agent_run_id",
        table_name="draft",
    )
    op.drop_table("draft")

    op.drop_index(
        "idx_agent_run_source_agent_run_id",
        table_name="agent_run_source",
    )
    op.drop_table("agent_run_source")
    op.drop_index(
        "idx_agent_run_step_agent_run_id",
        table_name="agent_run_step",
    )
    op.drop_table("agent_run_step")
    op.drop_index(
        "idx_agent_run_domain_status_created_at",
        table_name="agent_run",
    )
    op.drop_index(
        "idx_agent_run_requested_by_user_id",
        table_name="agent_run",
    )
    op.drop_index(
        "uq_agent_run_queue_task_id",
        table_name="agent_run",
    )
    op.drop_table("agent_run")

    op.drop_table("topic_cluster")
    op.drop_table("creator_channel")
    op.drop_index(
        "uq_source_site_feed_url",
        table_name="source_site",
    )
    op.drop_table("source_site")
    op.drop_table("subtopic")
    op.drop_table("domain")

    _downgrade_user_account()

    # vector 확장은 다른 스키마/기능이 공유할 수 있어 자동 삭제하지 않는다.
