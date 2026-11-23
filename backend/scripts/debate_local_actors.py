"""로컬 토론방 시험용 가짜 참여자.

브라우저에서 혼자서는 10표를 채울 수 없어서, 개발용 가입·로그인 API로 시험 계정
(debate-actor-NN@example.com)을 만들고 온보딩까지 마친 뒤 그 계정들로 주제를 올리거나 투표한다.
로컬 주소(localhost·127.0.0.1)에서 개발용 API(ENABLE_DEV_API=true)와 토론 기능(DEBATE_ENABLED=true)이
켜진 서버에만 쓴다. 다른 주소는 실행 전에 거절한다.

    # 예시 주제 3개를 올리고 표를 채운다 (하나는 9표라서 브라우저에서 10번째 표를 넣으면 바로 열린다)
    python scripts/debate_local_actors.py seed

    # 12번 주제에 시험 계정 9명이 투표한다
    python scripts/debate_local_actors.py vote --debate 12 --count 9

    # 시험 계정 5번이 주제를 올린다
    python scripts/debate_local_actors.py topic --actor 5 --title "탄지로와 렌고쿠, 누가 더 강할까?" --side-a 탄지로 --side-b 렌고쿠

    # 열린 4번 토론방에서 시험 계정 4명이 진영을 고르고 3초마다 채팅한다 (브라우저에서 실시간으로 보인다)
    python scripts/debate_local_actors.py chat --debate 4 --count 4 --messages 20

    # 시험 계정 40번으로 4번 토론방의 실시간 스트림(SSE)을 그대로 찍어 본다 (Ctrl+C로 끝)
    python scripts/debate_local_actors.py listen --debate 4
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from urllib.parse import urlsplit

import httpx

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
FRONTEND_ORIGIN = "http://localhost:3000"
PASSWORD = "Debate!local-actor-2026"
SEED_TOPICS = (
    # (작성 계정, 제목, A, B, 작품 검색어, 투표할 계정들)
    (1, "데스노트의 라이트, 그는 악인일까?", "악인이다", "아니다", "데스노트", range(2, 11)),
    (2, "에렌과 리바이가 싸우면 누가 이길까?", "에렌", "리바이", "진격의 거인", range(3, 8)),
    (3, "고죠 사토루는 정말 최강일까?", "최강이다", "아니다", "주술회전", range(4, 6)),
)
CHAT_LINES = {
    "a": ("저는 {A} 편이에요. 근거가 더 탄탄해요.", "{A} 쪽 말이 계속 더 와닿네요.", "마지막 장면 다시 보면 {A} 쪽이 맞아요.",
          "{B} 편 말도 일리 있는데 그래도 {A}요."),
    "b": ("{B} 쪽이 한 수 위라고 봐요.", "솔직히 {B} 편 근거가 더 설득력 있어요.", "{A} 편 얘기 들어봐도 저는 {B}예요.",
          "원작까지 보면 {B} 쪽이 맞다고 생각해요."),
    # 진영을 고르지 않고 채팅하는 사람
    None: ("둘 다 일리가 있어서 아직 못 고르겠어요.", "양쪽 얘기 더 듣고 정할게요.", "{A} 편이랑 {B} 편 다 설득력 있네요."),
}


class ActorError(RuntimeError):
    pass


class Actor:
    """시험 계정 하나. 자기 쿠키를 가진 HTTP 클라이언트로 움직인다."""

    def __init__(self, base_url: str, index: int, topic_id: int) -> None:
        self.index = index
        self.topic_id = topic_id
        self.email = f"debate-actor-{index:02d}@example.com"
        self.nickname = f"토론참여자{index:02d}"
        self.client = httpx.Client(base_url=base_url, headers={"Origin": FRONTEND_ORIGIN}, timeout=30)

    def close(self) -> None:
        self.client.close()

    def _session(self) -> dict:
        response = self.client.get("/api/v1/auth/session")
        response.raise_for_status()
        return response.json()

    def send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"X-CSRF-Token": self._session()["csrfToken"]}
        return self.client.request(method, path, headers=headers, **kwargs)

    def sign_in(self) -> None:
        account = {"email": self.email, "password": PASSWORD}
        response = self.send("POST", "/api/v1/dev/auth/register", json={**account, "nickname": self.nickname})
        if response.status_code not in (201, 409):
            raise ActorError(f"{self.nickname} 가입 실패: {response.status_code} {response.text}")
        response = self.send("POST", "/api/v1/dev/auth/login", json=account)
        if response.status_code != 200:
            raise ActorError(f"{self.nickname} 로그인 실패 (ENABLE_DEV_API=true인지 확인): {response.status_code} {response.text}")
        if self._session()["sessionState"] == "onboarding_pending":
            self._onboard()

    def _onboard(self) -> None:
        consents = [{"type": item["type"], "policyVersion": item["policyVersion"], "agreed": True}
                    for item in self.client.get("/api/v1/policies").json()["consentItems"]]
        entity_id = None
        for query in ("원피스", "나루토", "진격"):
            works = self.client.get("/api/v1/onboarding/works", params={"topicId": self.topic_id, "q": query}).json().get("items", [])
            entity_id = next((work["entityId"] for work in works if work.get("selectable")), None)
            if entity_id:
                break
        if entity_id is None:
            raise ActorError("온보딩에 쓸 작품을 찾지 못했어요. 작품 목록(entity)이 있는 DB인지 확인해 주세요.")
        tags = self.send("POST", "/api/v1/onboarding/preference-options", json={"topicId": self.topic_id, "entityIds": [entity_id]}).json()
        response = self.send("POST", "/api/v1/onboarding/complete", json={
            "nickname": self.nickname, "accountType": "user", "consents": consents, "topicId": self.topic_id,
            "answers": {"version": 2, "favoriteEntityIds": [entity_id], "consumptionType": "world",
                        "preferenceTagIds": [tags["genres"][0]["tagId"], tags["themes"][0]["tagId"]]},
        })
        if response.status_code != 201:
            raise ActorError(f"{self.nickname} 온보딩 실패: {response.status_code} {response.text}")

    def work_id(self, query: str) -> int | None:
        response = self.client.get("/api/v1/debates/works", params={"topicId": self.topic_id, "q": query})
        if response.status_code == 404 and response.json().get("code") == "NOT_FOUND":
            raise ActorError("토론 API가 없어요. 서버의 DEBATE_ENABLED=true인지 확인해 주세요.")
        items = response.json().get("items", []) if response.status_code == 200 else []
        return items[0]["entityId"] if items else None

    def create(self, title: str, side_a: str, side_b: str, work_query: str | None = None) -> dict | None:
        work = self.work_id(work_query) if work_query else None
        response = self.send("POST", "/api/v1/debates", json={
            "topicId": self.topic_id, "title": title, "sideA": side_a, "sideB": side_b, "workIds": [work] if work else [],
        })
        if response.status_code == 409 and response.json().get("code") == "DEBATE_ACTIVE_LIMIT":
            print(f"  - {self.nickname}: 이미 투표 중인 주제가 있어서 건너뜀")
            return None
        if response.status_code != 201:
            raise ActorError(f"{self.nickname} 주제 올리기 실패: {response.status_code} {response.text}")
        return response.json()

    def join(self, debate_id: int) -> None:
        """토론방에 참여한다. 참여한 사람만 채팅·진영 고르기·실시간 연결을 할 수 있다."""
        response = self.send("PUT", f"/api/v1/debates/{debate_id}/membership")
        if response.status_code != 200:
            raise ActorError(f"{self.nickname} 토론방 참여 실패: {response.status_code} {response.text}")

    def choose_side(self, debate_id: int, side: str) -> None:
        response = self.send("PUT", f"/api/v1/debates/{debate_id}/side", json={"side": side})
        if response.status_code != 200:
            raise ActorError(f"{self.nickname} 진영 고르기 실패: {response.status_code} {response.text}")

    def say(self, debate_id: int, body: str) -> httpx.Response:
        return self.send("POST", f"/api/v1/debates/{debate_id}/messages", json={"body": body})

    def vote(self, debate_id: int) -> dict:
        response = self.send("PUT", f"/api/v1/debates/{debate_id}/vote")
        if response.status_code != 200:
            return {"error": response.json().get("message", response.text)}
        return response.json()


def _check_base(base_url: str) -> None:
    host = urlsplit(base_url).hostname
    if host not in LOOPBACK_HOSTS:
        sys.exit(f"로컬 주소에서만 쓸 수 있어요 (받은 주소: {base_url}).")


def _actors(base_url: str, topic_id: int, indexes) -> dict[int, Actor]:
    actors = {}
    for index in sorted(set(indexes)):
        actor = Actor(base_url, index, topic_id)
        actor.sign_in()
        actors[index] = actor
    return actors


def run_vote(base_url: str, topic_id: int, debate_id: int, count: int, start: int) -> None:
    actors = _actors(base_url, topic_id, range(start, start + count))
    try:
        for actor in actors.values():
            result = actor.vote(debate_id)
            if "error" in result:
                print(f"  - {actor.nickname}: {result['error']}")
                continue
            debate = result["debate"]
            note = " → 토론방이 열렸어요!" if result["opened"] else ""
            print(f"  - {actor.nickname}: 투표 완료 ({debate['voteCount']}/{debate['voteThreshold']}표){note}")
    finally:
        for actor in actors.values():
            actor.close()


def run_seed(base_url: str, topic_id: int) -> None:
    indexes = {author for author, *_ in SEED_TOPICS} | {voter for *_, voters in SEED_TOPICS for voter in voters}
    actors = _actors(base_url, topic_id, indexes)
    try:
        for author, title, side_a, side_b, work_query, voters in SEED_TOPICS:
            debate = actors[author].create(title, side_a, side_b, work_query)
            if debate is None:
                continue
            print(f"주제 {debate['debateId']}번 '{title}' (작성: {actors[author].nickname})")
            for voter in voters:
                result = actors[voter].vote(debate["debateId"])
                if "error" in result:
                    print(f"  - {actors[voter].nickname}: {result['error']}")
            final = actors[author].send("GET", f"/api/v1/debates/{debate['debateId']}").json()
            print(f"  → {final['voteCount']}/{final['voteThreshold']}표")
    finally:
        for actor in actors.values():
            actor.close()


def run_chat(base_url: str, topic_id: int, debate_id: int, count: int, start: int, messages: int, interval: float) -> None:
    actors = _actors(base_url, topic_id, range(start, start + count))
    try:
        debate = next(iter(actors.values())).send("GET", f"/api/v1/debates/{debate_id}").json()
        if debate.get("status") != "open":
            raise ActorError(f"{debate_id}번은 열린 토론방이 아니에요 (상태: {debate.get('status', debate.get('message'))}).")
        labels = {"A": debate["sideA"]["label"], "B": debate["sideB"]["label"]}
        rng = random.Random()
        sides: dict[int, str | None] = {}
        for actor in actors.values():
            actor.join(debate_id)
            side = rng.choice(("a", "a", "b", "b", None))  # 몇 명은 진영 없이 채팅한다
            if side:
                actor.choose_side(debate_id, side)
            sides[actor.index] = side
            print(f"  - {actor.nickname}: {labels['A' if side == 'a' else 'B'] + ' 편' if side else '진영 없음'}")
        for _ in range(messages):
            actor = rng.choice(list(actors.values()))
            if rng.random() < 0.15:  # 토론 중에 마음이 바뀌기도 한다
                side = "b" if sides[actor.index] == "a" else "a"
                actor.choose_side(debate_id, side)
                sides[actor.index] = side
                print(f"  - {actor.nickname}: {labels[side.upper()]} 편으로 바꿈")
            body = rng.choice(CHAT_LINES[sides[actor.index]]).format(**labels)
            response = actor.say(debate_id, body)
            if response.status_code == 201:
                print(f"  - {actor.nickname}: {body}")
            else:
                print(f"  - {actor.nickname}: 보내지 못함 ({response.json().get('message', response.text)})")
                if response.json().get("code") == "DEBATE_NOT_OPEN":
                    break
            time.sleep(interval)
    finally:
        for actor in actors.values():
            actor.close()


def run_listen(base_url: str, topic_id: int, debate_id: int, actor_index: int, after: int | None) -> None:
    actor = Actor(base_url, actor_index, topic_id)
    try:
        actor.sign_in()
        actor.join(debate_id)
        params = {"after": after} if after is not None else {}
        print(f"{actor.nickname}로 {debate_id}번 토론방 스트림을 엽니다. 끝내려면 Ctrl+C")
        with actor.client.stream("GET", f"/api/v1/debates/{debate_id}/stream", params=params,
                                 timeout=httpx.Timeout(30, read=None)) as response:
            if response.status_code != 200:
                response.read()
                raise ActorError(f"스트림을 열지 못했어요: {response.status_code} {response.text}")
            for line in response.iter_lines():
                if line:
                    print(line, flush=True)
    except KeyboardInterrupt:
        print("스트림을 닫았어요.")
    finally:
        actor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="API 주소 (로컬만)")
    parser.add_argument("--topic-id", type=int, default=1, help="분야 번호 (로컬 애니 분야 기본 1)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="예시 주제 3개를 올리고 표를 채운다")
    vote = commands.add_parser("vote", help="시험 계정들이 한 주제에 투표한다")
    vote.add_argument("--debate", type=int, required=True, help="토론 번호")
    vote.add_argument("--count", type=int, default=9, help="투표할 계정 수")
    vote.add_argument("--start", type=int, default=20, help="몇 번 계정부터 쓸지 (seed와 겹치지 않게 기본 20)")
    topic = commands.add_parser("topic", help="시험 계정 하나가 주제를 올린다")
    topic.add_argument("--actor", type=int, default=30, help="계정 번호")
    topic.add_argument("--title", required=True)
    topic.add_argument("--side-a", required=True)
    topic.add_argument("--side-b", required=True)
    topic.add_argument("--work", help="관련 작품 검색어 (선택)")
    chat = commands.add_parser("chat", help="시험 계정들이 열린 토론방에서 진영을 고르고 채팅한다")
    chat.add_argument("--debate", type=int, required=True, help="토론 번호")
    chat.add_argument("--count", type=int, default=4, help="채팅할 계정 수")
    chat.add_argument("--start", type=int, default=20, help="몇 번 계정부터 쓸지")
    chat.add_argument("--messages", type=int, default=20, help="보낼 채팅 수")
    chat.add_argument("--interval", type=float, default=3.0, help="채팅 사이 간격(초)")
    listen = commands.add_parser("listen", help="시험 계정 하나로 실시간 스트림(SSE)을 열어 그대로 찍는다")
    listen.add_argument("--debate", type=int, required=True, help="토론 번호")
    listen.add_argument("--actor", type=int, default=40, help="계정 번호")
    listen.add_argument("--after", type=int, help="이 채팅 번호 뒤의 것부터 먼저 받기 (선택)")
    args = parser.parse_args()
    _check_base(args.base_url)
    try:
        if args.command == "seed":
            run_seed(args.base_url, args.topic_id)
        elif args.command == "vote":
            run_vote(args.base_url, args.topic_id, args.debate, args.count, args.start)
        elif args.command == "chat":
            run_chat(args.base_url, args.topic_id, args.debate, args.count, args.start, args.messages, args.interval)
        elif args.command == "listen":
            run_listen(args.base_url, args.topic_id, args.debate, args.actor, args.after)
        else:
            actor = Actor(args.base_url, args.actor, args.topic_id)
            try:
                actor.sign_in()
                debate = actor.create(args.title, args.side_a, args.side_b, args.work)
                if debate:
                    print(f"주제 {debate['debateId']}번을 올렸어요 (작성: {actor.nickname}).")
            finally:
                actor.close()
    except httpx.ConnectError:
        sys.exit(f"{args.base_url}에 연결하지 못했어요. 백엔드 서버가 켜져 있는지 확인해 주세요.")
    except ActorError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
