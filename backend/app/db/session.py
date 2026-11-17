from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine_options: dict[str, bool | int] = {"echo": settings.database_echo}
# SQLite test engines may use StaticPool, which does not accept queue-pool options.
if make_url(settings.database_url).get_backend_name() != "sqlite":
    if settings.database_pool_size is not None:
        _engine_options["pool_size"] = settings.database_pool_size
    if settings.database_max_overflow is not None:
        _engine_options["max_overflow"] = settings.database_max_overflow

engine = create_async_engine(
    settings.database_url,
    **_engine_options,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[
    AsyncSession,
    None,
]:
    async with AsyncSessionLocal() as session:
        yield session
