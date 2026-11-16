"""Work-linked image candidate extraction and explicit, host-limited downloads.

Nothing in this module runs during import. Callers must pass a confirmed local
work ID and invoke download_candidate explicitly for each source image.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from ani_rag.content import ImageAsset, ImageEntityLink
from ani_rag.settings import EvergreenSourceRegistry, SourceStatus, require_source_media_url, require_source_url

_KITSU_MEDIA_FIELDS = ("original", "large", "medium", "small", "tiny")
_SOURCE_ID = re.compile(r"^[a-z0-9_]+$")
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_MAX_PIXELS = 100_000_000


@dataclass(frozen=True)
class ImageCandidate:
    source_id: str
    source_url: str
    source_page_url: str
    source_native_id: str
    menu_work_id: int
    image_role: str
    alt_text: str | None = None
    work_link_status: str = "matched"
    entity_links: tuple[ImageEntityLink, ...] = ()

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.source_id, self.source_url, self.source_page_url,
            self.source_native_id, self.image_role,
        )):
            raise ValueError("Image candidate needs source, URLs, native ID, and image role")
        if self.menu_work_id <= 0 or self.work_link_status != "matched":
            raise ValueError("Image candidates require a confirmed positive local work ID")
        if any(link.status != "matched" for link in self.entity_links):
            raise ValueError("Image candidates may include only evidence-backed matched entity links")


@dataclass(frozen=True)
class WebImageSearchHit:
    """Unverified image-search result; it has no local work link until vision review."""

    image_url: str
    source_page_url: str
    title: str
    description: str = ""
    native_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.image_url, self.source_page_url, self.title):
            if not value.strip():
                raise ValueError("Web image result needs an image URL, source page, and title")


class WebImageSearchAdapter(Protocol):
    async def search_images(self, query: str, *, limit: int) -> tuple[WebImageSearchHit, ...]:
        """Return bounded image candidates with both direct-image and source-page URLs."""


@dataclass(frozen=True)
class ImageIngestFailure:
    source_url: str
    error_type: str


@dataclass(frozen=True)
class ImageIngestResult:
    assets: tuple[ImageAsset, ...]
    failures: tuple[ImageIngestFailure, ...]
    reused_count: int = 0


def kitsu_image_candidates(
    payload: Mapping[str, object], *, source_page_url: str, menu_work_id: int,
    work_link_status: str = "matched",
) -> tuple[ImageCandidate, ...]:
    """Extract every distinct Kitsu cover/character image size from a matched API result."""
    if work_link_status != "matched" or menu_work_id <= 0:
        raise ValueError("Resolve the Kitsu anime entry to one local work ID before attaching images")
    return tuple(ImageCandidate(
        source_id="kitsu_anime_catalog", source_url=item["url"],
        source_page_url=source_page_url, source_native_id=item["source_native_id"],
        menu_work_id=menu_work_id, image_role=item["role"], alt_text=item.get("alt_text"),
    ) for item in kitsu_image_references(payload))


def kitsu_image_references(payload: Mapping[str, object]) -> list[dict[str, str]]:
    """Extract every distinct Kitsu cover/character image URL and its record role."""
    data_value = payload.get("data")
    anime_rows = data_value if isinstance(data_value, list) else [data_value]
    included_value = payload.get("included")
    included = included_value if isinstance(included_value, list) else []
    references: list[dict[str, str]] = []
    seen: set[str] = set()

    for anime in anime_rows:
        if not isinstance(anime, dict) or anime.get("type") != "anime":
            continue
        native_id = str(anime.get("id") or "").strip()
        attrs = anime.get("attributes") if isinstance(anime.get("attributes"), dict) else {}
        label = attrs.get("canonicalTitle") if isinstance(attrs.get("canonicalTitle"), str) else native_id
        _append_kitsu_image_references(
            references, seen, attrs.get("coverImage"), native_id, "cover", str(label),
        )

    relation_roles: dict[str, str] = {}
    for record in included:
        if not isinstance(record, dict) or record.get("type") != "animeCharacters":
            continue
        attributes = record.get("attributes")
        role = attributes.get("role") if isinstance(attributes, dict) else None
        relationships = record.get("relationships")
        character = relationships.get("character") if isinstance(relationships, dict) else None
        character_data = character.get("data") if isinstance(character, dict) else None
        character_id = character_data.get("id") if isinstance(character_data, dict) else None
        if character_id:
            relation_roles[str(character_id)] = str(role or "unspecified")

    for record in included:
        if not isinstance(record, dict) or record.get("type") != "characters":
            continue
        native_id = str(record.get("id") or "").strip()
        attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
        label = attrs.get("name") if isinstance(attrs.get("name"), str) else native_id
        role = relation_roles.get(native_id, "unspecified")
        _append_kitsu_image_references(
            references, seen, attrs.get("image"), native_id,
            f"character:{role}", str(label),
        )
    return references


def _append_kitsu_image_references(
    output: list[dict[str, str]], seen: set[str], image_object: object,
    native_id: str, role: str, label: str,
) -> None:
    if not native_id or not isinstance(image_object, dict):
        return
    for size in _KITSU_MEDIA_FIELDS:
        url = image_object.get(size)
        if not isinstance(url, str) or not url.strip() or url in seen:
            continue
        seen.add(url)
        output.append({
            "url": url, "source_native_id": native_id,
            "role": f"{role}:{size}", "alt_text": label,
        })


class _MediaHostRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, request, file, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or host not in self.allowed_hosts:
            return None
        return super().redirect_request(request, file, code, message, headers, new_url)


def download_candidate(
    candidate: ImageCandidate, *, registry: EvergreenSourceRegistry,
    destination_dir: Path, max_bytes: int = _DEFAULT_MAX_BYTES,
) -> ImageAsset:
    """Download one explicitly selected candidate and save it under source/work/hash."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    source = next((item for item in registry.sources if item.source_id == candidate.source_id), None)
    if source is None or source.status is not SourceStatus.APPROVED:
        raise PermissionError("Image source must be present and approved in the RAG source registry")
    if not _SOURCE_ID.fullmatch(candidate.source_id):
        raise ValueError("Invalid source ID")
    require_source_url(source, candidate.source_page_url)
    require_source_media_url(source, candidate.source_url)
    if candidate.work_link_status != "matched" or candidate.menu_work_id <= 0:
        raise PermissionError("Only images with one confirmed local work ID may be downloaded")

    request = Request(candidate.source_url, headers={
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        "User-Agent": "ani-rag-image-ingest/0.1",
    })
    opener = build_opener(_MediaHostRedirectHandler(source.allowed_media_hosts))
    try:
        with opener.open(request, timeout=30) as response:
            final_url = response.geturl()
            require_source_media_url(source, final_url)
            content_type = response.headers.get_content_type().casefold()
            if not content_type.startswith("image/"):
                raise ValueError(f"Expected image content, received {content_type!r}")
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > max_bytes:
                raise ValueError("Image exceeds the configured byte limit")
            data = response.read(max_bytes + 1)
    except HTTPError as error:
        raise RuntimeError(f"Image host returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Could not retrieve image from the approved media host: {error.reason}") from error
    if not data or len(data) > max_bytes:
        raise ValueError("Image response was empty or exceeded the configured byte limit")

    with Image.open(io.BytesIO(data)) as image:
        image_format = (image.format or "").upper()
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
            raise ValueError("Image dimensions are outside the supported bounds")
        image.verify()
    extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "AVIF": ".avif"}.get(image_format)
    if extension is None:
        raise ValueError(f"Unsupported image format: {image_format!r}")

    digest = hashlib.sha256(data).hexdigest()
    asset_id = "img_" + hashlib.sha256(
        f"{candidate.source_id}\n{final_url}\n{digest}".encode("utf-8")
    ).hexdigest()[:32]
    root = destination_dir.resolve()
    target_dir = (root / candidate.source_id / str(candidate.menu_work_id)).resolve()
    if root not in target_dir.parents:
        raise ValueError("Resolved image destination escaped the configured asset directory")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{extension}"
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)

    return ImageAsset(
        asset_id=asset_id, source_url=final_url, source_id=candidate.source_id,
        work_link_status=candidate.work_link_status, menu_work_ids=(candidate.menu_work_id,),
        rights_basis=None, rights_evidence_url=None, local_path=str(target),
        source_page_url=candidate.source_page_url, source_native_id=candidate.source_native_id,
        image_role=candidate.image_role, content_sha256=digest, media_type=Image.MIME.get(image_format),
        alt_text=candidate.alt_text, byte_size=len(data), width=width, height=height,
        entity_links=candidate.entity_links,
    )


class ImageAssetRepository(Protocol):
    async def register_asset(self, asset: ImageAsset) -> None: ...
    async def get_existing_for_candidate(self, candidate: ImageCandidate) -> ImageAsset | None: ...


async def ingest_image_candidates(
    candidates: tuple[ImageCandidate, ...], *, registry: EvergreenSourceRegistry,
    repository: ImageAssetRepository, destination_dir: Path,
    max_bytes_per_image: int = _DEFAULT_MAX_BYTES,
    interval_seconds: float = 0.25,
) -> ImageIngestResult:
    """Try every candidate serially; one unavailable image does not lose its evidence."""
    if interval_seconds < 0:
        raise ValueError("interval_seconds cannot be negative")
    assets: list[ImageAsset] = []
    failures: list[ImageIngestFailure] = []
    reused_count = 0
    for index, candidate in enumerate(candidates):
        try:
            find_existing = getattr(repository, "get_existing_for_candidate", None)
            if callable(find_existing):
                existing = await find_existing(candidate)
                if existing is not None:
                    assets.append(existing)
                    reused_count += 1
                    continue
            asset = await asyncio.to_thread(
                download_candidate, candidate, registry=registry,
                destination_dir=destination_dir, max_bytes=max_bytes_per_image,
            )
            await repository.register_asset(asset)
            assets.append(asset)
        except Exception as error:
            failures.append(ImageIngestFailure(candidate.source_url, type(error).__name__))
        if interval_seconds and index + 1 < len(candidates):
            await asyncio.sleep(interval_seconds)
    return ImageIngestResult(tuple(assets), tuple(failures), reused_count)
