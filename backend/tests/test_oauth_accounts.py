from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Integer, MetaData, create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models.user import UserAccount
from app.services.oauth_account_service import resolve_oauth_user


def identity(**overrides):
    # The service consumes the provider adapter's immutable identity attributes.
    return SimpleNamespace(**{
        "provider": "kakao", "provider_user_id": "provider-user-1",
        "email": " new@example.com ", "nickname": " 새 회원 ", **overrides,
    })


class MemoryDatabase:
    def __init__(self, session):
        self.session = session
        self.commit = AsyncMock(side_effect=AssertionError("Callback must own commit"))

    async def scalar(self, statement):
        return self.session.scalar(statement)

    def add(self, user):
        self.session.add(user)

    async def flush(self):
        self.session.flush()

    async def refresh(self, user):
        self.session.refresh(user)

    @asynccontextmanager
    async def begin_nested(self):
        with self.session.begin_nested():
            yield


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite://")
    table = UserAccount.__table__.to_metadata(MetaData())
    # SQLite autoincrement needs INTEGER; production keeps its BIGINT identity.
    table.c.user_id.type = Integer()
    table.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield MemoryDatabase(session)
    engine.dispose()


def existing_user(memory_db, **overrides):
    user = UserAccount(**{
        "auth_provider": "kakao", "provider_user_id": "provider-user-1",
        "email": "existing@example.com", "nickname": "기존 이름",
        "account_status": "active", **overrides,
    })
    memory_db.session.add(user)
    memory_db.session.commit()
    return user


@pytest.mark.asyncio
async def test_new_social_user_is_normalized_and_requires_onboarding(memory_db):
    user = await resolve_oauth_user(memory_db, identity(email=" NEW@EXAMPLE.COM "))
    assert user.user_id is not None
    assert user.email == "new@example.com"
    assert user.nickname == "새 회원"
    assert user.auth_provider == "kakao"
    assert user.provider_user_id == "provider-user-1"
    assert user.account_type == "user" and user.account_status == "active"
    assert user.hashed_password is user.profile_image_url is None
    assert user.onboarding_completed_at is None
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_provider_identity_reuses_account_without_overwriting_profile(memory_db):
    stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    existing = existing_user(memory_db, onboarding_completed_at=stamp)
    user = await resolve_oauth_user(memory_db, identity(email="changed@example.com", nickname="공급자 이름"))
    assert user is existing
    assert user.email == "existing@example.com" and user.nickname == "기존 이름"
    assert user.onboarding_completed_at == stamp
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["kakao", "naver"])
@pytest.mark.parametrize("stored_email", [None, ""])
async def test_existing_identity_backfills_only_missing_email(memory_db, provider, stored_email):
    stamp = datetime(2026, 10, 20, tzinfo=timezone.utc)
    existing = existing_user(
        memory_db, auth_provider=provider, email=stored_email, onboarding_completed_at=stamp,
    )
    user = await resolve_oauth_user(
        memory_db, identity(provider=provider, email=" NEW@EXAMPLE.COM ", nickname="공급자 이름"),
    )
    assert user is existing
    assert user.email == "new@example.com"
    assert user.nickname == "기존 이름" and user.onboarding_completed_at == stamp
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("email", [None, "", "   ", "invalid-email", 123])
async def test_unusable_email_does_not_block_existing_identity(memory_db, email):
    existing = existing_user(memory_db, email=None)
    assert await resolve_oauth_user(memory_db, identity(email=email)) is existing
    assert existing.email is None and existing.nickname == "기존 이름"
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_provider", ["kakao", "naver", "local"])
async def test_existing_identity_email_conflict_preserves_both_accounts(memory_db, other_provider):
    existing = existing_user(memory_db, email=None)
    owner = existing_user(
        memory_db, auth_provider=other_provider,
        provider_user_id=None if other_provider == "local" else "other-provider-user",
        hashed_password="existing-hash" if other_provider == "local" else None,
        email="NEW@EXAMPLE.COM", nickname="이메일 소유자",
    )
    assert await resolve_oauth_user(memory_db, identity()) is existing
    assert existing.email is None and existing.nickname == "기존 이름"
    assert owner.email == "NEW@EXAMPLE.COM" and owner.nickname == "이메일 소유자"
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 2
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["naver", "local"])
async def test_other_provider_email_conflict_never_merges_accounts(memory_db, provider):
    existing = existing_user(
        memory_db, auth_provider=provider,
        provider_user_id=None if provider == "local" else "provider-user-1",
        hashed_password="existing-hash" if provider == "local" else None,
        email="NEW@EXAMPLE.COM",
    )
    with pytest.raises(ApiError) as error:
        await resolve_oauth_user(memory_db, identity())
    assert error.value.status_code == 409 and error.value.code == "AUTH_ACCOUNT_EXISTS"
    assert "NEW@EXAMPLE.COM" not in error.value.message and provider not in error.value.message
    assert existing.auth_provider == provider
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1


@pytest.mark.asyncio
async def test_same_provider_user_number_on_other_provider_is_a_different_account(memory_db):
    existing = existing_user(memory_db, auth_provider="naver", email=None)
    user = await resolve_oauth_user(memory_db, identity(email=None))
    assert user.user_id != existing.user_id
    assert user.auth_provider == "kakao"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["warned", "suspended", "banned", "withdrawn"])
async def test_nonactive_existing_account_is_unavailable(memory_db, status):
    existing = existing_user(memory_db, account_status=status, email=None)
    with pytest.raises(ApiError) as error:
        await resolve_oauth_user(memory_db, identity())
    assert error.value.status_code == 403 and error.value.code == "AUTH_ACCOUNT_UNAVAILABLE"
    assert existing.email is None
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_inside_the_withdrawal_grace_period_can_sign_in(memory_db):
    existing_user(memory_db, account_status="withdrawn", withdrawn_at=datetime.now(timezone.utc) - timedelta(days=29))
    user = await resolve_oauth_user(memory_db, identity())
    assert user.account_status == "withdrawn"   # 복구는 콜백 뒤 복구 API 에서만
    memory_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_past_the_withdrawal_grace_period_is_unavailable(memory_db):
    existing_user(memory_db, account_status="withdrawn", withdrawn_at=datetime.now(timezone.utc) - timedelta(days=30))
    with pytest.raises(ApiError) as error:
        await resolve_oauth_user(memory_db, identity())
    assert error.value.status_code == 403 and error.value.code == "AUTH_ACCOUNT_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("email", [None, "", "   "])
async def test_missing_email_does_not_block_new_account(memory_db, email):
    user = await resolve_oauth_user(memory_db, identity(email=email, nickname=None))
    assert user.email is None and user.nickname == "새사용자"


@pytest.mark.asyncio
async def test_nickname_is_trimmed_to_database_limit(memory_db):
    user = await resolve_oauth_user(memory_db, identity(nickname=" " + "가" * 60 + " "))
    assert user.nickname == "가" * 50


@pytest.mark.asyncio
async def test_invalid_supplied_email_does_not_create_account(memory_db):
    with pytest.raises(ApiError) as error:
        await resolve_oauth_user(memory_db, identity(email="private-invalid-email"))
    assert error.value.status_code == 401 and error.value.code == "AUTH_FAILED"
    assert "private-invalid-email" not in error.value.message
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 0


class RacingDatabase(MemoryDatabase):
    def __init__(self, session, winner):
        super().__init__(session)
        self.winner = winner

    @asynccontextmanager
    async def begin_nested(self):
        # Simulate a competing callback becoming visible between lookup and INSERT.
        self.session.add(self.winner)
        self.session.flush()
        async with super().begin_nested():
            yield


@pytest.mark.asyncio
async def test_same_identity_created_before_email_precheck_is_reused(memory_db):
    winner = UserAccount(
        auth_provider="kakao", provider_user_id="provider-user-1", email="new@example.com",
        nickname="동시 생성 계정", account_status="active",
    )

    class EmailLookupRace(MemoryDatabase):
        reads = 0

        async def scalar(self, statement):
            self.reads += 1
            if self.reads == 2:
                self.session.add(winner)
                self.session.flush()
            return await super().scalar(statement)

    assert await resolve_oauth_user(EmailLookupRace(memory_db.session), identity()) is winner
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "suspended"])
async def test_identity_insert_race_recovers_winner_and_rechecks_account_status(memory_db, status):
    winner = UserAccount(
        auth_provider="kakao", provider_user_id="provider-user-1", email="new@example.com",
        nickname="동시 생성 계정", account_status=status,
    )
    racing = RacingDatabase(memory_db.session, winner)
    if status == "active":
        assert await resolve_oauth_user(racing, identity()) is winner
    else:
        with pytest.raises(ApiError) as error:
            await resolve_oauth_user(racing, identity())
        assert error.value.code == "AUTH_ACCOUNT_UNAVAILABLE"
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1
    racing.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_insert_race_returns_conflict_and_keeps_outer_transaction_usable(memory_db):
    winner = UserAccount(
        auth_provider="naver", provider_user_id="other-provider-user", email="NEW@EXAMPLE.COM",
        nickname="기존 회원", account_status="active",
    )
    racing = RacingDatabase(memory_db.session, winner)
    with pytest.raises(ApiError) as error:
        await resolve_oauth_user(racing, identity())
    assert error.value.code == "AUTH_ACCOUNT_EXISTS"
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1
    winner.nickname = "트랜잭션 유지"
    memory_db.session.flush()
    racing.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_backfill_race_keeps_existing_login_and_outer_transaction_usable(memory_db):
    existing = existing_user(memory_db, email=None)
    winner = UserAccount(
        auth_provider="naver", provider_user_id="other-provider-user", email="NEW@EXAMPLE.COM",
        nickname="이메일 소유자", account_status="active",
    )
    racing = RacingDatabase(memory_db.session, winner)
    user = await resolve_oauth_user(racing, identity())
    assert user is existing
    # A nested rollback expires the modified ORM instance unless explicitly refreshed.
    assert not inspect(user).expired_attributes
    assert user.email is None and user.nickname == "기존 이름"
    assert winner.email == "NEW@EXAMPLE.COM"
    user.last_login_at = datetime.now(timezone.utc)
    await racing.flush()
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 2
    racing.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_insert_race_backfills_the_winners_missing_email(memory_db):
    winner = UserAccount(
        auth_provider="kakao", provider_user_id="provider-user-1", email=None,
        nickname="동시 생성 계정", account_status="active",
    )
    racing = RacingDatabase(memory_db.session, winner)
    assert await resolve_oauth_user(racing, identity()) is winner
    assert winner.email == "new@example.com" and winner.nickname == "동시 생성 계정"
    assert memory_db.session.scalar(select(func.count()).select_from(UserAccount)) == 1
    racing.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,constraint", [
    ("23505", "another_unique_index"),
    ("23514", "uq_user_account_email"),
])
async def test_backfill_does_not_hide_unrelated_integrity_errors(memory_db, code, constraint):
    existing_user(memory_db, email=None)
    original = Exception("unrelated integrity failure")
    original.sqlstate = code
    original.diag = SimpleNamespace(constraint_name=constraint)
    failure = IntegrityError("UPDATE", {}, original)

    class FailingDatabase(MemoryDatabase):
        async def flush(self):
            raise failure

    with pytest.raises(IntegrityError) as caught:
        await resolve_oauth_user(FailingDatabase(memory_db.session), identity())
    assert caught.value is failure


@pytest.mark.asyncio
async def test_email_backfill_recovers_psycopg_unique_violation(memory_db):
    existing = existing_user(memory_db, email=None)
    original = Exception("email already claimed")
    original.sqlstate = "23505"
    original.diag = SimpleNamespace(constraint_name="uq_user_account_email")

    class FailingDatabase(MemoryDatabase):
        async def flush(self):
            raise IntegrityError("UPDATE", {}, original)

    assert await resolve_oauth_user(FailingDatabase(memory_db.session), identity()) is existing
    assert not inspect(existing).expired_attributes
    assert existing.email is None


@pytest.mark.asyncio
async def test_backfill_does_not_hide_integrity_errors_before_savepoint(memory_db):
    existing = existing_user(memory_db, email=None)
    original = Exception("earlier pending write conflicts")
    original.sqlstate = "23505"
    original.diag = SimpleNamespace(constraint_name="uq_user_account_email")
    failure = IntegrityError("INSERT", {}, original)

    class FailingDatabase(MemoryDatabase):
        @asynccontextmanager
        async def begin_nested(self):
            raise failure
            yield

    with pytest.raises(IntegrityError) as caught:
        await resolve_oauth_user(FailingDatabase(memory_db.session), identity())
    assert caught.value is failure
    assert existing.email is None
