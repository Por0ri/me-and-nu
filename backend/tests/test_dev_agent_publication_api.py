"""Local Agent publication endpoints keep the preview override behind dev mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_csrf
from app.api.dev import agent_publication as publication_routes
from app.core.config import settings
from app.db.session import get_db
from app.main import create_app
from app.models import Content, UserAccount
from app.services.movie_agent_publication import MovieAgentPublicationError


def _client(monkeypatch, *, bypass=True, client_host="127.0.0.1"):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", bypass)
    app = create_app(enable_dev_api=True)

    async def override_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: UserAccount(user_id=2)
    app.dependency_overrides[require_csrf] = lambda: None
    return TestClient(app, base_url="http://localhost:8000", client=(client_host, 50000))


def test_dev_preview_publish_returns_public_links_and_preview_state(monkeypatch):
    content = Content(
        content_id=12,
        source_draft_id=3,
        topic_id=1,
        status="active",
        judgment_status="needs_review",
        ai_judgment_basis={"preview": True},
    )
    publish = AsyncMock(return_value=content)
    monkeypatch.setattr(publication_routes, "publish_movie_agent_run", publish)
    with _client(monkeypatch) as client:
        response = client.post("/api/v1/dev/agent-runs/7/publish-preview")

    assert response.status_code == 200
    assert response.json() == {
        "contentId": 12,
        "sourceDraftId": 3,
        "topicId": 1,
        "status": "active",
        "judgmentStatus": "needs_review",
        "preview": True,
        "feedUrl": "/api/v1/topics/1/feed",
        "detailUrl": "/api/v1/contents/12?topicId=1",
    }
    assert publish.await_args.kwargs == {"force_preview": True}


def test_dev_preview_publish_requires_local_bypass(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(publication_routes, "publish_movie_agent_run", publish)
    with _client(monkeypatch, bypass=False) as client:
        disabled = client.post("/api/v1/dev/agent-runs/7/publish-preview")
    with _client(monkeypatch, client_host="198.51.100.7") as client:
        remote = client.post("/api/v1/dev/agent-runs/7/publish-preview")
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "DEV_PREVIEW_DISABLED"
    assert remote.status_code == 403
    assert publish.await_count == 0


def test_approved_publish_maps_unready_draft_to_conflict(monkeypatch):
    publish = AsyncMock(
        side_effect=MovieAgentPublicationError(
            "AGENT_DRAFT_REVIEW_REQUIRED", "사실 확인이 필요합니다."
        )
    )
    monkeypatch.setattr(publication_routes, "publish_movie_agent_run", publish)
    with _client(monkeypatch) as client:
        response = client.post("/api/v1/dev/agent-runs/7/publish")
    assert response.status_code == 409
    assert response.json()["code"] == "AGENT_DRAFT_REVIEW_REQUIRED"
    assert publish.await_args.kwargs == {"force_preview": False}
