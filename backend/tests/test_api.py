from fastapi.testclient import TestClient

from app.main import app, create_app


def test_health_check_stays_unversioned():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_v1_routes_replace_memory_content_crud():
    paths = app.openapi()["paths"]
    required = {
        "/api/v1/auth/session",
        "/api/v1/auth/logout",
        "/api/v1/policies",
        "/api/v1/onboarding",
        "/api/v1/users/me",
        "/api/v1/users/me/consents",
        "/api/v1/topics",
        "/api/v1/topics/{topicId}/subtopics",
        "/api/v1/subtopics/{subtopicId}",
        "/api/v1/me/topics",
        "/api/v1/topics/{topicId}/feed",
        "/api/v1/contents/{contentId}",
        "/api/v1/contents/{contentId}/preference",
        "/api/v1/contents/{contentId}/like",
        "/api/v1/contents/{contentId}/bookmark",
    }
    assert required <= set(paths)
    assert "/api/v1/auth/me" not in paths
    assert "/api/v1/auth/login" not in paths
    assert "/api/v1/contents" not in paths
    assert "/api/v1/agent-runs" not in paths
    assert "post" not in paths["/api/v1/contents/{contentId}"]
    assert "patch" not in paths["/api/v1/contents/{contentId}"]


def test_protected_resources_reject_anonymous_call_without_touching_db():
    with TestClient(app) as client:
        topics = client.get("/api/v1/topics")
        detail = client.get("/api/v1/contents/301?topicId=1")
    assert topics.status_code == 401
    assert topics.json()["code"] == "AUTH_REQUIRED"
    assert detail.status_code == 401
    assert detail.json()["code"] == "AUTH_REQUIRED"


def test_openapi_exposes_camelcase_security_and_empty_204_contract():
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert {"SessionCookie", "CsrfToken"} <= set(schema["components"]["securitySchemes"])
    assert "topicId" in schema["components"]["schemas"]["TopicAction"]["properties"]
    delete = schema["paths"]["/api/v1/contents/{contentId}/like"]["delete"]
    assert "content" not in delete["responses"]["204"]


def test_dev_routes_are_absent_from_openapi_when_disabled():
    enabled = create_app(enable_dev_api=True).openapi()["paths"]
    disabled = create_app(enable_dev_api=False).openapi()["paths"]
    assert "/api/v1/dev/auth/register" in enabled
    assert "/api/v1/dev/auth/login" in enabled
    assert not any(path.startswith("/api/v1/dev/") for path in disabled)
