"""add persistent V1 sessions, onboarding and content interaction tables

Revision ID: 7c8e1a9b4d2f
Revises: 4e2b1a7c9d30
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "7c8e1a9b4d2f"
down_revision: str | Sequence[str] | None = "4e2b1a7c9d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_account",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True)),
    )
    op.add_column("content", sa.Column("image_url", sa.String(length=1000)))

    op.create_table(
        "auth_session",
        sa.Column("auth_session_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column(
            "session_state",
            sa.String(length=20),
            server_default="anonymous",
            nullable=False,
        ),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "session_state IN ('anonymous', 'onboarding_pending', 'active')",
            name="ck_auth_session_state",
        ),
        sa.CheckConstraint(
            "(session_state = 'anonymous' AND user_id IS NULL) "
            "OR (session_state <> 'anonymous' AND user_id IS NOT NULL)",
            name="ck_auth_session_state_user",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("auth_session_id", name="pk_auth_session"),
        sa.UniqueConstraint("session_token_hash", name="uq_auth_session_token_hash"),
    )
    op.create_index(
        "idx_auth_session_user_expires_revoked",
        "auth_session",
        ["user_id", "expires_at", "revoked_at"],
    )
    op.create_index(
        "idx_auth_session_expires_revoked",
        "auth_session",
        ["expires_at", "revoked_at"],
    )

    op.create_table(
        "policy",
        sa.Column("policy_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("policy_type", sa.String(length=30), nullable=False),
        sa.Column("policy_version", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("content_url", sa.String(length=1000)),
        sa.Column("is_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content IS NOT NULL OR content_url IS NOT NULL",
            name="ck_policy_has_content",
        ),
        sa.PrimaryKeyConstraint("policy_id", name="pk_policy"),
        sa.UniqueConstraint("policy_type", "policy_version", name="uq_policy_type_version"),
    )
    op.create_index(
        "idx_policy_active_type_effective",
        "policy",
        ["is_active", "policy_type", sa.text("effective_at DESC")],
    )

    op.create_table(
        "consent_history",
        sa.Column("consent_history_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("consent_type", sa.String(length=30), nullable=False),
        sa.Column("is_agreed", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(length=30), nullable=False),
        sa.Column("agreed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("consent_history_id", name="pk_consent_history"),
    )
    op.create_index(
        "idx_consent_history_user_type_latest",
        "consent_history",
        [
            "user_id",
            "consent_type",
            sa.text("created_at DESC"),
            sa.text("consent_history_id DESC"),
        ],
    )

    op.create_table(
        "notification_setting",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("report_result", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("creator_new_post", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("saved_resurface", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("marketing", sa.Boolean(), server_default=sa.false(), nullable=False),
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
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_notification_setting"),
    )

    op.create_table(
        "tap",
        sa.Column("tap_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topic.topic_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tap_id", name="pk_tap"),
        sa.UniqueConstraint("tap_id", "user_id", name="uq_tap_id_user_id"),
    )
    op.create_index(
        "uq_tap_active_user_topic",
        "tap",
        ["user_id", "topic_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("idx_tap_user_deleted", "tap", ["user_id", "deleted_at"])

    op.create_table(
        "tap_topic",
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("subtopic_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "subscribed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tap_id"], ["tap.tap_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subtopic_id"], ["subtopic.subtopic_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tap_id", "subtopic_id", name="pk_tap_topic"),
    )
    op.create_index("idx_tap_topic_subtopic_id", "tap_topic", ["subtopic_id"])

    op.create_table(
        "content_tag",
        sa.Column("content_tag_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("subtopic_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_id"], ["content.content_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topic.topic_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["subtopic_id"], ["subtopic.subtopic_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("content_tag_id", name="pk_content_tag"),
        sa.UniqueConstraint(
            "content_id", "subtopic_id", name="uq_content_tag_content_subtopic"
        ),
    )
    op.create_index(
        "idx_content_tag_subtopic_content",
        "content_tag",
        ["subtopic_id", "content_id"],
    )
    op.create_index(
        "idx_content_tag_topic_content", "content_tag", ["topic_id", "content_id"]
    )

    op.create_table(
        "content_reaction",
        sa.Column("content_reaction_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("ox_feedback", sa.String(length=1)),
        sa.Column("is_liked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("liked_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "ox_feedback IN ('O', 'X')", name="ck_content_reaction_ox_feedback"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_content_reaction_tap_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_id"], ["content.content_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("content_reaction_id", name="pk_content_reaction"),
        sa.UniqueConstraint(
            "user_id", "tap_id", "content_id", name="uq_content_reaction_user_tap_content"
        ),
    )
    op.create_index(
        "idx_content_reaction_user_content",
        "content_reaction",
        ["user_id", "content_id"],
    )

    op.create_table(
        "saved_item",
        sa.Column("saved_item_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="kept", nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "saved_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('kept', 'resurface_off', 'deleted')",
            name="ck_saved_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["content_id"], ["content.content_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_saved_item_tap_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("saved_item_id", name="pk_saved_item"),
        sa.UniqueConstraint(
            "user_id", "content_id", "tap_id", name="uq_saved_item_user_content_tap"
        ),
    )
    op.create_index(
        "idx_saved_item_user_status_saved_at",
        "saved_item",
        ["user_id", "status", sa.text("saved_at DESC")],
    )


def downgrade() -> None:
    # Only structures introduced in this revision are removed. Existing content,
    # users, catalog and agent storage are left in place.
    op.drop_index("idx_saved_item_user_status_saved_at", table_name="saved_item")
    op.drop_table("saved_item")
    op.drop_index("idx_content_reaction_user_content", table_name="content_reaction")
    op.drop_table("content_reaction")
    op.drop_index("idx_content_tag_topic_content", table_name="content_tag")
    op.drop_index("idx_content_tag_subtopic_content", table_name="content_tag")
    op.drop_table("content_tag")
    op.drop_index("idx_tap_topic_subtopic_id", table_name="tap_topic")
    op.drop_table("tap_topic")
    op.drop_index("idx_tap_user_deleted", table_name="tap")
    op.drop_index("uq_tap_active_user_topic", table_name="tap")
    op.drop_table("tap")
    op.drop_table("notification_setting")
    op.drop_index("idx_consent_history_user_type_latest", table_name="consent_history")
    op.drop_table("consent_history")
    op.drop_index("idx_policy_active_type_effective", table_name="policy")
    op.drop_table("policy")
    op.drop_index("idx_auth_session_expires_revoked", table_name="auth_session")
    op.drop_index("idx_auth_session_user_expires_revoked", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_column("content", "image_url")
    op.drop_column("user_account", "onboarding_completed_at")
