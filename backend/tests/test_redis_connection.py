from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError

from app.core import redis as redis_connection
from app.core.config import Settings, settings
from app.main import create_app


def test_redis_connection_is_closed_and_recreated_per_app_lifespan(monkeypatch):
    clients = [AsyncMock(), AsyncMock()]
    factory = Mock(side_effect=clients)
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_connection.Redis, "from_url", factory)
    api = create_app(enable_dev_api=False)
    for redis_client in clients:
        with TestClient(api) as client:
            assert api.state.redis is redis_client
            assert client.get("/health/redis").json() == {"status": "ok"}
            redis_client.aclose.assert_not_awaited()
        redis_client.aclose.assert_awaited_once()
        assert api.state.redis is None
    assert factory.call_count == 2


def test_disabled_redis_does_not_open_connection(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(redis_connection.Redis, "from_url", factory)
    with TestClient(create_app(enable_dev_api=False)) as client:
        response = client.get("/health/redis")
    assert response.status_code == 200
    assert response.json() == {"status": "disabled"}
    factory.assert_not_called()


def test_unavailable_redis_health_is_503_without_connection_details(monkeypatch):
    redis_client = AsyncMock()
    redis_client.ping.side_effect = ConnectionError("redis://secret:password@private-host")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_connection.Redis, "from_url", Mock(return_value=redis_client))
    with TestClient(create_app(enable_dev_api=False)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/health/redis")
    assert response.status_code == 503
    assert response.json()["code"] == "REDIS_UNAVAILABLE"
    assert "password" not in response.text
    assert "private-host" not in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_backend": "redis", "redis_url": None},
        {"session_backend": "unknown"},
        {"session_expire_minutes": 0},
        {"redis_socket_timeout_seconds": 0},
        {"redis_max_connections": 0},
        {"redis_key_prefix": ""},
    ],
)
def test_invalid_session_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql://unused", **overrides)
