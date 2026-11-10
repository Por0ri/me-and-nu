"""홈 피드에서 빼는 '최근 14일 안에 끝까지 본 글' 을 일회용 로컬 PostgreSQL 에서 확인합니다.

RECOMMENDATION_TEST_DATABASE_URL 이 있을 때만 돕니다 (test_recommendation_flow_postgres 와 같은 일회용 DB).
끝까지 본 글 = 카드를 마지막 장까지 넘김 또는 본문 80% 이상 (모델 EXPERIMENTS 부록 A #34 와 같은 규칙).
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import ContentEntityLink, Tap, UserAccount, UserEvent
from app.repositories import recommendation_repository as repo
from test_recommendation_flow_postgres import recommendation_pg, seed_demo  # noqa: F401


def run_async(task):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(task)
    return asyncio.run(task)


def test_only_posts_read_to_the_end_inside_the_window_are_hidden(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    seed_demo(pg.engine)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=13)
    with Session(pg.engine) as db:
        post = dict(db.execute(select(ContentEntityLink.entity_id, ContentEntityLink.content_id)
                               .where(ContentEntityLink.sort_order == 1)).all())
        user = UserAccount(nickname="끝까지본사람", auth_provider="kakao", provider_user_id=uuid4().hex,
                           onboarding_completed_at=now)
        db.add(user)
        db.flush()
        tap = Tap(user_id=user.user_id, topic_id=1)
        db.add(tap)
        db.flush()

        def event(content_id, kind, value, at=now):
            db.add(UserEvent(client_event_id=uuid4(), user_id=user.user_id, tap_id=tap.tap_id, event_type=kind,
                             content_id=content_id, event_value=value, occurred_at=at, received_at=at,
                             data_source="automated_test"))

        event(post[1], "card_swipe", {"card_index": 3, "card_count": 3})   # 마지막 장 → 뺌
        event(post[2], "card_swipe", {"card_index": 2, "card_count": 3})   # 중간까지 → 후보
        event(post[2], "open", {"scroll_pct": 79.9})                       # 본문 80% 미만 → 후보
        event(post[3], "open", {"scroll_pct": 80.0})                       # 본문 80% → 뺌
        event(post[4], "card_swipe", {"card_index": 3, "card_count": 3}, at=since - timedelta(minutes=1))  # 기간 밖 → 후보
        db.commit()
        tap_id = tap.tap_id

    async def read():
        engine = create_async_engine(pg.url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return await repo.fully_read_content_ids(db, tap_id, since, now)
        finally:
            await engine.dispose()

    assert run_async(read()) == {post[1], post[3]}
