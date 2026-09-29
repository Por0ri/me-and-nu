"""Fill empty content.image_url with posters / album covers.

Preview: python -m app.seeds.content_images
Apply:   python -m app.seeds.content_images --apply
Redo:    python -m app.seeds.content_images --apply --refresh   (overwrite earlier TMDB images)

Movies and anime use TMDB (needs TMDB_KEY in backend/.env). A still image
without text (backdrop) is preferred so the card title stays readable; the
poster is the fallback. Music uses
MusicBrainz + Cover Art Archive (no key). Only rows whose image_url is empty
and that came from the committed Agent article import are touched; the work
name comes from agent/contents/contents.json (the same file the import used).
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text

from app.core.config import settings

CONTENTS_PATH = Path(__file__).resolve().parents[3] / "agent" / "contents" / "contents.json"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w780"


def tmdb_image(item: dict) -> str | None:
    path = item.get("backdrop_path") or item.get("poster_path")
    return TMDB_IMAGE + path if path else None
USER_AGENT = "me-nu-local-demo/0.1 (school project)"


def parse_title_year(work: str) -> tuple[str, int | None]:
    """'알렉산더 (2004)' -> ('알렉산더', 2004)"""
    match = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", work.strip())
    return (match.group(1).strip(), int(match.group(2))) if match else (work.strip(), None)


def parse_album(work: str) -> tuple[str, str] | None:
    """'Queen(퀸) - Innuendo (1991)' -> ('Queen', 'Innuendo')"""
    title, _ = parse_title_year(work)
    if " - " not in title:
        return None
    artist, album = title.split(" - ", 1)
    artist = re.sub(r"\(.*?\)", "", artist).strip()
    return artist, album.strip()


def anime_query(article: dict) -> list[str]:
    """《블리치 천년혈전 편 화진담》 first, then the series name from the file name."""
    queries = re.findall(r"《(.+?)》", article.get("title", ""))
    parts = Path(article.get("origin", "")).name.split("_")
    if len(parts) > 1:
        queries.append(parts[1])
    return list(dict.fromkeys(q for q in queries if q))


def tmdb_get(client: httpx.Client, path: str, **params) -> dict:
    response = client.get(
        f"https://api.themoviedb.org/3{path}",
        params={"api_key": settings.tmdb_key, "language": "ko-KR", **params},
    )
    response.raise_for_status()
    return response.json()


def find_movie_poster(client: httpx.Client, work: str) -> str | None:
    title, year = parse_title_year(work)
    for extra in ({"year": year} if year else {}, {}):
        results = tmdb_get(client, "/search/movie", query=title, **extra).get("results", [])
        for item in results:
            if tmdb_image(item):
                return tmdb_image(item)
    return None


def find_anime_poster(client: httpx.Client, article: dict) -> str | None:
    for query in anime_query(article):
        for path in ("/search/tv", "/search/movie"):
            results = tmdb_get(client, path, query=query).get("results", [])
            # 애니메이션 장르(16)를 먼저 고른다.
            results.sort(key=lambda item: 16 not in item.get("genre_ids", []))
            for item in results:
                if tmdb_image(item):
                    return tmdb_image(item)
    return None


def find_album_cover(client: httpx.Client, work: str) -> str | None:
    parsed = parse_album(work)
    if not parsed:
        return None
    artist, album = parsed
    for attempt in range(3):
        time.sleep(1.1 * (attempt + 1))  # MusicBrainz 요청 제한(초당 1회)
        response = client.get(
            "https://musicbrainz.org/ws/2/release-group/",
            params={"query": f'releasegroup:"{album}" AND artist:"{artist}"', "fmt": "json", "limit": 5},
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code != 503:
            break
    response.raise_for_status()
    for group in response.json().get("release-groups", []):
        cover = f"https://coverartarchive.org/release-group/{group['id']}/front-500"
        check = client.head(cover, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        if check.status_code == 200:
            return cover
    return None


async def run(apply: bool, refresh: bool) -> int:
    from app.db.session import AsyncSessionLocal, engine

    articles = {item["id"]: item for item in json.loads(CONTENTS_PATH.read_text(encoding="utf-8"))}
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text(
                """
                select c.content_id, ar.input_payload #>> '{import,item_id}' as item_id
                from content c
                join draft d on d.draft_id = c.source_draft_id
                join agent_run ar on ar.agent_run_id = d.agent_run_id
                where coalesce(c.image_url, '') = ''
                   or (:refresh and c.image_url like 'https://image.tmdb.org/%')
                order by c.content_id
                """
            ), {"refresh": refresh})).all()
            found = {}
            with httpx.Client(timeout=15) as client:
                for content_id, item_id in rows:
                    article = articles.get(item_id)
                    if not article:
                        continue
                    agent, work = article.get("agent"), article.get("work", "")
                    try:
                        if agent.startswith("movie"):
                            url = find_movie_poster(client, work) if settings.tmdb_key else None
                        elif agent == "anime":
                            url = find_anime_poster(client, article) if settings.tmdb_key else None
                        elif agent == "music":
                            url = find_album_cover(client, work)
                        else:
                            url = None
                    except httpx.HTTPError as exc:
                        print(f"  ! {content_id} {work}: {exc}")
                        url = None
                    print(f"{'O' if url else 'X'} {content_id:>3} [{agent}] {work or article.get('title')}")
                    if url:
                        found[content_id] = url
            if apply and found:
                for content_id, url in found.items():
                    await db.execute(
                        text(
                            "update content set image_url = :url, updated_at = now() where content_id = :id"
                            " and (coalesce(image_url, '') = '' or (:refresh and image_url like 'https://image.tmdb.org/%'))"
                        ),
                        {"url": url, "id": content_id, "refresh": refresh},
                    )
                await db.commit()
            print(f"\n{len(found)}/{len(rows)} found" + (" — saved" if apply else " — preview only (add --apply to save)"))
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write image_url to the database")
    parser.add_argument("--refresh", action="store_true", help="also replace images that came from TMDB earlier")
    args = parser.parse_args()
    if not settings.tmdb_key:
        print("TMDB_KEY가 없어 영화·애니는 건너뜁니다.", file=sys.stderr)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(run(args.apply, args.refresh)))


if __name__ == "__main__":
    main()
