"""Opt-in real PostgreSQL contention: reaction + canonical event are one write.

Uses the existing guarded loopback recommendation_test fixture with a random
schema. Never connects through the app's configured DATABASE_URL.
"""
import asyncio
from datetime import datetime, timezone
import sys

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models import Content, ContentReaction, SavedItem, Tap, Topic, UserAccount, UserEvent
from app.services import reaction_service as service
from test_recommendation_flow_postgres import TARGET, recommendation_pg  # noqa: F401


@pytest.fixture
def reaction_pg(recommendation_pg, monkeypatch):
    recommendation_pg.migrate(TARGET)
    now = datetime.now(timezone.utc)
    with Session(recommendation_pg.engine) as db:
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="합성", is_active=True))
        db.add(UserAccount(user_id=1, nickname="합성", auth_provider="kakao", provider_user_id="canonical-test",
                           onboarding_completed_at=now))
        db.flush()
        db.add(Tap(tap_id=11, user_id=1, topic_id=1))
        db.add(Content(content_id=101, topic_id=1, title="합성 콘텐츠", judgment_status="confirmed",
                       production_type="human", status="active"))
        db.commit()
    monkeypatch.setattr(settings, "event_data_source", "automated_test")
    return recommendation_pg


async def concurrent_retries(reaction_pg, monkeypatch, kind):
    first_has_lock, release_first, second_attempt = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedCommit(AsyncSession):
        async def commit(self):
            if self.info.get("pause_commit"):
                first_has_lock.set()
                await asyncio.wait_for(release_first.wait(), timeout=10)
            await super().commit()

    original_lock = service._lock_reactions
    async def observed_lock(db, *args):
        if db.info.get("second"):
            second_attempt.set()
        await original_lock(db, *args)
    monkeypatch.setattr(service, "_lock_reactions", observed_lock)
    engine = create_async_engine(reaction_pg.url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=PausedCommit, expire_on_commit=False)
    operation = {"like": service.set_like, "bookmark": service.set_bookmark, "preference": service.set_preference}[kind]
    arguments = (1, 1, 101, "positive") if kind == "preference" else (1, 1, 101)
    workers = []
    try:
        async with sessions() as first, sessions() as second:
            first.info["pause_commit"], second.info["second"] = True, True
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            workers.append(asyncio.create_task(operation(first, *arguments)))
            await asyncio.wait_for(first_has_lock.wait(), timeout=10)
            workers.append(asyncio.create_task(operation(second, *arguments)))
            await asyncio.wait_for(second_attempt.wait(), timeout=10)
            # Confirm actual PostgreSQL contention. Merely racing two tasks can
            # pass without ever exercising the missing-reaction lock boundary.
            waiting = False
            for _ in range(100):
                with reaction_pg.engine.connect() as observer:
                    waiting = observer.scalar(text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                                              {"pid": second_pid})
                if waiting:
                    break
                await asyncio.sleep(.02)
            assert waiting and not workers[1].done()
            release_first.set()
            await asyncio.wait_for(asyncio.gather(*workers), timeout=10)
        with Session(reaction_pg.engine) as db:
            rows = list(db.scalars(select(UserEvent)))
            assert len(rows) == 1
            assert rows[0].event_type == ("preference_positive" if kind == "preference" else kind)
            assert rows[0].event_value == {"origin": "server_reaction", "schemaVersion": 1}
            assert rows[0].occurred_at == rows[0].received_at
            assert rows[0].occurred_at.tzinfo is not None
            assert rows[0].data_source == "automated_test"
            reaction, saved = db.scalar(select(ContentReaction)), db.scalar(select(SavedItem))
            if kind == "bookmark":
                assert saved.status == "kept" and reaction is None
            else:
                assert saved is None
                assert reaction.is_liked if kind == "like" else reaction.ox_feedback == "O"
    finally:
        release_first.set()
        for worker in workers:
            if not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await engine.dispose()


@pytest.mark.parametrize("kind", ["like", "bookmark", "preference"])
def test_concurrent_retries_wait_on_parent_and_commit_one_label(reaction_pg, monkeypatch, kind):
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    with asyncio.Runner(**options) as runner:
        runner.run(concurrent_retries(reaction_pg, monkeypatch, kind))
