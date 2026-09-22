"""rename domain to topic

Revision ID: 4e2b1a7c9d30
Revises: 9f3d8b2a7c41
Create Date: 2026-09-22
"""

from collections.abc import Sequence

from alembic import op


revision: str = "4e2b1a7c9d30"
down_revision: str | None = "9f3d8b2a7c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TOPIC_FOREIGN_KEYS = (
    ("subtopic", "fk_subtopic_domain_id", "fk_subtopic_topic_id"),
    (
        "source_site",
        "fk_source_site_domain_id",
        "fk_source_site_topic_id",
    ),
    (
        "creator_channel",
        "fk_creator_channel_domain_id",
        "fk_creator_channel_topic_id",
    ),
    ("agent_run", "fk_agent_run_domain_id", "fk_agent_run_topic_id"),
    ("draft", "fk_draft_domain_id", "fk_draft_topic_id"),
    ("content", "fk_content_domain_id", "fk_content_topic_id"),
)

TOPIC_INDEXES = (
    (
        "idx_agent_run_domain_status_created_at",
        "idx_agent_run_topic_status_created_at",
    ),
    (
        "idx_draft_domain_status_updated_at",
        "idx_draft_topic_status_updated_at",
    ),
    (
        "idx_content_domain_status_published_at",
        "idx_content_topic_status_published_at",
    ),
)


def _rename_constraint(table: str, old: str, new: str) -> None:
    op.execute(
        f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"'
    )


def _rename_index(old: str, new: str) -> None:
    op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    op.rename_table("domain", "topic")
    op.execute(
        'ALTER SEQUENCE "domain_domain_id_seq" '
        'RENAME TO "topic_topic_id_seq"'
    )

    op.alter_column("topic", "domain_id", new_column_name="topic_id")
    op.alter_column("topic", "domain_code", new_column_name="topic_code")
    op.alter_column("topic", "domain_name", new_column_name="topic_name")

    for table, _, _ in TOPIC_FOREIGN_KEYS:
        op.alter_column(table, "domain_id", new_column_name="topic_id")

    _rename_constraint("topic", "pk_domain", "pk_topic")
    _rename_constraint(
        "topic",
        "uq_domain_domain_code",
        "uq_topic_topic_code",
    )
    for table, old, new in TOPIC_FOREIGN_KEYS:
        _rename_constraint(table, old, new)
    _rename_constraint(
        "creator_channel",
        "uq_creator_channel_user_id_domain_id",
        "uq_creator_channel_user_id_topic_id",
    )

    for old, new in TOPIC_INDEXES:
        _rename_index(old, new)


def downgrade() -> None:
    for old, new in reversed(TOPIC_INDEXES):
        _rename_index(new, old)

    _rename_constraint(
        "creator_channel",
        "uq_creator_channel_user_id_topic_id",
        "uq_creator_channel_user_id_domain_id",
    )
    for table, old, new in reversed(TOPIC_FOREIGN_KEYS):
        _rename_constraint(table, new, old)
    _rename_constraint(
        "topic",
        "uq_topic_topic_code",
        "uq_domain_domain_code",
    )
    _rename_constraint("topic", "pk_topic", "pk_domain")

    for table, _, _ in reversed(TOPIC_FOREIGN_KEYS):
        op.alter_column(table, "topic_id", new_column_name="domain_id")

    op.alter_column("topic", "topic_name", new_column_name="domain_name")
    op.alter_column("topic", "topic_code", new_column_name="domain_code")
    op.alter_column("topic", "topic_id", new_column_name="domain_id")

    op.execute(
        'ALTER SEQUENCE "topic_topic_id_seq" '
        'RENAME TO "domain_domain_id_seq"'
    )
    op.rename_table("topic", "domain")

