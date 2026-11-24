"""Resolve a Kitsu anime poster/cover from the provider's image ID and exact season."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from ani_rag.menu_dict_reference import DEFAULT_MENU_DICT_DIR
from ani_rag.season_crosswalk import provider_season_owners
from ani_rag.work_registry import kitsu_page_id


def poster_owners(official_entries: Path, external_ids: Path = DEFAULT_MENU_DICT_DIR / "external_id.csv") -> dict[str, int]:
    return provider_season_owners(official_entries, external_ids, "kitsu")


def poster_owner(source_id: str, image_url: str, page_url: str, owners: dict[str, int]) -> int | None:
    if source_id != "kitsu_anime_catalog":
        return None
    image = urlsplit(image_url)
    if image.scheme != "https" or image.hostname not in {"media.kitsu.app", "media.kitsu.io"}:
        return None
    match = re.fullmatch(r"/anime/(?:poster_images|cover_images)/([1-9][0-9]*)/[^/]+", image.path)
    if match is None or kitsu_page_id(page_url) != match[1]:
        return None
    return owners.get(match[1])


def collection_artwork_owner(source_id: str, image_url: str, page_url: str, owners: dict[str, int]) -> int | None:
    """One source-specific appearance. A character image can have several such source records."""
    owner = poster_owner(source_id, image_url, page_url, owners)
    if owner is not None or source_id != "kitsu_anime_catalog":
        return owner
    page, image = urlsplit(page_url), urlsplit(image_url)
    if image.scheme != "https" or image.hostname not in {"media.kitsu.app","media.kitsu.io"}:
        return None
    for endpoint, image_kind in (("episodes","episodes/thumbnails"),("characters","characters/images")):
        if page.path.endswith("/" + endpoint) and re.fullmatch(fr"/{image_kind}/[1-9][0-9]*/[^/]+",image.path):
            base = page._replace(path=page.path.rsplit("/",1)[0],query="",fragment="").geturl()
            return owners.get(kitsu_page_id(base))
    return None
