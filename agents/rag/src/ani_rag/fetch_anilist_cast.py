"""AniList 에서 캐릭터 목록 · 일본 성우 · 한국 성우 받기 (모델 DB 의 시즌당 10명 제한을 넘어 더 깊게)

  uv run python -m ani_rag.fetch_anilist_cast        (agents/rag 폴더에서)

- 1차: 시즌/극장판 전부의 첫 25명 (10개씩 묶음, 3,887개면 389번)
- 2차: 인기 상위 작품의 TV · ONA 시즌만 다음 쪽까지 (상위 300편, 시즌당 최대 6쪽 = 150명, 쪽마다 요청 1번)
- 4초 간격 (anilist_api.py). 받은 원본은 .cache/raw/anilist_cast/<날짜>/ 에 저장, 끊겨도 이어서 받음
- 성우는 언어를 나누지 않고 한 번에 받아 응답의 languageV2 로 나눈다
  (일본어 · 한국어를 별명으로 같이 물으면 AniList 가 둘 다 마지막 언어로 답한다)
- 이 스크립트가 맡는 칸 (출처 anilist): character (시즌 → 캐릭터), voice_actor (캐릭터 → 일본 성우),
  voice_actor_ko (캐릭터 → 한국 성우), name_ko (한글 이름이 있는 인물)
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time

import psycopg

from ani_rag.anilist_api import PAUSE, fetch_batches, post, read_batches
from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

ANILIST = "anilist"
ENTRY_URL = "https://anilist.co/anime/{}"
CHARACTER_URL = "https://anilist.co/character/{}"
HANGUL = re.compile(r"[가-힣]")
FIELDS = ("character", "voice_actor", "voice_actor_ko", "name_ko")
VOICE_LANGS = {"Japanese": ("voice_actor", "ja"), "Korean": ("voice_actor_ko", "ko")}
FIRST_BATCH = 10
TOP_WORKS = 300
MAX_PAGES = 6
CAST = """pageInfo { hasNextPage } edges { role node { id name { full native } }
    voiceActors(sort: RELEVANCE) { id languageV2 name { full native } } }"""
QUERY_FIRST = """query ($ids: [Int]) { Page(page: 1, perPage: 10) { media(id_in: $ids, type: ANIME) { id
  characters(page: 1, perPage: 25, sort: [ROLE, RELEVANCE, ID]) { __CAST__ } } } }""".replace("__CAST__", CAST)
QUERY_PAGE = """query ($id: Int, $page: Int) { Media(id: $id, type: ANIME) { id
  characters(page: $page, perPage: 25, sort: [ROLE, RELEVANCE, ID]) { __CAST__ } } }""".replace("__CAST__", CAST)


def fetch_more(targets: list[int], raw_dir) -> int:
    """2차: 시즌마다 2쪽부터 다음 쪽이 없을 때까지 (최대 MAX_PAGES). 받은 쪽은 다시 요청하지 않음"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    sent, last = 0, 0.0
    for n, aid in enumerate(targets, 1):
        for page in range(2, MAX_PAGES + 1):
            path = raw_dir / f"{aid}_{page}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))["data"]
            else:
                time.sleep(max(0.0, last + PAUSE - time.time()))
                last = time.time()
                data, _ = post(QUERY_PAGE, {"id": aid, "page": page})
                if data.get("errors"):
                    raise SystemExit(f"AniList 오류: {data['errors'][:1]}")
                record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "data": data}
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                sent += 1
            if not data["data"]["Media"]["characters"]["pageInfo"]["hasNextPage"]:
                break
        if n % 50 == 0:
            print(f"  2차 {n}/{len(targets)} 시즌 · 지금까지 요청 {sent}번", flush=True)
    return sent


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    base = PACKAGE_ROOT / ".cache" / "raw" / "anilist_cast" / dt.date.today().strftime("%Y%m%d")
    first_dir, more_dir = base / "first_all_lang", base / "more_all_lang"

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("select external_id from dict.external_id where source_id = 'anilist' and id_kind = 'anime'")
        ids = sorted(int(r[0]) for r in cur.fetchall())
        cur.execute("""with pop as (select e.parent_id as work, max((f.value #>> '{}')::int) as p
                                    from dict.fact f join dict.entity e on e.entity_id = f.entity_id
                                    where f.field = 'popularity' and e.kind = 'entry' group by 1),
                            top as (select work from pop order by p desc limit %s)
                       select x.external_id::int
                       from dict.external_id x join dict.entity e on e.entity_id = x.entity_id
                       join top on top.work = e.parent_id
                       join dict.fact f on f.entity_id = e.entity_id and f.field = 'format' and f.source_id = 'anilist'
                       where x.source_id = 'anilist' and x.id_kind = 'anime'
                         and f.value #>> '{}' in ('TV', 'TV_SHORT', 'ONA')""", (TOP_WORKS,))
        priority = {r[0] for r in cur.fetchall()}
    fetch_batches(QUERY_FIRST, ids, first_dir, batch_size=FIRST_BATCH)

    has_more = []
    records: list[tuple[dt.datetime, int, dict]] = []
    for fetched_at, data in read_batches(first_dir):
        for m in data["Page"]["media"]:
            records.append((fetched_at, m["id"], m["characters"]))
            if m["characters"]["pageInfo"]["hasNextPage"] and m["id"] in priority:
                has_more.append(m["id"])
    print(f"다음 쪽을 더 받을 시즌 {len(has_more):,}개", flush=True)
    sent = fetch_more(sorted(has_more), more_dir)
    print(f"2차 요청 {sent}번", flush=True)
    for path in sorted(more_dir.glob("*.json")) if more_dir.exists() else []:
        record = json.loads(path.read_text(encoding="utf-8"))
        media = record["data"]["data"]["Media"]
        records.append((dt.datetime.fromisoformat(record["fetched_at"]), media["id"], media["characters"]))

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        entry_ids = store.external_map(ANILIST, "anime")
        chars, people, korean_names = {}, {}, {}
        for _, _, block in records:
            for edge in block["edges"]:
                node = edge["node"]
                chars[str(node["id"])] = (None, node["name"]["native"], node["name"]["full"])
                for va in edge["voiceActors"] or []:
                    if va["languageV2"] not in VOICE_LANGS:
                        continue
                    people[str(va["id"])] = (None, va["name"]["native"], va["name"]["full"])
                    if HANGUL.search(va["name"]["native"] or ""):
                        korean_names[str(va["id"])] = va["name"]["native"]
        char_ids, chars_made, _ = store.upsert_external(ANILIST, "character", "character", chars)
        person_ids, people_made, _ = store.upsert_external(ANILIST, "staff", "person", people)

        for fetched_at, aid, block in records:
            eid, url = entry_ids.get(str(aid)), ENTRY_URL.format(aid)
            for edge in block["edges"]:
                cid, node = char_ids[str(edge["node"]["id"])], edge["node"]
                store.fact(eid, "character", {"role": edge["role"]}, fetched_at, url, ANILIST, value_entity_id=cid)
                store.alias(cid, node["name"]["full"], "romaji", ANILIST)
                store.alias(cid, node["name"]["native"], "native", ANILIST)
                curl = CHARACTER_URL.format(node["id"])
                for va in edge["voiceActors"] or []:
                    if va["languageV2"] not in VOICE_LANGS:
                        continue
                    field, lang = VOICE_LANGS[va["languageV2"]]
                    pid = person_ids[str(va["id"])]
                    store.fact(cid, field, {"lang": lang}, fetched_at, curl, ANILIST, value_entity_id=pid)
                    store.alias(pid, va["name"]["full"], "romaji", ANILIST)
                    store.alias(pid, va["name"]["native"], "ko" if lang == "ko" else "native", ANILIST)
                    if str(va["id"]) in korean_names:
                        store.fact(pid, "name_ko", korean_names[str(va["id"])], fetched_at,
                                   f"https://anilist.co/staff/{va['id']}", ANILIST)
        upserted, deleted = store.write_facts(ANILIST, FIELDS)
        aliases = store.write_aliases()
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'anilist' and field = any(%s) group by 1 order by 2 desc""", (list(FIELDS),))
        by_field = cur.fetchall()

    print(f"캐릭터 {len(chars):,} (새로 {chars_made:,}) · 성우 {len(people):,} (새로 {people_made:,})")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
