"""Review writes using existing Content/Draft and PR #71's audit contract.

The lightweight table clause has no DDL/metadata registration. It cannot create
the proposed audit table; absence of that table aborts the whole transaction.
"""

from sqlalchemy import BigInteger, DateTime, JSON, String, Text, column, insert, select, table, update
from sqlalchemy.dialects.postgresql import JSONB

from app.models import Content, Draft

audit_log = table(
    "admin_action_log",
    column("admin_action_log_id", BigInteger),
    column("admin_account_id", BigInteger),
    column("action_type", String(40)), column("target_type", String(20)),
    column("target_id", BigInteger),
    column("before_value", JSONB().with_variant(JSON(), "sqlite")),
    column("after_value", JSONB().with_variant(JSON(), "sqlite")),
    column("rationale", Text), column("acted_at", DateTime(timezone=True)),
)


async def lock_content(db, content_id):
    return (await db.execute(select(
        Content.content_id, Content.source_draft_id, Content.status,
        Content.judgment_status, Content.published_at, Content.updated_at,
        Content.deleted_at, Content.ai_judgment_basis,
    ).where(Content.content_id == content_id).with_for_update())).mappings().one_or_none()


async def lock_draft(db, draft_id):
    return (await db.execute(select(
        Draft.draft_id, Draft.status, Draft.updated_at, Draft.factcheck_result,
    ).where(Draft.draft_id == draft_id).with_for_update())).mappings().one_or_none()


async def previous_action(db, *, content_id, admin_account_id, request_id):
    return (await db.execute(select(audit_log).where(
        audit_log.c.target_type == "content", audit_log.c.target_id == content_id,
        audit_log.c.admin_account_id == admin_account_id,
        audit_log.c.action_type.in_(["content.approve", "content.reject"]),
        audit_log.c.after_value["request_id"].as_string() == str(request_id),
    ))).mappings().one_or_none()


async def update_content(db, original, values):
    # CAS also detects a concurrent writer on engines without row locks.
    result = await db.execute(update(Content).where(
        Content.content_id == original["content_id"],
        Content.updated_at == original["updated_at"],
        Content.status == original["status"],
        Content.judgment_status == original["judgment_status"],
        Content.source_draft_id == original["source_draft_id"],
        Content.deleted_at.is_(None),
    ).values(**values).execution_options(synchronize_session=False))
    return result.rowcount == 1


async def update_draft(db, original, values):
    result = await db.execute(update(Draft).where(
        Draft.draft_id == original["draft_id"], Draft.status == original["status"],
        Draft.updated_at == original["updated_at"],
    ).values(**values).execution_options(synchronize_session=False))
    return result.rowcount == 1


async def append_audit(db, **values):
    return (await db.execute(insert(audit_log).values(**values)
                            .returning(audit_log.c.admin_action_log_id))).scalar_one()
