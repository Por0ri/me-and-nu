"""로컬 토론방 화면 시험용 풍성한 데이터 (2026-11-23).

화면의 여러 상태를 브라우저에서 직접 눌러 보도록 시험 계정(debate-actor-61 ~ 140)으로 만든다.
  - 주제 투표 12개: 표 0~9개, 남은 날 14시간~6일 (나루토 주제는 9표라 내가 투표하면 바로 열린다)
  - 실시간 토론방 5개: 진영 고르기와 채팅, 남은 시간 5~47시간 (2개는 내 계정이 참여해 있다)
  - 지난 토론방 4개: A 승·B 승·무승부. 로컬 DB에서 시간을 과거로 당긴 뒤 정리 작업으로 끝낸다
  - 내 계정(debate-me@example.com): 투표한 주제 2개, 참여 중인 방 2개, 내가 올린 주제 1개(9표 → 내가 투표하면 선정됨)
    마지막에 내 투표권을 3개로 채운다.
같은 작품(나루토·원피스·진격의 거인·헌터×헌터)이 여러 탭에 있어서 탭별 검색도 시험할 수 있다.

내 컴퓨터에서 시험할 때만 쓴다 (배포 서버에는 절대 쓰지 않는다): API 주소와 DB 주소가 모두 내 컴퓨터(localhost)이고
DB 이름이 me_nu_debate 일 때만 움직이고, 만든 주제가 그 DB 에 실제로 생겼는지도 하나씩 확인한다. backend 폴더에서 실행한다.

    python scripts/debate_local_demo.py seed     # 만들기 (1~2분). 시작할 때 시험 계정 투표권을 가득 채워서 다시 돌려도 된다
    python scripts/debate_local_demo.py clean    # 시험 계정 61~140 과 내 계정이 올린 주제를 지우고 모두의 투표권을 가득 채운다
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
import psycopg  # noqa: E402

from debate_local_actors import CHAT_LINES, LOOPBACK_HOSTS, Actor, ActorError, _check_base  # noqa: E402

EXPECTED_DATABASE = "me_nu_debate"
ME_EMAIL = "debate-me@example.com"
AUTHOR_FIRST, POOL_FIRST, LAST = 61, 85, 140
NICKNAMES = [
    "애니토론러", "구경러", "리매치기대", "전략덕후", "넨연구가", "작화감독지망", "주말정주행", "오타쿠아님", "명장면수집가", "떡밥회수반",
    "엔딩요정", "원작파", "애니파", "오프닝장인", "성우덕질중", "복선탐정", "캐릭터분석가", "세계관러버", "결말토론러", "다시보기중",
    "새벽정주행", "굿즈수집가", "극장판대기", "연출덕후", "작붕감시단", "명대사암기", "최애는하나", "덕질3년차", "애니입문자", "코믹스파",
    "본방사수", "스포금지", "리액션장인", "토론좋아", "중립기어", "편들기장인", "반박환영", "근거제시", "팩트체커", "과몰입중",
    "느긋한시청자", "명작발굴단", "작가님믿어요", "외전기다림", "애니메이터꿈", "설정덕후", "전투씬광", "감성파", "논리파", "직관파",
    "밤샘토론", "애니카페", "만화방단골", "첫사랑애니", "인생애니있음", "OST무한반복", "작화칭찬단", "결말반대파", "결말찬성파", "다음화기다림",
    "열혈팬", "조용한관전러", "질문많은편", "정리왕", "요약전문", "명장면재현", "코스프레꿈나무", "애니라디오", "시청률분석", "평점짠편",
    "평점후한편", "오리지널파", "리메이크파", "감독팬", "원화가팬", "배경미술팬", "음악감독팬", "애니토크", "덕담러", "토론참관인",
]
# (작품 검색어, 제목, A, B, 설명, 받을 표 수, 투표 마감까지 남은 시간)
VOTING = [
    ("나루토", "나루토와 사스케, 최종 승자는?", "나루토", "사스케", "마지막 결투에서 진짜 이긴 쪽은 누구일까요? 실력과 성장, 결말의 의미까지 함께 이야기해봐요.", 9, timedelta(days=4, hours=6)),
    ("원피스", "루피 vs 샹크스, 지금 붙으면 누가 이길까?", "루피", "샹크스", "기어5를 얻은 루피와 사황 샹크스. 지금 맞붙는다면 승자는?", 7, timedelta(hours=14)),
    ("귀멸의 칼날", "탄지로와 렌고쿠, 누가 더 강할까?", "탄지로", "렌고쿠", "최종전의 탄지로와 무한열차의 렌고쿠, 전성기끼리 붙는다면 누가 이길까요?", 5, timedelta(days=2, hours=3)),
    ("주술회전", "고죠 사토루 vs 스쿠나, 전성기 대결 승자는?", "고죠", "스쿠나", "둘 다 제약 없는 전성기라면 결과가 달라졌을까요?", 8, timedelta(days=5)),
    ("헌터", "곤과 키루아, 더 크게 성장한 쪽은?", "곤", "키루아", "처음과 지금을 비교했을 때 더 많이 성장한 캐릭터는 누구일까요?", 3, timedelta(days=3, hours=10)),
    ("체인소 맨", "덴지의 선택, 이해된다 vs 안 된다", "이해된다", "안 된다", "1부 결말에서 덴지가 내린 선택, 여러분은 공감하나요?", 2, timedelta(days=6, hours=2)),
    ("스파이 패밀리", "로이드와 요르, 정체를 먼저 알게 될 쪽은?", "로이드", "요르", "서로의 비밀을 먼저 눈치챌 사람은 누구일까요?", 1, timedelta(days=6, hours=20)),
    ("하이큐", "카게야마와 오이카와, 더 뛰어난 세터는?", "카게야마", "오이카와", "천재형 세터와 노력형 세터, 팀을 더 강하게 만드는 쪽은?", 4, timedelta(days=1, hours=5)),
    ("강철의 연금술사", "에드워드 vs 로이 머스탱, 연금술 실력은?", "에드워드", "머스탱", "응용력의 에드와 화력의 머스탱, 진짜 실력자는 누구일까요?", 6, timedelta(days=5, hours=12)),
    ("장송의 프리렌", "프리렌과 플람메, 더 위대한 마법사는?", "프리렌", "플람메", "제자와 스승, 마법사로서 더 큰 발자취를 남긴 쪽은?", 0, timedelta(days=6, hours=23)),
    ("명탐정 코난", "코난과 키드, 머리싸움 승자는?", "코난", "키드", "명탐정과 괴도, 두뇌 싸움에서 더 한 수 위인 쪽은 누구일까요?", 2, timedelta(days=3)),
    ("원펀맨", "사이타마는 정말 무적일까?", "무적이다", "아니다", "한 방에 끝내는 사이타마를 이길 수 있는 존재가 있을까요?", 3, timedelta(days=4)),
]
# (작품, 제목, A, B, 설명, 남은 시간, A 편 사람 수, B 편 사람 수, 진영 없는 사람 수, 내 계정 참여: None·"none"·"a"·"b")
OPEN = [
    ("헌터", "히소카와 크로로가 다시 싸우면 누가 이길까?", "히소카", "크로로", "천재적인 전투 센스의 히소카 vs 치밀한 전략가 크로로! 만약 둘이 다시 맞붙는다면, 이번엔 누가 승리할지 의견을 나눠봐요.", timedelta(hours=31), 4, 3, 1, "a"),
    ("나루토", "이타치는 선인일까 악인일까?", "선인", "악인", "마을을 위해 일족을 버린 이타치. 그의 선택을 어떻게 봐야 할까요?", timedelta(hours=44), 3, 2, 1, None),
    ("원피스", "원피스 최강의 동료는 조로일까 상디일까?", "조로", "상디", "밀짚모자 일당의 양 날개, 더 강한 쪽은 누구일까요?", timedelta(hours=12), 3, 3, 0, "none"),
    ("귀멸의 칼날", "렌고쿠 vs 아카자, 다시 붙으면?", "렌고쿠", "아카자", "해가 뜨기 전 끝까지 싸울 수 있었다면 결과가 달랐을까요?", timedelta(hours=5), 2, 4, 1, None),
    ("진격의 거인", "에렌의 선택은 옳았을까?", "옳았다", "틀렸다", "땅울림을 선택한 에렌. 그의 결정을 어떻게 평가하나요?", timedelta(hours=47), 2, 2, 2, None),
]
# (작품, 제목, A, B, 설명, 끝난 지 얼마나, A 편 수, B 편 수)
CLOSED = [
    ("헌터", "곤과 히소카, 진짜 승부를 냈다면?", "곤", "히소카", "피구 대결 이후 미뤄 둔 진짜 승부, 붙었다면 누가 이겼을까요?", timedelta(hours=3), 5, 2),
    ("진격의 거인", "미카사 vs 리바이, 아커만 최강자는?", "미카사", "리바이", "아커만 혈통의 두 사람, 1대1이라면 누가 더 강할까요?", timedelta(days=1), 2, 6),
    ("원피스", "조로는 세계 최강의 검사가 될까?", "된다", "안 된다", "미호크를 넘어설 수 있을지, 여러분의 생각은?", timedelta(days=3), 3, 3),
    ("나루토", "나루토 최고의 스승은 지라이야일까 카카시일까?", "지라이야", "카카시", "나루토를 가장 크게 성장시킨 스승은 누구일까요?", timedelta(days=6), 4, 1),
]
MY_TOPIC = ("체인소 맨", "체인소 맨 2부, 1부보다 재밌다 vs 아니다", "재밌다", "아니다", "새 주인공과 함께 시작한 2부, 여러분의 평가는 어떤가요?", 9)
NEUTRAL_LINES = ("이 주제 진짜 오래 고민했어요.", "다들 근거가 탄탄해서 읽는 재미가 있네요.", "원작 다시 보고 올게요!", "명장면 얘기 나오니까 또 보고 싶다.")


class Me(Actor):
    """내 브라우저 계정 (로컬 로그인 페이지와 같은 계정)."""

    def __init__(self, base_url: str, topic_id: int) -> None:
        super().__init__(base_url, 0, topic_id)
        self.email = ME_EMAIL
        self.nickname = "킹갓레전드스피노2"  # 가입 요청은 409(이미 있음)로 끝나고 로그인만 한다


def database_url() -> str:
    from sqlalchemy.engine import make_url

    from app.core.config import settings
    from app.seeds.menu_works import validate_target

    validate_target(settings.database_url, EXPECTED_DATABASE)
    if make_url(settings.database_url).host not in LOOPBACK_HOSTS:
        sys.exit("내 컴퓨터(localhost)의 DB 에서만 쓸 수 있어요. backend/.env 의 DATABASE_URL 을 확인해 주세요.")
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def ticket_max() -> int:
    from app.core.config import settings

    return settings.debate_vote_ticket_max


def actor_emails() -> list[str]:
    return [f"debate-actor-{index:02d}@example.com" for index in range(AUTHOR_FIRST, LAST + 1)]


def refill_tickets(emails: list[str]) -> None:
    """투표권을 가득 채운다 (투표권 줄이 아직 없는 계정은 원래 가득으로 본다)."""
    sql("UPDATE debate_vote_ticket SET balance = %s, refilled_at = now() "
        "WHERE user_id IN (SELECT user_id FROM user_account WHERE email = ANY(%s))", (ticket_max(), emails))


def sql(statement: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(database_url()) as connection, connection.cursor() as cursor:
        cursor.execute(statement, params)
        rows = cursor.fetchall() if cursor.description else []
        connection.commit()
        return rows


class Pool:
    """투표할 시험 계정들. 한 사람에 투표권이 3개라 고르게 나눠 쓴다."""

    def __init__(self, actors: list[Actor]) -> None:
        self.left = {actor.index: ticket_max() for actor in actors}  # seed 시작 때 refill_tickets 로 가득 채운다
        self.actors = {actor.index: actor for actor in actors}

    def voters(self, count: int, exclude: set[int]) -> list[Actor]:
        ready = sorted((index for index, left in self.left.items() if left > 0 and index not in exclude),
                       key=lambda index: (-self.left[index], index))
        if len(ready) < count:
            raise ActorError("투표할 시험 계정이 모자라요. LAST 를 늘려 주세요.")
        chosen = ready[:count]
        for index in chosen:
            self.left[index] -= 1
        return [self.actors[index] for index in chosen]


def sign_in_all(base_url: str, topic_id: int, indexes) -> dict[int, Actor]:
    actors: dict[int, Actor] = {}
    for index in indexes:
        actor = Actor(base_url, index, topic_id)
        actor.nickname = NICKNAMES[(index - AUTHOR_FIRST) % len(NICKNAMES)]
        try:
            actor.sign_in()
        except ActorError as error:
            if "NICKNAME_TAKEN" not in str(error):
                raise
            actor.close()
            actor = Actor(base_url, index, topic_id)  # 닉네임이 이미 있으면 기본 이름으로
            actor.sign_in()
        actors[index] = actor
        print(f"  계정 {index}: {actor.nickname}", flush=True)
    return actors


def vote_all(debate_id: int, voters: list[Actor]) -> dict:
    last: dict = {}
    for voter in voters:
        result = voter.vote(debate_id)
        if "error" in result:
            raise ActorError(f"{voter.nickname} 투표 실패: {result['error']}")
        last = result
    return last


def chat(debate_id: int, members: list[tuple[Actor, str | None]], labels: dict[str, str], count: int, rng: random.Random) -> None:
    for number in range(count):
        actor, side = members[number % len(members)]
        lines = CHAT_LINES[side] + (NEUTRAL_LINES if side is None else ())
        response = actor.say(debate_id, rng.choice(lines).format(**labels))
        if response.status_code != 201:
            raise ActorError(f"{actor.nickname} 채팅 실패: {response.status_code} {response.text}")
        time.sleep(max(0.3, 2.2 / len(members)))  # 한 사람이 10초에 5개를 넘지 않게


def open_room(author: Actor, pool: Pool, spec) -> int:
    """주제를 올리고 시험 계정 10명이 투표해 토론방을 연다."""
    work, title, side_a, side_b, description, *_ = spec
    debate = create_topic(author, work, title, side_a, side_b, description)
    result = vote_all(debate["debateId"], pool.voters(10, {author.index}))
    if not result.get("opened"):
        raise ActorError(f"'{title}' 토론방이 열리지 않았어요 (기준 표 수 확인).")
    return debate["debateId"]


def create_topic(author: Actor, work: str, title: str, side_a: str, side_b: str, description: str) -> dict:
    work_id = author.work_id(work)
    response = author.send("POST", "/api/v1/debates", json={
        "topicId": author.topic_id, "title": title, "description": description,
        "sideA": side_a, "sideB": side_b, "workIds": [work_id] if work_id else [],
    })
    if response.status_code != 201:
        raise ActorError(f"{author.nickname} 주제 올리기 실패: {response.status_code} {response.text}")
    debate = response.json()
    # 시각을 당기는 SQL 은 이 스크립트가 직접 DB 에 쓴다. API 서버가 다른 DB 를 보고 있으면 엉뚱한 주제를 바꾸게 되므로 멈춘다.
    stored = sql("SELECT title FROM debate WHERE debate_id = %s", (debate["debateId"],))
    if not stored or stored[0][0] != title:
        raise ActorError(f"API 서버와 이 스크립트가 서로 다른 DB 를 보고 있어요. 서버를 이 backend 폴더의 .env (DB {EXPECTED_DATABASE})로 켰는지 확인해 주세요.")
    return debate


def fill_room(debate_id: int, people: list[Actor], spec, rng: random.Random, chat_count: int) -> None:
    _, _, side_a, side_b, _, _, count_a, count_b, count_none, *_ = spec
    labels = {"A": side_a, "B": side_b}
    sides = ["a"] * count_a + ["b"] * count_b + [None] * count_none
    members: list[tuple[Actor, str | None]] = []
    for actor, side in zip(people, sides):
        actor.join(debate_id)
        if side:
            actor.choose_side(debate_id, side)
        members.append((actor, side))
    rng.shuffle(members)
    if chat_count:
        chat(debate_id, members, labels, chat_count, rng)


def run_seed(base_url: str, topic_id: int) -> None:
    _check_base(base_url)
    database_url()  # 로컬 DB 가 맞는지 먼저 확인
    rng = random.Random(20261123)
    print("시험 계정 로그인 (처음이면 가입·온보딩까지)…", flush=True)
    actors = sign_in_all(base_url, topic_id, range(AUTHOR_FIRST, LAST + 1))
    authors = [actors[index] for index in range(AUTHOR_FIRST, POOL_FIRST)]
    # 전에 seed 로 쓴 투표권은 1시간에 1개씩만 돌아오므로, 다시 돌려도 모자라지 않게 시험 계정 투표권을 먼저 가득 채운다.
    refill_tickets(actor_emails())
    pool = Pool([actors[index] for index in range(POOL_FIRST, LAST + 1)])
    me = Me(base_url, topic_id)
    me.sign_in()
    now = datetime.now(timezone.utc)
    try:
        print("주제 투표 12개…", flush=True)
        voting_ids: list[int] = []
        for author, (work, title, a, b, description, votes, left) in zip(authors, VOTING):
            debate = create_topic(author, work, title, a, b, description)
            if votes:
                vote_all(debate["debateId"], pool.voters(votes, {author.index}))
            ends = now + left
            sql("UPDATE debate SET voting_ends_at = %s, created_at = %s WHERE debate_id = %s",
                (ends, ends - timedelta(days=7), debate["debateId"]))
            voting_ids.append(debate["debateId"])
            print(f"  {debate['debateId']}번 {title} ({votes}표)", flush=True)
        for index in (1, 3):  # 내 계정은 원피스·주술회전 주제에 투표해 둔다
            if "error" in me.vote(voting_ids[index]):
                print(f"  - 내 계정 투표 건너뜀 ({voting_ids[index]}번)")

        print("실시간 토론방 5개…", flush=True)
        room_authors = authors[len(VOTING):len(VOTING) + len(OPEN)]
        spare = [actors[index] for index in range(POOL_FIRST, LAST + 1)]
        for author, spec in zip(room_authors, OPEN):
            debate_id = open_room(author, pool, spec)
            # 참여한 뒤의 채팅만 보이므로 내 계정이 먼저 들어간 뒤에 시험 계정들이 채팅한다.
            my_side = spec[9]
            if my_side is not None:
                me.join(debate_id)
                if my_side in ("a", "b"):
                    me.choose_side(debate_id, my_side)
            people = rng.sample(spare, spec[6] + spec[7] + spec[8])
            fill_room(debate_id, people, spec, rng, chat_count=rng.randint(10, 16))
            if my_side is not None:
                me.say(debate_id, f"저도 들어왔어요! 저는 {spec[2] if my_side == 'a' else spec[3]} 쪽이 궁금해요." if my_side in ("a", "b") else "구경하러 왔어요!")
            left = spec[5]
            closes = now + left
            sql("UPDATE debate SET opened_at = %s, closes_at = %s, voting_ends_at = %s, created_at = %s WHERE debate_id = %s",
                (closes - timedelta(hours=48), closes, closes - timedelta(hours=48), closes - timedelta(hours=48, days=3), debate_id))
            print(f"  {debate_id}번 {spec[1]} (남은 {int(left.total_seconds() // 3600)}시간)", flush=True)

        print("지난 토론방 4개…", flush=True)
        closed_authors = authors[len(VOTING) + len(OPEN):len(VOTING) + len(OPEN) + len(CLOSED)]
        for author, spec in zip(closed_authors, CLOSED):
            work, title, a, b, description, ago, count_a, count_b = spec
            debate_id = open_room(author, pool, (work, title, a, b, description))
            people = rng.sample(spare, count_a + count_b)
            fill_room(debate_id, people, (work, title, a, b, description, None, count_a, count_b, 0), rng, chat_count=4)
            closes = now - ago
            sql("UPDATE debate SET opened_at = %s, closes_at = %s, voting_ends_at = %s, created_at = %s WHERE debate_id = %s",
                (closes - timedelta(hours=48), closes, closes - timedelta(hours=48), closes - timedelta(hours=48, days=2), debate_id))
            print(f"  {debate_id}번 {title} (끝난 지 {ago})", flush=True)
        report = finalize()
        print(f"  정리 작업: {report}", flush=True)

        print("내가 올린 주제 (9표, 내가 투표하면 토론방이 열린다)…", flush=True)
        work, title, a, b, description, votes = MY_TOPIC
        mine = me.send("GET", "/api/v1/users/me/debates").json()["items"]
        # 투표 중이거나 토론 중인 내 주제가 있으면 새 주제를 못 올린다 (서버 규칙, 2026-11-24)
        if any(item["status"] in ("voting", "open") for item in mine):
            print("  - 이미 투표 중이거나 토론 중인 내 주제가 있어서 건너뜀")
        else:
            debate = create_topic(me, work, title, a, b, description)
            vote_all(debate["debateId"], pool.voters(votes, set()))
            print(f"  {debate['debateId']}번 {title} ({votes}표)")
        refill_tickets([ME_EMAIL])
        print(f"내 투표권을 {ticket_max()}개로 채웠어요. 끝!")
    finally:
        me.close()
        for actor in actors.values():
            actor.close()


def finalize() -> dict:
    from app.jobs.finalize_debates import run

    task = run(expected_database=EXPECTED_DATABASE, apply=True)
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(task)
    return asyncio.run(task)


def run_more_chat(base_url: str, topic_id: int, debate_ids: list[int], messages: int) -> None:
    """열린 방에 참여해 있는 시험 계정(61~140)들이 지금 고른 진영대로 채팅을 더 한다."""
    _check_base(base_url)
    rng = random.Random()
    for debate_id in debate_ids:
        rows = sql("""SELECT u.email, s.side, d.side_a_label, d.side_b_label FROM debate_member m
                      JOIN user_account u ON u.user_id = m.user_id JOIN debate d ON d.debate_id = m.debate_id
                      LEFT JOIN debate_side s ON s.debate_id = m.debate_id AND s.user_id = m.user_id
                      WHERE m.debate_id = %s AND u.email LIKE 'debate-actor-%%'""", (debate_id,))
        members: list[tuple[Actor, str | None]] = []
        for email, side, label_a, label_b in rows:
            index = int(email.split("-")[2].split("@")[0])
            if not AUTHOR_FIRST <= index <= LAST:
                continue
            actor = Actor(base_url, index, topic_id)
            actor.nickname = NICKNAMES[(index - AUTHOR_FIRST) % len(NICKNAMES)]
            actor.sign_in()
            members.append((actor, side))
        if not members:
            print(f"{debate_id}번: 채팅할 시험 계정이 없어요.")
            continue
        try:
            chat(debate_id, members, {"A": rows[0][2], "B": rows[0][3]}, messages, rng)
            print(f"{debate_id}번: {len(members)}명이 채팅 {messages}개를 보냈어요.")
        finally:
            for actor, _ in members:
                actor.close()


def run_clean() -> None:
    emails = actor_emails() + [ME_EMAIL]
    rows = sql("DELETE FROM debate WHERE author_user_id IN (SELECT user_id FROM user_account WHERE email = ANY(%s)) RETURNING debate_id",
               (emails,))
    # 주제를 SQL 로 바로 지우면 쓴 투표권이 돌아오지 않으므로 시험 계정과 내 계정 모두 가득 채운다.
    refill_tickets(emails)
    print(f"주제 {len(rows)}개를 지우고 시험 계정과 내 투표권을 {ticket_max()}개로 채웠어요.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="API 주소 (로컬만)")
    parser.add_argument("--topic-id", type=int, default=1, help="분야 번호 (로컬 애니 분야 기본 1)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="여러 상태의 주제·토론방·채팅을 만든다")
    commands.add_parser("clean", help="이 스크립트로 만든 주제를 지우고 투표권을 가득 채운다")
    more = commands.add_parser("chat", help="열린 방의 지금 참여자(시험 계정)들이 자기 진영대로 채팅을 더 한다")
    more.add_argument("--debate", type=int, action="append", required=True, help="토론 번호 (여러 번 쓸 수 있다)")
    more.add_argument("--messages", type=int, default=12, help="방마다 보낼 채팅 수")
    args = parser.parse_args()
    try:
        if args.command == "seed":
            run_seed(args.base_url, args.topic_id)
        elif args.command == "chat":
            run_more_chat(args.base_url, args.topic_id, args.debate, args.messages)
        else:
            run_clean()
    except httpx.ConnectError:
        sys.exit(f"{args.base_url}에 연결하지 못했어요. 백엔드 서버가 켜져 있는지 확인해 주세요.")
    except ActorError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
