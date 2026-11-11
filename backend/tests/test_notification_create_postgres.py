"""새 글 알림 · 추천 알림 · 운영공지 만들기 (app.jobs.create_notifications) 를 일회용 로컬 PostgreSQL 에서 확인합니다.

RECOMMENDATION_TEST_DATABASE_URL 이 있을 때만 돕니다 (test_recommendation_flow_postgres 와 같은 일회용 DB,
시험마다 새 스키마). 합성 자료만 씁니다. 글 · 작품은 seed_demo 그대로: 작품 1~4, 글 1~4 의 대표 작품이
각각 작품 1~4 이고 1~4시간 전에 공개됨.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.jobs import create_notifications
from app.models import (
    Content, ContentEntityLink, ContentReaction, Notification, NotificationSetting, RecommendationItem, RecommendationRound,
    Tap, TapEntity, UserAccount, UserEvent,
)
from app.services import notification_service
from test_recommendation_flow_postgres import recommendation_pg, seed_demo  # noqa: F401


def run_async(task):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(task)
    return asyncio.run(task)


def run(pg, command, **options):
    return run_async(create_notifications.run(
        command, expected_database="recommendation_test",
        database_url=pg.url.render_as_string(hide_password=False), **options,
    ))


def inbox(pg, user_id):
    async def read():
        engine = create_async_engine(pg.url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                page = await notification_service.list_notifications(
                    db, user_id, unread_only=False, cursor=None, limit=20,
                )
                return page.items, (await notification_service.unread_count(db, user_id)).unread_count
        finally:
            await engine.dispose()
    return run_async(read())


def add_member(db, name, works, *, in_app=True, withdrawn=False):
    now = datetime.now(timezone.utc)
    user = UserAccount(
        nickname=name, auth_provider="kakao", provider_user_id=uuid4().hex,
        account_status="withdrawn" if withdrawn else "active",
        onboarding_completed_at=now - timedelta(days=3),
        withdrawn_at=now - timedelta(days=1) if withdrawn else None,
    )
    db.add(user)
    db.flush()
    tap = Tap(user_id=user.user_id, topic_id=1)
    db.add(tap)
    db.flush()
    for work in works:
        db.add(TapEntity(tap_id=tap.tap_id, entity_id=work, topic_id=1))
    db.add(NotificationSetting(user_id=user.user_id, in_app_enabled=in_app))
    db.flush()
    return SimpleNamespace(user_id=user.user_id, tap_id=tap.tap_id)


def sent(pg):
    with Session(pg.engine) as db:
        rows = db.execute(select(Notification.user_id, Notification.content_id).where(
            Notification.notification_type == "topic_new_post")).all()
    result = {}
    for user_id, content_id in rows:
        result.setdefault(user_id, set()).add(content_id)
    return result


def test_new_post_notifications_follow_q1_works_settings_and_daily_limit(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    seed_demo(pg.engine)
    with Session(pg.engine) as db:
        post = dict(db.execute(select(ContentEntityLink.entity_id, ContentEntityLink.content_id)
                               .where(ContentEntityLink.sort_order == 1)).all())  # 작품 → 그 작품이 대표인 글
        fan = add_member(db, "알림팬", [1, 2])
        add_member(db, "알림끔", [1], in_app=False)
        add_member(db, "탈퇴유예", [1], withdrawn=True)
        reader = add_member(db, "이미본사람", [1, 2])
        heavy = add_member(db, "많이고름", [1, 2, 3, 4])
        db.add(UserEvent(client_event_id=uuid4(), user_id=reader.user_id, tap_id=reader.tap_id, event_type="open",
                         content_id=post[1], event_value={}, data_source="automated_test"))
        db.add(ContentReaction(user_id=reader.user_id, tap_id=reader.tap_id, content_id=post[2], ox_feedback="X"))
        db.commit()

    # 기간: 2.5시간이면 글 1 · 2 만 새 글
    narrow = run(pg, "new-posts", apply=False, hours=2.5)
    assert (narrow["mode"], narrow["selected"], narrow["created"]) == ("preview", 4, 0)
    preview = run(pg, "new-posts", apply=False)
    assert (preview["selected"], preview["created"]) == (5, 0)
    assert sent(pg) == {}

    applied = run(pg, "new-posts", apply=True)
    assert applied["created"] == 5
    # 알림 끈 회원 · 탈퇴 유예 · 이미 열어 본 글 · 그만 볼래요 글은 빠지고, 4개 고른 회원은 최신 3개만
    assert sent(pg) == {fan.user_id: {post[1], post[2]}, heavy.user_id: {post[1], post[2], post[3]}}
    with Session(pg.engine) as db:
        row = db.execute(select(Notification).where(
            Notification.user_id == fan.user_id, Notification.content_id == post[1])).scalar_one()
        assert (row.title, row.body, row.topic_id) == ("‘로컬 테스트 작품 1’ 새 글", "[로컬 테스트] 추천 캐러셀 1", 1)

    again = run(pg, "new-posts", apply=True)
    assert (again["selected"], again["created"]) == (0, 0)

    items, unread = inbox(pg, fan.user_id)
    assert [item.type for item in items] == ["topic_new_post", "topic_new_post", "system_notice"]
    assert {item.content_id for item in items if item.type == "topic_new_post"} == {post[1], post[2]}
    assert items[-1].title == notification_service.WELCOME_TITLE
    assert unread == 3


def test_new_post_notification_waits_until_the_post_opens(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    seed_demo(pg.engine)
    with Session(pg.engine) as db:
        post = dict(db.execute(select(ContentEntityLink.entity_id, ContentEntityLink.content_id)
                               .where(ContentEntityLink.sort_order == 1)).all())
        fan = add_member(db, "공개대기", [1])
        db.commit()

    def open_in(hours):
        with Session(pg.engine) as db:
            db.execute(update(Content).where(Content.content_id == post[1]).values(
                published_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
            db.commit()

    open_in(8)
    assert run(pg, "new-posts", apply=True)["created"] == 0
    open_in(-9)     # 한국 0시에 열리고 9시에 알림을 돌린 경우
    assert run(pg, "new-posts", apply=True)["created"] == 1
    assert sent(pg) == {fan.user_id: {post[1]}}


def add_round(db, member, day, ranked, version="v1-usertest"):
    """ranked: {순위: 글 번호}. 오늘 추천 한 벌을 넣는다."""
    round_ = RecommendationRound(tap_id=member.tap_id, service_date=day, algorithm_version=version)
    db.add(round_)
    db.flush()
    for rank, content_id in ranked.items():
        db.add(RecommendationItem(recommendation_round_id=round_.recommendation_round_id,
                                  content_id=content_id, rank=rank, score=1.0))
    db.flush()


def add_event(db, member, kind, content_id):
    db.add(UserEvent(client_event_id=uuid4(), user_id=member.user_id, tap_id=member.tap_id, event_type=kind,
                     content_id=content_id, event_value={}, data_source="automated_test"))


def recommended(pg):
    with Session(pg.engine) as db:
        rows = db.execute(select(Notification.user_id, Notification.content_id).where(
            Notification.notification_type == "recommended_post")).all()
    result = {}
    for user_id, content_id in rows:
        result.setdefault(user_id, set()).add(content_id)
    return result


def test_recommended_notification_starts_at_rank_10_and_skips_seen_posts(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    seed_demo(pg.engine)
    now = datetime.now(timezone.utc)
    today = now.astimezone(notification_service.KST).date()
    with Session(pg.engine) as db:
        post = dict(db.execute(select(ContentEntityLink.entity_id, ContentEntityLink.content_id)
                               .where(ContentEntityLink.sort_order == 1)).all())
        fan = add_member(db, "추천팬", [])
        add_round(db, fan, today, {9: post[1], 10: post[2], 11: post[3]})
        # 노출 · 열람 · 카드 넘김 · 그만 볼래요 글은 전부 빠져서 보낼 글이 없다
        skipper = add_member(db, "다본사람", [])
        add_round(db, skipper, today, {10: post[1], 11: post[2], 12: post[3], 13: post[4]})
        add_event(db, skipper, "impression", post[1])
        add_event(db, skipper, "open", post[2])
        add_event(db, skipper, "card_swipe", post[3])
        db.add(ContentReaction(user_id=skipper.user_id, tap_id=skipper.tap_id, content_id=post[4], ox_feedback="X"))
        # 새 글 알림으로 이미 간 글은 건너뛰고 다음 순위
        told = add_member(db, "새글받음", [])
        add_round(db, told, today, {10: post[1], 11: post[2]})
        db.add(Notification(user_id=told.user_id, notification_type="topic_new_post", title="새 글", body="본문",
                            topic_id=1, content_id=post[1], created_at=now - timedelta(days=2)))
        # 같은 날 두 벌이면 홈처럼 나중에 계산한 쪽
        twice = add_member(db, "두벌", [])
        add_round(db, twice, today, {10: post[1]}, version="old")
        add_round(db, twice, today, {10: post[3]}, version="new")
        # 받으면 안 되는 사람들: 알림 끔 · 탈퇴 유예 · 어제 추천뿐
        for name, options, day in (("알림끔", {"in_app": False}, today), ("탈퇴유예", {"withdrawn": True}, today),
                                   ("어제뿐", {}, today - timedelta(days=1))):
            add_round(db, add_member(db, name, [], **options), day, {10: post[1]})
        db.commit()

    preview = run(pg, "recommended", apply=False, now=now)
    assert (preview["mode"], preview["selected"], preview["created"]) == ("preview", 3, 0)
    assert recommended(pg) == {}

    assert run(pg, "recommended", apply=True, now=now)["created"] == 3
    assert recommended(pg) == {fan.user_id: {post[2]}, told.user_id: {post[2]}, twice.user_id: {post[3]}}
    with Session(pg.engine) as db:
        row = db.execute(select(Notification).where(Notification.user_id == fan.user_id)).scalar_one()
        assert (row.title, row.body, row.topic_id) == ("‘로컬 테스트 작품 2’ 추천 글", "[로컬 테스트] 추천 캐러셀 2", 1)

    # 같은 날 다시 돌려도 하루 1개 (후보가 더 남은 회원은 추천팬뿐)
    again = run(pg, "recommended", apply=True, now=now)
    assert (again["sent_today"], again["selected"], again["created"]) == (1, 0, 0)

    items, unread = inbox(pg, fan.user_id)
    assert [item.type for item in items] == ["recommended_post", "system_notice"]
    assert (items[0].content_id, unread) == (post[2], 2)


def test_recommended_notification_stops_at_two_unread_and_new_posts_do_not_repeat_it(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    seed_demo(pg.engine)
    first = datetime.now(timezone.utc)
    days = [first + timedelta(days=offset) for offset in range(3)]
    dates = [day.astimezone(notification_service.KST).date() for day in days]
    with Session(pg.engine) as db:
        post = dict(db.execute(select(ContentEntityLink.entity_id, ContentEntityLink.content_id)
                               .where(ContentEntityLink.sort_order == 1)).all())
        fan = add_member(db, "추천팬", [1, 2])
        for day in dates:
            add_round(db, fan, day, {10: post[1], 11: post[2], 12: post[3], 13: post[4]})
        db.commit()

    # 추천으로 간 글 1 은 새 글 알림에서 빠지고 글 2 만 새 글로 간다. 추천은 그다음 글 3 으로
    assert run(pg, "recommended", apply=True, now=days[0])["created"] == 1
    assert run(pg, "new-posts", apply=True, now=days[0])["created"] == 1
    assert sent(pg) == {fan.user_id: {post[2]}}
    assert run(pg, "recommended", apply=True, now=days[1])["created"] == 1
    assert recommended(pg) == {fan.user_id: {post[1], post[3]}}

    # 안 읽은 추천 2개면 안 보내고, 하나 읽으면 다시 보낸다
    full = run(pg, "recommended", apply=True, now=days[2])
    assert (full["unread_full"], full["created"]) == (1, 0)
    with Session(pg.engine) as db:
        oldest = db.execute(select(Notification).where(
            Notification.user_id == fan.user_id, Notification.content_id == post[1],
            Notification.notification_type == "recommended_post")).scalar_one()
        oldest.read_at = days[2]
        db.commit()
    assert run(pg, "recommended", apply=True, now=days[2])["created"] == 1
    assert recommended(pg) == {fan.user_id: {post[1], post[3], post[4]}}


def test_notice_reaches_active_members_even_with_notifications_off_and_only_once(recommendation_pg):
    pg = recommendation_pg
    pg.migrate("head")
    _, pending = seed_demo(pg.engine)  # 온보딩 전 회원
    with Session(pg.engine) as db:
        on = add_member(db, "공지받음", [1])
        off = add_member(db, "알림끔", [1], in_app=False)
        gone = add_member(db, "탈퇴유예", [1], withdrawn=True)
        db.commit()

    notice = {"title": "점검 안내", "body": "11월 20일 새벽 2시부터 30분 동안 점검해요."}
    preview = run(pg, "notice", apply=False, **notice)
    assert (preview["targets"], preview["selected"], preview["created"]) == (2, 2, 0)
    assert run(pg, "notice", apply=True, **notice)["created"] == 2
    second = run(pg, "notice", apply=True, **notice)
    assert (second["created"], second["already"]) == (0, 2)

    picked = run(pg, "notice", apply=True, title="개별 안내", body="한 사람에게만",
                 user_ids=[off.user_id, gone.user_id, pending])
    assert picked["created"] == 1
    assert picked["not_eligible_user_ids"] == sorted([gone.user_id, pending])
    with Session(pg.engine) as db:
        rows = db.execute(select(Notification.user_id, Notification.title)).all()
        assert db.scalar(select(func.count()).select_from(Notification).where(
            Notification.notification_type != "system_notice")) == 0
    assert sorted(rows) == sorted([(on.user_id, "점검 안내"), (off.user_id, "점검 안내"), (off.user_id, "개별 안내")])

    # 공지가 먼저 와 있어도 처음 알림함을 열면 환영 공지가 따로 생긴다
    items, unread = inbox(pg, on.user_id)
    assert [item.title for item in items] == ["점검 안내", notification_service.WELCOME_TITLE]
    assert unread == 2
