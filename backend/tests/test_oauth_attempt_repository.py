from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.core.exceptions import ApiError
from app.core.security import hash_token
from app.db.base import Base
from app.models import OAuthAttempt
from app.repositories import oauth_attempt_repository as repo


NOW = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
BROWSER = hash_token("browser-session")


@pytest.fixture
def db(monkeypatch):
    """Run SQL against SQLite memory, mocking only PostgreSQL advisory locks."""
    engine = create_engine("sqlite://")
    OAuthAttempt.__table__.create(engine)
    session = Session(engine)
    monkeypatch.setattr(repo, "_utcnow", lambda: NOW)

    def execute(statement, *args, **kwargs):
        if isinstance(statement, TextClause) and "pg_advisory_xact_lock" in str(statement):
            return None
        return session.execute(statement, *args, **kwargs)

    adapter = SimpleNamespace(
        add=Mock(side_effect=session.add),
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(side_effect=session.commit),
        rollback=AsyncMock(side_effect=session.rollback),
        session=session,
    )
    yield adapter
    session.close()
    engine.dispose()


async def _create(db, **kwargs):
    return await repo.create_oauth_attempt(
        db, provider="kakao", browser_session_hash=BROWSER, ttl_seconds=600, **kwargs
    )


async def _consume(db, raw_state, **kwargs):
    return await repo.consume_oauth_attempt(
        db, provider=kwargs.pop("provider", "kakao"),
        browser_session_hash=kwargs.pop("browser_session_hash", BROWSER),
        raw_state=raw_state, **kwargs,
    )


def test_model_has_only_hashed_state_and_no_session_or_user_foreign_key():
    table = OAuthAttempt.__table__
    assert table.metadata is Base.metadata
    assert set(table.columns.keys()) == {
        "state_hash", "browser_session_hash", "provider", "created_at", "expires_at", "consumed_at"
    }
    assert list(table.primary_key.columns.keys()) == ["state_hash"]
    assert not table.foreign_keys
    assert table.c.state_hash.type.length == table.c.browser_session_hash.type.length == 64


@pytest.mark.asyncio
async def test_create_commits_unique_256_bit_states_and_only_stores_hashes(db):
    first = await _create(db)
    second = await _create(db)

    assert len(first) == len(second) == 43
    assert first != second
    rows = db.session.scalars(select(OAuthAttempt)).all()
    assert {row.state_hash for row in rows} == {hash_token(first), hash_token(second)}
    for row in rows:
        assert row.browser_session_hash == BROWSER
        assert row.provider == "kakao"
        assert row.expires_at - row.created_at == timedelta(seconds=600)
        assert row.consumed_at is None
        assert first not in row.__dict__.values() and second not in row.__dict__.values()
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_state_is_bound_to_provider_and_browser_and_can_be_used_only_once(db):
    raw = await _create(db)

    assert not await _consume(db, raw, provider="naver")
    assert not await _consume(db, raw, browser_session_hash=hash_token("another-browser"))
    assert not await _consume(db, "unknown-state")
    assert await _consume(db, raw)
    assert not await _consume(db, raw)
    assert db.commit.await_count == 6
    row = db.session.get(OAuthAttempt, hash_token(raw))
    assert row.consumed_at == NOW.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_expiry_boundary_is_rejected(db, monkeypatch):
    raw = await _create(db)
    monkeypatch.setattr(repo, "_utcnow", lambda: NOW + timedelta(seconds=600))
    assert not await _consume(db, raw)
    assert db.session.get(OAuthAttempt, hash_token(raw)).consumed_at is None


@pytest.mark.asyncio
async def test_consumption_uses_single_conditional_update_then_commit(db):
    raw = await _create(db)
    db.execute.reset_mock()
    db.commit.reset_mock()

    assert await _consume(db, raw)

    assert db.execute.await_count == db.commit.await_count == 1
    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.startswith("UPDATE oauth_attempt SET consumed_at=")
    assert "oauth_attempt.state_hash =" in sql
    assert "oauth_attempt.provider =" in sql
    assert "oauth_attempt.browser_session_hash =" in sql
    assert "oauth_attempt.consumed_at IS NULL" in sql
    assert "oauth_attempt.expires_at >" in sql
    assert "RETURNING oauth_attempt.state_hash" in sql
    assert raw not in str(statement.compile().params)


@pytest.mark.asyncio
async def test_rate_limit_counts_consumed_expired_attempts_across_providers(db, monkeypatch):
    for index in range(10):
        raw = await repo.create_oauth_attempt(
            db, provider="kakao" if index % 2 else "naver",
            browser_session_hash=BROWSER, ttl_seconds=1,
        )
        assert await _consume(db, raw, provider="kakao" if index % 2 else "naver")

    monkeypatch.setattr(repo, "_utcnow", lambda: NOW + timedelta(seconds=2))
    with pytest.raises(ApiError) as error:
        await _create(db)
    assert (error.value.status_code, error.value.code) == (429, "RATE_LIMITED")
    assert db.session.scalar(select(func.count()).select_from(OAuthAttempt)) == 10
    db.rollback.assert_awaited_once()

    # A different browser remains eligible and cannot clear recent expired
    # records that must still count against the first browser's rate limit.
    await repo.create_oauth_attempt(
        db, provider="naver", browser_session_hash=hash_token("other-browser"), ttl_seconds=600,
    )
    assert db.session.scalar(select(func.count()).select_from(OAuthAttempt)) == 11
    with pytest.raises(ApiError) as error:
        await _create(db)
    assert error.value.code == "RATE_LIMITED"

    monkeypatch.setattr(repo, "_utcnow", lambda: NOW + timedelta(seconds=600))
    await _create(db)
    # Old expired attempts are cleaned, but the other browser's live attempt remains.
    assert db.session.scalar(select(func.count()).select_from(OAuthAttempt)) == 2


@pytest.mark.asyncio
async def test_creation_serializes_rate_check_with_browser_transaction_lock(db):
    await _create(db)
    calls = db.execute.await_args_list
    assert "pg_advisory_xact_lock(:lock_key)" == str(calls[0].args[0]).removeprefix("SELECT ")
    lock_key = calls[0].args[1]["lock_key"]
    assert -(1 << 63) <= lock_key < 1 << 63
    assert "count(*)" in str(calls[1].args[0])
    assert "DELETE FROM oauth_attempt" in str(calls[2].args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "consume"])
@pytest.mark.parametrize("failed_method", ["execute", "commit"])
async def test_storage_failure_rolls_back_and_returns_neutral_error(db, operation, failed_method):
    raw = await _create(db)
    getattr(db, failed_method).side_effect = RuntimeError("database://secret/raw-state")
    db.rollback.reset_mock()

    with pytest.raises(ApiError) as error:
        if operation == "create":
            await _create(db)
        else:
            await _consume(db, raw)
    assert (error.value.status_code, error.value.code) == (503, "AUTH_UNAVAILABLE")
    assert "secret" not in error.value.message
    assert raw not in error.value.message
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_failure_does_not_expose_database_details(db):
    db.execute.side_effect = RuntimeError("secret execute failure")
    db.rollback.side_effect = RuntimeError("secret rollback failure")
    with pytest.raises(ApiError) as error:
        await _create(db)
    assert error.value.code == "AUTH_UNAVAILABLE"
    assert "secret" not in error.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "x" * 257])
async def test_invalid_state_is_rejected_without_database_work(db, raw):
    assert not await _consume(db, raw)
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()
