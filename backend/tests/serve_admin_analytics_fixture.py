"""Loopback-only real HTTP analytics fixture; never connects to a service DB.

Run from backend: python tests/serve_admin_analytics_fixture.py --port 8198
Build FE with NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8198, serve on 3220.
Cookies: menu_session=analytics-fixture-{viewer,operator,admin,member,anonymous}.
Only this test entrypoint installs the loopback control route and synthetic DB.
"""

import argparse
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Literal

# Override before importing application modules. The configured engine is never
# used: every request receives the private in-memory SQLite dependency below.
os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
os.environ["ENABLE_DEV_AUTH_BYPASS"] = "false"
os.environ["ENABLE_DEV_API"] = "false"
os.environ["SESSION_BACKEND"] = "postgres"
os.environ["CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import uvicorn

from app.api.session_store import get_session_store
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AdminAccount, AuthSession, UserAccount
from test_admin_analytics import aggregate_tables
from test_admin_recommendation_analytics import artifact as evaluation_fixture

CONTROL_KEY = "isolated-analytics-browser-tests"


class Control(BaseModel):
    mode: Literal["normal", "zero", "db_failure", "evaluation_missing", "shrinking_queue"] = "normal"
    revoked: bool = False
    delay_period: Literal["30d", "none"] = "none"
    delay_dataset: Literal["service", "none"] = "none"
    evaluation_variant: Literal["normal", "existing_only", "refreshed", "disjoint_users", "long_labels"] = "normal"
    service_variant: Literal["waiting", "published", "failed", "manifest_missing"] = "waiting"


def build_fixture(directory: Path):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (UserAccount, AdminAccount, AuthSession):
        model.__table__.create(engine)
    meta = aggregate_tables()
    meta.create_all(engine)  # Existing full UserAccount schema is preserved.
    state = {"mode": "normal", "delay_period": "none", "delay_dataset": "none"}
    artifact_path = directory / "evaluation.json"
    service_path = directory / "service-evaluation.json"
    pipeline_path = directory / "pipeline-status.json"
    now = datetime.now(timezone.utc)

    def reset_data(mode):
        with engine.begin() as db:
            for name in ("user_event", "search_log", "onboarding_answers", "tap", "content_report", "inquiry", "agent_run", "content"):
                db.execute(meta.tables[name].delete())
            db.execute(delete(UserAccount).where(UserAccount.user_id >= 10))
        if mode == "zero":
            return
        with Session(engine) as db:
            for i in range(10, 14):
                db.add(UserAccount(user_id=i, nickname=f"synthetic-{i}", auth_provider="kakao",
                    provider_user_id=f"analytics-data-{i}", account_status="active",
                    joined_at=now - timedelta(days=3), onboarding_completed_at=now - timedelta(days=2)))
            db.commit()
        with engine.begin() as db:
            def put(table, **row):
                db.execute(meta.tables[table].insert().values(**row))
            for i in range(1, 7):
                put("content", content_id=i, title=f"통합 검증 콘텐츠 {i}")
            for i, choice in enumerate(["ost", "relationships", "goods", "world"], 10):
                put("tap", tap_id=i, user_id=i, deleted_at=None)
                put("onboarding_answers", tap_id=i, schema_version=2, q1_choice=None,
                    consumption_type=choice, created_at=now - timedelta(days=2))
            event_id = 1
            for user in range(10, 14):
                put("user_event", user_event_id=event_id, user_id=user, content_id=1,
                    event_type="impression", event_value={"surface": "home_feed", "session_id": "test"},
                    occurred_at=now - timedelta(hours=2), received_at=now - timedelta(hours=2), data_source="local_demo")
                event_id += 1
            # Six different open counts and deterministic top-five ordering.
            for content in range(1, 7):
                for _ in range(7 - content):
                    put("user_event", user_event_id=event_id, user_id=10, content_id=content,
                        event_type="open", event_value={"surface": "detail", "session_id": "test"},
                        occurred_at=now - timedelta(hours=1), received_at=now - timedelta(hours=1), data_source="local_demo")
                    event_id += 1
            put("user_event", user_event_id=event_id, user_id=11, content_id=1, event_type="open",
                event_value={"surface": "detail"}, occurred_at=now - timedelta(days=10),
                received_at=now - timedelta(days=10), data_source="local_demo")
            # Out-of-source records must not affect displayed counts.
            put("user_event", user_event_id=event_id + 1, user_id=12, content_id=1, event_type="open",
                event_value={"surface": "detail"}, occurred_at=now - timedelta(hours=1),
                received_at=now - timedelta(hours=1), data_source="automated_test")
            for i in range(1, 7):
                put("search_log", search_log_id=i, search_text="검색 검증" if i < 4 else "결과 없는 검색",
                    result_count=2 if i < 4 else 0, searched_at=now - timedelta(hours=1), data_source="local_demo")
            for i in range(1, 8):
                put("content_report", report_id=i, status="reviewing" if i == 1 else "received",
                    reason_code="wrong_info", created_at=now - timedelta(days=10, hours=i), content_id=1,
                    detail="PRIVATE_REPORT_TEXT_MUST_NOT_APPEAR", user_id=10)
            for i in range(1, 3):
                put("inquiry", inquiry_id=i, status="received", inquiry_type="service",
                    created_at=now - timedelta(days=1, hours=i), detail="PRIVATE_INQUIRY_TEXT_MUST_NOT_APPEAR", user_id=11)
            for i, status in enumerate(["succeeded", "succeeded", "running", "failed"], 1):
                put("agent_run", agent_run_id=i, status=status, created_at=now - timedelta(hours=1))
            if mode == "shrinking_queue":
                db.execute(meta.tables["content_report"].delete().where(meta.tables["content_report"].c.report_id > 1))
                db.execute(meta.tables["inquiry"].delete())

    with Session(engine) as db:
        for i, role in enumerate(["viewer", "operator", "admin", "member"], 1):
            token = f"analytics-fixture-{role}"
            db.add(UserAccount(user_id=i, nickname=f"test-{role}", auth_provider="kakao",
                provider_user_id=f"analytics-auth-{role}", account_status="active",
                joined_at=now - timedelta(days=200), onboarding_completed_at=now - timedelta(days=200)))
            if role != "member":
                db.add(AdminAccount(admin_account_id=i, user_id=i, role=role))
            db.add(AuthSession(auth_session_id=i, user_id=i, session_state="active",
                session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
                expires_at=now + timedelta(hours=8)))
        token = "analytics-fixture-anonymous"
        db.add(AuthSession(auth_session_id=5, user_id=None, session_state="anonymous",
            session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
            expires_at=now + timedelta(hours=8)))
        db.commit()
    reset_data("normal")

    evaluation = evaluation_fixture.__wrapped__()
    evaluation["evaluatedAt"] = (now - timedelta(minutes=1)).isoformat()
    evaluation["evaluationId"] = "synthetic-browser-evaluation"
    evaluation["conditions"]["evaluationData"] = "격리 브라우저 검증용 합성 표본"
    evaluation["cohorts"][0]["definition"] = "같은 합성 표본의 전체 기록 입력 장면"
    evaluation["cohorts"][1]["definition"] = "같은 합성 표본의 온보딩 입력 장면"
    def publish_evaluation(variant="normal"):
        result = copy.deepcopy(evaluation)
        if variant == "existing_only":
            result["cohorts"] = result["cohorts"][:1]
        elif variant == "refreshed":
            result["evaluationId"] = "synthetic-browser-evaluation-refreshed"
            result["models"].reverse()
            result["models"].append({"modelId": "tag", "label": "태그 취향", "version": "rev2"})
            result["currentModelId"] = "tag"
            for cohort in result["cohorts"]:
                for model in cohort["models"]:
                    model["targetRanks"] = [3, 1, 8]
                cohort["models"].append({"modelId": "tag", "targetRanks": [1, 1, 2]})
        elif variant == "disjoint_users":
            result["conditions"]["cohortInterpretation"] = "disjoint_users"
            for cohort, digest in zip(result["cohorts"], ["c", "d"]):
                cohort["definition"] = f"서로 다른 합성 사용자 집단: {cohort['audience']}"
                cohort["caseSetSha256"] = digest * 64
        elif variant == "long_labels":
            for model in result["models"]:
                model["label"] = (model["modelId"] + "LongModelNameWithoutSpaces" * 4)[:100]
        # Exercise the same atomic replacement used by the real exporter.
        pending = artifact_path.with_suffix(".pending")
        pending.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        pending.replace(artifact_path)

    def publish_service_evaluation(variant="waiting"):
        # The same loopback-only fixture uses a separate content artifact.
        # No real recommendation state or production publisher is involved.
        if variant == "waiting":
            service_path.unlink(missing_ok=True)
        else:
            result = copy.deepcopy(evaluation)
            result.update(schemaVersion=2, evaluationKind="service", dataSource="local_demo", split="prospective",
                          evaluationId="synthetic-browser-service-evaluation")
            result["conditions"].update(aggregationUnit="content", cohortInterpretation="disjoint_users",
                evaluationData="격리 브라우저 검증용 서비스 콘텐츠 합성 표본 · local_demo",
                inputConditions="합성 추천 후보와 순위를 관측 전에 고정",
                targetPeriod="합성 예측 고정 후 7일 관측",
                groundTruth="관측 기간의 첫 서버 긍정 반응 콘텐츠 1개",
                candidatePolicy="동일한 합성 콘텐츠 후보 집합 · 상위 10개",
                recordSource="synthetic local_demo canonical reaction fixture")
            result["currentModelId"] = "stored"
            for model in result["models"]:
                if model["modelId"] == "v05":
                    model.update(modelId="stored", label="저장 추천 순서")
            for cohort in result["cohorts"]:
                cohort["definition"] = f"서로 다른 합성 서비스 사용자 집단: {cohort['audience']}"
                cohort["caseSetSha256"] = ("c" if cohort["audience"] == "existing" else "d") * 64
                cohort["sampleSize"] = 20
                for model in cohort["models"]:
                    if model["modelId"] == "v05":
                        model.update(modelId="stored", targetRanks=[1, 2, 10])
                    model["targetRanks"] = (model["targetRanks"] * 7)[:20]
            pending = service_path.with_suffix(".pending")
            pending.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            pending.replace(service_path)
        if variant == "manifest_missing":
            pipeline_path.unlink(missing_ok=True)
            return
        stamp = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schemaVersion": 1, "dataset": "service", "state": "failed" if variant == "failed" else variant,
            "reason": "collector_failed" if variant == "failed" else "insufficient_samples" if variant == "waiting" else None,
            "dataSource": "local_demo", "observationDays": 7, "minSamples": 20,
            "collectedCases": 50, "matureCases": 4 if variant == "waiting" else 45,
            "eligibleCases": 4 if variant == "waiting" else 40,
            "cohortSampleSizes": {"existing": 2 if variant == "waiting" else 20, "new": 2 if variant == "waiting" else 20},
            "lastEvaluationId": None if variant == "waiting" else "synthetic-browser-service-evaluation",
            "lastAttemptAt": stamp, "lastSuccessAt": stamp, "lastPublishedAt": None if variant == "waiting" else stamp,
        }
        pending = pipeline_path.with_suffix(".pending")
        pending.write_text(json.dumps(manifest), encoding="utf-8")
        pending.replace(pipeline_path)

    publish_evaluation()
    publish_service_evaluation()
    settings.admin_recommendation_evaluation_path = str(artifact_path)
    settings.admin_service_evaluation_path = str(service_path)
    settings.redis_url = None
    settings.event_data_source = "local_demo"
    settings.frontend_origins = "http://127.0.0.1:3220,http://localhost:3220"
    settings.session_cookie_secure = False
    app = create_app(enable_dev_api=False)

    class ReadSession:
        def __init__(self, db): self.db = db
        def fault(self, statement):
            if state["mode"] == "db_failure" and any(table in str(statement) for table in ("user_event", "search_log", "content_report", "agent_run")):
                raise SQLAlchemyError("isolated fixture aggregate storage unavailable")
        async def execute(self, statement):
            self.fault(statement)
            return self.db.execute(statement)
        async def scalar(self, statement):
            self.fault(statement)
            return self.db.scalar(statement)
        async def get(self, model, key): return self.db.get(model, key)
        async def commit(self): self.db.commit()
        async def flush(self): self.db.flush()
        def add(self, row):
            if isinstance(row, AuthSession) and row.auth_session_id is None:
                row.auth_session_id = (self.db.scalar(select(func.max(AuthSession.auth_session_id))) or 0) + 1
            self.db.add(row)

    async def isolated_db():
        with Session(engine) as db:
            yield ReadSession(db)
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_session_store] = lambda: None

    @app.middleware("http")
    async def delayed_query(request, call_next):
        if ((state["delay_period"] == "30d" and request.query_params.get("period") == "30d"
                or state["delay_dataset"] == "service" and request.query_params.get("dataset") == "service")
                and request.url.path.startswith("/api/v1/admin/")):
            await asyncio.sleep(2)
        return await call_next(request)

    @app.post("/__test__/control", include_in_schema=False)
    async def control(request: Request, payload: Control, x_fixture_key: str = Header()):
        if request.client.host not in {"127.0.0.1", "::1"} or x_fixture_key != CONTROL_KEY:
            raise HTTPException(403)
        state.update(mode=payload.mode, delay_period=payload.delay_period, delay_dataset=payload.delay_dataset)
        reset_data(payload.mode)
        publish_evaluation(payload.evaluation_variant)
        publish_service_evaluation(payload.service_variant)
        settings.admin_recommendation_evaluation_path = None if payload.mode == "evaluation_missing" else str(artifact_path)
        with engine.begin() as db:
            db.execute(update(AdminAccount).values(revoked_at=now if payload.revoked else None))
        return {"mode": payload.mode, "revoked": payload.revoked, "synthetic": True}

    @app.get("/__test__/ready", include_in_schema=False)
    async def ready():
        return {"synthetic": True, "database": "private-in-memory-sqlite", "api": "real-registered-routes"}
    return app, engine


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8198)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="menu-admin-analytics-fixture-") as directory:
        app, engine = build_fixture(Path(directory))
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            engine.dispose()
