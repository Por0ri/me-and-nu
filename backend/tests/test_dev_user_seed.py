from collections import deque

import pytest

from app.models import (
    ConsentHistory,
    NotificationSetting,
    Policy,
    Subtopic,
    Tap,
    TapTopic,
    Topic,
    UserAccount,
)
from app.seeds import dev_user


class FakeAsyncSession:
    def __init__(self, scalar_results=(), get_results=()):
        self.scalar_results = deque(scalar_results)
        self.get_results = deque(get_results)
        self.added = []
        self.next_ids = {}

    async def scalar(self, _statement):
        return self.scalar_results.popleft()

    async def get(self, model, _identity):
        assert model is NotificationSetting
        return self.get_results.popleft()

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        for instance in self.added:
            primary_keys = list(instance.__table__.primary_key.columns)
            if len(primary_keys) != 1:
                continue
            key = primary_keys[0].name
            if getattr(instance, key) is None:
                table = instance.__table__.name
                self.next_ids[table] = self.next_ids.get(table, 0) + 1
                setattr(instance, key, self.next_ids[table])


def _catalog():
    topic = Topic(topic_id=7, topic_code="movie", topic_name="영화", is_active=True)
    subtopic = Subtopic(
        subtopic_id=11,
        topic_id=7,
        subtopic_name="영화 정보",
        depth_level=1,
    )
    return topic, subtopic


def _policies():
    return {
        "terms": Policy(policy_type="terms", policy_version="v2", is_required=True),
        "privacy": Policy(policy_type="privacy", policy_version="v3", is_required=True),
        "marketing": Policy(policy_type="marketing", policy_version="v1", is_required=False),
    }


@pytest.mark.asyncio
async def test_dev_user_seed_creates_reserved_identity_and_is_idempotent(monkeypatch):
    async def current_policies(_db):
        return _policies()

    monkeypatch.setattr(dev_user, "get_current_policies", current_policies)
    topic, subtopic = _catalog()
    db = FakeAsyncSession(
        [topic, subtopic, None, None, None, None, None, None],
        [None],
    )

    user = await dev_user.ensure_dev_user(db)

    assert user.email == dev_user.DEV_USER_EMAIL
    assert user.auth_provider == "kakao"
    assert user.provider_user_id == dev_user.DEV_USER_PROVIDER_ID
    assert user.hashed_password is None
    assert user.account_status == "active"
    assert user.signup_channel == "consumer"
    assert user.onboarding_completed_at is not None
    tap = next(item for item in db.added if isinstance(item, Tap))
    tap_topic = next(item for item in db.added if isinstance(item, TapTopic))
    notification = next(item for item in db.added if isinstance(item, NotificationSetting))
    consents = [item for item in db.added if isinstance(item, ConsentHistory)]
    assert tap.user_id == user.user_id and tap.topic_id == topic.topic_id
    assert (tap_topic.tap_id, tap_topic.subtopic_id) == (tap.tap_id, subtopic.subtopic_id)
    assert notification.user_id == user.user_id
    assert {(row.consent_type, row.policy_version, row.is_agreed) for row in consents} == {
        ("terms", "v2", True),
        ("privacy", "v3", True),
    }

    first_count = len(db.added)
    db.scalar_results.extend(
        [topic, subtopic, user, tap, tap_topic]
        + [row.consent_history_id for row in consents]
    )
    db.get_results.append(notification)

    assert await dev_user.ensure_dev_user(db) is user
    assert len(db.added) == first_count


@pytest.mark.asyncio
async def test_dev_user_seed_rejects_email_collision_without_mutation(monkeypatch):
    async def current_policies(_db):
        return _policies()

    monkeypatch.setattr(dev_user, "get_current_policies", current_policies)
    topic, subtopic = _catalog()
    existing = UserAccount(
        user_id=5,
        email=dev_user.DEV_USER_EMAIL,
        auth_provider="local",
        hashed_password="existing-hash",
        nickname="기존 사용자",
        signup_channel="consumer",
        account_status="active",
    )
    db = FakeAsyncSession([topic, subtopic, existing])

    with pytest.raises(RuntimeError, match="다른 계정"):
        await dev_user.ensure_dev_user(db)

    assert db.added == []
    assert existing.auth_provider == "local"
    assert existing.hashed_password == "existing-hash"


@pytest.mark.asyncio
async def test_dev_user_seed_requires_local_catalog_and_policies(monkeypatch):
    topic, subtopic = _catalog()
    no_topic = FakeAsyncSession([None])
    with pytest.raises(RuntimeError, match="영화 Topic"):
        await dev_user.ensure_dev_user(no_topic)
    assert no_topic.added == []

    async def no_policies(_db):
        return {}

    monkeypatch.setattr(dev_user, "get_current_policies", no_policies)
    no_policy = FakeAsyncSession([topic, subtopic])
    with pytest.raises(RuntimeError, match="필수 이용약관"):
        await dev_user.ensure_dev_user(no_policy)
    assert no_policy.added == []
