from unittest.mock import AsyncMock

import pytest

from app.core.security import hash_password
from app.models.user import UserAccount
from app.schemas.auth import RegisterRequest
from app.services import user_service


@pytest.mark.asyncio
async def test_authenticate_user_accepts_valid_password(
    monkeypatch: pytest.MonkeyPatch,
):
    user = UserAccount(
        user_id=1,
        email="test@example.com",
        nickname="테스터",
        signup_channel="consumer",
        auth_provider="local",
        hashed_password=hash_password("test1234"),
    )
    monkeypatch.setattr(
        user_service,
        "get_user_by_email",
        AsyncMock(return_value=user),
    )

    result = await user_service.authenticate_user(
        AsyncMock(),
        email="TEST@example.com",
        password="test1234",
    )

    assert result is user


@pytest.mark.asyncio
async def test_authenticate_user_rejects_invalid_password(
    monkeypatch: pytest.MonkeyPatch,
):
    user = UserAccount(
        user_id=1,
        email="test@example.com",
        nickname="테스터",
        signup_channel="consumer",
        auth_provider="local",
        hashed_password=hash_password("test1234"),
    )
    monkeypatch.setattr(
        user_service,
        "get_user_by_email",
        AsyncMock(return_value=user),
    )

    result = await user_service.authenticate_user(
        AsyncMock(),
        email="test@example.com",
        password="wrong-password",
    )

    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_rejects_social_account_password_login(
    monkeypatch: pytest.MonkeyPatch,
):
    user = UserAccount(
        user_id=1,
        provider_user_id="social-user-1",
        email="test@example.com",
        nickname="테스터",
        signup_channel="consumer",
        auth_provider="kakao",
        hashed_password=None,
    )
    monkeypatch.setattr(
        user_service,
        "get_user_by_email",
        AsyncMock(return_value=user),
    )

    result = await user_service.authenticate_user(
        AsyncMock(),
        email="test@example.com",
        password="test1234",
    )

    assert result is None


@pytest.mark.asyncio
async def test_register_user_commits_hashed_password(
    monkeypatch: pytest.MonkeyPatch,
):
    db = AsyncMock()
    user = UserAccount(
        user_id=1,
        email="test@example.com",
        nickname="테스터",
        signup_channel="consumer",
        auth_provider="local",
        hashed_password="stored-hash",
    )
    get_user_mock = AsyncMock(return_value=None)
    create_user_mock = AsyncMock(return_value=user)
    monkeypatch.setattr(user_service, "get_user_by_email", get_user_mock)
    monkeypatch.setattr(user_service, "create_user", create_user_mock)

    result = await user_service.register_user(
        db,
        RegisterRequest(
            email="TEST@example.com",
            password="test1234",
            nickname=" 테스터 ",
        ),
    )

    assert result is user
    get_user_mock.assert_awaited_once_with(db, "test@example.com")
    create_kwargs = create_user_mock.await_args.kwargs
    assert create_kwargs["email"] == "test@example.com"
    assert create_kwargs["nickname"] == "테스터"
    assert create_kwargs["signup_channel"] == "consumer"
    assert create_kwargs["hashed_password"] != "test1234"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(user)
