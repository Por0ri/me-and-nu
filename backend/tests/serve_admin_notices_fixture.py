"""Loopback-only notice browser fixture with its own disposable PostgreSQL.

Creates one labelled tmpfs pgvector container and an isolated migrated schema;
never reads application database settings. Test-only login/controls require the
fixed fixture key and are never registered by app.main.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Literal
from uuid import uuid4

# Do not inherit application database/session credentials into this fixture.
os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
os.environ["ENABLE_DEV_AUTH_BYPASS"] = "false"
os.environ["ENABLE_DEV_API"] = "false"
os.environ["SESSION_BACKEND"] = "postgres"
os.environ["REDIS_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
import uvicorn

from app.api.session_store import get_session_store
from app.api.v1.admin.notices_router import get_notice_write_db
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token, set_session_cookie
from app.db.session import get_db
from app.jobs.publish_scheduled_notices import dispatch_one
from app.main import create_app
from app.models import AdminAccount, AdminActionLog, AuthSession, Notice, Notification, UserAccount
from app.schemas.admin_notice import AdminNoticeCreateRequest, AdminNoticeScope
from app.services.admin_notice_permission_provider import DatabaseAdminNoticePermissionProvider
from app.services.admin_notice_service import create_notice
from app.services.notice_delivery_service import lock_notice_publications
from test_admin_postgres_integration import admin_pg

KEY = "isolated-admin-notices-browser"
FRONTEND = "http://127.0.0.1:3301"
ROLES = {role: uid for uid, role in enumerate(("viewer", "operator", "admin", "member"), 900001)}


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lose_next_write_response: bool | None = None
    due_notice_id: int | None = Field(default=None, gt=0)
    dispatch_due: bool = False
    revoked: bool | None = None
    role: Literal["viewer", "operator", "admin"] = "operator"
    shutdown: bool = False


def docker(*args):
    result = subprocess.run(["docker", *args], check=True, text=True, capture_output=True)
    return result.stdout.strip()


async def build_fixture(pg):
    pg.migrate()
    now = datetime.now(timezone.utc)
    with Session(pg.engine) as db, db.begin():
        for role, uid in ROLES.items():
            token = "notice-fixture-" + role
            db.add(UserAccount(user_id=uid, nickname="합성 " + role, auth_provider="kakao", provider_user_id="notice-browser-" + role,
                               account_status="active", joined_at=now - timedelta(days=30), onboarding_completed_at=now))
            db.flush()
            if role != "member":
                db.add(AdminAccount(admin_account_id=uid, user_id=uid, role=role))
            db.add(AuthSession(auth_session_id=uid, user_id=uid, session_state="active",
                session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
                expires_at=now + timedelta(hours=8)))
    settings.redis_url = None
    settings.session_backend = "postgres"
    settings.frontend_origins = FRONTEND
    settings.session_cookie_secure = False
    engine = create_async_engine(pg.url, poolclass=NullPool, echo=False, hide_parameters=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for action, title, body in (
        ("save_draft", "[로컬 검증] 임시 저장 공지", "아직 회원에게 보이지 않는 임시 저장 내용입니다."),
        ("schedule", "[로컬 검증] 예약 공지", "정해진 시각에 공지와 회원 알림이 함께 게시됩니다."),
        ("publish", "[로컬 검증] 게시된 공지", "공지 API와 알림함 연결을 확인하는 합성 공지입니다.\n두 번째 문단입니다."),
    ):
        async with factory() as db:
            await create_notice(db, AdminNoticeCreateRequest(request_id=uuid4(), action=action, title=title, body=body,
                scheduled_at=now + timedelta(days=1) if action == "schedule" else None), user_id=ROLES["operator"],
                permissions=DatabaseAdminNoticePermissionProvider(),
                expected_scope=AdminNoticeScope(actor_user_id=str(ROLES["operator"]), admin_account_id=str(ROLES["operator"])))
    async def database():
        async with factory() as db: yield db
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_notice_write_db] = database
    app.dependency_overrides[get_session_store] = lambda: None
    state = {"lose_next_write_response": False, "server": None}

    def guard(request):
        if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
                or request.headers.get("X-Fixture-Key") != KEY):
            raise HTTPException(403)

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse(status_code=403, content={"code": "LOCAL_FIXTURE_ONLY"})
        lose = (state["lose_next_write_response"] and request.method == "POST"
                and request.url.path.startswith("/api/v1/admin/notices"))
        if lose: state["lose_next_write_response"] = False
        result = await call_next(request)
        if lose and result.status_code == 200:
            return JSONResponse(status_code=503,
                content={"code": "SYNTHETIC_RESPONSE_LOSS", "message": "합성 응답 유실: 실제 저장 후 응답만 유실되었습니다."},
                headers={"Access-Control-Allow-Origin": FRONTEND, "Access-Control-Allow-Credentials": "true",
                         "Cache-Control": "private, no-store"})
        return result

    @app.get("/__fixture_login/{role}")
    def login(role: Literal["viewer", "operator", "admin", "member"], key: str, request: Request):
        if (key != KEY or request.client is None or request.client.host not in {"127.0.0.1", "::1"}):
            raise HTTPException(403)
        destination = "/pivot/home?view=notifications" if role == "member" else "/admin/notices"
        response = RedirectResponse(FRONTEND + destination, status_code=303)
        response.headers["Cache-Control"] = "no-store"
        set_session_cookie(response, "notice-fixture-" + role)
        return response

    @app.post("/__notice_control")
    async def control(value: Control, request: Request):
        guard(request)
        if value.lose_next_write_response is not None:
            state["lose_next_write_response"] = value.lose_next_write_response
        if value.revoked is not None:
            async with factory() as db, db.begin():
                await lock_notice_publications(db)
                await db.execute(update(AdminAccount).where(AdminAccount.user_id == ROLES[value.role])
                    .values(revoked_at=datetime.now(timezone.utc) if value.revoked else None))
        if value.due_notice_id is not None:
            async with factory() as db, db.begin():
                await lock_notice_publications(db)
                notice = await db.scalar(select(Notice).where(Notice.notice_id == value.due_notice_id).with_for_update())
                if notice is None or notice.status != "scheduled":
                    raise HTTPException(409, "Only a scheduled fixture notice can become due")
                notice.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        count = deliveries = 0
        if value.dispatch_due:
            for _ in range(20):
                async with factory() as db, db.begin():
                    created = await dispatch_one(db, now=datetime.now(timezone.utc))
                if created is None: break
                count += 1
                deliveries += created
        if value.shutdown and state["server"] is not None:
            state["server"].should_exit = True
        return {"ok": True, "dispatched": count, "deliveries": deliveries}

    @app.get("/__notice_state")
    async def evidence(request: Request):
        guard(request)
        async with factory() as db:
            rows = (await db.execute(select(Notice.notice_id, Notice.title, Notice.status, Notice.scheduled_at,
                                            Notice.published_at).order_by(Notice.notice_id))).mappings().all()
            notices = [dict(row) for row in rows]
            duplicates = await db.scalar(select(func.count()).select_from(select(Notification.user_id, Notification.notice_id)
                .where(Notification.notice_id.is_not(None)).group_by(Notification.user_id, Notification.notice_id)
                .having(func.count() > 1).subquery()))
            audits = (await db.execute(select(AdminActionLog.action_type, AdminActionLog.target_id).where(
                AdminActionLog.target_type == "notice").order_by(AdminActionLog.admin_action_log_id))).mappings().all()
            deliveries = await db.scalar(select(func.count()).select_from(Notification).where(Notification.notice_id.is_not(None)))
        return {"notices": notices, "audits": [dict(row) for row in audits], "auditCount": len(audits),
                "deliveries": deliveries, "duplicateDeliveries": duplicates}
    return app, state, engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9018)
    args = parser.parse_args()
    if args.port != 9018: raise SystemExit("This fixture is fixed to loopback port 9018")
    name = "menu-notices-browser-" + uuid4().hex
    password = secrets.token_hex(24)
    created = False
    fixture = None
    try:
        docker("image", "inspect", "pgvector/pgvector:pg17")
        docker("run", "--detach", "--name", name, "--label", "menu.test=admin-notices-browser",
               "--env", "POSTGRES_USER=admin_test", "--env", f"POSTGRES_PASSWORD={password}",
               "--env", "POSTGRES_DB=admin_be_test", "--publish", "127.0.0.1::5432",
               "--tmpfs", "/var/lib/postgresql/data", "pgvector/pgvector:pg17")
        created = True
        for _ in range(120):
            try:
                docker("exec", name, "pg_isready", "-U", "admin_test", "-d", "admin_be_test")
                break
            except subprocess.CalledProcessError: time.sleep(0.25)
        else: raise RuntimeError("Disposable PostgreSQL did not become ready")
        binding = docker("port", name, "5432/tcp")
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", binding)
        if match is None: raise RuntimeError("Unexpected PostgreSQL binding")
        os.environ["ADMIN_POSTGRES_TEST_URL"] = f"postgresql+psycopg://admin_test:{password}@127.0.0.1:{match[1]}/admin_be_test"
        fixture = admin_pg.__wrapped__()
        pg = next(fixture)
        async def serve():
            app, state, engine = await build_fixture(pg)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                                   access_log=False, log_level="warning"))
            state["server"] = server
            print(json.dumps({"ready": True, "api": "http://127.0.0.1:9018", "frontend": FRONTEND,
                              "container": name, "pid": os.getpid(), "roles": list(ROLES)}), flush=True)
            try: await server.serve()
            finally: await engine.dispose()
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(serve())
    finally:
        if fixture is not None:
            try: next(fixture)
            except StopIteration: pass
        if created:
            assert re.fullmatch(r"menu-notices-browser-[0-9a-f]{32}", name)
            docker("rm", "--force", "--volumes", name)


if __name__ == "__main__":
    main()
