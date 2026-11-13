"""챗봇 '우리 글 찾기': 질문에서 조건 읽기 (DB 없이) + 조건에 맞는 글 모으기 (SQLite)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, Uuid, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.models import (
    Content, ContentEntityLink, ContentReaction, ContentSlide, ContentSource, Entity, EntityTag, PreferenceTag,
    RecommendationItem, RecommendationRound, SavedItem, Tap, Topic, UserAccount, UserEvent,
)
from app.services import chat_finder
from test_fe_integration_seed import AsyncAdapter

KST = timezone(timedelta(hours=9))
NARUTO, FRIEREN, BOCCHI, MADOKA, DUNGEON = 901, 902, 903, 904, 905
TAGS = {1: ("genre", "fantasy", "판타지"), 2: ("genre", "adventure", "모험"), 3: ("genre", "music", "음악"),
        4: ("theme", "magic", "마법"), 5: ("genre", "comedy", "코미디")}


# ---------- 질문에서 조건 읽기 (DB 없이) ----------

def _index():
    names = {NARUTO: "나루토", FRIEREN: "장송의 프리렌", BOCCHI: "봇치 더 록!", MADOKA: "마법소녀 마도카☆마기카",
             DUNGEON: "던전밥"}
    aliases = {FRIEREN: ["프리렌"], MADOKA: ["마도카 마기카"], NARUTO: ["NARUTO"]}
    keys: dict[str, set[int]] = {}
    for entity_id, name in names.items():
        for value in [name, *aliases.get(entity_id, [])]:
            keys.setdefault(chat_finder.dictionary.name_key(value), set()).add(entity_id)
    return chat_finder._WorkIndex(names, keys, 0.0)


def _tags():
    return [SimpleNamespace(tag_id=tag_id, display_name=name) for tag_id, (_, _, name) in TAGS.items()]


def _read(question):
    ask = chat_finder._read(question.lower(), _index(), _tags())
    return SimpleNamespace(wants=ask.wants, works=ask.works, tags=[tag.display_name for tag in ask.tags],
                           types=ask.types, card_news=ask.card_news, mine=ask.mine, similar=ask.similar)


def test_design_chips_become_conditions():
    genre = _read("판타지 장르의 애니 콘텐츠 찾아줘")
    assert (genre.wants, genre.tags, genre.types, genre.works) == (True, ["판타지"], (), ())
    work = _read("'나루토' 관련 카드뉴스 보여줘")
    assert (work.wants, work.works, work.card_news, work.types) == (True, (NARUTO,), True, ())
    event = _read("팝업이나 전시 콘텐츠만 보고 싶어")
    assert (event.wants, event.types, event.works) == (True, ("event",), ())


def test_music_is_a_post_type_unless_it_describes_the_anime():
    assert (_read("음악 콘텐츠 보여줘").types, _read("음악 콘텐츠 보여줘").tags) == (("music",), [])
    assert (_read("음악 애니 글 추천해줘").types, _read("음악 애니 글 추천해줘").tags) == ((), ["음악"])
    assert _read("프리렌 OST 관련 글 있어?").types == ("music",)


def test_words_inside_a_work_name_are_not_genres():
    madoka = _read("마법소녀 마도카 글 보여줘")
    assert (madoka.works, madoka.tags) == ((MADOKA,), [])
    assert _read("프리렌이랑 비슷한 거 보여줘").works == (FRIEREN,)  # 별칭 + 조사


def test_part_of_a_name_must_point_to_few_works():
    index = _index()
    index.keys.update({f"마왕성{n}": {1000 + n} for n in range(2)})
    assert chat_finder._read("마왕성 글 보여줘", index, _tags()).works == (1000, 1001)
    index.keys.update({f"마왕성{n}": {1000 + n} for n in range(2, 4)})  # '마왕성' 이 든 작품이 4개 → 흔한 낱말
    assert chat_finder._read("마왕성 글 보여줘", index, _tags()).works == ()
    assert chat_finder._read("마왕 글 보여줘", index, _tags()).works == ()  # 두 글자는 이름 일부로 찾지 않는다


def test_my_records_and_similar_words():
    assert _read("예전에 본 콘텐츠 중에 고양이 나오는 거 찾아줘").mine == "viewed"
    assert _read("일본 콘텐츠 보여줘").mine is None  # '일본' 의 '본' 은 본 글이 아니다
    assert _read("좋아요 누른 글 보여줘").mine == "liked"
    saved = _read("저장한 거 중에 행사 있어?")
    assert (saved.mine, saved.types, saved.wants) == ("saved", ("event",), True)
    assert _read("이거랑 비슷한 거 보여줘").similar is True


def test_detail_questions_without_search_words_are_not_searches():
    for question in ("이 OST가 등장하는 장면은?", "이 작품의 다른 OST도 알려줘", "가사에는 어떤 의미가 담겨 있나요?"):
        assert _read(question).wants is False


def test_prompt_block_lists_posts_with_type_work_and_day():
    post = SimpleNamespace(content_id=7, title="나루토 팝업", summary=" 성수에서 열려 ", content_type="event",
                           published_at=datetime(2026, 11, 12, 16, tzinfo=timezone.utc))
    found = chat_finder.Found(conditions=["작품: 나루토", "글 종류: 굿즈"], contents=[post], work_names={7: "나루토"},
                              relaxed=True)
    assert found.prompt_block() == (
        "찾은 조건: 작품: 나루토 / 글 종류: 굿즈 (딱 맞는 글이 없어서 글 종류 조건은 빼고 찾았다)\n"
        "[글 1] 나루토 팝업 (행사 · 나루토 · 2026-11-13)\n"
        "성수에서 열려"
    )
    assert chat_finder.Found(conditions=["조건 없음: 오늘 추천 순서"]).prompt_block().endswith("(조건에 맞는 글 없음)")


# ---------- 조건에 맞는 글 모으기 (SQLite) ----------

MODELS = (Topic, UserAccount, Tap, Content, ContentSlide, ContentSource, ContentReaction, SavedItem,
          RecommendationRound, RecommendationItem, UserEvent, Entity, ContentEntityLink, PreferenceTag, EntityTag)
POSTS = {  # 글 번호: (작품, 글 종류)
    501: (NARUTO, "event"), 502: (NARUTO, "news"), 503: (FRIEREN, "music"), 504: (FRIEREN, "review"),
    505: (DUNGEON, "guide"), 506: (MADOKA, "news"), 507: (BOCCHI, "music"), 508: (None, "event"),
    509: (FRIEREN, "news"),
}
WORK_TAGS = {NARUTO: (2, 5), FRIEREN: (1, 2, 4), BOCCHI: (3, 5), MADOKA: (1, 4), DUNGEON: (1, 2, 5)}


@pytest.fixture
def engine():
    # test_recommended_feed 와 같은 방식: 필요한 표의 DDL 을 SQLite 용으로 복제한다
    metadata = MetaData()
    tables = {model.__table__ for model in MODELS}
    pending = list(tables)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            if foreign_key.column.table not in tables:
                tables.add(foreign_key.column.table)
                pending.append(foreign_key.column.table)
    for table in tables:
        for column in table.to_metadata(metadata).columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                if column.server_default is not None:
                    column.server_default = DefaultClause(text(str(column.server_default.arg).replace("::jsonb", "")))
            elif isinstance(column.type, UUID):
                column.type = Uuid()
            elif isinstance(column.type, BigInteger):
                column.type = Integer()
    db_engine = create_engine("sqlite:///:memory:")
    metadata.create_all(db_engine)
    now = datetime.now(timezone.utc)
    with db_engine.begin() as connection:
        connection.execute(Topic.__table__.insert(), [
            {"topic_id": 1, "topic_code": "anime", "topic_name": "애니", "is_active": True}])
        connection.execute(UserAccount.__table__.insert(), [
            {"user_id": 7, "nickname": "사용자", "auth_provider": "kakao", "provider_user_id": "7",
             "onboarding_completed_at": now - timedelta(days=3)}])
        connection.execute(Tap.__table__.insert(), [{"tap_id": 11, "user_id": 7, "topic_id": 1}])
        connection.execute(Entity.__table__.insert(), [
            {"entity_id": entity_id, "topic_id": 1, "name": name, "aliases": aliases}
            for entity_id, name, aliases in [(NARUTO, "나루토", []), (FRIEREN, "장송의 프리렌", ["프리렌"]),
                                             (BOCCHI, "봇치 더 록!", []), (MADOKA, "마법소녀 마도카☆마기카", []),
                                             (DUNGEON, "던전밥", [])]])
        connection.execute(PreferenceTag.__table__.insert(), [
            {"tag_id": tag_id, "topic_id": 1, "kind": kind, "code": code, "display_name": name, "sort_order": tag_id,
             "is_active": True} for tag_id, (kind, code, name) in TAGS.items()])
        connection.execute(EntityTag.__table__.insert(), [
            {"entity_id": entity_id, "tag_id": tag_id, "topic_id": 1}
            for entity_id, tag_ids in WORK_TAGS.items() for tag_id in tag_ids])
        connection.execute(Content.__table__.insert(), [
            {"content_id": content_id, "topic_id": 1, "production_type": "ai", "title": f"글 {content_id}",
             "summary": f"요약 {content_id}", "content_type": content_type, "status": "active",
             "judgment_status": "confirmed", "published_at": now - timedelta(days=1, minutes=600 - content_id)}
            for content_id, (_, content_type) in POSTS.items()])
        connection.execute(ContentSource.__table__.insert(), [
            {"content_id": content_id, "source_url": f"https://example.com/{content_id}", "source_role": "primary"}
            for content_id in POSTS])
        connection.execute(ContentEntityLink.__table__.insert(), [
            {"content_id": content_id, "entity_id": work, "topic_id": 1, "link_method": "carousel", "sort_order": 1}
            for content_id, (work, _) in POSTS.items() if work])
        today = now.astimezone(KST).date()
        connection.execute(RecommendationRound.__table__.insert(), [
            {"recommendation_round_id": 1, "tap_id": 11, "service_date": today, "algorithm_version": "v1"}])
        connection.execute(RecommendationItem.__table__.insert(), [
            {"recommendation_item_id": 100 + rank, "recommendation_round_id": 1, "content_id": content_id,
             "rank": rank, "recommend_reason": None}
            for rank, content_id in enumerate([504, 501, 507], start=1)])
        connection.execute(ContentReaction.__table__.insert(), [
            {"user_id": 7, "tap_id": 11, "content_id": 509, "ox_feedback": "X", "is_liked": False, "liked_at": None,
             "updated_at": now},  # 그만볼래요
            {"user_id": 7, "tap_id": 11, "content_id": 507, "ox_feedback": None, "is_liked": True, "liked_at": now,
             "updated_at": now},
        ])
        connection.execute(SavedItem.__table__.insert(), [
            {"user_id": 7, "tap_id": 11, "content_id": 505, "status": "kept", "saved_at": now, "saved_tags": []}])
        connection.execute(UserEvent.__table__.insert(), [
            {"client_event_id": uuid.uuid4(), "user_id": 7, "tap_id": 11, "event_type": "open", "content_id": 506,
             "event_value": {}, "occurred_at": now - timedelta(days=2), "received_at": now - timedelta(days=2),
             "data_source": "production"}])
    chat_finder._work_indexes.clear()
    yield db_engine
    chat_finder._work_indexes.clear()
    db_engine.dispose()


async def _find(engine, question, *, anchor=None, previous=None, shown=()):
    with Session(engine) as session:
        found = await chat_finder.find(AsyncAdapter(session), user_id=7, topic_id=1, tap_id=11,
                                       anchor_content_id=anchor, question=question, previous=previous, shown=shown)
        return found if found is None else SimpleNamespace(
            ids=[content.content_id for content in found.contents], conditions=found.conditions,
            relaxed=found.relaxed, labels=found.labels, works=found.work_names, base_missing=found.base_missing)


@pytest.mark.asyncio
async def test_genre_search_follows_today_rank_then_newest(engine):
    found = await _find(engine, "판타지 장르의 애니 콘텐츠 찾아줘")
    assert found.ids == [504, 506, 505, 503]  # 509 는 그만볼래요
    assert found.conditions == ["장르 · 소재: 판타지"]
    assert found.works[504] == "장송의 프리렌"


@pytest.mark.asyncio
async def test_two_genres_prefer_works_with_both(engine):
    assert (await _find(engine, "판타지 모험 애니 보여줘")).ids == [504, 505, 503]
    assert (await _find(engine, "음악 애니 판타지 보여줘")).ids == [504, 507, 506, 505, 503]  # 둘 다인 작품 없음


@pytest.mark.asyncio
async def test_work_and_type_drop_only_the_type_when_nothing_matches(engine):
    exact = await _find(engine, "나루토 관련 행사 보여줘")
    assert (exact.ids, exact.relaxed) == ([501], False)
    relaxed = await _find(engine, "나루토 굿즈 보여줘")
    assert (relaxed.ids, relaxed.relaxed) == ([501, 502], True)
    assert (await _find(engine, "팝업이나 전시 콘텐츠만 보고 싶어")).ids == [501, 508]


@pytest.mark.asyncio
async def test_my_records(engine):
    assert (await _find(engine, "예전에 본 거 중에 찾아줘")).ids == [506]
    assert (await _find(engine, "저장한 글 보여줘")).ids == [505]
    liked = await _find(engine, "좋아요 누른 글 보여줘")
    assert (liked.ids, liked.conditions) == ([507], ["내 기록: 좋아요 누른 글"])


@pytest.mark.asyncio
async def test_similar_posts_on_a_detail_page(engine):
    found = await _find(engine, "이거랑 비슷한 거 보여줘", anchor=503)
    # 같은 작품(프리렌) 글 먼저, 그다음 칩이 2개 겹치는 작품(마도카 · 던전밥) 글. 503 은 보고 있는 글
    assert found.ids == [504, 506, 505]
    assert (found.labels[504], found.labels[506]) == ("같은 작품", "비슷한 작품")


@pytest.mark.asyncio
async def test_similar_without_any_base_asks_back(engine):
    with engine.begin() as connection:
        connection.execute(UserEvent.__table__.delete())
    found = await _find(engine, "이런 느낌 글 더 보여줘")
    assert (found.ids, found.base_missing) == ([], True)


@pytest.mark.asyncio
async def test_detail_question_without_search_words_is_not_a_search(engine):
    assert await _find(engine, "이 노래 가사 무슨 뜻이야?", anchor=503) is None


@pytest.mark.asyncio
async def test_more_please_reuses_previous_conditions_and_skips_shown_posts(engine):
    found = await _find(engine, "더 보여줘", previous="판타지 콘텐츠 찾아줘", shown=[504])
    assert found.ids == [506, 505, 503]


@pytest.mark.asyncio
async def test_no_conditions_on_home_use_today_rank(engine):
    found = await _find(engine, "요즘 볼만한 거 추천해줘")
    assert found.ids == [504, 501, 507, 508, 506, 505, 503, 502]
    assert found.conditions == ["조건 없음: 오늘 추천 순서"]


@pytest.mark.asyncio
async def test_card_news_keeps_only_imported_carousels(engine):
    found = await _find(engine, "나루토 카드뉴스 보여줘")
    assert (found.ids, found.conditions) == ([], ["카드뉴스만", "작품: 나루토"])  # 시험 글은 캐러셀이 아니다
