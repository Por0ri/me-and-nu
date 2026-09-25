from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

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
