"""Read named character art from an official, work-scoped character page."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ani_menu_agent.image_selection import goods_subject_names_match
from ani_menu_agent.models import ContentPackage, ImageAsset, SourceEvidence
from ani_menu_agent.sources.models import CharacterArtProfile, Source
from ani_menu_agent.work_aliases import work_character_alias_groups
from ani_menu_agent.work_catalog import exact_catalog_work_id


def _name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def goods_character_names(package: ContentPackage) -> list[str]:
    if package.content_type != "goods" or len(package.menu_work_ids) != 1:
        return []
    # Translations and nicknames may expand a search only after the original
    # article identifies exactly one subject in the matched work's dictionary.
    groups = [
        names for names in work_character_alias_groups(package.menu_work_ids[0])
        if goods_subject_names_match(package, names)
    ]
    if len(groups) == 1:
        return sorted(groups[0])
    return []


def _named_images(soup: BeautifulSoup, page: str, profile: CharacterArtProfile) -> list[str]:
    names = {_name(name) for name in profile.names}
    images = []
    # Generic character galleries can give the character's full name as image alt text.
    # Exclude links in site-wide navigation and related-story panels.
    for img in soup.select("main img[alt][src], [role=main] img[alt][src]"):
        if _name(str(img["alt"])) in names and not img.find_parent(
            ["aside", "nav", "header", "footer"]
        ):
            images.append(urljoin(page, str(img["src"])))
    # Some galleries use a graphic as the character heading. The registry identifies
    # its reviewed nameplates; take only the primary illustration in that same slide.
    plates = {urljoin(page, path) for path in profile.nameplates}
    if plates:
        for slide in reversed(soup.select("#js_character .charaslide")):
            label = slide.select_one(
                ":scope > .charaslide_data > .charaslide_data_ttl[data-imgload]"
            )
            art = slide.select_one(":scope > .charaslide_img[data-imgload]")
            if label and art and urljoin(page, str(label["data-imgload"])) in plates:
                images.append(urljoin(page, str(art["data-imgload"])))
    return list(dict.fromkeys(images))


def matched_character_profile(
    package: ContentPackage,
    sources: Iterable[Source],
) -> tuple[Source, CharacterArtProfile] | None:
    if package.content_type != "goods" or len(package.menu_work_ids) != 1:
        return None
    originals = {str(s.url): s.id for s in package.sources if s.role in {"primary", "editorial"}}
    named = {
        name
        for fact in package.facts
        if not set(fact.source_ids).isdisjoint(originals.values())
        if isinstance(name := fact.details.get("goods_subject"), str)
        and 2 <= len(name.strip()) <= 120
    }
    direct = CharacterArtProfile(names=[named.pop().strip()]) if len(named) == 1 else None
    # Only use a verified character-page link traced to the original article.
    # A registered work-specific official host supplies the independent ownership check.
    linked_pages = [
        s.url
        for s in package.sources
        if s.target_work_match
        and s.link_classification == "official_verified"
        and str(s.linked_from_url) in originals
        and re.search(r"/characters?(?:/|$)", s.url.path or "", re.I)
    ]
    matches = {}
    for source in sources:
        if (
            source.status != "active"
            or source.role != "primary"
            or source.collection != "html"
            or not source.work_title_ko
            or exact_catalog_work_id(source.work_title_ko) != package.menu_work_ids[0]
        ):
            continue
        if source.category == "official_character_art" or source.character_art_profiles:
            profiles = [
                p
                for p in source.character_art_profiles
                if goods_subject_names_match(package, set(p.names))
            ]
            if len(profiles) > 1:
                return None
            if profile := (profiles[0] if profiles else direct):
                matches[str(source.url)] = (source, profile)
        if direct:
            for url in linked_pages:
                if url.scheme == "https" and url.host == source.url.host:
                    matches.setdefault(str(url), (source.model_copy(update={"url": url}), direct))
    return next(iter(matches.values())) if len(matches) == 1 else None


def collect_goods_character_art(
    package: ContentPackage,
    profile_source: tuple[Source, CharacterArtProfile],
    limit: int,
    *,
    fetch_html: Callable,
    redistribution_notice: Callable,
) -> list[ImageAsset]:
    if limit <= 0:
        return []
    source, profile = profile_source
    try:
        html, final_url = fetch_html(str(source.url), source, 12.0)
    except (httpx.HTTPError, ValueError):
        return []
    if final_url.rstrip("/") != str(source.url).rstrip("/"):
        return []
    soup = BeautifulSoup(html, "html.parser")
    if redistribution_notice(soup):
        return []
    provenance = SourceEvidence(
        id=source.id,
        title=profile.names[0],
        url=final_url,
        publisher=source.name,
        is_official=True,
        role="primary",
        region=source.region,
        checked_at=datetime.now(UTC).isoformat(),
        allowed_asset_hosts=source.allowed_asset_hosts,
        link_relationship_type="character",
        target_work_match=True,
    )
    result = []
    seen = {str(image.url) for image in package.images}
    for url in _named_images(soup, final_url, profile):
        parsed = urlparse(url)
        if (
            url in seen
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not re.search(r"\.(?:png|webp|jpe?g)$", parsed.path, re.I)
        ):
            continue
        asset = ImageAsset(
            id=f"{source.id}-{hashlib.sha1(url.encode()).hexdigest()[:14]}",
            url=url,
            source_url=provenance.url,
            provenance_source=provenance,
            official=True,
            menu_work_ids=package.menu_work_ids,
            asset_kind="character_art",
            depicted_entities=profile.names,
            asset_metadata_confidence=1.0,
            source_description=f"{source.work_title_ko} · {profile.names[0]} · 작품 일러스트",
        )
        if asset.public_official_source_matches([provenance]):
            result.append(asset)
            seen.add(url)
        if len(result) >= limit:
            break
    return result
