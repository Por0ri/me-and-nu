"""Content lifecycle checks based on explicit validity dates, not article age."""

from __future__ import annotations

import re
import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_EXPIRED_STATES = {
    "cancelled", "canceled", "ended", "expired", "sold_out",
    "판매 종료", "예약 종료", "품절", "종료", "마감", "終了", "売り切れ",
}
_EVENT_END_KEYS = ("event_end", "event_end_at", "ends_at", "end_date")
_GOODS_END_KEYS = (
    "sale_end",
    "sales_end",
    "preorder_end",
    "reservation_end",
    "availability_end",
    "campaign_end",
    "valid_until",
    "expires_at",
)
_PERIOD_LABELS = {
    "event": re.compile(
        r"(?:행사|이벤트|전시|팝업|운영)\s*기간|개최\s*기간|행사\s*일정|개최|"
        r"開催期間|開催日程|会期|イベント期間|期間\s*[:：]"
    ),
    "goods": re.compile(
        r"(?:판매|예약|주문|접수)\s*(?:기간|종료|마감)|판매\s*종료일|예약\s*마감일|"
        r"販売期間|販売終了日?|受注期間|予約期間|予約締切|受付期間|期間\s*[:：]"
    ),
}
_DATE_TOKEN = re.compile(
    r"(?:(20\d{2})\s*(?:[-./년年])\s*)?(\d{1,2})\s*(?:[-./월月])\s*"
    r"(\d{1,2})\s*(?:일|日)?"
)
_IMMEDIATE_NEWS_ACTION = re.compile(
    r"\b(?:pre[- ]?registration|registration|reservation|pre[- ]?order|applications?)\b"
    r".{0,35}\b(?:starts?|begins?|opens?|available|launched?)\b|"
    r"\b(?:starts?|begins?|opens?)\b.{0,35}\b(?:today|pre[- ]?registration|registration|reservation|pre[- ]?order)\b|"
    r"本日.{0,30}(?:事前登録|予約|受付|発売|販売|配信|放送).{0,15}(?:開始|受付)|"
    r"(?:事前登録|予約受付|受付|応募|販売|発売|配信|放送).{0,15}(?:開始|開始日|スタート)|"
    r"(?:오늘|오늘부터|금일부터).{0,30}(?:사전s*등록|예약|접수|판매|출시|발매|방영|방송).{0,15}(?:시작|개시|오픈)|"
    r"(?:사전s*등록|예약s*접수|접수|응모|판매|출시|발매|방영|방송).{0,15}(?:시작|개시|오픈)",
    re.IGNORECASE,
)


def candidate_publication_date(candidate: dict) -> date | None:
    """Return a publication date for display/sorting; it never determines expiry."""
    value = str(candidate.get("published_at") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
        else:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(KST)
            return parsed.date()

    title = str(candidate.get("title") or "")
    match = re.search(
        r"(?<!\d)(20\d{2})\s*(?:[./-]|년|年)\s*(\d{1,2})\s*"
        r"(?:[./-]|월|月)\s*(\d{1,2})\s*(?:일|日)?",
        title,
    )
    if not match:
        return None


def immediate_news_action_expiry(
    candidate: dict, *, today: date | None = None
) -> dict[str, str | None]:
    """Hold time-sensitive start announcements once their published day passed.

    This is deliberately limited to call-to-action headlines. It does not age out
    ordinary evergreen news or use publication date as an event/goods end date.
    """
    fact_text = "\n".join(
        str(fact.get("statement") or "")
        for fact in candidate.get("facts", [])
        if isinstance(fact, dict)
    )
    text = "\n".join(
        str(candidate.get(key) or "")
        for key in ("title", "topic", "summary", "text", "main_text", "raw_text")
    ) + "\n" + fact_text
    if not _IMMEDIATE_NEWS_ACTION.search(text):
        return {"status": "not_applicable", "published_at": None, "evidence": None}
    published = candidate_publication_date(candidate)
    reference = today or datetime.now(KST).date()
    if published and published < reference:
        return {
            "status": "expired",
            "published_at": published.isoformat(),
            "evidence": f"time-sensitive start announcement; published_at={published.isoformat()}",
        }
    date_label = re.compile(
        r"(?:release(?:\s+date)?|distribution(?:\s+date)?|launch(?:\s+date)?|airing(?:\s+date)?|"
        r"リリース|配信日|配信予定|発売日|発売予定|放送日|公開日|출시일|배포일|방영일|방송일)"
        r"[^\d]{0,24}(20\d{2})\s*(?:年|년|[-./])\s*(\d{1,2})\s*(?:月|월|[-./])"
        r"\s*(\d{1,2})?\s*(?:日|일)?",
        re.IGNORECASE,
    )
    for match in date_label.finditer(text):
        year, month, day = (int(value) if value else None for value in match.groups())
        try:
            labeled_date = date(year, month, day or calendar.monthrange(year, month)[1])
        except (TypeError, ValueError):
            continue
        if labeled_date < reference:
            return {
                "status": "expired",
                "published_at": published.isoformat() if published else None,
                "evidence": f"past dated release/action: {match.group(0).strip()}",
            }
        return {
            "status": "active",
            "published_at": published.isoformat() if published else None,
            "evidence": f"upcoming dated release/action: {match.group(0).strip()}",
        }
    if published is None:
        return {
            "status": "unresolved",
            "published_at": None,
            "evidence": "time-sensitive start announcement has no source publication or action date",
        }
    return {
        "status": "active",
        "published_at": published.isoformat(),
        "evidence": f"time-sensitive start announcement; published_at={published.isoformat()}",
    }
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _as_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(KST)
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _expiry_values(candidate: dict, type_code: str) -> list[object]:
    end_keys = _EVENT_END_KEYS if type_code == "event" else (*_GOODS_END_KEYS, "deadline")
    values = [candidate.get(key) for key in end_keys]
    if type_code == "event":
        values.extend(candidate.get(key) for key in ("valid_until", "expires_at"))

    facts = candidate.get("facts")
    if not isinstance(facts, list):
        return values
    accepted_fact_types = (
        {"event_schedule", "event_period", "event_date"}
        if type_code == "event"
        else {
            "campaign", "promotion", "product", "product_availability",
            "product_sales", "sales_period", "sales_status", "preorder",
        }
    )
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_type = str(fact.get("fact_type") or "").strip().lower()
        if fact_type not in accepted_fact_types:
            continue
        details = fact.get("details")
        if isinstance(details, dict):
            fact_end_keys = (
                (*end_keys, "deadline", "valid_until")
                if type_code == "goods"
                else end_keys
            )
            values.extend(details.get(key) for key in fact_end_keys)
    return values


def _labeled_expiry_date(text: str, type_code: str) -> tuple[date | None, str]:
    """Extract an end date only from a nearby, explicit period/end label.

    Publication dates and unlabelled dates elsewhere in an article are never
    treated as event or sales end dates. A year must be present in the labeled
    excerpt, either on the end date or inherited from the first date in a range.
    """
    label_pattern = _PERIOD_LABELS.get(type_code)
    if not label_pattern:
        return None, ""
    for label in label_pattern.finditer(text):
        start = max(0, label.start() - 140) if type_code == "event" else label.start()
        excerpt = text[start : label.start() + 220]
        parsed_dates: list[tuple[date, str]] = []
        inherited_year: int | None = None
        for match in _DATE_TOKEN.finditer(excerpt):
            year = int(match.group(1)) if match.group(1) else inherited_year
            if match.group(1):
                inherited_year = int(match.group(1))
            if year is None:
                continue
            try:
                parsed_dates.append(
                    (
                        date(year, int(match.group(2)), int(match.group(3))),
                        match.group(0),
                    )
                )
            except ValueError:
                continue
        if parsed_dates:
            last_date = parsed_dates[-1][0]
            first_date_match = next(_DATE_TOKEN.finditer(excerpt), None)
            tail = excerpt[first_date_match.end() :] if first_date_match else ""
            abbreviated_end = re.search(r"(?:~|～|〜|–|—)\s*(\d{1,2})\s*일", tail)
            if abbreviated_end:
                try:
                    parsed_dates.append(
                        (
                            date(last_date.year, last_date.month, int(abbreviated_end.group(1))),
                            abbreviated_end.group(0),
                        )
                    )
                except ValueError:
                    pass
        is_period_label = any(
            marker in label.group(0)
            for marker in ("期間", "会期", "日程", "期間", "開催期間")
        )
        if parsed_dates and (len(parsed_dates) >= 2 or is_period_label):
            end_date, matched_text = parsed_dates[-1]
            return end_date, excerpt[: min(len(excerpt), 220)]
    return None, ""


def expiry_evidence(candidate: dict, type_code: str) -> dict[str, str | None]:
    """Classify event/goods expiry from explicit dates/statuses, never article age."""
    if type_code not in {"event", "goods"}:
        return {"status": "not_applicable", "end_date": None, "evidence": None}

    end_keys = _EVENT_END_KEYS if type_code == "event" else (*_GOODS_END_KEYS, "deadline")
    explicit_dates: list[tuple[date, str]] = []
    for key in end_keys:
        parsed = _as_date(candidate.get(key))
        if parsed:
            explicit_dates.append((parsed, f"{key}={candidate.get(key)}"))

    facts = candidate.get("facts")
    if isinstance(facts, list):
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            details = fact.get("details")
            if not isinstance(details, dict):
                continue
            for key in end_keys:
                parsed = _as_date(details.get(key))
                if parsed:
                    explicit_dates.append(
                        (
                            parsed,
                            str(details.get("source_quote") or f"{key}={details.get(key)}"),
                        )
                    )

    distinct_dates = {item[0] for item in explicit_dates}
    if len(distinct_dates) > 1:
        return {"status": "unresolved", "end_date": None, "evidence": "conflicting explicit end dates"}
    if explicit_dates:
        parsed, evidence = explicit_dates[0]
        return {
            "status": (
                "expired"
                if parsed < datetime.now(KST).date() or candidate_is_stale(candidate, type_code)
                else "active"
            ),
            "end_date": parsed.isoformat(),
            "evidence": evidence,
        }
    if candidate_is_stale(candidate, type_code):
        return {"status": "expired", "end_date": None, "evidence": "explicit expired status"}

    fact_text = "\n".join(
        str(fact.get("statement") or "")
        for fact in facts
        if isinstance(facts, list) and isinstance(fact, dict)
    ) if isinstance(facts, list) else ""
    text = "\n".join(
        str(candidate.get(key) or "")
        for key in ("text", "main_text", "raw_text", "summary", "title")
    ) + "\n" + fact_text
    parsed, quote = _labeled_expiry_date(text, type_code)
    if parsed:
        return {
            "status": "expired" if parsed < datetime.now(KST).date() else "active",
            "end_date": parsed.isoformat(),
            "evidence": quote,
        }
    return {"status": "unresolved", "end_date": None, "evidence": None}


def candidate_is_stale(
    candidate: dict,
    type_code: str,
    *,
    today: date | None = None,
) -> bool:
    """Only mark event/goods candidates expired from an explicit end date or status."""
    if type_code not in {"event", "goods"}:
        return False

    reference = today or datetime.now(KST).date()
    states = [candidate.get("temporal_status"), candidate.get("availability_status")]
    facts = candidate.get("facts")
    if isinstance(facts, list):
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            details = fact.get("details")
            if isinstance(details, dict):
                states.extend(
                    (details.get("temporal_status"), details.get("availability_status"))
                )
    if any(str(value or "").strip().casefold() in _EXPIRED_STATES for value in states):
        return True

    expired_dates = [
        parsed
        for value in _expiry_values(candidate, type_code)
        if (parsed := _as_date(value)) is not None
    ]
    return any(expiry < reference for expiry in expired_dates)
