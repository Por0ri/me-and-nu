"""Keep OAuth credentials out of access logs without changing requests."""

import logging
from copy import deepcopy
from io import StringIO

import pytest
from starlette.requests import Request
from uvicorn.logging import AccessFormatter
from uvicorn.protocols.utils import get_path_with_query_string

from app.core.oauth_logging import (
    OAuthCallbackAccessLogFilter,
    install_oauth_access_log_filter,
)


@pytest.fixture
def access_log(monkeypatch):
    logger = logging.getLogger("uvicorn.access")
    previous_level = logger.level
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter(
            '%(client_addr)s - "%(request_line)s" %(status_code)s',
            use_colors=False,
        )
    )
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "filters", [])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "disabled", False)
    logger.setLevel(logging.INFO)
    try:
        yield logger, stream
    finally:
        logger.setLevel(previous_level)
        handler.close()


def emit_access(logger, target, status=303):
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:54321",
        "GET",
        target,
        "1.1",
        status,
    )


@pytest.mark.parametrize(
    ("provider", "suffix", "status"),
    [
        ("kakao", "", 303),
        ("naver", "/", 303),
        ("unknown-provider", "", 404),
        ("kakao", "/", 307),
    ],
)
def test_callback_access_log_drops_query_but_keeps_request_and_status(
    access_log, provider, suffix, status
):
    logger, stream = access_log
    install_oauth_access_log_filter()
    path = f"/api/v1/auth/login/{provider}/callback{suffix}"
    emit_access(
        logger,
        path + "?code=fake-one-use-code&state=fake-state&error_description=fake-provider-detail",
        status,
    )

    output = stream.getvalue()
    assert f'127.0.0.1:54321 - "GET {path} HTTP/1.1" {status} ' in output
    assert "?" not in output
    assert "fake-one-use-code" not in output
    assert "fake-state" not in output
    assert "fake-provider-detail" not in output
    assert "error_description" not in output


@pytest.mark.parametrize(
    "target",
    [
        "/health?probe=ready",
        "/api/v1/topics/2/feed?cursor=page-2&limit=10",
        "/api/v1/auth/login/kakao?purpose=signin",
        "/api/v1/auth/login/kakao/callback/extra?trace=visible",
    ],
)
def test_non_callback_access_log_preserves_query(access_log, target):
    logger, stream = access_log
    install_oauth_access_log_filter()

    emit_access(logger, target, 200)

    assert f'"GET {target} HTTP/1.1" 200 OK' in stream.getvalue()


def test_app_creation_installs_filter_once_and_preserves_other_filters(access_log):
    from app.main import create_app

    logger, stream = access_log
    other_filter = logging.Filter()
    logger.addFilter(other_filter)

    create_app(enable_dev_api=False)
    assert sum(isinstance(item, OAuthCallbackAccessLogFilter) for item in logger.filters) == 1
    create_app(enable_dev_api=False)
    install_oauth_access_log_filter()
    install_oauth_access_log_filter()

    assert sum(isinstance(item, OAuthCallbackAccessLogFilter) for item in logger.filters) == 1
    assert other_filter in logger.filters
    emit_access(logger, "/api/v1/auth/login/kakao/callback?code=fake-code", 303)
    assert "fake-code" not in stream.getvalue()
    assert stream.getvalue().count("GET /api/v1/auth/login/kakao/callback HTTP/1.1") == 1


def test_access_log_redaction_keeps_asgi_callback_query_available(access_log):
    logger, stream = access_log
    install_oauth_access_log_filter()
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
        "path": "/api/v1/auth/login/kakao/callback",
        "raw_path": b"/api/v1/auth/login/kakao/callback",
        "query_string": b"code=fake-code&state=fake-state&error_description=fake-detail",
        "headers": [],
        "client": ("127.0.0.1", 54321),
        "server": ("localhost", 8000),
    }
    original_scope = deepcopy(scope)
    request = Request(scope)

    emit_access(logger, get_path_with_query_string(scope))

    assert scope == original_scope
    assert dict(request.query_params) == {
        "code": "fake-code",
        "state": "fake-state",
        "error_description": "fake-detail",
    }
    assert "fake-code" not in stream.getvalue()
    assert "fake-state" not in stream.getvalue()
    assert "fake-detail" not in stream.getvalue()
