"""에이전트 마디와 갈림길. 마디는 바꿀 값만 돌려준다."""

import asyncio
import json
import re
from datetime import datetime, timezone

from langchain_core.runnables import RunnableConfig

from agent.chatbot import limits, phrases, prompts
from agent.chatbot.state import ChatDeps, ChatState

# 앞단 규칙: 뻔한 공격 문장
_ATTACK_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(이전|앞의|위의|지금까지의?)\s*.{0,10}(지시|명령|규칙|설정).{0,10}(무시|잊어|따르지)",
        r"(시스템|system)\s*(프롬프트|prompt)",
        r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions",
        r"(개발자|관리자)\s*모드",
        r"너의\s*(지시문|설정|규칙)(을|를)?\s*(보여|알려|출력)",
    )
]
# 입구 검사: 무료 판별 모델 분류 → 위험 신호
_S1_CATEGORIES = {"self-harm", "self-harm/intent", "self-harm/instructions"}
_S3_CATEGORIES = {
    "sexual/minors",
    "illicit/violent",
    "hate/threatening",
    "harassment/threatening",
    "violence/graphic",
}
# 출구 검사(코드): 개인정보
_PII_PATTERNS = [
    re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}"),  # 휴대전화
    re.compile(r"\d{6}\s?-\s?[1-4]\d{6}"),  # 주민등록번호
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # 이메일
]
_HELPLINES = ("109", "1577-0199")


def _deps(config: RunnableConfig) -> ChatDeps:
    return config["configurable"]["deps"]


def _pre_write_budget(state: ChatState) -> bool:
    return state.get("llm_calls", 0) < limits.LLM_CALL_CAP - limits.WRITE_RESERVE


def _time_left(state: ChatState, deps: ChatDeps) -> bool:
    return deps.clock() - state["started_at"] < limits.TURN_SECONDS


def _clip(text: str | None, size: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= size else text[:size] + "…"


async def _ask(
    state: ChatState, deps: ChatDeps, name: str, effort: str, instructions: str, prompt: str
) -> tuple[dict | None, dict]:
    """LLM을 한 번 부르고 기록 한 줄을 만든다. 실패하면 None을 돌려준다."""
    started = datetime.now(timezone.utc)
    try:
        result = await asyncio.wait_for(
            deps.llm_json(instructions, prompt, effort), limits.CALL_TIMEOUT_SECONDS
        )
        ok = isinstance(result, dict)
    except Exception as exc:  # noqa: BLE001 — 실패는 기록하고 갈림길에서 처리한다.
        result, ok = {"error": f"{type(exc).__name__}: {exc}"[:300]}, False
    step = {
        "name": name,
        "model": deps.model_name,
        "effort": effort,
        "promptVersion": prompts.PROMPT_VERSION,
        "input": prompt[:2000],
        "result": result,
        "ok": ok,
        "startedAt": started.isoformat(),
        "finishedAt": datetime.now(timezone.utc).isoformat(),
    }
    return (result if ok else None), step


def _counted(state: ChatState, *steps: dict) -> dict:
    return {
        "llm_calls": state.get("llm_calls", 0) + len(steps),
        "steps": [*state.get("steps", []), *steps],
    }


def _fixed(result_type: str, message: str, notices: list[dict] | None = None, **extra) -> dict:
    return {
        "result_type": result_type,
        "message": message,
        "sources": [],
        "notices": notices or [],
        "unanswered_reason": None,
        **extra,
    }


def _history_block(state: ChatState) -> str:
    lines = [
        f"{'사용자' if m['role'] == 'user' else 'AI'}: {_clip(m['text'], 400)}"
        for m in state.get("history", [])
    ]
    parts = []
    if state.get("memory_summary"):
        parts.append(f"(앞 대화 요약) {state['memory_summary']}")
    parts.extend(lines)
    return "\n".join(parts) or "(없음)"


def _evidence_block(evidence: list[dict]) -> str:
    if not evidence:
        return "(없음)"
    return "\n\n".join(
        f"- contentId {e['contentId']} · {e['title']}\n{e['excerpt']}" for e in evidence
    )


def _facts_block(facts: list[dict]) -> str:
    if not facts:
        return "(없음)"
    return "\n".join(f"- {f['text']} ({f['attribution']} · {f['checkedAt']} 확인)" for f in facts)


def _question_block(state: ChatState) -> str:
    parts = [f"[분야] {state['topic_name']} ({state['topic_code']})"]
    if state.get("anchor"):
        parts.append(f"[지금 보고 있는 글] {state['anchor']['title']}")
    if state.get("selected_text"):
        parts.append(f"[사용자가 고른 문장]\n{_clip(state['selected_text'], 800)}")
    parts.append(f"[대화]\n{_history_block(state)}")
    parts.append(f"[질문]\n{state['question']}")
    return "\n\n".join(parts)


# ---------- 앞단 (코드) ----------

async def front(state: ChatState, config: RunnableConfig) -> dict:
    update: dict = {
        "llm_calls": 0,
        "sub_calls": 0,
        "replans": 0,
        "planner_runs": 0,
        "retrieve_tries": 0,
        "fact_tries": 0,
        "exit_retries": 0,
        "evidence": [],
        "facts": [],
        "exit_feedback": [],
        "steps": [],
        "outcome": None,
        "limit_hit": None,
        "fact_failed": False,
        "pending_fact": False,
        "clarify": None,
        "draft": None,
    }
    if state.get("anchor"):
        update["evidence"] = [state["anchor"]]  # 지금 보고 있는 글을 첫 근거로 둔다.
    if not state.get("topic_id"):
        update["outcome"] = _fixed("blocked", phrases.NO_TOPIC)
        return update
    question = (state.get("question") or "").strip()[: limits.MAX_QUESTION_CHARS]
    update["question"] = question
    if any(p.search(question) for p in _ATTACK_PATTERNS):
        update["outcome"] = _fixed("blocked", phrases.ATTACK_REFUSAL, blocked_by="front_rule")
    return update


# ---------- ① 입구 검사 ----------

async def gate(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    text = f"{state['question']}\n{state.get('selected_text') or ''}".strip()
    try:
        moderation = await asyncio.wait_for(deps.moderate(text), limits.CALL_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — 판별 모델이 안 되면 규칙과 LLM으로 계속 본다.
        moderation = {"flagged": False, "categories": {}}
    flagged = {name for name, hit in (moderation.get("categories") or {}).items() if hit}
    if flagged & _S1_CATEGORIES:
        return {
            "outcome": _fixed(
                "answer", phrases.S1_HELPLINE, [{"code": "S1", "message": "상담 창구 안내"}]
            )
        }
    if flagged & _S3_CATEGORIES:
        return {"outcome": _fixed("blocked", phrases.S3_BLOCKED, blocked_by="gate_s3")}

    result, step = await _ask(state, deps, "gate", "none", prompts.GATE, f"[질문]\n{text}")
    update = _counted(state, step)
    if result and result.get("attack") is True:
        update["outcome"] = _fixed("blocked", phrases.ATTACK_REFUSAL, blocked_by="gate_llm")
    return update


# ---------- ② 총괄 ----------

def _planner_prompt(state: ChatState) -> str:
    others = ", ".join(f"{t['code']}({t['name']})" for t in state.get("other_topics", []))
    providers = ", ".join(state.get("fact_providers", [])) or "(없음)"
    return "\n\n".join(
        [
            _question_block(state),
            f"[다른 분야] {others or '(없음)'}",
            f"[쓸 수 있는 조회] {providers}",
            f"[지금까지 모은 자료]\n{_evidence_block(state.get('evidence', []))}",
            f"[지금까지 조회한 사실]\n{_facts_block(state.get('facts', []))}",
            f"[남은 하위 호출] {limits.SUB_CALL_CAP - state.get('sub_calls', 0)}",
        ]
    )


async def planner(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    runs = state.get("planner_runs", 0)
    update: dict = {"planner_runs": runs + 1}

    if not _time_left(state, deps):
        return {**update, "next_step": "write", "limit_hit": "time", "route": state.get("route", "work")}
    if not _pre_write_budget(state) or state.get("sub_calls", 0) >= limits.SUB_CALL_CAP:
        return {**update, "next_step": "write", "limit_hit": "calls", "route": state.get("route", "work")}

    result, step = await _ask(
        state, deps, "planner", "low", prompts.PLANNER, _planner_prompt(state)
    )
    update.update(_counted(state, step))
    if result is None:
        # 첫 계획이 실패하면 질문 그대로 한 번 찾아 본다. 그 뒤 실패는 모은 것으로 쓴다.
        if runs == 0:
            return {**update, "route": "work", "next_step": "retrieve", "retrieve_query": state["question"]}
        return {**update, "next_step": "write"}

    route = result.get("route") if runs == 0 else state.get("route", "work")
    if route not in {"work", "chitchat", "unrelated", "other_topic", "unclear"}:
        route = "work"
    update["route"] = route
    if runs == 0 and route == "other_topic":
        codes = {t["code"] for t in state.get("other_topics", [])}
        target = result.get("targetTopic")
        if target in codes:
            update["target_topic_code"] = target
        else:
            update["route"] = route = "work"
    if route != "work":
        return {**update, "next_step": "write"}

    next_step = result.get("next")
    if result.get("enough") is True or next_step not in {"retrieve", "fact", "both"}:
        return {**update, "next_step": "write", "evidence_enough": bool(result.get("enough"))}

    if runs > 0:
        if state.get("replans", 0) >= limits.REPLAN_CAP:
            return {**update, "next_step": "write"}
        update["replans"] = state.get("replans", 0) + 1

    fact_query = result.get("factQuery") if isinstance(result.get("factQuery"), dict) else None
    if fact_query and fact_query.get("provider") not in state.get("fact_providers", []):
        fact_query = None  # 코드 벽: 이 토픽에 허용된 조회처만
    if next_step in {"fact", "both"} and fact_query is None:
        next_step = "retrieve"
    query = str(result.get("retrieveQuery") or state["question"])[:200]
    if next_step == "both":
        return {**update, "next_step": "retrieve", "pending_fact": True, "retrieve_query": query, "fact_query": fact_query}
    if next_step == "fact":
        return {**update, "next_step": "fact", "fact_query": fact_query}
    return {**update, "next_step": "retrieve", "retrieve_query": query}


def route_after_planner(state: ChatState) -> str:
    return state.get("next_step", "write")


# ---------- ③ 근거 찾기 ----------

async def retrieve(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    evidence = list(state.get("evidence", []))
    have = {e["contentId"] for e in evidence}
    update: dict = {"sub_calls": state.get("sub_calls", 0) + 1}
    steps: list[dict] = []
    query = state.get("retrieve_query") or state["question"]
    tries = 0
    enough = False

    while True:
        candidates = await deps.search(state["topic_id"], query, have)
        if not candidates:
            break
        if not _pre_write_budget({**state, "llm_calls": state.get("llm_calls", 0) + len(steps)}):
            picked = candidates[:3]  # 호출 여유가 없으면 점수 순으로 가져간다.
            retry = None
        else:
            listing = "\n\n".join(
                f"[후보 {i}] {c['title']}\n{c['excerpt']}" for i, c in enumerate(candidates, start=1)
            )
            result, step = await _ask(
                state,
                deps,
                "retrieve",
                "none",
                prompts.RETRIEVER,
                f"{_question_block(state)}\n\n[후보 글]\n{listing}",
            )
            steps.append(step)
            if result is None:
                picked, retry = candidates[:3], None
            else:
                use = [n for n in result.get("use") or [] if isinstance(n, int) and 1 <= n <= len(candidates)]
                picked = [candidates[n - 1] for n in dict.fromkeys(use)]
                enough = result.get("enough") is True
                retry = result.get("retryQuery")
        for item in picked:
            if item["contentId"] not in have and len(evidence) < limits.MAX_EXCERPTS:
                evidence.append(item)
                have.add(item["contentId"])
        if enough or not retry or tries >= limits.RETRIEVE_RETRY_CAP:
            break
        tries += 1
        query = str(retry)[:200]

    update.update(_counted(state, *steps))
    update.update(
        {
            "evidence": evidence,
            "evidence_enough": enough,
            "retrieve_tries": state.get("retrieve_tries", 0) + tries,
        }
    )
    return update


def route_after_retrieve(state: ChatState) -> str:
    return "fact" if state.get("pending_fact") else "planner"


# ---------- ④ 사실 확인 ----------

async def fact(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    update: dict = {"sub_calls": state.get("sub_calls", 0) + 1, "pending_fact": False}
    query = dict(state.get("fact_query") or {})
    provider = query.get("provider")
    if provider not in state.get("fact_providers", []):
        return {**update, "fact_failed": True}

    facts = list(state.get("facts", []))
    steps: list[dict] = []
    tries = 0
    clarify = None
    found = False
    search = str(query.get("query") or state["question"])[:200]

    while True:
        data = await deps.fact_lookup(provider, search, state["topic_id"])
        if data is not None and _pre_write_budget({**state, "llm_calls": state.get("llm_calls", 0) + len(steps)}):
            result, step = await _ask(
                state,
                deps,
                "fact",
                "low",
                prompts.FACT_CHECKER,
                f"{_question_block(state)}\n\n[자료]\n{_evidence_block(state.get('evidence', []))}"
                f"\n\n[조회 결과]\n{json.dumps(data.get('data'), ensure_ascii=False)[:3000]}",
            )
            steps.append(step)
            if result is not None:
                texts = [str(t)[:200] for t in result.get("facts") or [] if str(t).strip()]
                for text in texts[:5]:
                    facts.append(
                        {
                            "text": text,
                            "provider": provider,
                            "attribution": data.get("attribution") or provider,
                            "checkedAt": data.get("checkedAt"),
                        }
                    )
                found = found or bool(texts)
                if result.get("ambiguous") is True:
                    clarify = str(result.get("clarify") or "")[:200] or None
                retry = result.get("retryQuery")
                if texts and not result.get("ambiguous"):
                    clarify = None
                    break
            else:
                retry = None
        else:
            retry = None
        if not retry or tries >= limits.FACT_RETRY_CAP:
            break
        tries += 1
        search = str(retry)[:200]

    update.update(_counted(state, *steps))
    update.update(
        {
            "facts": facts,
            "fact_tries": state.get("fact_tries", 0) + tries,
            "fact_failed": not found,
            "clarify": clarify,
        }
    )
    return update


# ---------- ⑤ 답변 작성 ----------

def _write_task(state: ChatState) -> str:
    route = state.get("route", "work")
    if route == "other_topic":
        target = next(
            (t for t in state.get("other_topics", []) if t["code"] == state.get("target_topic_code")),
            None,
        )
        name = target["name"] if target else "다른"
        return f"다른 분야 질문이다. '{name}' 분야에서 물어봐 달라고 한두 문장으로 안내한다."
    if route == "unclear" or state.get("clarify"):
        hint = f" 되물을 내용: {state['clarify']}" if state.get("clarify") else ""
        return "무엇을 묻는지 확실하지 않다. 사용자에게 한 문장으로 되묻는다." + hint
    if route == "chitchat":
        return "잡담이다. 짧게 받아 주고 이 분야 이야기로 자연스럽게 이어 간다."
    if route == "unrelated":
        return "이 분야와 상관없는 질문이다. 답할 수 없다고 짧게 말하고 이 분야에서 도울 수 있는 것을 말한다."
    if not state.get("evidence") and not state.get("facts"):
        return "모아 둔 자료가 없다. 확인되지 않는다고 말하고 추측하지 않는다."
    return "질문에 답한다."


async def write(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    parts = [
        _question_block(state),
        f"[할 일] {_write_task(state)}",
        f"[자료]\n{_evidence_block(state.get('evidence', []))}",
        f"[조회 결과]\n{_facts_block(state.get('facts', []))}",
    ]
    if state.get("exit_feedback"):
        previous = (state.get("draft") or {}).get("answer", "")
        parts.append(f"[앞 답변]\n{previous}")
        parts.append("[점검 지적]\n" + "\n".join(f"- {p}" for p in state["exit_feedback"]))
    result, step = await _ask(state, deps, "write", "low", prompts.WRITER, "\n\n".join(parts))
    update = _counted(state, step)
    answer = str((result or {}).get("answer") or "").strip()
    if not answer:
        update["outcome"] = _fixed("failed", phrases.LLM_DOWN)
        return update
    update["draft"] = {
        "answer": answer,
        "used": [n for n in (result.get("used") or []) if isinstance(n, int)],
        "evidence": result.get("evidence"),
    }
    return update


def route_after_write(state: ChatState) -> str:
    return "end" if state.get("outcome") else "exit_check"


# ---------- ⑥ 출구 점검 ----------

def _code_check(answer: str) -> str | None:
    if any(marker in answer for marker in prompts.LEAK_MARKERS):
        return "leak"
    scrubbed = answer
    for number in _HELPLINES:
        scrubbed = scrubbed.replace(number, "")
    if any(p.search(scrubbed) for p in _PII_PATTERNS):
        return "pii"
    return None


def _final(state: ChatState, draft: dict, sensitive: bool) -> dict:
    route = state.get("route", "work")
    notices: list[dict] = []
    sources: list[dict] = []
    reason = None
    if route == "other_topic":
        target = next(
            (t for t in state.get("other_topics", []) if t["code"] == state.get("target_topic_code")),
            None,
        )
        if target:
            notices.append({"code": "TOPIC_SWITCH", "topicId": str(target["topicId"])})
            return _fixed("topic_switch_suggested", draft["answer"], notices)
    if route == "unclear" or state.get("clarify"):
        return _fixed("needs_clarification", draft["answer"])

    by_id = {e["contentId"]: e for e in state.get("evidence", [])}
    for content_id in dict.fromkeys(draft.get("used", [])):
        if content_id in by_id:
            e = by_id[content_id]
            sources.append({"contentId": e["contentId"], "title": e["title"], "url": e.get("url", "")})
    if route == "work":
        if state.get("facts"):
            shown = set()
            for f in state["facts"]:
                key = (f["attribution"], f["checkedAt"])
                if key in shown:
                    continue
                shown.add(key)
                notices.append(
                    {
                        "code": "FACT_SOURCE",
                        "message": phrases.SOURCE_TEMPLATE.format(
                            label="사실 정보", attribution=f["attribution"], checked=f["checkedAt"]
                        ),
                    }
                )
            notices.append({"code": "MAY_HAVE_CHANGED", "message": phrases.MAY_HAVE_CHANGED})
        insufficient = draft.get("evidence") == "insufficient" or (not sources and not state.get("facts"))
        if insufficient:
            notices.append({"code": "EVIDENCE_INSUFFICIENT", "message": phrases.INSUFFICIENT})
            reason = "fact_lookup_failed" if state.get("fact_failed") and not state.get("evidence") else "no_evidence"
    if sensitive:
        notices.append({"code": "SENSITIVE_DOMAIN", "message": phrases.SENSITIVE_NOTICE})
    if state.get("limit_hit"):
        notices.append({"code": "TURN_LIMIT", "message": phrases.TURN_LIMIT})
        reason = reason or ("turn_limit" if route == "work" else None)
    return {
        "result_type": "answer",
        "message": draft["answer"],
        "sources": sources,
        "notices": notices,
        "unanswered_reason": reason,
    }


async def exit_check(state: ChatState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    draft = state["draft"] or {}
    answer = draft.get("answer", "")

    problem = _code_check(answer)
    if problem:  # 개인정보 · 설정 유출은 되돌리지 않고 바로 막는다.
        return {"outcome": _fixed("blocked", phrases.LEAK_BLOCKED, blocked_by=f"exit_{problem}")}
    try:
        moderation = await asyncio.wait_for(deps.moderate(answer), limits.CALL_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        moderation = {"flagged": False, "categories": {}}
    flagged = {name for name, hit in (moderation.get("categories") or {}).items() if hit}
    if flagged & (_S1_CATEGORIES | _S3_CATEGORIES):
        return {"outcome": _fixed("blocked", phrases.SAFE_FALLBACK, blocked_by="exit_moderation")}

    prompt = "\n\n".join(
        [
            f"[분야] {state['topic_name']}",
            f"[질문]\n{state['question']}",
            f"[자료]\n{_evidence_block(state.get('evidence', []))}",
            f"[조회 결과]\n{_facts_block(state.get('facts', []))}",
            f"[답변]\n{answer}",
        ]
    )
    steps: list[dict] = []
    result = None
    for _ in range(2):  # FR-607-4: 점검이 실패하면 답을 막고 점검을 다시 한다.
        if state.get("llm_calls", 0) + len(steps) >= limits.LLM_CALL_CAP:
            break
        result, step = await _ask(state, deps, "exit_check", "none", prompts.EXIT_CHECKER, prompt)
        steps.append(step)
        if result is not None:
            break
    update = _counted(state, *steps)
    if result is None:
        update["outcome"] = _fixed("failed", phrases.LLM_DOWN, blocked_by="exit_unavailable")
        return update
    if result.get("pass") is True:
        update["outcome"] = _final(state, draft, result.get("sensitive") is True)
        return update

    problems = [str(p)[:200] for p in result.get("problems") or []][:5] or ["점검에서 통과하지 못했다."]
    can_retry = (
        state.get("exit_retries", 0) < limits.EXIT_RETRY_CAP
        and update["llm_calls"] + 2 <= limits.LLM_CALL_CAP
    )
    if can_retry:
        update.update({"exit_retries": state.get("exit_retries", 0) + 1, "exit_feedback": problems})
    else:
        update["outcome"] = _fixed("blocked", phrases.SAFE_FALLBACK, blocked_by="exit_second_fail")
    return update


def route_after_exit(state: ChatState) -> str:
    return "end" if state.get("outcome") else "write"


def route_stop_or(next_node: str):
    def _route(state: ChatState) -> str:
        return "end" if state.get("outcome") else next_node

    return _route
