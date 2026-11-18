"""Importer ownership rules after an administrator's committed review.

SQLite checks data preservation and batch rollback here; actual row-lock
contention is exercised by test_admin_postgres_integration.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AdminAccount, AdminActionLog, AgentRun, Content, Draft
from app.seeds import carousel_contents as seed
from test_carousel_import import (
    AsyncAdapter, LocalPreviewAdapter, NOW, counts, engine, prepare, raw_bundle, review,
)  # noqa: F401 -- shared isolated synthetic fixtures


def record_admin_decision(session, action):
    """Only importer policy fixtures; no fake claim of exercising the review API."""
    content = session.scalars(select(Content)).one()
    draft = session.scalars(select(Draft)).one()
    session.execute(text("INSERT INTO user_account(user_id) VALUES (1)"))
    grant = AdminAccount(user_id=1, role="operator")
    session.add(grant)
    session.flush()
    content.status = "active" if action == "approve" else "hidden"
    content.judgment_status = "confirmed" if action == "approve" else "failed"
    draft.status = "published" if action == "approve" else "discarded"
    if action == "approve":
        content.published_at = NOW
        for row, name in ((content, "ai_judgment_basis"), (draft, "factcheck_result")):
            setattr(row, name, {key: value for key, value in getattr(row, name).items()
                                if key not in {"preview", "local_test_preview", "checking_test_preview"}})
    session.add(AdminActionLog(admin_account_id=grant.admin_account_id,
        action_type=f"content.{action}", target_type="content", target_id=content.content_id,
        before_value={}, after_value={}, rationale="Isolated importer protection fixture"))
    session.flush()


def snapshot(session):
    content = session.scalars(select(Content)).one()
    draft = session.scalars(select(Draft)).one()
    run = session.scalars(select(AgentRun)).one()
    return deepcopy({
        "content": (content.status, content.judgment_status, content.published_at,
                    content.updated_at, content.ai_judgment_basis),
        "draft": (draft.status, draft.updated_at, draft.factcheck_result),
        "run": (run.outcome, run.output_payload),
        "counts": counts(session),
        "audit": [(row.admin_action_log_id, row.action_type, row.after_value)
                  for row in session.scalars(select(AdminActionLog)).all()],
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("apply", [False, True])
async def test_identical_import_preserves_administrator_decision(engine, tmp_path, raw_bundle, action, apply):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        record_admin_decision(session, action)
    with Session(engine) as session, session.begin():
        before = snapshot(session)
        result = await seed.import_bundle(AsyncAdapter(session), bundle, apply=apply, now=NOW)
        assert (result["created"], result["review_updated"], result["unchanged"]) == (0, 0, 1)
        assert result["items"][0]["admin_review_protected"] is True
        assert result["items"][0]["status"] == before["content"][0]
        assert snapshot(session) == before
        assert not session.new and not session.dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("incoming", ["approved", "rejected"])
@pytest.mark.parametrize("apply", [False, True])
async def test_external_review_cannot_replace_admin_decision(engine, tmp_path, raw_bundle, action, incoming, apply):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        record_admin_decision(session, action)
    reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0], incoming)])
    with Session(engine) as session:
        before = snapshot(session)
    with Session(engine) as session, pytest.raises(seed.CarouselImportError, match="Administrator-reviewed"), session.begin():
        await seed.import_bundle(AsyncAdapter(session), reviewed, apply=apply, now=NOW)
    with Session(engine) as session:
        assert snapshot(session) == before


@pytest.mark.asyncio
async def test_admin_approval_preview_cleanup_does_not_break_identical_retry(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    arguments = dict(local_test_preview=True, expected_database="checking_live_e2e", now=NOW)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, **arguments)
        record_admin_decision(session, "approve")
    with Session(engine) as session, session.begin():
        before = snapshot(session)
        result = await seed.import_bundle(LocalPreviewAdapter(session), bundle, apply=True, **arguments)
        assert result["unchanged"] == 1
        assert snapshot(session) == before
        assert "preview" not in session.scalars(select(Content)).one().ai_judgment_basis


@pytest.mark.asyncio
async def test_guard_blocks_batch_before_any_new_item_write(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        record_admin_decision(session, "reject")
    batch = deepcopy(raw_bundle)
    new_item = deepcopy(batch["items"][0])
    new_item["package"]["request_id"] = "new-before-protected"
    batch["items"].insert(0, new_item)
    prepared = prepare(tmp_path, batch, [review(bundle.items[0])])
    with Session(engine) as session:
        before = snapshot(session)
        with pytest.raises(seed.CarouselImportError, match="Administrator-reviewed"):
            await seed.import_bundle(AsyncAdapter(session), prepared, apply=True, now=NOW)
        assert not session.new and not session.dirty
        assert snapshot(session) == before


@pytest.mark.asyncio
async def test_missing_audit_storage_fails_closed(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        session.execute(text("DROP TABLE admin_action_log"))
    reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0])])
    with Session(engine) as session, pytest.raises(SQLAlchemyError), session.begin():
        await seed.import_bundle(AsyncAdapter(session), reviewed, apply=True, now=NOW)
    with Session(engine) as session:
        assert session.scalars(select(Content)).one().status == "pending_review"
        assert session.scalars(select(Draft)).one().status == "checking"
        assert session.scalars(select(AgentRun)).one().outcome == "on_hold"


@pytest.mark.asyncio
async def test_audit_for_other_target_does_not_block_review_import(engine, tmp_path, raw_bundle):
    bundle = prepare(tmp_path, raw_bundle)
    with Session(engine) as session, session.begin():
        await seed.import_bundle(AsyncAdapter(session), bundle, apply=True, now=NOW)
        session.execute(text("INSERT INTO user_account(user_id) VALUES (1)"))
        grant = AdminAccount(user_id=1, role="operator")
        session.add(grant)
        session.flush()
        session.add_all([
            AdminActionLog(admin_account_id=grant.admin_account_id, action_type="content.reject",
                           target_type="user", target_id=1, rationale="Other target"),
            AdminActionLog(admin_account_id=grant.admin_account_id, action_type="content.reject",
                           target_type="content", target_id=999, rationale="Other content"),
        ])
    reviewed = prepare(tmp_path, raw_bundle, [review(bundle.items[0])])
    with Session(engine) as session, session.begin():
        result = await seed.import_bundle(AsyncAdapter(session), reviewed, apply=True, now=NOW)
        assert result["review_updated"] == 1
        assert result["items"][0]["admin_review_protected"] is False
        assert session.scalars(select(Content)).one().status == "active"
