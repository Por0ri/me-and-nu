"""Exercise the PostgreSQL session path with isolated SQLite SQL transactions.

Only the PostgreSQL advisory-lock statement and external provider are replaced.
Account, attempt, CSRF, session, onboarding, commit, rollback and logout logic
remain real. SQLite does not verify PostgreSQL row-lock or concurrency behavior.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Integer, MetaData, create_engine, event, func, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.auth import oauth
from app.core.config import settings
from app.core.security import hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuthSession, ConsentHistory, NotificationSetting, OnboardingAnswersRecord,
    Policy, Subtopic, Tap, TapTopic, Topic, UserAccount,
)
from app.models.oauth_attempt import OAuthAttempt
from app.services.oauth_provider import OAuthIdentity


class AsyncSqliteSession:
    def __init__(self, session, failure):
        self.session = session
        self.failure = failure

    def add(self, row):
        self.session.add(row)

    async def execute(self, statement, params=None):
        if "pg_advisory_xact_lock" in str(statement):
            return None
        return self.session.execute(statement, params)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def flush(self):
        if self.failure.answers_insert:
            for row in self.session.new:
                if isinstance(row, OnboardingAnswersRecord):
                    # Force the real DB CHECK to fail after the parent Tap was
                    # flushed, proving the whole signup transaction rolls back.
                    row.q1_choice = "unknown"
                    self.failure.answers_insert = False
        self.session.flush()

    async def commit(self):
        if self.failure.enabled and any(
            isinstance(row, AuthSession) and row.user_id is not None
            for row in self.session.identity_map.values()
        ):
            self.failure.enabled = False
            raise SQLAlchemyError("simulated final transaction failure")
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    @asynccontextmanager
    async def begin_nested(self):
        with self.session.begin_nested():
            yield


@pytest.fixture
def postgres_flow(monkeypatch):
    for name, value in {
        "session_backend": "postgres", "redis_url": None,
        "enable_dev_auth_bypass": False, "session_cookie_secure": False,
        "kakao_oauth_enabled": True, "naver_oauth_enabled": False,
        "kakao_client_id": "test-id", "kakao_client_secret": SecretStr("test-secret"),
        "oauth_frontend_origin": "http://localhost:3000",
        "oauth_public_api_origin": "http://localhost:8000",
    }.items():
        monkeypatch.setattr(settings, name, value)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def use_explicit_transactions(connection, _):
        # A SELECT must begin a real transaction, so RELEASE SAVEPOINT cannot
        # accidentally commit the account ahead of the callback's outer commit.
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    metadata = MetaData()
    for model in (
        UserAccount, AuthSession, OAuthAttempt, Topic, Subtopic, Policy,
        ConsentHistory, NotificationSetting, Tap, TapTopic, OnboardingAnswersRecord,
    ):
        table = model.__table__.to_metadata(metadata)
        for column in table.primary_key.columns:
            if len(table.primary_key.columns) == 1 and isinstance(column.type, Integer):
                column.type = Integer()
    metadata.create_all(engine)
    failure = SimpleNamespace(enabled=False, answers_insert=False)

    async def database():
        with Session(engine, expire_on_commit=False) as session:
            yield AsyncSqliteSession(session, failure)

    provider = SimpleNamespace(
        build_authorization_url=Mock(side_effect=lambda _, state: "https://provider.example/?" + urlencode({"state": state})),
        exchange_identity=AsyncMock(return_value=OAuthIdentity("kakao", "123", "member@example.com", "회원")),
    )
    api = create_app(enable_dev_api=False)
    api.dependency_overrides[get_db] = database
    api.dependency_overrides[oauth.get_oauth_provider_client] = lambda: provider
    try:
        with TestClient(api, follow_redirects=False, raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, engine=engine, provider=provider, failure=failure)
    finally:
        engine.dispose()


def begin_login(flow):
    response = flow.client.get("/api/v1/auth/session")
    assert response.status_code == 200
    csrf = response.json()["csrfToken"]
    response = flow.client.post(
        "/api/v1/auth/login/kakao", json={"purpose": "signin"}, headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorizationUrl"]).query)["state"][0]


def callback(flow, state):
    return flow.client.get("/api/v1/auth/login/kakao/callback", params={"state": state, "code": "provider-code"})


@pytest.mark.parametrize("already_onboarded", [False, True])
def test_database_session_login_state_rotation_and_logout(postgres_flow, already_onboarded):
    flow = postgres_flow
    if already_onboarded:
        with Session(flow.engine) as session:
            session.add(UserAccount(
                auth_provider="kakao", provider_user_id="123", email="member@example.com",
                nickname="기존 이름", account_status="active",
                onboarding_completed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
            ))
            session.commit()
    state = begin_login(flow)
    old_cookie = flow.client.cookies.get(settings.session_cookie_name)
    response = callback(flow, state)
    assert response.status_code == 303
    expected = "/pivot/home" if already_onboarded else "/pivot/consent"
    assert response.headers["location"] == "http://localhost:3000" + expected
    assert flow.client.cookies.get(settings.session_cookie_name) != old_cookie
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["authenticated"] is True
    assert current["sessionState"] == ("active" if already_onboarded else "onboarding_pending")
    assert current["onboardingCompleted"] is already_onboarded
    with Session(flow.engine) as session:
        users = session.scalars(select(UserAccount)).all()
        assert len(users) == 1 and users[0].account_type == "user"
        assert users[0].nickname == ("기존 이름" if already_onboarded else "회원")
        assert users[0].last_login_at is not None
        attempt = session.get(OAuthAttempt, hash_token(state))
        assert attempt is not None and attempt.consumed_at is not None
        old_session = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == hash_token(old_cookie)))
        assert old_session.revoked_at is not None
        assert session.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 1
    replay = callback(flow, state)
    assert replay.status_code == 303 and replay.headers["location"].endswith("?error=AUTH_FAILED")
    flow.provider.exchange_identity.assert_awaited_once()
    response = flow.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": current["csrfToken"]})
    assert response.status_code == 204
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 0
    assert flow.client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_failed_final_commit_rolls_back_account_and_session_but_consumes_state(postgres_flow):
    flow = postgres_flow
    state = begin_login(flow)
    old_cookie = flow.client.cookies.get(settings.session_cookie_name)
    flow.failure.enabled = True
    response = callback(flow, state)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/pivot/login?error=AUTH_UNAVAILABLE")
    assert "set-cookie" not in response.headers
    assert flow.client.cookies.get(settings.session_cookie_name) == old_cookie
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(UserAccount)) == 0
        sessions = session.scalars(select(AuthSession)).all()
        assert len(sessions) == 1 and sessions[0].revoked_at is None and sessions[0].user_id is None
        assert session.get(OAuthAttempt, hash_token(state)).consumed_at is not None
    replay = callback(flow, state)
    assert replay.status_code == 303 and replay.headers["location"].endswith("?error=AUTH_FAILED")
    flow.provider.exchange_identity.assert_awaited_once()


@pytest.fixture
def onboarding_flow(postgres_flow):
    flow = postgres_flow
    with Session(flow.engine) as session:
        session.add(Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True))
        for policy_type, required in (("terms", True), ("privacy", True), ("marketing", False)):
            session.add(Policy(
                policy_type=policy_type, policy_version="v1", title=policy_type,
                content="테스트 정책", is_required=required, is_active=True,
                effective_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ))
        session.commit()
    return flow


def pending_onboarding(flow):
    assert callback(flow, begin_login(flow)).status_code == 303
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "onboarding_pending"
    return current["csrfToken"], flow.client.cookies.get(settings.session_cookie_name)


def signup_request(flow, csrf, answers):
    return flow.client.post(
        "/api/v1/onboarding/complete",
        json={
            "nickname": "선택 저장 회원", "birthDate": "2000-01-01",
            "accountType": "user", "topicId": 1, "answers": answers,
            "consents": [
                {"type": "terms", "policyVersion": "v1", "agreed": True},
                {"type": "privacy", "policyVersion": "v1", "agreed": True},
                {"type": "marketing", "policyVersion": "v1", "agreed": False},
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )


@pytest.mark.parametrize("answers", [
    {"q1": "world", "q2": "releases"}, {"q1": "events", "q2": "events"},
])
def test_onboarding_persists_answers_without_subtopics_and_rotates_session(onboarding_flow, answers):
    flow = onboarding_flow
    csrf, pending_token = pending_onboarding(flow)
    response = signup_request(flow, csrf, answers)
    assert response.status_code == 201, response.text
    assert response.json()["answers"] == answers
    assert response.json()["activeTopic"] == {
        "id": 1, "code": "anime", "name": "애니메이션", "subtopicIds": [],
    }
    assert response.json()["onboardingCompleted"] is True
    active_token = flow.client.cookies.get(settings.session_cookie_name)
    assert active_token != pending_token
    with Session(flow.engine) as session:
        user = session.scalar(select(UserAccount))
        assert user.nickname == "선택 저장 회원"
        assert user.birth_date == date(2000, 1, 1)
        assert user.onboarding_completed_at is not None
        tap = session.scalar(select(Tap))
        assert (tap.user_id, tap.topic_id) == (user.user_id, 1)
        record = session.get(OnboardingAnswersRecord, tap.tap_id)
        assert (record.q1_choice, record.q2_choice) == (answers["q1"], answers["q2"])
        assert record.created_at is not None
        assert session.scalar(select(func.count()).select_from(TapTopic)) == 0
        assert {row.consent_type: row.is_agreed for row in session.scalars(select(ConsentHistory))} == {
            "terms": True, "privacy": True, "marketing": False,
        }
        assert session.get(NotificationSetting, user.user_id).marketing is False
        assert session.scalar(select(AuthSession).where(AuthSession.session_token_hash == hash_token(pending_token))).revoked_at is not None
        active = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == hash_token(active_token)))
        assert active.session_state == "active" and active.revoked_at is None
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "active"
    repeated = signup_request(flow, current["csrfToken"], answers)
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "ONBOARDING_ALREADY_COMPLETED"
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(OnboardingAnswersRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ConsentHistory)) == 3


@pytest.mark.parametrize("failure_stage", ["answers_insert", "enabled"])
def test_failed_onboarding_rolls_back_answers_profile_consents_and_session(onboarding_flow, failure_stage):
    flow = onboarding_flow
    csrf, pending_token = pending_onboarding(flow)
    with Session(flow.engine) as session:
        session_count = session.scalar(select(func.count()).select_from(AuthSession))
    setattr(flow.failure, failure_stage, True)
    response = signup_request(flow, csrf, {"q1": "world", "q2": "releases"})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert flow.client.cookies.get(settings.session_cookie_name) == pending_token
    with Session(flow.engine) as session:
        user = session.scalar(select(UserAccount))
        assert user.nickname == "회원" and user.birth_date is None
        assert user.onboarding_completed_at is None
        for model in (Tap, TapTopic, OnboardingAnswersRecord, ConsentHistory, NotificationSetting):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(select(func.count()).select_from(AuthSession)) == session_count
        pending = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == hash_token(pending_token)))
        assert pending.session_state == "onboarding_pending" and pending.revoked_at is None
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "onboarding_pending"
    retry = signup_request(flow, csrf, {"q1": "world", "q2": "releases"})
    assert retry.status_code == 201, retry.text


@pytest.mark.parametrize("invalid", [
    {"q1_choice": None}, {"q2_choice": None},
    {"q1_choice": "releases"}, {"q2_choice": "world"},
    {"q1_choice": "unknown"}, {"q2_choice": '["events"]'},
])
def test_answer_database_constraints_reject_invalid_choices(onboarding_flow, invalid):
    flow = onboarding_flow
    with Session(flow.engine) as session:
        session.add(UserAccount(
            user_id=501, email="constraint@example.com", nickname="제약 검증 회원",
            auth_provider="local", hashed_password="test-hash",
        ))
        session.flush()
        session.add(Tap(tap_id=601, user_id=501, topic_id=1))
        session.commit()
    with pytest.raises(IntegrityError) as caught, flow.engine.begin() as connection:
        connection.execute(insert(OnboardingAnswersRecord), {
            "tap_id": 601, "q1_choice": "world", "q2_choice": "releases", **invalid,
        })
    column, value = next(iter(invalid.items()))
    expected_error = (
        f"NOT NULL constraint failed: onboarding_answers.{column}" if value is None
        else f"CHECK constraint failed: ck_onboarding_answers_{column}"
    )
    assert expected_error in str(caught.value.orig)
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(OnboardingAnswersRecord)) == 0


def test_answers_require_a_tap_and_allow_only_one_record_per_tap(onboarding_flow):
    flow = onboarding_flow
    with pytest.raises(IntegrityError), flow.engine.begin() as connection:
        connection.execute(insert(OnboardingAnswersRecord), {
            "tap_id": 999, "q1_choice": "world", "q2_choice": "releases",
        })
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, {"q1": "events", "q2": "events"}).status_code == 201
    with Session(flow.engine) as session:
        tap_id = session.scalar(select(Tap.tap_id))
    with pytest.raises(IntegrityError), flow.engine.begin() as connection:
        connection.execute(insert(OnboardingAnswersRecord), {
            "tap_id": tap_id, "q1_choice": "world", "q2_choice": "releases",
        })
    with Session(flow.engine) as session:
        record = session.get(OnboardingAnswersRecord, tap_id)
        assert (record.q1_choice, record.q2_choice) == ("events", "events")
