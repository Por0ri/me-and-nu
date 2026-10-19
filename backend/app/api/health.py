from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.exceptions import ApiError
from app.core.redis import get_redis
from app.schemas.common import ErrorResponse

router = APIRouter(tags=["상태 확인"])


@router.get("/health", summary="서버 상태 확인")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db", summary="데이터베이스 연결 확인")
async def database_health_check(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str | int]:
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": result.scalar_one()}


@router.get(
    "/health/redis",
    summary="Redis 연결 확인",
    responses={503: {"model": ErrorResponse, "description": "REDIS_UNAVAILABLE"}},
)
async def redis_health_check(
    client: Annotated[Redis | None, Depends(get_redis)],
) -> dict[str, str]:
    if client is None:
        return {"status": "disabled"}
    try:
        await client.ping()
    except RedisError as exc:
        raise ApiError(503, "REDIS_UNAVAILABLE", "Redis에 연결할 수 없습니다.") from exc
    return {"status": "ok"}
