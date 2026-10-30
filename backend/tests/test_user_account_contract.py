import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from app.models.user import UserAccount
from app.schemas.auth import RegisterRequest
from app.schemas.onboarding import OnboardingRequest


def onboarding_payload(**changes):
    return {
        "nickname": "일반 회원",
        "accountType": "user",
        "topicId": 1,
        "answers": {"q1": "world", "q2": "releases"},
        "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True}],
        **changes,
    }


def test_single_user_type_accepts_one_answer_per_question():
    payload = OnboardingRequest.model_validate(onboarding_payload())
    assert payload.account_type == "user"
    assert payload.answers.q1 == "world"
    assert payload.answers.q2 == "releases"
    registration = RegisterRequest.model_validate({
        "email": "user@example.com", "password": "password123", "nickname": "일반 회원",
    })
    assert registration.role == "user"


@pytest.mark.parametrize("legacy_type", ["consumer", "creator"])
def test_onboarding_rejects_legacy_types(legacy_type):
    with pytest.raises(ValidationError):
        OnboardingRequest.model_validate(onboarding_payload(accountType=legacy_type))


@pytest.mark.parametrize("changes", [
    {"topicId": None}, {"topicId": 0}, {"topicId": -1},
])
def test_user_onboarding_still_requires_valid_topic_selection(changes):
    with pytest.raises(ValidationError):
        OnboardingRequest.model_validate(onboarding_payload(**changes))


@pytest.mark.parametrize("has_legacy_column", [False, True])
def test_user_persistence_works_before_and_after_account_column_removal(has_legacy_column):
    engine = create_engine("sqlite://")
    table = UserAccount.__table__
    try:
        table.create(engine)
        with engine.begin() as connection:
            if has_legacy_column:
                # The existing DB can retain its old default/check until migration.
                connection.exec_driver_sql(
                    "ALTER TABLE user_account ADD COLUMN signup_channel VARCHAR(20) "
                    "NOT NULL DEFAULT 'consumer' CHECK (signup_channel IN ('consumer', 'creator'))"
                )
            connection.execute(insert(table), [
                {"user_id": 1, "nickname": "기존 회원", "email": "one@example.com", "hashed_password": "hash"},
                {"user_id": 2, "nickname": "기존 창작자", "email": "two@example.com", "hashed_password": "hash"},
            ])
            if has_legacy_column:
                connection.exec_driver_sql("UPDATE user_account SET signup_channel = 'creator' WHERE user_id = 2")

        with Session(engine) as session:
            users = session.scalars(select(UserAccount).order_by(UserAccount.user_id)).all()
            assert [user.account_type for user in users] == ["user", "user"]
            users[1].nickname = "수정한 이름"
            session.add(UserAccount(
                user_id=3, nickname="신규 회원", email="three@example.com",
                auth_provider="local", hashed_password="hash",
            ))
            session.commit()
            session.expire_all()
            assert session.get(UserAccount, 2).nickname == "수정한 이름"
            assert session.get(UserAccount, 3).account_type == "user"
        if has_legacy_column:
            with engine.connect() as connection:
                assert connection.exec_driver_sql(
                    "SELECT signup_channel FROM user_account ORDER BY user_id"
                ).scalars().all() == ["consumer", "creator", "consumer"]
    finally:
        engine.dispose()
