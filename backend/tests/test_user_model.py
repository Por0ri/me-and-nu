from app.db.base import Base
from app.models.user import User


def test_user_model_is_registered_on_metadata():
    assert User.__table__.metadata is Base.metadata
    assert "users" in Base.metadata.tables


def test_user_model_columns():
    columns = User.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "email",
        "nickname",
        "role",
        "hashed_password",
        "created_at",
        "updated_at",
    }
    assert columns["id"].primary_key is True
    assert columns["email"].unique is True
    assert columns["email"].nullable is False
    assert columns["hashed_password"].nullable is False
    assert "password" not in columns
