"""사용 조건이 승인된 1차 HTML 출처에서 본문과 이미지 URL 후보를 수집합니다."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.models import StructuredWorkReference
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source

MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
MAX_IMAGE_URLS = 100
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


class PageOutboundLink(BaseModel):
    url: HttpUrl
    text: str = ""


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
    image_source_titles: dict[str, str] = Field(default_factory=dict)
    outbound_links: list[PageOutboundLink] = Field(default_factory=list)
    structured_work_references: list[StructuredWorkReference] = Field(default_factory=list)
    related_image_pages: list[HttpUrl] = Field(default_factory=list)
    excerpt_truncated: bool = False
    status: str = "unverified_capture"


def _article_outbound_links(
    soup: BeautifulSoup, page_url: str, source_id: str | None = None
) -> list[PageOutboundLink]:
    """Capture external links only from an explicit article body for provenance routing."""
    scope = None
    for selector in (
        '[itemprop="articleBody"]',
        ".news_detail_cont",
        ".entry-content",
        ".post-content",
        ".article-content",
        ".article-body",
        "article",
    ):
        scope = soup.select_one(selector)
        if scope:
            break
    if scope is None and source_id == "recal_kr":
        # RE:CAL renders event records in a Next.js <main> tree rather than an
        # article element. Keep only explicitly labelled organizer/source links.
        scope = soup.select_one("main")
    if scope is None:
        return []

    page_host = (urlparse(page_url).hostname or "").lower().rstrip(".")
    links: list[PageOutboundLink] = []
    seen: set[str] = set()
    for anchor in scope.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        link_text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
        if source_id == "recal_kr" and not re.search(
            r"official|\uacf5\uc2dd|\ucd9c\ucc98|\uacf5\uc9c0|\uc548\ub0b4", link_text, re.I
        ):
            continue
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").lower().rstrip(".")
        sensitive_query_keys = {
            "token", "access_token", "auth", "authorization", "session", "sessionid",
            "csrf", "email", "user", "account", "code", "state",
        }
        query_keys = {key.casefold() for key in parse_qs(parsed.query)}
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or host == page_host
            or query_keys & sensitive_query_keys
            or absolute in seen
        ):
            continue
        seen.add(absolute)
        links.append(
            PageOutboundLink(
                url=absolute,
                text=link_text[:200],
            )
        )
        if len(links) >= 100:
            break
    return links


def _approved_hosts(source: Source) -> set[str]:
    return {
        (urlparse(str(source.url)).hostname or "").lower().rstrip("."),
        *[host.lower().rstrip(".") for host in source.allowed_asset_hosts],
    }


def _page_title(soup: BeautifulSoup) -> str:
    for selector in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()[:300]
    return soup.title.get_text(" ", strip=True)[:300] if soup.title else ""


def _daewon_article_metadata(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Read the headline and publication date from Daewon Media's labelled detail row."""
    detail = soup.select_one(".detail-box .list01")
    if detail is None:
        return None, None
    text = re.sub(r"\s+", " ", detail.get_text(" ", strip=True))
    match = re.search(
        r"\uC81C\uBAA9\s*(.*?)\s*\uB0A0\uC9DC\s*(20\d{2}-\d{2}-\d{2})",
        text,
    )
    if not match:
        return None, None
    headline = match.group(1).strip(" :：")
    return (headline or None), match.group(2)


def _aniplus_exhibition_title(soup: BeautifulSoup, page_url: str) -> str | None:
    """Read the public exhibition title embedded in ANIPLUS's Next.js response."""
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return None
    try:
        payload = json.loads(script.string or script.get_text())
    except (json.JSONDecodeError, TypeError):
        return None
    page_props = payload.get("props", {}).get("pageProps", {})
    queries = page_props.get("dehydratedState", {}).get("queries", [])
    page_idx = urlparse(page_url).path.rstrip("/").rsplit("/", 1)[-1]
    for query in queries:
        query_key = query.get("queryKey", [])
        if not query_key or query_key[0] != "exhibition-list":
            continue
        for item in query.get("state", {}).get("data", {}).get("list", []):
            if str(item.get("idx")) == page_idx and item.get("title"):
                return str(item["title"]).strip()[:300]
    return None


def _structured_work_references(soup: BeautifulSoup) -> list[StructuredWorkReference]:
    """Read only schema.org about/mentions labels, never arbitrary page prose."""
    references: list[StructuredWorkReference] = []
    seen: set[tuple[str, str, str]] = set()

    def add(relation: str, value: object) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str):
                name, url = item.strip(), None
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("alternateName") or "").strip()
                url_value = item.get("url") or item.get("@id")
                url = str(url_value).strip() if isinstance(url_value, str) else None
            else:
                continue
            if not name:
                continue
            key = (relation, name.casefold(), url or "")
            if key in seen:
                continue
            seen.add(key)
            try:
                references.append(
                    StructuredWorkReference(relation=relation, name=name[:500], url=url)
                )
            except ValueError:
                references.append(
                    StructuredWorkReference(relation=relation, name=name[:500])
                )

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key in ("about", "mentions"):
                if key in value:
                    add("about" if key == "about" else "mention", value[key])
            # JSON-LD commonly wraps article objects in @graph. Walk nested
            # containers only; names outside about/mentions are not work signals.
            for key, child in value.items():
                if key in {"@graph", "@included"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            visit(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue

    for node in soup.select('[itemprop="about"], [itemprop="mentions"]'):
        relation = "about" if node.get("itemprop") == "about" else "mention"
        name = str(node.get("content") or node.get_text(" ", strip=True)).strip()
        url = str(node.get("href") or node.get("resource") or "").strip() or None
        if name:
            add(relation, {"name": name, "url": url} if url else name)
    return references[:40]


def _is_decorative_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    filename = path.rsplit("/", maxsplit=1)[-1]
    return (
        path.endswith(".svg")
        or path.startswith("/assets/images/common/")
        or any(
            token in filename
            for token in (
                "logo",
                "icon",
                "favicon",
                "search",
                "btn_menu",
                "site-image",
                "shop_aniplus",
            )
        )
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
                    current_url = _secure_redirect_target(current_url, location)
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


def _secure_redirect_target(current_url: str, location: str) -> str:
    """Keep same-host canonical redirects on HTTPS without visiting HTTP."""
    target = urljoin(current_url, location)
    current = urlparse(current_url)
    redirected = urlparse(target)
    current_host = (current.hostname or "").lower().rstrip(".")
    redirected_host = (redirected.hostname or "").lower().rstrip(".")
    if (
        current.scheme == "https"
        and redirected.scheme == "http"
        and current_host
        and current_host == redirected_host
        and redirected.port in {None, 80}
    ):
        return redirected._replace(scheme="https", netloc=redirected.hostname).geturl()
    return target


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
    scope = _article_content_scope(soup, page_url)

    scoped_images = scope.find_all("img")
    parsed_page = urlparse(page_url)
    character_path = re.fullmatch(
        r"/character/([^/]+)/index\.html?", parsed_page.path, re.IGNORECASE
    )
    if (parsed_page.hostname or "").lower() == "one-piece.com" and character_path:
        # ONE PIECE character detail pages append a long recommendation rail
        # containing other crew members' portraits. Keep only art under this
        # character's own slug so unrelated characters cannot be selected for
        # the deck just because they are embedded in the same HTML document.
        slug_segment = f"/{character_path.group(1).casefold()}/"
        scoped_images = [
            tag
            for tag in scoped_images
            if slug_segment in urlparse(
                urljoin(page_url, str(tag.get("src") or tag.get("data-src") or ""))
            ).path.casefold()
        ]
    # Keep the article's social/hero image as an additional candidate even when
    # the body also contains images. Many publishers expose the original asset
    # only through Open Graph metadata.
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
            tag.get(key)
            for key in (
                "src",
                "data-src",
                "data-original",
                "data-lazy-src",
                "data-original-src",
                "data-image",
                "data-echo",
                "data-actualsrc",
            )
        ]

        for srcset_key in ("srcset", "data-srcset"):
            best = _largest_srcset_candidate(tag.get(srcset_key))
            if best:
                candidates.append(best)
        picture = tag.find_parent("picture")
        if picture:
            for source_tag in picture.find_all("source"):
                best = _largest_srcset_candidate(
                    source_tag.get("srcset") or source_tag.get("data-srcset")
                )
                if best:
                    candidates.append(best)
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
    # Some publishers render article/gallery visuals as CSS backgrounds instead
    # of <img> tags. Collect those only from the selected article body.
    for tag in scope.select("[style]"):
        style = str(tag.get("style") or "")
        for raw_url in re.findall(r"url\((?:['\"])?(.*?)(?:['\"])?\)", style, re.IGNORECASE):
            absolute_url = urljoin(page_url, raw_url.strip())
            scores[absolute_url] = max(scores.get(absolute_url, 0), 20)
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


def _article_content_scope(soup: BeautifulSoup, page_url: str | None = None):
    """Prefer article-body containers over broad main/article wrappers with related content."""
    if page_url:
        parsed_page = urlparse(page_url)
        if (
            (parsed_page.hostname or "").lower() == "toynews.kr"
            and parsed_page.path.startswith("/article/")
        ):
            view_content = soup.select_one("#viewConts, #subCtsGrp")
            if view_content:
                return view_content
        if (
            (parsed_page.hostname or "").lower() == "recal.kr"
            and parsed_page.path.startswith("/ko/e/offline/")
        ):
            headline = soup.find("h1")
            hero_section = headline.find_parent("section") if headline else None
            if hero_section and hero_section.find("img"):
                return hero_section
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
    parsed_page = urlparse(page_url)
    if (parsed_page.hostname or "").lower() == "toynews.kr" and parsed_page.path.startswith(
        "/article/"
    ):
        # These pages place the article in image panels and generic extraction can
        # mistake the site-wide weather strip for article facts.
        view_content = soup.select_one("#viewConts, #subCtsGrp")
        if view_content is not None:
            lines = [
                re.sub(r"\s+", " ", line).strip()
                for line in view_content.get_text(separator="\n", strip=True).splitlines()
            ]
            return "\n".join(line for line in lines if line)
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
        # Character indexes often encode their actual facts in linked cards
        # (name, birthday, affiliation, bounty, voice actor), which a prose
        # extractor treats as navigation and silently drops. Preserve only
        # same-site character-detail anchors that visibly include structured
        # profile fields; unrelated menus and bare links are excluded.
        parsed_page = urlparse(page_url)
        if (
            (parsed_page.hostname or "").lower() == "one-piece.com"
            and parsed_page.path.rstrip("/").lower() == "/story/index.html"
        ):
            arc_rows: list[str] = []
            seen_arcs: set[str] = set()
            for anchor in _article_content_scope(soup, page_url).select('a[href*="/story/"]'):
                href = urlparse(urljoin(page_url, str(anchor.get("href", ""))))
                row = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
                if (
                    (href.hostname or "").lower() != (parsed_page.hostname or "").lower()
                    or href.path.rstrip("/").lower() == parsed_page.path.rstrip("/").lower()
                    or len(row) < 12
                    or not re.search(r"原作.*\d+巻.*アニメ.*\d+話", row)
                    or row in seen_arcs
                ):
                    continue
                seen_arcs.add(row)
                arc_rows.append(row)
                if len(arc_rows) >= 50:
                    break
            if arc_rows:
                extracted = "\n".join(arc_rows)
        if re.search(r"/characters?/(?:index\.html)?/?$", parsed_page.path, re.IGNORECASE):
            profile_rows: list[str] = []
            seen_rows: set[str] = set()
            for anchor in _article_content_scope(soup, page_url).select('a[href*="/character/"]'):
                href = urlparse(urljoin(page_url, str(anchor.get("href", ""))))
                if (href.hostname or "").lower() != (parsed_page.hostname or "").lower():
                    continue
                if href.path.rstrip("/").lower() == parsed_page.path.rstrip("/").lower():
                    continue
                row = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
                if (
                    len(row) < 16
                    or len(row) > 500
                    or not re.search(r"懸賞金|誕生日|悪魔の実|声優|組織", row)
                    or row in seen_rows
                ):
                    continue
                seen_rows.add(row)
                profile_rows.append(row)
                if len(profile_rows) >= 60:
                    break
            if profile_rows:
                extracted = "\n".join(profile_rows)
        return extracted

    scope = _article_content_scope(soup, page_url)
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


def _largest_srcset_candidate(srcset: object) -> tuple[str, int] | None:
    entries = [item.strip().split() for item in str(srcset or "").split(",") if item.strip()]
    if not entries:
        return None
    # Save the best rendition for each image source instead of every
    # thumbnail/retina variant, avoiding duplicate resolutions in the asset pool.
    best = max(entries, key=lambda item: _srcset_width(item[-1] if len(item) > 1 else ""))
    return best[0], min(60, _srcset_width(best[-1] if len(best) > 1 else "") // 20)


def _related_image_page_candidates(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Find a few same-site article/gallery pages; never follow off-domain links."""
    current = urlparse(page_url)
    allowed_hosts = {(current.hostname or "").lower().rstrip(".")}
    scored: dict[str, int] = {}
    for anchor in _article_content_scope(soup, page_url).select("a[href]"):
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
    allow_discovery_page: bool = False,
) -> SourcePageCapture:
    """Read one approved page; discovery pages are link-resolution context only."""
    if (
        source.status != "active"
        or source.role not in {"primary", "editorial", "reference"}
        and not (allow_discovery_page and source.role == "discovery")
        or source.collection != "html"
    ):
        raise ValueError(
            "HTML 수집은 승인된 primary/editorial/reference 출처 또는 "
            "링크 해석 목적으로 명시 허용한 discovery 출처만 가능합니다."
        )
    html, final_url = _get_html(url, source, timeout)
    soup = BeautifulSoup(html, "html.parser")
    title = _page_title(soup)
    daewon_title = daewon_published_at = None
    if source.id == "daewonmedia_kr":
        daewon_title, daewon_published_at = _daewon_article_metadata(soup)
        if daewon_title:
            title = daewon_title
    if source.id == "aniplus_shop":
        title = _aniplus_exhibition_title(soup, final_url) or title

    published_at = _published_date_from_html(soup)
    if source.id == "daewonmedia_kr":
        published_at = published_at or daewon_published_at

    extracted = _extract_article_text(html, final_url, soup)
    if source.id == "aniplus_shop" and len(extracted.strip()) < 20:
        # The public SSR payload includes the campaign title, but product
        # availability/details may be client-loaded. Keep only this explicit fact.
        extracted = title
    image_urls, image_alt_text = _image_candidates(soup, final_url)
    seed_image_urls = image_urls
    image_urls = list(seed_image_urls)
    image_source_pages = {candidate: final_url for candidate in image_urls}
    image_source_titles = {candidate: title for candidate in image_urls}
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
        related_title = _page_title(related_soup)
        related_pages.append(related_final_url)
        related_urls, related_alt_text = _image_candidates(related_soup, related_final_url)
        for candidate in related_urls:
            image_source_pages.setdefault(candidate, related_final_url)
            image_source_titles.setdefault(candidate, related_title)
        related_image_urls.extend(related_urls)
        image_alt_text.update(related_alt_text)
    # Keep every seed-page image before images from optional linked galleries.
    image_urls.extend(related_image_urls)

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
        image_source_titles={
            str(image_url): image_source_titles.get(str(image_url), title)
            for image_url in unique_images
        },
        outbound_links=_article_outbound_links(soup, final_url, source.id),
        structured_work_references=_structured_work_references(soup),
        related_image_pages=[HttpUrl(page) for page in related_pages],
        excerpt_truncated=len(extracted) > excerpt_limit,
    )


def collect_reference_page(
    source: Source,
    url: str,
    *,
    timeout: float = 20.0,
) -> SourcePageCapture:
    """Fetch one approved Kitsu or Wikipedia record and normalize it for extraction."""
    if source.status != "active" or source.role != "reference" or source.collection != "api":
        raise ValueError("Only active reference API sources can use the reference collector.")
    _validate_url(url, source)
    parsed = urlparse(url)
    fetched_at = datetime.now(UTC).isoformat()
    structured_work_references: list[StructuredWorkReference] = []
    headers = {
        "Accept": "application/vnd.api+json, application/json",
        "User-Agent": "me-nu-content-agent/0.3 (https://github.com/Por0ri/me-and-nu)",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        if (
            source.category == "kitsu_anime_catalog"
            and re.fullmatch(r"/api/edge/media-characters/\d+", parsed.path)
        ):
            pace_host(url, source.min_interval_seconds)
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > MAX_PAGE_BYTES:
                raise ValueError("Kitsu API response exceeds the 5MB collection limit.")
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("Kitsu returned no character relationship record.")
            resources = {
                (str(item.get("type")), str(item.get("id"))): item
                for item in payload.get("included", [])
                if isinstance(item, dict)
            }
            relationships = data.get("relationships") or {}
            character_ref = (relationships.get("character") or {}).get("data") or {}
            media_ref = (relationships.get("media") or {}).get("data") or {}
            character = resources.get(
                (str(character_ref.get("type", "characters")), str(character_ref.get("id", "")))
            )
            media = resources.get(
                (str(media_ref.get("type", "anime")), str(media_ref.get("id", "")))
            )
            char_attrs = (character or {}).get("attributes") or {}
            media_attrs = (media or {}).get("attributes") or {}
            title = str(char_attrs.get("canonicalName") or "Kitsu character")
            media_title = str(media_attrs.get("canonicalTitle") or "").strip()
            if media_title:
                structured_work_references.append(
                    StructuredWorkReference(relation="about", name=media_title)
                )
            text = json.dumps(
                {
                    "record_type": "anime_character_reference",
                    "character_name": title,
                    "alternate_names": char_attrs.get("names"),
                    "description": BeautifulSoup(
                        str(char_attrs.get("description") or ""), "html.parser"
                    ).get_text(" ", strip=True)[:3000],
                    "character_role_in_work": (data.get("attributes") or {}).get("role"),
                    "work_title": media_attrs.get("canonicalTitle"),
                    "work_titles": media_attrs.get("titles"),
                    "source_updated_at": char_attrs.get("updatedAt"),
                },
                ensure_ascii=False,
                indent=2,
            )
            canonical = url
        elif (
            source.category == "kitsu_anime_catalog"
            and parsed.path == "/api/edge/anime"
            and parse_qs(parsed.query).get("sort") == ["-averageRating"]
        ):
            query = parse_qs(parsed.query)
            try:
                limit = int((query.get("page[limit]") or ["20"])[0])
            except ValueError as exc:
                raise ValueError("Kitsu page limit is invalid.") from exc
            if not 1 <= limit <= 20:
                raise ValueError("Kitsu rating pages are limited to 20 works.")
            pace_host(url, source.min_interval_seconds)
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > MAX_PAGE_BYTES:
                raise ValueError("Kitsu API response exceeds the 5MB collection limit.")
            payload = response.json()
            fields = []
            for row in (payload.get("data") or [])[:limit]:
                attrs = row.get("attributes") or {}
                if not attrs.get("canonicalTitle") or attrs.get("averageRating") is None:
                    continue
                structured_work_references.append(
                    StructuredWorkReference(
                        relation="mention", name=str(attrs["canonicalTitle"])
                    )
                )
                fields.append(
                    {
                        "rank": attrs.get("ratingRank"),
                        "work_title": attrs.get("canonicalTitle"),
                        "average_rating": attrs.get("averageRating"),
                        "rating_count": attrs.get("userCount"),
                        "source_updated_at": attrs.get("updatedAt"),
                        "work_url": f"https://kitsu.io/anime/{row.get('id')}",
                    }
                )
            if not fields:
                raise ValueError("Kitsu returned no rating-ranked works.")
            title = "Kitsu 사용자 평점 랭킹"
            text = json.dumps(fields, ensure_ascii=False, indent=2)
            canonical = url
        elif source.category == "kitsu_anime_catalog":
            character_match = re.fullmatch(r"/api/edge/media-characters/(\d+)", parsed.path)
            ranking = parsed.path == "/api/edge/anime" and parse_qs(parsed.query).get(
                "sort"
            ) == ["-averageRating"]
            anime_match = re.fullmatch(r"/anime/(\d+)/?", parsed.path)
            if not (character_match or ranking or anime_match):
                raise ValueError(
                    "Kitsu reference URL is outside the approved anime/character routes."
                )
            endpoint = url if character_match or ranking else (
                f"{str(source.url).rstrip('/')}/{anime_match.group(1)}"
            )
            _validate_url(endpoint, source)
            pace_host(endpoint, source.min_interval_seconds)
            response = client.get(endpoint)
            response.raise_for_status()
            if len(response.content) > MAX_PAGE_BYTES:
                raise ValueError("Kitsu API response exceeds the 5MB collection limit.")
            payload = response.json()
            if character_match:
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Kitsu returned no character relationship record.")
                included = payload.get("included") or []
                resources = {
                    (str(item.get("type")), str(item.get("id"))): item
                    for item in included
                    if isinstance(item, dict)
                }
                relationships = data.get("relationships") or {}
                character_ref = (relationships.get("character") or {}).get("data") or {}
                media_ref = (relationships.get("media") or {}).get("data") or {}
                character = resources.get(
                    (str(character_ref.get("type", "characters")), str(character_ref.get("id", "")))
                )
                media = resources.get(
                    (str(media_ref.get("type", "anime")), str(media_ref.get("id", "")))
                )
                char_attrs = (character or {}).get("attributes") or {}
                media_attrs = (media or {}).get("attributes") or {}
                title = str(char_attrs.get("canonicalName") or "Kitsu character")
                text = json.dumps(
                    {
                        "record_type": "anime_character_reference",
                        "character_name": title,
                        "alternate_names": char_attrs.get("names"),
                        "description": BeautifulSoup(
                            str(char_attrs.get("description") or ""), "html.parser"
                        ).get_text(" ", strip=True)[:3000],
                        "character_role_in_work": (data.get("attributes") or {}).get("role"),
                        "work_title": media_attrs.get("canonicalTitle"),
                        "work_titles": media_attrs.get("titles"),
                        "source_updated_at": char_attrs.get("updatedAt"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            elif ranking:
                rows = payload.get("data") or []
                fields = []
                for row in rows[:20]:
                    attrs = row.get("attributes") or {}
                    if not attrs.get("canonicalTitle") or attrs.get("averageRating") is None:
                        continue
                    fields.append(
                        {
                            "rank": attrs.get("ratingRank"),
                            "work_title": attrs.get("canonicalTitle"),
                            "average_rating": attrs.get("averageRating"),
                            "rating_count": attrs.get("userCount"),
                            "source_updated_at": attrs.get("updatedAt"),
                            "work_url": f"https://kitsu.io/anime/{row.get('id')}",
                        }
                    )
                if not fields:
                    raise ValueError("Kitsu returned no rating-ranked works.")
                title = "Kitsu 사용자 평점 랭킹"
                text = json.dumps(fields, ensure_ascii=False, indent=2)
            else:
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Kitsu returned no anime detail record.")
                attrs = data.get("attributes") or {}
                fields = {
                    key: attrs.get(key)
                    for key in (
                        "canonicalTitle",
                        "titles",
                        "subtype",
                        "startDate",
                        "endDate",
                        "episodeCount",
                        "episodeLength",
                        "status",
                        "ageRating",
                        "averageRating",
                        "ratingRank",
                        "popularityRank",
                        "synopsis",
                    )
                    if attrs.get(key) is not None
                }
                title = str(
                    attrs.get("canonicalTitle") or fields.get("titles") or "Kitsu anime"
                )
                for work_title in dict.fromkeys(
                    [title, *(str(value) for value in (fields.get("titles") or {}).values())]
                ):
                    if work_title.strip():
                        structured_work_references.append(
                            StructuredWorkReference(relation="about", name=work_title.strip())
                        )
                text = json.dumps(fields, ensure_ascii=False, indent=2)
            canonical = url
        elif source.category == "wikipedia_reference":
            if not parsed.path.startswith("/wiki/"):
                raise ValueError("Wikipedia reference URLs must identify one /wiki/ article.")
            title = unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ")
            if not title:
                raise ValueError("Wikipedia reference URL has no article title.")
            params = {
                "action": "query",
                "prop": "extracts|info",
                "explaintext": "1",
                "inprop": "url",
                "titles": title,
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
            }
            endpoint = str(source.url)
            pace_host(endpoint, source.min_interval_seconds)
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
            page = next(
                (row for row in pages if isinstance(row, dict) and not row.get("missing")),
                None,
            )
            if page is None or not str(page.get("extract", "")).strip():
                raise ValueError("Wikipedia returned no article extract for this title.")
            title = str(page.get("title") or title)
            canonical = str(page.get("fullurl") or url)
            text = str(page["extract"])[:24_000]
        else:
            raise ValueError(f"Unsupported reference API adapter: {source.category}")

    return SourcePageCapture(
        source_id=source.id,
        title=title,
        url=HttpUrl(canonical),
        fetched_at=fetched_at,
        text=text,
        image_urls=[],
        structured_work_references=structured_work_references,
        status="unverified_capture",
        excerpt_truncated=len(text) >= 24_000,
    )
