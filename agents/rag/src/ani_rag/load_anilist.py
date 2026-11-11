"""모델 DB(AniList 원본) → 정보사전 옮기기. 모델 DB 에 없는 시즌/극장판만 AniList 에 요청한다

  uv run python -m ani_rag.load_anilist        (agents/rag 폴더에서)

- 작품 목록(menu_works.csv · menu_work_entries.csv)에 있는 작품만 옮긴다
- 모델 DB 에 없는 번호(신작)는 AniList 에서 같은 칸을 받아 합친다 (10개씩 묶음, 4초 간격).
  따로 넣으면 다시 돌릴 때 FIELDS 를 맞추면서 서로 지우기 때문에 여기서 함께 넣는다.
  받은 원본은 .cache/raw/anilist_new/<날짜>/ 에 저장. 캐릭터 · 성우는 fetch_anilist_cast 가 받는다
- 여러 번 돌려도 같은 결과. 이 스크립트가 맡는 칸(FIELDS)의 AniList 사실은 이번 결과로 맞춘다
  (모델 DB 에서 없어진 줄은 지움). 별칭은 더하기만 한다
- 형식 · 방영 상태 · 날짜는 fetch_anilist_status 가 맡는다 (모델 DB 의 예전 수집분엔 그 칸이 없음)
- 캐릭터 · 성우 연결은 fetch_anilist_cast 가 맡는다 (더 깊은 목록 · 한국 성우)
- 받은 날짜는 모델 DB 에 넣은 날짜(media_raw.loaded_at)로 대신한다 (실제로 받은 날보다 조금 늦음)
- 작품 한국어 이름(entity.name_ko)은 작품 목록이 정한다: title_ko_check 가 완료인 것만 확정,
  확인 필요는 후보 별칭으로만 넣는다
- 영어 줄거리(synopsis_en_ref)는 참고용. 글에 문장을 옮기지 않는다
- 다른 출처와 비교하려고 고른 칸: main_studio (메인 스튜디오), director (제작진 역할 중 감독)
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import re
import sys
import time
from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from ani_rag.anilist_api import fetch_batches, read_batches
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, unique_pairs

ANILIST, CATALOG, MAL = "anilist", "menu_catalog", "mal"
ENTRY_URL = "https://anilist.co/anime/{}"
STUDIO_URL = "https://anilist.co/studio/{}"
DONE = ("", "완료")  # title_ko_check 가 이 값이면 사람이 확인을 끝낸 한국어 제목
DIRECTOR_ROLE = re.compile(r"^(Chief |Series )?Director( \(.*\))?$")  # 회차 감독 · 작화 감독 등은 뺌

# 이 스크립트가 맡는 칸 (출처 anilist). 다시 돌리면 이 칸은 이번 결과로 맞춘다
FIELDS = (
    "episodes", "duration_min", "season_year", "source_material", "country", "is_adult",
    "genre", "tag", "popularity", "favourites", "average_score", "synopsis_en_ref",
    "studio", "main_studio", "is_animation_studio", "staff", "director", "relation", "recommendation",
)
PARTS = ("base", "detail", "links")  # 모델 DB 원본(media_raw)이 작품마다 나눠 둔 조각
NEW_BATCH = 10  # 제작진까지 붙으면 요청 하나의 복잡도 한도 때문에 작게 (모델 DB 수집과 같은 값)
# 모델 DB 수집(model/scripts/fetch_anilist.py)의 세 조각과 같은 칸 · 같은 깊이
QUERY_NEW = """query ($ids: [Int]) { Page(page: 1, perPage: 10) { media(id_in: $ids, type: ANIME) {
  id isAdult genres title { romaji english native }
  tags { name rank category isAdult isGeneralSpoiler isMediaSpoiler }
  description(asHtml: false) source(version: 3) seasonYear episodes duration countryOfOrigin
  studios { edges { isMain node { id name isAnimationStudio } } }
  staff(sort: RELEVANCE, perPage: 25) { edges { role node { id name { full native } } } }
  popularity favourites averageScore synonyms
  relations { edges { relationType node { id type } } }
  recommendations(sort: RATING_DESC, perPage: 10) { nodes { rating mediaRecommendation { id } } } } } }"""


def read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fetch_model(conn: psycopg.Connection, ids: list[int]) -> dict[str, list[dict]]:
    """모델 DB 에서 작품 목록에 있는 AniList 작품 것만 읽는다"""
    queries = {
        "media": """select anilist_id, is_adult, description, source, season_year, episodes, duration,
                           country, popularity, favourites, average_score,
                           title_romaji, title_english, title_native
                    from anilist.media where anilist_id = any(%(ids)s)""",
        "loaded": "select anilist_id, part, loaded_at from anilist.media_raw where anilist_id = any(%(ids)s)",
        "synonyms": """select anilist_id, s.synonym from anilist.media_raw,
                         jsonb_array_elements_text(case when jsonb_typeof(payload->'synonyms') = 'array'
                                                        then payload->'synonyms' else '[]'::jsonb end) s(synonym)
                       where part = 'links' and anilist_id = any(%(ids)s)""",
        "genre": "select anilist_id, genre from anilist.media_genre where anilist_id = any(%(ids)s)",
        "tag": """select anilist_id, tag, rank, category, is_spoiler, is_adult
                  from anilist.media_tag where anilist_id = any(%(ids)s)""",
        "studio": """select ms.anilist_id, ms.studio_id, ms.is_main, s.name, s.is_animation_studio
                     from anilist.media_studio ms join anilist.studio s using (studio_id)
                     where ms.anilist_id = any(%(ids)s)""",
        "staff": """select ms.anilist_id, ms.staff_id, ms.role, s.name_full, s.name_native
                    from anilist.media_staff ms join anilist.staff s using (staff_id)
                    where ms.anilist_id = any(%(ids)s)""",
        "character": """select mc.anilist_id, mc.character_id, mc.role, c.name_full, c.name_native
                        from anilist.media_character mc join anilist.character c using (character_id)
                        where mc.anilist_id = any(%(ids)s)""",
        "voice": """select cv.anilist_id, cv.character_id, cv.staff_id, s.name_full, s.name_native
                    from anilist.character_voice cv join anilist.staff s using (staff_id)
                    where cv.anilist_id = any(%(ids)s)""",
        "relation": """select anilist_id, related_id, relation_type, related_type
                       from anilist.media_relation where anilist_id = any(%(ids)s)""",
        "recommendation": """select anilist_id, rec_id, rating
                             from anilist.media_recommendation where anilist_id = any(%(ids)s)""",
    }
    out = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for name, sql in queries.items():
            cur.execute(sql, {"ids": ids})
            out[name] = cur.fetchall()
    return out


def fetch_new(ids: list[int], raw_dir: pathlib.Path) -> dict[str, list[dict]]:
    """모델 DB 에 없는 작품을 AniList 에서 받아 fetch_model 과 같은 모양으로 돌려준다 (캐릭터 · 성우는 비움)"""
    fetch_batches(QUERY_NEW, ids, raw_dir, batch_size=NEW_BATCH)
    out: dict[str, list[dict]] = defaultdict(list)
    for fetched_at, data in read_batches(raw_dir):
        for m in data["Page"]["media"]:
            aid, title = m["id"], m["title"]
            out["media"].append({
                "anilist_id": aid, "is_adult": m["isAdult"], "description": m["description"], "source": m["source"],
                "season_year": m["seasonYear"], "episodes": m["episodes"], "duration": m["duration"],
                "country": m["countryOfOrigin"], "popularity": m["popularity"], "favourites": m["favourites"],
                "average_score": m["averageScore"], "title_romaji": title["romaji"],
                "title_english": title["english"], "title_native": title["native"]})
            out["loaded"] += [{"anilist_id": aid, "part": part, "loaded_at": fetched_at} for part in PARTS]
            out["synonyms"] += [{"anilist_id": aid, "synonym": s} for s in m["synonyms"] or []]
            out["genre"] += [{"anilist_id": aid, "genre": g} for g in m["genres"] or []]
            out["tag"] += [{"anilist_id": aid, "tag": t["name"], "rank": t["rank"], "category": t["category"],
                            "is_spoiler": bool(t["isGeneralSpoiler"] or t["isMediaSpoiler"]),
                            "is_adult": bool(t["isAdult"])} for t in m["tags"] or []]
            out["studio"] += [{"anilist_id": aid, "studio_id": e["node"]["id"], "is_main": e["isMain"],
                               "name": e["node"]["name"], "is_animation_studio": e["node"]["isAnimationStudio"]}
                              for e in m["studios"]["edges"]]
            out["staff"] += [{"anilist_id": aid, "staff_id": e["node"]["id"], "role": e["role"],
                              "name_full": e["node"]["name"]["full"], "name_native": e["node"]["name"]["native"]}
                             for e in m["staff"]["edges"]]
            out["relation"] += [{"anilist_id": aid, "related_id": e["node"]["id"], "related_type": e["node"]["type"],
                                 "relation_type": e["relationType"]} for e in m["relations"]["edges"]]
            out["recommendation"] += [{"anilist_id": aid, "rec_id": n["mediaRecommendation"]["id"],
                                       "rating": n["rating"]}
                                      for n in m["recommendations"]["nodes"] if n["mediaRecommendation"]]
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # 윈도우에서 출력을 다른 곳으로 넘길 때 한글이 깨지지 않게
    started = time.time()
    settings = load_settings(need_model_db=True)
    works = read_csv(settings.catalog_dir / "menu_works.csv")
    entries = read_csv(settings.catalog_dir / "menu_work_entries.csv")
    print(f"작품 목록: {settings.catalog_dir} (작품 {len(works):,} · 시즌/극장판 {len(entries):,})")

    ids = [int(e["anilist_id"]) for e in entries]
    with psycopg.connect(settings.model_conninfo) as mconn:
        m = fetch_model(mconn, ids)
    print("모델 DB 읽기: " + " · ".join(f"{k} {len(v):,}" for k, v in m.items()))

    # 세 조각이 다 없는 번호는 모델 DB 줄을 버리고 AniList 에서 통째로 받는다 (칸이 섞이지 않게)
    parts = {(r["anilist_id"], r["part"]) for r in m["loaded"]}
    missing = {i for i in ids if any((i, part) not in parts for part in PARTS)}
    if missing:
        print(f"모델 DB 에 없는 시즌/극장판 {len(missing):,}개 → AniList 에서 받기", flush=True)
        m = {k: [r for r in rows if r["anilist_id"] not in missing] for k, rows in m.items()}
        raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "anilist_new" / dt.date.today().strftime("%Y%m%d")
        for name, rows in fetch_new(sorted(missing), raw_dir).items():
            m[name] += rows
        print("합친 뒤: " + " · ".join(f"{k} {len(v):,}" for k, v in m.items()))

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from dict.source where source_id = any(%s)", ([ANILIST, CATALOG, MAL],))
        if cur.fetchone()[0] < 3:
            raise SystemExit("출처가 없음 → db/sources.sql 을 먼저 넣기")
        store = Store(cur)

        # 1) 작품 (한국어 이름은 확인 끝난 것만 확정)
        work_rows = []
        for w in works:
            confirmed = w["title_ko"] if w["title_ko"] and w["title_ko_check"] in DONE else None
            work_rows.append((int(w["menu_work_id"]), confirmed or None,
                              w["title_native"] or None, w["title_romaji"] or None))
        work_ids, works_changed = store.upsert_works(work_rows)
        for w in works:
            eid = work_ids[int(w["menu_work_id"])]
            if w["title_ko"]:
                store.alias(eid, w["title_ko"], "ko", CATALOG, candidate=w["title_ko_check"] not in DONE)
            store.alias(eid, w["title_romaji"], "romaji", CATALOG)
            store.alias(eid, w["title_english"], "en", CATALOG)
            store.alias(eid, w["title_native"], "native", CATALOG)

        # 2) 시즌 · 극장판 · 스튜디오 · 인물 · 캐릭터 (AniList 번호로 찾거나 만듦)
        media = {r["anilist_id"]: r for r in m["media"]}
        entry_rows = {}
        for e in entries:
            row = media.get(int(e["anilist_id"])) or {}
            entry_rows[e["anilist_id"]] = (work_ids[int(e["menu_work_id"])],
                                           row.get("title_native") or e["title_native"] or None,
                                           row.get("title_romaji") or e["title_romaji"] or None)
        entry_ids, entries_made, entries_changed = store.upsert_external(ANILIST, "anime", "entry", entry_rows)
        studio_ids, studios_made, _ = store.upsert_external(
            ANILIST, "studio", "studio", {str(r["studio_id"]): (None, None, r["name"]) for r in m["studio"]})
        person_rows = {str(r["staff_id"]): (None, r["name_native"], r["name_full"])
                       for r in m["staff"] + m["voice"]}
        person_ids, persons_made, _ = store.upsert_external(ANILIST, "staff", "person", person_rows)
        char_rows = {str(r["character_id"]): (None, r["name_native"], r["name_full"]) for r in m["character"]}
        char_ids, chars_made, _ = store.upsert_external(ANILIST, "character", "character", char_rows)

        # MAL 번호 (작품 목록에서 둘 이상에 붙은 번호는 어느 쪽인지 몰라서 뺌)
        mal_pairs, mal_ambiguous = unique_pairs([(e["mal_id"], entry_ids[e["anilist_id"]])
                                                 for e in entries if e["mal_id"]])
        mal_added, mal_clash = store.add_external_ids(MAL, "anime", mal_pairs)

        # 3) 사실 · 별칭
        loaded = {(r["anilist_id"], r["part"]): r["loaded_at"] for r in m["loaded"]}

        def at(aid: int, part: str):
            return loaded.get((aid, part)) or loaded.get((aid, "base"))

        def entry_of(aid: int):
            return entry_ids.get(str(aid))

        def fact(eid, field, value, aid, part, url=None, value_entity_id=None):
            store.fact(eid, field, value, at(aid, part), url or ENTRY_URL.format(aid), ANILIST, value_entity_id)

        for aid, r in media.items():
            eid = entry_of(aid)
            fact(eid, "is_adult", r["is_adult"], aid, "base")
            fact(eid, "episodes", r["episodes"], aid, "detail")
            fact(eid, "duration_min", r["duration"], aid, "detail")
            fact(eid, "season_year", r["season_year"], aid, "detail")
            fact(eid, "source_material", r["source"], aid, "detail")
            fact(eid, "country", r["country"], aid, "detail")
            if (r["description"] or "").strip():
                fact(eid, "synopsis_en_ref", r["description"].strip(), aid, "detail")
            fact(eid, "popularity", r["popularity"], aid, "links")
            fact(eid, "favourites", r["favourites"], aid, "links")
            fact(eid, "average_score", r["average_score"], aid, "links")
            store.alias(eid, r["title_romaji"], "romaji", ANILIST)
            store.alias(eid, r["title_english"], "en", ANILIST)
            store.alias(eid, r["title_native"], "native", ANILIST)
        for r in m["synonyms"]:
            store.alias(entry_of(r["anilist_id"]), r["synonym"], None, ANILIST)
        for r in m["genre"]:
            fact(entry_of(r["anilist_id"]), "genre", r["genre"], r["anilist_id"], "base")
        for r in m["tag"]:
            value = {"name": r["tag"], "rank": r["rank"], "category": r["category"],
                     "spoiler": r["is_spoiler"], "adult": r["is_adult"]}
            fact(entry_of(r["anilist_id"]), "tag", value, r["anilist_id"], "base")
        for r in m["studio"]:
            aid, sid = r["anilist_id"], studio_ids[str(r["studio_id"])]
            fact(entry_of(aid), "studio", {}, aid, "detail", value_entity_id=sid)
            if r["is_main"]:
                fact(entry_of(aid), "main_studio", {}, aid, "detail", value_entity_id=sid)
            # 애니 제작 스튜디오인지 (아니면 투자 · 제작위원회 회사)
            fact(sid, "is_animation_studio", r["is_animation_studio"], aid, "detail",
                 url=STUDIO_URL.format(r["studio_id"]))
            store.alias(sid, r["name"], None, ANILIST)
        for r in m["staff"]:
            aid, pid = r["anilist_id"], person_ids[str(r["staff_id"])]
            fact(entry_of(aid), "staff", {"role": r["role"]}, aid, "detail", value_entity_id=pid)
            if DIRECTOR_ROLE.match(r["role"] or ""):
                fact(entry_of(aid), "director", {}, aid, "detail", value_entity_id=pid)
            store.alias(pid, r["name_full"], "romaji", ANILIST)
            store.alias(pid, r["name_native"], "native", ANILIST)
        for r in m["character"]:
            cid = char_ids[str(r["character_id"])]
            store.alias(cid, r["name_full"], "romaji", ANILIST)
            store.alias(cid, r["name_native"], "native", ANILIST)
        for r in m["voice"]:
            pid = person_ids[str(r["staff_id"])]
            store.alias(pid, r["name_full"], "romaji", ANILIST)
            store.alias(pid, r["name_native"], "native", ANILIST)
        for r in m["relation"]:
            aid, target = r["anilist_id"], entry_of(r["related_id"])
            value = ({"type": r["relation_type"]} if target else
                     {"type": r["relation_type"], "anilist_id": r["related_id"], "media_type": r["related_type"]})
            fact(entry_of(aid), "relation", value, aid, "links", value_entity_id=target)
        for r in m["recommendation"]:
            aid, target = r["anilist_id"], entry_of(r["rec_id"])
            value = {"rating": r["rating"]} if target else {"rating": r["rating"], "anilist_id": r["rec_id"]}
            fact(entry_of(aid), "recommendation", value, aid, "links", value_entity_id=target)

        facts_upserted, facts_deleted = store.write_facts(ANILIST, FIELDS)
        aliases_upserted = store.write_aliases()
        facts_now = store.count("select count(*) from dict.fact where source_id = %s", (ANILIST,))
        aliases_now = store.count("select count(*) from dict.alias")

    print(f"작품: {len(work_ids):,} (이름 바뀜 {works_changed:,})")
    print(f"시즌/극장판: {len(entry_ids):,} (새로 {entries_made:,} · 바뀜 {entries_changed:,})")
    print(f"스튜디오 {len(studio_ids):,} (새로 {studios_made:,}) · 인물 {len(person_ids):,} (새로 {persons_made:,})"
          f" · 캐릭터 {len(char_ids):,} (새로 {chars_made:,})")
    print(f"MAL 번호: 새로 붙임 {mal_added:,} · 작품 목록에서 겹쳐서 뺌 {mal_ambiguous}"
          f" · 다른 대상에 이미 붙음 {mal_clash}")
    print(f"사실: 넣거나 고침 {facts_upserted:,} · 지움 {facts_deleted:,} · AniList 사실 지금 {facts_now:,}")
    print(f"별칭: 넣거나 고침 {aliases_upserted:,} · 지금 {aliases_now:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
