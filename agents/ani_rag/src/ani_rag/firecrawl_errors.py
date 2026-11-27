"""Recognize an explicit Firecrawl billing refusal before a paid operation began."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any


_CREDIT_REFUSAL = re.compile(
    r"\b(?:insufficient|(?:not|don['’]t|doesn['’]t)(?: have)? enough|out of|no (?:remaining|available)|exhausted|depleted) credits?\b|"
    r"\b(?:no|zero|0) credits? (?:left|remaining|available|to perform)\b|"
    r"\bcredit balance (?:is |of )?(?:zero|0)\b(?![.,]\d)|"
    r"\bcredits? (?:balance )?(?:(?:is|are|has been|have been) )?(?:insufficient|exhausted|depleted|used up)\b",
    re.I,
)
_OTHER_REFUSAL = re.compile(
    r"\b(?:too many requests|rate limit(?:ed)?|quota exceeded|unauthori[sz]ed|forbidden|invalid api key|"
    r"target (?:site|website|page|url|server|host)|(?:upstream|origin|remote) (?:site|website|page|server|host|http|402)|"
    r"(?:web ?page|website|site|origin|upstream|target) returned|paywall|subscription required)\b|"
    r"\b(?:status(?: code)?|http)\s*[:=]?\s*(?:401|403|429|5\d\d)\b", re.I,
)
_ACCEPTED_JOB = re.compile(
    r"""\b(?:job|scrape|crawl|batch)[_ -]?id["']?\s*[:=]\s*["']?(?!null\b|none\b)[\w-]+""", re.I,
)


def is_credit_refusal(status: int | None, detail: str, *, job_started: bool = False) -> bool:
    """Use an explicit provider error only. An upstream paywall or uncertain delivery is inconclusive."""
    if job_started or status not in {None, 400, 402}:
        return False
    message = re.sub(r"""https?://[^\s<>"']+|\bfc-[\w-]+""", " ", detail, flags=re.I)
    normalized = " ".join(message.replace("_", " ").replace("-", " ").split())
    return bool(
        _CREDIT_REFUSAL.search(normalized)
        and not _OTHER_REFUSAL.search(normalized)
        and not _ACCEPTED_JOB.search(detail)
    )


def paid_work_started(envelope: object) -> bool:
    """A handed-out job, partial provider results, or target status rules out immediate replay."""
    if not isinstance(envelope, dict):
        return False
    if any(envelope.get(key) for key in (
        "jobId", "job_id", "scrapeId", "scrape_id", "crawlId", "crawl_id", "batchId", "batch_id",
        "markdown", "html", "rawHtml", "web", "images", "targetStatusCode", "pageStatusCode",
    )):
        return True
    if str(envelope.get("status", "")).casefold() in {"accepted", "queued", "pending", "scraping", "processing", "completed"}:
        return True
    if isinstance(envelope.get("data"), list) and envelope["data"]:
        return True
    metadata = envelope.get("metadata")
    if isinstance(metadata, dict) and any(metadata.get(key) for key in ("statusCode", "httpStatus", "sourceURL", "finalURL")):
        return True
    return any(paid_work_started(envelope.get(key)) for key in ("data", "error", "details"))


def response_parts(result: dict[str, Any]) -> Iterator[dict[str, Any] | str]:
    """MCP servers may return structured content or a JSON-encoded text block."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        yield structured
    for block in result.get("content") or ():
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            continue
        text = block["text"]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            yield text
        else:
            if isinstance(parsed, (dict, str)):
                yield parsed


def _error_fields(result: dict[str, Any]) -> Iterator[dict[str, Any] | str]:
    for data in response_parts(result):
        yield data
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            yield data["error"]


def error_detail(result: dict[str, Any]) -> str:
    parts = []
    for fields in _error_fields(result):
        if isinstance(fields, str):
            parts.append(fields)
            continue
        for key in ("error", "message", "detail", "code", "status", "statusCode"):
            value = fields.get(key)
            if isinstance(value, (str, int)):
                parts.append(str(value))
    return " ".join(parts)[:2_000]


def error_status(result: dict[str, Any]) -> int | None:
    statuses = []
    for data in _error_fields(result):
        if not isinstance(data, dict):
            continue
        for key in ("status", "statusCode", "http_status", "httpStatus"):
            value = data.get(key)
            if isinstance(value, int) and 100 <= value <= 599:
                statuses.append(value)
            elif isinstance(value, str) and len(value) == 3 and value.isascii() and value.isdigit() and 100 <= int(value) <= 599:
                statuses.append(int(value))
    statuses.extend(int(code) for code in re.findall(
        r"\b(?:HTTP|status(?:\s+code)?)\s*[:=]?\s*([1-5][0-9]{2})\b", error_detail(result), re.I,
    ))
    # A wrapper may report 400 while the provider reports e.g. 429. Avoid rotating on ambiguity.
    return next((code for code in statuses if code not in {400, 402}), statuses[0] if statuses else None)


def tool_credit_refusal(result: dict[str, Any]) -> bool:
    """Inspect provider failures only, never the contents of a successfully fetched article."""
    if result.get("isError") is not True:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            if structured.get("success") is not False:
                return False
        elif not any(isinstance(data, dict) and data.get("success") is False for data in response_parts(result)):
            return False
    return is_credit_refusal(error_status(result), error_detail(result), job_started=any(
        isinstance(data, dict) and (data.get("success") is True or paid_work_started(data))
        for data in response_parts(result)
    ))
