"""LLM 비용 없이 최신 방영·신작 후보를 찾는 수집기."""

import time
from datetime import datetime
from hashlib import sha1
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl

from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import _get_html
from ani_menu_agent.sources.registry import load_source_registry


class DiscoveryItem(BaseModel):
    id: str
    title: str
    url: HttpUrl
    source_id: str
    published_at: str | None = None
    summary: str = ""
    status: str = "discovery_only"
    region: str = "global"
    source_role: str = "discovery"


def _item_id(source_id: str, value: str) -> str:
    return f"{source_id}-{sha1(value.encode('utf-8')).hexdigest()[:12]}"


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
                published_at=entry.get("published"),
                summary=summary,
                region=region,
                source_role=source_role,
            )
        )
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
    source_path = source_parsed.path.rstrip("/").lower()
    for container in soup.select("article, .post, .news, .item, li"):
        for anchor in container.select("a[href]"):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href")
            if not title or not href or len(title) < 8:
                continue
            if len(title) % 2 == 0 and title[: len(title) // 2] == title[len(title) // 2 :]:
                title = title[: len(title) // 2].strip()
            url = str(anchor["href"])
            if url.startswith("/"):
                url = urljoin(str(source.url), url)
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != source_host or url in seen:
                continue
            # Listing-only links (navigation, pagination, tags) are not useful leads.
            path = parsed.path.lower()
            if source_path and not path.startswith(f"{source_path}/"):
                continue
            if path in {"", "/", "/news", "/news/"} or any(
                token in path for token in ("/tag/", "/category/", "/page/")
            ):
                continue
            seen.add(url)
            candidates.append(
                DiscoveryItem(
                    id=_item_id(source.id, url),
                    title=title[:300],
                    url=url,
                    source_id=source.id,
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
    return sorted(
        items,
        key=lambda item: (
            region_order.get(item.region, 3),
            item.source_id != "anilist",
            item.published_at or "9999-99-99",
        ),
    )


def collect_candidates(
    limit: int = 20,
    timeout: float = 15.0,
    region_policy: str = "kr_first",
) -> tuple[list[DiscoveryItem], list[str]]:
    """활성화된 출처의 RSS/API/HTML 연결기를 통해 미검증 후보만 수집합니다."""
    errors: list[str] = []
    items: list[DiscoveryItem] = []
    active = {source.id: source for source in load_source_registry().collectable_sources}
    headers = {"User-Agent": "ani-menu-agent/0.2 (+local development)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for rss_source in active.values():
            if rss_source.role == "primary" and rss_source.collection != "rss":
                continue
            try:
                if rss_source.collection in {"rss", "youtube_rss"}:
                    if rss_source.collection == "youtube_rss" and rss_source.feed_url is None:
                        errors.append(
                            f"{rss_source.id}: YouTube 채널 ID가 등록된 feed_url 설정이 필요합니다."
                        )
                        continue
                    feed_url = str(rss_source.feed_url or rss_source.url)
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
                    html, _ = _get_html(str(rss_source.url), rss_source, timeout=timeout)
                    items.extend(parse_html_listing(html, rss_source, limit=limit))
                elif rss_source.collection == "manual":
                    continue
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{rss_source.id}: {exc}")

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
            try:
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

    unique: dict[str, DiscoveryItem] = {}
    for item in items:
        unique.setdefault(str(item.url), item)
    ranked = rank_candidates(list(unique.values()), region_policy=region_policy)
    return ranked[:limit], errors
