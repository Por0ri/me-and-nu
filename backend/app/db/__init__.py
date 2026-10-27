"""Database exports; model metadata can be imported without runtime settings."""

from app.db.base import Base

__all__ = ["Base", "get_db"]


def __getattr__(name: str):
    if name == "get_db":
        from app.db.session import get_db

        return get_db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
