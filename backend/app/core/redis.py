from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import settings


@asynccontextmanager
async def redis_lifespan(api: FastAPI) -> AsyncIterator[None]:
    client = None
    if settings.redis_url:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            max_connections=settings.redis_max_connections,
            retry=Retry(NoBackoff(), 0),
        )
    api.state.redis = client
    try:
        yield
    finally:
        if client is not None:
            await client.aclose()
        api.state.redis = None


def get_redis(request: Request) -> Redis | None:
    return getattr(request.app.state, "redis", None)
