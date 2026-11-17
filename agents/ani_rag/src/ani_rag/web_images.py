"""Bounded Firecrawl image discovery for evidence-grounded generation.

Search results stay provisional in a temporary directory. Only images that pass
the generation service's per-card vision check are registered in the RAG store.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import io
import re
import shutil
import socket
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from ani_rag.content import ImageAsset
from ani_rag.image_ingest import ImageCandidate, WebImageSearchHit
from ani_rag.models import EvidenceDocument, RagContentType
from ani_rag.settings import EvergreenSource, SourceStatus
from ani_rag.storage.sqlite_workflow import SQLiteImageReader

WEB_IMAGE_SOURCE_ID = "firecrawl_web_images"
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_PIXELS = 80_000_000
_MAX_SEARCH_RESULTS = 8


class ImageSearchAdapter(Protocol):
    async def search_images(self, query: str, *, limit: int) -> Sequence[WebImageSearchHit]: ...


class _PublicImageRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        _validate_public_image_url(new_url)
        return super().redirect_request(request, file, code, message, headers, new_url)


class WebSearchImageReader:
    """Augment DB images with bounded web results and persist only visually accepted assets."""

    def __init__(
        self, local: SQLiteImageReader, adapter: ImageSearchAdapter, *,
        asset_dir: Path, max_results: int = _MAX_SEARCH_RESULTS,
    ) -> None:
        if not 1 <= max_results <= 10:
            raise ValueError("Web image search limit must be between 1 and 10")
        self.local = local
        self.adapter = adapter
        self.asset_dir = asset_dir
        self.max_results = max_results
        self._staging_dir: Path | None = None
        self._staged_assets: dict[str, ImageAsset] = {}

    async def candidates_for(self, evidence: Sequence[EvidenceDocument]) -> Sequence[ImageAsset]:
        candidates = await self.local.candidates_for(evidence)
        return tuple(
            asset for asset in candidates
            if _is_readable_image(asset)
            and (asset.source_id != WEB_IMAGE_SOURCE_ID
                 or not _is_collage_or_product_result(
                     asset.alt_text or "", "", asset.source_page_url or asset.source_url,
                 ))
        )

    async def get_existing_for_candidate(self, candidate: ImageCandidate) -> ImageAsset | None:
        return await self.local.get_existing_for_candidate(candidate)

    async def register_asset(self, asset: ImageAsset) -> None:
        await self.local.register_asset(asset)

    async def search_for_generation(
        self, evidence: Sequence[EvidenceDocument], *, query: str,
        content_type: RagContentType, menu_work_ids: Sequence[int] | None = None,
    ) -> Sequence[ImageAsset]:
        # Retrieval's explicit work scope is authoritative for this image search.
        # Stored evidence can carry legacy/ambiguous cross-links; unioning those
        # links made a valid scoped request look ambiguous and silently skipped MCP.
        work_ids = tuple(sorted(set(menu_work_ids or ())))
        if not work_ids:
            work_ids = tuple(sorted({work_id for item in evidence for work_id in item.menu_work_ids}))
        if len(work_ids) != 1:
            return ()
        existing = await self.local.candidates_for(evidence)
        # Keep a failed-card query focused. Appending several unrelated evidence
        # titles can turn a character portrait search back into a broad collage search.
        terms = [query, *(item.title for item in evidence[:1])]
        search_query = " ".join(dict.fromkeys(
            " ".join(value.split()) for value in terms if value.strip()
        ))[:450]
        if not search_query:
            return ()
        hits = await self.adapter.search_images(search_query, limit=self.max_results)
        if not hits:
            return ()
        if self._staging_dir is None:
            self._staging_dir = Path(tempfile.mkdtemp(prefix="ani_rag_web_images_"))
        known_hashes = {
            asset.content_sha256 for asset in (*existing, *self._staged_assets.values())
            if asset.content_sha256
        }
        assets: list[ImageAsset] = []
        for hit in hits[:self.max_results]:
            if _is_collage_or_product_result(hit.title, hit.description, hit.source_page_url):
                continue
            try:
                asset = await asyncio.to_thread(
                    _download_candidate, hit, menu_work_id=work_ids[0],
                    destination_dir=self._staging_dir,
                    image_role="music:search_candidate" if content_type is RagContentType.MUSIC else "web_search_candidate",
                )
            except Exception:
                continue
            fingerprint = asset.content_sha256 or asset.asset_id
            if fingerprint in known_hashes:
                # A repeated URL maps to the same staged filename. Keep the
                # already-registered candidate file alive across retry searches.
                if asset.asset_id not in self._staged_assets:
                    Path(asset.local_path).unlink(missing_ok=True)
                continue
            known_hashes.add(fingerprint)
            self._staged_assets[asset.asset_id] = asset
            assets.append(asset)
        return tuple(assets)

    async def persist_selected(self, asset_ids: Sequence[str]) -> None:
        selected = [self._staged_assets[item] for item in dict.fromkeys(asset_ids) if item in self._staged_assets]
        if not selected:
            self.discard_staged()
            return
        await self.local.store.register_source(_web_image_source())
        for candidate in selected:
            source = urlsplit(candidate.source_url)
            suffix = Path(source.path).suffix.casefold()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                suffix = _extension(candidate.media_type)
            root = self.asset_dir.resolve()
            target_dir = (root / WEB_IMAGE_SOURCE_ID / str(candidate.menu_work_ids[0])).resolve()
            if root not in target_dir.parents:
                raise ValueError("Web image destination escaped the configured asset directory")
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{candidate.content_sha256}{suffix}"
            if not target.exists():
                temporary = target.with_suffix(target.suffix + ".part")
                shutil.copyfile(candidate.local_path, temporary)
                temporary.replace(target)
            accepted = replace(
                candidate, work_link_status="matched", local_path=str(target),
            )
            await self.local.register_asset(accepted)
            self._staged_assets[accepted.asset_id] = accepted
        self.discard_staged(keep_assets=True)

    async def get_many(self, asset_ids: Sequence[str]) -> dict[str, ImageAsset]:
        stored = await self.local.get_many(asset_ids)
        staged = {asset_id: self._staged_assets[asset_id]
                  for asset_id in asset_ids if asset_id in self._staged_assets}
        return {**stored, **staged}

    def discard_staged(self, *, keep_assets: bool = False) -> None:
        if self._staging_dir is not None:
            shutil.rmtree(self._staging_dir, ignore_errors=True)
            self._staging_dir = None
        if not keep_assets:
            self._staged_assets.clear()


def _web_image_source() -> EvergreenSource:
    """Represent Firecrawl as the internal search mechanism, not as image authorship."""
    return EvergreenSource(
        source_id=WEB_IMAGE_SOURCE_ID,
        name="Firecrawl web image search",
        mcp_server="firecrawl",
        content_types=tuple(RagContentType),
        allowed_tools=frozenset({"search", "read"}),
        status=SourceStatus.APPROVED,
        usage_notes=(
            "Internal image discovery mechanism only. Each asset retains its original image URL and "
            "source-page URL; this source is not factual evidence for content claims."
        ),
        allowed_hosts=frozenset({"firecrawl.dev"}),
        tool_bindings=(("search", "firecrawl_search"), ("read", "firecrawl_scrape")),
        source_kind="image_search_service",
        collect_method="mcp_image_search",
        site_url="https://firecrawl.dev",
        source_grade="discovery_service",
        copyright_status="not_assessed",
        license_basis="original image and source-page URLs preserved for operator review",
        is_collecting=False,
    )


def _validate_public_image_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError("Web images must use credential-free HTTPS on the standard port")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".test", ".invalid")):
        raise ValueError("Local and reserved hosts cannot supply web images")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        raise ValueError("IP-literal image hosts are not accepted")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Image host must resolve only to public IP addresses")
    return host


def _download_candidate(
    hit: WebImageSearchHit, *, menu_work_id: int, destination_dir: Path,
    image_role: str,
) -> ImageAsset:
    if menu_work_id <= 0:
        raise ValueError("Web image candidates require one positive local work ID")
    _validate_public_image_url(hit.image_url)
    _validate_public_image_url(hit.source_page_url)
    request = Request(hit.image_url, headers={
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        "User-Agent": "ani-rag-web-image-preview/0.1",
    })
    opener = build_opener(_PublicImageRedirectHandler())
    try:
        with opener.open(request, timeout=25) as response:
            final_url = response.geturl()
            _validate_public_image_url(final_url)
            media_type = response.headers.get_content_type().casefold()
            if not media_type.startswith("image/"):
                raise ValueError("Image search URL did not return image data")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > _MAX_IMAGE_BYTES:
                raise ValueError("Web image exceeds the configured byte limit")
            data = response.read(_MAX_IMAGE_BYTES + 1)
    except HTTPError as error:
        raise RuntimeError(f"Image host returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError("Could not retrieve the image candidate") from error
    if not data or len(data) > _MAX_IMAGE_BYTES:
        raise ValueError("Web image was empty or exceeded the configured byte limit")
    with Image.open(io.BytesIO(data)) as image:
        image_format = (image.format or "").upper()
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
            raise ValueError("Web image dimensions are outside supported bounds")
        image.verify()
    extension = _extension(Image.MIME.get(image_format))
    digest = hashlib.sha256(data).hexdigest()
    asset_id = "img_" + hashlib.sha256(
        f"{WEB_IMAGE_SOURCE_ID}\n{final_url}\n{digest}".encode("utf-8")
    ).hexdigest()[:32]
    target = destination_dir / f"{asset_id}{extension}"
    target.write_bytes(data)
    return ImageAsset(
        asset_id=asset_id,
        source_url=final_url,
        source_id=WEB_IMAGE_SOURCE_ID,
        work_link_status="candidate",
        menu_work_ids=(menu_work_id,),
        rights_basis="not_assessed",
        rights_evidence_url=None,
        local_path=str(target),
        source_page_url=hit.source_page_url,
        source_native_id=hit.native_id,
        image_role=image_role,
        alt_text="; ".join(value for value in (hit.title, hit.description) if value)[:900],
        content_sha256=digest,
        media_type=Image.MIME.get(image_format),
        byte_size=len(data),
        width=width,
        height=height,
    )


def _extension(media_type: str | None) -> str:
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    }.get((media_type or "").casefold(), ".img")


def _is_collage_or_product_result(title: str, description: str, source_page_url: str) -> bool:
    text = f"{title} {description}".casefold()
    if re.search(r"\b(collage|wall\s*art|metal\s*poster|framed\s*poster|merchandise)\b", text):
        return True
    path = urlsplit(source_page_url).path.casefold()
    return any(segment in path for segment in ("/products/", "/product/", "/listing/"))


def _is_readable_image(asset: ImageAsset) -> bool:
    if not asset.local_path or not Path(asset.local_path).is_file():
        return False
    try:
        with Image.open(asset.local_path) as image:
            image.load()
            image.convert("RGB").load()
    except (OSError, ValueError):
        return False
    return True
