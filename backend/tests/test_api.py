from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_contents():
    response = client.get("/api/v1/contents")

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_get_missing_content():
    response = client.get("/api/v1/contents/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "콘텐츠를 찾을 수 없습니다."


def test_content_crud():
    create_response = client.post(
        "/api/v1/contents",
        json={
            "title": "FastAPI 테스트 콘텐츠",
            "category": "백엔드",
            "summary": "Mock REST API 테스트용 콘텐츠입니다.",
        },
    )
    assert create_response.status_code == 201
    content_id = create_response.json()["id"]

    update_response = client.patch(
        f"/api/v1/contents/{content_id}",
        json={"title": "수정된 콘텐츠"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "수정된 콘텐츠"

    delete_response = client.delete(f"/api/v1/contents/{content_id}")
    assert delete_response.status_code == 204
