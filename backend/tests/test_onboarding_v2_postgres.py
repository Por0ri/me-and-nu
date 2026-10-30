"""Opt-in PostgreSQL checks; only a disposable local onboarding_test DB is allowed.

Set ONBOARDING_TEST_DATABASE_URL to the temporary container, never a service DB.
Each test owns a random schema and cleans up only that schema.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuthSession, ConsentHistory, Entity, EntityTag, NotificationSetting,
    OnboardingAnswersRecord, Policy, PreferenceTag, Tap, TapEntity,
    TapPreferenceTag, Topic, UserAccount,
)

TARGET = "e3a7b9c2d451"
BASE = "c0e5f3b8a124"


@pytest.fixture
def pg_schema():
    value = os.environ.get("ONBOARDING_TEST_DATABASE_URL")
    if not value:
        pytest.skip("requires an explicitly selected disposable PostgreSQL container")
    url = make_url(value)
    if (url.host not in {"127.0.0.1", "localhost"}
            or url.database != "onboarding_test" or url.query):
        pytest.fail("Only the disposable local onboarding_test DB is accepted")
    schema = "onboarding_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "onboarding_test"
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(scoped, poolclass=NullPool)

    def migrate(target, *, downgrade=False):
        # Keep migration logging isolated from the rest of the test process.
        config = Config()
        config.set_main_option("script_location", "alembic")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        yield engine, scoped, migrate
    finally:
        engine.dispose()
        # schema is generated above, never supplied by the caller.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_migration_preserves_legacy_answers_and_birth_date(pg_schema):
    engine, _, migrate = pg_schema
    migrate(BASE)
    with engine.begin() as db:
        user_id = db.scalar(text("INSERT INTO user_account (nickname,auth_provider,provider_user_id,birth_date) "
                                 "VALUES ('기존회원','kakao','migration-fixture','2000-01-02') RETURNING user_id"))
        db.execute(text("INSERT INTO topic (topic_id,topic_code,topic_name,is_active) VALUES (1,'anime','애니',true)"))
        tap_id = db.scalar(text("INSERT INTO tap (user_id,topic_id) VALUES (:user,1) RETURNING tap_id"), {"user": user_id})
        db.execute(text("INSERT INTO onboarding_answers (tap_id,q1_choice,q2_choice) VALUES (:tap,'ost','events')"), {"tap": tap_id})
        db.execute(text("INSERT INTO consent_history (user_id,consent_type,policy_version,is_agreed) "
                        "VALUES (:user,'terms','v1',true)"), {"user": user_id})
    migrate(TARGET)
    with engine.begin() as db:
        versions = set(db.scalars(text("SELECT version_num FROM alembic_version")))
        assert versions == {TARGET}
        assert db.execute(text("SELECT schema_version,q1_choice,q2_choice,consumption_type FROM onboarding_answers")).one() == (1,"ost","events",None)
        has_birth = db.scalar(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                                  "WHERE table_schema=current_schema() AND table_name='user_account' AND column_name='birth_date')"))
        assert has_birth
        assert str(db.scalar(text("SELECT birth_date FROM user_account"))) == "2000-01-02"
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
        # Old writers omit schema_version and still produce a valid v1 row.
        db.execute(text("INSERT INTO user_account (nickname,auth_provider,provider_user_id) VALUES ('구클라이언트','kakao','old-writer')"))
        other_tap = db.scalar(text("INSERT INTO tap (user_id,topic_id) SELECT user_id,1 FROM user_account WHERE provider_user_id='old-writer' RETURNING tap_id"))
        db.execute(text("INSERT INTO onboarding_answers (tap_id,q1_choice,q2_choice) VALUES (:tap,'world','releases')"), {"tap": other_tap})
        assert set(db.scalars(text("SELECT schema_version FROM onboarding_answers"))) == {1}
    migrate(BASE, downgrade=True)
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM onboarding_answers")) == 2
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
        assert str(db.scalar(text("SELECT birth_date FROM user_account WHERE user_id=:user"), {"user": user_id})) == "2000-01-02"


@pytest.fixture
def api_pg(pg_schema, monkeypatch):
    engine, url, migrate = pg_schema
    migrate(TARGET)
    for key, value in {"session_backend":"postgres", "redis_url":None,
                       "enable_dev_auth_bypass":False, "session_cookie_secure":False}.items():
        monkeypatch.setattr(settings, key, value)
    token = "pending-" + uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        user = UserAccount(nickname="대기회원", auth_provider="kakao", provider_user_id=uuid4().hex)
        db.add(user)
        db.flush()
        user_id = user.user_id
        db.add(AuthSession(user_id=user_id, session_state="onboarding_pending", session_token_hash=hash_token(token),
                           csrf_token_hash=hash_token(csrf_token_for_session(token)), created_at=now,
                           last_accessed_at=now, expires_at=now+timedelta(hours=1)))
        db.add_all([Topic(topic_id=1,topic_code="anime",topic_name="애니",is_active=True),
                    Topic(topic_id=2,topic_code="other",topic_name="다른 분야",is_active=True)])
        db.add_all([Policy(policy_type=kind,policy_version="v1",title=kind,content="동의",is_required=True) for kind in ("terms","privacy")])
        db.flush()
        db.add_all([Entity(entity_id=i,topic_id=1,name=f"가상 작품 {i}",aliases=["Alias 100%_literal"] if i==1 else [],is_active=True) for i in (1,2,3)])
        db.add(Entity(entity_id=4,topic_id=2,name="다른 작품",aliases=[],is_active=True))
        # Six genres and six themes: twelve chips stay within the cap of seven per kind.
        db.add_all([PreferenceTag(tag_id=i,topic_id=1,kind="genre" if i<=6 else "theme",code=f"tag-{i}",display_name=f"칩 {i}",sort_order=i,is_active=True) for i in range(1,13)])
        db.flush()
        db.add_all([EntityTag(entity_id=1,tag_id=i,topic_id=1) for i in range(1,13)])
        db.add(EntityTag(entity_id=2,tag_id=1,topic_id=1))
        db.commit()
    async_engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def get_test_db():
        async with factory() as db:
            yield db

    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = get_test_db
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    client = TestClient(app, raise_server_exceptions=False, backend_options=options)
    client.cookies.set(settings.session_cookie_name, token)
    client.headers["X-CSRF-Token"] = csrf_token_for_session(token)
    payload = {"nickname":"새 회원","accountType":"user","topicId":1,
               "consents":[{"type":k,"policyVersion":"v1","agreed":True} for k in ("terms","privacy")],
               "answers":{"version":2,"favoriteEntityIds":[2,1],"preferenceTagIds":list(range(1,13)),"consumptionType":"world"}}
    try:
        yield client, engine, user_id, payload, migrate, app, options, token
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_real_pg_search_candidates_and_atomic_completion(api_pg):
    client, engine, user_id, payload, migrate, *_ = api_pg
    response = client.get("/api/v1/onboarding/works",params={"topicId":1,"q":"100%_literal"})
    assert response.status_code == 200, response.text
    assert [r["entityId"] for r in response.json()["items"]] == [1]
    first = client.get("/api/v1/onboarding/works",params={"topicId":1,"q":"가상","limit":1}).json()
    second = client.get("/api/v1/onboarding/works",params={"topicId":1,"q":"가상","limit":1,"cursor":first["nextCursor"]}).json()
    assert [first["items"][0]["entityId"],second["items"][0]["entityId"]] == [1,2]
    all_works = client.get("/api/v1/onboarding/works",params={"topicId":1,"q":"가상"}).json()["items"]
    assert all(work["selectable"] for work in all_works)
    options = client.post("/api/v1/onboarding/preference-options",json={"topicId":1,"entityIds":[1,2]})
    assert options.status_code == 200, options.text
    assert options.json()["genres"][0]["sourceEntityIds"] == [1,2]
    assert options.json()["maxSelections"] is None and options.json()["maxSelectionsPerKind"] == 7
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Tap)) == 0
        assert db.scalar(select(func.count()).select_from(ConsentHistory)) == 0
    completed = client.post("/api/v1/onboarding/complete",json=payload)
    assert completed.status_code == 201, completed.text
    assert completed.json()["answers"]["favoriteEntityIds"] == [1,2]
    assert len(completed.json()["answers"]["preferenceTagIds"]) == 12
    assert completed.json()["activeTopic"]["subtopicIds"] == []
    assert client.get("/api/v1/auth/session").json()["sessionState"] == "active"
    with Session(engine) as db:
        assert db.get(UserAccount,user_id).onboarding_completed_at is not None
        answer = db.scalar(select(OnboardingAnswersRecord))
        assert (answer.schema_version,answer.q1_choice,answer.q2_choice,answer.consumption_type) == (2,None,None,"world")
        assert db.scalar(select(func.count()).select_from(TapEntity)) == 2
        assert db.scalar(select(func.count()).select_from(TapPreferenceTag)) == 12
        assert db.scalar(select(func.count()).select_from(NotificationSetting)) == 1
    with pytest.raises(Exception, match="[Oo]nboarding|V2|v2"):
        migrate("onboarding_v2@-1",downgrade=True)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(TapPreferenceTag)) == 12


def test_pg_rejects_stale_catalog_and_leaves_no_partial_signup(api_pg):
    client, engine, user_id, payload, *_ = api_pg
    with engine.begin() as db:
        db.execute(text("UPDATE preference_tag SET is_active=false WHERE tag_id=12"))
    response = client.post("/api/v1/onboarding/complete",json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["fields"][0]["field"] == "answers.preferenceTagIds"
    with Session(engine) as db:
        assert db.get(UserAccount,user_id).onboarding_completed_at is None
        for model in (Tap,ConsentHistory,OnboardingAnswersRecord,NotificationSetting):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_pg_flush_failure_rolls_back_every_signup_write(api_pg):
    client, engine, user_id, payload, *_ = api_pg

    def reject_answers(session, *_):
        if any(isinstance(row,OnboardingAnswersRecord) for row in session.new):
            raise IntegrityError("test failure",{},ValueError("forced rollback"))

    event.listen(Session,"before_flush",reject_answers)
    try:
        response = client.post("/api/v1/onboarding/complete",json=payload)
        assert response.status_code == 500
    finally:
        event.remove(Session,"before_flush",reject_answers)
    with Session(engine) as db:
        assert db.get(UserAccount,user_id).onboarding_completed_at is None
        for model in (Tap,ConsentHistory,TapEntity,TapPreferenceTag,OnboardingAnswersRecord,NotificationSetting):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(select(AuthSession)).revoked_at is None


def test_pg_concurrent_completion_creates_one_signup(api_pg):
    _, engine, _, payload, _, app, _, token = api_pg

    async def concurrent_requests():
        # An async engine belongs to one event loop, as in the actual server.
        # Separate TestClient portals would incorrectly share it across loops.
        async with AsyncClient(transport=ASGITransport(app,raise_app_exceptions=False),
                               base_url="http://testserver",
                               cookies={settings.session_cookie_name:token}) as client:
            responses = await asyncio.gather(*[
                client.post("/api/v1/onboarding/complete",json=payload,
                            headers={"X-CSRF-Token":csrf_token_for_session(token)})
                for _ in range(2)
            ])
            return [response.status_code for response in responses]

    kwargs = {"loop_factory":asyncio.SelectorEventLoop} if sys.platform=="win32" else {}
    with asyncio.Runner(**kwargs) as runner:
        statuses = runner.run(asyncio.wait_for(concurrent_requests(),timeout=20))
    assert statuses.count(201) == 1, statuses
    assert all(code in {201,401,409} for code in statuses), statuses
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Tap)) == 1
        assert db.scalar(select(func.count()).select_from(OnboardingAnswersRecord)) == 1


def test_pg_foreign_keys_reject_cross_topic_links(api_pg):
    _, engine, *_ = api_pg
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(text("INSERT INTO entity_tag (entity_id,tag_id,topic_id) VALUES (4,1,1)"))


def test_pg_catalog_import_is_idempotent(pg_schema):
    from app.seeds.onboarding_catalog import DEMO_CATALOG, load_catalog, seed_catalog

    engine, url, migrate = pg_schema
    migrate(TARGET)
    with Session(engine) as db:
        db.add(Topic(topic_code="anime",topic_name="애니",is_active=True))
        db.commit()

    async def import_twice():
        async_engine = create_async_engine(url,poolclass=NullPool)
        factory = async_sessionmaker(async_engine,expire_on_commit=False)
        document = load_catalog(DEMO_CATALOG)
        try:
            async with factory() as db:
                async with db.begin():
                    await seed_catalog(db,document,apply=False)
                    assert await db.scalar(select(func.count()).select_from(Entity)) == 0
            async with factory() as db:
                async with db.begin():
                    await seed_catalog(db,document,apply=True)
                original_ids = list((await db.scalars(select(Entity.entity_id).order_by(Entity.entity_id))).all())
            async with factory() as db:
                async with db.begin():
                    await seed_catalog(db,document,apply=True)
                assert list((await db.scalars(select(Entity.entity_id).order_by(Entity.entity_id))).all()) == original_ids
                assert len(original_ids) == 2
        finally:
            await async_engine.dispose()

    kwargs = {"loop_factory":asyncio.SelectorEventLoop} if sys.platform=="win32" else {}
    with asyncio.Runner(**kwargs) as runner:
        runner.run(import_twice())
