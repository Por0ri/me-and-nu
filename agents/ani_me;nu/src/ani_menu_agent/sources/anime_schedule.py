"""Identify a season profile and its own AnimeSchedule poster rendition."""

import re
from hashlib import sha1
from urllib.parse import unquote, urlsplit


def profile_slug(url: str) -> str:
    page = urlsplit(url)
    if page.hostname not in {"animeschedule.net", "www.animeschedule.net"}:
        return ""
    match = re.fullmatch(r"/anime/([^/]+)/?", unquote(page.path))
    return match.group(1).casefold() if match else ""


def source_item_key(source_id: str, url: str, feed_entry_guid: str | None = None) -> str:
    """Episodes share their season URL. Use the feed's actual item ID where available."""
    guid = (feed_entry_guid or "").strip()
    if source_id == "anime_schedule_rss" and guid and profile_slug(url):
        if guid.rstrip("/").casefold() != url.rstrip("/").casefold():
            return f"anime_schedule_rss:guid:{guid}"
    return url


def source_item_digest(source_id: str, url: str, feed_entry_guid: str | None = None) -> str:
    return sha1(source_item_key(source_id, url, feed_entry_guid).encode("utf-8")).hexdigest()


def legacy_episode_key(source_id: str, url: str, title: str) -> tuple[str, str] | None:
    """Legacy attempts recorded the feed's exact episode headline, but not its GUID."""
    if source_id != "anime_schedule_rss" or not re.fullmatch(r"Episode \d+(?:\.\d+)? of .+ is out!", title.strip(), re.I):
        return None
    slug = profile_slug(url)
    return (slug, title.strip().casefold()) if slug else None


def is_profile_poster(url: str, slug: str) -> bool:
    asset = urlsplit(url)
    path = unquote(asset.path).casefold()
    if asset.hostname != "img.animeschedule.net" or "/img/anime/" not in path or not slug:
        return False
    filename = path.rsplit("/", 1)[-1]
    return re.fullmatch(re.escape(slug) + r"(?:-[0-9a-f]{6,64})?\.(?:jpg|jpeg|png|webp|avif)", filename) is not None
