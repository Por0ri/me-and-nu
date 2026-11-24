"""챗봇 시험 세트(cases.yaml)를 돌리고 보고서를 만든다. 로컬 시험 DB 에서만 돈다.

API 가 쓰는 함수(create_session → post_message → generate_answer → get_job)를 그대로 부르므로
서버를 켤 필요가 없다. 프로젝트 코드는 바꾸지 않고, 이 스크립트가 돌 때만 아래를 바꾼다.
- LLM 호출(httpx)을 감싸 토큰 수 · 시간을 잰다. --effort 를 주면 생각 정도를 넣어 보낸다.
- 하루 질문 한도를 끈다.
- --dict off 면 정보사전을 끈다 (사전이 없는 Checking 과 같은 상태).
- 글 찾기 · 자료 고르기 · 사전 결과를 보고서에 남기려고 감싸서 기록만 한다.
- viewed 가 있는 질문은 그 질문을 돌리는 동안만 시험 계정에 '본 글' 기록을 넣고 지운다.

backend/ 에서 로컬 시험 DB 환경 파일을 불러온 뒤 (정책 문서 12장):
  python scripts/chat_eval/run.py --dry-run               # 질문 · 기준 글만 확인 (LLM 안 부름)
  python scripts/chat_eval/run.py                         # 전체 (LLM 을 불러서 돈이 든다)
  python scripts/chat_eval/run.py --only C,E1 --label 고친뒤
  python scripts/chat_eval/run.py --effort low            # 생각 정도. 안 주면 보내지 않는다 (모델 기본값)
  python scripts/chat_eval/run.py --dict off              # 정보사전 없이
  python scripts/chat_eval/run.py --compare A.json B.json # 두 결과를 나란히 (LLM 안 부름)

결과는 scripts/chat_eval/results/ 에 JSON(비교용)과 HTML(보고서)로 남는다. git 에는 안 올라간다.

모든 답에 하는 공통 검사 (docs/CHATBOT_PROMPT_RULES.md 3장 · 4장)
- 존댓말로 끝나는 문장 · 'ㅋㅋ' · 'ㅎㅎ' · 이모지 (R11)
- 'ㅠㅠ'는 물은 작품 글이 없어서 비슷한 작품 글을 찾았을 때만 (R11)
- 상투어 (R12) · '자료 1' 같은 번호 · 주소 (R13)
- 느낌표 2개 이상 · 5문장 이상 (R11)
- 답 만들기 실패 · 25초 넘음
자동 검사는 답의 모양만 본다. 내용이 맞는지는 보고서를 보고 사람이 판단한다.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import yaml

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.integrations import dictionary, llm  # noqa: E402
from app.models import Content, Tap, Topic, UserAccount, UserEvent  # noqa: E402
from app.repositories.carousel_repository import local_test_database, public_content_condition  # noqa: E402
from app.schemas.chat import ChatMessageCreate  # noqa: E402
from app.seeds.dev_user import DEV_USER_EMAIL  # noqa: E402
from app.services import chat_finder, chat_service  # noqa: E402

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "cases.yaml"
RESULTS = HERE / "results"
KST = timezone(timedelta(hours=9))
SLOW_SECONDS = 25  # LLM 제한 시간과 같다
# 원 추정: 정책 문서의 '가격' 줄 (100만 토큰당 입력 $0.10 · 출력 $0.50, OpenAI 공식 가격표 확인 필요)과
# 1달러 = 1,400원 가정. 캐시된 입력도 보통 입력으로 센다 (실제보다 조금 비싸게 나온다).
# 출력 토큰에는 생각(reasoning) 토큰이 들어 있다.
INPUT_USD_PER_M = 0.10
OUTPUT_USD_PER_M = 0.50
KRW_PER_USD = 1400
EVAL_MARK = "chatEval"  # 시험이 넣은 '본 글' 기록 표시. 질문이 끝나면 이 표시가 있는 기록을 지운다
CHECK_KEYS = {"kind", "cards_min", "cards_max", "cards_include", "card_titles_any", "card_titles_none",
              "card_types", "facts_min", "answer_any", "answer_none", "not_only", "new_cards", "not_anchor"}
CASE_KEYS = {"id", "rule", "screen", "anchor", "selected", "viewed", "ask", "turns", "runs", "same_as", "check",
             "expect"}

POLITE = re.compile(r"(?:니다|세요|어요|아요|에요|예요|해요|까요|죠|네요|군요|래요|대요|게요|나요|가요|줘요|봐요"
                    r"|돼요|워요|와요|는데요|거든요|잖아요)$")
LAUGH = re.compile(r"ㅋ{2,}|ㅎ{2,}")
SAD = re.compile(r"ㅠ{2,}|ㅜ{2,}")
# 그림 문자 · 기호 (제목에 흔한 ★ ☆ ♪ ♫ 는 뺀다)
EMOJI = re.compile("[\U0001F000-\U0001FAFF✀-➿☀-☄☇-♩♬-⛿️]")
# '자료 1' 같은 번호. '글 3개'처럼 수를 세는 말은 뺀다
NUMBERED = re.compile(r"(?:자료|사실|글|사전)\s*\d+(?!\d)(?!\s*(?:개|편|건|장|화|권|명|곡|기|년|월|일|위|부|살))"
                      r"|\[\s*\d+\s*\]")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
CLICHES = ("도와드릴", "좋은 질문", "언제든", "도움이 되", "궁금한 점이 있으면", "무엇이든 물어")


class CaseError(Exception):
    """질문을 돌릴 준비가 안 됨 (기준 글이 없는 등). 그 질문만 건너뛴다."""


# ---------- 질문 파일 ----------

def load_cases(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    cases, seen = [], set()
    for item in raw:
        case_id = str(item.get("id") or "")
        problems = []
        if not case_id or case_id in seen:
            problems.append("번호가 없거나 겹침")
        if unknown := set(item) - CASE_KEYS:
            problems.append(f"모르는 칸 {sorted(unknown)}")
        if ("ask" in item) == ("turns" in item):
            problems.append("ask 와 turns 중 하나만 적어야 함")
        screen = item.get("screen", "home")
        if screen not in {"home", "detail"} or (screen == "detail") != bool(item.get("anchor")):
            problems.append("상세(detail)는 anchor 가 있어야 하고, 홈은 anchor 가 없어야 함")
        turns = [{"ask": item["ask"], "check": item.get("check") or {}}] if "ask" in item else [
            {"ask": turn["ask"], "check": turn.get("check") or {}} for turn in item.get("turns") or []]
        if "turns" in item and "check" in item:
            problems.append("turns 를 쓰면 check 는 질문마다 적음")
        for turn in turns:
            if unknown := set(turn["check"]) - CHECK_KEYS:
                problems.append(f"모르는 검사 {sorted(unknown)}")
        if problems:
            raise SystemExit(f"cases.yaml {case_id or '?'}: " + " · ".join(problems))
        seen.add(case_id)
        cases.append({"id": case_id, "rule": str(item.get("rule") or ""), "screen": screen,
                      "anchor": item.get("anchor"), "selected": item.get("selected"),
                      "viewed": list(item.get("viewed") or []), "turns": turns,
                      "runs": int(item.get("runs") or 1), "same_as": item.get("same_as"),
                      "expect": str(item.get("expect") or "")})
    for case in cases:
        if case["same_as"] and case["same_as"] not in seen:
            raise SystemExit(f"cases.yaml {case['id']}: same_as {case['same_as']} 가 없음")
    return cases


def pick_cases(cases: list[dict], only: str | None) -> list[dict]:
    if not only:
        return cases
    tokens = [token.strip() for token in only.split(",") if token.strip()]
    picked = [case for case in cases if any(
        case["id"] == token or (len(token) == 1 and case["id"].startswith(token)) for token in tokens)]
    if not picked:
        raise SystemExit(f"--only {only}: 맞는 질문이 없음")
    return picked


# ---------- 기록 (프로젝트 코드는 그대로 두고 감싸기만 한다) ----------

class Capture:
    """질문 하나를 답하는 동안 생긴 일."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.llm: list[dict] = []
        self.finder: dict | None = None
        self.finder_called = False
        self.materials: list[dict] = []
        self.facts: list[dict] = []
        self.errors: list[str] = []


CAPTURE = Capture()


class _FailureLog(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            CAPTURE.errors.append(f"{type(error).__name__}: {error}"[:300])
        else:
            CAPTURE.errors.append(record.getMessage()[:300])


def install_patches(*, effort: str | None, dict_off: bool) -> None:
    original_post = httpx.AsyncClient.post

    async def post(self, url, *args, **kwargs):
        if str(url) != llm.RESPONSES_URL:
            return await original_post(self, url, *args, **kwargs)
        body = dict(kwargs.get("json") or {})
        if effort:
            body["reasoning"] = {"effort": effort}
        kwargs["json"] = body
        call = {"effort": effort, "prompt": body.get("input", ""), "instructionChars": len(body.get("instructions", ""))}
        started = time.perf_counter()
        try:
            response = await original_post(self, url, *args, **kwargs)
        except Exception as error:
            call.update(seconds=round(time.perf_counter() - started, 2), error=f"{type(error).__name__}: {error}"[:300])
            CAPTURE.llm.append(call)
            raise
        call.update(seconds=round(time.perf_counter() - started, 2), status=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_error:
            call["error"] = response.text[:300]
        usage = payload.get("usage") or {}
        call["model"] = payload.get("model")
        call["tokens"] = {
            "input": usage.get("input_tokens") or 0,
            "cached": (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0,
            "reasoning": (usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0,
            "output": usage.get("output_tokens") or 0,
        }
        text = llm._output_text(payload) if payload else ""
        try:
            call["reply"] = json.loads(text)
        except (TypeError, ValueError):
            call["reply"] = None
            call["rawReply"] = (text or "")[:500]
        CAPTURE.llm.append(call)
        return response

    httpx.AsyncClient.post = post

    original_find = chat_finder.find

    async def find(db, **kwargs):
        found = await original_find(db, **kwargs)
        CAPTURE.finder_called = True
        CAPTURE.finder = None if found is None else {
            "conditions": list(found.conditions), "substituted": found.substituted, "relaxed": found.relaxed,
            "baseMissing": found.base_missing,
            "candidates": [{"id": content.content_id, "title": content.title, "type": content.content_type,
                            "work": found.work_names.get(content.content_id), "label": found.labels.get(content.content_id)}
                           for content in found.contents],
        }
        return found

    chat_finder.find = find

    original_pick = chat_service._pick_materials

    async def pick_materials(*args, **kwargs):
        materials = await original_pick(*args, **kwargs)
        CAPTURE.materials = [{"id": content.content_id, "title": content.title, "type": content.content_type}
                             for content in materials]
        return materials

    chat_service._pick_materials = pick_materials

    original_lookup = dictionary.lookup

    async def lookup(question, menu_work_ids=(), *, focus_text=None):
        if dict_off:
            result = dictionary.DictionaryResult()
        else:
            result = await original_lookup(question, menu_work_ids, focus_text=focus_text)
        CAPTURE.facts.append({"targets": [f"{hit.kind}:{hit.name}" for hit in result.targets],
                              "lines": len(result.lines), "failed": result.failed})
        return result

    dictionary.lookup = lookup

    async def no_daily_limit(db, user_id):
        return None

    chat_service._check_daily_limit = no_daily_limit

    failures = logging.getLogger(chat_service.__name__)
    failures.addHandler(_FailureLog(level=logging.ERROR))
    failures.propagate = False  # 실패 내용은 보고서에 남기고 화면에는 한 줄만


# ---------- DB 준비 ----------

class Context:
    def __init__(self, user_id: int, topic_id: int, tap_id: int) -> None:
        self.user_id, self.topic_id, self.tap_id = user_id, topic_id, tap_id


def guard() -> str:
    name = local_test_database(settings.database_url)
    if name is None or settings.event_data_source == "production":
        raise SystemExit("로컬 시험 DB 에서만 돈다 (주소가 localhost 이고 DB 이름이 _test 로 끝나야 함)")
    return name


async def prepare() -> Context:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(UserAccount).where(func.lower(UserAccount.email) == DEV_USER_EMAIL))
        topic = await db.scalar(select(Topic).where(Topic.topic_code == "anime", Topic.is_active.is_(True)))
        if user is None or topic is None:
            raise SystemExit("시험 계정(app.seeds.dev_user)과 애니 분야가 있어야 함")
        tap = await db.scalar(select(Tap).where(Tap.user_id == user.user_id, Tap.topic_id == topic.topic_id,
                                                Tap.deleted_at.is_(None)))
        if tap is None:
            raise SystemExit("시험 계정에 애니 분야 탭이 있어야 함")
        if left := await clear_viewed(db, user.user_id):
            print(f"지난번에 남은 시험용 본 글 기록 {left}개를 지움")
        await db.commit()
        return Context(user.user_id, topic.topic_id, tap.tap_id)


async def find_post(db, topic_id: int, words: str) -> Content:
    """제목에 words 가 든 공개 글 (여러 개면 가장 먼저 올라간 글)."""
    post = await db.scalar(select(Content).where(
        Content.topic_id == topic_id, public_content_condition(), Content.title.contains(words, autoescape=True),
    ).order_by(Content.content_id).limit(1))
    if post is None:
        raise CaseError(f"제목에 '{words}' 가 든 공개 글이 없음")
    return post


async def add_viewed(db, ctx: Context, posts: list[Content]) -> None:
    now = datetime.now(timezone.utc)
    for order, post in enumerate(posts):  # 앞에 적은 글이 가장 최근에 본 글
        db.add(UserEvent(client_event_id=uuid.uuid4(), user_id=ctx.user_id, tap_id=ctx.tap_id, event_type="open",
                         content_id=post.content_id, event_value={EVAL_MARK: True},
                         occurred_at=now - timedelta(minutes=order + 1), received_at=now,
                         data_source=settings.event_data_source))
    await db.commit()


async def clear_viewed(db, user_id: int) -> int:
    result = await db.execute(delete(UserEvent).where(
        UserEvent.user_id == user_id, UserEvent.event_value.has_key(EVAL_MARK)))
    return result.rowcount or 0


# ---------- 질문 돌리기 ----------

async def ask(ctx: Context, session_id: int, question: str, selected: str | None) -> dict:
    CAPTURE.reset()
    async with AsyncSessionLocal() as db:
        accepted, _ = await chat_service.post_message(db, ctx.user_id, session_id, ChatMessageCreate(
            client_message_id=uuid.uuid4(), content=question, selected_text=selected))
    started = time.perf_counter()
    await chat_service.generate_answer(accepted.job_id)
    seconds = round(time.perf_counter() - started, 2)
    async with AsyncSessionLocal() as db:
        job = await chat_service.get_job(db, ctx.user_id, accepted.job_id)
        result = job.result
        cards = []
        if result and result.contents:
            types = dict((await db.execute(select(Content.content_id, Content.content_type).where(
                Content.content_id.in_([card.id for card in result.contents])))).all())
            cards = [{"id": card.id, "title": card.title, "type": types.get(card.id)} for card in result.contents]
    reply = next((call.get("reply") for call in reversed(CAPTURE.llm) if call.get("reply")), None) or {}
    return {
        "ask": question, "selected": selected, "status": job.status, "seconds": seconds,
        "answer": result.content if result else "", "kind": reply.get("kind"), "evidence": reply.get("evidence"),
        "cards": cards,
        "sources": [{"id": source.content_id, "title": source.title} for source in result.sources] if result else [],
        "factSources": [source.name for source in result.fact_sources] if result else [],
        "notices": [notice.get("code") for notice in result.notices] if result else [],
        "coverage": result.context_coverage if result else None,
        "error": job.error.message if job.error else None, "errors": list(CAPTURE.errors),
        "llm": list(CAPTURE.llm), "finder": CAPTURE.finder if CAPTURE.finder_called else None,
        "finderCalled": CAPTURE.finder_called, "materials": list(CAPTURE.materials), "dictionary": list(CAPTURE.facts),
    }


async def run_case(ctx: Context, case: dict) -> dict:
    record = {key: case[key] for key in ("id", "rule", "screen", "expect", "selected", "same_as")}
    record.update(anchor=None, viewed=[], runs=[], error=None)
    viewed = []
    try:
        async with AsyncSessionLocal() as db:
            anchor = await find_post(db, ctx.topic_id, case["anchor"]) if case["anchor"] else None
            viewed = [await find_post(db, ctx.topic_id, words) for words in case["viewed"]]
            if viewed:
                await add_viewed(db, ctx, viewed)
        record["anchor"] = {"id": anchor.content_id, "title": anchor.title} if anchor else None
        record["viewed"] = [{"id": post.content_id, "title": post.title} for post in viewed]
        for _ in range(case["runs"]):
            async with AsyncSessionLocal() as db:
                session = await chat_service.create_session(
                    db, ctx.user_id, ctx.topic_id, anchor.content_id if anchor else None)
            turns: list[dict] = []
            for spec in case["turns"]:
                turn = await ask(ctx, session.session_id, spec["ask"], case["selected"])
                turn["checks"] = common_checks(turn) + case_checks(
                    turn, spec["check"], previous=turns[-1] if turns else None,
                    anchor_id=anchor.content_id if anchor else None)
                turns.append(turn)
            record["runs"].append(turns)
    except CaseError as error:
        record["error"] = str(error)
    finally:
        if viewed:
            async with AsyncSessionLocal() as db:
                await clear_viewed(db, ctx.user_id)
                await db.commit()
    return record


# ---------- 검사 ----------

def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?…])\s+|\n+", text) if part.strip()]


def _bare_end(sentence: str) -> str:
    return re.sub(r"[\s.!?…~)\]」』'\"”’]+$", "", sentence)


def _plain(text: str) -> str:
    return re.sub(r"[\s.!?…,'\"“”‘’]", "", text)


def common_checks(turn: dict) -> list[dict]:
    if turn["status"] != "completed":
        return [_check("답 만들기", False, turn.get("error") or turn["status"])]
    text = turn["answer"] or ""
    checks = []
    polite = [sentence for sentence in sentences(text) if POLITE.search(_bare_end(sentence))]
    checks.append(_check("반말", not polite, " / ".join(polite)[:150]))
    laughs = LAUGH.findall(text) + EMOJI.findall(text)
    checks.append(_check("ㅋㅋ · ㅎㅎ · 이모지 없음", not laughs, " ".join(laughs)))
    substituted = bool((turn.get("finder") or {}).get("substituted"))
    checks.append(_check("ㅠㅠ 는 대신 찾은 글일 때만", substituted or not SAD.search(text)))
    cliches = [word for word in CLICHES if word in text]
    checks.append(_check("상투어 없음", not cliches, ", ".join(cliches)))
    refs = NUMBERED.findall(text) + URL.findall(text)
    checks.append(_check("번호 · 주소 없음", not refs, ", ".join(refs)))
    marks = text.count("!")
    checks.append(_check("느낌표 1개 이하", marks <= 1, f"{marks}개"))
    count = len(sentences(text))
    checks.append(_check("4문장 이하", count <= 4, f"{count}문장"))
    checks.append(_check(f"{SLOW_SECONDS}초 안", turn["seconds"] <= SLOW_SECONDS, f"{turn['seconds']}초"))
    return checks


def case_checks(turn: dict, spec: dict, *, previous: dict | None, anchor_id: int | None) -> list[dict]:
    if turn["status"] != "completed":
        return []
    text = turn["answer"] or ""
    cards = turn["cards"]
    titles = [card["title"] for card in cards]
    ids = {card["id"] for card in cards}
    checks = []
    if "kind" in spec:
        allowed = spec["kind"] if isinstance(spec["kind"], list) else [spec["kind"]]
        checks.append(_check("종류 " + " · ".join(allowed), turn["kind"] in allowed, str(turn["kind"])))
    if "cards_min" in spec:
        checks.append(_check(f"카드 {spec['cards_min']}개 이상", len(cards) >= spec["cards_min"], f"{len(cards)}개"))
    if "cards_max" in spec:
        checks.append(_check(f"카드 {spec['cards_max']}개 이하", len(cards) <= spec["cards_max"], f"{len(cards)}개"))
    for words in spec.get("cards_include") or []:
        checks.append(_check(f"카드에 '{words}'", any(words in title for title in titles)))
    if words := spec.get("card_titles_any"):
        checks.append(_check("카드 제목에 " + " / ".join(words), any(w in t for t in titles for w in words)))
    if words := spec.get("card_titles_none"):
        hits = [t for t in titles if any(w in t for w in words)]
        checks.append(_check("카드 제목에 없어야 함: " + " / ".join(words), not hits, " / ".join(hits)))
    if types := spec.get("card_types"):
        odd = [f"{card['title']} ({card['type']})" for card in cards if card["type"] not in types]
        checks.append(_check("카드 종류 " + " · ".join(types), not odd, " / ".join(odd)))
    if "facts_min" in spec:
        count = len(turn["factSources"])
        checks.append(_check(f"사전 근거 {spec['facts_min']}개 이상", count >= spec["facts_min"], f"{count}개"))
    if words := spec.get("answer_any"):
        checks.append(_check("답에 " + " / ".join(words), any(word in text for word in words)))
    if words := spec.get("answer_none"):
        hits = [word for word in words if word in text]
        checks.append(_check("답에 없어야 함: " + " / ".join(words), not hits, ", ".join(hits)))
    if only := spec.get("not_only"):
        checks.append(_check(f"'{only}'만 답하지 않음", _plain(text) != _plain(only)))
    if spec.get("new_cards") and previous is not None:
        overlap = ids & {card["id"] for card in previous["cards"]}
        checks.append(_check("앞 질문 카드와 안 겹침", not overlap, f"{len(overlap)}개 겹침" if overlap else ""))
    if spec.get("not_anchor") and anchor_id is not None:
        checks.append(_check("보고 있는 글은 카드에서 뺌", anchor_id not in ids))
    return checks


def opening(text: str) -> str:
    return " ".join(text.split()[:2])


def add_opening_checks(records: list[dict]) -> None:
    """same_as: 같은 질문을 새 대화로 다시 물었을 때 첫마디가 모두 같으면 걸린다 (R11)."""
    by_id = {record["id"]: record for record in records}
    for record in records:
        if not record.get("same_as") or record.get("error"):
            continue
        answers = [run[0]["answer"] for run in record["runs"] if run and run[0]["status"] == "completed"]
        base = by_id.get(record["same_as"])
        if base and base["runs"] and base["runs"][0] and base["runs"][0][0]["status"] == "completed":
            answers.insert(0, base["runs"][0][0]["answer"])
        openings = [opening(answer) for answer in answers]
        if len(openings) >= 2 and record["runs"] and record["runs"][-1]:
            same = len(set(openings)) == 1
            record["runs"][-1][-1]["checks"].append(
                _check(f"첫마디가 {record['same_as']} 와 모두 같지 않음", not same, " / ".join(openings)))


# ---------- 합계 · 저장 ----------

def won(tokens: dict) -> float:
    return (tokens["input"] * INPUT_USD_PER_M + tokens["output"] * OUTPUT_USD_PER_M) / 1_000_000 * KRW_PER_USD


def turn_tokens(turn: dict) -> dict:
    total = {"input": 0, "cached": 0, "reasoning": 0, "output": 0}
    for call in turn["llm"]:
        for key in total:
            total[key] += (call.get("tokens") or {}).get(key, 0)
    return total


def all_turns(record: dict) -> list[dict]:
    return [turn for run in record["runs"] for turn in run]


def totals(records: list[dict]) -> dict:
    turns = [turn for record in records for turn in all_turns(record)]
    tokens = {"input": 0, "cached": 0, "reasoning": 0, "output": 0}
    for turn in turns:
        for key, value in turn_tokens(turn).items():
            tokens[key] += value
    seconds = [turn["seconds"] for turn in turns]
    return {
        "cases": len(records), "skipped": sum(1 for record in records if record.get("error")),
        "turns": len(turns), "llmCalls": sum(len(turn["llm"]) for turn in turns),
        "failed": sum(1 for turn in turns if turn["status"] != "completed"),
        "flaggedTurns": sum(1 for turn in turns if any(not check["ok"] for check in turn["checks"])),
        "seconds": round(sum(seconds), 1), "avgSeconds": round(sum(seconds) / len(seconds), 1) if seconds else 0,
        "maxSeconds": max(seconds) if seconds else 0, "tokens": tokens, "won": round(won(tokens), 1),
    }


def git_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BACKEND, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=BACKEND, capture_output=True,
                               text=True, check=True).stdout.strip()
        return commit + (" (고친 파일 있음)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "알 수 없음"


def safe_label(label: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", label).strip("-") or "run"


# ---------- 보고서 ----------

CSS = """
:root { --fg:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7; --bg:#fff; --soft:#f5f5f7; --bad:#c62828; --ok:#2e7d32; }
@media (prefers-color-scheme: dark) { :root { --fg:#f5f5f7; --muted:#a1a1a6; --line:#3a3a3c; --bg:#1c1c1e;
  --soft:#2c2c2e; --bad:#ff6b6b; --ok:#81c784; } }
body { font: 15px/1.6 -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; color:var(--fg); background:var(--bg);
  margin:0 auto; padding:24px 16px 64px; max-width:1100px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:36px 0 4px; } h3 { font-size:15px; margin:16px 0 6px; }
.meta, .muted { color:var(--muted); font-size:13px; }
table { border-collapse:collapse; width:100%; font-size:13px; margin:12px 0; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { color:var(--muted); font-weight:600; }
.turn { border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:10px 0; }
.q { font-weight:600; } .answer { white-space:pre-wrap; background:var(--soft); border-radius:8px; padding:10px 12px; margin:8px 0; }
.bad { color:var(--bad); font-weight:600; } .ok { color:var(--ok); }
ul { margin:4px 0; padding-left:20px; } details { margin:6px 0; font-size:13px; } summary { cursor:pointer; color:var(--muted); }
pre { white-space:pre-wrap; font-size:12px; background:var(--soft); padding:8px; border-radius:6px; max-height:420px; overflow:auto; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:12px; } @media (max-width:760px) { .cols { grid-template-columns:1fr; } }
a { color:inherit; }
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{_e(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _flags(turn: dict) -> list[dict]:
    return [check for check in turn["checks"] if not check["ok"]]


def _turn_html(turn: dict, number: int | None = None) -> str:
    head = f"<div class='q'>{'' if number is None else f'{number}. '}질문: {_e(turn['ask'])}</div>"
    if turn.get("selected"):
        head += f"<div class='muted'>고른 문장: {_e(turn['selected'])}</div>"
    tokens = turn_tokens(turn)
    info = (f"종류 {_e(turn['kind'])} · {turn['seconds']}초 · 토큰 입력 {tokens['input']:,} (캐시 {tokens['cached']:,})"
            f" · 생각 {tokens['reasoning']:,} · 출력 {tokens['output']:,} · 약 {won(tokens):.2f}원")
    parts = [head, f"<div class='answer'>{_e(turn['answer'] or turn.get('error') or turn['status'])}</div>",
             f"<div class='muted'>{info}</div>"]
    if turn["cards"]:
        parts.append("<div>카드</div><ul>" + "".join(
            f"<li>{_e(card['title'])} <span class='muted'>({_e(card['type'])})</span></li>" for card in turn["cards"])
            + "</ul>")
    extras = []
    if turn["factSources"]:
        extras.append("근거: " + _e(" · ".join(dict.fromkeys(turn["factSources"]))))
    if turn["sources"]:
        extras.append("참고한 글: " + _e(" / ".join(source["title"] for source in turn["sources"])))
    if turn["notices"]:
        extras.append("안내: " + _e(", ".join(turn["notices"])))
    if extras:
        parts.append("<div class='muted'>" + "<br>".join(extras) + "</div>")
    flags = _flags(turn)
    passed = [check for check in turn["checks"] if check["ok"]]
    if flags:
        parts.append("<ul>" + "".join(
            f"<li class='bad'>걸림: {_e(check['name'])}{' — ' + _e(check['detail']) if check['detail'] else ''}</li>"
            for check in flags) + "</ul>")
    parts.append(f"<div class='muted ok'>통과 {len(passed)}개: {_e(' · '.join(check['name'] for check in passed))}</div>")
    inner = []
    finder = turn.get("finder")
    if turn.get("finderCalled"):
        if finder is None:
            inner.append("<div>글 찾기: 안 함 (상세에서 찾아 달라는 말이 없음)</div>")
        else:
            marks = [label for key, label in (("substituted", "대신 찾음"), ("relaxed", "글 종류 조건 뺌"),
                                               ("baseMissing", "기준 작품 없음")) if finder.get(key)]
            inner.append(f"<div>글 찾기 조건: {_e(' / '.join(finder['conditions']))}"
                         f"{' · ' + _e(', '.join(marks)) if marks else ''} · 후보 {len(finder['candidates'])}개</div><ul>"
                         + "".join(f"<li>{_e(c['title'])} <span class='muted'>({_e(c['type'])} · {_e(c['work'])}"
                                   f"{' · ' + _e(c['label']) if c.get('label') else ''})</span></li>"
                                   for c in finder["candidates"]) + "</ul>")
    if turn["materials"]:
        inner.append("<div>자료 글: " + _e(" / ".join(f"{m['title']} ({m['type']})" for m in turn["materials"])) + "</div>")
    for facts in turn["dictionary"]:
        inner.append(f"<div>사전: 대상 {_e(', '.join(facts['targets']) or '없음')} · 사실 {facts['lines']}줄"
                     f"{' · 조회 실패' if facts['failed'] else ''}</div>")
    for call in turn["llm"]:
        reply = call.get("reply") or {}
        inner.append(f"<div>LLM: {_e(call.get('model'))} · 생각 정도 {_e(call.get('effort') or '기본')} · {call.get('seconds')}초"
                     f" · picks {_e(reply.get('picks'))} · used {_e(reply.get('used'))} · usedFacts {_e(reply.get('usedFacts'))}"
                     f" · evidence {_e(reply.get('evidence'))}{' · 오류 ' + _e(call['error']) if call.get('error') else ''}</div>"
                     f"<details><summary>LLM 에 넘긴 질문 묶음 ({len(call.get('prompt') or ''):,}자)</summary>"
                     f"<pre>{_e(call.get('prompt'))}</pre></details>")
    for error in turn.get("errors") or []:
        inner.append(f"<div class='bad'>오류: {_e(error)}</div>")
    if inner:
        parts.append("<details><summary>안쪽 보기 (글 찾기 · 자료 · 사전 · LLM)</summary>" + "".join(inner) + "</details>")
    return "<div class='turn'>" + "".join(parts) + "</div>"


def render_run(data: dict) -> str:
    meta, total, records = data["meta"], data["totals"], data["cases"]
    tokens = total["tokens"]
    body = [f"<h1>챗봇 시험 결과 · {_e(meta['label'])}</h1>",
            f"<div class='meta'>{_e(meta['startedAt'])} · DB {_e(meta['database'])} · 생각 정도 {_e(meta['effort'] or '기본')}"
            f" · 정보사전 {'끔' if meta['dictOff'] else '켬'} · 커밋 {_e(meta['commit'])} · 질문 파일 {_e(meta['casesHash'])}</div>",
            f"<p>질문 {total['cases']}개 (건너뜀 {total['skipped']}) · 답 {total['turns']}개 · LLM {total['llmCalls']}번 · "
            f"<span class='{'bad' if total['flaggedTurns'] else 'ok'}'>검사에 걸린 답 {total['flaggedTurns']}개</span> · "
            f"실패 {total['failed']}개<br>시간 합계 {total['seconds']}초 · 평균 {total['avgSeconds']}초 · 가장 긴 답 "
            f"{total['maxSeconds']}초<br>토큰 입력 {tokens['input']:,} (캐시 {tokens['cached']:,}) · 생각 "
            f"{tokens['reasoning']:,} · 출력 {tokens['output']:,} · 약 {total['won']}원</p>",
            "<p class='muted'>원은 추정이야: 정책 문서의 가격(100만 토큰당 입력 $0.10 · 출력 $0.50, 공식 가격표 확인 필요)과 "
            "1달러 = 1,400원 가정. 자동 검사는 답의 모양만 봐. 내용이 맞는지는 사람이 판단해.</p>",
            "<table><tr><th>번호</th><th>질문</th><th>종류</th><th>카드</th><th>시간</th><th>걸린 검사</th></tr>"]
    for record in records:
        if record.get("error"):
            body.append(f"<tr><td><a href='#{_e(record['id'])}'>{_e(record['id'])}</a></td>"
                        f"<td colspan='5' class='bad'>건너뜀: {_e(record['error'])}</td></tr>")
            continue
        for run in record["runs"]:
            for turn in run:
                flags = _flags(turn)
                body.append(f"<tr><td><a href='#{_e(record['id'])}'>{_e(record['id'])}</a></td><td>{_e(turn['ask'])}</td>"
                            f"<td>{_e(turn['kind'])}</td><td>{len(turn['cards'])}</td><td>{turn['seconds']}초</td>"
                            f"<td class='{'bad' if flags else 'ok'}'>{_e(', '.join(c['name'] for c in flags) or '없음')}</td></tr>")
    body.append("</table>")
    for record in records:
        where = "상세 · " + record["anchor"]["title"] if record.get("anchor") else ("상세" if record["screen"] == "detail" else "홈")
        body.append(f"<h2 id='{_e(record['id'])}'>{_e(record['id'])} · {_e(record['rule'])} · {_e(where)}</h2>"
                    f"<div class='muted'>기대: {_e(record['expect'])}</div>")
        if record.get("viewed"):
            body.append("<div class='muted'>시험용 본 글: " + _e(" / ".join(post["title"] for post in record["viewed"])) + "</div>")
        if record.get("error"):
            body.append(f"<p class='bad'>건너뜀: {_e(record['error'])}</p>")
        for index, run in enumerate(record["runs"], 1):
            if len(record["runs"]) > 1:
                body.append(f"<h3>새 대화 {index}</h3>")
            for number, turn in enumerate(run, 1):
                body.append(_turn_html(turn, number if len(run) > 1 else None))
    return _page(f"챗봇 시험 {meta['label']}", "".join(body))


def render_compare(a: dict, b: dict) -> str:
    labels = (a["meta"]["label"], b["meta"]["label"])
    rows = []
    for data in (a, b):
        total, tokens = data["totals"], data["totals"]["tokens"]
        rows.append(f"<tr><td>{_e(data['meta']['label'])}</td><td>{_e(data['meta']['startedAt'])}</td>"
                    f"<td>{_e(data['meta']['effort'] or '기본')} · 사전 {'끔' if data['meta']['dictOff'] else '켬'}</td>"
                    f"<td>{total['turns']}</td><td>{total['flaggedTurns']}</td><td>{total['failed']}</td>"
                    f"<td>{total['avgSeconds']}초</td><td>{tokens['input']:,} / {tokens['reasoning']:,} / {tokens['output']:,}</td>"
                    f"<td>{total['won']}원</td></tr>")
    body = [f"<h1>챗봇 시험 비교 · {_e(labels[0])} ↔ {_e(labels[1])}</h1>",
            "<table><tr><th>이름</th><th>시각</th><th>설정</th><th>답</th><th>걸린 답</th><th>실패</th><th>평균 시간</th>"
            "<th>토큰 입력 / 생각 / 출력</th><th>원 (추정)</th></tr>" + "".join(rows) + "</table>"]
    first = {record["id"]: record for record in a["cases"]}
    second = {record["id"]: record for record in b["cases"]}
    for case_id in list(dict.fromkeys(list(first) + list(second))):
        records = (first.get(case_id), second.get(case_id))
        expect = next((record["expect"] for record in records if record), "")
        body.append(f"<h2>{_e(case_id)}</h2><div class='muted'>기대: {_e(expect)}</div>")
        columns = []
        for label, record in zip(labels, records):
            if record is None:
                columns.append(f"<div><h3>{_e(label)}</h3><p class='muted'>이 질문을 안 돌림</p></div>")
                continue
            turns = all_turns(record)
            inner = "".join(_turn_html(turn, number) for number, turn in enumerate(turns, 1)) or \
                f"<p class='bad'>건너뜀: {_e(record.get('error'))}</p>"
            columns.append(f"<div><h3>{_e(label)}</h3>{inner}</div>")
        body.append("<div class='cols'>" + "".join(columns) + "</div>")
    return _page(f"챗봇 시험 비교 {labels[0]} {labels[1]}", "".join(body))


# ---------- 실행 ----------

async def dry_run(cases: list[dict]) -> None:
    ctx = await prepare()
    calls = 0
    async with AsyncSessionLocal() as db:
        for case in cases:
            notes = []
            try:
                if case["anchor"]:
                    post = await find_post(db, ctx.topic_id, case["anchor"])
                    notes.append(f"기준 글 [{post.content_id}] {post.title}")
                for words in case["viewed"]:
                    post = await find_post(db, ctx.topic_id, words)
                    notes.append(f"본 글 [{post.content_id}] {post.title}")
            except CaseError as error:
                notes.append(f"문제: {error}")
            calls += len(case["turns"]) * case["runs"]
            print(f"{case['id']:4} {' → '.join(turn['ask'] for turn in case['turns'])}"
                  + (f"  ×{case['runs']}" if case["runs"] > 1 else "") + "".join(f"\n       {note}" for note in notes))
    print(f"\n질문 {len(cases)}개 · LLM 호출 {calls}번 예정")


async def run_all(cases: list[dict], *, label: str, effort: str | None, dict_off: bool, database: str) -> Path:
    install_patches(effort=effort, dict_off=dict_off)
    ctx = await prepare()
    started = datetime.now(KST)
    records = []
    for number, case in enumerate(cases, 1):
        record = await run_case(ctx, case)
        records.append(record)
        if record.get("error"):
            print(f"[{number}/{len(cases)}] {case['id']} 건너뜀: {record['error']}", flush=True)
            continue
        for run in record["runs"]:
            for turn in run:
                flags = _flags(turn)
                print(f"[{number}/{len(cases)}] {case['id']} {turn['ask'][:28]} → {turn['status']} {turn['seconds']}초 · "
                      f"카드 {len(turn['cards'])} · 걸림 {len(flags)}"
                      + (f" ({', '.join(check['name'] for check in flags)})" if flags else "")
                      + (f" — {turn['errors'][0]}" if turn["status"] != "completed" and turn["errors"] else ""), flush=True)
    add_opening_checks(records)
    models = sorted({call.get("model") for record in records for turn in all_turns(record)
                     for call in turn["llm"] if call.get("model")})
    data = {
        "meta": {"label": label, "startedAt": started.strftime("%Y-%m-%d %H:%M"), "database": database,
                 "effort": effort, "dictOff": dict_off, "models": models, "commit": git_commit(),
                 "casesHash": hashlib.sha256(CASES_FILE.read_bytes()).hexdigest()[:10]},
        "totals": totals(records), "cases": records,
    }
    RESULTS.mkdir(exist_ok=True)
    stem = RESULTS / f"{started.strftime('%Y%m%d-%H%M%S')}-{safe_label(label)}"
    stem.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    stem.with_suffix(".html").write_text(render_run(data), encoding="utf-8")
    total = data["totals"]
    print(f"\n답 {total['turns']}개 · 걸린 답 {total['flaggedTurns']}개 · 실패 {total['failed']}개 · "
          f"평균 {total['avgSeconds']}초 · 약 {total['won']}원")
    return stem.with_suffix(".html")


async def _close() -> None:
    await engine.dispose()
    cached = getattr(dictionary, "_engine_cache", None)
    if cached is not None:
        await cached.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="챗봇 시험 세트를 돌리고 보고서를 만든다 (로컬 시험 DB 전용).")
    parser.add_argument("--only", help="돌릴 질문 번호 (쉼표로). 글자 하나면 그 묶음 전체 (예: C,E1)")
    parser.add_argument("--label", help="결과 파일 이름에 붙일 말 (기본: 생각 정도 · 사전 설정)")
    parser.add_argument("--effort", choices=["none", "minimal", "low", "medium", "high"],
                        help="LLM 생각 정도. 안 주면 보내지 않는다 (모델 기본값)")
    parser.add_argument("--dict", choices=["on", "off"], default="on", help="정보사전 켜기 · 끄기")
    parser.add_argument("--dry-run", action="store_true", help="질문 · 기준 글만 확인 (LLM 안 부름)")
    parser.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="두 결과를 나란히 (LLM 안 부름)")
    args = parser.parse_args()
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.compare:
        first, second = (json.loads(Path(path).read_text(encoding="utf-8")) for path in args.compare)
        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / f"compare-{safe_label(first['meta']['label'])}-{safe_label(second['meta']['label'])}.html"
        out.write_text(render_compare(first, second), encoding="utf-8")
        print(f"비교 보고서: {out}")
        return

    database = guard()
    cases = pick_cases(load_cases(CASES_FILE), args.only)
    dict_off = args.dict == "off"
    label = args.label or "-".join(part for part in (args.effort or "기본", "사전끔" if dict_off else "") if part)
    print(f"DB {database} · 질문 {len(cases)}개 · 생각 정도 {args.effort or '기본'} · 정보사전 {'끔' if dict_off else '켬'}")
    if not args.dry_run and not settings.llm_key:
        raise SystemExit("LLM_KEY 가 없어서 답을 만들 수 없음 (backend/.env)")

    async def task():
        try:
            if args.dry_run:
                await dry_run(cases)
                return None
            return await run_all(cases, label=label, effort=args.effort, dict_off=dict_off, database=database)
        finally:
            await _close()

    if sys.platform == "win32":
        # psycopg 의 비동기 연결은 윈도우 기본 이벤트 루프에서 못 돈다.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            report = runner.run(task())
    else:
        report = asyncio.run(task())
    if report:
        print(f"보고서: {report}")


if __name__ == "__main__":
    main()
