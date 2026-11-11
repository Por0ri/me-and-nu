"""AniList 에서 원작(만화 · 소설) 정보 받기 — 형식 · 연재 상태 · 권수 · 화수 · 기간 · 작가

  uv run python -m ani_rag.fetch_anilist_sources        (agents/rag 폴더에서)

- 대상: 사전의 시즌/극장판에 관계(relation)로 연결된 원작 번호 전부 (우리 작품 목록엔 없는 만화 · 소설)
- 요청 수: 원작 수 ÷ 25, 4초 간격 (제작진이 붙어서 묶음을 작게)
- 받은 원본은 .cache/raw/anilist_sources/<날짜>/ 에 저장
- 이 스크립트가 맡는 칸 (출처 anilist): source_work (시즌마다, 값 안에 원작 정보)
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from collections import defaultdict

import psycopg

from ani_rag.anilist_api import fetch_batches, read_batches
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

ANILIST = "anilist"
MANGA_URL = "https://anilist.co/manga/{}"
FIELDS = ("source_work",)
BATCH = 25
QUERY = """query ($ids: [Int]) { Page(page: 1, perPage: 25) { media(id_in: $ids, type: MANGA) {
  id format status chapters volumes countryOfOrigin
  title { romaji english native } startDate { year month day } endDate { year month day }
  staff(sort: RELEVANCE, perPage: 6) { edges { role node { id name { full native } } } } } } }"""


def fuzzy(d: dict | None) -> dict | None:
    parts = {k: d[k] for k in ("year", "month", "day") if d and d.get(k)}
    return parts or None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_sources" / dt.date.today().strftime("%Y%m%d")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select entity_id, (value->>'anilist_id')::int from dict.fact
                       where source_id = 'anilist' and field = 'relation' and value ? 'anilist_id'
                         and value->>'media_type' = 'MANGA' and value->>'type' in ('SOURCE', 'ADAPTATION')""")
        entries_of: dict[int, set[int]] = defaultdict(set)
        for eid, aid in cur.fetchall():
            entries_of[aid].add(eid)
    ids = sorted(entries_of)
    print(f"원작 {len(ids):,}개", flush=True)
    fetch_batches(QUERY, ids, raw_dir, batch_size=BATCH)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        got = 0
        for fetched_at, data in read_batches(raw_dir):
            for m in data["Page"]["media"]:
                got += 1
                authors = [{"role": e["role"], "name": e["node"]["name"]["full"], "native": e["node"]["name"]["native"],
                            "anilist_staff": e["node"]["id"]} for e in (m["staff"] or {}).get("edges") or []]
                value = {"anilist_id": m["id"], "title_romaji": m["title"]["romaji"],
                         "title_english": m["title"]["english"], "title_native": m["title"]["native"],
                         "format": m["format"], "status": m["status"], "volumes": m["volumes"],
                         "chapters": m["chapters"], "country": m["countryOfOrigin"],
                         "start": fuzzy(m["startDate"]), "end": fuzzy(m["endDate"]), "authors": authors}
                for eid in entries_of[m["id"]]:
                    store.fact(eid, "source_work", value, fetched_at, MANGA_URL.format(m["id"]), ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        entries = store.count("select count(distinct entity_id) from dict.fact where field = 'source_work'")

    print(f"받은 원작 {got:,} / {len(ids):,} · 원작 정보가 붙은 시즌 {entries:,}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
