"""공식 출처에 연결된 이미지 URL을 안전하게 받아 PostgreSQL 적재용으로 검증합니다."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import socket
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from PIL import Image

from ani_menu_agent.models import ContentPackage, ImageAsset, NormalizedBox, SourceEvidence
from ani_menu_agent.policy import POLICY

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_ROBOTS_BYTES = 256 * 1024
ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
REDIRECT_LIMIT = 4


class ImageDownloadError(ValueError):
    """이미지 URL이나 응답이 다운로드 정책을 충족하지 못할 때 발생합니다."""


def _host(value: str) -> str:
    return (urlparse(value).hostname or "").rstrip(".").lower()


def _source_for_image(package: ContentPackage, image: ImageAsset) -> SourceEvidence:
    page_host = _host(str(image.source_url))
    for source in package.sources:
        if not source.is_official or source.role != "primary":
            continue
        if _host_is_allowed(page_host, source):
            return source
    raise ImageDownloadError("공식 1차 출처와 연결되지 않은 이미지라 다운로드를 보류했습니다.")


def _host_is_allowed(candidate: str, source: SourceEvidence) -> bool:
    host = candidate.lower().rstrip(".")
    approved_hosts = {
        _host(str(source.url)),
        *[item.lower().rstrip(".") for item in source.allowed_asset_hosts],
    }
    return any(host == approved or host.endswith(f".{approved}") for approved in approved_hosts)


def _validate_public_https(url: str, source: SourceEvidence) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ImageDownloadError("이미지 주소는 HTTPS여야 합니다.")
    host = parsed.hostname.lower().rstrip(".")
    if not _host_is_allowed(host, source):
        raise ImageDownloadError(f"허용 목록에 없는 이미지 호스트입니다: {host}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ImageDownloadError(f"이미지 호스트를 확인할 수 없습니다: {host}") from exc
    if not addresses:
        raise ImageDownloadError(f"이미지 호스트의 주소가 없습니다: {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ImageDownloadError("내부 네트워크 주소로 연결되는 이미지는 받을 수 없습니다.")


def _fetch_bytes(
    url: str,
    source: SourceEvidence,
    timeout: float,
    robots_cache: dict[str, RobotFileParser | None] | None = None,
) -> tuple[bytes, str]:
    headers = {"User-Agent": "me-nu-content-agent/0.3 (image asset fetcher)"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        current_url = url
        for _ in range(REDIRECT_LIMIT + 1):
            _validate_public_https(current_url, source)
            _check_robots(client, current_url, source, robots_cache)
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ImageDownloadError("리다이렉트 주소가 비어 있습니다.")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    raise ImageDownloadError(f"이미지 응답이 아닙니다: {content_type or 'unknown'}")
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_IMAGE_BYTES:
                    raise ImageDownloadError("이미지 파일이 20MB 제한을 초과합니다.")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ImageDownloadError("이미지 파일이 20MB 제한을 초과합니다.")
                    chunks.append(chunk)
                return b"".join(chunks), content_type
    raise ImageDownloadError("이미지 리다이렉트 횟수가 제한을 초과했습니다.")


def _check_robots(
    client: httpx.Client,
    url: str,
    source: SourceEvidence,
    robots_cache: dict[str, RobotFileParser | None] | None = None,
) -> None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    _validate_public_https(robots_url, source)
    if robots_cache is not None and robots_url in robots_cache:
        parser = robots_cache[robots_url]
        if parser is not None and not parser.can_fetch("me-nu-content-agent", url):
            raise ImageDownloadError("robots.txt에서 이미지 자동 다운로드를 허용하지 않습니다.")
        return
    try:
        with client.stream("GET", robots_url) as response:
            if response.status_code in {404, 410}:
                if robots_cache is not None:
                    robots_cache[robots_url] = None
                return
            if response.status_code != 200:
                raise ImageDownloadError(f"robots.txt 확인 실패: HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_ROBOTS_BYTES:
                    raise ImageDownloadError("robots.txt가 256KB 제한을 초과합니다.")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ImageDownloadError(
            f"robots.txt를 확인할 수 없어 다운로드를 중단했습니다: {exc}"
        ) from exc
    parser = RobotFileParser(robots_url)
    parser.parse(b"".join(chunks).decode("utf-8", errors="replace").splitlines())
    if robots_cache is not None:
        robots_cache[robots_url] = parser
    if not parser.can_fetch("me-nu-content-agent", url):
        raise ImageDownloadError("robots.txt에서 이미지 자동 다운로드를 허용하지 않습니다.")


def _validated_image(
    payload: bytes, content_type: str
) -> tuple[str, int, int, bool, NormalizedBox | None]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image_format = (image.format or "").upper()
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            has_alpha = alpha.getextrema()[0] < 255
            alpha_box = None
            if has_alpha:
                bbox = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
                if bbox is not None:
                    left, top, right, bottom = bbox
                    alpha_box = NormalizedBox(
                        x=left / width,
                        y=top / height,
                        width=(right - left) / width,
                        height=(bottom - top) / height,
                    )
    except Exception as exc:
        raise ImageDownloadError("응답 파일을 정상 이미지로 판독하지 못했습니다.") from exc
    if image_format not in ALLOWED_FORMATS:
        raise ImageDownloadError(f"지원하지 않는 이미지 포맷입니다: {image_format or content_type}")
    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
        raise ImageDownloadError("이미지 크기가 허용 범위를 벗어났습니다.")
    return ALLOWED_FORMATS[image_format], width, height, has_alpha, alpha_box


def download_image(
    package: ContentPackage,
    image: ImageAsset,
    timeout: float = 20.0,
    robots_cache: dict[str, RobotFileParser | None] | None = None,
) -> ImageAsset:
    """이미지를 검증해 메모리에서 PostgreSQL 적재 단계로 전달합니다."""
    if not image.url:
        raise ImageDownloadError("다운로드할 이미지 URL이 없습니다.")
    if not image.official or not image.eligible_for_editorial_use(
        package.sources,
        allow_risk_accepted=POLICY.allow_public_official_image_risk,
    ):
        raise ImageDownloadError(
            "공식 출처 또는 me;nu 사용 근거가 확인되지 않아 다운로드하지 않습니다."
        )
    source = _source_for_image(package, image)
    payload, content_type = _fetch_bytes(str(image.url), source, timeout, robots_cache)
    suffix, width, height, has_alpha, subject_box = _validated_image(payload, content_type)
    digest = hashlib.sha256(payload).hexdigest()
    return image.model_copy(
        update={
            "local_path": None,
            "storage_key": digest,
            "media_type": content_type,
            "file_size_bytes": len(payload),
            "content_bytes": payload,
            "width": width,
            "height": height,
            "has_alpha": has_alpha,
            "subject_box": subject_box,
            "fingerprint": digest,
            "download_status": "downloaded",
            "download_error": None,
        }
    )
