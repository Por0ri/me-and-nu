"""OpenAI Responses API 호출. 에이전트와 같은 키(LLM_KEY)와 모델을 쓴다."""

import json

import httpx

from app.core.config import settings

CHAT_MODEL = "gpt-6-luna"
RESPONSES_URL = "https://api.openai.com/v1/responses"
MODERATIONS_URL = "https://api.openai.com/v1/moderations"
MODERATION_MODEL = "omni-moderation-latest"  # 무료 판별 모델


class LlmUnavailable(Exception):
    """키가 없거나 호출이 실패해 답을 만들지 못함."""


def _output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                return part.get("text", "")
    return payload.get("output_text") or ""


async def ask_json(
    instructions: str,
    prompt: str,
    *,
    timeout: float = 25.0,
    reasoning_effort: str | None = None,
) -> dict:
    """JSON 한 덩어리로 답하라고 시키고, 파싱한 dict를 돌려준다.

    reasoning_effort를 주면 추론 강도를 직접 정한다(서버 기본값 medium은 비싸다).
    서버가 그 값을 받지 않으면 추론 강도 없이 한 번 더 부른다.
    """
    if not settings.llm_key:
        raise LlmUnavailable("LLM_KEY is not configured")
    body = {
        "model": CHAT_MODEL,
        "instructions": instructions,
        # json_object 형식은 입력에도 'json'이라는 단어가 있어야 한다.
        "input": f"{prompt}\n\n(답은 JSON 하나로만)",
        "text": {"format": {"type": "json_object"}},
    }
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    headers = {"Authorization": f"Bearer {settings.llm_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(RESPONSES_URL, headers=headers, json=body)
            if response.status_code == 400 and "reasoning" in body and "reasoning" in response.text:
                body.pop("reasoning")
                response = await client.post(RESPONSES_URL, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise LlmUnavailable(str(exc)) from exc
    if response.is_error:
        raise LlmUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
    text = _output_text(response.json()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmUnavailable("LLM did not return JSON") from exc


async def moderate(text: str, *, timeout: float = 10.0) -> dict:
    """무료 판별 모델로 위험 내용을 본다. {"flagged": bool, "categories": {이름: bool}}"""
    if not settings.llm_key:
        raise LlmUnavailable("LLM_KEY is not configured")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                MODERATIONS_URL,
                headers={"Authorization": f"Bearer {settings.llm_key}"},
                json={"model": MODERATION_MODEL, "input": text[:4000]},
            )
    except httpx.HTTPError as exc:
        raise LlmUnavailable(str(exc)) from exc
    if response.is_error:
        raise LlmUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
    first = (response.json().get("results") or [{}])[0]
    return {"flagged": bool(first.get("flagged")), "categories": first.get("categories") or {}}
