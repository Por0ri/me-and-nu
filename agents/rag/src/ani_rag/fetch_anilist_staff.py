"""AniList 에서 인물 정보 받기 — 직업 · 생년월일 · 출신지 · 활동 시기 · 소개 글(참고용) (사전의 인물 전부, 50명씩)

  uv run python -m ani_rag.fetch_anilist_staff        (agents/rag 폴더에서)

- 요청 수: 인물 수 ÷ 50, 4초 간격 (anilist_api.py). 받은 원본은 .cache/raw/anilist_staff/<날짜>/ 에 저장
- 이 스크립트가 맡는 칸 (출처 anilist): occupation · language · person_gender · birth_date · death_date ·
  years_active · home_town · person_blood_type · person_favourites · person_description_ref
- 소개 글(person_description_ref)은 참고용. 글에 문장을 옮기지 않는다
- 생년월일은 위키데이터와 같은 모양이라 두 출처 일치를 셀 수 있다
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
STAFF_URL = "https://anilist.co/staff/{}"
FIELDS = ("occupation", "language", "person_gender", "birth_date", "death_date", "years_active", "home_town",
          "person_blood_type", "person_favourites", "person_description_ref")
QUERY = """query ($ids: [Int]) { Page(page: 1, perPage: 50) { staff(id_in: $ids) {
  id name { alternative } languageV2 primaryOccupations gender homeTown bloodType favourites yearsActive
  dateOfBirth { year month day } dateOfDeath { year month day } description(asHtml: false) } } }"""


def fuzzy(d: dict | None) -> dict | None:
    parts = {k: d[k] for k in ("year", "month", "day") if d and d.get(k)}
    return parts or None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_staff" / dt.date.today().strftime("%Y%m%d")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'staff'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
    fetch_batches(QUERY, ids, raw_dir)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        person_ids = store.external_map(ANILIST, "staff")
        got = 0
        for fetched_at, data in read_batches(raw_dir):
            for s in data["Page"]["staff"]:
                eid, url = person_ids.get(str(s["id"])), STAFF_URL.format(s["id"])
                if eid is None:
                    continue
                got += 1
                for job in s["primaryOccupations"] or []:
                    store.fact(eid, "occupation", job, fetched_at, url, ANILIST)
                store.fact(eid, "language", s["languageV2"], fetched_at, url, ANILIST)
                store.fact(eid, "person_gender", s["gender"], fetched_at, url, ANILIST)
                store.fact(eid, "birth_date", fuzzy(s["dateOfBirth"]), fetched_at, url, ANILIST)
                store.fact(eid, "death_date", fuzzy(s["dateOfDeath"]), fetched_at, url, ANILIST)
                store.fact(eid, "home_town", s["homeTown"], fetched_at, url, ANILIST)
                store.fact(eid, "person_blood_type", s["bloodType"], fetched_at, url, ANILIST)
                store.fact(eid, "person_favourites", s["favourites"], fetched_at, url, ANILIST)
                years = [y for y in (s["yearsActive"] or []) if y]
                if years:
                    store.fact(eid, "years_active", years, fetched_at, url, ANILIST)
                if (s["description"] or "").strip():
                    store.fact(eid, "person_description_ref", s["description"].strip(), fetched_at, url, ANILIST)
                for alt in (s["name"] or {}).get("alternative") or []:
                    store.alias(eid, alt, None, ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        aliases = store.write_aliases()
        cur.execute("""select field, count(distinct entity_id) from dict.fact
                       where source_id = 'anilist' and field = any(%s) group by 1 order by 2 desc""", (list(FIELDS),))
        by_field = cur.fetchall()

    print(f"받은 인물 {got:,} / {len(ids):,}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print("칸별 인물 수: " + " · ".join(f"{f} {n:,}" for f, n in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
