from fastapi.testclient import TestClient

from app.main import app, create_app

client = TestClient(app)


def test_local_frontend_preflight_is_allowed():
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ("http://localhost:3000")
    assert response.headers["access-control-allow-credentials"] == "true"


def test_unknown_origin_is_not_allowed():
    response = client.get(
        "/health",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_unsupported_content_type_keeps_cors_error_visible():
    response = client.post(
        "/api/v1/onboarding",
        content="{}",
        headers={"Origin": "http://localhost:3000", "Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unexpected_error_keeps_cors_and_no_store_headers():
    failure_app = create_app(enable_dev_api=False)

    @failure_app.get("/api/v1/_test_failure")
    async def fail():
        raise RuntimeError("test failure")

    with TestClient(failure_app, raise_server_exceptions=False) as failure_client:
        response = failure_client.get(
            "/api/v1/_test_failure",
            headers={"Origin": "http://localhost:3000"},
        )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
