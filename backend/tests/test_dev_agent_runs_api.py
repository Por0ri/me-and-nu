"""Local Agent inspection exposes drafts and judgments without raw payloads."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.main import create_app
from app.models import AgentRun, AgentRunSource, AgentRunStep, Draft, JudgmentLog, UserAccount


class Rows:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


def _sample_rows():
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    run = AgentRun(
        agent_run_id=71,
        topic_id=1,
        agent_code="movie_review",
        agent_version="V2.0",
        status="succeeded",
        outcome="rejected_archive",
        input_payload={"private": "input"},
        output_payload={"private": "output"},
        error_message="private error",
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    draft = Draft(
        draft_id=12,
        agent_run_id=71,
        topic_id=1,
        title="탈락한 영화 리뷰",
        body="검토할 본문",
        status="discarded",
        factcheck_result={"requires_review": True, "items": ["출처 확인 필요"]},
    )
    source = AgentRunSource(
        agent_run_source_id=5,
        agent_run_id=71,
        source_type="metadata",
        source_url="https://www.themoviedb.org/movie/1",
    )
    judgment = JudgmentLog(
        judgment_log_id=8,
        agent_run_id=71,
        stage_name="judge",
        agent_code="movie_review",
        attempt_no=2,
        prompt_version="V2.0",
        judgment_result={"결과": "탈락", "이유": "근거 부족", "점수": {"topic_fit": 3}},
        is_adopted=False,
    )
    step = AgentRunStep(
        agent_run_step_id=9,
        agent_run_id=71,
        step_order=1,
        step_name="shape",
        attempt_no=1,
        step_result={"단계": "형태검사", "결과": ["본문이 너무 짧다"]},
    )
    return run, draft, source, judgment, step


def _client(monkeypatch, db, *, client_host="127.0.0.1", base_url="http://localhost:8000"):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    app = create_app(enable_dev_api=True)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: UserAccount(user_id=99)
    return TestClient(app, base_url=base_url, client=(client_host, 50000))


def test_dev_agent_run_list_and_detail_include_rejected_draft_and_judgment(monkeypatch):
    run, draft, source, judgment, step = _sample_rows()
    db = SimpleNamespace(
        scalars=AsyncMock(
            side_effect=[
                Rows([run]), Rows([draft]), Rows([source]), Rows([judgment]), Rows([step]),
                Rows([draft]), Rows([source]), Rows([judgment]), Rows([step]),
            ]
        ),
        scalar=AsyncMock(return_value=run),
    )
    with _client(monkeypatch, db) as client:
        listed = client.get("/api/v1/dev/agent-runs?limit=1&offset=0")
        detail = client.get("/api/v1/dev/agent-runs/71")

    assert listed.status_code == 200
    assert listed.json()["hasMore"] is False
    item = listed.json()["items"][0]
    assert detail.status_code == 200
    assert detail.json() == item
    assert item["agentVersion"] == "V2.0"
    assert item["outcome"] == "rejected_archive"
    assert item["draft"] == {
        "draftId": 12,
        "title": "탈락한 영화 리뷰",
        "body": "검토할 본문",
        "status": "discarded",
        "factcheckResult": {"requiresReview": True, "items": ["출처 확인 필요"]},
    }
    assert item["sourceUrls"] == ["https://www.themoviedb.org/movie/1"]
    assert item["judgments"] == [{
        "judgmentLogId": 8,
        "attemptNo": 2,
        "decision": "탈락",
        "reason": "근거 부족",
        "scores": {"topic_fit": 3},
        "isAdopted": False,
    }]
    assert item["steps"] == [{
        "stepOrder": 1,
        "stepName": "shape",
        "attemptNo": 1,
        "result": None,
        "issues": ["본문이 너무 짧다"],
        "reason": None,
    }]
    assert "inputPayload" not in item
    assert "outputPayload" not in item
    assert "errorMessage" not in item


def test_dev_agent_run_list_limit_is_bounded_and_missing_run_is_404(monkeypatch):
    db = SimpleNamespace(scalars=AsyncMock(return_value=Rows([])), scalar=AsyncMock(return_value=None))
    with _client(monkeypatch, db) as client:
        assert client.get("/api/v1/dev/agent-runs?limit=51").status_code == 422
        missing = client.get("/api/v1/dev/agent-runs/999")
    assert missing.status_code == 404
    assert missing.json()["code"] == "AGENT_RUN_NOT_FOUND"


def test_dev_agent_run_requires_loopback_client_and_host(monkeypatch):
    db = SimpleNamespace(scalars=AsyncMock(return_value=Rows([])))
    with _client(monkeypatch, db, client_host="198.51.100.7") as client:
        remote_client = client.get("/api/v1/dev/agent-runs")
    with _client(monkeypatch, db, base_url="http://example.com") as client:
        remote_host = client.get("/api/v1/dev/agent-runs")
    assert remote_client.status_code == 403
    assert remote_host.status_code == 403
    assert db.scalars.await_count == 0


def test_dev_agent_run_requires_authenticated_user(monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", False)
    app = create_app(enable_dev_api=True)

    async def override_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/v1/dev/agent-runs")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_dev_agent_run_routes_disappear_when_dev_api_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", False)
    app = create_app(enable_dev_api=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/dev/agent-runs").status_code == 404
        assert "/api/v1/dev/agent-runs" not in client.get("/openapi.json").json()["paths"]
