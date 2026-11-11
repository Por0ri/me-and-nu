"""AnimeThemes → 정보사전: 주제가(OP/ED) · 곡 · 가수 · 회차

  uv run python -m ani_rag.load_animethemes        (agents/rag 폴더에서)

- 요청 수: 시즌/극장판 수 ÷ 100 (3,887개면 39번), 앞 요청 시작에서 1.2초 뒤 다음 요청 (제한 분당 90회)
- 받은 원본은 .cache/raw/animethemes/<날짜>/ 에 저장. 같은 날 다시 돌리면 받은 쪽은 다시 요청하지 않음
- 곡은 대상(kind = song)으로 만든다. 가수는 AnimeThemes 에 붙은 AniList 인물 번호로 우리 인물에 연결하고,
  우리 사전에 없으면 새 인물로 만든다
- 이 스크립트가 맡는 칸 (출처 animethemes): theme_song (시즌 → 곡, 종류 · 번호 · 회차), performer (곡 → 가수),
  song_link (곡의 음원 · 영상 링크)
- 곡 이름은 대부분 로마자뿐 (일본어 원제는 MusicBrainz 로 보강할 예정)
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, unique_pairs

AT = "animethemes"
URL = "https://graphql.animethemes.moe/"
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.2
BATCH = 100
FIELDS = ("theme_song", "performer", "song_link")
QUERY = """query ($id: [Int!]) { findAnimeByExternalSite(site: ANILIST, id: $id) {
  id slug resources(site: ANILIST) { edges { node { externalId } } }
  animethemes { id type sequence
    song { id title { romaji native }
      resources { edges { node { site link } } }
      performances { as artist { id slug name { main native }
        resources { edges { node { site externalId link } } } } } }
    animethemeentries { episodes version } } } }"""


def post(ids: list[int]) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"id": ids}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 60)
                print(f"  제한에 걸림 → {wait + 1}초 쉼", flush=True)
                time.sleep(wait + 1)
            elif e.code >= 500:
                time.sleep(20 * (attempt + 1))
            else:
                raise
        except urllib.error.URLError:
            time.sleep(20 * (attempt + 1))
    raise SystemExit("AnimeThemes 요청이 5번 연속 실패함")


def fetch_all(ids: list[int], raw_dir) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    batches = [ids[s:s + BATCH] for s in range(0, len(ids), BATCH)]
    todo = [(n, b) for n, b in enumerate(batches) if not (raw_dir / f"page_{n:03d}.json").exists()]
    print(f"요청: 전체 {len(batches)}번 중 남은 {len(todo)}번", flush=True)
    last = 0.0
    for n, batch in todo:
        time.sleep(max(0.0, last + PAUSE - time.time()))
        last = time.time()
        data = post(batch)
        if data.get("errors"):
            raise SystemExit(f"AnimeThemes 오류: {data['errors'][:1]}")
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "data": data}
        (raw_dir / f"page_{n:03d}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def anilist_ids(resources: dict | None) -> list[str]:
    """필터 없이 받은 외부 번호 중 AniList 것만"""
    return [str(e["node"]["externalId"]) for e in (resources or {}).get("edges") or []
            if e["node"]["externalId"] and e["node"].get("site", "ANILIST") == "ANILIST"]


def site_ids(resources: dict | None, site: str) -> list[str]:
    return [str(e["node"]["externalId"]) for e in (resources or {}).get("edges") or []
            if e["node"]["externalId"] and e["node"].get("site") == site]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "animethemes" / dt.date.today().strftime("%Y%m%d")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'anime'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
    fetch_all(ids, raw_dir)

    records = []
    for path in sorted(raw_dir.glob("page_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        at = dt.datetime.fromisoformat(record["fetched_at"])
        records += [(at, a) for a in record["data"]["data"]["findAnimeByExternalSite"]]

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        entry_ids = store.external_map("anilist", "anime")
        person_ids = store.external_map("anilist", "staff")

        # 1) 곡 · 가수 대상
        songs, new_artists, linked_artists, artist_anilist = {}, {}, {}, {}
        for _, a in records:
            for t in a["animethemes"]:
                s = t.get("song")
                if not s:
                    continue
                songs[str(s["id"])] = (None, s["title"].get("native"), s["title"].get("romaji"))
                for p in s.get("performances") or []:
                    artist = p.get("artist")
                    if not artist:
                        continue
                    aid, als = str(artist["id"]), anilist_ids(artist.get("resources"))
                    existing = next((person_ids[x] for x in als if x in person_ids), None)
                    if existing:
                        linked_artists[aid] = existing
                    else:
                        new_artists[aid] = (None, artist["name"].get("native"), artist["name"].get("main"))
                        if als:
                            artist_anilist[aid] = als[0]
        song_ids, songs_made, _ = store.upsert_external(AT, "song", "song", songs)
        store.add_external_ids(AT, "artist", linked_artists)
        artist_ids, artists_made, _ = store.upsert_external(AT, "artist", "person", new_artists)
        artist_ids = store.external_map(AT, "artist")
        # 가수의 MAL 인물 번호 · ANN 인물 번호도 붙여 둠 (위키데이터 연결 · 검산용)
        for site, source, kind in (("MAL", "mal", "people"), ("ANN", "ann", "person")):
            found = []
            for _, a in records:
                for t in a["animethemes"]:
                    for p in (t.get("song") or {}).get("performances") or []:
                        artist = p.get("artist")
                        if artist and str(artist["id"]) in artist_ids:
                            pid = artist_ids[str(artist["id"])]
                            found += [(x, pid) for x in site_ids(artist.get("resources"), site)]
            mapped, _ = unique_pairs(found)
            store.add_external_ids(source, kind, mapped)
        # 새로 만든 가수에 AniList 인물 번호가 있으면 같이 붙여 둠 (나중에 AniList 인물로 이어지게)
        store.add_external_ids("anilist", "staff",
                               {al: artist_ids[aid] for aid, al in artist_anilist.items() if aid in artist_ids})

        # 2) AnimeThemes 작품 번호 → 우리 시즌
        at_pairs = []
        for _, a in records:
            for al in anilist_ids(a.get("resources")):
                if al in entry_ids:
                    at_pairs.append((str(a["id"]), entry_ids[al]))
        at_map, at_ambiguous = unique_pairs(at_pairs)
        store.add_external_ids(AT, "anime", at_map)

        # 3) 사실 · 별칭
        themes = 0
        for fetched_at, a in records:
            url = f"https://animethemes.moe/anime/{a['slug']}"
            entries = {entry_ids[al] for al in anilist_ids(a.get("resources")) if al in entry_ids}
            for t in a["animethemes"]:
                s = t.get("song")
                if not s:
                    continue
                sid = song_ids.get(str(s["id"]))
                versions = [{"version": e["version"], "episodes": e["episodes"]}
                            for e in sorted(t["animethemeentries"], key=lambda e: e["version"] or 0)]
                value = {"type": t["type"], "sequence": t["sequence"], "versions": versions}
                for eid in entries:
                    store.fact(eid, "theme_song", value, fetched_at, url, AT, value_entity_id=sid)
                    themes += 1
                for edge in (s.get("resources") or {}).get("edges") or []:
                    store.fact(sid, "song_link", {"site": edge["node"]["site"], "url": edge["node"]["link"]},
                               fetched_at, url, AT)
                store.alias(sid, s["title"].get("romaji"), "romaji", AT)
                store.alias(sid, s["title"].get("native"), "native", AT)
                for p in s.get("performances") or []:
                    artist = p.get("artist")
                    pid = artist_ids.get(str(artist["id"])) if artist else None
                    if pid:
                        store.fact(sid, "performer", {"as": p.get("as")}, fetched_at,
                                   f"https://animethemes.moe/artist/{artist['slug']}", AT, value_entity_id=pid)
                        store.alias(pid, artist["name"].get("main"), "romaji", AT)
                        store.alias(pid, artist["name"].get("native"), "native", AT)
        upserted, deleted = store.write_facts(AT, FIELDS)
        aliases = store.write_aliases()
        works_with = store.count("""select count(distinct e.parent_id) from dict.fact f
                                    join dict.entity e on e.entity_id = f.entity_id
                                    where f.source_id = 'animethemes' and f.field = 'theme_song'""")

    print(f"AnimeThemes 작품 {len(records):,} · 곡 {len(song_ids):,} (새로 {songs_made:,})")
    print(f"가수: 우리 인물에 연결 {len(linked_artists):,} · 새 인물 {len(new_artists):,} (새로 만듦 {artists_made:,})")
    print(f"시즌 → 주제가 {themes:,} · 주제가 있는 작품 {works_with:,} / 2,322 · 작품 번호 겹침(뺌) {at_ambiguous}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
