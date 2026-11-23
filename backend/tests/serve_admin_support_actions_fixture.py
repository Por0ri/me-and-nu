"""Loopback-only real HTTP/session/PostgreSQL fixture for support action E2E."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_admin_member_support_fixture import build_fixture, seed
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
import uvicorn

from app.api.v1.admin.support_router import get_support_write_db
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.models import AdminAccount, AdminActionLog, AuthSession, ContentReport, Inquiry, InquiryAnswer, UserAccount
from test_admin_postgres_integration import admin_pg

KEY = "isolated-support-actions-browser"

class Control(BaseModel):
    reset: bool = False
    revoked: bool = False
    lose_next_write_response: bool = False

def create_fixture(pg):
    app = build_fixture(pg, frontend_origins="http://127.0.0.1:3223,http://localhost:3223")
    factory = async_sessionmaker(create_async_engine(pg.url, poolclass=NullPool), expire_on_commit=False)
    async def writer():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_support_write_db] = writer
    state = {"lose_next_write_response": False}

    def reset():
        with Session(pg.engine) as db, db.begin():
            db.execute(delete(AdminActionLog))
        seed(pg.engine)
        now = datetime.now(timezone.utc)
        with Session(pg.engine) as db, db.begin():
            db.execute(update(AdminAccount).values(revoked_at=None))
            db.execute(update(UserAccount).where(UserAccount.user_id >= 900000).values(onboarding_completed_at=now))
            for uid, role in ((101, "owner"), (106, "other")):
                token = f"support-actions-fixture-{role}"
                db.add(AuthSession(auth_session_id=uid, user_id=uid, session_state="active",
                    session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
                    expires_at=now + timedelta(hours=8)))
    reset()

    @app.middleware("http")
    async def response_loss(request: Request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse(status_code=403, content={"code": "LOCAL_FIXTURE_ONLY"})
        lose = (state["lose_next_write_response"] and request.method == "POST" and
                request.url.path.startswith("/api/v1/admin/") and request.url.path.endswith(("/answer", "/actions")))
        if lose:
            state["lose_next_write_response"] = False
        result = await call_next(request)
        if lose and result.status_code == 200:
            return JSONResponse(status_code=503, content={"code": "SYNTHETIC_RESPONSE_LOSS", "message": "합성 응답 유실"},
                headers={"Access-Control-Allow-Origin": "http://127.0.0.1:3223", "Access-Control-Allow-Credentials": "true", "Cache-Control": "private, no-store"})
        return result

    @app.post("/__support_control")
    def control(value: Control, request: Request):
        if request.headers.get("X-Fixture-Key") != KEY:
            raise HTTPException(403)
        if value.reset:
            reset()
        with pg.engine.begin() as db:
            db.execute(update(AdminAccount).values(revoked_at=datetime.now(timezone.utc) if value.revoked else None))
        state["lose_next_write_response"] = value.lose_next_write_response
        return {"ok": True}

    @app.get("/__support_state")
    def evidence(request: Request):
        if request.headers.get("X-Fixture-Key") != KEY:
            raise HTTPException(403)
        with Session(pg.engine) as db:
            logs = db.scalars(select(AdminActionLog)).all()
            return {"auditCount": len(logs), "answerCount": len(db.scalars(select(InquiryAnswer)).all()),
                    "reportStatus": db.get(ContentReport, 1).status, "inquiryStatus": db.get(Inquiry, 2).status,
                    "auditContainsAnswerBody": any("SYNTHETIC_PRIVATE_REPLY" in str(row.after_value) + str(row.before_value) + row.rationale for row in logs)}
    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8201)
    args = parser.parse_args()
    fixture = admin_pg.__wrapped__()
    pg = next(fixture)
    try:
        app = create_fixture(pg)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"))
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(server.serve())
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
