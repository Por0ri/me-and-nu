"""Registered analytics HTTP routes with actual isolated session/grant queries.

Only aggregate providers are stubbed here; SQL metric correctness is covered by
test_admin_analytics. No application DB, production identity or token is used.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_deps
from app.api.session_store import get_session_store
from app.api.v1.admin import analytics_router
from app.core.config import settings
from app.core.security import hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AdminAccount, AuthSession, UserAccount
from app.schemas.admin_analytics import (
    AnalyticsOverviewResponse, AttentionResponse, Distribution, GenerationSummary,
    Metric, Ranking, UsageMetrics,
)
from app.services import admin_analytics_service as service
from app.services.admin_permission_provider import get_admin_permission_provider

PATHS = (
    "/api/v1/admin/dashboard",
    "/api/v1/admin/dashboard/attention",
    "/api/v1/admin/analytics/overview",
    "/api/v1/admin/analytics/recommendation-performance",
    "/api/v1/admin/generation",
)
TOKEN = "isolated-admin-analytics-http-session"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def analytics_api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (UserAccount, AdminAccount, AuthSession):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add(UserAccount(user_id=7, nickname="isolated", auth_provider="kakao",
                           provider_user_id="analytics-fixture", account_status="active"))
        db.add(AdminAccount(admin_account_id=70, user_id=7, role="viewer"))
        db.add(AuthSession(auth_session_id=1, user_id=7, session_state="active",
                           session_token_hash=hash_token(TOKEN), csrf_token_hash="fixture",
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()

    class ReadSession:
        def __init__(self, db):
            self.db = db

        async def execute(self, statement):
            return self.db.execute(statement)

        async def scalar(self, statement):
            return self.db.scalar(statement)

        async def get(self, model, key):
            return self.db.get(model, key)

        async def commit(self):
            self.db.commit()

    async def isolated_db():
        with Session(engine) as db:
            yield ReadSession(db)

    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(settings, "session_backend", "postgres")
    monkeypatch.setattr(settings, "admin_recommendation_evaluation_path", None)
    monkeypatch.setattr(settings, "admin_service_evaluation_path", None)
    monkeypatch.setattr(settings, "admin_generation_snapshot_path", None)
    monkeypatch.setattr(settings, "event_data_source", "local_demo")
    window = service.analytics_window("7d", now=NOW)
    unknown = service.unavailable("Uncollected fixture metric", "not_collected")
    usage = UsageMetrics(
        active_users=Metric(availability="partial", reason="observed_activity_only", value=3, definition="fixture"),
        signup_feed_observation_rate=service.unavailable("fixture rate", "empty_denominator", unit="percent"),
        content_read_segments=Metric(availability="partial", reason="open_segments_not_pageviews", value=8, definition="fixture"),
    )
    ranking = Ranking(availability="available", definition="fixture")
    distribution = Distribution(availability="unavailable", reason="not_collected", definition="fixture")
    attention = AttentionResponse(
        generated_at=NOW, availability="partial", reason="notice_workflow_not_implemented",
        known_total=0, reports=0, inquiries=0, scheduled_notices=unknown, page=1, page_size=5, items=[],
    )
    aggregate = AnalyticsOverviewResponse(
        meta=service.metadata(window, "local_demo"), usage=usage, funnel=[],
        preference_distribution=distribution, device_distribution=distribution,
        top_contents=ranking, popular_searches=ranking, zero_result_searches=ranking,
        returning_user_rate=unknown, average_session_duration=unknown,
    )
    mocks = {}
    for name, value in {
        "overview": aggregate, "usage_metrics": usage, "top_contents": ranking,
        "preferences": distribution, "attention": attention,
        "generation": GenerationSummary(window_start_at=NOW - timedelta(hours=24), window_end_at=NOW),
    }.items():
        mocks[name] = AsyncMock(return_value=value)
        monkeypatch.setattr(service, name, mocks[name])
    performance = Mock(wraps=analytics_router.get_recommendation_performance)
    monkeypatch.setattr(analytics_router, "get_recommendation_performance", performance)
    generation = Mock(wraps=analytics_router.get_generation_status)
    monkeypatch.setattr(analytics_router, "get_generation_status", generation)

    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_session_store] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, mocks=mocks, performance=performance, generation=generation)
    engine.dispose()


def login(api):
    api.client.cookies.set(settings.session_cookie_name, TOKEN)


def test_service_dataset_is_authorized_and_routes_only_to_configured_service_path(analytics_api, monkeypatch, tmp_path):
    api = analytics_api
    path = PATHS[3] + "?dataset=service"
    assert api.client.get(path).status_code == 401
    login(api)
    target = str(tmp_path / "service-evaluation.json")
    monkeypatch.setattr(settings, "admin_service_evaluation_path", target)
    response = api.client.get(path)
    assert response.status_code == 200
    assert response.json()["source"] == "service_feedback"
    assert response.json()["reason"] == "artifact_missing"
    assert api.performance.call_args.kwargs["artifact_path"] == target
    assert api.performance.call_args.kwargs["dataset"] == "service"
    assert api.client.get(PATHS[3] + "?dataset=../../etc/passwd").status_code == 422


def assert_no_reads(api):
    api.generation.assert_not_called()
    for mock in api.mocks.values():
        mock.assert_not_awaited()
    api.performance.assert_not_called()


def assert_denied(api, status, code):
    for path in PATHS:
        response = api.client.get(path, headers={"X-Role": "admin", "X-Admin": "true"})
        assert response.status_code == status, response.text
        assert response.json()["code"] == code
        assert response.headers["cache-control"] == "private, no-store"
        assert "secret" not in response.text
    assert_no_reads(api)


@pytest.mark.parametrize("cookie", [None, "forged-session"])
def test_unauthenticated_cannot_query_aggregates(analytics_api, cookie):
    if cookie:
        analytics_api.client.cookies.set(settings.session_cookie_name, cookie)
    assert_denied(analytics_api, 401, "AUTH_REQUIRED")


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_real_current_database_roles_can_read_all_routes(analytics_api, role):
    with analytics_api.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET role=:role"), {"role": role})
    login(analytics_api)
    for path in PATHS:
        response = analytics_api.client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"


def test_grant_revocation_is_effective_without_rotating_session(analytics_api):
    login(analytics_api)
    for path in PATHS:
        assert analytics_api.client.get(path).status_code == 200
    with analytics_api.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP"))
    for mock in analytics_api.mocks.values():
        mock.reset_mock()
    analytics_api.performance.reset_mock()
    analytics_api.generation.reset_mock()
    assert_denied(analytics_api, 403, "ADMIN_REQUIRED")


def test_authenticated_user_without_server_grant_is_denied(analytics_api):
    with analytics_api.engine.begin() as db:
        db.execute(text("DELETE FROM admin_account"))
    login(analytics_api)
    assert_denied(analytics_api, 403, "ADMIN_REQUIRED")


@pytest.mark.parametrize("change", [
    "UPDATE user_account SET account_status='suspended'",
    "UPDATE user_account SET account_status='withdrawn'",
    "UPDATE auth_session SET session_state='onboarding_pending'",
    "UPDATE auth_session SET expires_at='2000-01-01'",
    "UPDATE auth_session SET revoked_at=CURRENT_TIMESTAMP",
])
def test_inactive_user_or_session_is_denied_before_aggregate(analytics_api, change):
    with analytics_api.engine.begin() as db:
        db.execute(text(change))
    login(analytics_api)
    assert_denied(analytics_api, 401, "AUTH_REQUIRED")


def test_development_bypass_does_not_enable_analytics(analytics_api, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    assert_denied(analytics_api, 401, "AUTH_REQUIRED")


@pytest.mark.parametrize("failure", ["session", "permission"])
def test_auth_storage_failure_is_closed(analytics_api, monkeypatch, failure):
    login(analytics_api)
    if failure == "session":
        monkeypatch.setattr(admin_deps, "find_auth_session", AsyncMock(side_effect=RuntimeError("secret credentials")))
        code = "ADMIN_AUTH_UNAVAILABLE"
    else:
        analytics_api.app.dependency_overrides[get_admin_permission_provider] = lambda: SimpleNamespace(
            can_read_contents=AsyncMock(side_effect=RuntimeError("secret credentials")))
        code = "ADMIN_PERMISSION_UNAVAILABLE"
    assert_denied(analytics_api, 503, code)


@pytest.mark.parametrize(("path", "service_name"), [
    (PATHS[0], "usage_metrics"), (PATHS[1], "attention"), (PATHS[2], "overview"),
])
def test_aggregate_storage_failure_is_503_not_a_zero_filled_success(analytics_api, path, service_name):
    login(analytics_api)
    analytics_api.mocks[service_name].side_effect = SQLAlchemyError("secret query/connection detail")
    response = analytics_api.client.get(path)
    assert response.status_code == 503
    assert response.json() == {"code": "ADMIN_ANALYTICS_UNAVAILABLE", "message": "통계 데이터를 조회할 수 없습니다. 잠시 후 다시 시도해 주세요."}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(("path", "params"), [
    (PATHS[0], {"period": "today"}),
    (PATHS[2], {"period": "all"}),
    (PATHS[3], {"audience": "staff"}),
    (PATHS[3], {"period": "1d"}),
    (PATHS[1], {"kind": "member"}),
    (PATHS[1], {"page": 0}),
    (PATHS[1], {"pageSize": 101}),
])
def test_invalid_queries_are_rejected_before_aggregation(analytics_api, path, params):
    login(analytics_api)
    response = analytics_api.client.get(path, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert_no_reads(analytics_api)


def test_route_query_contract_and_camel_case_unavailable_values(analytics_api):
    login(analytics_api)
    response = analytics_api.client.get(PATHS[0], params={"period": "30d"})
    data = response.json()
    assert response.status_code == 200, response.text
    assert data["meta"]["window"]["period"] == "30d"
    assert data["meta"]["scope"] == "connected_database"
    assert data["meta"]["eventDataSource"] == "local_demo"
    assert data["usage"]["contentReadSegments"]["value"] == 8
    assert data["usage"]["signupFeedObservationRate"]["value"] is None
    assert data["todayNotices"]["value"] is None
    assert data["recommendationPerformance"]["reason"] == "not_configured"
    assert data["recommendationPerformance"]["models"] == []
    assert data["recommendationPerformance"]["period"] == "30d"
    assert "recommendation_performance" not in data
    assert "private path" not in response.text

    analytics_api.client.get(PATHS[1], params={"kind": "report", "page": 3, "pageSize": 7})
    assert analytics_api.mocks["attention"].await_args.kwargs["kind"] == "report"
    assert analytics_api.mocks["attention"].await_args.kwargs["page"] == 3
    assert analytics_api.mocks["attention"].await_args.kwargs["page_size"] == 7
    analytics_api.client.get(PATHS[3], params={"period": "6m", "audience": "new"})
    assert analytics_api.performance.call_args.kwargs["period"] == "6m"
    assert analytics_api.performance.call_args.kwargs["audience"] == "new"


def test_openapi_advertises_registered_read_contracts_and_query_bounds(analytics_api):
    spec = analytics_api.client.get("/openapi.json").json()
    for path in PATHS:
        operation = spec["paths"][path]["get"]
        assert operation["security"]
        assert "200" in operation["responses"]
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]
        assert "503" in operation["responses"]
        assert not {"post", "patch", "put", "delete"}.intersection(spec["paths"][path])
    params = {item["name"]: item["schema"] for item in spec["paths"][PATHS[1]]["get"]["parameters"]}
    assert params["pageSize"]["maximum"] == 100
    assert params["page"]["minimum"] == 1


def test_generation_snapshot_endpoint_does_not_query_dashboard_aggregates_or_accept_client_path(analytics_api, monkeypatch):
    login(analytics_api)
    target = "/private/host/snapshot.json"
    monkeypatch.setattr(settings, "admin_generation_snapshot_path", target)
    response = analytics_api.client.get(PATHS[-1], params={"snapshot_path": "/etc/passwd", "path": "/other"})
    assert response.status_code == 200
    assert response.json()["availability"] == "unavailable"
    assert response.json()["daily"] is None
    assert response.headers["cache-control"] == "private, no-store"
    assert analytics_api.generation.call_args.kwargs == {"snapshot_path": target}
    for mock in analytics_api.mocks.values():
        mock.assert_not_called()
    analytics_api.performance.assert_not_called()
    assert "private" not in response.text and "passwd" not in response.text


def test_generation_snapshot_http_preserves_strict_camel_case_and_unknown_cost(analytics_api, monkeypatch, tmp_path):
    import json
    from test_admin_generation import snapshot

    login(analytics_api)
    data = snapshot()
    current = datetime.now(timezone.utc)
    data["generated_at"] = current.isoformat()
    data["policy_date"] = (current + timedelta(hours=9)).date().isoformat()
    target = tmp_path / "generation.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings, "admin_generation_snapshot_path", str(target))
    response = analytics_api.client.get(PATHS[-1])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "available"
    assert body["daily"]["knownEstimatedCostUsd"] == "0.0123456789"
    assert body["daily"]["totalEstimatedCostUsd"] is None
    assert body["daily"]["passes"] == 1
    assert body["jobs"]["rag"]["readyForStaging"] == 1
    assert body["runtime"]["articleWorker"] == "running"
    assert "schemaVersion" not in body and "purpose" not in body
    for mock in analytics_api.mocks.values():
        mock.assert_not_called()
