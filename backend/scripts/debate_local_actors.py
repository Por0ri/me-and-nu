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
"""

from __future__ import annotations

import argparse
import sys
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
    args = parser.parse_args()
    _check_base(args.base_url)
    try:
        if args.command == "seed":
            run_seed(args.base_url, args.topic_id)
        elif args.command == "vote":
            run_vote(args.base_url, args.topic_id, args.debate, args.count, args.start)
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
