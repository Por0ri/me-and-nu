"""Canonical labels are actual state transitions committed with the reaction.

These tests use a private SQLite database for transaction failures and
idempotency. The companion PostgreSQL test covers concurrent parent-row locks.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ApiError
from app.models import Content, ContentReaction, SavedItem, Tap, Topic, UserAccount, UserEvent
from app.schemas.activity import ActivityEventsRequest
from app.services import reaction_service as service
from test_fe_integration_seed import AsyncAdapter, memory_db  # noqa: F401


@pytest.fixture
def reactions(memory_db, monkeypatch):
    now = datetime.now(timezone.utc)
    with memory_db.begin() as connection:
        connection.execute(Topic.__table__.insert().values(topic_id=1, topic_code="anime", topic_name="애니", is_active=True))
        connection.execute(UserAccount.__table__.insert().values(user_id=1, nickname="합성", auth_provider="kakao",
            provider_user_id="canonical-test", onboarding_completed_at=now))
        connection.execute(Tap.__table__.insert().values(tap_id=11, user_id=1, topic_id=1))
        connection.execute(Content.__table__.insert().values(content_id=101, topic_id=1,
            title="합성 콘텐츠", judgment_status="confirmed", production_type="human", status="active"))
    monkeypatch.setattr(settings, "event_data_source", "automated_test")
    with Session(memory_db, expire_on_commit=False) as session:
        yield SimpleNamespace(db=AsyncAdapter(session), session=session, engine=memory_db)


def events(state):
    # Scalars read through the real ORM and therefore also detect malformed
    # UUID / JSON / timestamp values, rather than just checking db.add calls.
    return list(state.session.scalars(select(UserEvent).order_by(UserEvent.user_event_id)))


@pytest.mark.asyncio
async def test_repeated_like_and_cancel_log_only_transitions(reactions):
    start = datetime.now(timezone.utc)
    await service.set_like(reactions.db, 1, 1, 101)
    await service.set_like(reactions.db, 1, 1, 101)
    assert [e.event_type for e in events(reactions)] == ["like"]
    await service.clear_like(reactions.db, 1, 1, 101)
    await service.clear_like(reactions.db, 1, 1, 101)
    await service.set_like(reactions.db, 1, 1, 101)
    rows = events(reactions)
    assert [e.event_type for e in rows] == ["like", "like_cancel", "like"]
    assert len({e.client_event_id for e in rows}) == 3
    for row in rows:
        assert isinstance(row.client_event_id, UUID)
        assert (row.user_id, row.tap_id, row.content_id) == (1, 11, 101)
        assert row.event_value == {"origin": "server_reaction", "schemaVersion": 1}
        assert row.data_source == "automated_test"
        assert row.recommendation_item_id is None
        assert row.occurred_at == row.received_at
        # SQLite drops the offset on read; production timestamptz is verified
        # in the PostgreSQL counterpart, not inferred from this adapter.
        assert row.occurred_at.replace(tzinfo=timezone.utc) >= start


@pytest.mark.asyncio
async def test_preference_direction_changes_and_clear_preserve_like(reactions):
    await service.set_like(reactions.db, 1, 1, 101)
    for value in ("positive", "positive", "negative", "negative", "positive"):
        await service.set_preference(reactions.db, 1, 1, 101, value)
    await service.clear_preference(reactions.db, 1, 1, 101)
    await service.clear_preference(reactions.db, 1, 1, 101)
    assert [e.event_type for e in events(reactions)] == [
        "like", "preference_positive", "preference_negative", "preference_positive", "preference_cancel"]
    reaction = reactions.session.scalar(select(ContentReaction))
    assert reaction.is_liked and reaction.ox_feedback is None


@pytest.mark.asyncio
async def test_bookmark_retries_resurface_off_cancel_and_restore(reactions):
    first = await service.set_bookmark(reactions.db, 1, 1, 101)
    repeated = await service.set_bookmark(reactions.db, 1, 1, 101)
    assert repeated.saved_item_id == first.saved_item_id
    saved = reactions.session.scalar(select(SavedItem))
    saved.status = "resurface_off"
    reactions.session.commit()
    await service.set_bookmark(reactions.db, 1, 1, 101)
    assert [e.event_type for e in events(reactions)] == ["bookmark"]
    await service.clear_bookmark(reactions.db, 1, 1, 101)
    await service.clear_bookmark(reactions.db, 1, 1, 101)
    restored = await service.set_bookmark(reactions.db, 1, 1, 101)
    assert restored.saved_item_id == first.saved_item_id and restored.resurface_enabled
    assert [e.event_type for e in events(reactions)] == ["bookmark", "bookmark_cancel", "bookmark"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [service.set_like, service.set_bookmark])
async def test_event_constraint_failure_rolls_back_reaction_and_event(reactions, operation):
    # Real database constraint failure at the event insert, after any preceding
    # reaction INSERT, must undo both. Retries must not report success without a
    # durable canonical label or leave a reaction written by a failed request.
    with reactions.engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER reject_canonical_event BEFORE INSERT ON user_event "
            "BEGIN SELECT RAISE(ABORT, 'synthetic rejection'); END"))
    with pytest.raises(ApiError) as error:
        await operation(reactions.db, 1, 1, 101)
    assert error.value.code == "STATE_CONFLICT"
    assert events(reactions) == []
    assert reactions.session.scalar(select(ContentReaction)) is None
    assert reactions.session.scalar(select(SavedItem)) is None
    reactions.session.rollback()
    with reactions.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER reject_canonical_event"))
    await operation(reactions.db, 1, 1, 101)
    assert len(events(reactions)) == 1


@pytest.mark.asyncio
async def test_retry_after_transaction_failure_writes_exactly_one_label(reactions):
    failures = 0
    from sqlalchemy.exc import IntegrityError

    def fail_first_event(_conn, _cursor, statement, _params, _context, _executemany):
        nonlocal failures
        if statement.startswith("INSERT INTO user_event") and failures == 0:
            failures += 1
            raise IntegrityError(statement, {}, RuntimeError("synthetic transient conflict"))

    event.listen(reactions.engine, "before_cursor_execute", fail_first_event)
    try:
        await service.set_like(reactions.db, 1, 1, 101)
    finally:
        event.remove(reactions.engine, "before_cursor_execute", fail_first_event)
    assert failures == 1
    assert [e.event_type for e in events(reactions)] == ["like"]
    assert reactions.session.scalar(select(ContentReaction)).is_liked


@pytest.mark.parametrize("extra", [{"origin": "server_reaction"}, {"schemaVersion": 1}])
def test_client_cannot_forge_canonical_event_marker(extra):
    with pytest.raises(ValidationError):
        ActivityEventsRequest.model_validate({"events": [{"clientEventId": "00000000-0000-4000-8000-000000000001",
            "eventType": "like", "topicId": 1, "contentId": 101,
            "occurredAt": "2026-11-22T00:00:00Z", "value": extra}]})
