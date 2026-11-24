"""Article-bound event photos and verified manufacturer product-photo permissions."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl

from ani_menu_agent.models import (
    ContentPackage,
    ImageAsset,
    RightsBasis,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.sources.models import Source

MAKER = "https://www.kotobukiya.co.jp"
PRODUCT_PATH = r"/product/detail/p[0-9]{13}/?"
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"


def anime_hack_news(url: str) -> str | None:
    parsed = urlparse(url)
    match = re.fullmatch(r"/news/([0-9]+)/?", parsed.path)
    return match[1] if match and parsed.netloc == "anime.eiga.com" else None


def is_anime_hack_photo(url: str, article_id: str) -> bool:
    parsed = urlparse(url)
    path = rf"/images/anime/news/{article_id}/photo/[\w-]+\.(?:jpe?g|png|webp)"
    return bool(
        parsed.scheme == "https"
        and parsed.netloc == "media.eiga.com"
        and re.fullmatch(path, parsed.path, re.I)
        and not parsed.query
        and not parsed.fragment
    )


def anime_hack_photos(soup: BeautifulSoup, article_id: str) -> tuple[list[str], dict[str, str]]:
    photos: dict[str, str] = {}
    for img in soup.select(
        ".articleContainer .newsDetailBox > [class^='newsDetailPhotoArea'] "
        "> .newsDetailPhoto > img[src]"
    ):
        url = urljoin("https://anime.eiga.com/", str(img["src"]))
        if is_anime_hack_photo(url, article_id):
            photos.setdefault(url, str(img.get("alt") or "")[:300])
    return list(photos), {url: caption for url, caption in photos.items() if caption}


def publisher_goods_article(source_id: str, url: str) -> bool:
    parsed = urlparse(url)
    return (
        (source_id, parsed.netloc)
        in {("anime_anime_goods", "animeanime.jp"), ("inside_goods", "www.inside-games.jp")}
        and parsed.scheme == "https"
        and bool(re.fullmatch(r"/article/\d{4}/\d{2}/\d{2}/\d+\.html", parsed.path))
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _product_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.kotobukiya.co.jp"
        and bool(re.fullmatch(PRODUCT_PATH, parsed.path))
        and not parsed.query
        and not parsed.fragment
    )


def _gallery_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.kotobukiya.co.jp"
        and not parsed.query
        and not parsed.fragment
        and bool(
            re.fullmatch(
                r"/(?:sm_files/co|files)/product/(?:[\w-]+/)+[\w.-]+\.(?:jpe?g|png|webp)",
                parsed.path,
                re.I,
            )
        )
    )


class ProductPhotoPermission(BaseModel):
    """An express license on an individual photo in the matched product gallery."""

    product_url: HttpUrl
    product_name: str
    article_url: HttpUrl
    license_url: Literal["https://creativecommons.org/publicdomain/zero/1.0/"]
    checked_at: str


def maker_photo_fields(permissions: Mapping, image_url: str) -> dict:
    record = permissions.get(image_url)
    if not record or not _gallery_url(image_url):
        return {}
    permission = ProductPhotoPermission.model_validate(record)
    if not _product_url(str(permission.product_url)) or not publisher_goods_article(
        "anime_anime_goods", str(permission.article_url)
    ):
        return {}
    source = SourceEvidence(
        id="kotobukiya_product",
        title=permission.product_name,
        url=permission.product_url,
        publisher="Kotobukiya",
        role="primary",
        is_official=True,
        region="JP",
        checked_at=permission.checked_at,
        discovered_via_source_id="anime_anime_goods",
        discovery_url=permission.article_url,
        link_relationship_type="product",
        target_work_match=True,
    )
    return {
        "official": True,
        "provenance_source": source,
        "source_url": source.url,
        "asset_kind": "goods_product",
        "rights_status": RightsStatus.ALLOWED,
        "rights_basis": RightsBasis.LICENSE,
        "rights_evidence_url": source.url,
        "rights_scope": ["instagram", "app", "commercial", "adapt"],
        "rights_checked_at": permission.checked_at,
        "attribution_text": f"Kotobukiya · {source.title} · CC0 1.0 ({CC0})",
    }


def compact_image_exclusions(payload: dict) -> dict:
    exclusions = [
        {key: row[key] for key in ("page_url", "notice_text", "reason") if key in row}
        for row in payload.get("image_exclusions", [])
    ]
    details = {"image_exclusions": exclusions} if exclusions else {}
    if name := payload.get("goods_subject"):
        details["goods_subject"] = name
    if url := payload.get("verified_product_url"):
        details["verified_product_url"] = str(url)
    return details


def verified_goods_product_url(package: ContentPackage) -> str | None:
    """Return the unique maker detail matched to this article's product sheet."""
    if package.content_type != "goods":
        return None
    urls: set[str] = set()
    for fact in package.facts:
        if not any(
            source.id in fact.source_ids and publisher_goods_article(source.id, str(source.url))
            for source in package.sources
        ):
            continue
        url = fact.details.get("verified_product_url")
        if url and _product_url(str(url)):
            urls.add(str(url))
        # Earlier captures recorded an exact manufacturer match in the image review.
        for row in fact.details.get("image_exclusions") or []:
            if isinstance(row, dict) and row.get("reason") in {
                "manufacturer_no_redistribution", "manufacturer_permission_unverified"
            } and _product_url(str(row.get("page_url", ""))):
                urls.add(str(row["page_url"]))
    return urls.pop() if len(urls) == 1 else None


def restricted_product_article(package: ContentPackage) -> str | None:
    if package.content_type != "goods":
        return None
    blocked = {
        row.get("page_url")
        for fact in package.facts
        for row in (fact.details.get("image_exclusions") or [])
        if isinstance(row, dict) and row.get("reason") == "explicit_no_redistribution_notice"
    }
    return next(
        (
            str(s.url)
            for s in package.sources
            if str(s.url) in blocked and publisher_goods_article(s.id, str(s.url))
        ),
        None,
    )


def licensed_product_image(image: ImageAsset, package: ContentPackage, article_url: str) -> bool:
    if image.asset_kind != "goods_product" or not image.cleared_for_mennu:
        return False
    # Existing explicit permissions remain valid for the package's product
    # source. A separate grant may also name a photo in the editorial article.
    if str(image.source_url) == article_url:
        return (
            image.rights_basis == RightsBasis.RIGHTS_HOLDER_PERMISSION
            and str(image.rights_evidence_url) != article_url
        )
    if any(
        source.url == image.source_url and source.is_official and source.role == "primary"
        for source in package.sources
    ):
        return True
    provenance = image.provenance_source
    return bool(
        provenance
        and provenance.is_official
        and provenance.role == "primary"
        and provenance.url == image.source_url
        and image.public_official_source_matches([provenance])
        and (
            str(provenance.discovery_url) == article_url
            or str(provenance.linked_from_url) == article_url
        )
        and provenance.link_relationship_type == "product"
        and provenance.target_work_match
    )


@dataclass
class ProductPhotoReview:
    page_url: str
    subject_name: str = ""
    matched_product_url: str | None = None
    reason: str = "manufacturer_product_unverified"
    notice: str = "The product sheet could not be linked to one exact manufacturer product."
    excluded_urls: list[str] = field(default_factory=list)
    permissions: dict[str, ProductPhotoPermission] = field(default_factory=dict)


def _cc0_gallery_images(soup: BeautifulSoup, gallery: set[str], page_url: str) -> set[str]:
    # A page-wide license, an offer price, or a generic press image is insufficient.
    # Read only a per-image declaration naming the exact gallery file.
    licensed = set()
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            pending = [json.loads(tag.string or tag.get_text())]
        except (ValueError, TypeError):
            continue
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
            if isinstance(node, dict):
                pending.extend(node.values())
                if node.get("@type") == "ImageObject" and node.get("license") == CC0:
                    url = urljoin(page_url, str(node.get("contentUrl") or ""))
                    if url in gallery:
                        licensed.add(url)
    return licensed


def review_product_photos(
    article: BeautifulSoup,
    article_url: str,
    source: Source,
    timeout: float,
    fetch_html: Callable,
    notice_text: Callable,
) -> ProductPhotoReview:
    """Follow one named manufacturer's catalogue and independently check its rights."""
    review = ProductPhotoReview(article_url)
    sheets = article.select("article.arti-body .enclosure")
    if len(sheets) != 1:
        return review
    sheet = unicodedata.normalize("NFKC", sheets[0].get_text("\n", strip=True))
    title = re.search(r"「([^「」\n]+)」\s*製品情報", sheet)
    name_scale = (
        re.search(r"(?:^|\s)(\S{2,100})\s+(\d+/\d+)\s*スケール", title[1]) if title else None
    )
    month = re.search(r"発売日\s*:\s*(20\d{2}年\d{1,2}月)", sheet)
    price = re.search(r"参考価格\s*:\s*([\d,]+)円", sheet)
    if not (
        name_scale and month and price and re.search(r"(?m)^ブランド\s*:\s*コトブキヤ\s*$", sheet)
    ):
        return review
    name, scale = name_scale.groups()
    work = title[1][: name_scale.start()].strip()
    if not work:
        return review
    review.subject_name = name
    # Only detail links in the product sheet or results of the manufacturer's own
    # exact-name search may supply the target. Retailer/affiliate links are ignored.
    detail_urls = {
        url
        for a in sheets[0].select("a[href]")
        if _product_url(url := urljoin(article_url, str(a["href"])))
    }
    maker_source = source.model_copy(update={"url": HttpUrl(MAKER), "allowed_asset_hosts": []})
    try:
        if not detail_urls:
            search_url = f"{MAKER}/search/?{urlencode({'fw': name})}"
            html, final = fetch_html(search_url, maker_source, timeout)
            if final != search_url:
                return review
            results = BeautifulSoup(html, "html.parser")
            for link in results.select(
                "article.contents_main .resultList a.resultList_itemInner[href]"
            ):
                label = link.select_one(".resultList_title")
                url = urljoin(final, str(link["href"]))
                if (
                    label
                    and _normalize(label.get_text(strip=True)) == _normalize(name)
                    and _product_url(url)
                ):
                    detail_urls.add(url)
        if len(detail_urls) != 1:
            return review
        target = next(iter(detail_urls))
        html, final = fetch_html(target, maker_source, timeout)
    except (httpx.HTTPError, ValueError) as exc:
        review.reason = "manufacturer_lookup_unavailable"
        review.notice = f"Manufacturer product lookup failed: {str(exc)[:200]}"
        return review
    if not _product_url(final) or final.rstrip("/") != target.rstrip("/"):
        return review
    product = BeautifulSoup(html, "html.parser")
    heading = product.select_one("h1.pageHeader02_title")
    specs = {
        row.th.get_text(strip=True): _normalize(row.td.get_text(strip=True))
        for row in product.select("article.contents_main tr")
        if row.th and row.td
    }
    price_tag = product.select_one("article.contents_main .detailHeader_price-taxIn")
    target_price = (
        re.match(r"^([\d,]+)円", _normalize(price_tag.get_text(strip=True))) if price_tag else None
    )
    if not (
        heading
        and _normalize(heading.get_text(strip=True)) == _normalize(name)
        and specs.get("作品") == _normalize(work)
        and specs.get("スケール") == scale
        and specs.get("初回発売月") == month[1]
        and target_price
        and target_price[1].replace(",", "") == price[1].replace(",", "")
    ):
        return review
    gallery = list(
        dict.fromkeys(
            url
            for img in product.select(
                "article.contents_main .detailSlider [data-modal-slider-gallery][data-src]"
            )
            if _gallery_url(url := urljoin(final, str(img["data-src"])))
        )
    )
    review.page_url = final
    review.matched_product_url = final
    if notice := notice_text(product):
        review.reason, review.notice, review.excluded_urls = (
            "manufacturer_no_redistribution",
            notice,
            gallery,
        )
        return review
    licensed = _cc0_gallery_images(product, set(gallery), final)
    permission = ProductPhotoPermission(
        product_url=final,
        product_name=name,
        article_url=article_url,
        license_url=CC0,
        checked_at=datetime.now(UTC).isoformat(),
    )
    review.permissions = {url: permission for url in gallery if url in licensed}
    review.excluded_urls = [url for url in gallery if url not in licensed]
    review.reason = "manufacturer_permission_unverified"
    review.notice = (
        "The manufacturer did not supply a verified reuse grant for these product photos."
    )
    return review
