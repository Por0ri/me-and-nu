"""MusicBrainz → 정보사전: 주제가 가수의 정보(유형 · 활동 지역 · 시작일 · 한국어 이름 · 공식 사이트)와 곡 발매일

  uv run python -m ani_rag.load_musicbrainz        (agents/rag 폴더에서)

- 대상: 주제가 가수 중 MusicBrainz 번호(위키데이터 P434)가 있는 사람 · 그룹 (이름으로 찾지 않음)
- 요청: 가수마다 정보 1번 + 음반 목록 100개씩. 1.2초 간격 (MusicBrainz 제한 1초에 1번)
- 받은 원본은 .cache/raw/musicbrainz/<날짜>/ 에 저장. 끊겨도 다시 돌리면 이어서 받음
- 곡 발매일: 그 가수의 음반 제목이 우리 곡의 일본어 원제 또는 로마자 제목과 똑같을 때만 붙인다
- 핵심 데이터만 쓴다 (CC0). 태그 · 평점은 쓰지 않는다
- 이 스크립트가 맡는 칸 (출처 musicbrainz): artist_type · artist_area · artist_begin · name_ko · official_site ·
  release_date · release_type
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, norm

MB = "musicbrainz"
API = "https://musicbrainz.org/ws/2"
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.2
FIELDS = ("artist_type", "artist_area", "artist_begin", "name_ko", "official_site", "release_date", "release_type")
HANGUL = re.compile(r"[가-힣]")
MAX_RELEASE_PAGES = 5
last = 0.0


def get(path: str) -> dict:
    global last
    time.sleep(max(0.0, last + PAUSE - time.time()))
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(5):
        last = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            if e.code in (429, 503):
                time.sleep(int(e.headers.get("Retry-After") or 30) + 1)
            elif e.code >= 500:
                time.sleep(20 * (attempt + 1))
            else:
                raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(20 * (attempt + 1))
    raise SystemExit("MusicBrainz 요청이 5번 연속 실패함")


def fetch(mbid: str, raw_dir: pathlib.Path) -> None:
    path = raw_dir / f"{mbid}.json"
    if path.exists():
        return
    artist = get(f"artist/{mbid}?inc=aliases+url-rels&fmt=json")
    groups, offset = [], 0
    for _ in range(MAX_RELEASE_PAGES):
        data = get(f"release-group?artist={mbid}&limit=100&offset={offset}&fmt=json")
        batch = data.get("release-groups") or []
        groups += [{"id": g["id"], "title": g["title"], "date": g.get("first-release-date"),
                    "type": g.get("primary-type")} for g in batch]
        offset += len(batch)
        if not batch or offset >= (data.get("release-group-count") or 0):
            break
    record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "artist": artist, "groups": groups}
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def date_of(text: str | None) -> dict | None:
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text or "")
    if not m:
        return None
    return {k: int(v) for k, v in zip(("year", "month", "day"), m.groups(), strict=True) if v}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "musicbrainz" / dt.date.today().strftime("%Y%m%d")
    raw_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select distinct x.external_id, x.entity_id from dict.external_id x
                       join dict.fact f on f.value_entity_id = x.entity_id and f.field = 'performer'
                       where x.source_id = 'musicbrainz' and x.id_kind = 'artist'""")
        artists = cur.fetchall()
        cur.execute("""select p.value_entity_id, s.entity_id, s.name_romaji, t.value #>> '{}'
                       from dict.fact p join dict.entity s on s.entity_id = p.entity_id
                       left join dict.fact t on t.entity_id = s.entity_id and t.field = 'song_title_ja'
                       where p.field = 'performer'""")
        songs_of: dict[int, list[tuple[int, str | None, str | None]]] = defaultdict(list)
        for pid, sid, romaji, ja in cur.fetchall():
            songs_of[pid].append((sid, romaji, ja))
    print(f"MusicBrainz 번호가 있는 주제가 가수 {len(artists):,}명", flush=True)
    for n, (mbid, _) in enumerate(sorted(artists), 1):
        fetch(mbid, raw_dir)
        if n % 100 == 0:
            print(f"  {n}/{len(artists)}", flush=True)

    stats: Counter = Counter()
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        for mbid, pid in artists:
            path = raw_dir / f"{mbid}.json"
            if not path.exists():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            at = dt.datetime.fromisoformat(record["fetched_at"])
            artist, url = record["artist"] or {}, f"https://musicbrainz.org/artist/{mbid}"
            store.fact(pid, "artist_type", artist.get("type"), at, url, MB)
            store.fact(pid, "artist_area", (artist.get("area") or {}).get("name"), at, url, MB)
            store.fact(pid, "artist_begin", date_of((artist.get("life-span") or {}).get("begin")), at, url, MB)
            for alias in artist.get("aliases") or []:
                if alias.get("locale") == "ko" or HANGUL.search(alias.get("name") or ""):
                    store.fact(pid, "name_ko", alias["name"], at, url, MB)
                    store.alias(pid, alias["name"], "ko", MB)
                    stats["한국어 이름"] += 1
            for rel in artist.get("relations") or []:
                if rel.get("type") == "official homepage":
                    store.fact(pid, "official_site", {"url": rel["url"]["resource"]}, at, url, MB)
            by_title: dict[str, list[dict]] = defaultdict(list)
            for g in record["groups"]:
                by_title[norm(g["title"])].append(g)
            for sid, romaji, ja in songs_of.get(pid, []):
                found = by_title.get(norm(ja), []) if ja else []
                found = found or by_title.get(norm(romaji), [])
                dated = sorted((g for g in found if g.get("date")), key=lambda g: g["date"])
                if not dated:
                    stats["음반 못 찾음"] += 1
                    continue
                first = dated[0]
                stats["발매일 붙임"] += 1
                gurl = f"https://musicbrainz.org/release-group/{first['id']}"
                store.fact(sid, "release_date", date_of(first["date"]), at, gurl, MB)
                store.fact(sid, "release_type", first.get("type"), at, gurl, MB)
        upserted, deleted = store.write_facts(MB, FIELDS)
        aliases = store.write_aliases(sync_source=MB)
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'musicbrainz' group by 1 order by 2 desc""")
        by_field = cur.fetchall()

    print("결과: " + " · ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
