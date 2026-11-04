"""Nickname rule (2~20 Korean, English or digit characters) and uniqueness among onboarded members,
at onboarding and on the My Page profile change (PATCH /users/me), on the disposable SQLite flow of
test_oauth_postgres_flow."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import UserAccount
from app.schemas.onboarding import OnboardingRequest, nickname_problem
from test_oauth_postgres_flow import onboarding_flow, pending_onboarding, postgres_flow, signup_request  # noqa: F401

LEGACY_ANSWERS = {"q1": "world", "q2": "releases"}


def request(nickname):
    return {
        "nickname": nickname, "accountType": "user", "topicId": 1, "answers": LEGACY_ANSWERS,
        "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True}],
    }


@pytest.mark.parametrize("nickname", ["미뉴덕후", "menu", "Menu12", "ㅋㅋ", "가" * 20, " 미뉴 "])
def test_rule_accepts_korean_english_digits_and_jamo(nickname):
    assert nickname_problem(nickname.strip()) is None
    assert OnboardingRequest.model_validate(request(nickname)).nickname == nickname.strip()


@pytest.mark.parametrize("nickname", ["", "미", "가" * 21, "미뉴 덕후", "미뉴!", "me_nu", "😀😀"])
def test_rule_rejects_length_spaces_and_symbols(nickname):
    assert nickname_problem(nickname.strip()) is not None
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(request(nickname))
    assert [error["loc"] for error in caught.value.errors()] == [("nickname",)]


def add_member(flow, nickname, *, onboarded=True):
    with Session(flow.engine) as session:
        session.add(UserAccount(nickname=nickname, auth_provider="kakao", provider_user_id=f"other-{nickname}",
                                onboarding_completed_at=datetime.now(timezone.utc) if onboarded else None))
        session.commit()


def check(flow, nickname):
    response = flow.client.get("/api/v1/onboarding/nickname", params={"nickname": nickname})
    assert response.status_code == 200, response.text
    return response.json()


def test_check_finds_onboarded_members_case_insensitively_and_ignores_pending_ones(onboarding_flow):
    flow = onboarding_flow
    pending_onboarding(flow)
    add_member(flow, "Menu")
    add_member(flow, "가입대기", onboarded=False)
    assert check(flow, "menu") == {"nickname": "menu", "available": False, "reason": "taken"}
    assert check(flow, "가입대기") == {"nickname": "가입대기", "available": True, "reason": None}
    assert check(flow, " 미뉴덕후 ") == {"nickname": "미뉴덕후", "available": True, "reason": None}
    assert check(flow, "미뉴!") == {"nickname": "미뉴!", "available": False, "reason": "invalid"}


def test_check_needs_a_pending_onboarding_session(onboarding_flow):
    response = onboarding_flow.client.get("/api/v1/onboarding/nickname", params={"nickname": "미뉴덕후"})
    assert response.status_code == 401


def test_completion_rejects_a_nickname_taken_since_the_check(onboarding_flow):
    flow = onboarding_flow
    csrf, _ = pending_onboarding(flow)
    add_member(flow, "미뉴덕후")
    taken = signup_request(flow, csrf, LEGACY_ANSWERS, nickname="미뉴덕후")
    assert taken.status_code == 409, taken.text
    assert taken.json()["code"] == "NICKNAME_TAKEN"
    assert [field["field"] for field in taken.json()["fields"]] == ["nickname"]
    with Session(flow.engine) as session:
        onboarded = select(func.count()).select_from(UserAccount).where(UserAccount.onboarding_completed_at.is_not(None))
        assert session.scalar(onboarded) == 1   # only the other member
    assert signup_request(flow, csrf, LEGACY_ANSWERS, nickname="미뉴덕후2").status_code == 201


def onboarded(flow, nickname):
    """Finishes onboarding as nickname; returns the CSRF token of the new active session."""
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, LEGACY_ANSWERS, nickname=nickname).status_code == 201
    return flow.client.get("/api/v1/auth/session").json()["csrfToken"]


def change(flow, csrf, nickname):
    return flow.client.patch("/api/v1/users/me", json={"nickname": nickname}, headers={"X-CSRF-Token": csrf})


@pytest.mark.parametrize("nickname", ["", "미", "가" * 21, "미뉴 덕후", "미뉴!", "me_nu", "😀😀"])
def test_profile_change_follows_the_onboarding_rule(onboarding_flow, nickname):
    flow = onboarding_flow
    csrf = onboarded(flow, "미뉴덕후")
    rejected = change(flow, csrf, nickname)
    assert rejected.status_code == 422, rejected.text
    assert [field["field"] for field in rejected.json()["fields"]] == ["nickname"]
    assert change(flow, csrf, " 미뉴덕후2 ").json()["nickname"] == "미뉴덕후2"


def my_check(flow, nickname):
    return flow.client.get("/api/v1/users/me/nickname", params={"nickname": nickname})


def test_my_page_check_uses_the_onboarding_rule_and_keeps_ones_own_name(onboarding_flow):
    flow = onboarding_flow
    onboarded(flow, "미뉴덕후")
    add_member(flow, "Menu")
    add_member(flow, "가입대기", onboarded=False)
    assert my_check(flow, "menu").json() == {"nickname": "menu", "available": False, "reason": "taken"}
    assert my_check(flow, "미뉴덕후").json() == {"nickname": "미뉴덕후", "available": True, "reason": None}   # one's own
    assert my_check(flow, "가입대기").json() == {"nickname": "가입대기", "available": True, "reason": None}
    assert my_check(flow, " 미뉴! ").json() == {"nickname": "미뉴!", "available": False, "reason": "invalid"}


def test_my_page_check_needs_an_onboarded_session(onboarding_flow):
    flow = onboarding_flow
    assert my_check(flow, "미뉴덕후").status_code == 401
    pending_onboarding(flow)
    assert my_check(flow, "미뉴덕후").status_code == 403


def test_profile_change_rejects_a_name_another_member_uses(onboarding_flow):
    flow = onboarding_flow
    csrf = onboarded(flow, "미뉴덕후")
    add_member(flow, "Menu")
    add_member(flow, "가입대기", onboarded=False)
    taken = change(flow, csrf, "menu")
    assert taken.status_code == 409, taken.text
    assert taken.json()["code"] == "NICKNAME_TAKEN"
    assert [field["field"] for field in taken.json()["fields"]] == ["nickname"]
    assert flow.client.get("/api/v1/users/me").json()["nickname"] == "미뉴덕후"   # unchanged
    assert change(flow, csrf, "가입대기").status_code == 200   # a pending account does not hold its name
    assert change(flow, csrf, "가입대기").status_code == 200   # and keeping one's own name is fine
