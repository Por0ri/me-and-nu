"""OpenAI Responses API 호출. 에이전트와 같은 키(LLM_KEY)와 모델을 쓴다."""

import json

import httpx

from app.core.config import settings

CHAT_MODEL = "gpt-6-luna"
RESPONSES_URL = "https://api.openai.com/v1/responses"


class LlmUnavailable(Exception):
    """키가 없거나 호출이 실패해 답을 만들지 못함."""


def _output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                return part.get("text", "")
    return payload.get("output_text") or ""


async def ask_json(instructions: str, prompt: str, *, timeout: float = 25.0) -> dict:
    """JSON 한 덩어리로 답하라고 시키고, 파싱한 dict를 돌려준다."""
    if not settings.llm_key:
        raise LlmUnavailable("LLM_KEY is not configured")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                RESPONSES_URL,
                headers={"Authorization": f"Bearer {settings.llm_key}"},
                json={
                    "model": CHAT_MODEL,
                    "instructions": instructions,
                    # json_object 형식은 입력에도 'json'이라는 단어가 있어야 한다.
                    "input": f"{prompt}\n\n(답은 JSON 하나로만)",
                    "text": {"format": {"type": "json_object"}},
                },
            )
    except httpx.HTTPError as exc:
        raise LlmUnavailable(str(exc)) from exc
    if response.is_error:
        raise LlmUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
    text = _output_text(response.json()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmUnavailable("LLM did not return JSON") from exc
