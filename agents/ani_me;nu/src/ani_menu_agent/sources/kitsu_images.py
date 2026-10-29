"""Fetch exact-work catalog artwork from the already-approved public Kitsu API."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import HttpUrl

from ani_menu_agent.models import ImageAsset, RightsStatus, SourceEvidence
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source
from ani_menu_agent.work_catalog import load_work_catalog

MAX_IMAGES = 20
MAX_CHARACTERS_PER_WORK = 12
# Kitsu's documented maximum page size is 20. Larger values fail the episode
# relationship request, which silently removes a useful source of scene art.
MAX_EPISODES_PER_WORK = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _normalized_title(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _titles(attributes: dict[str, Any]) -> set[str]:
    values = [attributes.get("canonicalTitle")]
    titles = attributes.get("titles")
    if isinstance(titles, dict):
        values.extend(titles.values())
    elif isinstance(titles, list):
        values.extend(titles)
    return {_normalized_title(str(value)) for value in values if isinstance(value, str) and value}


def _get_json(client: httpx.Client, url: str, source: Source) -> dict[str, Any]:
    pace_host(url, source.min_interval_seconds)
    response = client.get(url)
    response.raise_for_status()
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("Kitsu image reference response exceeds the 5MB collection limit.")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Kitsu returned a non-object API response.")
    return payload


def collect_kitsu_work_images(
    work_ids: list[int],
    source: Source,
    *,
    max_images: int = MAX_IMAGES,
    timeout: float = 20.0,
) -> list[ImageAsset]:
    """Return only poster/character art whose Kitsu titles exactly match the work catalog.

    Kitsu is used as an image-reference source, not as evidence for article facts.
    Every image keeps the exact API endpoint and catalog provenance for the risk
    and relevance checks downstream.
    """
    if (
        source.status != "active"
        or source.role != "reference"
        or source.category != "kitsu_anime_catalog"
        or source.collection != "api"
        or max_images < 1
    ):
        return []
    catalog = {
        int(row["menu_work_id"]): row
        for row in load_work_catalog()
        if row.get("menu_work_id") is not None
    }
    assets: list[ImageAsset] = []
    seen_urls: set[str] = set()
    headers = {
        "Accept": "application/vnd.api+json, application/json",
        "User-Agent": "me-nu-content-agent/0.3 (public catalog image references)",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for work_id in work_ids:
            if len(assets) >= max_images or work_id not in catalog:
                break
            work = catalog[work_id]
            expected_titles = {
                _normalized_title(str(work.get(key) or ""))
                for key in ("title_ko", "title_romaji", "title_english", "title_native")
                if work.get(key)
            }
            queries = [
                str(work.get(key) or "").strip()
                for key in ("title_english", "title_romaji", "title_native", "title_ko")
                if work.get(key)
            ]
            matched: dict[str, Any] | None = None
            for query in queries:
                search_params = httpx.QueryParams(
                    {"filter[text]": query, "page[limit]": 10}
                )
                try:
                    payload = _get_json(
                        client,
                        f"{str(source.url).rstrip('/')}?{search_params}",
                        source,
                    )
                except (httpx.HTTPError, ValueError, KeyError):
                    continue
                for row in payload.get("data") or []:
                    if not isinstance(row, dict) or not isinstance(row.get("attributes"), dict):
                        continue
                    if _titles(row["attributes"]) & expected_titles:
                        matched = row
                        break
                if matched:
                    break
            if not matched:
                continue

            kitsu_id = str(matched.get("id") or "")
            attributes = matched["attributes"]
            selected_title = next(
                (
                    str(attributes.get(key))
                    for key in ("canonicalTitle",)
                    if attributes.get(key)
                ),
                str(work.get("title_english") or work.get("title_romaji") or "Anime"),
            )
            anime_endpoint = f"{str(source.url).rstrip('/')}/{kitsu_id}"
            poster = (attributes.get("posterImage") or {}).get("original")
            for image_url, asset_kind, label in (
                (poster, "catalog_poster", "poster"),
                ((attributes.get("coverImage") or {}).get("original"), "catalog_cover", "cover"),
            ):
                if not isinstance(image_url, str) or image_url in seen_urls:
                    continue
                evidence = SourceEvidence(
                    id="kitsu_anime_catalog",
                    title=f"{selected_title} — Kitsu catalog {label} reference",
                    url=HttpUrl(anime_endpoint),
                    publisher=source.name,
                    is_official=False,
                    role="reference",
                    region="global",
                    allowed_asset_hosts=source.allowed_asset_hosts,
                )
                asset = _asset(
                    image_url,
                    evidence,
                    work_id,
                    f"Kitsu catalog {label} — {selected_title}",
                    asset_kind,
                    [],
                    0.98,
                )
                if asset and asset.url:
                    assets.append(asset)
                    seen_urls.add(str(asset.url))
            if len(assets) >= max_images:
                break

            character_endpoint = (
                f"{anime_endpoint}/characters?page[limit]={MAX_CHARACTERS_PER_WORK}"
                "&include=character"
            )
            try:
                character_payload = _get_json(client, character_endpoint, source)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            included = {
                str(item.get("id")): item
                for item in character_payload.get("included", [])
                if isinstance(item, dict) and item.get("type") == "characters"
            }
            relations = [
                item
                for item in character_payload.get("data", [])
                if isinstance(item, dict)
            ]
            relations.sort(
                key=lambda item: (
                    (item.get("attributes") or {}).get("role") != "main",
                    (item.get("attributes") or {}).get("role") != "supporting",
                )
            )
            # Kitsu character portraits are frequently only 225x350 and fail
            # the unchanged editorial-resolution floor. Reserve slots for
            # full-size episode stills so low-resolution art cannot exhaust
            # the request budget before usable visuals are collected.
            episode_reserve = min(4, max_images - len(assets))
            character_image_limit = max_images - len(assets) - episode_reserve
            character_images_added = 0
            evidence = SourceEvidence(
                id="kitsu_anime_catalog",
                title=f"{selected_title} — Kitsu character image references",
                url=HttpUrl(character_endpoint),
                publisher=source.name,
                is_official=False,
                role="reference",
                region="global",
                allowed_asset_hosts=source.allowed_asset_hosts,
            )
            for relation in relations:
                if (
                    len(assets) >= max_images
                    or character_images_added >= character_image_limit
                ):
                    break
                relationship = (relation.get("relationships") or {}).get("character") or {}
                character_ref = relationship.get("data") or {}
                character = included.get(str(character_ref.get("id") or ""))
                if not character:
                    continue
                char_attributes = character.get("attributes") or {}
                image_url = (char_attributes.get("image") or {}).get("original")
                if not isinstance(image_url, str) or image_url in seen_urls:
                    continue
                character_names = [char_attributes.get("canonicalName")]
                aliases = char_attributes.get("names")
                if isinstance(aliases, dict):
                    character_names.extend(aliases.values())
                names = list(
                    dict.fromkeys(
                        str(name).strip()
                        for name in character_names
                        if isinstance(name, str) and name.strip()
                    )
                )
                character_label = names[0] if names else "character"
                asset = _asset(
                    image_url,
                    evidence,
                    work_id,
                    f"Kitsu catalog character art — {character_label} · {selected_title}",
                    "character_art",
                    names,
                    0.95,
                )
                if asset:
                    assets.append(asset)
                    seen_urls.add(image_url)
                    character_images_added += 1
            if len(assets) >= max_images:
                break

            episode_endpoint = (
                f"{anime_endpoint}/episodes?page[limit]={MAX_EPISODES_PER_WORK}"
            )
            try:
                episode_payload = _get_json(client, episode_endpoint, source)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            episode_evidence = SourceEvidence(
                id="kitsu_anime_catalog",
                title=f"{selected_title} — Kitsu episode still references",
                url=HttpUrl(episode_endpoint),
                publisher=source.name,
                is_official=False,
                role="reference",
                region="global",
                allowed_asset_hosts=source.allowed_asset_hosts,
            )
            for episode in episode_payload.get("data", []):
                if len(assets) >= max_images:
                    break
                if not isinstance(episode, dict):
                    continue
                episode_attributes = episode.get("attributes") or {}
                thumbnail = (episode_attributes.get("thumbnail") or {}).get("original")
                if not isinstance(thumbnail, str) or thumbnail in seen_urls:
                    continue
                episode_title = str(episode_attributes.get("canonicalTitle") or "").strip()
                description = (
                    f"Kitsu episode still — {episode_title or 'episode'} · {selected_title}"
                )
                asset = _asset(
                    thumbnail,
                    episode_evidence,
                    work_id,
                    description,
                    "scene_still",
                    [],
                    0.90,
                )
                if asset:
                    assets.append(asset)
                    seen_urls.add(thumbnail)
    return assets


def _asset(
    image_url: str,
    evidence: SourceEvidence,
    work_id: int,
    description: str,
    asset_kind: str,
    entities: list[str],
    confidence: float,
) -> ImageAsset | None:
    parsed = urlparse(image_url)
    allowed_hosts = {
        host.lower().rstrip(".")
        for host in evidence.allowed_asset_hosts
    }
    asset_host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not asset_host or not any(
        asset_host == host or asset_host.endswith(f".{host}") for host in allowed_hosts
    ):
        return None
    image_id = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:16]
    return ImageAsset(
        id=f"kitsu-{image_id}",
        url=HttpUrl(image_url),
        source_url=evidence.url,
        provenance_source=evidence,
        menu_work_ids=[work_id],
        official=False,
        rights_status=RightsStatus.NEEDS_REVIEW,
        source_description=description[:240],
        asset_kind=asset_kind,
        depicted_entities=entities[:20],
        asset_metadata_confidence=confidence,
        # Exact-work reference art is not fact evidence for a card; factual
        # association is added only when a specific source fact is verified.
        visual_match="context",
        attribution_text=(
            f"공개 Kitsu 카탈로그 이미지 참고: {evidence.title} "
            "(사용 허가 확인과는 별개)"
        ),
    )
