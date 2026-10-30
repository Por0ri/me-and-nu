"""Build the name -> work dictionaries for headlines that never name their work.

A headline such as "ロック・リーのグッズが登場" or "SPYAIR 내한" names a character or an
artist instead of the anime. These files map such names to menu_work_id so the work can be
resolved from data instead of a model's guess.

    uv run python scripts/build_work_match_data.py
    uv run python scripts/build_work_match_data.py --only characters

Sources, both open to bulk use:
- Wikidata (CC0): entities "present in work" (P1441) of anime items that carry an AniList
  anime ID (P8729), with their Japanese, English, and Korean labels and aliases.
- AnimeThemes: OP/ED song titles and artists per anime, queried by AniList ID in batches at
  a low rate (robots.txt Content-Signal use=reference).
AniList itself is not bulk-collected: its API terms forbid mass collection of data.

Writes data/reference/work_characters.csv and data/reference/work_theme_songs.csv.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "reference"
USER_AGENT = "me-nu-content-agent/0.1 (work-match dictionary build; low-rate)"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
ANIMETHEMES_ANIME = "https://api.animethemes.moe/anime"
WIKIDATA_BATCH = 200
ANIMETHEMES_BATCH = 50
PAUSE_SECONDS = 2.0
_VIA_RANK = {"anime": 0, "source": 1, "series": 2}

# Characters are often attached to the source manga or the franchise rather than to the
# anime item itself, so the anime's source work (P144) and series (P179) count too.
CHARACTER_QUERY = """
SELECT ?anilist ?entity ?via ?name (LANG(?name) AS ?lang) ?kind WHERE {
  VALUES ?anilist { %s }
  ?work wdt:P8729 ?anilist .
  { ?entity wdt:P1441 ?work . BIND("anime" AS ?via) }
  UNION { ?work wdt:P144 ?source . ?entity wdt:P1441 ?source . BIND("source" AS ?via) }
  UNION { ?work wdt:P179 ?series . ?entity wdt:P1441 ?series . BIND("series" AS ?via) }
  { ?entity rdfs:label ?name . BIND("label" AS ?kind) }
  UNION
  { ?entity skos:altLabel ?name . BIND("alias" AS ?kind) }
  FILTER(LANG(?name) IN ("ja", "en", "ko"))
}
"""


def anilist_index() -> dict[str, int]:
    """AniList anime ID -> menu_work_id from the catalog (every season/entry ID)."""
    index: dict[str, int] = {}
    with (REFERENCE / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            for value in (row.get("anilist_ids") or "").split(";"):
                if value.strip().isdigit():
                    index.setdefault(value.strip(), int(row["menu_work_id"]))
    return index


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """GET with a polite pause, waiting out 429s instead of retrying in a burst."""
    for attempt in range(4):
        time.sleep(PAUSE_SECONDS)
        response = client.get(url, params=params)
        if response.status_code == 429 and attempt < 3:
            time.sleep(min(120.0, float(response.headers.get("Retry-After", "60"))))
            continue
        response.raise_for_status()
        return response
    raise RuntimeError(f"rate limit did not clear for {url}")


def build_characters(index: dict[str, int]) -> list[dict]:
    rows: dict[tuple, dict] = {}
    headers = {"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT}
    ids = sorted(index, key=int)
    with httpx.Client(headers=headers, timeout=120) as client:
        for number, batch in enumerate(_batches(ids, WIKIDATA_BATCH), start=1):
            values = " ".join(f'"{value}"' for value in batch)
            response = _get(client, WIKIDATA_SPARQL, {"query": CHARACTER_QUERY % values})
            for binding in response.json()["results"]["bindings"]:
                anilist = binding["anilist"]["value"]
                row = {
                    "menu_work_id": index[anilist],
                    "anilist_id": anilist,
                    "wikidata_id": binding["entity"]["value"].rsplit("/", 1)[-1],
                    "via": binding["via"]["value"],
                    "lang": binding["lang"]["value"],
                    "kind": binding["kind"]["value"],
                    "name": binding["name"]["value"].strip(),
                }
                key = (row["menu_work_id"], row["wikidata_id"], row["lang"], row["name"])
                # The most direct link wins: anime item, then source work, then series.
                previous = rows.get(key)
                if row["name"] and (previous is None or _VIA_RANK[row["via"]] < _VIA_RANK[
                    previous["via"]
                ]):
                    rows[key] = row
            print(f"  wikidata batch {number}: {len(rows)} names so far", flush=True)
    return sorted(
        rows.values(),
        key=lambda row: (row["menu_work_id"], row["wikidata_id"], row["lang"], row["name"]),
    )


def build_theme_songs(index: dict[str, int]) -> list[dict]:
    rows: dict[tuple, dict] = {}
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    ids = sorted(index, key=int)
    with httpx.Client(headers=headers, timeout=60) as client:
        for number, batch in enumerate(_batches(ids, ANIMETHEMES_BATCH), start=1):
            params = {
                "filter[has]": "resources",
                "filter[site]": "AniList",
                "filter[external_id]": ",".join(batch),
                "include": "resources,animethemes.song.artists",
                "fields[anime]": "name,slug",
                "fields[animetheme]": "type,sequence",
                "fields[song]": "title",
                "fields[artist]": "name",
                "fields[resource]": "site,external_id",
                "page[size]": "100",
            }
            url: str | None = ANIMETHEMES_ANIME
            while url:
                response = _get(client, url, params)
                payload = response.json()
                for anime in payload.get("anime") or []:
                    anilist_ids = [
                        str(resource.get("external_id"))
                        for resource in anime.get("resources") or []
                        if resource.get("site") == "AniList"
                        and str(resource.get("external_id")) in index
                    ]
                    for anilist in anilist_ids[:1]:
                        for theme in anime.get("animethemes") or []:
                            song = theme.get("song") or {}
                            title = (song.get("title") or "").strip()
                            if not title:
                                continue
                            row = {
                                "menu_work_id": index[anilist],
                                "anilist_id": anilist,
                                "theme": f"{theme.get('type')}{theme.get('sequence') or ''}",
                                "song_title": title,
                                "artists": " / ".join(
                                    artist["name"] for artist in song.get("artists") or []
                                ),
                                "animethemes_anime": anime.get("slug") or "",
                            }
                            key = (row["menu_work_id"], row["theme"], row["song_title"])
                            rows.setdefault(key, row)
                url = (payload.get("links") or {}).get("next")
                params = None  # the next link already carries the query
            print(f"  animethemes batch {number}: {len(rows)} songs so far", flush=True)
    return sorted(
        rows.values(), key=lambda row: (row["menu_work_id"], row["theme"], row["song_title"])
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        print(f"no rows for {path.name}; left unchanged")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    works = len({row["menu_work_id"] for row in rows})
    print(f"wrote {len(rows)} rows for {works} works -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=["characters", "theme_songs"])
    args = parser.parse_args()
    index = anilist_index()
    print(f"{len(index)} AniList IDs across {len(set(index.values()))} catalog works")
    if args.only in (None, "characters"):
        write_csv(REFERENCE / "work_characters.csv", build_characters(index))
    if args.only in (None, "theme_songs"):
        write_csv(REFERENCE / "work_theme_songs.csv", build_theme_songs(index))


if __name__ == "__main__":
    main()
