"""AniList 에서 형식 · 방영 상태 · 날짜를 새로 받기 (사전에 있는 시즌/극장판 전부, 50개씩 묶어서)

  uv run python -m ani_rag.fetch_anilist_status        (agents/rag 폴더에서)

- 요청 수: 시즌/극장판 수 ÷ 50 (3,887개면 78번), 4초 간격 → 약 5분 (anilist_api.py)
- 받은 원본은 .cache/raw/anilist_status/<날짜>/ 에 저장. 같은 날 다시 돌리면 받은 쪽은 다시 요청하지 않음
- 이 스크립트가 맡는 칸 (출처 anilist): format · status · season · start_date · end_date · start_year · next_airing
"""

from __future__ import annotations

import datetime as dt
import sys
import time

import psycopg

from ani_rag.anilist_api import fetch_batches, read_batches
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

ANILIST = "anilist"
ENTRY_URL = "https://anilist.co/anime/{}"
FIELDS = ("format", "status", "season", "start_date", "end_date", "start_year", "next_airing")
QUERY = """query ($ids: [Int]) { Page(page: 1, perPage: 50) { media(id_in: $ids, type: ANIME) {
  id format status season
  startDate { year month day } endDate { year month day } nextAiringEpisode { airingAt episode } } } }"""


def fuzzy(d: dict | None) -> dict | None:
    """AniList 날짜(연 · 월 · 일 중 빈칸 가능) → 있는 것만"""
    parts = {k: d[k] for k in ("year", "month", "day") if d and d.get(k)}
    return parts or None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_status" / dt.date.today().strftime("%Y%m%d")

    # 1) 번호 읽기 → 2) 받기 (DB 를 열어 둔 채로 기다리지 않게 나눔) → 3) 넣기
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'anime'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
    fetch_batches(QUERY, ids, raw_dir)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        entry_ids = store.external_map(ANILIST, "anime")
        got = set()
        for fetched_at, data in read_batches(raw_dir):
            for r in data["Page"]["media"]:
                eid, url = entry_ids.get(str(r["id"])), ENTRY_URL.format(r["id"])
                if eid is None:
                    continue
                got.add(r["id"])
                start = fuzzy(r["startDate"])
                store.fact(eid, "format", r["format"], fetched_at, url, ANILIST)
                store.fact(eid, "status", r["status"], fetched_at, url, ANILIST)
                store.fact(eid, "season", r["season"], fetched_at, url, ANILIST)
                store.fact(eid, "start_date", start, fetched_at, url, ANILIST)
                store.fact(eid, "start_year", (start or {}).get("year"), fetched_at, url, ANILIST)
                store.fact(eid, "end_date", fuzzy(r["endDate"]), fetched_at, url, ANILIST)
                nxt = r["nextAiringEpisode"]
                if nxt:
                    at = dt.datetime.fromtimestamp(nxt["airingAt"], dt.UTC).isoformat()
                    store.fact(eid, "next_airing", {"airing_at": at, "episode": nxt["episode"]},
                               fetched_at, url, ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        cur.execute("""select value #>> '{}' as status, count(*) from dict.fact
                       where source_id = 'anilist' and field = 'status' group by 1 order by 2 desc""")
        statuses = cur.fetchall()

    print(f"받은 시즌/극장판 {len(got):,} / {len(ids):,} (AniList 에서 못 찾음 {len(ids) - len(got)})")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("방영 상태: " + " · ".join(f"{s} {n:,}" for s, n in statuses))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
