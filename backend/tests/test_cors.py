from fastapi.testclient import TestClient

from app.main import app

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
