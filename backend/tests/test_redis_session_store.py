"""Authentication session persistence and failure behavior in Redis."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError, TimeoutError
from sqlalchemy import inspect

from app.core.exceptions import ApiError
from app.core.security import csrf_token_for_session, hash_token
from app.models import AuthSession
from app.repositories.redis_session_store import RedisSessionStore

RAW_TOKEN = "test-session-token-with-no-user-information"


@pytest.fixture
def session_row() -> AuthSession:
    created_at = datetime.now(timezone.utc)
    return AuthSession(
        session_token_hash=hash_token(RAW_TOKEN),
        csrf_token_hash=hash_token(csrf_token_for_session(RAW_TOKEN)),
        user_id=23,
        session_state="active",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=30),
    )


@pytest.fixture
def redis_client() -> AsyncMock:
    client = AsyncMock()
    client.set.return_value = True
    client.get.return_value = None
    client.delete.return_value = 1
    return client


def _payload(row: AuthSession) -> dict:
    return {
        "session_token_hash": row.session_token_hash,
        "csrf_token_hash": row.csrf_token_hash,
        "user_id": row.user_id,
        "session_state": row.session_state,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_save_uses_hashed_key_absolute_expiry_and_no_raw_credentials(redis_client, session_row):
    store = RedisSessionStore(redis_client, prefix="test")

    await store.save(session_row)

    call = redis_client.set.await_args
    assert call.args[0] == f"test:auth-session:v1:{hash_token(RAW_TOKEN)}"
    saved_payload = json.loads(call.args[1])
    expected_payload = _payload(session_row)
    for field in ("created_at", "expires_at"):
        assert datetime.fromisoformat(saved_payload.pop(field)) == datetime.fromisoformat(
            expected_payload.pop(field)
        )
    assert saved_payload == expected_payload
    assert RAW_TOKEN not in call.args[1]
    assert csrf_token_for_session(RAW_TOKEN) not in call.args[1]
    assert call.kwargs == {
        "pxat": int(session_row.expires_at.timestamp() * 1000),
        "nx": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_state", "user_id"),
    [("anonymous", None), ("onboarding_pending", 23), ("active", 23)],
)
async def test_find_returns_transient_session_without_renewing_expiry(
    redis_client, session_row, session_state, user_id
):
    session_row.session_state = session_state
    session_row.user_id = user_id
    redis_client.get.return_value = json.dumps(_payload(session_row)).encode()
    store = RedisSessionStore(redis_client)

    first = await store.find(RAW_TOKEN)
    second = await store.find(RAW_TOKEN)

    assert first is not None and second is not None
    assert first.user_id == user_id
    assert first.session_state == session_state
    assert first.csrf_token_hash == session_row.csrf_token_hash
    assert first.created_at == session_row.created_at
    assert first.expires_at == second.expires_at == session_row.expires_at
    assert first.revoked_at is None
    assert first.last_accessed_at is None
    assert inspect(first).transient
    assert [call[0] for call in redis_client.mock_calls] == ["get", "get"]
    redis_client.get.assert_awaited_with(f"me_nu:auth-session:v1:{hash_token(RAW_TOKEN)}")


@pytest.mark.asyncio
async def test_find_returns_none_for_missing_or_expired_session(redis_client, session_row):
    store = RedisSessionStore(redis_client)
    assert await store.find(RAW_TOKEN) is None

    session_row.created_at -= timedelta(hours=1)
    session_row.expires_at -= timedelta(hours=1)
    redis_client.get.return_value = json.dumps(_payload(session_row))
    assert await store.find(RAW_TOKEN) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_token_hash", "a" * 64),
        ("session_token_hash", "a" * 63),
        ("csrf_token_hash", "b" * 64),
        ("csrf_token_hash", "z" * 64),
        ("user_id", True),
        ("user_id", "23"),
        ("user_id", 0),
        ("user_id", -1),
        ("user_id", None),
        ("session_state", "anonymous"),
        ("session_state", "admin"),
        ("created_at", "2026-01-01T00:00:00"),
        ("expires_at", "2099-01-01T00:00:00"),
        ("expires_at", "not-a-date"),
        ("revoked_at", "2026-01-01T00:00:00+00:00"),
    ],
)
async def test_find_rejects_invalid_or_mismatched_payload(
    redis_client, session_row, field, value
):
    payload = _payload(session_row)
    payload[field] = value
    redis_client.get.return_value = json.dumps(payload)

    assert await RedisSessionStore(redis_client).find(RAW_TOKEN) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_payload", ["{", "null", "[]", "{}", b"\xff"])
async def test_find_rejects_malformed_payload(redis_client, raw_payload):
    redis_client.get.return_value = raw_payload

    assert await RedisSessionStore(redis_client).find(RAW_TOKEN) is None


@pytest.mark.asyncio
async def test_find_rejects_redis_response_with_invalid_utf8(redis_client):
    redis_client.get.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    assert await RedisSessionStore(redis_client).find(RAW_TOKEN) is None


@pytest.mark.asyncio
async def test_find_rejects_expiry_before_creation(redis_client, session_row):
    payload = _payload(session_row)
    payload["created_at"] = (session_row.expires_at + timedelta(seconds=1)).isoformat()
    redis_client.get.return_value = json.dumps(payload)

    assert await RedisSessionStore(redis_client).find(RAW_TOKEN) is None


@pytest.mark.asyncio
async def test_revoke_deletes_hashed_key_and_marks_row_after_success(redis_client, session_row):
    store = RedisSessionStore(redis_client)

    await store.revoke(session_row)

    redis_client.delete.assert_awaited_once_with(
        f"me_nu:auth-session:v1:{session_row.session_token_hash}"
    )
    assert session_row.revoked_at is not None
    assert session_row.revoked_at.tzinfo is not None


@pytest.mark.asyncio
async def test_revoke_of_missing_session_is_idempotent(redis_client, session_row):
    redis_client.delete.return_value = 0

    await RedisSessionStore(redis_client).revoke(session_row)

    assert session_row.revoked_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "command"), [("save", "set"), ("find", "get"), ("revoke", "delete")])
@pytest.mark.parametrize("error_type", [ConnectionError, TimeoutError])
async def test_redis_errors_are_generic_503_without_sensitive_logs(
    redis_client, session_row, operation, command, error_type, caplog
):
    secret = "redis://private-user:private-password@private-host/0"
    getattr(redis_client, command).side_effect = error_type(f"{secret} {RAW_TOKEN}")
    store = RedisSessionStore(redis_client)

    with pytest.raises(ApiError) as raised:
        await getattr(store, operation)(RAW_TOKEN if operation == "find" else session_row)

    assert raised.value.status_code == 503
    assert raised.value.code == "SESSION_STORE_UNAVAILABLE"
    assert secret not in str(raised.value)
    assert RAW_TOKEN not in str(raised.value)
    assert secret not in caplog.text
    assert RAW_TOKEN not in caplog.text
    assert session_row.revoked_at is None


@pytest.mark.asyncio
async def test_save_collision_does_not_replace_or_extend_existing_session(redis_client, session_row):
    redis_client.set.return_value = None

    with pytest.raises(ApiError) as raised:
        await RedisSessionStore(redis_client).save(session_row)

    assert raised.value.code == "SESSION_STORE_UNAVAILABLE"
    assert redis_client.set.await_count == 1
    assert redis_client.set.await_args.kwargs["nx"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_state", ["expired", "revoked"])
async def test_save_refuses_to_restore_invalid_session(redis_client, session_row, invalid_state):
    if invalid_state == "expired":
        session_row.created_at -= timedelta(hours=1)
        session_row.expires_at -= timedelta(hours=1)
    else:
        session_row.revoked_at = datetime.now(timezone.utc)

    with pytest.raises(ValueError):
        await RedisSessionStore(redis_client).save(session_row)

    redis_client.set.assert_not_awaited()
