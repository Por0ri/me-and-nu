"""LLM 비용 없이 최신 방영·신작 후보를 찾는 수집기."""

import json
import re
import time
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from hashlib import sha1
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.models import CONTENT_TYPE_LABELS
from ani_menu_agent.classification import infer_toei_press_type
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import _get_html_with_client
from ani_menu_agent.sources.registry import load_source_registry
from ani_menu_agent.work_catalog import load_work_catalog, resolve_title_work_ids


class DiscoveryItem(BaseModel):
    id: str
    title: str
    url: HttpUrl
    source_id: str
    content_types: list[str] = Field(default_factory=list)
    published_at: str | None = None
    summary: str = ""
    status: str = "discovery_only"
    region: str = "global"
    source_role: str = "discovery"
    is_community_post: bool = False
    community_posts: list[dict[str, str]] = Field(default_factory=list)
    # Onnada birthday records are reusable calendar references, not publication dates.
    character_name: str | None = None
    linked_work_title: str | None = None
    birthday_mmdd: str | None = Field(
        default=None,
        pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$",
    )
    linked_work_ids: list[int] = Field(default_factory=list)


MAX_LISTING_CANDIDATES_PER_PAGE = 200
MAX_MASTODON_THREAD_LOOKUPS = 5


def _listing_page_number(url: str) -> int:
    parsed = urlparse(url)
    for key in ("page", "paged", "page_no", "p"):
        value = parse_qs(parsed.query).get(key, [None])[0]
        if value and value.isdigit():
            return int(value)
    match = re.search(r"/page/(\d+)/?$", parsed.path)
    return int(match.group(1)) if match else 1


def _is_listing_page_url(candidate_url: str, source: Source) -> bool:
    candidate = urlparse(candidate_url)
    source_url = urlparse(str(source.url))
    if (
        candidate.scheme != "https"
        or (candidate.hostname or "").lower() != (source_url.hostname or "").lower()
        or candidate.username
        or candidate.password
    ):
        return False
    source_path = source_url.path.rstrip("/")
    candidate_path = candidate.path.rstrip("/")
    query_page = any(
        (value := parse_qs(candidate.query).get(key, [None])[0]) and value.isdigit()
        for key in ("page", "paged", "page_no", "p")
    )
    path_page = bool(
        re.fullmatch(re.escape(source_path) + r"/page/\d+", candidate_path)
    )
    return candidate_path == source_path and query_page or path_page


def _next_listing_url(html: str, current_url: str, source: Source) -> str | None:
    """Find a same-listing next-page link without guessing arbitrary URLs."""
    soup = BeautifulSoup(html, "html.parser")
    current_page = _listing_page_number(current_url)
    expected_page = current_page + 1
    explicit_next: list[str] = []
    numbered_next: list[str] = []
    next_text = re.compile(
        r"\b(?:next|older\s+posts?|more\s+posts?)\b|次へ|次のページ|もっと見る|さらに読む|下一页",
        re.IGNORECASE,
    )

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(current_url, href)
        if not _is_listing_page_url(absolute, source):
            continue
        if _listing_page_number(absolute) != expected_page:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        label = " ".join(
            [
                text,
                str(anchor.get("aria-label") or ""),
                " ".join(str(value) for value in anchor.get("class", [])),
                str(anchor.parent.get("class") if anchor.parent else ""),
            ]
        )
        if anchor.get("rel") and "next" in anchor.get("rel", []):
            explicit_next.append(absolute)
        elif next_text.search(label):
            explicit_next.append(absolute)
        else:
            parent = anchor
            in_pagination = False
            for _ in range(4):
                if parent is None:
                    break
                marker = " ".join(
                    [str(parent.get("id") or ""), " ".join(parent.get("class", []))]
                ).casefold()
                if parent.name == "nav" or any(
                    term in marker for term in ("pagination", "pager", "paging", "page-links")
                ):
                    in_pagination = True
                    break
                parent = parent.parent
            if in_pagination:
                numbered_next.append(absolute)

    candidates = explicit_next or numbered_next
    return candidates[0] if candidates else None


def _listing_card_published_at(anchor) -> str | None:
    """Read a publication date only from the anchor's nearby article card."""
    card = anchor.find_parent("li") or anchor.find_parent("article")
    if card is None:
        parent = anchor.parent
        for _ in range(4):
            if parent is None:
                break
            marker = " ".join(
                [str(parent.get("id") or ""), " ".join(parent.get("class", []))]
            ).casefold()
            if any(term in marker for term in ("post", "entry", "article", "news", "card")):
                card = parent
                break
            parent = parent.parent
    if card is None:
        return None
    date_node = card.select_one(
        'time[datetime], time, [itemprop="datePublished"], '
        '[property="article:published_time"], .article_date, '
        '.boxDate, [class*="published"], [class*="date"]'
    )
    if date_node is None:
        return None
    value = str(date_node.get("datetime") or date_node.get("content") or "").strip()
    if not value:
        value = date_node.get_text(" ", strip=True)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        match = re.search(
            r"(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5?"
            r"|(20\d{2})[./-](\d{1,2})[./-](\d{1,2})",
            value,
        )
        if match:
            parts = match.groups()[:3] if match.group(1) else match.groups()[3:]
            try:
                return datetime(*(int(part) for part in parts)).date().isoformat()
            except (TypeError, ValueError):
                return None
        for date_format in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                continue
        return None


def _publication_order(value: str | None) -> float:
    """Return a newest-first sort key; undated candidates follow dated ones."""
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return float("inf")
    return -parsed.timestamp()


def _listing_anchor_title(anchor, source: Source) -> str:
    """Prefer the headline in a listing card over its date and teaser text."""
    card = anchor.find_parent("li") or anchor.find_parent("article")
    if card is None:
        parent = anchor.parent
        for _ in range(5):
            if parent is None:
                break
            marker = " ".join(
                [str(parent.get("id") or ""), " ".join(parent.get("class", []))]
            ).casefold()
            if any(term in marker for term in ("item", "card", "article", "entry", "post")):
                card = parent
                break
            parent = parent.parent
    if card is None:
        card = anchor.find_parent("li")

    title_node = anchor.select_one("h1, h2, h3, h4")
    if title_node is None and card is not None:
        title_node = card.select_one("h1, h2, h3, h4")
    if title_node is None:
        title_node = anchor.select_one("[class*='title'], [class*='name']")
    if title_node is None and card is not None:
        title_node = card.select_one("[class*='title'], [class*='name']")
    if title_node is not None:
        title = " ".join(title_node.get_text(" ", strip=True).split())
    else:
        title = " ".join(anchor.get_text(" ", strip=True).split())

    if not title:
        image = anchor.find("img")
        title = " ".join(
            part.strip()
            for part in (
                str(anchor.get("aria-label") or ""),
                str(anchor.get("title") or ""),
                str(anchor.get("data-title") or ""),
                str(image.get("alt") or "") if image else "",
                str(image.get("title") or "") if image else "",
            )
            if part and part.strip()
        )
    title = re.sub(r"\s+", " ", title)
    if source.id == "natsume_official":
        # Natsume's card wrappers contain a site-wide broadcast banner before
        # the linked article headline; the link text itself is the article title.
        title = " ".join(anchor.get_text(" ", strip=True).split())

    if source.id == "src_www_toei_anim_co_jp":
        title = re.sub(
            r"^\s*20\d{2}(?:\u5e74\d{1,2}\u6708\d{1,2}\u65e5?|[./-]\d{1,2}[./-]\d{1,2})\s*"
            r"(?:\u30cb\u30e5\u30fc\u30b9|NEWS)?\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
    if source.id == "src_kusuriyanohitorigoto_jp":
        title = re.sub(r"^\s*20\d{2}[./年-]\d{1,2}[./月-]\d{1,2}\s*", "", title)
    if source.id in {
        "jujutsu_kaisen_official",
        "natsume_official",
        "haikyu_official",
        "spy_family_official",
        "my_hero_academia_official",
    }:
        title = re.sub(r"^\s*20\d{2}[./年-]\d{1,2}[./月-]\d{1,2}日?\s*", "", title)
    if source.id == "haikyu_official":
        title = re.sub(r"\s+20\d{2}\.\d{2}\.\d{2}\s*$", "", title)
    if source.id == "src_dfx_theshop_jp":
        title = re.sub(r"\s+JPY\s+[\d,]+.*$", "", title, flags=re.IGNORECASE)
    if source.id == "eeo_events":
        title = re.sub(r"\s+20\d{4}\.\d{2}\.\d{2}\s*$", "", title)
    return title.strip()


def _recal_music_records(soup: BeautifulSoup) -> list[dict]:
    """Read the complete concert list from RE:CAL's serialized Next.js payload."""
    records: dict[str, dict] = {}
    for script in soup.select("script"):
        script_text = script.string or script.get_text()
        if not script_text or "music:concert:" not in script_text:
            continue
        chunks = re.findall(
            r"self\.__next_f\.push\(\[1,(\"(?:\\\\.|[^\"\\\\])*\")\]\)",
            script_text,
        )
        for encoded_chunk in chunks:
            try:
                payload_text = json.loads(encoded_chunk)
            except json.JSONDecodeError:
                continue
            for match in re.finditer(r'\{"key":"music:concert:', payload_text):
                start = match.start()
                depth = 0
                quote = False
                escaped = False
                end = None
                for index in range(start, len(payload_text)):
                    char = payload_text[index]
                    if quote:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            quote = False
                        continue
                    if char == '"':
                        quote = True
                    elif char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            end = index + 1
                            break
                if end is None:
                    continue
                try:
                    item = json.loads(payload_text[start:end])
                except json.JSONDecodeError:
                    continue
                item_id = str(item.get("id") or item.get("key") or "")
                href = str(item.get("href") or "")
                if item_id and href.startswith("/e/music/") and item.get("title"):
                    records.setdefault(item_id, item)
    return list(records.values())


def _item_id(source_id: str, value: str) -> str:
    return f"{source_id}-{sha1(value.encode('utf-8')).hexdigest()[:12]}"


@lru_cache(maxsize=1)
def _source_type_map() -> dict[str, list[str]]:
    registry = load_source_registry()
    return {source.id: [item.value for item in source.content_types] for source in registry.sources}


def _source_type_codes(source_id: str) -> list[str]:
    return _source_type_map().get(source_id, [])


def _inferred_candidate_types(item: DiscoveryItem, source: Source) -> set[str]:
    """Narrow broad source capabilities to the topic actually signaled by a candidate."""
    # Classify the article topic from its headline, not the listing route.
    # Broad paths such as /news-event otherwise make every article look like an event.
    title = item.title.casefold()
    available = set(item.content_types)
    inferred: set[str] = set()
    patterns = {
        "event": (
            r"event|pop[ -]?up|exhibition|collab(?:oration)? caf[eé]|cafe|"
            r"팝업|전시|전시회|展|행사|페어|티켓|관람|콜라보(?:레이션)? ?카페|오프라인 ?스토어|"
            r"フェア|開催|展覧|展示|催事|公演|舞台|コラボカフェ|イベント"
        ),
        "goods": (
            r"goods|merch|figure|product|굿즈|상품|피규어|완구|장난감|인형|블라인드박스|구매특전|한정판|"
            r"グッズ|フィギュア|一番くじ|くじ|ぬい|"
            r"アクスタ|アクリルスタンド|発売|予約受付|販売|商品"
        ),
        "music": (
            r"music|soundtrack|\bost\b|theme song|opening|ending|discography|"
            r"主題歌|サントラ|音楽|楽曲|挿入歌|オープニング|エンディング|アルバム|シングル"
        ),
        "people": (
            r"interview|voice actor|cast|staff|director|creator|artist|"
            r"声優|キャスト|出演者|スタッフ|監督|作者|作家|対談|インタビュー|コメント"
        ),
        "review": r"review|レビュー|批評|感想",
        "curation": (
            r"poll|ranking|ranked|ratings?|ランキング|投票|アンケート|"
            r"評価|おすすめ|top anime"
        ),
        "character": r"character profile|birthday|誕生日|キャラクター紹介|人物紹介|キャラクター",
        "guide": (
            r"episode|story|season|introduction|guide|arc|"
            r"各話|あらすじ|ストーリー|シーズン|編|作品紹介"
        ),
    }
    for type_code, pattern in patterns.items():
        if re.search(pattern, title):
            inferred.add(type_code)
    # Explicit localized signals keep Korean event and fandom headlines routable
    # even when a publisher's broader taxonomy is shared by news and commerce.
    if re.search(
        r"\ud31d\uc5c5|\uc804\uc2dc|\uc804\uc2dc\ud68c|\ud589\uc0ac|\ud398\uc5b4|"
        r"\ud2f0\ucf13|\uad00\ub78c|\ucf5c\ub77c\ubcf4\s*\uce74\ud398|\uc624\ud504\ub77c\uc778\s*\uc2a4\ud1a0\uc5b4|"
        r"\u958b\u50ac|\u5c55\u89a7|\u5c55\u793a|\u50ac\u4e8b|\u516c\u6f14|\u821e\u53f0|\u30a4\u30d9\u30f3\u30c8",
        title,
    ):
        inferred.add("event")
    if re.search(
        r"fan\s+(?:debate|discussion|theory|prediction)|fans?\s+(?:react|debate)|"
        r"meme|fandom|behind[ -]?the[ -]?scenes|making(?:[ -]?of)?|production\s+(?:interview|notes|report)|"
        r"\ud32c\ub364|\ud32c\s*(?:\ud1a0\ub860|\uc774\ub860|\uc608\uce21|\ud654\uc81c)|"
        r"\ubc08|\uc720\uba38|\ub18d\ub2f4|\uc81c\uc791\uc9c4\s*\uc778\ud130\ubdf0|\uc81c\uc791\s*\uacfc\uc815|\ube44\ud558\uc778\ub4dc|\uba54\uc774\ud0b9|"
        r"\u5236\u4f5c\u79d8\u8a71|\u821e\u53f0\u88cf|\u30e1\u30a4\u30ad\u30f3\u30b0|\u88fd\u4f5c\u9663",
        title,
    ):
        inferred.add("humor")
    if re.search(
        r"behind[ -]?the[ -]?scenes|making(?:[ -]?of)?|production notes|"
        r"production interview|staff interview|production report|recording report|"
        r"\u5236\u4f5c\u79d8\u8a71|\u821e\u53f0\u88cf|\u30e1\u30a4\u30ad\u30f3\u30b0|"
        r"\u5236\u4f5c\u904e\u7a0b|\u88fd\u4f5c\u9663|\ube44\ud558\uc778\ub4dc|"
        r"\uba54\uc774\ud0b9|\uc81c\uc791\uc9c4\s*\uc778\ud130\ubdf0|\uc81c\uc791\s*\uacfc\uc815",
        title,
    ):
        inferred.add("humor")
    if re.search(r"announce|announcement|announced|発表|解禁|決定|ニュース|news", title):
        inferred.add("news")

    # Editorial/reference endpoints have a narrow, explicit purpose even when their
    # list-page title is generic. Community feeds remain trend signals only.
    category_defaults = {
        "news": "news",
        "review": "review",
        "curation": "curation",
        "community_trend": "humor",
        "music_label": "music",
        "goods": "goods",
        "event": "event",
        "korea_entertainment_trade_news": "news",
        "korea_official_news": "news",
        "kitsu_anime_catalog": "guide",
        "onnada_character_catalog": "character",
        "wikipedia_reference": "guide",
        "wikidata_character_catalog": "character",
        "ann_encyclopedia": "guide",
    }
    if source.category in category_defaults:
        inferred.add(category_defaults[source.category])
    # Review listings often expose polls, rankings and community UI beside
    # actual review articles. The publisher category alone must not turn
    # those navigation links into editorial reviews.
    path = urlparse(str(item.url)).path.casefold()
    if source.category == "kitsu_anime_catalog" and "/media-characters/" in path:
        inferred.discard("guide")
        inferred.add("character")
    if source.category == "review" and re.search(
        r"/(?:db/community/rankings|polls|rankings|forum)(?:/|$)", path
    ):
        inferred.discard("review")
    if source.category == "official_news" and not inferred:
        inferred.add("news")
    if "news" in inferred and re.search(r"announce|announcement|announced|発表|解禁|決定", title):
        inferred.discard("guide")
    if source.role == "editorial" and source.category not in {
        "news",
        "review",
        "curation",
        "event",
        "goods",
        "korea_entertainment_trade_news",
        "korea_official_news",
    }:
        inferred.discard("news")
    # A period-specific subject must flow through the event/goods crawler so
    # expiry_evidence can hold expired or undated items before generation.
    # Broad source categories such as korea_official_news must not add a second
    # generic-news route for the same pop-up or merchandise announcement.
    if inferred & {"event", "goods"}:
        inferred.discard("news")
    announcement_only = re.search(
        r"new season|season \d+|season [ivx]+|sequel|cast announcement|broadcast date|"
        r"premiere date|airing date|放送決定|放送開始|放送日|放送|放映|配信開始|続編|"
        r"第[0-9０-９一二三四五六七八九十]+期|キャスト解禁|新シリーズ|"
        r"방영 확정|방송 시작|방송일|방영일|방영|방송|캐스팅 공개|새 시즌|후속 시즌|속편",
        title,
    )
    actual_event = re.search(
        r"event|exhibition|festival|concert|ticket|venue|screening|pop[ -]?up|"
        r"イベント|展示|会場|公演|上映|チケット|開催|전시|축제|공연|상영|티켓|팝업|오프라인",
        title,
    )
    actual_event = actual_event or re.search(
        r"\ud31d\uc5c5|\uc804\uc2dc|\uc804\uc2dc\ud68c|\ud589\uc0ac|\ud398\uc5b4|\ud2f0\ucf13|\uad00\ub78c|"
        r"\ucf5c\ub77c\ubcf4\s*\uce74\ud398|\uc624\ud504\ub77c\uc778\s*\uc2a4\ud1a0\uc5b4|"
        r"\u958b\u50ac|\u5c55\u89a7|\u5c55\u793a|\u50ac\u4e8b|\u516c\u6f14|\u821e\u53f0|\u30a4\u30d9\u30f3\u30c8",
        title,
    )
    if announcement_only and not actual_event:
        inferred.discard("event")
        if "news" in available:
            inferred.add("news")
    if not inferred and "news" in available and source.role in {"primary", "discovery"}:
        inferred.add("news")
    # The announcement fallback above is intended for evergreen news only.
    # Restore period routing if a release headline also matched event/goods.
    if inferred & {"event", "goods"}:
        inferred.discard("news")
    return inferred & available


def supports_discovery_adapter(source: Source, *, work_title: str | None = None) -> bool:
    """Return whether discovery.py has an implementation for this source route.

    This is a code-capability check, not proof that a live endpoint is reachable,
    permitted, or currently returning useful results.
    """
    if not source.discovery_enabled:
        return False
    if source.status == "trend_only":
        return (
            source.role == "trend"
            and source.collection == "api"
            and source.category == "community_trend"
        )
    if source.status != "active":
        return False
    if source.collection == "rss":
        return True
    if source.collection == "youtube_rss":
        return source.feed_url is not None
    if source.collection == "html":
        return source.role in {"primary", "editorial", "reference", "discovery"} and (
            source.category != "ann_encyclopedia" or bool(work_title)
        )
    if source.collection == "api":
        if source.role == "reference":
            if source.category == "kitsu_anime_catalog":
                return bool(work_title) or "curation" in {
                    item.value for item in source.content_types
                }
            return source.category in {
                "wikipedia_reference",
                "wikidata_character_catalog",
            } and bool(work_title)
        return source.id == "anilist" and source.role in {"primary", "discovery"}
    return False


def _collectable_sources_for_type(
    content_type: str | None,
    source_ids: set[str] | None = None,
    work_title: str | None = None,
) -> dict[str, Source]:
    active_types = {item.value for item in CONTENT_TYPE_LABELS}
    if content_type is not None and content_type not in active_types:
        raise ValueError(f"지원하지 않는 콘텐츠 유형입니다: {content_type}")
    return {
        source.id: source
        for source in load_source_registry().sources
        if supports_discovery_adapter(source, work_title=work_title or "adapter-check")
        if content_type is None or content_type in {item.value for item in source.content_types}
        if source_ids is None or source.id in source_ids
    }


def parse_anime_schedule(xml: bytes) -> list[DiscoveryItem]:
    feed = feedparser.parse(xml)
    items: list[DiscoveryItem] = []
    for entry in feed.entries:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        items.append(
            DiscoveryItem(
                id=_item_id("anime_schedule_rss", url),
                title=title.strip(),
                url=url,
                source_id="anime_schedule_rss",
                content_types=_source_type_codes("anime_schedule_rss"),
                published_at=entry.get("published"),
                summary=entry.get("summary", "").strip(),
                region="global",
            )
        )
    return items


ANIME_RELEASE_TERMS = (
    "アニメ",
    "anime",
    "アニソン",
    "애니메이션",
    "애니메",
)


def parse_release_rss(
    xml: bytes,
    source_id: str,
    region: str,
    source_role: str = "discovery",
) -> list[DiscoveryItem]:
    """Parse public press-release RSS as filtered leads, never as verified facts."""
    feed = feedparser.parse(xml)
    items: list[DiscoveryItem] = []
    for entry in feed.entries:
        url = entry.get("link")
        title = (entry.get("title") or "").strip()
        summary = (entry.get("summary") or "").strip()
        if not url or not title:
            continue
        searchable = f"{title} {summary}".casefold()
        # Japanese product releases often use アニメーション to mean visual effects,
        # not anime. Do not let that single generic word create a news lead.
        searchable = searchable.replace("アニメーション", "").replace("animation", "")
        if not any(term.casefold() in searchable for term in ANIME_RELEASE_TERMS):
            continue
        items.append(
            DiscoveryItem(
                id=_item_id(source_id, url),
                title=title,
                url=url,
                source_id=source_id,
                content_types=_source_type_codes(source_id),
                published_at=entry.get("published"),
                summary=summary,
                region=region,
                source_role=source_role,
            )
        )
    return items


def _normalized_tag(value: str) -> str:
    return re.sub(r"[^\w가-힣ぁ-んァ-ン一-龯]", "", value.casefold(), flags=re.UNICODE)


def _catalog_work_titles_for_tags(tags: list[str]) -> list[str]:
    normalized_tags = {_normalized_tag(tag) for tag in tags}
    matches: list[tuple[int, str]] = []
    seen: set[int] = set()
    for work in load_work_catalog():
        work_id = int(work["menu_work_id"])
        if work_id in seen:
            continue
        aliases = [
            str(work.get(key) or "")
            for key in ("title_ko", "title_romaji", "title_english", "title_native")
        ]
        if any(_normalized_tag(alias) in normalized_tags for alias in aliases if alias.strip()):
            matches.append((work_id, str(work.get("title_ko") or work.get("title_romaji"))))
            seen.add(work_id)
    return [title for _, title in matches]


def _catalog_work_titles_in_text(text: str) -> list[str]:
    # Avoid matching a work that appears only in a quoted credit, hashtag dump,
    # or incidental parenthetical unrelated to the post's main topic.
    lead = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)[0][:240]
    lead = re.sub(r"[（(][^）)]{1,100}[）)]", " ", lead)
    normalized_text = _normalized_tag(lead)
    raw_text = lead.casefold()
    matches: list[tuple[int, str]] = []
    for work in load_work_catalog():
        aliases = [
            str(work.get(key) or "")
            for key in ("title_ko", "title_romaji", "title_english", "title_native")
        ]
        matched = False
        for alias in aliases:
            normalized_alias = _normalized_tag(alias)
            if not alias.strip() or len(normalized_alias) < 3:
                continue
            if re.fullmatch(r"[a-z0-9]+", normalized_alias):
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                    raw_text,
                ):
                    matched = True
                    break
            elif normalized_alias in normalized_text:
                matched = True
                break
        if matched:
            matches.append(
                (
                    int(work["menu_work_id"]),
                    str(work.get("title_ko") or work.get("title_romaji")),
                )
            )
    return [title for _, title in sorted(matches)]


def _thread_worth_fetching(status: dict) -> bool:
    """Spend a Mastodon context lookup only on a non-bot, work-specific post with replies."""
    if (
        not isinstance(status, dict)
        or status.get("visibility") != "public"
        or status.get("sensitive")
        or status.get("reblog")
        or status.get("in_reply_to_id")
    ):
        return False
    account = status.get("account")
    if not isinstance(account, dict) or account.get("bot"):
        return False
    try:
        if int(status.get("replies_count") or 0) < 1:
            return False
    except (TypeError, ValueError):
        return False
    tags = [
        str(tag.get("name") or "").strip()
        for tag in status.get("tags", [])
        if isinstance(tag, dict) and tag.get("name")
    ]
    automation_tags = {"bot", "bots", "autopost", "automated", "rssbot", "feedbot"}
    if {_normalized_tag(tag) for tag in tags} & automation_tags:
        return False
    body = BeautifulSoup(str(status.get("content") or ""), "html.parser").get_text(
        " ", strip=True
    )
    body = re.sub(r"https?://\S+|@[\w.-]+(?:@[\w.-]+)?", "", body)
    return bool(_catalog_work_titles_for_tags(tags) or _catalog_work_titles_in_text(body))


def parse_mastodon_tag_timeline(payload: list[dict], source: Source) -> list[DiscoveryItem]:
    """Collect aggregate trend tags and anonymized, work-specific public discussion leads."""
    counts: Counter[str] = Counter()
    timestamps: list[str] = []
    items: list[DiscoveryItem] = []
    news_tags = {"animenews", "news", "newanime", "pressrelease", "newsrelease", "ニュース"}
    automation_tags = {"bot", "bots", "autopost", "automated", "rssbot", "feedbot"}
    for status in payload:
        if not isinstance(status, dict):
            continue
        if status.get("visibility") != "public" or status.get("sensitive"):
            continue
        tags = [
            str(tag.get("name") or "").strip()
            for tag in status.get("tags", [])
            if isinstance(tag, dict) and tag.get("name")
        ]
        normalized_tags = {_normalized_tag(tag) for tag in tags}
        if normalized_tags & automation_tags:
            continue
        created_at = status.get("created_at")
        if isinstance(created_at, str):
            timestamps.append(created_at)
        for tag in status.get("tags", []):
            name = tag.get("name") if isinstance(tag, dict) else None
            normalized = re.sub(r"[^\w가-힣-]", "", str(name or "").strip(), flags=re.UNICODE)
            if normalized and normalized.casefold() not in {
                "anime",
                "animation",
                "animeart",
                "art",
                "manga",
                "アニメ",
            }:
                counts[normalized] += 1
        # Replies are represented through a separately fetched public thread context.
        if status.get("reblog") or status.get("in_reply_to_id"):
            continue
        post_url = status.get("url")
        if not isinstance(post_url, str) or not post_url.startswith("https://"):
            continue
        body = BeautifulSoup(str(status.get("content") or ""), "html.parser").get_text(
            " ", strip=True
        )
        body = re.sub(r"https?://\S+|@[\w.-]+(?:@[\w.-]+)?", "", body)
        body = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        work_titles = _catalog_work_titles_for_tags(tags) or _catalog_work_titles_in_text(body)
        community_posts = status.get("community_posts")
        if not isinstance(community_posts, list):
            community_posts = []
        if (
            not work_titles
            or normalized_tags & news_tags
            or len(body) < 40
            or len(community_posts) < 2
        ):
            continue
        title = f"{work_titles[0]} 팬덤 토론"
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, str(status.get("id") or post_url)),
                title=title,
                url=post_url,
                source_id=source.id,
                content_types=["humor"],
                published_at=created_at if isinstance(created_at, str) else None,
                summary=body[:700],
                status="humor_post_candidate",
                region=source.region,
                source_role="trend",
                is_community_post=True,
                community_posts=community_posts[:6],
            )
        )
    if not counts:
        return items

    top_tags = [f"#{tag}" for tag, _ in counts.most_common(5)]
    latest = max(timestamps, default=None)
    identity = f"{latest or 'undated'}|{'|'.join(top_tags)}"
    items.append(
        DiscoveryItem(
            id=_item_id(source.id, identity),
            title=f"애니 커뮤니티 화제 태그: {' · '.join(top_tags)}",
            url=str(source.url),
            source_id=source.id,
            content_types=["humor"],
            published_at=latest,
            summary=(
                f"공개 태그 타임라인 {len(payload)}개 상태에서 태그 빈도만 집계한 주제 신호. "
                "이 집계 레코드에는 본문·계정·이미지가 없다. "
                "별도 원글 후보도 팬덤 전체 의견을 뜻하지 않는다."
            ),
            status="trend_signal",
            region=source.region,
            source_role="trend",
        )
    )
    return items


def _clean_public_mastodon_status(status: dict) -> dict[str, str] | None:
    """Return a public post's paraphrase input without account identifiers."""
    if (
        not isinstance(status, dict)
        or status.get("visibility") != "public"
        or status.get("sensitive")
        or status.get("reblog")
    ):
        return None
    url = status.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    body = BeautifulSoup(str(status.get("content") or ""), "html.parser").get_text(
        " ", strip=True
    )
    body = re.sub(r"https?://\S+|@[^\s]+", "", body)
    body = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) < 25:
        return None
    account = status.get("account")
    if not isinstance(account, dict):
        account = {}
    author_key = str(account.get("id") or account.get("acct") or "").strip()
    if not author_key:
        return None
    return {
        "text": body[:700],
        "url": url,
        "published_at": str(status.get("created_at") or ""),
        "_author_key": author_key,
        "_status_id": str(status.get("id") or url),
    }


def _mastodon_thread_posts(
    client: httpx.Client, source: Source, status: dict
) -> list[dict[str, str]]:
    """Fetch one bounded public thread and keep only distinct, non-sensitive views."""
    root = _clean_public_mastodon_status(status)
    if root is None or not str(status.get("id", "")).isdigit():
        return []
    base = urlparse(str(source.url))
    context_url = f"{base.scheme}://{base.netloc}/api/v1/statuses/{status['id']}/context"
    try:
        response = _get_with_backoff(client, context_url, source.min_interval_seconds)
        context = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    descendants = context.get("descendants", []) if isinstance(context, dict) else []
    candidates = [root]
    seen_statuses = {root["_status_id"]}
    for descendant in descendants if isinstance(descendants, list) else []:
        clean = _clean_public_mastodon_status(descendant)
        if clean and clean["_status_id"] not in seen_statuses:
            seen_statuses.add(clean["_status_id"])
            candidates.append(clean)
        if len(candidates) >= 6:
            break
    authors = {post["_author_key"] for post in candidates}
    if len(authors) < 2:
        return []
    # Account IDs are used only in memory to deduplicate. Never persist them.
    result = []
    seen_authors: set[str] = set()
    for post in candidates:
        author = post.pop("_author_key", "")
        post.pop("_status_id", None)
        if author in seen_authors:
            continue
        seen_authors.add(author)
        result.append(post)
    return result


def parse_kitsu_search(payload: dict, source: Source, limit: int = 10) -> list[DiscoveryItem]:
    """Map Kitsu title-search results to unverified work-specific leads."""
    rows = payload.get("data", [])
    items: list[DiscoveryItem] = []
    if not isinstance(rows, list):
        return items
    for row in rows[:limit]:
        if not isinstance(row, dict) or not str(row.get("id", "")).isdigit():
            continue
        attrs = row.get("attributes") or {}
        title = str(attrs.get("canonicalTitle") or "").strip()
        titles = attrs.get("titles") or {}
        if not title and isinstance(titles, dict):
            title = str(
                titles.get("en") or titles.get("en_jp") or titles.get("ja_jp") or ""
            ).strip()
        if not title:
            continue
        url = f"https://kitsu.io/anime/{row['id']}"
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, url),
                title=title,
                url=url,
                source_id=source.id,
                content_types=_source_type_codes(source.id),
                summary=(
                    "Kitsu public catalog title match; verify the exact work/season before use."
                ),
                region=source.region,
            )
        )
    return items


def parse_kitsu_characters(
    payload: dict,
    source: Source,
    anime: dict,
    limit: int = 20,
) -> list[DiscoveryItem]:
    """Map anime-character relationships to separately attributable API leads."""
    rows = payload.get("data", [])
    included = payload.get("included", [])
    if not isinstance(rows, list) or not isinstance(included, list):
        return []
    characters = {
        str(item.get("id")): item
        for item in included
        if isinstance(item, dict) and item.get("type") == "characters"
    }
    anime_attrs = anime.get("attributes") or {}
    titles = anime_attrs.get("titles") or {}
    anime_title = str(anime_attrs.get("canonicalTitle") or titles.get("en") or "").strip()
    items: list[DiscoveryItem] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id", "")).isdigit():
            continue
        relationship = row.get("relationships") or {}
        character_ref = (relationship.get("character") or {}).get("data") or {}
        character = characters.get(str(character_ref.get("id", "")))
        if character is None:
            continue
        attrs = character.get("attributes") or {}
        names = attrs.get("names") or {}
        name = str(attrs.get("canonicalName") or names.get("en") or "").strip()
        if len(name) < 3 or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        role = str((row.get("attributes") or {}).get("role") or "unknown")
        url = f"https://kitsu.io/api/edge/media-characters/{row['id']}?include=character,media"
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, url),
                title=f"{name} — {anime_title}" if anime_title else name,
                url=url,
                source_id=source.id,
                content_types=["character"],
                summary=(
                    f"Kitsu character relationship; character={name}; work={anime_title}; "
                    f"role={role}. Reference metadata; verify the exact work association."
                ),
                region=source.region,
                source_role=source.role,
            )
        )
        if len(items) >= limit:
            break
    items.sort(key=lambda item: ("role=main" not in item.summary, item.title.casefold()))
    return items


def parse_onnada_birthday_listing(
    html: str,
    source: Source,
    *,
    limit: int = 30,
    work_title: str | None = None,
) -> list[DiscoveryItem]:
    """Create character leads from Onnada's dated character/work pairs."""
    soup = BeautifulSoup(html, "html.parser")
    requested_work_ids = set(resolve_title_work_ids(work_title or ""))
    items: list[DiscoveryItem] = []
    seen: set[str] = set()

    for character_anchor in soup.select('a[href^="/character/"]'):
        character_match = re.fullmatch(
            r"/character/(\d+)(?:/index\.html?)?/?",
            urlparse(urljoin(str(source.url), str(character_anchor.get("href") or ""))).path,
            re.IGNORECASE,
        )
        if not character_match:
            continue

        card = character_anchor
        work_anchor = None
        day_item = None
        for _ in range(5):
            card = card.parent
            if card is None:
                break
            character_links = [
                link
                for link in card.select('a[href^="/character/"]')
                if re.fullmatch(
                    r"/character/\d+(?:/index\.html?)?/?",
                    urlparse(urljoin(str(source.url), str(link.get("href") or ""))).path,
                    re.IGNORECASE,
                )
            ]
            work_links = [
                link
                for link in card.select('a[href^="/anime/"]')
                if re.fullmatch(
                    r"/anime/\d+(?:/index\.html?)?/?",
                    urlparse(urljoin(str(source.url), str(link.get("href") or ""))).path,
                    re.IGNORECASE,
                )
            ]
            if len(character_links) == 1 and len(work_links) == 1:
                character_anchor = character_links[0]
                work_anchor = work_links[0]
                day_item = card.find_parent(
                    "li", id=re.compile(r"^character-birthday-timeline-day-")
                )
                break
        if work_anchor is None:
            continue

        character_name = re.sub(r"\s+", " ", character_anchor.get_text(" ", strip=True)).strip()
        linked_work_title = re.sub(r"\s+", " ", work_anchor.get_text(" ", strip=True)).strip()
        if len(character_name) < 2 or len(linked_work_title) < 2:
            continue
        linked_work_ids = set(resolve_title_work_ids(linked_work_title))
        if requested_work_ids and linked_work_ids and not requested_work_ids.intersection(
            linked_work_ids
        ):
            continue
        if requested_work_ids and not linked_work_ids:
            requested_normalized = _normalized_tag(work_title or "")
            linked_normalized = _normalized_tag(linked_work_title)
            if not requested_normalized or not (
                requested_normalized in linked_normalized
                or linked_normalized in requested_normalized
            ):
                continue

        character_id = character_match.group(1)
        profile_url = urljoin(str(source.url), f"/character/{character_id}")
        if day_item is not None:
            date_match = re.search(
                r"character-birthday-timeline-day-(20\d{2})-(\d{1,2})-(\d{1,2})",
                str(day_item.get("id") or ""),
            )
        else:
            date_match = None
        birthday = (
            f"{int(date_match.group(2)):02d}-"
            f"{int(date_match.group(3)):02d}"
            if date_match
            else ""
        )
        identity = f"{character_id}|{linked_work_title.casefold()}"
        if identity in seen:
            continue
        seen.add(identity)
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, identity),
                title=f"{linked_work_title} — {character_name}",
                url=profile_url,
                source_id=source.id,
                content_types=["character"],
                character_name=character_name,
                linked_work_title=linked_work_title,
                birthday_mmdd=birthday or None,
                linked_work_ids=sorted(linked_work_ids),
                summary=(
                    f"Onnada character_id={character_id}; character={character_name}; "
                    f"birthday={birthday or 'not-listed'}; linked_work={linked_work_title}. "
                    "Use the linked character profile and preserve its NamuWiki reference URL."
                ),
                region="KR",
                source_role=source.role,
            )
        )
        if len(items) >= limit:
            break
    return items


def parse_kitsu_rating_ranking(
    payload: dict,
    source: Source,
    limit: int = 20,
) -> list[DiscoveryItem]:
    """Build an explicitly attributed Kitsu-user-rating roundup lead."""
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        return []
    ranking_rows: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict) or not str(row.get("id", "")).isdigit():
            continue
        attrs = row.get("attributes") or {}
        title = str(attrs.get("canonicalTitle") or "").strip()
        rating = attrs.get("averageRating")
        rank = attrs.get("ratingRank")
        if not title or rating is None or rank is None:
            continue
        ranking_rows.append(
            {
                "rank": rank,
                "title": title,
                "average_rating": rating,
                "rating_count": attrs.get("userCount"),
                "kitsu_url": f"https://kitsu.io/anime/{row['id']}",
            }
        )
    if not ranking_rows:
        return []
    base_url = str(source.url).rstrip("/")
    url = f"{base_url}?sort=-averageRating&page[limit]={min(max(limit, 1), 20)}"
    return [
        DiscoveryItem(
            id=_item_id(source.id, url),
            title="Kitsu rating ranking · 사용자 평점 랭킹",
            url=url,
            source_id=source.id,
            content_types=["curation"],
            summary=(
                "Kitsu 등록 사용자 표본의 평점 기준 랭킹이며 전체 애니 팬덤의 보편적 선호를 "
                "뜻하지 않습니다. 순위·평점·평가 수: "
                + json.dumps(ranking_rows, ensure_ascii=False)
            ),
            region=source.region,
            source_role=source.role,
        )
    ]


def parse_wikipedia_search(payload: dict, source: Source, limit: int = 10) -> list[DiscoveryItem]:
    """Map MediaWiki title-search results to cited article URLs, not copied prose."""
    results = payload.get("query", {}).get("search", [])
    if not isinstance(results, list):
        return []
    host = urlparse(str(source.url)).hostname
    if not host:
        return []
    items: list[DiscoveryItem] = []
    for result in results[:limit]:
        if not isinstance(result, dict):
            continue
        title = BeautifulSoup(str(result.get("title", "")), "html.parser").get_text(" ", strip=True)
        if not title:
            continue
        url = f"https://{host}/wiki/{quote(title.replace(' ', '_'))}"
        snippet = BeautifulSoup(str(result.get("snippet", "")), "html.parser").get_text(
            " ", strip=True
        )
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, url),
                title=title[:300],
                url=url,
                source_id=source.id,
                content_types=_source_type_codes(source.id),
                summary=snippet[:500],
                region=source.region,
            )
        )
    return items


def parse_wikidata_characters(
    payload: dict, source: Source, limit: int = 20
) -> list[DiscoveryItem]:
    """Convert exact work-to-character relationships into reference leads."""
    bindings = payload.get("results", {}).get("bindings", [])
    items: list[DiscoveryItem] = []
    seen: set[str] = set()
    for binding in bindings:
        character = binding.get("character", {}).get("value", "")
        title = binding.get("characterLabel", {}).get("value", "").strip()
        work_title = binding.get("workLabel", {}).get("value", "").strip()
        if not character.startswith("http://www.wikidata.org/entity/") or not title:
            continue
        url = character.replace("http://", "https://", 1)
        if url in seen:
            continue
        seen.add(url)
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, url),
                title=title[:300],
                url=url,
                source_id=source.id,
                content_types=_source_type_codes(source.id),
                summary=(
                    f"Wikidata character relationship for {work_title}; "
                    "verify against a primary source."
                ),
                region=source.region,
                source_role=source.role,
            )
        )
        if len(items) >= limit:
            break
    return items


def parse_ann_search(
    html: str,
    source: Source,
    *,
    content_type: str,
    limit: int = 10,
) -> list[DiscoveryItem]:
    """Extract actual ANN title/person records from a title-query result page."""
    soup = BeautifulSoup(html, "html.parser")
    expected_path = (
        "/encyclopedia/people.php" if content_type == "people" else "/encyclopedia/anime.php"
    )
    items: list[DiscoveryItem] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", ""))
        parsed = urlparse(urljoin(str(source.url), href))
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if (
            parsed.scheme != "https"
            or parsed.hostname != urlparse(str(source.url)).hostname
            or parsed.path != expected_path
            or not parsed.query
            or len(title) < 3
        ):
            continue
        normalized = parsed._replace(fragment="").geturl()
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            DiscoveryItem(
                id=_item_id(source.id, normalized),
                title=title[:300],
                url=normalized,
                source_id=source.id,
                content_types=_source_type_codes(source.id),
                summary="ANN Encyclopedia search result; confirm the exact work entry.",
                region=source.region,
            )
        )
        if len(items) >= limit:
            break
    return items


def parse_anilist(payload: dict) -> list[DiscoveryItem]:
    items: list[DiscoveryItem] = []
    media = payload.get("data", {}).get("Page", {}).get("media", [])
    for anime in media:
        titles = anime.get("title") or {}
        title = titles.get("english") or titles.get("romaji") or titles.get("native")
        url = anime.get("siteUrl")
        if not title or not url:
            continue
        start = anime.get("startDate") or {}
        date_parts = [start.get("year"), start.get("month"), start.get("day")]
        published_at = None
        if all(date_parts):
            published_at = f"{date_parts[0]:04d}-{date_parts[1]:02d}-{date_parts[2]:02d}"
        items.append(
            DiscoveryItem(
                id=f"anilist-{anime['id']}",
                title=title.strip(),
                url=url,
                source_id="anilist",
                content_types=_source_type_codes("anilist"),
                published_at=published_at,
                summary="방영 전 작품 후보. 공식 발표 원문 확인 필요.",
                region="JP",
            )
        )
    return items


def parse_html_listing(html: str, source: Source, limit: int = 30) -> list[DiscoveryItem]:
    """Turn article links from an approved listing page into unverified leads."""
    soup = BeautifulSoup(html, "html.parser")
    if source.id == "recal_music":
        items: list[DiscoveryItem] = []
        for record in _recal_music_records(soup):
            try:
                days_until = int(record.get("d"))
            except (TypeError, ValueError):
                days_until = 0
            # `d` is RE:CAL's days-until-event field, not publication age.
            if days_until < 0:
                continue
            href = str(record["href"])
            if not href.startswith("/ko/"):
                href = "/ko" + href
            url = urljoin(str(source.url), href)
            details = [
                str(record.get(key) or "").strip()
                for key in ("date", "ym", "venue", "region", "when", "runtime", "age", "artist")
                if record.get(key)
            ]
            prices = record.get("prices")
            if isinstance(prices, list):
                details.extend(str(price).strip() for price in prices if str(price).strip())
            items.append(
                DiscoveryItem(
                    id=f"recal_music-{record.get('id') or record.get('key')}",
                    title=str(record["title"]).strip()[:300],
                    url=url,
                    source_id=source.id,
                    content_types=[item.value for item in source.content_types],
                    summary="RE:CAL 월별 공연 목록 후보: " + " · ".join(details)[:1800],
                    region=source.region,
                    status="discovery_only",
                    source_role=source.role,
                )
            )
            if len(items) >= limit:
                break
        if items:
            return items

    candidates: list[DiscoveryItem] = []
    seen: set[str] = set()
    source_parsed = urlparse(str(source.url))
    source_host = (source_parsed.hostname or "").lower()
    source_page_path = source_parsed.path.rstrip("/").lower()
    page_segments = [segment for segment in source_page_path.split("/") if segment]
    taxonomy_listing = any(
        segment in {"category", "categories", "tag", "tags", "event", "events", "goods"}
        for segment in page_segments
    )
    source_path = source_page_path
    if source.id == "recal_kr" and source_page_path == "/ko/c/offline":
        # RE:CAL's dedicated offline-event index links to records under a
        # sibling path (/ko/e/offline/...), so the listing path is not a
        # prefix of its event detail URLs. Keep discovery scoped to that
        # event-record namespace instead of opening the broader site root.
        source_path = "/ko/e/offline"
    if source.id == "recal_music" and source_page_path == "/ko/c/music":
        # Concert detail records live below /ko/e/music rather than under the
        # category path. Restrict this source to those event records.
        source_path = "/ko/e/music"
    if taxonomy_listing:
        # Category archives often link to root-level canonical articles. Keep a
        # /news prefix when the archive itself lives there, to avoid treating
        # unrelated site navigation (e.g. /program or /movie) as story leads.
        source_path = "/news" if "news" in page_segments and "category" not in page_segments else ""
    elif source_path.endswith((".html", ".htm", ".php", ".aspx")) or source_path.rsplit("/", 1)[
        -1
    ] in {"archive", "archives", "index", "latest", "all"}:
        source_path = source_path.rpartition("/")[0]
    source_path = source_path.rstrip("/")
    listing_only_segments = {
        "archive",
        "archives",
        "about",
        "about-us",
        "author",
        "authors",
        "calendar",
        "category",
        "categories",
        "contact",
        "donate",
        "editorial-policy",
        "event",
        "events",
        "feed",
        "goods",
        "news",
        "page",
        "privacy",
        "program",
        "ranking",
        "search",
        "shop",
        "services",
        "submissions",
        "tag",
        "tags",
        "terms",
        "movie",
        "movies",
    }
    navigation_titles = {
        "about",
        "articles",
        "database",
        "home",
        "login",
        "privacy policy",
        "register",
        "search",
        "shop",
        "submissions",
        "terms of use",
        "top",
        "vote now",
    }
    minimum_path_segments = 1 if not source_path else 2
    if source.id == "spy_family_official":
        # The official site renders article details as /news/?p=<id>.
        minimum_path_segments = 1
    if source.id == "recal_kr" and source_path == "/ko/e/offline":
        minimum_path_segments = 4
    if source.id == "recal_music" and source_path == "/ko/e/music":
        minimum_path_segments = 4

    # Prefer article cards and main content so a page's nav/footer links do not
    # consume the candidate limit before actual stories are reached.
    ordered_anchors = list(soup.select("article a[href]"))
    ordered_anchors.extend(soup.select("main a[href]"))
    ordered_anchors.extend(soup.select("a[href]"))
    anchors: list = []
    anchor_keys: set[tuple[str, str]] = set()
    for anchor in ordered_anchors:
        key = (str(anchor.get("href", "")), " ".join(anchor.get_text(" ", strip=True).split()))
        if key not in anchor_keys:
            anchor_keys.add(key)
            anchors.append(anchor)

    # Some official sites render cards outside semantic article/list elements.
    # Inspect all links after content-scoped links, then reject taxonomy/navigation.
    for anchor in anchors:
        title = _listing_anchor_title(anchor, source)
        href = anchor.get("href")
        if not title or not href or len(title) < 8:
            continue
        if len(title) % 2 == 0 and title[: len(title) // 2] == title[len(title) // 2 :]:
            title = title[: len(title) // 2].strip()
        if title.casefold().strip(" .!?:") in navigation_titles:
            continue
        url = urljoin(str(source.url), str(href))
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != source_host:
            continue
        detail_query = (
            parsed.query
            if source.id == "spy_family_official"
            and re.fullmatch(r"p=\d+", parsed.query)
            else ""
        )
        normalized_url = parsed._replace(
            path=parsed.path.rstrip("/"), query=detail_query, fragment=""
        ).geturl()
        if normalized_url in seen:
            continue
        path = parsed.path.rstrip("/").lower()
        if source.id == "natsume_official" and not path.startswith("/news/"):
            continue
        if source.id == "spy_family_official" and not re.fullmatch(r"p=\d+", parsed.query):
            continue
        if source.id == "src_www_toei_anim_co_jp" and not path.startswith("/ja/press/"):
            # Toei's corporate press archive shares its page with policy and
            # corporate-navigation links; only dated press records are leads.
            continue
        if source.id == "anime_news_network" and path.startswith("/review/game/"):
            # The review archive also includes video-game reviews, which are not
            # anime-review leads for this source mapping.
            continue
        if source.id == "anime_news_network" and re.search(
            r"\blive[ -]?action\b|実写|실사", title, re.IGNORECASE
        ):
            continue
        if source.id == "eeo_events" and not re.fullmatch(
            r"/media/\d{4}/\d{2}/\d{2}/\d+", path
        ):
            continue
        if source.id == "anime_hack_events" and not re.search(
            r"\u958b\u50ac|\u5c55\u89a7|\u30a4\u30d9\u30f3\u30c8|\u30d5\u30a7\u30a2|"
            r"\u30d5\u30a7\u30b9|\u30b3\u30e9\u30dc|\u4e0a\u6620|\u4f1a\u5834|"
            r"\u516c\u6f14|\u30c1\u30b1\u30c3\u30c8|\u4f53\u9a13|"
            r"\u30dd\u30c3\u30d7\u30a2\u30c3\u30d7|\u30b9\u30c8\u30a2",
            title,
        ):
            continue
        if source.id == "src_www_megahobby_jp" and not re.fullmatch(
            r"/products/item/\d+", path
        ):
            continue
        if source.category == "review" and (
            path.startswith("/db/community/rankings/") or path in {"/polls", "/polls/browse"}
        ):
            # These are navigation/user-ranking pages linked from the review
            # publisher, not review articles or usable poll results.
            continue
        if source.category in {"event", "goods"} and re.match(r"^/store(?:/|$)", path):
            # Publisher store/CTA links are not editorial event or product leads.
            continue
        if path.endswith((".pdf", ".zip", ".xml", ".json", ".rss", ".mp4", ".mp3")):
            continue
        if not path or (
            path == source_page_path
            and not (
                source.id == "spy_family_official"
                and re.fullmatch(r"p=\d+", parsed.query)
            )
        ):
            continue
        query_detail = (
            source.id == "spy_family_official"
            and path == source_path
            and re.fullmatch(r"p=\d+", parsed.query)
        )
        if source_path and not path.startswith(f"{source_path}/") and not query_detail:
            continue
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < minimum_path_segments or (
            segments[-1] in listing_only_segments and not query_detail
        ):
            continue
        if re.search(r"/(?:page|tag|category|author)(?:/|$)", path):
            continue
        published_at = _listing_card_published_at(anchor)
        if source.id in {
            "src_www_toei_anim_co_jp",
            "jujutsu_kaisen_official",
            "natsume_official",
            "haikyu_official",
            "spy_family_official",
            "my_hero_academia_official",
        }:
            raw_title = " ".join(anchor.get_text(" ", strip=True).split())
            date_match = re.match(
                r"\s*(?:(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5?"
                r"|(20\d{2})[./-](\d{1,2})[./-](\d{1,2}))",
                raw_title,
            )
            if date_match:
                try:
                    parts = date_match.groups()[:3] if date_match.group(1) else date_match.groups()[3:]
                    published_at = datetime(*(int(part) for part in parts)).date().isoformat()
                except (TypeError, ValueError):
                    pass
        if source.id == "eeo_events" and not published_at:
            # EEO archive URLs encode publication date as /media/YYYY/MM/DD/<id>.
            date_match = re.fullmatch(r"/media/(20\d{2})/(\d{2})/(\d{2})/\d+", path)
            if date_match:
                try:
                    published_at = datetime(
                        *(int(part) for part in date_match.groups())
                    ).date().isoformat()
                except ValueError:
                    pass

        seen.add(normalized_url)
        candidate_types = [item.value for item in source.content_types]
        if source.id == "src_www_toei_anim_co_jp":
            # The corporate archive mixes ordinary announcements, events,
            # pop-ups, and merchandise on one listing. Route each lead by its
            # headline so period-sensitive items reach the right lane.
            candidate_types = [infer_toei_press_type(title)]
        candidates.append(
            DiscoveryItem(
                id=_item_id(source.id, normalized_url),
                title=title[:300],
                url=normalized_url,
                source_id=source.id,
                content_types=candidate_types,
                summary="목록 페이지에서 발견한 후보. 원문 확인 전 미검증.",
                region=source.region,
                status="trend_signal" if source.role == "trend" else "discovery_only",
                source_role=source.role,
                published_at=published_at,
            )
        )
    # Listings are not always rendered in publication order. Sort the page's
    # candidates before applying its batch limit so a newer item lower in the
    # HTML does not lose to an older item near the top.
    candidates.sort(key=lambda item: _publication_order(item.published_at))
    return candidates[:limit]


def _respect_interval(url: str, seconds: float) -> None:
    pace_host(url, seconds)


def _get_with_backoff(client: httpx.Client, url: str, min_interval: float) -> httpx.Response:
    """Respect per-host pacing and server Retry-After; never retry auth/block responses."""
    for attempt in range(3):
        _respect_interval(url, min_interval)
        response = client.get(url)
        if response.status_code not in {429, 503} or attempt == 2:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = min(max(float(retry_after), min_interval), 300.0)
        except ValueError:
            delay = min(2**attempt * max(min_interval, 1.0), 60.0)
        time.sleep(delay)
    raise AssertionError("unreachable")


def rank_candidates(
    items: list[DiscoveryItem],
    region_policy: str = "mixed",
) -> list[DiscoveryItem]:
    """Rank all leads newest-first, regardless of region or source.

    ``region_policy`` remains accepted for compatibility, but is ignored.
    """
    del region_policy
    return sorted(items, key=lambda item: _publication_order(item.published_at))


def collect_candidates(
    limit: int = 20,
    timeout: float = 15.0,
    region_policy: str = "mixed",
    content_type: str | None = None,
    work_title: str | None = None,
    source_ids: set[str] | None = None,
    unfiltered_sources: set[str] | None = None,
    source_report: list[dict[str, object]] | None = None,
    birthday_catalog: list[DiscoveryItem] | None = None,
    exhaustive_listing: bool = False,
    known_candidate_ids_by_source: dict[str, set[str]] | None = None,
) -> tuple[list[DiscoveryItem], list[str]]:
    """활성 출처 중 선택 유형을 지원하는 곳에서 미검증 후보만 수집합니다."""
    errors: list[str] = []
    items: list[DiscoveryItem] = []
    active = _collectable_sources_for_type(content_type, source_ids)
    known_candidate_ids_by_source = known_candidate_ids_by_source or {}
    latest_first_sources = {
        "eeo_events",
        "daewonmedia_kr",
        "src_www_toei_anim_co_jp",
    }
    headers = {"User-Agent": "me-nu-content-agent/0.3 (https://github.com/Por0ri/me-and-nu)"}
    robots_cache = {}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for rss_source in active.values():
            source_item_count = len(items)
            source_error_count = len(errors)
            attempted = False
            pages_scanned = 0
            page_candidate_counts: list[int] = []
            duplicate_candidate_count = 0
            listing_stop_reason = "not_applicable"
            observed_listing_items: list[DiscoveryItem] = []
            try:
                if rss_source.collection in {"rss", "youtube_rss"}:
                    if rss_source.collection == "youtube_rss" and rss_source.feed_url is None:
                        errors.append(
                            f"{rss_source.id}: YouTube 채널 ID가 등록된 feed_url 설정이 필요합니다."
                        )
                        continue
                    feed_url = str(rss_source.feed_url or rss_source.url)
                    attempted = True
                    response = _get_with_backoff(client, feed_url, rss_source.min_interval_seconds)
                    if rss_source.id == "anime_schedule_rss":
                        items.extend(parse_anime_schedule(response.content))
                    else:
                        items.extend(
                            parse_release_rss(
                                response.content,
                                rss_source.id,
                                rss_source.region,
                                rss_source.role,
                            )
                        )
                elif rss_source.collection == "html":
                    page_url = str(rss_source.url)
                    if rss_source.category == "onnada_character_catalog":
                        now = datetime.now(ZoneInfo("Asia/Seoul"))
                        query = urlencode({"y": now.year, "m": now.month})
                        birthday_url = f"{page_url.rstrip('/')}/character/birthday?{query}"
                        attempted = True
                        html, _ = _get_html_with_client(
                            client, birthday_url, rss_source, timeout, robots_cache
                        )
                        monthly_birthdays = parse_onnada_birthday_listing(
                            html,
                            rss_source,
                            # Keep the durable calendar complete even though the
                            # downstream generation candidate batch is bounded.
                            limit=1000,
                            work_title=None,
                        )
                        if birthday_catalog is not None:
                            birthday_catalog.extend(monthly_birthdays)
                        candidate_birthdays = (
                            monthly_birthdays
                            if unfiltered_sources and rss_source.id in unfiltered_sources
                            else parse_onnada_birthday_listing(
                                html,
                                rss_source,
                                limit=limit,
                                work_title=work_title,
                            )
                        )
                        items.extend(candidate_birthdays)
                    elif rss_source.category == "ann_encyclopedia" and work_title:
                        only = "person" if content_type == "people" else "anime"
                        page_url = f"{page_url}?{urlencode({'only': only, 'q': work_title})}"
                        attempted = True
                        html, _ = _get_html_with_client(
                            client, page_url, rss_source, timeout, robots_cache
                        )
                        items.extend(
                            parse_ann_search(
                                html,
                                rss_source,
                                content_type=content_type or "guide",
                                limit=limit,
                            )
                        )
                    elif rss_source.category == "ann_encyclopedia":
                        continue
                    else:
                        attempted = True
                        seen_source_urls: set[str] = set()
                        seen_page_urls: set[str] = set()
                        listing_url = page_url
                        while listing_url:
                            normalized_listing_url = urlparse(listing_url)._replace(
                                fragment=""
                            ).geturl()
                            if normalized_listing_url in seen_page_urls:
                                listing_stop_reason = "repeated_page_url"
                                break
                            seen_page_urls.add(normalized_listing_url)
                            html, _ = _get_html_with_client(
                                client, listing_url, rss_source, timeout, robots_cache
                            )
                            # `limit` is a downstream candidate batch size. During
                            # exhaustive discovery, inspect the whole listing page so
                            # that batch sizing does not silently discard its tail.
                            page_limit = (
                                max(limit, MAX_LISTING_CANDIDATES_PER_PAGE)
                                if exhaustive_listing
                                else limit
                            )
                            page_items = parse_html_listing(
                                html, rss_source, limit=page_limit
                            )
                            pages_scanned += 1
                            page_candidate_counts.append(len(page_items))
                            observed_listing_items.extend(page_items)
                            # Discovery follows the source archive regardless of article age.
                            # Time validity is checked only after source facts are captured.
                            page_new_count = 0
                            reached_known_candidate = False
                            known_ids = known_candidate_ids_by_source.get(
                                rss_source.id, set()
                            )
                            for item in page_items:
                                candidate_url = str(item.url)
                                if (
                                    rss_source.id in latest_first_sources
                                    and (item.id in known_ids or candidate_url in known_ids)
                                ):
                                    reached_known_candidate = True
                                    break
                                if candidate_url in seen_source_urls:
                                    duplicate_candidate_count += 1
                                    continue
                                seen_source_urls.add(candidate_url)
                                items.append(item)
                                page_new_count += 1
                            if reached_known_candidate:
                                listing_stop_reason = "known_candidate_overlap"
                                break
                            if rss_source.id in latest_first_sources and not known_ids:
                                # Establish the cursor from the newest page on the
                                # first pass; later passes stop at its known overlap.
                                listing_stop_reason = "latest_first_initial_page"
                                break
                            if page_new_count == 0:
                                listing_stop_reason = "no_new_candidates"
                                break
                            if not exhaustive_listing and len(seen_source_urls) >= limit:
                                listing_stop_reason = "requested_limit_reached"
                                break
                            next_url = _next_listing_url(html, listing_url, rss_source)
                            if not next_url:
                                listing_stop_reason = "no_next_page_link"
                                break
                            listing_url = next_url
                elif rss_source.collection == "api" and rss_source.role == "reference":
                    if rss_source.category == "kitsu_anime_catalog":
                        if content_type == "curation":
                            attempted = True
                            _respect_interval(str(rss_source.url), rss_source.min_interval_seconds)
                            response = client.get(
                                str(rss_source.url),
                                params={
                                    "sort": "-averageRating",
                                    "page[limit]": min(limit, 20),
                                },
                            )
                            response.raise_for_status()
                            items.extend(
                                parse_kitsu_rating_ranking(response.json(), rss_source, limit)
                            )
                            continue
                        if not work_title:
                            continue
                        attempted = True
                        _respect_interval(str(rss_source.url), rss_source.min_interval_seconds)
                        response = client.get(
                            str(rss_source.url),
                            params={
                                "filter[text]": work_title,
                                "page[limit]": min(limit, 3 if content_type == "character" else 10),
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if content_type == "character":
                            anime_rows = payload.get("data", [])
                            for anime in anime_rows[:3]:
                                anime_id = str(anime.get("id", ""))
                                if not anime_id.isdigit():
                                    continue
                                character_url = (
                                    f"https://kitsu.io/api/edge/anime/{anime_id}/characters"
                                )
                                _respect_interval(character_url, rss_source.min_interval_seconds)
                                character_response = client.get(
                                    character_url,
                                    params={"include": "character", "page[limit]": 20},
                                )
                                character_response.raise_for_status()
                                items.extend(
                                    parse_kitsu_characters(
                                        character_response.json(),
                                        rss_source,
                                        anime,
                                        limit,
                                    )
                                )
                                if len(items) >= limit:
                                    break
                        else:
                            items.extend(parse_kitsu_search(payload, rss_source, limit))
                    elif rss_source.category == "wikipedia_reference":
                        if not work_title:
                            continue
                        attempted = True
                        suffix = " anime" if rss_source.region == "global" else " アニメ"
                        _respect_interval(str(rss_source.url), rss_source.min_interval_seconds)
                        response = client.get(
                            str(rss_source.url),
                            params={
                                "action": "query",
                                "list": "search",
                                "srsearch": f"{work_title}{suffix}",
                                "srlimit": min(limit, 10),
                                "format": "json",
                                "formatversion": "2",
                            },
                        )
                        response.raise_for_status()
                        items.extend(parse_wikipedia_search(response.json(), rss_source, limit))
                    elif rss_source.category == "wikidata_character_catalog":
                        if not work_title:
                            continue
                        attempted = True
                        title_literal = json.dumps(work_title, ensure_ascii=False)
                        query = f"""
                        SELECT DISTINCT ?work ?workLabel ?character ?characterLabel WHERE {{
                          VALUES ?titleLabel {{ {title_literal}@en {title_literal}@ja }}
                          ?work rdfs:label ?titleLabel;
                                wdt:P674 ?character.
                          SERVICE wikibase:label {{
                            bd:serviceParam wikibase:language "en,ja".
                          }}
                        }}
                        LIMIT {min(max(limit, 1), 50)}
                        """
                        _respect_interval(str(rss_source.url), rss_source.min_interval_seconds)
                        response = client.get(
                            str(rss_source.url),
                            params={"query": query, "format": "json", "maxlag": 1},
                            headers={"Accept": "application/sparql-results+json"},
                        )
                        response.raise_for_status()
                        items.extend(
                            parse_wikidata_characters(response.json(), rss_source, limit)
                        )
                elif rss_source.collection == "api" and rss_source.category == "community_trend":
                    timeline_url = str(rss_source.url)
                    if work_title:
                        # Mastodon's public tag endpoint supports work-specific timelines.
                        # Strip separators because hashtags cannot contain whitespace.
                        work_tag = re.sub(
                            r"[^\w가-힣ぁ-んァ-ヶ一-龯]", "", work_title, flags=re.UNICODE
                        )
                        if work_tag:
                            parsed_source_url = urlparse(timeline_url)
                            timeline_url = (
                                f"{parsed_source_url.scheme}://{parsed_source_url.netloc}"
                                f"/api/v1/timelines/tag/{quote(work_tag, safe='')}"
                            )
                    attempted = True
                    response = _get_with_backoff(
                        client,
                        timeline_url,
                        rss_source.min_interval_seconds,
                    )
                    source_for_parse = rss_source.model_copy(update={"url": timeline_url})
                    timeline = response.json()
                    if isinstance(timeline, list):
                        enriched = []
                        context_requests = 0
                        for status in timeline:
                            if (
                                isinstance(status, dict)
                                and _thread_worth_fetching(status)
                                and context_requests < MAX_MASTODON_THREAD_LOOKUPS
                            ):
                                status = dict(status)
                                status["community_posts"] = _mastodon_thread_posts(
                                    client, rss_source, status
                                )
                                context_requests += 1
                            enriched.append(status)
                        items.extend(parse_mastodon_tag_timeline(enriched, source_for_parse))
                elif rss_source.collection == "manual":
                    continue
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{rss_source.id}: {exc}")
                if pages_scanned:
                    listing_stop_reason = "page_fetch_error"
            finally:
                if source_report is not None:
                    source_errors = errors[source_error_count:]
                    source_candidate_count = len(items) - source_item_count
                    source_publication_dates = [
                        item.published_at
                        for item in (
                            [*observed_listing_items, *items[source_item_count:]]
                        )
                        if item.published_at
                    ]
                    latest_published_at = (
                        min(source_publication_dates, key=_publication_order)
                        if source_publication_dates
                        else None
                    )
                    source_report.append(
                        {
                            "source_id": rss_source.id,
                            "status": (
                                "partial"
                                if source_errors and pages_scanned and source_candidate_count
                                else "failed"
                                if source_errors
                                else "succeeded"
                                if attempted and source_candidate_count > 0
                                else "succeeded_empty"
                                if attempted
                                else "skipped_needs_work_title"
                            ),
                            "attempted": attempted,
                            "candidate_count": source_candidate_count,
                            "latest_published_at": latest_published_at,
                            "pages_scanned": pages_scanned,
                            "page_candidate_counts": page_candidate_counts,
                            "duplicate_candidate_count": duplicate_candidate_count,
                            "pagination_stop_reason": listing_stop_reason,
                            "error": " | ".join(source_errors),
                        }
                    )

        query = """
        query Upcoming($perPage: Int, $startDate: FuzzyDateInt) {
          Page(page: 1, perPage: $perPage) {
            media(
              type: ANIME,
              status: NOT_YET_RELEASED,
              countryOfOrigin: JP,
              startDate_greater: $startDate,
              sort: [START_DATE, POPULARITY_DESC]
            ) {
              id
              title { romaji english native }
              siteUrl
              startDate { year month day }
            }
          }
        }
        """
        api_source = active.get("anilist")
        if api_source and api_source.collection == "api":
            source_item_count = len(items)
            source_error_count = len(errors)
            attempted = False
            try:
                attempted = True
                _respect_interval(str(api_source.url), api_source.min_interval_seconds)
                response = client.post(
                    str(api_source.url),
                    json={
                        "query": query,
                        "variables": {
                            "perPage": min(limit, 50),
                            "startDate": int(
                                datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
                            ),
                        },
                    },
                )
                response.raise_for_status()
                items.extend(parse_anilist(response.json()))
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"anilist: {exc}")
            finally:
                if source_report is not None:
                    source_errors = errors[source_error_count:]
                    source_report.append(
                        {
                            "source_id": api_source.id,
                            "status": (
                                "failed"
                                if source_errors
                                else "succeeded"
                                if attempted and len(items) > source_item_count
                                else "succeeded_empty"
                            ),
                            "attempted": attempted,
                            "candidate_count": len(items) - source_item_count,
                            "error": " | ".join(source_errors),
                        }
                    )

    unique: dict[str, DiscoveryItem] = {}
    for item in items:
        if content_type is not None:
            source = active.get(item.source_id)
            if source is None:
                continue
            inferred_types = _inferred_candidate_types(item, source)
            if content_type not in inferred_types:
                continue
            item = item.model_copy(update={"content_types": sorted(inferred_types)})
        unique.setdefault(str(item.url), item)
    ranked = rank_candidates(list(unique.values()), region_policy=region_policy)
    return (ranked if exhaustive_listing else ranked[:limit]), errors
