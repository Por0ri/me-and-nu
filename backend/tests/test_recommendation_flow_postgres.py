"""Opt-in recommendation integration against a disposable local PostgreSQL only.

RECOMMENDATION_TEST_DATABASE_URL must name recommendation_test on loopback,
using a non-default port and the recommendation_test user. Each test owns a
random schema. Real OAuth and model datasets are replaced with synthetic input;
session, onboarding, ranking, batch writes, feed and reaction APIs are real.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuthSession, Content, ContentEntityLink, ContentSlide, ContentSource, Entity,
    EntityExternalId, EntityTag, Policy, PreferenceTag, Topic, UserAccount,
)

ROOT = Path(__file__).resolve().parents[2]
TARGET = "150a601074af"
PREVIOUS = "f4b8c1d9a620"


@pytest.fixture
def recommendation_pg():
    value = os.environ.get("RECOMMENDATION_TEST_DATABASE_URL")
    if not value:
        pytest.skip("requires a disposable local recommendation_test database")
    url = make_url(value)
    if (url.host not in {"127.0.0.1", "localhost"}
            or url.database != "recommendation_test" or url.username != "recommendation_test"
            or not url.port or url.port == 5432 or url.query):
        pytest.fail("Only the explicit disposable loopback recommendation_test DB is accepted")
    schema = "recommendation_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as conn:
        assert conn.scalar(text("SELECT current_database()")) == "recommendation_test"
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(scoped, poolclass=NullPool)

    def migrate(target):
        config = Config()
        config.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with engine.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, target)

    try:
        yield SimpleNamespace(engine=engine, url=scoped, migrate=migrate)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def seed_demo(engine):
    """Synthetic fixture only; no user data, external images or producer calls."""
    now = datetime.now(timezone.utc)
    token = "recommendation-local-" + uuid4().hex
    layout = {"image_box": {"x": 0, "y": 0, "width": 1, "height": 1},
              "copy_box": {"x": 0.06, "y": 0.65, "width": 0.88, "height": 0.29}}
    pngs = []
    for page, color in enumerate(("#3562aa", "#8355a4", "#168979"), 1):
        picture = Image.new("RGB", (1308, 1632), color)
        ImageDraw.Draw(picture).text((70, 80), f"LOCAL TEST / {page} of 3", fill="white", font_size=72)
        output = io.BytesIO()
        picture.save(output, format="PNG")
        pngs.append(output.getvalue())
    with Session(engine) as db:
        user = UserAccount(nickname="로컬검증대기", auth_provider="kakao", provider_user_id=uuid4().hex)
        db.add(user)
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니", is_active=True))
        db.flush()
        db.add(AuthSession(user_id=user.user_id, session_state="onboarding_pending",
                           session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
                           created_at=now, last_accessed_at=now, expires_at=now + timedelta(hours=2)))
        for kind in ("terms", "privacy"):
            db.add(Policy(policy_type=kind, policy_version="local-test", title="로컬 테스트 " + kind,
                          content="합성 테스트 데이터", is_required=True))
        for i in range(1, 5):
            db.add(Entity(entity_id=i, topic_id=1, name=f"로컬 테스트 작품 {i}", aliases=[], is_active=True))
        db.add_all([
            PreferenceTag(tag_id=1, topic_id=1, kind="genre", code="action", display_name="액션", sort_order=1, is_active=True),
            PreferenceTag(tag_id=2, topic_id=1, kind="theme", code="isekai", display_name="이세계", sort_order=2, is_active=True),
        ])
        db.flush()
        for i in range(1, 5):
            db.add(EntityExternalId(entity_id=i, provider="menu_work", external_id=str(i)))
            db.add(EntityTag(entity_id=i, tag_id=1, topic_id=1))
            db.add(EntityTag(entity_id=i, tag_id=2, topic_id=1))
            content = Content(topic_id=1, production_type="ai", title=f"[로컬 테스트] 추천 캐러셀 {i}",
                              summary="실제 생성 콘텐츠가 아닌 통합 검증용 자료입니다.", content_type="guide",
                              published_at=now - timedelta(hours=i), feed_valid_until=now + timedelta(days=7),
                              status="active", judgment_status="confirmed",
                              ai_judgment_basis={"method": "carousel_import_v1"})
            db.add(content)
            db.flush()
            db.add(ContentSource(content_id=content.content_id, source_url=f"https://example.invalid/local-test/{i}",
                                 source_title="로컬 합성 테스트", source_role="primary"))
            db.add(ContentEntityLink(content_id=content.content_id, entity_id=i, topic_id=1,
                                     link_method="manual", sort_order=1))
            for page, png in enumerate(pngs, 1):
                db.add(ContentSlide(content_id=content.content_id, page=page, role="cover" if page == 1 else "detail",
                                    eyebrow="로컬 테스트 데이터", title_lines=[f"테스트 카드 {page}"],
                                    body_sentences=["추천 연결 확인용 합성 자료"], layout_json=layout,
                                    image_data=png, image_sha256=hashlib.sha256(png).hexdigest(),
                                    image_width=1308, image_height=1632))
        db.commit()
        return token, user.user_id


def completion_payload():
    return {"nickname": "추천통합테스트", "accountType": "user", "topicId": 1,
            "consents": [{"type": k, "policyVersion": "local-test", "agreed": True} for k in ("terms", "privacy")],
            "answers": {"version": 2, "favoriteEntityIds": [1], "preferenceTagIds": [1, 2], "consumptionType": "world"}}


def make_test_app(url):
    factory = async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)
    async def database():
        async with factory() as db:
            yield db
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    return app


def feed_cards(response):
    assert response.status_code == 200, response.text
    return [card for section in response.json()["sections"] for card in section["contents"]]


def test_service_migration_preserves_existing_content_and_adds_recommendation_tables(recommendation_pg):
    pg = recommendation_pg
    pg.migrate(PREVIOUS)
    with pg.engine.begin() as db:
        db.execute(text("INSERT INTO topic(topic_id,topic_code,topic_name) VALUES(1,'anime','애니')"))
        db.execute(text("INSERT INTO content(topic_id,title) VALUES(1,'migration-preserved-local-fixture')"))
    pg.migrate(TARGET)
    with pg.engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == TARGET
        assert db.scalar(text("SELECT title FROM content")) == "migration-preserved-local-fixture"
        for table in ("content_entity_link", "recommendation_round", "recommendation_item", "user_event"):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_onboarding_batch_feed_activity_reactions_and_logout(recommendation_pg, monkeypatch):
    # The real algorithm uses an explicitly synthetic world, not private MAL/AniList datasets.
    sys.path.insert(0, str(ROOT))
    from model.scripts import daily_recommend as batch
    from model.tests.fixtures import build_synthetic_world

    pg = recommendation_pg
    pg.migrate(TARGET)
    token, user_id = seed_demo(pg.engine)
    for key, value in {"session_backend": "postgres", "redis_url": None, "enable_dev_auth_bypass": False,
                       "session_cookie_secure": False, "event_data_source": "local_demo"}.items():
        monkeypatch.setattr(settings, key, value)
    app = make_test_app(pg.url)
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    with TestClient(app, backend_options=options) as client:
        client.cookies.set(settings.session_cookie_name, token)
        client.headers["X-CSRF-Token"] = csrf_token_for_session(token)
        assert client.get("/api/v1/auth/session").json()["sessionState"] == "onboarding_pending"
        candidates = client.post("/api/v1/onboarding/preference-options", json={"topicId": 1, "entityIds": [1]})
        assert candidates.status_code == 200, candidates.text
        assert candidates.json()["minGenreSelections"] == candidates.json()["minThemeSelections"] == 1
        invalid = completion_payload()
        invalid["answers"]["preferenceTagIds"] = [1]
        assert client.post("/api/v1/onboarding/complete", json=invalid).status_code == 422
        complete = client.post("/api/v1/onboarding/complete", json=completion_payload())
        assert complete.status_code == 201, complete.text
        session = client.get("/api/v1/auth/session").json()
        assert session["sessionState"] == "active"
        # Completion rotates the cookie and CSRF token; subsequent writes use the new pair.
        client.headers["X-CSRF-Token"] = session["csrfToken"]
        assert all(card["recommendationId"] is None for card in feed_cards(client.get("/api/v1/topics/1/feed")))

        world = build_synthetic_world()
        day = datetime.now(batch.KST).date()
        batch_url = pg.url.set(drivername="postgresql").render_as_string(hide_password=False)
        batch.run_once(batch_url, world, day, apply=False, replace=False, new_only=False, show=0)
        with pg.engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM recommendation_round")) == 0
        batch.run_once(batch_url, world, day, apply=True, replace=False, new_only=False, show=0)
        batch.run_once(batch_url, world, day, apply=True, replace=False, new_only=False, show=0)
        with pg.engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM recommendation_round")) == 1
            assert db.scalar(text("SELECT count(*) FROM recommendation_item")) == 4
            tap_id = db.scalar(text("SELECT tap_id FROM tap WHERE user_id=:uid"), {"uid": user_id})
        first = client.get("/api/v1/topics/1/feed", params={"limit": 2})
        first_cards = feed_cards(first)
        assert len(first_cards) == 2 and first.json()["nextCursor"]
        card = first_cards[0]
        assert card["recommendationId"] and card["recommendationReason"] and len(card["slides"]) == 3
        event_base = {"topicId": 1, "contentId": card["id"], "recommendationItemId": card["recommendationId"],
                      "occurredAt": datetime.now(timezone.utc).isoformat()}
        events = {"events": [{**event_base, "clientEventId": str(uuid4()), "eventType": kind, "value": value}
                             for kind, value in (
                                 ("impression", {"sessionId": "local-flow", "surface": "home_feed", "visibleMs": 1500}),
                                 ("open", {"sessionId": "local-flow", "surface": "detail", "activeMs": 2000, "scrollPct": 70}),
                                 ("card_swipe", {"sessionId": "local-flow", "surface": "detail", "cardIndex": 3, "cardCount": 3}),
                             )]}
        assert client.post("/api/v1/users/me/activity-events", json=events).json() == {"saved": 3, "duplicated": 0, "rejected": 0}
        assert client.post("/api/v1/users/me/activity-events", json=events).json() == {"saved": 0, "duplicated": 3, "rejected": 0}
        second_cards = feed_cards(client.get("/api/v1/topics/1/feed", params={"limit": 2, "cursor": first.json()["nextCursor"]}))
        assert len({c["id"] for c in first_cards + second_cards}) == 4
        assert card["id"] not in {c["id"] for c in feed_cards(client.get("/api/v1/topics/1/feed"))}
        detail_path = f'/api/v1/contents/{card["id"]}'
        assert client.get(detail_path, params={"topicId": 1}).status_code == 200
        image = client.get(card["slides"][0]["imageUrl"])
        assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        for action, body in (("like", {"topicId": 1}), ("bookmark", {"topicId": 1}),
                             ("preference", {"topicId": 1, "value": "positive"})):
            response = client.put(detail_path + "/" + action, json=body)
            assert response.status_code == 200, response.text
        reaction = client.get(detail_path, params={"topicId": 1}).json()["myReaction"]
        assert reaction == {"liked": True, "bookmarked": True, "preference": "positive"}
        assert client.get("/api/v1/users/me/bookmarks").status_code == 200
        with psycopg.connect(batch_url) as connection, connection.cursor() as cur:
            posts, index = batch.load_posts(cur, 1, day)
            visits = batch.load_visits(cur, [tap_id], index)
        kinds = {entry[0] for visit in visits[tap_id] for entry in visit}
        assert {"impression", "open", "read", "like", "save", "more"} <= kinds
        for action in ("like", "bookmark", "preference"):
            assert client.delete(detail_path + "/" + action, params={"topicId": 1}).status_code == 204
        assert client.get(detail_path, params={"topicId": 1}).json()["myReaction"] == {
            "liked": False, "bookmarked": False, "preference": None}
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/session").json()["authenticated"] is False
        assert client.post("/api/v1/users/me/activity-events", json=events).status_code == 401
