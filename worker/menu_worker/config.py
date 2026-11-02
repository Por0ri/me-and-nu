"""Small explicit configuration surface; never load backend or agent .env files."""
from dataclasses import dataclass, field
import os
from collections.abc import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    broker_url: str = field(repr=False)
    result_backend: str = field(repr=False)
    beat_interval_seconds: int


def _redis_database(value: str, name: str) -> tuple:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query or parsed.fragment
            or not parsed.path.removeprefix("/").isdigit()
            or any(character.isspace() for character in value)
        ):
            raise ValueError
        return parsed.hostname, parsed.port or 6379, int(parsed.path[1:])
    except (ValueError, AttributeError):
        raise ValueError(f"{name} must be a Redis URL with an explicit database number") from None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if environ is None else environ
    broker = values.get("CELERY_BROKER_URL", "redis://redis-tasks:6379/0")
    backend = values.get("CELERY_RESULT_BACKEND", "redis://redis-tasks:6379/1")
    if _redis_database(broker, "CELERY_BROKER_URL") == _redis_database(backend, "CELERY_RESULT_BACKEND"):
        raise ValueError("Broker and result backend must use separate Redis databases")
    try:
        raw_interval = values.get("CELERY_BEAT_INTERVAL_SECONDS", "30")
        if not isinstance(raw_interval, str) or not raw_interval.isdecimal():
            raise ValueError
        interval = int(raw_interval)
        if not 5 <= interval <= 3600:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("CELERY_BEAT_INTERVAL_SECONDS must be an integer from 5 to 3600") from None
    return Settings(broker, backend, interval)
