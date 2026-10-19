from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.redis import get_redis
from app.repositories.redis_session_store import RedisSessionStore


def get_session_store(
    client: Annotated[Redis | None, Depends(get_redis)],
) -> RedisSessionStore | None:
    if settings.session_backend == "postgres":
        return None
    if client is None:
        raise ApiError(503, "SESSION_STORE_UNAVAILABLE", "세션 저장소에 연결할 수 없습니다.")
    return RedisSessionStore(client, prefix=settings.redis_key_prefix)
