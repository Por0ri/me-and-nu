import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.core.security import create_session_id, csrf_token_for_session, hash_token
from app.models import AuthSession
from app.repositories.redis_session_store import RedisSessionStore

redis_url = os.getenv("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not redis_url, reason="TEST_REDIS_URL is not configured")


@pytest.mark.asyncio
async def test_real_redis_shares_sessions_and_preserves_expiry_and_revocation():
    clients = [Redis.from_url(redis_url, decode_responses=True) for _ in range(2)]
    prefix = f"me_nu:test:{uuid4().hex}"
    stores = [RedisSessionStore(client, prefix=prefix) for client in clients]
    raw_token = create_session_id()
    now = datetime.now(timezone.utc)
    row = AuthSession(
        session_token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(csrf_token_for_session(raw_token)),
        user_id=23,
        session_state="active",
        created_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    key = f"{prefix}:auth-session:v1:{row.session_token_hash}"
    try:
        await stores[0].save(row)
        initial_ttl = await clients[0].pttl(key)
        loaded = await stores[1].find(raw_token)
        assert loaded is not None
        assert loaded.user_id == row.user_id
        assert loaded.expires_at == row.expires_at
        assert 0 < await clients[1].pttl(key) <= initial_ttl <= 30000
        await stores[1].revoke(loaded)
        assert await stores[0].find(raw_token) is None
        assert await clients[0].exists(key) == 0
    finally:
        await clients[0].delete(key)
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_removes_expired_session_key():
    client = Redis.from_url(redis_url, decode_responses=True)
    prefix = f"me_nu:test:{uuid4().hex}"
    store = RedisSessionStore(client, prefix=prefix)
    raw_token = create_session_id()
    now = datetime.now(timezone.utc)
    row = AuthSession(
        session_token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(csrf_token_for_session(raw_token)),
        user_id=None,
        session_state="anonymous",
        created_at=now,
        expires_at=now + timedelta(seconds=1),
    )
    key = f"{prefix}:auth-session:v1:{row.session_token_hash}"
    try:
        await store.save(row)
        await asyncio.sleep(1.1)
        assert await client.exists(key) == 0
        assert await store.find(raw_token) is None
    finally:
        await client.delete(key)
        await client.aclose()
