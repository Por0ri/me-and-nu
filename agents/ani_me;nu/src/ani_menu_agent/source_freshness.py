"""Check exposure windows using source dates and time-sensitive announcements."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from ani_menu_agent.models import Fact
from ani_menu_agent.policy import POLICY

KST = ZoneInfo("Asia/Seoul")


def publication_timestamp(value: str | None) -> str | None:
    """Accept dated ISO or RSS timestamps, never relative countdowns or times alone."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text)
    except ValueError:
        try:
            return parsedate_to_datetime(text).isoformat()
        except (TypeError, ValueError, OverflowError):
            return None
    return text

_EXPIRED_STATES = {
    "cancelled", "canceled", "ended", "expired", "sold_out", "soldout", "out_of_stock", "outofstock", "discontinued",
    "판매 종료", "예약 종료", "품절", "종료", "마감", "終了", "売り切れ",
}
_GOODS_ACTIVE_STATES = {
    "available",
    "in_stock",
    "on_sale",
    "selling",
    "currently_available",
    "preorder_open",
    "reservation_open",
    "販売中",
    "予約受付中",
    "在庫あり",
}
_GOODS_RELEASE_PERIOD = re.compile(
    r"(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月"
    r"[ \t]*(上旬|中旬|下旬)?[ \t]*(?:頃|ごろ)?"
)
_GOODS_RELEASE_PERIOD_PREFIX = re.compile(
    r"(?:発売(?:予定|日)?|販売開始(?:予定|日)?|リリース(?:予定|日)?)"
    r"\s*[:：]?\s*[-・]?\s*$"
)
_GOODS_RELEASE_PERIOD_SUFFIX = re.compile(
    r"\s*(?:発売(?:予定)?|販売開始(?:予定)?|リリース(?:予定)?)"
)
_GOODS_RELEASE_PERIOD_NEGATION = re.compile(
    r"(?:では(?:あり)?ません|ではない|ではなく|未定|延期|中止|撤回|取消|取り消し|"
    r"変更(?:済み?|(?:いた)?しました|(?:と|に)なりました|予定|(?:と|に)なる予定|"
    r"されました|されています|しています|(?:いた)?します))"
)
_GOODS_PERIOD_FACT_TYPES = {"product_sales", "sales_period", "preorder"}
_SCHEDULE_ALIASES = {
    "sale_start": ("sale_start", "sales_start", "availability_start", "campaign_start"),
    "sale_end": ("sale_end", "sales_end", "availability_end", "campaign_end"),
    "reservation_start": ("reservation_start", "preorder_start", "reservation_opens_at"),
    "reservation_end": ("reservation_end", "preorder_end"),
    "event_start": ("event_start", "event_start_at", "starts_on", "starts_at", "start_date"),
    "event_end": ("event_end", "event_end_at", "ends_on", "ends_at", "end_date"),
    "release_date": ("release_date", "発売日"),
    "premiere_date": ("premiere_date", "first_air_date", "broadcast_start"),
}
_SCHEDULE_FIELDS = {
    "event": ("event_start", "event_end", "reservation_start", "reservation_end"),
    "goods": ("sale_start", "sale_end", "reservation_start", "reservation_end", "release_date"),
    "news": ("premiere_date", "release_date"),
}
# Match a label immediately before or after its date. Dates attached to another
# activity (delivery, announcement, tickets) must not complete a partial range.
_LABELS = {
    "reservation": r"(?:予約(?:販売|受付)?|受注|受付|예약(?:\s*(?:판매|접수))?|사전\s*주문|pre[- ]?orders?|reservations?)",
    "sale": r"(?:販売|판매|sales?|on sale)",
    "event": r"(?:開催|会期|イベント|実施|配布|(?<!\w)期間(?=\s*[:：])|행사|전시|이벤트|팝업|개최|운영|event|exhibition)",
    "release_date": r"(?:発売|リリース|출시|발매|release(?:\s+date)?|launch)",
    "premiere_date": r"(?:初回放送|放送開始|放送スタート|初放送|放送|첫\s*(?:방송|방영)|방영\s*시작|방송\s*시작|방송|방영|premiere(?:s|\s+date)?|first\s+airs?)",
}
_KIND = (
    r"(?:期間|日程|日|予定|期間中|終了日?|締切日?|開始日?|スタート|기간|일정|예정|일|종료일?|마감일?|"
    r"시작일?|개시|개최|period|dates?|start(?:s)?(?:\s+date)?|end(?:s)?(?:\s+date)?|deadline|opens?|closes?)*"
)
_OPEN = re.compile(r"^\s*(?:[~～〜–—]|から|より|부터|에서부터|\bfrom\b)", re.I)
_ENDING = re.compile(r"終了|締切|마감|종료|deadline|ends?|closes?", re.I)
_UNTIL = re.compile(r"^\s*(?:まで|까지|\b(?:deadline|end)\b)", re.I)
# Campaign articles often put the store between the subject and the dates.
# Keep the subject in the quotation so a benefit's own period cannot supply it.
_CAMPAIGN = r"(?:コラボ(?:レーション)?キャンペーン|キャンペーン)"
_CAMPAIGN_BEFORE = re.compile(
    _CAMPAIGN + r"(?:全体)?(?:の)?(?:(?:開催|実施)?期間)?(?:は|が|を)?"
    r"[ \t、:：【】]*(?:[^。！？、\n\d]{1,48}(?:にて|で))?[ \t]*$"
)
_CAMPAIGN_AFTER = re.compile(
    r"^[ \t]*(?:まで|から|より)?(?:の期間(?:中)?)?[ \t、]*"
    + _CAMPAIGN + r"(?:を|が)?(?:開催|実施)"
)
_CAMPAIGN_ACTION = re.compile(r"^[ \t]*(?:まで|から|より)?(?:の期間)?[ \t、]*(?:開催|実施|開始|スタート)")
_CAMPAIGN_BOUND = re.compile(
    r"^[ \t]*(?:[~～〜]|から|より|まで)(?=$|[。、！？\n]|[ \t]*(?:終了日未定|なくなり次第終了))"
)
_SUBCAMPAIGN = re.compile(r"(?:購入|応募|抽選|プレゼント|特典|配布|スタンプ|第[一二三四五\d]+弾)(?:の)?$")
_GAME_RELEASE_BEFORE = re.compile(r"ゲーム(?:ソフト)?(?:[「『][^」』。\n]+[」』])?(?:の|は)?発売(?:予定)?日?[は:：\s]*$")
_GAME_RELEASE_AFTER = re.compile(r"^[ \t]*(?:に|より)?発売(?:予定)?の(?:新作)?ゲーム")
_YEAR_CONTEXT = re.compile(r"一昨年|再来年|昨年|去年|今年|本年|来年|(?:20\d{2}|令和(?:元|\d{1,2}))年")
_YEAR_OFFSET = {"一昨年": -2, "昨年": -1, "去年": -1, "今年": 0, "本年": 0, "来年": 1, "再来年": 2}
_PAST_PERIOD = re.compile(
    r"^(?:[ \t、]|まで|に|の期間)*(?:開催|実施|販売|終了)(?:されました|された|しました|した)"
)
_UPCOMING_PERIOD = re.compile(
    r"^(?:[ \t、]|まで|に|の期間)*(?:開催|実施|販売)(?:予定|します|されます)"
)
_RETROSPECTIVE = re.compile(r"振り返[るり]|回顧|当時の|회고|되돌아보|retrospective", re.I)
_ONE_DAY = r"(?:단\s*하루|하루만|당일만|1日限り|当日限定|one\s+day\s+only)"
_LABEL_BEFORE = {
    key: re.compile(label + r"[ \t]*" + _KIND + r"(?:은|는|이|가|は|が|を|on|from)?[\s【】\[\]:：]*$", re.I)
    for key, label in _LABELS.items()
}
_LABEL_AFTER = {
    key: re.compile(r"^[ \t]*(?:(?:から|より|まで|부터|까지|에|にて|に|で|[~～〜])\s*)?"
                    + rf"(?:{_ONE_DAY}(?:で|の)?\s*)?" + label + r"[ \t]*" + _KIND, re.I)
    for key, label in _LABELS.items()
}
_EVENT_YEAR = r"(?:[2２][0０]\d{2}|令和\s*(?:元|\d{1,2}))"
_YEAR_SEP = r"\s*[年년./-]\s*"
_MONTH_SEP = r"\s*[月월./-]\s*"
def _date_suffix(clock_group: str) -> str:
    return (
        r"(?:\s*[（(][月火水木金土日월화수목금토일](?:曜日|요일)?[)）])?"
        rf"(?:[ \tT]*(?P<{clock_group}_ampm>오전|오후|午前|午後)?[ \t]*"
        rf"(?P<{clock_group}>[01]?\d|2[0-3])[ \t]*(?:[:：](?P<{clock_group}_minute>[0-5]\d)"
        rf"(?::(?P<{clock_group}_second>[0-5]\d))?|[時시](?:[ \t]*(?P<{clock_group}_spoken>\d{{1,2}})[分분])?)"
        rf"(?:[ \t]*[(（]?(?P<{clock_group}_zone>JST|KST|UTC|GMT|Z|[+-](?:0\d|1[0-4]):[0-5]\d)[)）]?)?)?"
    )


_EVENT_DATES = re.compile(
    rf"(?<!\d)(?:(?P<year>{_EVENT_YEAR}){_YEAR_SEP})?"
    rf"(?P<month>\d{{1,2}}){_MONTH_SEP}(?P<day>\d{{1,2}})(?!\d)[ \t]*[日일]?{_date_suffix('hour')}"
    rf"(?:[ \t]*(?:[~～〜–—-]|から|부터)[ \t]*(?:(?P<end_year>{_EVENT_YEAR}){_YEAR_SEP})?"
    rf"(?:(?P<end_month>\d{{1,2}}){_MONTH_SEP}|(?P<next_month>翌月|다음\s*달)\s*)?(?P<end_day>\d{{1,2}})"
    rf"(?!\d|[ \t]*[年月년월]|[./]\d)[ \t]*[日일]?{_date_suffix('end_hour')})?"
)
_MONTH_ONLY = re.compile(r"(?<!\d)(20\d{2})\s*[年년]\s*(\d{1,2})\s*[月월](?!\s*\d)")
_IMMEDIATE_NEWS_ACTION = re.compile(
    r"\b(?:pre[- ]?registration|registration|reservation|pre[- ]?order|applications?)\b"
    r".{0,35}\b(?:starts?|begins?|opens?|available|launched?)\b|"
    r"\b(?:starts?|begins?|opens?)\b.{0,35}"
    r"\b(?:today|pre[- ]?registration|registration|reservation|pre[- ]?order)\b|"
    r"本日.{0,30}(?:事前登録|予約|受付|発売|販売|配信|放送).{0,15}(?:開始|受付)|"
    r"(?:事前登録|予約受付|受付|応募|販売|発売|配信|放送).{0,15}(?:開始|開始日|スタート)|"
    r"(?:오늘|오늘부터|금일부터).{0,30}(?:사전\s*등록|예약|접수|판매|출시|발매|방영|방송).{0,15}(?:시작|개시|오픈)|"
    r"(?:사전\s*등록|예약\s*접수|접수|응모|판매|출시|발매|방영|방송).{0,15}(?:시작|개시|오픈)",
    re.IGNORECASE,
)


def candidate_publication_date(candidate: dict) -> date | None:
    """Return the KST publication day to sort articles and resolve relative wording."""
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
    match = re.match(
        r"\s*(20\d{2})\s*(?:[./-]|년|年)\s*(\d{1,2})\s*"
        r"(?:[./-]|월|月)\s*(\d{1,2})\s*(?:일|日)?",
        title,
    )
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


_THIS_SEASON_RELEASE = re.compile(
    r"(?:今[春夏秋冬]|올\s*(?:봄|여름|가을|겨울)|this\s+(?:spring|summer|autumn|fall|winter))"
    r".{0,30}(?:配信|放送|公開|방영|공개|개봉|premiere|stream|release)|"
    r"(?:配信|放送|公開|방영|공개|개봉|premiere|stream|release).{0,30}"
    r"(?:今[春夏秋冬]|올\s*(?:봄|여름|가을|겨울)|this\s+(?:spring|summer|autumn|fall|winter))", re.I,
)
_TITLE_ANNOUNCEMENT = re.compile(
    r"タイトル(?:が|を|も)?(?:正式)?(?:決定|発表|解禁)|(?:放送|配信)(?:が)?決定|"
    r"(?:정식\s*)?제목(?:이|을)?\s*(?:공개|발표|확정)|"
    r"\btitle\s+(?:(?:has\s+been|was|is)\s+)?(?:revealed|confirmed|announced)\b", re.I,
)


def immediate_news_action_expiry(
    candidate: dict, *, today: date | None = None
) -> dict[str, str | None]:
    """Check dated release announcements and immediate calls to action.

    The publication date anchors relative wording on the source page. Ordinary
    evergreen news has no inferred expiry from its publication date.
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
    # Queue packages keep the article timestamp on the source, not on the package.
    sources = candidate.get("sources") or []
    article = sources[0] if sources and isinstance(sources[0], dict) else {}
    published = candidate_publication_date(article) or candidate_publication_date(candidate)
    reference = today or datetime.now(KST).date()
    headline = str(article.get("title") or candidate.get("title") or candidate.get("topic") or "")
    # 'This winter' can cross New Year. The announcement is certainly past by
    # spring of the following year; the exact season end need not be invented.
    previous_season = (published and published.year < reference.year
                       and reference >= date(published.year + 1, 4, 1)
                       and _THIS_SEASON_RELEASE.search(headline))
    archived_title = (published and (reference - published).days >= POLICY.default_content_validity_days
                      and _TITLE_ANNOUNCEMENT.search(headline))
    immediate = _IMMEDIATE_NEWS_ACTION.search(text)
    if not (previous_season or archived_title or immediate):
        return {"status": "not_applicable", "published_at": None, "evidence": None}
    for field, value in extract_source_schedule(candidate, "news").items():
        if value[:10] >= reference.isoformat()[:min(10, len(value))]:
            return {"status": "active", "published_at": published.isoformat() if published else None,
                    "evidence": f"upcoming {field}={value}"}
    date_label = re.compile(
        r"(?:release(?:\s+date)?|distribution(?:\s+date)?|launch(?:\s+date)?|airing(?:\s+date)?|"
        r"リリース|配信日|配信予定|発売日|発売予定|放送日|公開日|출시일|배포일|방영일|방송일)"
        r"[^\d]{0,24}(20\d{2})\s*(?:年|년|[-./])\s*(\d{1,2})\s*(?:月|월|[-./])"
        r"\s*(\d{1,2})?\s*(?:日|일)?",
        re.IGNORECASE,
    )
    past_evidence = None
    for match in date_label.finditer(headline + "\n" + text):
        year, month, day = (int(value) if value else None for value in match.groups())
        try:
            labeled_date = date(year, month, day or calendar.monthrange(year, month)[1])
        except (TypeError, ValueError):
            continue
        if labeled_date < reference:
            past_evidence = f"past dated release/action: {match.group(0).strip()}"
            continue
        return {
            "status": "active",
            "published_at": published.isoformat() if published else None,
            "evidence": f"upcoming dated release/action: {match.group(0).strip()}",
        }
    if past_evidence or (published and published < reference):
        return {"status": "expired", "published_at": published.isoformat() if published else None,
                "evidence": past_evidence or f"past news announcement: {headline}; published_at={published.isoformat()}"}
    if published is None:
        return {
            "status": "unresolved",
            "published_at": None,
            "evidence": (
                "time-sensitive start announcement has no source publication or action date"
            ),
        }
    return {
        "status": "active",
        "published_at": published.isoformat(),
        "evidence": f"time-sensitive start announcement; published_at={published.isoformat()}",
    }


_SALE_CLOCK = re.compile(
    r"(?<!\d)(?:(?P<year>\d{4})\s*[年년./-]\s*)?"
    r"(?P<month>\d{1,2})\s*[月월./-]\s*(?P<day>\d{1,2})\s*[日일]?"
    r"\s*(?:[(（][^()（）\n]{1,4}[)）]\s*)?(?P<hour>[01]?\d|2[0-3])"
    r"\s*(?:[:：]|時|시)\s*(?P<minute>[0-5]\d)\s*(?:分|분)?"
)


def _quoted_sale_cutoff(end_day: date, quote: str) -> datetime:
    """Use a clock tied to the end of the sale, never the opening or a nearby notice."""
    closing_times = set()
    for match in _SALE_CLOCK.finditer(quote):
        if int(match["month"]) != end_day.month or int(match["day"]) != end_day.day:
            continue
        if match["year"] and int(match["year"]) != end_day.year:
            continue
        prefix = quote[:match.start()].rstrip()
        suffix = quote[match.end():].lstrip()
        if not (
            prefix.endswith(("~", "～", "〜", "から", "부터", "–", "—"))
            or re.search(r"(?:販売終了|予約締切|마감|판매\s*종료)\s*[:：]?\s*$", prefix)
            or suffix.startswith(("まで", "까지", "마감", "종료"))
        ):
            continue
        closing_times.add(time(int(match["hour"]), int(match["minute"])))
    if len(closing_times) == 1:
        return datetime.combine(end_day, closing_times.pop(), tzinfo=KST)
    return datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=KST)


def goods_sale_deadline(facts: list[Fact]) -> datetime | None:
    """Return the source closing time used to reject already-ended sale announcements."""
    typed = next((fact for fact in facts if fact.fact_type == "source_schedule"), None)
    inputs = [typed] if typed else facts
    schedule, quotes, _, _ = _resolve_schedule({"facts": [f.model_dump(mode="json") for f in inputs]}, "goods")
    key = "sale_end" if any(k.startswith("sale_") for k in schedule) else "reservation_end"
    value = schedule.get(key)
    if not value or len(value) < 10:
        return None
    if len(value) == 10:
        return _quoted_sale_cutoff(date.fromisoformat(value), quotes[key])
    deadline = datetime.fromisoformat(value)
    return deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=KST)


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


def _calendar_year(token: str) -> int:
    if token.startswith("令和"):
        year = token.removeprefix("令和").strip()
        return 2018 + (1 if year == "元" else int(year))
    return int(token)


def _wall_clock(match: re.Match, group: str) -> str:
    if not match[group]:
        return ""
    hour = int(match[group])
    ampm = match[group + "_ampm"]
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("invalid 12-hour clock")
        hour = hour % 12 + (12 if ampm in {"오후", "午後"} else 0)
    minute = int(match[group + "_minute"] or match[group + "_spoken"] or "0")
    second = match[group + "_second"]
    clock = time(hour, minute, int(second or 0)).isoformat(timespec="seconds" if second else "minutes")
    zone = match[group + "_zone"] or ""
    offset = {"JST": "+09:00", "KST": "+09:00", "UTC": "+00:00", "GMT": "+00:00", "Z": "+00:00"}
    return "T" + clock + offset.get(zone, zone)


def schedule_end_precedes_start(start: str, end: str) -> bool:
    if "T" in start and "T" in end:
        begin, finish = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if (begin.tzinfo is None) == (finish.tzinfo is None):
            return finish < begin
    precision = min(10, len(start), len(end))
    return end[:precision] < start[:precision]


def _date_span(
    match: re.Match, published: date | None, infer_publication_year: bool
) -> tuple[str, str | None] | None:
    month = int(match["month"])
    end_month = month % 12 + 1 if match["next_month"] else int(match["end_month"] or month)
    if match["year"]:
        year = _calendar_year(match["year"])
    elif match["end_year"]:
        year = _calendar_year(match["end_year"]) - (month > end_month)
    elif published:
        before = re.split(r"[。！？\n]", match.string[:match.start()])[-1]
        context = _YEAR_CONTEXT.search(before)
        if context:
            # Only inherit a year directly qualifying the date or its subject.
            # An unrelated year elsewhere in the sentence makes inference unsafe.
            if not re.fullmatch(
                r"(?:の?(?:(?:コラボ(?:レーション)?)?キャンペーン|販売|予約|開催|会期)"
                r"(?:の)?(?:開催|実施)?(?:期間)?(?:は|が|を)?)?[ \t、:：]*", before[context.end():]
            ):
                return None
            token = context[0]
            year = (published.year + _YEAR_OFFSET[token] if token in _YEAR_OFFSET
                    else _calendar_year(token[:-1]))
        else:
            if not infer_publication_year:
                return None
            # Only cross a publication year at the December/January boundary.
            # A remote month could be a retrospective or a later announcement.
            delta = month - published.month
            if abs(delta) == 11:
                year = published.year + (1 if delta < 0 else -1)
            elif abs(delta) >= 6:
                return None
            else:
                year = published.year
            after = match.string[match.end():]
            last = year + (month > end_month), end_month, int(match["end_day"] or match["day"])
            article_day = published.year, published.month, published.day
            if _PAST_PERIOD.match(after) and (not match["end_day"] or last > article_day):
                return None
            if match["end_day"] and _UPCOMING_PERIOD.match(after) and last < article_day:
                return None
    else:
        return None
    try:
        start = date(year, month, int(match["day"])).isoformat() + _wall_clock(match, "hour")
        if not match["end_day"]:
            return start, None
        end_year = _calendar_year(match["end_year"]) if match["end_year"] else year + (month > end_month)
        end = date(end_year, end_month, int(match["end_day"])).isoformat() + _wall_clock(match, "end_hour")
    except ValueError:
        return None
    if schedule_end_precedes_start(start, end):
        return None
    return start, end


def _date_label(text: str, match: re.Match, fields: tuple[str, ...]):
    before = text[max(0, match.start() - 48):match.start()]
    after = text[match.end():match.end() + 48]
    # A preceding field label binds its value before a following label is considered.
    for is_prefix, patterns, fragment in ((True, _LABEL_BEFORE, before), (False, _LABEL_AFTER, after)):
        options = []
        for family, pattern in patterns.items():
            label = pattern.search(fragment)
            if label:
                options.append((label.end() - label.start(), family, label))
        if options:
            _, family, label = max(options, key=lambda item: item[0])
            if family in fields or family + "_start" in fields:
                # An ordinary episode airdate is not necessarily the show's first broadcast.
                if family == "premiere_date":
                    explicit = re.search(r"初|開始|スタート|첫|시작|premiere|first", label[0], re.I)
                    begins = not is_prefix and _OPEN.match(after)
                    if not explicit and not begins:
                        continue
                quote = label[0] + match[0] if is_prefix else match[0] + label[0]
                if is_prefix:
                    direction = _OPEN.match(after) or _UNTIL.match(after)
                    if direction:
                        quote += direction[0]
                return family, label[0], after, quote.strip()
            return None
    return None


def _campaign_date_label(text: str, match: re.Match):
    before = re.split(r"[。！？\n]", text[max(0, match.start() - 120):match.start()])[-1]
    after = text[match.end():match.end() + 48]
    subject = _CAMPAIGN_BEFORE.search(before)
    if subject and not _SUBCAMPAIGN.search(before[:subject.start()]):
        action = _CAMPAIGN_ACTION.match(after) or _CAMPAIGN_BOUND.match(after)
        # A campaign's article, product launch or application deadline can also
        # follow its name. Require its own period label, range or holding verb.
        standalone_range = match["end_day"] and re.match(r"^[ \t]*(?:[。！？\n]|$)", after)
        if "期間" in subject[0] or standalone_range or action:
            quote = subject[0] + match[0] + (action[0] if action else "")
            return "campaign", subject[0], after, quote.strip()
    action = _CAMPAIGN_AFTER.match(after)
    if action:
        return "campaign", action[0], after, (match[0] + action[0]).strip()
    return None


def _release_field(family: str, fields: tuple[str, ...], match: re.Match, after: str) -> str:
    if family == "release_date" and "sale_start" in fields and (
        _GAME_RELEASE_BEFORE.search(match.string[max(0, match.start() - 80):match.start()])
        or _GAME_RELEASE_AFTER.match(after)
    ):
        return "game_release_date"
    return family


def _text_schedule(
    text: str, fields: tuple[str, ...], published: date | None, infer_publication_year: bool
):
    # Follow-up pages may describe a second event with the same franchise.
    text = re.split(r"(?m)^\[연결된 (?:사이트 후보|상세 원문):", text, maxsplit=1)[0]
    for match in _EVENT_DATES.finditer(text):
        if "sale_start" in fields or "event_start" in fields:
            labeled = _campaign_date_label(text, match) or _date_label(text, match, fields)
        else:
            labeled = _date_label(text, match, fields)
        if not labeled:
            continue
        family, label, after, quote = labeled
        # A game mentioned in a goods campaign can have its own release date.
        # Resolve omitted years for period endpoints, not unrelated releases.
        anchor = published if family in {"campaign", "event", "sale", "reservation"} else None
        span = _date_span(match, anchor, infer_publication_year)
        if not span:
            if family == "campaign":
                # An unresolved campaign is still the subject of this period.
                yield "campaign_unresolved", None, quote
            continue
        start, end = span
        approximate = re.match(r"^\s*(?:頃|ごろ|경|무렵|전후)", after)
        if family in ("release_date", "premiere_date"):
            if not end and not approximate:
                yield _release_field(family, fields, match, after), start, quote
        elif end:
            yield family + "_start", start, quote
            if not approximate:
                yield family + "_end", end, quote
        elif approximate:
            continue
        elif _ENDING.search(label) or _UNTIL.match(after):
            yield family + "_end", start, quote
        else:
            # A lone date following a period label is not a known closing date.
            yield family + "_start", start, quote
            if re.match(r"^\s*(?:(?:에|に)\s*)?" + _ONE_DAY, after, re.I):
                yield family + "_end", start, quote
    for match in _MONTH_ONLY.finditer(text):
        labeled = _date_label(text, match, fields)
        if not labeled or not 1 <= int(match[2]) <= 12:
            continue
        family, label, after, quote = labeled
        month = f"{match[1]}-{int(match[2]):02}"
        if family in ("release_date", "premiere_date"):
            yield _release_field(family, fields, match, after), month, quote
        else:
            bound = "_end" if _ENDING.search(label) or _UNTIL.match(after) else "_start"
            yield family + bound, month, quote


def _source_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    month = re.fullmatch(r"(20\d{2})[-./年년](\d{1,2})[月월]?", value)
    if month and 1 <= int(month[2]) <= 12:
        return f"{month[1]}-{int(month[2]):02}"
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value)
        if parsed.microsecond:
            return parsed.isoformat()
        return parsed.isoformat(timespec="seconds" if parsed.second else "minutes")
    except ValueError:
        return None


def _consistent(value: str, precise: str) -> bool:
    if value == precise or precise.startswith(value):
        return True
    if "T" not in value or "T" not in precise:
        return False
    left, right = datetime.fromisoformat(value), datetime.fromisoformat(precise)
    if left.tzinfo is None or right.tzinfo is None:
        return left.replace(tzinfo=None) == right.replace(tzinfo=None)
    return left == right


def _resolve_schedule(candidate: dict, type_code: str) -> tuple[dict[str, str], dict[str, str], set[str], bool]:
    fields = _SCHEDULE_FIELDS.get(type_code, ())
    if not fields:
        return {}, {}, set(), False
    values: dict[str, dict[str, str]] = {}
    texts = [candidate.get(key) for key in ("title", "topic", "main_text", "text", "raw_text", "summary")]
    sources = candidate.get("sources") or []
    primary = sources[0] if sources and isinstance(sources[0], dict) else {}
    published = _as_date(publication_timestamp((primary or candidate).get("published_at")))
    # A retrospective's publication year says nothing about the year it covers.
    headline = str(primary.get("title") or candidate.get("title") or "")
    infer_publication_year = not _RETROSPECTIVE.search(headline)
    texts.append(primary.get("title"))
    # A previously synthesized sale_end on an archived capture may have come
    # from an open-ended range. Re-read its quotation instead of trusting the label.
    prior_interpretation = candidate.get("temporal_expiry") or {}
    if prior_interpretation.get("evidence") and candidate.get("sale_end"):
        texts.append(prior_interpretation["evidence"])
        details_list = [{k: v for k, v in candidate.items() if k != "sale_end"}]
    else:
        details_list = [candidate]
    stored_schedule = candidate.get("source_schedule")
    if isinstance(stored_schedule, dict):
        details_list.append(stored_schedule)
    for fact in candidate.get("facts") or ():
        if not isinstance(fact, dict):
            continue
        if primary.get("id") and fact.get("source_ids") and primary["id"] not in fact["source_ids"]:
            continue
        details = fact.get("details") or {}
        if isinstance(details, dict):
            if str(fact.get("id") or "").endswith("-sales-period") and fact.get("fact_type") == "sales_period":
                texts.append(details.get("source_quote"))
                continue
            details_list.append(details)
            texts.append(details.get("source_quote"))
            # The generic deadline key needs the fact's subject to identify its purpose.
            fact_type = fact.get("fact_type")
            deadline = _source_date(details.get("deadline"))
            if deadline and type_code == "goods" and fact_type in _GOODS_PERIOD_FACT_TYPES:
                field = "reservation_end" if fact_type == "preorder" else "sale_end"
                values.setdefault(field, {})[deadline] = details.get("source_quote") or f"deadline={deadline}"
        # A fact's paraphrase can mention another date or omit the source year.
        # When its quotation is present, use the original wording for the period.
        if fact.get("fact_type") != "source_schedule" and not (
            isinstance(details, dict) and details.get("source_quote")
        ):
            texts.append(fact.get("statement"))
    for details in details_list:
        for field in fields:
            for alias in _SCHEDULE_ALIASES[field]:
                canonical = _source_date(details.get(alias))
                if canonical:
                    quote = details.get("source_quote") or (details.get("event_date_evidence") if field.startswith("event_") else None)
                    values.setdefault(field, {})[canonical] = str(quote or f"{alias}={canonical}")
    extracted = [
        reading
        for text in dict.fromkeys(t for t in texts if isinstance(t, str) and t.strip())
        for reading in _text_schedule(text, fields, published, infer_publication_year)
    ]
    # When the article identifies the overall campaign, its sub-offers cannot
    # complete an open end or add their own start time to the parent period.
    campaign_fields = any(field.startswith("campaign_") for field, _, _ in extracted)
    period = "sale_" if type_code == "goods" else "event_"
    for field, canonical, quote in extracted:
        if field == "campaign_unresolved":
            continue
        if field.startswith("campaign_"):
            field = field.replace("campaign_", period, 1)
        elif field == "game_release_date":
            if campaign_fields:
                continue
            field = "release_date"
        elif campaign_fields and field.startswith(period):
            continue
        values.setdefault(field, {})[canonical] = quote
    schedule, quotes, conflicts = {}, {}, set()
    for field, readings in values.items():
        precise = max(readings, key=len)
        if all(_consistent(reading, precise) for reading in readings):
            schedule[field] = precise
            quotes[field] = readings[precise]
        else:
            conflicts.add(field)
    for family in ("event", "sale", "reservation"):
        begin, finish = family + "_start", family + "_end"
        if begin in schedule and finish in schedule and schedule_end_precedes_start(schedule[begin], schedule[finish]):
            for field in (begin, finish):
                schedule.pop(field)
                quotes.pop(field)
                conflicts.add(field)
    return schedule, quotes, conflicts, campaign_fields


def extract_source_schedule(candidate: dict, type_code: str) -> dict[str, str]:
    """Return only source-backed schedule fields, preserving their day/month/clock precision."""
    return _resolve_schedule(candidate, type_code)[0]


def event_period_from_text(
    text: str, *, published_at: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """Return the event days expected by the legacy page collector and a supporting excerpt."""
    dates, quotes, _, _ = _resolve_schedule({"text": text, "published_at": published_at}, "event")
    start = dates.get("event_start")
    end = dates.get("event_end")
    return start[:10] if start else None, end[:10] if end else None, quotes.get("event_end") or quotes.get("event_start")


def _expired_status(candidate: dict) -> bool:
    states = [candidate.get("temporal_status"), candidate.get("availability_status")]
    sources = candidate.get("sources") or []
    primary_id = sources[0].get("id") if sources and isinstance(sources[0], dict) else None
    for fact in candidate.get("facts") or ():
        if not isinstance(fact, dict) or (primary_id and primary_id not in (fact.get("source_ids") or [])):
            continue
        details = fact.get("details")
        if isinstance(details, dict):
            states.extend((details.get("temporal_status"), details.get("availability_status")))
    return any(str(value or "").strip().casefold() in _EXPIRED_STATES for value in states)


def _upcoming_goods_release_period(candidate: dict, reference: date, *, campaign: bool) -> dict[str, str | None] | None:
    """Keep explicitly labeled Japanese release periods at their source precision.

    Window bounds are comparison metadata, not announced release dates. Require
    the whole window to remain in the future; once it starts, the phrase alone
    cannot establish whether the product is still upcoming or available.
    """
    publication = candidate_publication_date(candidate)
    texts = [
        str(candidate.get(key) or "")
        for key in ("title", "summary", "text", "main_text", "raw_text")
    ]
    # The source-backed phrase survives capture -> package -> DB reload as a
    # provenance-tagged fact. Reparse that phrase, never persisted numeric bounds.
    facts = candidate.get("facts")
    sources = candidate.get("sources") or []
    primary = sources[0] if sources and isinstance(sources[0], dict) else {}
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict) or fact.get("fact_type") not in {
            "product_availability", "product_release_period"
        }:
            continue
        if primary.get("id") and primary["id"] not in (fact.get("source_ids") or ()):
            continue
        details = fact.get("details")
        if isinstance(details, dict) and details.get("evidence_kind") == "source_release_window":
            texts.append(str(details.get("source_quote") or details.get("release_text") or ""))

    periods: dict[tuple[date, date, str], str] = {}
    for text in texts:
        for match in _GOODS_RELEASE_PERIOD.finditer(text):
            if campaign and _release_field("release_date", _SCHEDULE_FIELDS["goods"], match, text[match.end():]) == "game_release_date":
                continue
            suffix = _GOODS_RELEASE_PERIOD_SUFFIX.match(text, match.end())
            prefix_text = text[max(0, match.start() - 32) : match.start()]
            prefix = _GOODS_RELEASE_PERIOD_PREFIX.search(prefix_text)
            if suffix:
                quote = text[match.start() : suffix.end()].strip()
            elif prefix and (
                match.end() == len(text) or text[match.end()] in "\r\n、。．,;；)）"
            ):
                quote = (prefix_text[prefix.start() :] + match.group(0)).strip()
            else:
                continue
            # Qualifiers such as "予定でしたが延期" invalidate the quoted
            # schedule even when an unchanged title still repeats it. Keep this
            # bounded to the same sentence/line so a normal next-line notice
            # that release dates may change does not invalidate the schedule.
            boundaries = ("\n", "\r", "。", "．", "!", "！", "?", "？")
            clause_start = max(text.rfind(mark, 0, match.start()) for mark in boundaries) + 1
            clause_ends = [text.find(mark, match.end()) for mark in boundaries]
            clause_end = min(
                [end for end in clause_ends if end >= 0] + [len(text), match.end() + 80]
            )
            clause = text[max(clause_start, match.start() - 80) : clause_end]
            if _GOODS_RELEASE_PERIOD_NEGATION.search(clause):
                return None
            year, month = int(match.group(1)), int(match.group(2))
            section = match.group(3)
            try:
                last_day = calendar.monthrange(year, month)[1]
                first, last, precision = {
                    None: (1, last_day, "month"),
                    "上旬": (1, 10, "early_month"),
                    "中旬": (11, 20, "mid_month"),
                    "下旬": (21, last_day, "late_month"),
                }[section]
                start, end = date(year, month, first), date(year, month, last)
            except ValueError:
                continue
            periods.setdefault((start, end, precision), quote)
    # Do not guess which of conflicting release periods applies to this product.
    if len(periods) != 1:
        return None
    (start, end, precision), quote = next(iter(periods.items()))
    if reference >= start or (publication and publication > start):
        return None
    evidence = f"upcoming product release period: {quote}"
    if precision == "month":
        evidence += "; precision=month"
    return {
        "status": "active",
        "end_date": None,
        "evidence": evidence,
        "availability_status": "upcoming_release",
        "release_date": None,
        "release_precision": precision,
        "release_window_start": start.isoformat(),
        "release_window_end": end.isoformat(),
        "release_text": quote,
    }


def _reported_goods_availability(candidate: dict) -> dict[str, str | None] | None:
    sources = candidate.get("sources") or []
    primary = sources[0] if sources and isinstance(sources[0], dict) else {}
    entries = [(candidate, candidate.get("availability_evidence"))]
    for fact in candidate.get("facts") or ():
        if not isinstance(fact, dict) or (primary.get("id") and primary["id"] not in (fact.get("source_ids") or ())):
            continue
        details = fact.get("details")
        if isinstance(details, dict):
            entries.append((details, details.get("source_quote")))
    for details, quote in entries:
        if not isinstance(quote, str) or not quote.strip():
            continue
        for field in ("availability_status", "sale_status", "sales_status", "stock_status"):
            status = re.sub(r"[\s-]+", "_", str(details.get(field) or "").strip().lower())
            if status in _GOODS_ACTIVE_STATES:
                return {"status": "active", "end_date": None, "evidence": quote.strip(),
                        "availability_status": status}
    return None


def expiry_evidence(
    candidate: dict, type_code: str, *, today: date | None = None, now: datetime | None = None
) -> dict[str, str | None]:
    """Eligibility reflects usable source dates; a start does not assert current availability."""
    if type_code not in {"event", "goods"}:
        return {"status": "not_applicable", "end_date": None, "evidence": None}
    # Callers supplying only a calendar day ask for a day-level decision.
    if now is None and today is None:
        now = datetime.now(KST)
    if now is not None:
        now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
        today = now.date()
    schedule, quotes, conflicts, campaign = _resolve_schedule(candidate, type_code)
    family = "event" if type_code == "event" else "sale"
    if type_code == "goods" and not any(key.startswith("sale_") for key in schedule.keys() | conflicts):
        if any(key.startswith("reservation_") for key in schedule.keys() | conflicts):
            family = "reservation"
    end_key, start_key = family + "_end", family + "_start"
    end = schedule.get(end_key)
    # A month-only end is not a known last day.
    day = end[:10] if end and len(end) >= 10 else None
    if _expired_status(candidate):
        return {"status": "expired", "end_date": day, "evidence": "explicit expired status"}
    if {start_key, end_key} & conflicts:
        return {"status": "unresolved", "end_date": None, "evidence": "conflicting source dates"}
    if type_code == "goods" and day and not schedule.get("sale_start") and not schedule.get("reservation_start"):
        # Two isolated, conflicting deadlines do not establish which one applies.
        other_end = schedule.get("reservation_end")
        if family == "sale" and other_end and day != other_end[:10]:
            return {"status": "unresolved", "end_date": None, "evidence": "conflicting explicit end dates"}
    if day:
        expired = day < today.isoformat()
        if now is not None and "T" in end:
            cutoff = datetime.fromisoformat(end)
            expired = now >= (cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=KST))
        return {"status": "expired" if expired else "active",
                "end_date": day, "evidence": quotes[end_key]}
    if end:
        return {"status": "expired" if end < today.isoformat()[:7] else "active",
                "end_date": None, "evidence": f"{end_key}={end}; precision=month; {quotes[end_key]}"}
    start = schedule.get(start_key)
    release = schedule.get("release_date")
    if type_code == "goods":
        reported = _reported_goods_availability(candidate)
        if reported:
            return reported
        period = _upcoming_goods_release_period(candidate, today, campaign=campaign)
        if period and not (release and len(release) >= 10):
            return period
    if start:
        return {"status": "active", "end_date": None, "evidence": f"{start_key}={start}; {quotes[start_key]}"}
    if release and release[:10] >= today.isoformat()[:min(10, len(release))]:
        if type_code == "goods" and len(release) == 7 and _GOODS_RELEASE_PERIOD.search(quotes["release_date"]):
            # The Japanese source-period parser above checks negation, conflicts and whether the whole window is upcoming.
            return {"status": "unresolved", "end_date": None, "evidence": None}
        precision = "month" if len(release) == 7 else "day"
        return {"status": "active", "end_date": None, "release_date": release, "availability_status": "upcoming_release",
                "evidence": f"release_date={release}; precision={precision}; no source-backed sale end date; {quotes['release_date']}"}
    report = candidate.get("completed_screening")
    if (
        type_code == "event" and candidate.get("source_id") == "anime_hack_events"
        and re.fullmatch(r"https://anime\.eiga\.com/news/\d+/?", str(candidate.get("url") or ""))
        and isinstance(report, dict)
    ):
        occurred = _as_date(report.get("occurred_on"))
        published = _as_date(candidate.get("published_at"))
        if occurred and published and occurred < published < today and report.get("quote"):
            return {
                "status": "expired", "end_date": None, "occurred_on": occurred.isoformat(),
                "evidence": f"Reported screening on {occurred.isoformat()}: {report['quote']}",
                "reason": "The source reports a completed screening.",
            }
    return {"status": "unresolved", "end_date": None, "evidence": None}


def candidate_is_stale(
    candidate: dict,
    type_code: str,
    *,
    today: date | None = None,
) -> bool:
    """Check news announcements, or the explicit end date/status of events and goods."""
    if type_code == "news":
        return immediate_news_action_expiry(candidate, today=today)["status"] == "expired"
    if type_code not in {"event", "goods"}:
        return False

    return expiry_evidence(candidate, type_code, today=today)["status"] == "expired"
