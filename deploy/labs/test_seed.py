"""Run the labs fixture workflow against in-memory SQLite, never a configured DB."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
# app.db.__init__ imports the runtime session module. Its lazy engine must have
# an inert target, regardless of the caller's environment; no connection is made.
os.environ["DATABASE_URL"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/never_used"
from app.models import AgentRun, AuthSession, ConsentHistory, Content, ContentSource, ContentTag, Draft, Policy, SavedItem, Subtopic, Tap, Topic, UserAccount
from app.services import feed_service

spec = importlib.util.spec_from_file_location("labs_seed", Path(__file__).with_name("seed.py"))
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
MODELS = (AgentRun, AuthSession, ConsentHistory, Content, ContentSource, ContentTag, Draft, Policy, SavedItem, Subtopic, Tap, Topic, UserAccount)


class AsyncAdapter:
    def __init__(self, session, fail_second=False):
        self.session, self.fail_second = session, fail_second
    async def scalar(self, statement): return self.session.scalar(statement)
    async def scalars(self, statement): return self.session.scalars(statement)
    async def get(self, model, identity): return self.session.get(model, identity)
    def add(self, row): self.session.add(row)
    async def flush(self):
        if self.fail_second and any(isinstance(row, Content) and row.title == seed.FIXTURES[1]["title"] for row in self.session.new):
            raise RuntimeError("second fixture failure")
        self.session.flush()


@pytest.fixture
def memory_db():
    metadata = MetaData()
    tables = {model.__table__ for model in MODELS}
    pending = list(tables)
    while pending:
        for key in pending.pop().foreign_keys:
            parent = key.column.table
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
    yield engine
    engine.dispose()


def settings(**overrides):
    values = dict(oauth_public_api_origin="https://api.labs.me-nu.kr", oauth_frontend_origin="https://labs.me-nu.kr",
                  frontend_origins="https://labs.me-nu.kr,https://api.labs.me-nu.kr", redis_key_prefix="me_nu_labs",
                  session_backend="redis", session_cookie_name="menu_labs_session", enable_dev_auth_bypass=False,
                  enable_dev_api=False, session_cookie_secure=True,
                  database_url="postgresql+psycopg://me_nu_labs:test@postgres:5432/me_nu_labs",
                  redis_url="redis://:test@redis:6379/0")
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("change", [
    {"oauth_public_api_origin": "https://api.test.me-nu.kr"}, {"oauth_frontend_origin": "https://test.me-nu.kr"},
    {"redis_key_prefix": "me_nu_staging"}, {"session_cookie_name": "menu_staging_session"},
    {"enable_dev_auth_bypass": True}, {"enable_dev_api": True}, {"session_cookie_secure": False},
    {"database_url": "postgresql+psycopg://me_nu:test@postgres:5432/me_nu"},
    {"database_url": "postgresql+psycopg://me_nu_labs:secret@outside:5432/me_nu_labs"},
    {"database_url": "not a URL with secret"}, {"redis_url": "redis://:secret@oldredis:6379/0"},
])
@pytest.mark.asyncio
async def test_unsafe_target_rejected_before_engine(monkeypatch, change):
    monkeypatch.setattr(seed, "create_async_engine", lambda *_a, **_kw: pytest.fail("Engine created before guard"))
    with pytest.raises(seed.FixtureError) as error:
        await seed.run(apply=True, settings=settings(**change), environment={"MENU_DEPLOYMENT_ENV": "labs"})
    assert "secret" not in str(error.value)


def test_target_also_requires_labs_marker():
    with pytest.raises(seed.FixtureError): seed.validate_target(settings(), {})
    seed.validate_target(settings(), {"MENU_DEPLOYMENT_ENV": "labs"})


def counts(session):
    return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in MODELS}


@pytest.mark.asyncio
async def test_dry_run_idempotence_and_no_test_identities(memory_db):
    with Session(memory_db) as session, session.begin():
        report = await seed.seed_labs(AsyncAdapter(session))
        assert report["new_contents"] == 2 and report["new_policies"] == 4
        assert not session.new and all(value == 0 for value in counts(session).values())
    with Session(memory_db) as session, session.begin():
        first = await seed.seed_labs(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session, session.begin():
        before = counts(session)
        second = await seed.seed_labs(AsyncAdapter(session), apply=True)
        assert second["new_contents"] == second["new_policies"] == 0
        assert second["content_ids"] == first["content_ids"] and counts(session) == before
        assert before["Content"] == 2 and before["Policy"] == 4
        assert before["Draft"] == before["AgentRun"] == 1
        assert all(before[name] == 0 for name in ("UserAccount", "AuthSession", "ConsentHistory", "SavedItem", "Tap"))


@pytest.mark.asyncio
async def test_transaction_rolls_back_all_records(memory_db):
    with pytest.raises(RuntimeError, match="second fixture"):
        with Session(memory_db) as session, session.begin():
            await seed.seed_labs(AsyncAdapter(session, fail_second=True), apply=True)
    with Session(memory_db) as session:
        assert all(value == 0 for value in counts(session).values())


@pytest.mark.parametrize("change", ["body", "source", "policy", "tag"])
@pytest.mark.asyncio
async def test_existing_edits_are_preserved(memory_db, change):
    with Session(memory_db) as session, session.begin():
        report = await seed.seed_labs(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session, session.begin():
        if change == "body": session.get(Content, report["content_ids"]["anime-body"]).body = "User edit"
        elif change == "policy": session.scalar(select(Policy)).content = "User policy"
        elif change == "source": session.delete(session.scalar(select(ContentSource)))
        elif change == "tag": session.delete(session.scalar(select(ContentTag)))
    with Session(memory_db) as session:
        before = counts(session)
    with pytest.raises(seed.FixtureError):
        with Session(memory_db) as session, session.begin():
            await seed.seed_labs(AsyncAdapter(session), apply=True)
    with Session(memory_db) as session:
        assert counts(session) == before
        if change == "body": assert session.get(Content, report["content_ids"]["anime-body"]).body == "User edit"


@pytest.mark.asyncio
async def test_real_detail_contract_covers_both_modes(memory_db):
    with Session(memory_db) as session, session.begin():
        report = await seed.seed_labs(AsyncAdapter(session), apply=True)
        user = UserAccount(nickname="test harness", auth_provider="kakao", provider_user_id="test-harness")
        session.add(user)
        session.flush()
        user_id = user.user_id
        session.add(Tap(user_id=user_id, topic_id=report["topic_id"]))
    with Session(memory_db) as session:
        for key, content_id in report["content_ids"].items():
            detail = await feed_service.get_content_detail(AsyncAdapter(session), user_id, report["topic_id"], content_id)
            assert detail.display_mode == ("full_body" if key == "anime-body" else "link_excerpt")
            assert bool(detail.body) == (key == "anime-body")
            assert detail.image_url is None and detail.source_url.startswith("https://example.com/menu-labs-fixtures/")
