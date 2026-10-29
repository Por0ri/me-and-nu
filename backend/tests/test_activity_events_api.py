"""API-033 activity events: contract, auth, idempotency and ownership checks."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import Integer, Uuid, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_optional_auth_context
from app.core.security import csrf_token_for_session, hash_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    Content, ContentSlide, ContentSource, RecommendationItem, RecommendationRound, Tap, Topic, UserEvent,
)
from app.repositories import activity_repository
from app.schemas.activity import ActivityEventsRequest, ActivityEventsResponse
from app.services import activity_service


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
RAW_SESSION = "activity-session"
CSRF = csrf_token_for_session(RAW_SESSION)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _event(number: int, event_type: str = "impression", content_id: int = 501, **extra) -> dict:
    return {
        "clientEventId": _id(number), "eventType": event_type, "topicId": 1,
        "contentId": content_id, "occurredAt": "2026-11-02T11:59:00+00:00", **extra,
    }


@pytest.fixture
def api(monkeypatch):
    app = create_app(enable_dev_api=False)
    user = SimpleNamespace(user_id=7, onboarding_completed_at=NOW)
    state = {"session": "active"}

    async def context(_request: Request):
        if state["session"] == "anonymous":
            return None
        session = SimpleNamespace(session_state=state["session"], csrf_token_hash=hash_token(CSRF))
        return AuthContext(session=session, raw_token=RAW_SESSION, user=user)

    db = object()

    async def get_test_db():
        yield db

    app.dependency_overrides[get_optional_auth_context] = context
    app.dependency_overrides[get_db] = get_test_db
    record = AsyncMock(return_value=ActivityEventsResponse(saved=1, duplicated=0, rejected=0))
    monkeypatch.setattr(activity_service, "record_events", record)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, state=state, record=record, db=db)


def test_activity_openapi_has_one_post_route_and_event_fields():
    spec = create_app(enable_dev_api=False).openapi()
    path = spec["paths"]["/api/v1/users/me/activity-events"]
    assert set(path) == {"post"}
    assert {"200", "401", "403", "422"} <= set(path["post"]["responses"])
    event = spec["components"]["schemas"]["ActivityEventIn"]
    assert set(event["properties"]) == {
        "clientEventId", "eventType", "topicId", "contentId",
        "recommendationItemId", "occurredAt", "value",
    }
    assert set(event["required"]) == {"clientEventId", "eventType", "topicId", "contentId", "occurredAt"}


def test_activity_requires_login_completed_onboarding_and_csrf(api):
    body = {"events": [_event(1)]}
    api.state["session"] = "anonymous"
    assert api.client.post("/api/v1/users/me/activity-events", json=body).json()["code"] == "AUTH_REQUIRED"
    api.state["session"] = "onboarding_pending"
    pending = api.client.post("/api/v1/users/me/activity-events", json=body, headers={"X-CSRF-Token": CSRF})
    assert (pending.status_code, pending.json()["code"]) == (403, "ONBOARDING_REQUIRED")
    api.state["session"] = "active"
    no_token = api.client.post("/api/v1/users/me/activity-events", json=body)
    assert (no_token.status_code, no_token.json()["code"]) == (403, "CSRF_INVALID")
    api.record.assert_not_awaited()

    saved = api.client.post("/api/v1/users/me/activity-events", json=body, headers={"X-CSRF-Token": CSRF})
    assert saved.status_code == 200
    assert saved.json() == {"saved": 1, "duplicated": 0, "rejected": 0}
    db, user_id, request = api.record.await_args.args
    assert (db, user_id) == (api.db, 7)
    assert request.events[0].client_event_id == UUID(_id(1))


@pytest.mark.parametrize("body", [
    {"events": []},
    {"events": [_event(n) for n in range(101)]},
    {"events": [_event(1, "view")]},
    {"events": [_event(1, occurredAt="2026-11-02T11:59:00")]},       # no time zone
    {"events": [_event(1, contentId=True)]},
    {"events": [_event(1, topicId="1")]},
    {"events": [_event(1, clientEventId="not-a-uuid")]},
    {"events": [_event(1, value={"activeMs": -1})]},
    {"events": [_event(1, value={"visibleMs": -5})]},
    {"events": [_event(1, value={"dwell": 3})]},                     # unknown measurement
    {"events": [_event(1, debateId=3)]},
    {"events": [_event(1)], "topicId": 1},
])
def test_activity_rejects_malformed_batches_before_saving(api, body):
    response = api.client.post("/api/v1/users/me/activity-events", json=body, headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 422
    api.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_events_skips_resends_rejects_foreign_rows_and_keeps_first_impression(monkeypatch):
    active_topics = {1: SimpleNamespace(topic_id=1)}
    monkeypatch.setattr(
        activity_service.catalog_repo, "get_active_topic",
        AsyncMock(side_effect=lambda _db, topic_id: active_topics.get(topic_id)),
    )
    monkeypatch.setattr(activity_service.catalog_repo, "get_tap", AsyncMock(return_value=SimpleNamespace(tap_id=11)))
    monkeypatch.setattr(activity_service.repo, "visible_content_topics", AsyncMock(return_value={501: 1, 502: 1, 503: 2}))
    monkeypatch.setattr(
        activity_service.repo, "recommendation_item_owners",
        AsyncMock(return_value={901: (11, 501), 902: (22, 502)}),
    )
    inserted = []

    async def insert_events(_db, rows):
        inserted.extend(rows)
        return len(rows) - 1      # one row was already stored by an earlier request

    monkeypatch.setattr(activity_service.repo, "insert_events", insert_events)
    mark_impressed = AsyncMock()
    monkeypatch.setattr(activity_service.repo, "mark_impressed", mark_impressed)
    monkeypatch.setattr(activity_service.settings, "event_data_source", "production")
    db = SimpleNamespace(commit=AsyncMock())
    minute = timedelta(minutes=1)
    request = ActivityEventsRequest.model_validate({"events": [
        _event(1, recommendationItemId=901, occurredAt=(NOW - minute).isoformat(), value={"visibleMs": 3500}),
        _event(1, recommendationItemId=901),                                         # same id in the batch
        _event(2, recommendationItemId=901, occurredAt=(NOW - 2 * minute).isoformat()),
        _event(3, "open", 502, recommendationItemId=902),                            # another Tap's recommendation
        _event(4, "like", 503),                                                      # content of another Topic
        _event(5, "like", 501, topicId=2),                                           # inactive Topic
        _event(6, "open", 502, occurredAt=(NOW + timedelta(days=1)).isoformat(), value={"activeMs": 1200}),
        _event(7, "like", 504),                                                      # hidden or deleted content
    ]})

    result = await activity_service.record_events(db, 7, request, now=NOW)

    assert result == ActivityEventsResponse(saved=2, duplicated=2, rejected=4)
    assert [row["client_event_id"] for row in inserted] == [UUID(_id(1)), UUID(_id(2)), UUID(_id(6))]
    assert {row["tap_id"] for row in inserted} == {11}
    assert {row["data_source"] for row in inserted} == {"production"}
    assert inserted[0]["event_value"] == {"visible_ms": 3500}
    assert inserted[1]["event_value"] == {}
    assert inserted[2]["occurred_at"] == NOW          # device clock a day ahead
    assert inserted[2]["event_value"] == {"active_ms": 1200}
    mark_impressed.assert_awaited_once_with(db, {901: NOW - 2 * minute})
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("offset, kept", [
    (timedelta(minutes=-1), True),
    (timedelta(minutes=5), True),
    (timedelta(minutes=6), False),
    (-timedelta(days=7), True),
    (-timedelta(days=8), False),
])
def test_occurred_at_keeps_device_time_within_tolerance(offset, kept):
    assert activity_service.occurred_at(NOW + offset, NOW) == (NOW + offset if kept else NOW)


@pytest.mark.asyncio
async def test_repository_skips_stored_ids_and_marks_impression_once(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [
        Topic.__table__, Tap.__table__, Content.__table__, ContentSlide.__table__, ContentSource.__table__,
        RecommendationRound.__table__, RecommendationItem.__table__, UserEvent.__table__,
    ]
    with monkeypatch.context() as patch:
        # Test DDL only: the production default has PostgreSQL's :: cast, SQLite
        # only auto-numbers INTEGER primary keys, and a column declared "UUID"
        # gets numeric affinity in SQLite, so digit-only UUID text would become a number.
        patch.setattr(UserEvent.__table__.c.event_value, "server_default", None)
        patch.setattr(UserEvent.__table__.c.user_event_id, "type", Integer())
        patch.setattr(UserEvent.__table__.c.client_event_id, "type", Uuid())
        Base.metadata.create_all(engine, tables=tables)
    first_seen = NOW - timedelta(minutes=2)
    with engine.begin() as connection:
        connection.execute(Topic.__table__.insert(), [
            {"topic_id": 1, "topic_code": "anime", "topic_name": "애니", "is_active": True},
        ])
        connection.execute(Tap.__table__.insert(), [{"tap_id": 11, "user_id": 7, "topic_id": 1}])
        connection.execute(Content.__table__.insert(), [
            {"content_id": 501, "topic_id": 1, "title": "공개 글", "status": "active", "deleted_at": None},
            {"content_id": 502, "topic_id": 1, "title": "숨긴 글", "status": "hidden", "deleted_at": None},
            {"content_id": 503, "topic_id": 1, "title": "지운 글", "status": "active",
             "deleted_at": datetime(2026, 10, 1)},
        ])
        connection.execute(RecommendationRound.__table__.insert(), [
            {"recommendation_round_id": 1, "tap_id": 11, "service_date": date(2026, 11, 2),
             "algorithm_version": "v1"},
        ])
        connection.execute(RecommendationItem.__table__.insert(), [
            {"recommendation_item_id": 901, "recommendation_round_id": 1, "content_id": 501, "rank": 1},
        ])

        def row(number: int) -> dict:
            return {
                "client_event_id": UUID(_id(number)), "user_id": 7, "tap_id": 11,
                "event_type": "impression", "content_id": 501, "recommendation_item_id": 901,
                "event_value": {"surface": "home_feed"}, "occurred_at": NOW, "received_at": NOW,
                "data_source": "automated_test",
            }

        with Session(bind=connection) as session:
            class SessionAdapter:
                async def execute(self, statement):
                    return session.execute(statement)

            db = SessionAdapter()
            visible = await activity_repository.visible_content_topics(db, {501, 502, 503, 504})
            owners = await activity_repository.recommendation_item_owners(db, {901, 999})
            first = await activity_repository.insert_events(db, [row(1), row(2)])
            resent = await activity_repository.insert_events(db, [row(2), row(3)])
            await activity_repository.mark_impressed(db, {901: first_seen})
            await activity_repository.mark_impressed(db, {901: NOW})
            stored = session.scalars(select(UserEvent.client_event_id).order_by(UserEvent.user_event_id)).all()
            impressed_at = session.scalar(select(RecommendationItem.impressed_at))

    assert visible == {501: 1}
    assert owners == {901: (11, 501)}
    assert (first, resent) == (2, 1)
    assert stored == [UUID(_id(1)), UUID(_id(2)), UUID(_id(3))]
    assert impressed_at.replace(tzinfo=timezone.utc) == first_seen
