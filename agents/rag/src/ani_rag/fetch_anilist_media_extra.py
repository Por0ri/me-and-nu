"""AniList 에서 공식 사이트 · 해시태그 · 외부 링크 · 순위 받기 (사전에 있는 시즌/극장판 전부, 50개씩)

  uv run python -m ani_rag.fetch_anilist_media_extra        (agents/rag 폴더에서)

- 요청 수: 시즌/극장판 수 ÷ 50 (3,887개면 78번), 4초 간격 (anilist_api.py)
- 받은 원본은 .cache/raw/anilist_media_extra/<날짜>/ 에 저장
- 이 스크립트가 맡는 칸 (출처 anilist): external_link · official_site · kr_streaming · hashtag · ranking
- kr_streaming: 외부 링크 중 한국어 스트리밍 (라프텔 · 넷플릭스 한국 등)
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
FIELDS = ("external_link", "official_site", "kr_streaming", "hashtag", "ranking")
QUERY = """query ($ids: [Int]) { Page(page: 1, perPage: 50) { media(id_in: $ids, type: ANIME) {
  id hashtag
  externalLinks { site url type language isDisabled }
  rankings { rank type context year season allTime } } } }"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_media_extra" / dt.date.today().strftime("%Y%m%d")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'anime'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
    fetch_batches(QUERY, ids, raw_dir)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        entry_ids = store.external_map(ANILIST, "anime")
        for fetched_at, data in read_batches(raw_dir):
            for m in data["Page"]["media"]:
                eid, url = entry_ids.get(str(m["id"])), ENTRY_URL.format(m["id"])
                store.fact(eid, "hashtag", m["hashtag"], fetched_at, url, ANILIST)
                for link in m["externalLinks"] or []:
                    if link.get("isDisabled"):
                        continue
                    value = {"site": link["site"], "url": link["url"], "type": link["type"],
                             "language": link["language"]}
                    store.fact(eid, "external_link", value, fetched_at, url, ANILIST)
                    if link["site"] == "Official Site":
                        store.fact(eid, "official_site", {"url": link["url"], "language": link["language"]},
                                   fetched_at, url, ANILIST)
                    if link["language"] == "Korean" and link["type"] == "STREAMING":
                        store.fact(eid, "kr_streaming", {"service": link["site"], "url": link["url"]},
                                   fetched_at, url, ANILIST)
                for r in m["rankings"] or []:
                    value = {"rank": r["rank"], "type": r["type"], "context": r["context"], "year": r["year"],
                             "season": r["season"], "all_time": r["allTime"]}
                    store.fact(eid, "ranking", value, fetched_at, url, ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'anilist' and field = any(%s) group by 1 order by 2 desc""", (list(FIELDS),))
        by_field = cur.fetchall()

    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
