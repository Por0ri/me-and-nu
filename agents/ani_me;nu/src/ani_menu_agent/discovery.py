"""LLM 비용 없이 최신 방영·신작 후보를 찾는 수집기."""

import json
import re
import time
from collections import Counter
from datetime import datetime
from functools import lru_cache
from hashlib import sha1
from urllib.parse import quote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.models import CONTENT_TYPE_LABELS
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import _get_html
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
        "review": "review",
        "curation": "curation",
        "community_trend": "humor",
        "music_label": "music",
        "goods": "goods",
        "event": "event",
        "korea_entertainment_trade_news": "news",
        "korea_official_news": "news",
        "kitsu_anime_catalog": "guide",
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
        "review",
        "curation",
        "event",
        "goods",
        "korea_entertainment_trade_news",
        "korea_official_news",
    }:
        inferred.discard("news")
    if not inferred and "news" in available and source.role in {"primary", "discovery"}:
        inferred.add("news")
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
    normalized_text = _normalized_tag(text)
    matches: list[tuple[int, str]] = []
    for work in load_work_catalog():
        aliases = [
            str(work.get(key) or "")
            for key in ("title_ko", "title_romaji", "title_english", "title_native")
        ]
        if any(
            len(_normalized_tag(alias)) >= 3 and _normalized_tag(alias) in normalized_text
            for alias in aliases
            if alias.strip()
        ):
            matches.append(
                (
                    int(work["menu_work_id"]),
                    str(work.get("title_ko") or work.get("title_romaji")),
                )
            )
    return [title for _, title in sorted(matches)]


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
        # Replies and boosts often lose context or duplicate another author's post.
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
        if (
            not work_titles
            or normalized_tags & news_tags
            or len(body) < 40
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
        "index.html",
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
    if source.id == "recal_kr" and source_path == "/ko/e/offline":
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
        title = " ".join(anchor.get_text(" ", strip=True).split())
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
        normalized_url = parsed._replace(
            path=parsed.path.rstrip("/"), query="", fragment=""
        ).geturl()
        if normalized_url in seen:
            continue
        path = parsed.path.rstrip("/").lower()
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
        if not path or path == source_page_path:
            continue
        if source_path and not path.startswith(f"{source_path}/"):
            continue
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < minimum_path_segments or segments[-1] in listing_only_segments:
            continue
        if re.search(r"/(?:page|tag|category|author)(?:/|$)", path):
            continue
        seen.add(normalized_url)
        candidates.append(
            DiscoveryItem(
                id=_item_id(source.id, normalized_url),
                title=title[:300],
                url=normalized_url,
                source_id=source.id,
                content_types=[item.value for item in source.content_types],
                summary="목록 페이지에서 발견한 후보. 원문 확인 전 미검증.",
                region=source.region,
                status="trend_signal" if source.role == "trend" else "discovery_only",
                source_role=source.role,
            )
        )
        if len(candidates) >= limit:
            return candidates
    return candidates


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
    region_policy: str = "kr_first",
) -> list[DiscoveryItem]:
    priorities = {
        "kr_first": {"KR": 0, "JP": 1, "global": 2},
        "mixed": {"KR": 0, "JP": 0, "global": 1},
        "jp_first": {"JP": 0, "KR": 1, "global": 2},
    }
    region_order = priorities.get(region_policy, priorities["kr_first"])
    ordered = sorted(
        items,
        key=lambda item: (
            region_order.get(item.region, 3),
            item.source_id != "anilist",
            item.published_at or "9999-99-99",
        ),
    )
    # Interleave both publishers and explicitly resolvable works. This is a
    # soft ordering preference, not a per-work quota: if only one work has
    # usable current candidates, all of its candidates remain available.
    registry = {source.id: source for source in load_source_registry().sources}

    def work_key(item: DiscoveryItem) -> str:
        matched = resolve_title_work_ids(item.title)
        if not matched:
            source = registry.get(item.source_id)
            if source:
                for source_title in (source.work_title_ko, source.work_title_original):
                    if source_title:
                        matched = resolve_title_work_ids(source_title)
                        if matched:
                            break
        return ":".join(str(value) for value in matched) if matched else f"unresolved:{item.id}"

    by_source: dict[str, list[DiscoveryItem]] = {}
    for item in ordered:
        by_source.setdefault(item.source_id, []).append(item)

    for source_id, source_items in by_source.items():
        by_work: dict[str, list[DiscoveryItem]] = {}
        for item in source_items:
            by_work.setdefault(work_key(item), []).append(item)
        interleaved: list[DiscoveryItem] = []
        while by_work:
            for work_id in list(by_work):
                interleaved.append(by_work[work_id].pop(0))
                if not by_work[work_id]:
                    del by_work[work_id]
        by_source[source_id] = interleaved

    ranked: list[DiscoveryItem] = []
    while by_source:
        for source_id in list(by_source):
            ranked.append(by_source[source_id].pop(0))
            if not by_source[source_id]:
                del by_source[source_id]
    return ranked


def collect_candidates(
    limit: int = 20,
    timeout: float = 15.0,
    region_policy: str = "kr_first",
    content_type: str | None = None,
    work_title: str | None = None,
    source_ids: set[str] | None = None,
    source_report: list[dict[str, object]] | None = None,
) -> tuple[list[DiscoveryItem], list[str]]:
    """활성 출처 중 선택 유형을 지원하는 곳에서 미검증 후보만 수집합니다."""
    errors: list[str] = []
    items: list[DiscoveryItem] = []
    active = _collectable_sources_for_type(content_type, source_ids)
    headers = {"User-Agent": "me-nu-content-agent/0.3 (https://github.com/Por0ri/me-and-nu)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for rss_source in active.values():
            source_item_count = len(items)
            source_error_count = len(errors)
            attempted = False
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
                    if rss_source.category == "ann_encyclopedia" and work_title:
                        only = "person" if content_type == "people" else "anime"
                        page_url = f"{page_url}?{urlencode({'only': only, 'q': work_title})}"
                        attempted = True
                        html, _ = _get_html(page_url, rss_source, timeout=timeout)
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
                        html, _ = _get_html(page_url, rss_source, timeout=timeout)
                        items.extend(parse_html_listing(html, rss_source, limit=limit))
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
                    items.extend(parse_mastodon_tag_timeline(response.json(), source_for_parse))
                elif rss_source.collection == "manual":
                    continue
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{rss_source.id}: {exc}")
            finally:
                if source_report is not None:
                    source_errors = errors[source_error_count:]
                    source_report.append(
                        {
                            "source_id": rss_source.id,
                            "status": (
                                "failed"
                                if source_errors
                                else "succeeded"
                                if attempted and len(items) > source_item_count
                                else "succeeded_empty"
                                if attempted
                                else "skipped_needs_work_title"
                            ),
                            "attempted": attempted,
                            "candidate_count": len(items) - source_item_count,
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
    return ranked[:limit], errors
