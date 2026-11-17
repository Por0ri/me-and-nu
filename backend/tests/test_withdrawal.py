"""회원 탈퇴 30일 유예 (API-009) · 복구 (API-010) · 유예가 끝난 계정 삭제.

PostgreSQL 세션 경로는 test_oauth_postgres_flow 의 일회용 SQLite 흐름(외래 키 켬)으로,
Redis 세션 경로는 test_redis_auth_api 의 메모리 Redis 로 확인합니다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.session_store import get_session_store
from app.core.config import settings
from app.models import AuthSession, ConsentHistory, CreatorChannel, OnboardingAnswersRecord, Tap, UserAccount
from app.repositories.auth_session_repository import create_auth_session
from app.services.withdrawal_service import WITHDRAWAL_GRACE_DAYS, purge_withdrawn_accounts
from test_oauth_postgres_flow import (  # noqa: F401
    AsyncSqliteSession, begin_login, callback, onboarding_flow, pending_onboarding, postgres_flow, signup_request,
)
from test_redis_auth_api import anonymous_session, login, redis_auth_environment, use_cookie  # noqa: F401

LEGACY_ANSWERS = {"q1": "world", "q2": "releases"}
WITHDRAW = {"mode": "grace"}
RESTORE = "/api/v1/auth/withdrawal-restorations"


def member(flow):
    """카카오로 가입하고 온보딩까지 마친 회원 → (지금 세션 CSRF 토큰, 회원 번호)."""
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, LEGACY_ANSWERS).status_code == 201
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "active"
    return current["csrfToken"], current["user"]["id"]


def withdrawn_member(flow):
    csrf, user_id = member(flow)
    response = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    return user_id


def remaining(flow, user_id):
    with Session(flow.engine) as session:
        def count(model, *where):
            return session.scalar(select(func.count()).select_from(model).where(*where))
        return {
            "user": count(UserAccount, UserAccount.user_id == user_id),
            "consents": count(ConsentHistory, ConsentHistory.user_id == user_id),
            "taps": count(Tap, Tap.user_id == user_id),
            "answers": count(OnboardingAnswersRecord),
        }


def account(flow, user_id):
    with Session(flow.engine) as session:
        return session.get(UserAccount, user_id)


def withdrawn_days_ago(flow, user_id, days):
    with Session(flow.engine) as session:
        session.execute(
            update(UserAccount).where(UserAccount.user_id == user_id)
            .values(withdrawn_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        session.commit()


def purge(flow, **options):
    async def job():
        with Session(flow.engine, expire_on_commit=False) as session:
            db = AsyncSqliteSession(session, flow.failure)
            report = await purge_withdrawn_accounts(db, **options)
            await db.commit()
            return report
    return asyncio.run(job())


def add_member(session, provider_id, *, status="active", withdrawn_days=None):
    user = UserAccount(
        auth_provider="kakao", provider_user_id=provider_id, nickname=provider_id, account_status=status,
        withdrawn_at=None if withdrawn_days is None else datetime.now(timezone.utc) - timedelta(days=withdrawn_days),
    )
    session.add(user)
    session.flush()
    return user.user_id


def test_withdrawal_disables_the_account_keeps_data_and_ends_the_session(onboarding_flow):
    flow = onboarding_flow
    csrf, user_id = member(flow)
    before = remaining(flow, user_id)
    assert all(before.values())
    old_cookie = flow.client.cookies.get(settings.session_cookie_name)

    response = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "withdrawal_pending" and body["mode"] == "grace"
    left = datetime.fromisoformat(body["scheduledDeleteAt"]) - datetime.now(timezone.utc)
    assert timedelta(days=WITHDRAWAL_GRACE_DAYS) - timedelta(minutes=1) < left <= timedelta(days=WITHDRAWAL_GRACE_DAYS)
    assert flow.client.cookies.get(settings.session_cookie_name) is None
    assert remaining(flow, user_id) == before   # 유예 동안 데이터는 그대로
    stored = account(flow, user_id)
    assert stored.account_status == "withdrawn" and stored.withdrawn_at is not None

    assert flow.client.get("/api/v1/auth/session").json()["authenticated"] is False
    use_cookie(flow.client, old_cookie)   # 예전 쿠키를 다시 써도
    assert flow.client.get("/api/v1/users/me").status_code == 401


def test_withdrawal_needs_onboarding_csrf_and_grace_mode(onboarding_flow):
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
        ({"mode": "immediate"}, {"X-CSRF-Token": csrf}, 422, "WITHDRAWAL_MODE_UNSUPPORTED"),
    ]:
        response = flow.client.delete("/api/v1/users/me", params=params, headers=headers)
        assert (response.status_code, response.json()["code"]) == (status, code), params
    assert account(flow, user_id).account_status == "active"   # 거절된 요청은 아무것도 안 바꿈


def test_withdrawal_is_refused_for_an_account_with_a_creator_channel(onboarding_flow):
    flow = onboarding_flow
    csrf, user_id = member(flow)
    with Session(flow.engine) as session:
        session.add(CreatorChannel(user_id=user_id, channel_name="채널", topic_id=1))
        session.commit()
    response = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409 and response.json()["code"] == "WITHDRAWAL_BLOCKED"
    assert account(flow, user_id).account_status == "active"


def test_login_during_the_grace_period_only_gets_a_restoration_session(onboarding_flow):
    flow = onboarding_flow
    user_id = withdrawn_member(flow)

    response = callback(flow, begin_login(flow))
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3000/pivot/login"
    current = flow.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "restoration_pending"
    assert current["authenticated"] is False and current["user"] is None and current["onboardingCompleted"] is True
    assert datetime.fromisoformat(current["scheduledDeleteAt"]) > datetime.now(timezone.utc)
    assert account(flow, user_id).account_status == "withdrawn"   # 로그인만으로는 복구 안 됨

    assert flow.client.get("/api/v1/users/me").status_code == 401   # 일반 API 는 비로그인과 같음
    denied = flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": current["csrfToken"]})
    assert denied.status_code == 401

    assert flow.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": current["csrfToken"]}).status_code == 204
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "anonymous"
    assert account(flow, user_id).account_status == "withdrawn"


def test_restoration_brings_the_account_and_data_back(onboarding_flow):
    flow = onboarding_flow
    user_id = withdrawn_member(flow)
    before = remaining(flow, user_id)
    callback(flow, begin_login(flow))
    current = flow.client.get("/api/v1/auth/session").json()
    old_cookie = flow.client.cookies.get(settings.session_cookie_name)

    response = flow.client.post(RESTORE, headers={"X-CSRF-Token": current["csrfToken"]})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "user": {"id": user_id, "accountType": "user"}, "status": "active", "onboardingCompleted": True,
    }
    assert flow.client.cookies.get(settings.session_cookie_name) != old_cookie   # 세션을 새로 줌
    now = flow.client.get("/api/v1/auth/session").json()
    assert now["sessionState"] == "active" and now["authenticated"] is True and now["scheduledDeleteAt"] is None
    assert flow.client.get("/api/v1/users/me").status_code == 200
    assert remaining(flow, user_id) == before
    stored = account(flow, user_id)
    assert stored.account_status == "active" and stored.withdrawn_at is None
    assert purge(flow, apply=True)["targets"] == 0

    use_cookie(flow.client, old_cookie)   # 복구에 쓴 세션은 끝남
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "anonymous"


def test_restoration_needs_a_restoration_session_and_csrf(onboarding_flow):
    flow = onboarding_flow
    assert flow.client.post(RESTORE).json()["code"] == "AUTH_REQUIRED"   # 세션 자체가 없음

    csrf = flow.client.get("/api/v1/auth/session").json()["csrfToken"]
    anonymous = flow.client.post(RESTORE, headers={"X-CSRF-Token": csrf})
    assert anonymous.status_code == 403 and anonymous.json()["code"] == "RESTORATION_SESSION_REQUIRED"

    csrf, _ = member(flow)
    active = flow.client.post(RESTORE, headers={"X-CSRF-Token": csrf})
    assert active.status_code == 403 and active.json()["code"] == "RESTORATION_SESSION_REQUIRED"

    assert flow.client.delete("/api/v1/users/me", params=WITHDRAW, headers={"X-CSRF-Token": csrf}).status_code == 202
    callback(flow, begin_login(flow))
    missing = flow.client.post(RESTORE)
    assert missing.status_code == 403 and missing.json()["code"] == "CSRF_INVALID"


def test_restoration_is_refused_after_the_deadline(onboarding_flow):
    flow = onboarding_flow
    user_id = withdrawn_member(flow)
    callback(flow, begin_login(flow))
    csrf = flow.client.get("/api/v1/auth/session").json()["csrfToken"]
    withdrawn_days_ago(flow, user_id, WITHDRAWAL_GRACE_DAYS + 1)   # 세션을 받은 뒤 기한이 지남

    response = flow.client.post(RESTORE, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409 and response.json()["code"] == "RESTORATION_EXPIRED"
    assert account(flow, user_id).account_status == "withdrawn"


def test_login_after_the_deadline_is_unavailable_until_the_account_is_purged(onboarding_flow):
    flow = onboarding_flow
    user_id = withdrawn_member(flow)
    withdrawn_days_ago(flow, user_id, WITHDRAWAL_GRACE_DAYS)

    response = callback(flow, begin_login(flow))
    assert response.status_code == 303
    assert response.headers["location"].endswith("/pivot/login?error=AUTH_ACCOUNT_UNAVAILABLE")

    assert purge(flow, apply=True) == {"mode": "apply", "targets": 1, "deleted": 1, "blocked_user_ids": []}
    assert remaining(flow, user_id) == {"user": 0, "consents": 0, "taps": 0, "answers": 0}

    response = callback(flow, begin_login(flow))   # 지워진 뒤엔 새 회원으로 온보딩부터
    assert response.headers["location"] == "http://localhost:3000/pivot/consent"
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "onboarding_pending"


def test_purge_previews_by_default_and_deletes_only_expired_withdrawals(onboarding_flow):
    flow = onboarding_flow
    with Session(flow.engine) as session:
        active = add_member(session, "active")
        waiting = add_member(session, "waiting", status="withdrawn", withdrawn_days=WITHDRAWAL_GRACE_DAYS - 1)
        expired = add_member(session, "expired", status="withdrawn", withdrawn_days=WITHDRAWAL_GRACE_DAYS + 1)
        session.commit()

    assert purge(flow) == {"mode": "dry_run", "targets": 1, "deleted": 0, "blocked_user_ids": []}
    assert account(flow, expired) is not None   # 미리보기는 안 지움

    assert purge(flow, apply=True)["deleted"] == 1
    assert account(flow, expired) is None
    assert account(flow, waiting) is not None and account(flow, active) is not None


def test_purge_by_user_id_skips_the_wait_but_only_for_withdrawn_accounts(onboarding_flow):
    flow = onboarding_flow
    with Session(flow.engine) as session:
        active = add_member(session, "active")
        waiting = add_member(session, "waiting", status="withdrawn", withdrawn_days=1)
        session.commit()

    assert purge(flow, apply=True, user_id=active)["targets"] == 0
    assert account(flow, active) is not None
    assert purge(flow, apply=True, user_id=waiting)["deleted"] == 1
    assert account(flow, waiting) is None


def test_purge_reports_an_account_that_a_foreign_key_still_blocks(onboarding_flow):
    flow = onboarding_flow
    with Session(flow.engine) as session:
        blocked = add_member(session, "blocked", status="withdrawn", withdrawn_days=WITHDRAWAL_GRACE_DAYS + 1)
        expired = add_member(session, "expired", status="withdrawn", withdrawn_days=WITHDRAWAL_GRACE_DAYS + 1)
        session.add(CreatorChannel(user_id=blocked, channel_name="채널", topic_id=1))
        session.commit()

    report = purge(flow, apply=True)
    assert report == {"mode": "apply", "targets": 2, "deleted": 1, "blocked_user_ids": [blocked]}
    assert account(flow, blocked) is not None and account(flow, expired) is None


def test_redis_withdrawal_ends_this_session_and_other_devices_stop_working(redis_auth_environment):
    environment = redis_auth_environment
    environment.user.onboarding_completed_at = datetime.now(timezone.utc)
    environment.database.scalar = AsyncMock(return_value=None)   # 크리에이터 채널 없음
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
    environment.database.commit.assert_awaited()
    assert environment.user.account_status == "withdrawn"
    assert len(environment.redis.values) == 1   # 지금 기기 세션만 지움

    use_cookie(environment.client, tokens[1])   # 다른 기기는 계정 상태 검사에서 막힘
    assert environment.client.get("/api/v1/users/me").status_code == 401


def test_redis_restoration_session_restores_the_account(redis_auth_environment):
    environment = redis_auth_environment
    user = environment.user
    user.onboarding_completed_at = datetime.now(timezone.utc)
    user.account_status = "withdrawn"
    user.withdrawn_at = datetime.now(timezone.utc) - timedelta(days=1)
    environment.database.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=Mock(return_value=user)))
    store = get_session_store(environment.redis)
    _, raw_token = asyncio.run(create_auth_session(
        environment.database, user_id=user.user_id, session_state="restoration_pending", store=store,
    ))
    use_cookie(environment.client, raw_token)

    current = environment.client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "restoration_pending" and current["scheduledDeleteAt"] is not None
    assert environment.client.get("/api/v1/users/me").status_code == 401

    response = environment.client.post(RESTORE, headers={"X-CSRF-Token": current["csrfToken"]})
    assert response.status_code == 200, response.text
    assert user.account_status == "active" and user.withdrawn_at is None
    assert len(environment.redis.values) == 1   # 복구 대기 세션은 지우고 일반 세션 하나
    assert not any(isinstance(call.args[0], AuthSession) for call in environment.database.add.call_args_list)
    assert environment.client.get("/api/v1/auth/session").json()["sessionState"] == "active"
