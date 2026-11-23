"""Loopback-only HTTP fixture with actual migrated PostgreSQL and read grants.

Requires disposable ADMIN_POSTGRES_TEST_URL accepted by admin_pg; no service DB
is accessed. This test-only entrypoint never registers controls in the app.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
import re
from pathlib import Path
import sys
from typing import Literal

os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
os.environ["ENABLE_DEV_AUTH_BYPASS"] = "false"
os.environ["ENABLE_DEV_API"] = "false"
os.environ["SESSION_BACKEND"] = "postgres"
os.environ["REDIS_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
import uvicorn

from app.api.session_store import get_session_store
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AdminAccount, AuthSession, Content, ContentReaction, ContentReport, ContentSource,
    Entity, Inquiry, OnboardingAnswersRecord, PreferenceTag, SavedItem, Tap,
    TapEntity, TapPreferenceTag, Topic, UserAccount,
)
from test_admin_postgres_integration import admin_pg


class Control(BaseModel):
    mode: Literal["normal", "empty", "failure"] = "normal"
    revoked: bool = False
    delay_detail: bool = False


def seed(engine, mode="normal"):
    now = datetime.now(timezone.utc)
    with Session(engine) as db, db.begin():
        for model in (ContentReport, Inquiry, SavedItem, ContentReaction, TapEntity, TapPreferenceTag,
                      OnboardingAnswersRecord, Tap, ContentSource, Content, Entity, PreferenceTag):
            db.execute(delete(model))
        db.execute(delete(UserAccount).where(UserAccount.user_id < 900000))
        if db.get(Topic, 1) is None:
            db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True))
        for i, (status, provider) in enumerate([
            ("active", "kakao"), ("warned", "naver"), ("suspended", "local"),
            ("banned", "apple"), ("withdrawn", "kakao"), ("active", "kakao"),
            ("active", "kakao"), ("active", "kakao"),
        ], 101):
            db.add(UserAccount(user_id=i, nickname=f"통합회원{i}", email=f"haru{i}@example.invalid",
                auth_provider=provider, provider_user_id=str(i) if provider != "local" else None,
                hashed_password="synthetic-not-a-password" if provider == "local" else None,
                account_status=status, withdrawn_at=now if status == "withdrawn" else None,
                withdrawal_reason="PRIVATE_WITHDRAWAL_REASON", joined_at=now - timedelta(days=i - 100),
                last_login_at=now - timedelta(hours=i - 100), onboarding_completed_at=now - timedelta(hours=12)))
        db.flush()
        db.add(Tap(tap_id=101, user_id=101, topic_id=1))
        db.add(Entity(entity_id=1, topic_id=1, entity_type="anime", name="통합검증 작품"))
        db.add(PreferenceTag(tag_id=1, topic_id=1, kind="genre", code="action", display_name="액션"))
        db.add(PreferenceTag(tag_id=2, topic_id=1, kind="theme", code="growth", display_name="성장"))
        for i in range(1, 7):
            db.add(Content(content_id=i, topic_id=1, title=f"admin-read fixture 콘텐츠 {i}",
                summary="격리 검증용 요약", body="실제 조회 본문 <script>window.fixtureXss=true</script>",
                production_type="ai" if i % 2 else "human", status="active", judgment_status="confirmed"))
        db.flush()
        db.add(OnboardingAnswersRecord(tap_id=101, schema_version=2, consumption_type="world"))
        db.add(TapEntity(tap_id=101, entity_id=1, topic_id=1))
        db.add_all([TapPreferenceTag(tap_id=101, tag_id=i, topic_id=1) for i in (1, 2)])
        db.add(SavedItem(saved_item_id=1, user_id=101, content_id=1, tap_id=101, status="kept"))
        db.add(ContentReaction(content_reaction_id=1, user_id=101, tap_id=101, content_id=1, is_liked=True))
        db.add(ContentSource(content_source_id=1, content_id=1, source_url="https://example.invalid/source", source_title="합성 출처"))
        if mode != "empty":
            for i, status in enumerate(["received", "rejected", "reviewing", "resolved", "received", "received"], 1):
                db.add(ContentReport(report_id=i, user_id=101, content_id=i, reason_code="wrong_info",
                    detail=f"신고 상세 {i} <img src=x onerror=alert(1)>", status=status,
                    created_at=now - timedelta(hours=i), updated_at=now - timedelta(minutes=i)))
            for i, (kind, status) in enumerate([("service", "answered"), ("bug", "received"), ("service", "closed")], 1):
                db.add(Inquiry(inquiry_id=i, user_id=101, inquiry_type=kind, status=status,
                    detail=f"문의 상세 {i} - 문의에만 있는 내용", created_at=now - timedelta(hours=i), updated_at=now))


def build_fixture(pg):
    pg.migrate()
    now = datetime.now(timezone.utc)
    with Session(pg.engine) as db, db.begin():
        for i, role in enumerate(["viewer", "operator", "admin", "member"], 900001):
            token = f"admin-read-fixture-{role}"
            db.add(UserAccount(user_id=i, nickname=f"test-{role}", auth_provider="kakao", provider_user_id=str(i),
                               account_status="active", joined_at=now - timedelta(days=365)))
            db.flush()
            if role != "member":
                db.add(AdminAccount(admin_account_id=i, user_id=i, role=role))
            db.add(AuthSession(auth_session_id=i, user_id=i, session_state="active", session_token_hash=hash_token(token),
                csrf_token_hash=hash_token(csrf_token_for_session(token)), expires_at=now + timedelta(hours=8)))
    seed(pg.engine)
    settings.redis_url = None
    settings.session_backend = "postgres"
    settings.frontend_origins = "http://127.0.0.1:3221,http://localhost:3221"
    settings.session_cookie_secure = False
    state = {"mode": "normal", "delay_detail": False}
    async_engine = create_async_engine(pg.url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def isolated_db():
        async with factory() as db:
            # Real authentication updates the existing session heartbeat. All
            # member/support business queries are SELECT-only.
            if state["mode"] == "failure":
                from app.core.exceptions import ApiError
                raise ApiError(503, "ADMIN_READ_UNAVAILABLE", "조회 정보를 확인할 수 없습니다.")
            yield db

    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_session_store] = lambda: None

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"code": "LOCAL_FIXTURE_ONLY"})
        if state["delay_detail"] and re.fullmatch(r"/api/v1/admin/(?:reports|inquiries|members)/[1-9][0-9]*", request.url.path):
            await asyncio.sleep(1.5)
        return await call_next(request)

    @app.post("/__control")
    def control(value: Control, request: Request):
        if request.headers.get("X-Fixture-Key") != "isolated-admin-read-browser":
            raise HTTPException(403)
        seed(pg.engine, value.mode)
        with pg.engine.begin() as db:
            db.execute(update(AdminAccount).values(revoked_at=datetime.now(timezone.utc) if value.revoked else None))
        state.update(mode=value.mode, delay_detail=value.delay_detail)
        return {"mode": value.mode, "revoked": value.revoked, "delay_detail": value.delay_detail}

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8199)
    args = parser.parse_args()
    fixture = admin_pg.__wrapped__()
    pg = next(fixture)
    try:
        server = uvicorn.Server(uvicorn.Config(build_fixture(pg), host="127.0.0.1", port=args.port,
                                               access_log=False, log_level="warning"))
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(server.serve())
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
