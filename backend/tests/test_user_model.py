from app.db.base import Base
from app.models.user import UserAccount


def test_user_model_is_registered_on_metadata():
    assert UserAccount.__table__.metadata is Base.metadata
    assert "user_account" in Base.metadata.tables


def test_user_model_columns():
    columns = UserAccount.__table__.columns

    assert set(columns.keys()) == {
        "user_id",
        "provider_user_id",
        "email",
        "nickname",
        "signup_channel",
        "account_status",
        "birth_date",
        "auth_provider",
        "hashed_password",
        "profile_image_url",
        "last_login_at",
        "onboarding_completed_at",
        "joined_at",
        "withdrawn_at",
        "withdrawal_reason",
    }
    assert columns["user_id"].primary_key is True
    assert columns["email"].nullable is True
    assert columns["hashed_password"].nullable is True
    assert "password" not in columns
