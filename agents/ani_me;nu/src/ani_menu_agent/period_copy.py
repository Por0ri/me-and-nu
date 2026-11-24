"""Reader-facing terms reported by the source of an event or goods story."""

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ani_menu_agent.image_selection import goods_reference_image_label
from ani_menu_agent.models import ContentPackage
from ani_menu_agent.sources.anime_schedule import profile_slug


PERIOD_COPY_POLICY = (
    "For event and goods stories, reported_terms contains excerpts from the linked source. "
    "An explicit announcement is usable as reported even if no independent fact-check was performed. "
    "Give the actual schedule where useful, including times if stated. Keep the original currency, "
    "unit, tax and extra-fee conditions when giving a price. A price need not appear on every story. "
    "Report the announced plan directly in Korean; avoid filler like ‘기사에 따르면’. The source "
    "footer supplies attribution. With only a start date, describe the announced start in the correct "
    "tense. Claim ongoing sales, current stock or opening hours only when the source supports them."
)

_TERM_LABELS = {
    "sale_period": re.compile(r"販売期間|판매\s*기간|販売終了日"),
    "event_period": re.compile(r"開催期間|開催日程|会期|행사\s*(?:기간|일정)|개최\s*기간"),
    "price": re.compile(r"販売価格|価格(?:は|[：:])|판매\s*가격|상품\s*가격"),
    "admission": re.compile(r"入場料|입장료"),
}
_DATE_IN_LINE = re.compile(r"\d{1,2}\s*[月월]\s*\d{1,2}|\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_PRICE_IN_LINE = re.compile(r"\d[\d,]*\s*(?:円|엔|원)|無料|무료")
_NEXT_FIELD = re.compile("|".join(pattern.pattern for pattern in _TERM_LABELS.values()))
_UNKNOWN_TERM = re.compile(
    r"(?:^|[.!?。！？]\s*|[,，]\s*)(?:(?:다만|단|참고로|현재|아직|그리고|하지만)\s+)*"
    r"(?P<label>판매\s*기간|행사\s*기간|판매\s*가격|가격|입장료)"
    r"(?:과\s*진행\s*여부)?\s*(?:은|는|이|가|을|를)?\s*"
    r"(?:(?:여기서|아직|현재|따로|정확히)\s*)*"
    r"(?:단정하(?:지\s*않|기\s*어렵)|미(?:정|확인|공개)|"
    r"(?:알려지|공개되|확인되|나오)지\s*않|확인할\s*수\s*없|알\s*수\s*없)"
)

EPISODE_NEWS_POLICY = (
    "An episode_release is an attributed AnimeSchedule raw-feed announcement. Report the named "
    "season and episode in Korean. For the second card, explain the edition the release notice "
    "actually covers using episode_edition, instead of repeating the headline. If included, "
    "feed_posted_at is when the RSS notice appeared; label it as the feed posting time in the "
    "detail article. It is not an exact broadcast time. A brief of about 180–260 Korean characters "
    "is enough for a brief with additional factual material. The existing footer credits AnimeSchedule. "
    "Tell readers what happened, omit unknown details and keep "
    "verification workflow out of reader copy. Do not infer a "
    "streaming service, availability in Korea, subtitles, a plot, or any episode scene."
)


def _episode_terms(package: ContentPackage) -> list[dict]:
    for source in package.sources:
        if (source.id != "anime_schedule_rss" or source.role != "reference"
                or source.discovered_via_source_id != source.id or not profile_slug(str(source.url))):
            continue
        headline = source.title.strip()
        match = re.fullmatch(r"Episode (\d+(?:\.\d+)?) of (.+) is out!", headline, re.I)
        if not match or source.discovery_title != headline:
            continue
        episode, series = match.groups()
        if source.feed_entry_guid:
            identified = re.fullmatch(r"(.+) Episode (\d+(?:\.\d+)?)", source.feed_entry_guid, re.I)
            if identified and (identified[1].casefold() != series.casefold() or identified[2] != episode):
                continue
        for fact in package.facts:
            if (fact.fact_type != "source_page_capture" or fact.source_ids != [source.id]
                    or fact.details.get("captured_title") != headline
                    or fact.details.get("captured_url") != str(source.url)):
                continue
            lines = [line.strip() for line in fact.statement.splitlines() if line.strip()]
            if not lines or lines[0] != headline:
                continue
            origin = {
                "fact_id": fact.id, "source_ids": [source.id], "publisher": source.publisher,
                "feed_entry_guid": source.feed_entry_guid, "linked_profile_url": str(source.url),
                "feed_url": fact.details.get("discovery_feed_url") or "https://animeschedule.net/jpnrss.xml",
                "verified": fact.verified,
            }
            notice = {"term": "episode_release", "excerpt": headline, "episode": episode,
                      "series": series, **origin}
            posted = fact.details.get("published_at")
            if posted and posted == source.published_at:
                try:
                    moment = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    moment = None
                if moment is not None and moment.tzinfo is not None:
                    notice.update(feed_posted_at=posted, feed_posted_at_kst=moment.astimezone(ZoneInfo("Asia/Seoul")).isoformat())
            native = f"Episode {episode} of {series} has been released natively!"
            terms = [notice]
            edition_quote = next((line for line in lines[1:] if line.casefold() == native.casefold()), None)
            if edition_quote:
                terms.append({"term": "episode_edition", "edition": "original_language",
                              "excerpt": edition_quote, **origin})
            return terms
    return []


def episode_evidence_missing(package: ContentPackage) -> bool:
    """Catch episode-only packages which would merely repeat a profile/episode title across cards."""
    if package.content_type != "news" or {source.id for source in package.sources} != {"anime_schedule_rss"}:
        return False
    terms = _episode_terms(package)
    if not terms:
        return True
    if any(term["term"] == "episode_edition" for term in terms):
        return False
    notice = terms[0]
    return not any(
        fact.verified and fact.fact_type != "source_page_capture" and "anime_schedule_rss" in fact.source_ids
        and notice["feed_entry_guid"] and fact.details.get("feed_entry_guid") == notice["feed_entry_guid"]
        and fact.statement.strip() != notice["excerpt"] for fact in package.facts
    )


def authoring_policy(package: ContentPackage, terms: list[dict]) -> str:
    if any(term["term"] == "episode_release" for term in terms):
        return "\n" + EPISODE_NEWS_POLICY
    if package.content_type == "goods" and any(goods_reference_image_label(img, package) for img in package.images):
        return (
            "\n" + PERIOD_COPY_POLICY
            + " Images labelled 작품 포스터 or 작품 일러스트 illustrate the anime. Product features, "
              "poses, clothing and accessories must come from the product article, not the illustration. "
              "The app adds the image label and any verified manufacturer link."
        )
    return "\n" + PERIOD_COPY_POLICY if package.content_type in {"goods", "event"} else ""


def bulletin_terms(package: ContentPackage) -> list[dict]:
    """Only the two-line raw episode notice qualifies for deterministic short copy."""
    if package.content_type != "news" or len(package.sources) != 1 or len(package.facts) != 1:
        return []
    terms = _episode_terms(package)
    lines = [line.strip() for line in package.facts[0].statement.splitlines() if line.strip()]
    return terms if len(terms) == 2 and lines == [t["excerpt"] for t in terms] else []


@dataclass(frozen=True)
class EpisodeBulletin:
    title: str
    episode_label: str
    detail: str
    fact_id: str
    source_id: str


def compose_episode_bulletin(package: ContentPackage, work_rows: list[dict]) -> EpisodeBulletin | None:
    terms = bulletin_terms(package)
    if not terms or len(package.menu_work_ids) != 1:
        return None
    notice = terms[0]
    row = next((work for work in work_rows if int(work["menu_work_id"]) == package.menu_work_ids[0]), {})
    title = str(row.get("title_ko") or "").strip()
    if not title:
        return None
    series = notice["series"]
    suffix = re.search(r"\s+(?:(\d+)(?:st|nd|rd|th)\s+Season|Season\s+(\d+))$", series, re.I)
    base = series[:suffix.start()] if suffix else series
    aliases = {str(row.get(key) or "").strip().casefold()
               for key in ("title_ko", "title_romaji", "title_english", "title_native")}
    if not {series.casefold(), base.casefold()}.intersection(aliases):
        return None
    season = (suffix[1] or suffix[2]) if suffix else None
    season_label = f"{season}기 " if season and not title.endswith(f"{season}기") else ""
    label = f"{season_label}{notice['episode']}화"
    detail = f"〈{title}〉 {label}가 원어판으로 공개됐어."
    if posted := notice.get("feed_posted_at_kst"):
        stamp = datetime.fromisoformat(posted)
        clock = f"{stamp.hour}시 {stamp.minute}분이야" if stamp.minute else f"{stamp.hour}시야"
        detail += f" 공개 알림 게시 시각은 한국 시간 {stamp.year}년 {stamp.month}월 {stamp.day}일 {clock}."
    return EpisodeBulletin(title, label, detail, notice["fact_id"], notice["source_ids"][0])


def reported_terms(package: ContentPackage) -> list[dict]:
    if package.content_type == "news":
        return _episode_terms(package)
    if package.content_type not in {"event", "goods"}:
        return []
    source_ids = {s.id for s in package.sources if s.role not in {"trend", "reference"}}
    period = "sale_period" if package.content_type == "goods" else "event_period"
    keys = (period, "price" if period == "sale_period" else "admission")
    terms = []
    seen = set()
    for fact in sorted(package.facts, key=lambda item: item.fact_type == "source_page_capture"):
        linked = [source for source in fact.source_ids if source in source_ids]
        if not linked:
            continue
        excerpt = fact.details.get("source_quote") or (
            fact.statement if fact.verified or fact.fact_type == "source_page_capture" else ""
        )
        for line in re.split(r"[\n。]", str(excerpt)):
            for key in keys:
                match = _TERM_LABELS[key].search(line)
                if not match:
                    continue
                # Keep each labelled term with its own unit, time or tax text.
                next_field = _NEXT_FIELD.search(line, match.end())
                stop = next_field.start() if next_field else len(line)
                value = line[match.start():min(stop, match.start() + 180)].strip()
                number = _DATE_IN_LINE if key == period else _PRICE_IN_LINE
                # A shipping/booking fee after an undisclosed product price does not establish the price.
                numeric_part = re.split(r"送料|手数料|배송비|수수료|※", value, maxsplit=1)[0]
                identity = (key, tuple(linked), value)
                if number.search(numeric_part) and identity not in seen:
                    term = {"term": key, "excerpt": value, "fact_id": fact.id, "source_ids": linked}
                    if key == period and fact.fact_type in {"sales_period", "event_period"}:
                        end = fact.details.get("sale_end" if key == "sale_period" else "event_end")
                        if end:
                            term["ends_on"] = end
                    terms.append(term)
                    seen.add(identity)
    return terms


def denied_reported_terms(text: str, terms: list[dict]) -> list[dict]:
    labels = {"판매기간": "sale_period", "행사기간": "event_period", "판매가격": "price",
              "가격": "price", "입장료": "admission"}
    denied = {labels[re.sub(r"\s", "", match["label"])] for match in _UNKNOWN_TERM.finditer(text)}
    return [entry for entry in terms if entry["term"] in denied]
