"""사용 조건이 승인된 1차 HTML 출처에서 본문과 이미지 URL 후보를 수집합니다."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source

MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
MAX_IMAGE_URLS = 30
MAX_RELATED_IMAGE_PAGES = 4


def _published_date_from_html(soup: BeautifulSoup) -> str | None:
    """Read an explicit publication timestamp from standard article metadata."""
    for attrs in (
        {"property": "article:published_time"},
        {"name": "datePublished"},
        {"itemprop": "datePublished"},
        {"name": "publish_date"},
        {"name": "pubdate"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()

    # JSON-LD is common on news pages, including sites that omit Open Graph dates.
    def find_date_published(value: object) -> str | None:
        if isinstance(value, dict):
            for key in ("datePublished", "dateCreated"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()
            for item in value.values():
                found = find_date_published(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = find_date_published(item)
                if found:
                    return found
        return None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if raw:
            try:
                found = find_date_published(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                found = None
            if found:
                return found

    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return str(time_tag["datetime"]).strip()

    # Some publisher templates put the original date in a dedicated date class.
    # Restrict matching to date-labelled nodes so event dates in article prose
    # cannot be mistaken for the publication date.
    date_node = soup.find(
        lambda tag: (
            tag.name in {"time", "span", "p", "div"}
            and any(
                re.search(r"(?:publish|article|news|date|time)", str(value), re.IGNORECASE)
                for value in [tag.get("class", []), tag.get("id", "")]
            )
        )
    )
    if date_node:
        text = date_node.get_text(" ", strip=True)
        match = re.search(
            r"(?<!\d)(20\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})日?",
            text,
        )
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    # A few official templates place the publication stamp immediately before
    # the article headline but give it an opaque generated CSS class. Accept an
    # exact date token only in that heading-adjacent region, never from body copy.
    headline = soup.find("h1")
    if headline:
        for node in headline.find_all_previous(["p", "span", "div", "time"])[:12]:
            text = node.get_text(" ", strip=True)
            match = re.fullmatch(
                r"(?:20\d{2}[./年-]\s*\d{1,2}[./月-]\s*\d{1,2}日?)",
                text,
            )
            if match:
                parts = re.search(r"(20\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})", text)
                if parts:
                    return (
                        f"{int(parts.group(1)):04d}-{int(parts.group(2)):02d}-"
                        f"{int(parts.group(3)):02d}"
                    )
    return None


class SourcePageCapture(BaseModel):
    source_id: str
    title: str
    url: HttpUrl
    fetched_at: str
    published_at: str | None = None
    text: str
    image_urls: list[HttpUrl]
    image_alt_text: dict[str, str] = Field(default_factory=dict)
    image_source_pages: dict[str, str] = Field(default_factory=dict)
    related_image_pages: list[HttpUrl] = Field(default_factory=list)
    excerpt_truncated: bool = False
    status: str = "unverified_capture"


def _approved_hosts(source: Source) -> set[str]:
    return {
        (urlparse(str(source.url)).hostname or "").lower().rstrip("."),
        *[host.lower().rstrip(".") for host in source.allowed_asset_hosts],
    }


def _is_decorative_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    filename = path.rsplit("/", maxsplit=1)[-1]
    return (
        path.endswith(".svg")
        or path.startswith("/assets/images/common/")
        or any(token in filename for token in ("logo", "icon", "favicon", "search", "btn_menu"))
    )


def _validate_url(url: str, source: Source) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("수집 URL은 HTTPS여야 합니다.")
    host = parsed.hostname.lower().rstrip(".")
    if not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in _approved_hosts(source)
    ):
        raise ValueError(f"등록된 출처 도메인 밖의 URL입니다: {host}")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError(f"출처 주소를 확인할 수 없습니다: {host}") from exc
    non_public = any(
        not ipaddress.ip_address(address.split("%", 1)[0]).is_global for address in addresses
    )
    if not addresses or non_public:
        raise ValueError("내부 네트워크 주소로 연결되는 출처는 수집할 수 없습니다.")


def _get_html(url: str, source: Source, timeout: float) -> tuple[str, str]:
    headers = {"User-Agent": "me-nu-content-agent/0.3 (+approved-source collector)"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        current_url = url
        for _ in range(4):
            _validate_url(current_url, source)
            _check_robots(client, current_url, source)
            pace_host(current_url, source.min_interval_seconds)
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("출처 페이지의 리다이렉트 주소가 비어 있습니다.")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise ValueError(f"HTML 페이지가 아닙니다: {content_type or 'unknown'}")
                length = response.headers.get("content-length")
                if length and int(length) > MAX_PAGE_BYTES:
                    raise ValueError("페이지가 5MB 제한을 초과합니다.")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_PAGE_BYTES:
                        raise ValueError("페이지가 5MB 제한을 초과합니다.")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), current_url
    raise ValueError("페이지 리다이렉트 횟수가 제한을 초과했습니다.")


def _check_robots(client: httpx.Client, url: str, source: Source) -> None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    _validate_url(robots_url, source)
    try:
        pace_host(robots_url, source.min_interval_seconds)
        with client.stream("GET", robots_url) as response:
            if response.status_code in {404, 410}:
                return
            if response.status_code != 200:
                raise ValueError(f"robots.txt 확인 실패: HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_ROBOTS_BYTES:
                    raise ValueError("robots.txt가 256KB 제한을 초과합니다.")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ValueError(f"robots.txt를 확인할 수 없어 수집을 중단했습니다: {exc}") from exc
    parser = RobotFileParser(robots_url)
    parser.parse(b"".join(chunks).decode("utf-8", errors="replace").splitlines())
    if not parser.can_fetch("me-nu-content-agent", url):
        raise ValueError("robots.txt에서 이 페이지 자동 수집을 허용하지 않습니다.")


def _image_candidates(soup: BeautifulSoup, page_url: str) -> tuple[list[str], dict[str, str]]:
    """Extract article visuals, not unrelated navigation or recommendation images."""
    scores: dict[str, int] = {}
    descriptions: dict[str, str] = {}
    scope = _article_content_scope(soup)

    scoped_images = scope.find_all("img")
    scoped_urls = {
        urljoin(page_url, str(value))
        for tag in scoped_images
        for value in (
            tag.get("src"),
            tag.get("data-src"),
            tag.get("data-original"),
            tag.get("data-lazy-src"),
        )
        if value
    }
    if scope is soup or not scoped_urls:
        for tag in soup.find_all("meta", attrs={"property": "og:image"}):
            image_url = tag.get("content")
            if image_url:
                absolute_url = urljoin(page_url, str(image_url))
                scores[absolute_url] = max(scores.get(absolute_url, 0), 100)
    for tag in scoped_images:
        if _is_linked_to_another_news_article(tag, page_url):
            continue
        declared_size = _positive_dimension(tag.get("width")) * _positive_dimension(
            tag.get("height")
        )
        base_score = min(40, int(declared_size**0.5 / 20))
        candidates = [
            tag.get("src"),
            tag.get("data-src"),
            tag.get("data-original"),
            tag.get("data-lazy-src"),
        ]
        for srcset_key in ("srcset", "data-srcset"):
            srcset = tag.get(srcset_key) or ""
            # Prefer the largest declared rendition; descriptor values are hints only.
            entries = [item.strip().split() for item in str(srcset).split(",") if item.strip()]
            entries.sort(key=lambda item: _srcset_width(item[-1] if len(item) > 1 else ""))
            candidates.extend(
                (item[0], min(60, _srcset_width(item[-1] if len(item) > 1 else "") // 20))
                for item in reversed(entries)
            )
        description = str(tag.get("alt") or tag.get("title") or "").strip()
        for candidate in candidates:
            if not candidate:
                continue
            candidate_url, size_score = (
                candidate if isinstance(candidate, tuple) else (candidate, 0)
            )
            absolute_url = urljoin(page_url, str(candidate_url))
            parsed_candidate = urlparse(absolute_url)
            path = urlparse(absolute_url).path.casefold()
            score = base_score + size_score
            if any(token in path for token in ("original", "large", "main", "visual", "kv")):
                score += 20
            if any(token in path for token in ("thumb", "small", "icon", "logo", "avatar")):
                score -= 25
            # Some official CMS pages expose only a small rendition in <img src>
            # (for example /w707), even though the same asset endpoint serves a
            # larger /w1200 rendition. Prefer that source-sized variant so cards
            # meet the 4x carousel output target without inventing/upscaling pixels.
            if re.search(r"/w\d+/?$", parsed_candidate.path, flags=re.IGNORECASE):
                larger_path = re.sub(
                    r"/w\d+/?$", "/w1200", parsed_candidate.path, flags=re.IGNORECASE
                )
                absolute_url = parsed_candidate._replace(path=larger_path).geturl()
                score += 5
            scores[absolute_url] = max(scores.get(absolute_url, 0), score)
            if description:
                descriptions[absolute_url] = description[:300]
    # Apply the CMS rendition upgrade consistently to metadata/OG images too;
    # those can otherwise re-introduce a small duplicate before the article body
    # image with the larger URL is reached.
    normalized_scores: dict[str, int] = {}
    normalized_descriptions: dict[str, str] = {}
    for candidate, score in scores.items():
        parsed_candidate = urlparse(candidate)
        if re.search(r"/w\d+/?$", parsed_candidate.path, flags=re.IGNORECASE):
            candidate = parsed_candidate._replace(
                path=re.sub(r"/w\d+/?$", "/w1200", parsed_candidate.path, flags=re.IGNORECASE)
            ).geturl()
            score += 5
        normalized_scores[candidate] = max(normalized_scores.get(candidate, 0), score)
        if candidate in descriptions:
            normalized_descriptions[candidate] = descriptions[candidate]
    return (
        sorted(normalized_scores, key=lambda item: (-normalized_scores[item], item)),
        normalized_descriptions,
    )


def _is_linked_to_another_news_article(tag, page_url: str) -> bool:
    """Exclude related-news cards embedded inside otherwise valid article scopes."""
    anchor = tag.find_parent("a", href=True)
    if anchor is None:
        return False
    linked = urlparse(urljoin(page_url, str(anchor.get("href", ""))))
    current = urlparse(page_url)
    if (linked.hostname or "").lower() != (current.hostname or "").lower():
        return False
    linked_path = linked.path.rstrip("/").lower()
    current_path = current.path.rstrip("/").lower()
    return (
        linked_path != current_path
        and "/news/" in linked_path
        and not linked_path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    )


def _article_content_scope(soup: BeautifulSoup):
    """Prefer article-body containers over broad main/article wrappers with related content."""
    for selector in (
        ".news_detail_cont",
        ".entry-content",
        ".post-content",
        ".article-content",
        ".article-body",
        "article",
        '[role="main"]',
        "main",
    ):
        candidate_scope = soup.select_one(selector)
        if candidate_scope and candidate_scope.find("img"):
            return candidate_scope
    return soup


def _extract_article_text(html: str, page_url: str, soup: BeautifulSoup) -> str:
    """Extract prose from a known article body when the precision parser returns nothing."""
    extracted = (
        trafilatura.extract(
            html,
            url=page_url,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        or ""
    )
    if extracted.strip():
        return extracted

    scope = _article_content_scope(soup)
    # Do not fall back to the whole document: that risks turning navigation,
    # recommendations, or site chrome into apparent article facts.
    if scope is soup:
        return ""
    body = BeautifulSoup(str(scope), "html.parser")
    for tag in body.select("script, style, noscript, iframe, nav, footer, aside, form, button"):
        tag.decompose()
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in body.get_text(separator="\n", strip=True).splitlines()
    ]
    return "\n".join(line for line in lines if line)


def _positive_dimension(value: object) -> int:
    try:
        return max(0, int(str(value).replace("px", "").strip()))
    except (ValueError, TypeError):
        return 0


def _srcset_width(descriptor: str) -> int:
    value = descriptor.strip().lower()
    try:
        return int(value[:-1]) if value.endswith("w") else int(float(value[:-1]) * 1000)
    except (ValueError, IndexError):
        return 0


def _related_image_page_candidates(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Find a few same-site article/gallery pages; never follow off-domain links."""
    current = urlparse(page_url)
    allowed_hosts = {(current.hostname or "").lower().rstrip(".")}
    scored: dict[str, int] = {}
    for anchor in _article_content_scope(soup).select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        candidate = urljoin(page_url, href)
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or not any(host == item or host.endswith(f".{item}") for item in allowed_hosts)
            or parsed.path == current.path
            or parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"))
        ):
            continue
        path_and_text = f"{parsed.path} {anchor.get_text(' ', strip=True)}".casefold()
        score = 0
        if any(
            term in path_and_text
            for term in ("news", "article", "story", "event", "campaign", "goods", "gallery")
        ):
            score += 3
        if any(char.isdigit() for char in parsed.path):
            score += 1
        if len(anchor.get_text(" ", strip=True)) >= 5:
            score += 1
        if score:
            scored[candidate] = max(score, scored.get(candidate, 0))
    return [url for url, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))][
        :MAX_RELATED_IMAGE_PAGES
    ]


def collect_official_page(
    source: Source,
    url: str,
    *,
    timeout: float = 20.0,
    expand_related_pages: bool = True,
) -> SourcePageCapture:
    """자동 수집 허용된 primary/html 출처의 한 페이지를 읽습니다."""
    if source.status != "active" or source.role != "primary" or source.collection != "html":
        raise ValueError(
            "HTML 자동 수집은 status=active, role=primary, collection=html인 출처만 허용됩니다."
        )
    html, final_url = _get_html(url, source, timeout)
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for selector in (
        {"property": "og:title"},
        {"name": "twitter:title"},
    ):
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            title = str(tag["content"]).strip()
            break
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    published_at = _published_date_from_html(soup)

    extracted = _extract_article_text(html, final_url, soup)
    image_urls, image_alt_text = _image_candidates(soup, final_url)
    seed_image_urls = image_urls
    image_urls = seed_image_urls[:3]
    image_source_pages = {candidate: final_url for candidate in image_urls}
    related_candidates = (
        _related_image_page_candidates(soup, final_url) if expand_related_pages else []
    )
    related_pages: list[str] = []
    related_image_urls: list[str] = []
    for related_url in related_candidates:
        try:
            related_html, related_final_url = _get_html(related_url, source, timeout)
        except (httpx.HTTPError, ValueError):
            # One inaccessible related page should not discard evidence from the seed page.
            continue
        related_soup = BeautifulSoup(related_html, "html.parser")
        related_pages.append(related_final_url)
        related_urls, related_alt_text = _image_candidates(related_soup, related_final_url)
        related_urls = related_urls[:10]
        for candidate in related_urls:
            image_source_pages.setdefault(candidate, related_final_url)
        related_image_urls.extend(related_urls)
        image_alt_text.update(related_alt_text)
    # Ensure linked article/gallery assets enter the saved candidate pool before the
    # low-priority remainder of a news listing can consume the CLI's image limit.
    image_urls.extend(related_image_urls)
    image_urls.extend(seed_image_urls[3:])

    allowed = _approved_hosts(source)
    unique_images: list[HttpUrl] = []
    seen: set[str] = set()
    for candidate in image_urls:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host:
            continue
        if _is_decorative_image_url(candidate):
            continue
        if not any(host == item or host.endswith(f".{item}") for item in allowed):
            continue
        if candidate not in seen:
            seen.add(candidate)
            unique_images.append(HttpUrl(candidate))
        if len(unique_images) >= MAX_IMAGE_URLS:
            break

    excerpt_limit = 2_000
    return SourcePageCapture(
        source_id=source.id,
        title=(title or final_url)[:300],
        url=final_url,
        fetched_at=datetime.now(UTC).isoformat(),
        published_at=published_at,
        text=extracted[:excerpt_limit],
        image_urls=unique_images,
        image_alt_text={
            str(image_url): image_alt_text[str(image_url)]
            for image_url in unique_images
            if str(image_url) in image_alt_text
        },
        image_source_pages={
            str(image_url): image_source_pages.get(str(image_url), final_url)
            for image_url in unique_images
        },
        related_image_pages=[HttpUrl(page) for page in related_pages],
        excerpt_truncated=len(extracted) > excerpt_limit,
    )
