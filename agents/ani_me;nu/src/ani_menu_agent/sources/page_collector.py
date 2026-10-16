"""사용 조건이 승인된 1차 HTML 출처에서 본문과 이미지 URL 후보를 수집합니다."""

from __future__ import annotations

import ipaddress
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


class SourcePageCapture(BaseModel):
    source_id: str
    title: str
    url: HttpUrl
    fetched_at: str
    text: str
    image_urls: list[HttpUrl]
    image_alt_text: dict[str, str] = Field(default_factory=dict)
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


def collect_official_page(
    source: Source,
    url: str,
    *,
    timeout: float = 20.0,
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

    extracted = (
        trafilatura.extract(
            html,
            url=final_url,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        or ""
    )
    image_urls: list[str] = []
    image_alt_text: dict[str, str] = {}
    for tag in soup.find_all("meta", attrs={"property": "og:image"}):
        image_url = tag.get("content")
        if image_url:
            image_urls.append(urljoin(final_url, str(image_url)))
    for tag in soup.find_all("img"):
        image_url = tag.get("src") or tag.get("data-src")
        if image_url:
            absolute_url = urljoin(final_url, str(image_url))
            image_urls.append(absolute_url)
            description = str(tag.get("alt") or tag.get("title") or "").strip()
            if description:
                image_alt_text[absolute_url] = description[:300]

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
        text=extracted[:excerpt_limit],
        image_urls=unique_images,
        image_alt_text={
            str(image_url): image_alt_text[str(image_url)]
            for image_url in unique_images
            if str(image_url) in image_alt_text
        },
        excerpt_truncated=len(extracted) > excerpt_limit,
    )
