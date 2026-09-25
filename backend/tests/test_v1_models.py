from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.models import (
    AuthSession,
    ConsentHistory,
    ContentReaction,
    ContentTag,
    NotificationSetting,
    Policy,
    SavedItem,
    Tap,
    TapTopic,
)
from app.seeds.v1_local import LOCAL_POLICIES


def test_v1_models_are_registered_and_links_enforce_ownership():
    models = (
        AuthSession,
        Policy,
        ConsentHistory,
        NotificationSetting,
        Tap,
        TapTopic,
        ContentTag,
        ContentReaction,
        SavedItem,
    )
    assert all(model.__table__.metadata is Base.metadata for model in models)

    for model in (ContentReaction, SavedItem):
        assert any(
            isinstance(constraint, ForeignKeyConstraint)
            and {column.name for column in constraint.columns} == {"tap_id", "user_id"}
            and constraint.ondelete == "CASCADE"
            for constraint in model.__table__.constraints
        )

    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"user_id", "tap_id", "content_id"}
        for constraint in ContentReaction.__table__.constraints
    )
    assert {column.name for column in TapTopic.__table__.primary_key.columns} == {
        "tap_id",
        "subtopic_id",
    }


def test_local_policy_seed_covers_required_and_optional_consents():
    assert {row["policy_type"] for row in LOCAL_POLICIES} == {
        "terms",
        "privacy",
        "advertising",
        "marketing",
    }
    assert {row["policy_type"] for row in LOCAL_POLICIES if row["is_required"]} == {
        "terms",
        "privacy",
    }
    assert all(row["policy_version"] == "v1" for row in LOCAL_POLICIES)
    assert all(row["content"] for row in LOCAL_POLICIES)
