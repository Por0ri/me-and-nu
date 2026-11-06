"""회원 탈퇴 (DELETE /users/me?mode=immediate): 즉시 삭제 · 세션 종료 · 같은 소셜 계정 재가입.

PostgreSQL 세션 경로는 test_oauth_postgres_flow 의 일회용 SQLite 흐름(외래 키 켬)으로,
Redis 세션 경로는 test_redis_auth_api 의 메모리 Redis 로 확인합니다.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuthSession, ConsentHistory, OnboardingAnswersRecord, Tap, UserAccount
from test_oauth_postgres_flow import begin_login, callback, onboarding_flow, pending_onboarding, postgres_flow, signup_request  # noqa: F401
from test_redis_auth_api import anonymous_session, login, redis_auth_environment, use_cookie  # noqa: F401

LEGACY_ANSWERS = {"q1": "world", "q2": "releases"}
WITHDRAW = {"mode": "immediate"}


def member(flow):
    """카카오로 가입하고 온보딩까지 마친 회원 → (지금 세션 CSRF 토큰, 회원 번호)."""
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, LEGACY_ANSWERS).status_code == 201
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "active"
    return current["csrfToken"], current["user"]["id"]


def remaining(flow, user_id):
    with Session(flow.engine) as session:
        def count(model, *where):
            return session.scalar(select(func.count()).select_from(model).where(*where))
        return {
            "user": count(UserAccount, UserAccount.user_id == user_id),
            "sessions": count(AuthSession, AuthSession.user_id == user_id),
            "consents": count(ConsentHistory, ConsentHistory.user_id == user_id),
            "taps": count(Tap, Tap.user_id == user_id),
            "answers": count(OnboardingAnswersRecord),
        }


def test_withdrawal_deletes_member_data_and_ends_the_session(onboarding_flow):
    flow = onboarding_flow
    csrf, user_id = member(flow)
    assert all(remaining(flow, user_id).values())   # 지우기 전에는 모두 있음
    old_cookie = flow.client.cookies.get(settings.session_cookie_name)

    response = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "withdrawn" and body["mode"] == "immediate"
    assert datetime.fromisoformat(body["scheduledDeleteAt"]) <= datetime.now(timezone.utc)
    assert flow.client.cookies.get(settings.session_cookie_name) is None
    assert remaining(flow, user_id) == {"user": 0, "sessions": 0, "consents": 0, "taps": 0, "answers": 0}

    assert flow.client.get("/api/v1/auth/session").json()["authenticated"] is False
    use_cookie(flow.client, old_cookie)   # 예전 쿠키를 다시 써도
    assert flow.client.get("/api/v1/users/me").status_code == 401


def test_same_social_account_signs_up_again_as_a_new_member(onboarding_flow):
    flow = onboarding_flow
    csrf, _ = member(flow)
    assert flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf}).status_code == 202

    response = callback(flow, begin_login(flow))
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3000/pivot/consent"   # 새 회원 → 약관부터
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "onboarding_pending"
    with Session(flow.engine) as session:
        users = session.scalars(select(UserAccount)).all()
        assert len(users) == 1 and users[0].onboarding_completed_at is None and users[0].nickname == "회원"
        assert session.scalar(select(func.count()).select_from(Tap)) == 0


def test_withdrawal_needs_onboarding_csrf_and_immediate_mode(onboarding_flow):
    flow = onboarding_flow
    assert flow.client.delete("/api/v1/users/me", params=WITHDRAW).json()["code"] == "AUTH_REQUIRED"

    csrf, _ = pending_onboarding(flow)
    pending = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert pending.status_code == 403 and pending.json()["code"] == "ONBOARDING_REQUIRED"

    assert signup_request(flow, csrf, LEGACY_ANSWERS).status_code == 201
    current = flow.client.get("/api/v1/auth/session").json()
    csrf, user_id = current["csrfToken"], current["user"]["id"]
    for params, headers, status, code in [
        (WITHDRAW, {}, 403, "CSRF_INVALID"),
        ({}, {"X-CSRF-Token": csrf}, 422, "VALIDATION_ERROR"),
        ({"mode": "later"}, {"X-CSRF-Token": csrf}, 422, "VALIDATION_ERROR"),
        ({"mode": "grace"}, {"X-CSRF-Token": csrf}, 422, "WITHDRAWAL_MODE_UNSUPPORTED"),
    ]:
        response = flow.client.delete("/api/v1/users/me", params=params, headers=headers)
        assert (response.status_code, response.json()["code"]) == (status, code), params
    assert remaining(flow, user_id)["user"] == 1   # 거절된 요청은 아무것도 지우지 않음


def test_redis_withdrawal_removes_this_session_and_other_sessions_stop_working(redis_auth_environment):
    environment = redis_auth_environment
    environment.user.onboarding_completed_at = datetime.now(timezone.utc)
    environment.database.execute = AsyncMock()   # 가짜 DB: 회원 줄 지우기만 받음
    tokens = []
    for _ in range(2):   # 두 기기에서 로그인
        environment.client.cookies.clear()
        csrf, _ = anonymous_session(environment)
        assert login(environment, csrf).status_code == 200
        tokens.append(environment.client.cookies.get(settings.session_cookie_name))
    assert len(environment.redis.values) == 2

    use_cookie(environment.client, tokens[0])
    csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    response = environment.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    assert environment.client.cookies.get(settings.session_cookie_name) is None
    statement = environment.database.execute.await_args.args[0]
    assert str(statement).startswith("DELETE FROM user_account")
    environment.database.commit.assert_awaited()
    assert len(environment.redis.values) == 1   # 지금 기기 세션만 지움

    environment.find_user.return_value = None   # DB 에서 회원이 지워진 뒤
    use_cookie(environment.client, tokens[1])
    assert environment.client.get("/api/v1/users/me").status_code == 401
