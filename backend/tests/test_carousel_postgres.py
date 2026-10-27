"""Opt-in tests against a disposable local carousel_test PostgreSQL database.

Every test owns a random schema. No configured application database is used.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from uuid import uuid4
import asyncio
import json
import sys

import pytest
from PIL import Image
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

from test_carousel_import import raw_bundle  # noqa: F401 -- shared synthetic PNG fixture


TARGET = "f4b8c1d9a620"
PARENT = "e3a7b9c2d451"


@pytest.fixture
def carousel_pg():
    value = os.environ.get("CAROUSEL_TEST_DATABASE_URL")
    if not value:
        pytest.skip("requires an explicitly selected disposable carousel_test DB")
    url = make_url(value)
    if (url.host not in {"127.0.0.1", "localhost"}
            or url.database != "carousel_test" or url.query):
        pytest.fail("Only the disposable local carousel_test DB is accepted")
    schema = "carousel_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as db:
        assert db.scalar(text("SELECT current_database()")) == "carousel_test"
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(scoped, poolclass=NullPool)

    def migrate(target, *, downgrade=False):
        # Test migrations must not reconfigure/disable application loggers for
        # unrelated tests that run later in the same process.
        config = Config()
        config.set_main_option("script_location", "alembic")
        with engine.begin() as db:
            config.attributes["connection"] = db
            (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        yield engine, scoped, migrate
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _legacy_content(db):
    db.execute(text("INSERT INTO topic(topic_id,topic_code,topic_name,is_active) "
                    "VALUES (1,'anime','애니',true)"))
    return db.scalar(text("INSERT INTO content(topic_id,production_type,title,body,status) "
                          "VALUES (1,'human','기존 제목','기존 본문','active') RETURNING content_id"))


def test_upgrade_preserves_existing_content_and_birth_date(carousel_pg):
    engine, _, migrate = carousel_pg
    migrate("c0e5f3b8a124")
    with engine.begin() as db:
        db.execute(text("INSERT INTO user_account(nickname,auth_provider,provider_user_id,birth_date) "
                        "VALUES ('기존 회원','kakao','carousel-preservation','2000-01-02')"))
        content_id = _legacy_content(db)
    migrate(PARENT)
    migrate(TARGET)
    with engine.connect() as db:
        row = db.execute(text("SELECT title,body,feed_valid_until FROM content WHERE content_id=:id"),
                         {"id": content_id}).one()
        assert tuple(row) == ("기존 제목", "기존 본문", None)
        columns = {column["name"] for column in inspect(db).get_columns("user_account")}
        assert "birth_date" in columns
        assert str(db.scalar(text("SELECT birth_date FROM user_account"))) == "2000-01-02"
        assert set(db.scalars(text("SELECT version_num FROM alembic_version"))) == {TARGET}
        assert db.scalar(text("SELECT count(*) FROM content_slide")) == 0
    migrate(PARENT, downgrade=True)
    with engine.connect() as db:
        assert "content_slide" not in inspect(db).get_table_names()
        assert db.scalar(text("SELECT body FROM content")) == "기존 본문"
        assert str(db.scalar(text("SELECT birth_date FROM user_account"))) == "2000-01-02"


def test_slide_constraints_and_downgrade_preserve_stored_data(carousel_pg):
    engine, _, migrate = carousel_pg
    migrate(TARGET)
    output = io.BytesIO()
    Image.new("RGB", (1308, 1632), "navy").save(output, format="PNG")
    png = output.getvalue()
    with engine.begin() as db:
        content_id = _legacy_content(db)
        values = dict(id=content_id, page=1, image=png, sha=hashlib.sha256(png).hexdigest(), width=1308)
        insert = text("INSERT INTO content_slide(content_id,page,role,eyebrow,title_lines,body_sentences,"
                      "layout_json,image_data,image_sha256,image_width,image_height) "
                      "VALUES (:id,:page,'cover','소개','[\"제목\"]','[]','{}',:image,:sha,:width,1632)")
        db.execute(insert, values)
        for invalid in ({"page": 0}, {"page": 11}, {"page": 2, "image": b""},
                        {"page": 2, "width": 1}, {"page": 2, "sha": "short"}, {}):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(insert, {**values, **invalid})
    with pytest.raises(DBAPIError, match="Refusing"):
        migrate(PARENT, downgrade=True)
    with engine.begin() as db:
        assert db.scalar(text("SELECT count(*) FROM content_slide")) == 1
        db.execute(text("DELETE FROM content WHERE content_id=:id"), {"id": content_id})
        assert db.scalar(text("SELECT count(*) FROM content_slide")) == 0
    migrate(PARENT, downgrade=True)


def test_downgrade_does_not_remove_deadlines_even_without_slides(carousel_pg):
    engine, _, migrate = carousel_pg
    migrate(TARGET)
    with engine.begin() as db:
        content_id = _legacy_content(db)
        db.execute(text("UPDATE content SET feed_valid_until=:expiry WHERE content_id=:id"),
                   {"id": content_id, "expiry": datetime.now(timezone.utc) + timedelta(days=1)})
    with pytest.raises(DBAPIError, match="Refusing"):
        migrate(PARENT, downgrade=True)
    with engine.connect() as db:
        assert db.scalar(text("SELECT feed_valid_until FROM content")) is not None


def _run_async(task):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(task)
    return asyncio.run(task)


def _live_bundle(tmp_path, raw_bundle):
    from app.seeds.carousel_contents import load_bundle

    now = datetime.now(timezone.utc)
    package = raw_bundle["items"][0]["package"]
    package["feed_valid_until"] = (now + timedelta(days=2)).isoformat()
    package["purge_at"] = (now + timedelta(days=32)).isoformat()
    file = tmp_path / "bundle.json"
    file.write_text(json.dumps(raw_bundle, ensure_ascii=False), encoding="utf-8")
    pending = load_bundle(file)
    record = {"digest": pending.items[0].digest, "decision": "approved", "reviewedBy": "isolated-pg-test",
              "reviewedAt": (now - timedelta(minutes=1)).isoformat(), "comment": "Synthetic fixture review only."}
    review_file = tmp_path / "review.json"
    review_file.write_text(json.dumps({"schemaVersion": 1, "records": [record]}), encoding="utf-8")
    approved = load_bundle(file, review_record_file=review_file)
    return pending, approved


def test_import_approval_and_authenticated_api_roundtrip(carousel_pg, tmp_path, raw_bundle, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import Session

    from app.api.deps import require_onboarding
    from app.core.config import settings
    from app.core.security import csrf_token_for_session, hash_token
    from app.db.session import get_db
    from app.main import create_app
    from app.models import AgentRun, AuthSession, Content, ContentSlide, Draft, Tap, Topic, UserAccount
    from app.seeds.carousel_contents import import_bundle

    engine, url, migrate = carousel_pg
    migrate(TARGET)
    for key, value in {"session_backend": "postgres", "enable_dev_auth_bypass": False,
                       "session_cookie_secure": False, "redis_url": None}.items():
        monkeypatch.setattr(settings, key, value)
    now = datetime.now(timezone.utc)
    token = "carousel-test-" + uuid4().hex
    with Session(engine) as db, db.begin():
        db.add_all([Topic(topic_id=1, topic_code="anime", topic_name="애니", is_active=True),
                    Topic(topic_id=2, topic_code="other", topic_name="다른 토픽", is_active=True)])
        user = UserAccount(nickname="합성 테스트 사용자", auth_provider="kakao", provider_user_id=uuid4().hex,
                           onboarding_completed_at=now)
        db.add(user)
        db.flush()
        user_id = user.user_id
        db.add(Tap(user_id=user_id, topic_id=1))
        db.add(AuthSession(user_id=user_id, session_state="active", session_token_hash=hash_token(token),
                           csrf_token_hash=hash_token(csrf_token_for_session(token)), created_at=now,
                           last_accessed_at=now, expires_at=now + timedelta(hours=1)))
    pending, approved = _live_bundle(tmp_path, raw_bundle)

    async def scenario():
        async_engine = create_async_engine(url, poolclass=NullPool)
        factory = async_sessionmaker(async_engine, expire_on_commit=False)

        async def test_db():
            async with factory() as db:
                yield db

        app = create_app(enable_dev_api=False)
        app.dependency_overrides[get_db] = test_db
        assert require_onboarding not in app.dependency_overrides
        try:
            async with factory() as db, db.begin():
                await db.execute(text("SET TRANSACTION READ ONLY"))
                preview = await import_bundle(db, pending)
                assert preview["created"] == 1
                assert await db.scalar(select(func.count()).select_from(Content)) == 0
            async with factory() as db, db.begin():
                first = await import_bundle(db, pending, apply=True)
            content_id = first["items"][0]["content_id"]
            detail_url = f"/api/v1/contents/{content_id}?topicId=1"
            image_url = f"/api/v1/contents/{content_id}/slides/1/image?topicId=1"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                assert (await client.get(image_url)).status_code == 401
                client.cookies.set(settings.session_cookie_name, token)
                client.headers["X-CSRF-Token"] = csrf_token_for_session(token)
                assert (await client.get(detail_url)).status_code == 404
                assert (await client.get(image_url)).status_code == 404
                async with factory() as db, db.begin():
                    promoted = await import_bundle(db, approved, apply=True)
                    assert promoted["created"] == 0 and promoted["review_updated"] == 1
                feed = await client.get("/api/v1/topics/1/feed")
                assert feed.status_code == 200, feed.text
                card = feed.json()["sections"][0]["contents"][0]
                assert card["id"] == content_id
                assert [slide["page"] for slide in card["slides"]] == [1, 2, 3]
                assert card["imageUrl"] == image_url
                assert card["summary"]
                detail = await client.get(detail_url)
                assert detail.status_code == 200, detail.text
                payload = detail.json()
                assert payload["displayMode"] == "full_body"
                assert payload["body"] == raw_bundle["items"][0]["package"]["detail_body"]
                assert len(payload["sources"]) == 1
                assert payload["notices"][0]["code"] == "AI_GENERATED"
                assert "operator_review" not in detail.text and "reviewEvidence" not in detail.text
                image = await client.get(image_url)
                assert image.status_code == 200 and image.content == approved.items[0].images[1]
                assert image.headers["content-type"] == "image/png"
                assert image.headers["cache-control"] == "private, no-store"
                assert (await client.get(image_url.replace("topicId=1", "topicId=2"))).status_code == 404
                bookmarked = await client.put(f"/api/v1/contents/{content_id}/bookmark", json={"topicId": 1})
                assert bookmarked.status_code == 200, bookmarked.text
                saved = await client.get("/api/v1/users/me/bookmarks?topicId=1")
                assert saved.json()["items"][0]["imageUrl"] == image_url
                # Service copies remain readable after all local producer assets are gone.
                for source_image in (tmp_path / "cards").glob("*.png"):
                    source_image.unlink()
                assert (await client.get(image_url)).content == image.content
                async with factory() as db, db.begin():
                    again = await import_bundle(db, approved, apply=True)
                    assert again["unchanged"] == 1
                    assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
                    assert await db.scalar(select(func.count()).select_from(ContentSlide)) == 3
                    assert (await db.scalar(select(Draft))).status == "published"
                    await db.execute(text("UPDATE content SET feed_valid_until=now()-interval '1 second'"))
                assert (await client.get("/api/v1/topics/1/feed")).json()["sections"] == []
                assert (await client.get(detail_url)).status_code == 404
                assert (await client.get(image_url)).status_code == 404
                assert (await client.get("/api/v1/users/me/bookmarks?topicId=1")).json()["items"] == []
                assert (await client.put(f"/api/v1/contents/{content_id}/like", json={"topicId": 1})).status_code == 404
                assert (await client.delete(f"/api/v1/contents/{content_id}/bookmark?topicId=1")).status_code == 204
        finally:
            app.dependency_overrides.clear()
            await async_engine.dispose()

    _run_async(scenario())


def test_concurrent_import_is_idempotent_and_changed_content_conflicts(carousel_pg, tmp_path, raw_bundle):
    from copy import deepcopy
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import AgentRun, Content, ContentSlide
    from app.seeds.carousel_contents import CarouselImportError, import_bundle, load_bundle

    engine, url, migrate = carousel_pg
    migrate(TARGET)
    with engine.begin() as db:
        db.execute(text("INSERT INTO topic(topic_id,topic_code,topic_name,is_active) VALUES (1,'anime','애니',true)"))
    _, approved = _live_bundle(tmp_path, raw_bundle)
    changed_raw = deepcopy(raw_bundle)
    changed_raw["items"][0]["package"]["detail_body"] = "A different version of the body."
    changed_file = tmp_path / "changed.json"
    changed_file.write_text(json.dumps(changed_raw, ensure_ascii=False), encoding="utf-8")
    changed = load_bundle(changed_file)

    async def scenario():
        async_engine = create_async_engine(url, poolclass=NullPool)
        factory = async_sessionmaker(async_engine, expire_on_commit=False)

        async def apply(bundle):
            async with factory() as db, db.begin():
                return await import_bundle(db, bundle, apply=True)

        try:
            results = await asyncio.gather(apply(approved), apply(approved))
            assert sorted(result["created"] for result in results) == [0, 1]
            with pytest.raises(CarouselImportError, match="different immutable digest"):
                await apply(changed)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
                assert await db.scalar(select(func.count()).select_from(Content)) == 1
                assert await db.scalar(select(func.count()).select_from(ContentSlide)) == 3
        finally:
            await async_engine.dispose()

    _run_async(scenario())
