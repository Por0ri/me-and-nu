"""Exercise fixture ingestion and API services using only an in-memory database."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models import (
    AgentRun, Content, ContentSlide, ContentReaction, ContentSource, ContentTag, Draft,
    Policy, SavedItem, Subtopic, Tap, Topic, UserAccount,
)
from app.seeds import fe_integration as seed
from app.services import bookmark_service, feed_service, reaction_service


class AsyncAdapter:
    def __init__(self, session, *, fail_second=False):
        self.session = session
        self.fail_second = fail_second

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def get(self, model, identity):
        return self.session.get(model, identity)

    def add(self, instance):
        self.session.add(instance)

    async def flush(self):
        if self.fail_second and any(
            isinstance(item, Content) and item.title == seed.FIXTURES[1]["title"]
            for item in self.session.new
        ):
            raise RuntimeError("injected failure on second fixture")
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def refresh(self, instance):
        self.session.refresh(instance)

    async def rollback(self):
        self.session.rollback()


MODELS = (Topic, Subtopic, AgentRun, Draft, Content, ContentSlide, ContentSource, ContentTag,
          UserAccount, Policy, SavedItem, ContentReaction, Tap)


@pytest.fixture
def memory_db():
    # Clone DDL for SQLite without changing production ORM types or importing
    # app.db.session, Settings, an environment file, or any configured DB engine.
    metadata = MetaData()
    tables = {model.__table__ for model in MODELS}
    pending = list(tables)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            parent = foreign_key.column.table
            if parent not in tables:
                tables.add(parent)
                pending.append(parent)
    for table in tables:
        cloned = table.to_metadata(metadata)
        for column in cloned.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                if column.server_default is not None:
                    column.server_default = DefaultClause(text(str(column.server_default.arg).replace("::jsonb", "")))
            elif isinstance(column.type, BigInteger):
                column.type = Integer()
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def settings(**overrides):
    values = dict(
        oauth_public_api_origin=seed.TEST_ORIGIN,
        redis_key_prefix="me_nu_staging", session_backend="redis", redis_url="redis://test.invalid/0",
        enable_dev_auth_bypass=False, enable_dev_api=False, session_cookie_secure=True,
        session_cookie_name="menu_staging_session",
        database_url="postgresql+psycopg://test:test@test.invalid/me_nu",
    )
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("overrides", [
    {"oauth_public_api_origin": "https://api.me-nu.kr"},
    {"oauth_public_api_origin": "https://api.test.me-nu.kr.evil.invalid"},
    {"oauth_public_api_origin": "http://api.test.me-nu.kr"},
    {"redis_key_prefix": "me_nu"},
    {"session_backend": "postgres"},
    {"redis_url": None},
    {"enable_dev_auth_bypass": True},
    {"enable_dev_api": True},
    {"session_cookie_secure": False},
    {"session_cookie_name": "menu_session"},
    {"database_url": "postgresql+psycopg://test:secret@test.invalid/another_db"},
    {"database_url": "sqlite:///:memory:"},
    {"database_url": "not a url with secret"},
])
@pytest.mark.asyncio
async def test_unsafe_target_is_rejected_before_engine_creation(monkeypatch, overrides):
    def no_engine(*_args, **_kwargs):
        pytest.fail("An unsafe target must not create a database engine")
    monkeypatch.setattr(seed, "create_async_engine", no_engine)
    with pytest.raises(seed.FixtureError) as exc:
        await seed.run(confirmation=seed.TEST_HOST, expected_database="me_nu", apply=True, settings=settings(**overrides))
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("confirmation, database", [("", "me_nu"), ("api.me-nu.kr", "me_nu"), (seed.TEST_HOST, "")])
def test_target_requires_explicit_correct_confirmation_and_database(confirmation, database):
    with pytest.raises(seed.FixtureError):
        seed.validate_target(settings(), confirmation=confirmation, expected_database=database)


def test_verified_settings_and_database_name_are_accepted():
    seed.validate_target(settings(), confirmation=seed.TEST_HOST, expected_database="me_nu")


def counts(session):
    return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in MODELS}


@pytest.mark.asyncio
async def test_preview_writes_nothing_and_apply_reuses_exactly_two_ids(memory_db):
    with Session(memory_db) as session, session.begin():
        plan = await seed.seed_fe_integration(AsyncAdapter(session))
        assert plan["mode"] == "dry_run"
        assert plan["fixture_count"] == plan["new_contents"] == 2
        assert not session.new
        assert all(count == 0 for count in counts(session).values())
    with Session(memory_db) as session, session.begin():
        first = await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session, session.begin():
        before = counts(session)
        second = await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
        assert second["new_contents"] == 0
        assert second["existing_contents"] == 2
        assert second["content_ids"] == first["content_ids"]
        assert counts(session) == before
        assert before["Content"] == before["Draft"] == before["AgentRun"] == 2
        assert before["UserAccount"] == before["Policy"] == before["Tap"] == before["SavedItem"] == 0
        assert all(not row.input_payload["agent_invoked"] and row.node_count == 0
                   for row in session.scalars(select(AgentRun)))


@pytest.mark.asyncio
async def test_second_fixture_failure_rolls_back_entire_seed(memory_db):
    with pytest.raises(RuntimeError, match="second fixture"):
        with Session(memory_db) as session, session.begin():
            await seed.seed_fe_integration(AsyncAdapter(session, fail_second=True), apply=True)
    with Session(memory_db) as session:
        assert all(count == 0 for count in counts(session).values())


@pytest.mark.parametrize("change", ["hidden", "edited", "missing_source", "missing_tag"])
@pytest.mark.asyncio
async def test_changed_fixture_is_never_overwritten(memory_db, change):
    with Session(memory_db) as session, session.begin():
        report = await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session, session.begin():
        content = session.get(Content, report["content_ids"]["anime-01"])
        if change == "hidden":
            content.status = "hidden"
        elif change == "edited":
            content.body = "A human edited this record."
        else:
            model = ContentSource if change == "missing_source" else ContentTag
            session.delete(session.scalar(select(model).where(model.content_id == content.content_id)))
    with pytest.raises(seed.FixtureError):
        with Session(memory_db) as session, session.begin():
            await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session:
        content = session.get(Content, report["content_ids"]["anime-01"])
        assert counts(session)["Content"] == 2
        if change == "hidden":
            assert content.status == "hidden"
        elif change == "edited":
            assert content.body == "A human edited this record."


@pytest.mark.asyncio
async def test_source_marker_collision_preserves_unrelated_content(memory_db):
    with Session(memory_db) as session, session.begin():
        topic = Topic(topic_code="anime", topic_name="애니메이션", is_active=True)
        session.add(topic)
        session.flush()
        content = Content(topic_id=topic.topic_id, title="User owned", body="Do not change")
        session.add(content)
        session.flush()
        session.add(ContentSource(content_id=content.content_id, source_url=seed.source_url(seed.FIXTURES[0])))
    with pytest.raises(seed.FixtureError, match="source marker"):
        with Session(memory_db) as session, session.begin():
            await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session:
        assert counts(session)["Content"] == 1
        assert session.scalar(select(Content.body)) == "Do not change"


@pytest.mark.asyncio
async def test_real_services_share_ids_and_persist_bookmarks_between_sessions(memory_db):
    with Session(memory_db) as session, session.begin():
        report = await seed.seed_fe_integration(AsyncAdapter(session), apply=True)
        # Authentication/account setup belongs to the test harness, never the seed.
        user = UserAccount(nickname="integration test", auth_provider="kakao", provider_user_id="test-only-user",
                           onboarding_completed_at=datetime.now(timezone.utc))
        session.add(user)
        session.flush()
        user_id = user.user_id
        session.add(Tap(user_id=user_id, topic_id=report["topic_id"]))
    topic_id = report["topic_id"]
    content_ids = set(report["content_ids"].values())
    with Session(memory_db) as session:
        db = AsyncAdapter(session)
        feed = await feed_service.get_feed(db, user_id, topic_id, subtopic_id=None, cursor=None, limit=20)
        assert {card.id for card in feed.sections[0].contents} == content_ids
        for content_id in content_ids:
            detail = await feed_service.get_content_detail(db, user_id, topic_id, content_id)
            assert detail.display_mode == "full_body" and "고정 테스트 자료" in detail.body
            assert detail.content_id == content_id and detail.topic_id == topic_id
            assert {notice["code"] for notice in detail.notices} == {"DEV_PREVIEW", "REVIEW_REQUIRED"}
            result = await reaction_service.set_bookmark(db, user_id, topic_id, content_id)
            repeated = await reaction_service.set_bookmark(db, user_id, topic_id, content_id)
            assert repeated.saved_item_id == result.saved_item_id
    with Session(memory_db) as session:
        db = AsyncAdapter(session)
        saved = await bookmark_service.get_saved_bookmarks(db, user_id, topic_id=topic_id, q=None, cursor=None, limit=20)
        assert {item.content_id for item in saved.items} == content_ids
        feed = await feed_service.get_feed(db, user_id, topic_id, subtopic_id=None, cursor=None, limit=20)
        assert all(card.saved and card.saved_item_id for card in feed.sections[0].contents)
        for content_id in content_ids:
            assert (await feed_service.get_content_detail(db, user_id, topic_id, content_id)).my_reaction.bookmarked
            await reaction_service.clear_bookmark(db, user_id, topic_id, content_id)
            await reaction_service.clear_bookmark(db, user_id, topic_id, content_id)
    with Session(memory_db) as session:
        db = AsyncAdapter(session)
        saved = await bookmark_service.get_saved_bookmarks(db, user_id, topic_id=topic_id, q=None, cursor=None, limit=20)
        assert saved.items == []
        for content_id in content_ids:
            assert not (await feed_service.get_content_detail(db, user_id, topic_id, content_id)).my_reaction.bookmarked
        before = counts(session)
        # Re-seeding must not reset user bookmarks or create test identities.
        await seed.seed_fe_integration(db, apply=True)
        session.commit()
        assert counts(session) == before
        assert all(saved.status == "deleted" for saved in session.scalars(select(SavedItem)))
