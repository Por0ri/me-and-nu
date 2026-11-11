"""AniList 에서 캐릭터 생일 · 나이 · 혈액형 · 성별 · 다른 이름 받기 (사전에 있는 캐릭터 전부, 50명씩)

  uv run python -m ani_rag.fetch_anilist_characters        (agents/rag 폴더에서)

- 요청 수: 캐릭터 수 ÷ 50 (23,581명이면 472번), 4초 간격 → 약 32분 (anilist_api.py)
- 받은 원본은 .cache/raw/anilist_characters/<날짜>/ 에 저장. 끊겨도 다시 돌리면 이어서 받음
- 이 스크립트가 맡는 칸 (출처 anilist): birthday · birth_year · gender · age · blood_type ·
  character_favourites · character_description_ref (프로필 글, 참고용. 글에 문장을 옮기지 않음)
- 다른 이름(alternative)은 별칭으로 넣는다. 스포일러 이름(alternativeSpoiler)은 받지 않는다
- 생일은 위키데이터 · 온나다와 같은 모양({month, day})이라 두 출처 일치를 셀 수 있다
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
CHARACTER_URL = "https://anilist.co/character/{}"
FIELDS = ("birthday", "birth_year", "gender", "age", "blood_type", "character_favourites",
          "character_description_ref")
QUERY = """query ($ids: [Int]) { Page(page: 1, perPage: 50) { characters(id_in: $ids) {
  id name { alternative } gender age bloodType favourites description(asHtml: false)
  dateOfBirth { year month day } } } }"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_characters" / dt.date.today().strftime("%Y%m%d")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'character'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
    fetch_batches(QUERY, ids, raw_dir)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        char_ids = store.external_map(ANILIST, "character")
        got = 0
        for fetched_at, data in read_batches(raw_dir):
            for c in data["Page"]["characters"]:
                eid, url = char_ids.get(str(c["id"])), CHARACTER_URL.format(c["id"])
                if eid is None:
                    continue
                got += 1
                born = c["dateOfBirth"] or {}
                if born.get("month") and born.get("day"):
                    store.fact(eid, "birthday", {"month": born["month"], "day": born["day"]}, fetched_at, url, ANILIST)
                store.fact(eid, "birth_year", born.get("year"), fetched_at, url, ANILIST)
                store.fact(eid, "gender", c["gender"], fetched_at, url, ANILIST)
                store.fact(eid, "age", c["age"], fetched_at, url, ANILIST)
                store.fact(eid, "blood_type", c["bloodType"], fetched_at, url, ANILIST)
                store.fact(eid, "character_favourites", c["favourites"], fetched_at, url, ANILIST)
                if (c["description"] or "").strip():
                    store.fact(eid, "character_description_ref", c["description"].strip(), fetched_at, url, ANILIST)
                for alt in (c["name"] or {}).get("alternative") or []:
                    store.alias(eid, alt, None, ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        aliases = store.write_aliases()
        cur.execute("""select field, count(*) from dict.fact where source_id = 'anilist' and field = any(%s)
                       group by 1 order by 2 desc""", (list(FIELDS),))
        by_field = cur.fetchall()

    print(f"받은 캐릭터 {got:,} / {len(ids):,}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print("칸별: " + " · ".join(f"{f} {n:,}" for f, n in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
