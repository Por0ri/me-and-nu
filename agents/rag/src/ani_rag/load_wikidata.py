"""위키데이터 → 정보사전 (구조화 데이터 CC0). 우리 AniList 번호로 맞춘다

  uv run python -m ani_rag.load_wikidata        (agents/rag 폴더에서)

- 질의: 시즌/극장판 1,000개씩 × 5가지(번호 · 날짜와 화수 · 제작사 · 감독 · 시리즈와 원작 한국어 이름)
  + 인물 1번 + 캐릭터 1번. 3,887개면 22번, 앞 질의가 끝나고 2초 쉼
- 받은 원본은 .cache/raw/wikidata/<날짜>/ 에 저장. 같은 날 다시 돌리면 받은 질의는 다시 보내지 않음
- 이 스크립트가 맡는 칸 (출처 wikidata): start_date · start_year · episodes · studio · director ·
  name_ko · birth_date · birthday · birth_year
- 인물 · 캐릭터는 위키데이터 항목에 붙은 AniList 번호(P11227 · P11736)로 우리 대상에 연결
- 제작사(P272)는 영어 이름으로 우리 스튜디오와 맞추고(대소문자 · 기호 무시), 못 맞추면 이름만 남김 (비교에서 빠짐)
- 한국어 이름: 시즌 항목 → 시즌 별칭, 위 시리즈 항목 → 작품 별칭, 원작 항목(정발 제목) → 작품 후보 별칭
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, norm, unique_pairs

WD = "wikidata"
URL = "https://query.wikidata.org/sparql"
UA = "menu-dict/0.1 (school project)"
PAUSE = 2.0
CHUNK = 1000
ITEM_URL = "https://www.wikidata.org/wiki/{}"
FIELDS = ("start_date", "start_year", "episodes", "studio", "director", "name_ko",
          "birth_date", "birthday", "birth_year")
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
RANK_OK = "?st wikibase:rank ?rank FILTER(?rank != wikibase:DeprecatedRank)"

# 시즌/극장판 질의 (__VALUES__ 자리에 AniList 번호 1,000개)
ENTRY_QUERIES = {
    "ids": """SELECT ?al ?item ?ko ?kowiki ?anidb ?ann ?kitsu ?syoboi ?livechart WHERE {
  VALUES ?al { __VALUES__ }
  ?item wdt:P8729 ?al .
  OPTIONAL { ?item rdfs:label ?ko FILTER(lang(?ko) = "ko") }
  OPTIONAL { ?kowiki schema:about ?item ; schema:isPartOf <https://ko.wikipedia.org/> }
  OPTIONAL { ?item wdt:P5646 ?anidb }
  OPTIONAL { ?item wdt:P1985 ?ann }
  OPTIONAL { ?item wdt:P11495 ?kitsu }
  OPTIONAL { ?item wdt:P11648 ?syoboi }
  OPTIONAL { ?item wdt:P12489 ?livechart }
}""",
    "dates": f"""SELECT ?al ?item ?prop ?t ?prec ?eps WHERE {{
  VALUES ?al {{ __VALUES__ }}
  ?item wdt:P8729 ?al .
  {{ ?item p:P580 ?st . ?st psv:P580 ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec .
     {RANK_OK} BIND("P580" AS ?prop) }}
  UNION
  {{ ?item p:P577 ?st . ?st psv:P577 ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec .
     {RANK_OK} BIND("P577" AS ?prop) }}
  UNION
  {{ ?item wdt:P1113 ?eps . BIND("P1113" AS ?prop) }}
}}""",
    "studio": """SELECT ?al ?item ?co ?coLabel WHERE {
  VALUES ?al { __VALUES__ }
  ?item wdt:P8729 ?al ; wdt:P272 ?co .
  OPTIONAL { ?co rdfs:label ?coLabel FILTER(lang(?coLabel) = "en") }
}""",
    "director": """SELECT ?al ?item ?d ?dal WHERE {
  VALUES ?al { __VALUES__ }
  ?item wdt:P8729 ?al ; wdt:P57 ?d .
  OPTIONAL { ?d wdt:P11227 ?dal }
}""",
    "names": """SELECT ?al ?pko ?bko WHERE {
  VALUES ?al { __VALUES__ }
  ?item wdt:P8729 ?al .
  OPTIONAL { ?item (wdt:P179|wdt:P361|wdt:P8345) ?parent .
             ?parent rdfs:label ?pko FILTER(lang(?pko) = "ko") }
  OPTIONAL { ?item wdt:P144 ?based . ?based rdfs:label ?bko FILTER(lang(?bko) = "ko") }
}""",
}
PERSON_QUERY = f"""SELECT ?sid ?item ?ko ?t ?prec WHERE {{
  ?item wdt:P11227 ?sid .
  OPTIONAL {{ ?item rdfs:label ?ko FILTER(lang(?ko) = "ko") }}
  OPTIONAL {{ ?item p:P569 ?st . ?st psv:P569 ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec .
             {RANK_OK} }}
}}"""
PERSON_MAL_QUERY = f"""SELECT ?mal ?item ?ko ?t ?prec WHERE {{
  VALUES ?mal {{ __VALUES__ }}
  ?item wdt:P4084 ?mal .
  OPTIONAL {{ ?item rdfs:label ?ko FILTER(lang(?ko) = "ko") }}
  OPTIONAL {{ ?item p:P569 ?st . ?st psv:P569 ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec .
             {RANK_OK} }}
}}"""
CHARACTER_QUERY = f"""SELECT ?cid ?item ?ko ?t ?prec ?bday WHERE {{
  ?item wdt:P11736 ?cid .
  OPTIONAL {{ ?item rdfs:label ?ko FILTER(lang(?ko) = "ko") }}
  OPTIONAL {{ ?item p:P569 ?st . ?st psv:P569 ?v . ?v wikibase:timeValue ?t ; wikibase:timePrecision ?prec .
             {RANK_OK} }}
  OPTIONAL {{ ?item wdt:P3150 ?b . ?b rdfs:label ?bday FILTER(lang(?bday) = "en") }}
}}"""


def post(query: str) -> dict:
    body = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "User-Agent": UA, "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
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
        except (urllib.error.URLError, TimeoutError):
            time.sleep(20 * (attempt + 1))
    raise SystemExit("위키데이터 질의가 4번 연속 실패함")


def fetch_all(jobs: list[tuple[str, str]], raw_dir) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    todo = [(name, q) for name, q in jobs if not (raw_dir / f"{name}.json").exists()]
    print(f"질의: 전체 {len(jobs)}번 중 남은 {len(todo)}번", flush=True)
    for n, (name, query) in enumerate(todo, 1):
        data = post(query)
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "data": data}
        (raw_dir / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        print(f"  {n}/{len(todo)} {name}: {len(data['results']['bindings']):,}줄", flush=True)
        time.sleep(PAUSE)


def read(raw_dir, prefix: str):
    """(받은 날짜, 결과 한 줄) — prefix_0.json · prefix_1.json … 또는 prefix.json"""
    for path in sorted(raw_dir.glob(f"{prefix}*.json")):
        if path.stem != prefix and not re.fullmatch(rf"{prefix}_\d+", path.stem):
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        at = dt.datetime.fromisoformat(record["fetched_at"])
        for b in record["data"]["results"]["bindings"]:
            yield at, {k: v["value"] for k, v in b.items()}


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def wd_date(t: str | None, prec: str | None) -> dict | None:
    """위키데이터 날짜 → 정밀도만큼만 (11 = 일, 10 = 월, 9 = 연)"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t or "")
    if not m or not prec:
        return None
    y, mo, d = map(int, m.groups())
    p = int(prec)
    if p >= 11:
        return {"year": y, "month": mo, "day": d}
    if p == 10:
        return {"year": y, "month": mo}
    if p == 9:
        return {"year": y}
    return None


def month_day(label: str | None) -> dict | None:
    """'May 5' · '5 May' → {month, day}"""
    m = re.fullmatch(r"([A-Za-z]+) (\d{1,2})", label or "") or re.fullmatch(r"(\d{1,2}) ([A-Za-z]+)", label or "")
    if not m:
        return None
    a, b = m.groups()
    month, day = (a, b) if a.isalpha() else (b, a)
    return {"month": MONTHS[month], "day": int(day)} if month in MONTHS else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "wikidata" / dt.date.today().strftime("%Y%m%d")
    stats: Counter = Counter()

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        entry_ids = store.external_map("anilist", "anime")
        person_ids = store.external_map("anilist", "staff")
        char_ids = store.external_map("anilist", "character")
        cur.execute("select entity_id, parent_id from dict.entity where kind = 'entry'")
        parent_of = dict(cur.fetchall())
        cur.execute("select entity_id, name_romaji from dict.entity where kind = 'studio'")
        studio_by_name: dict[str, set[int]] = defaultdict(set)
        for eid, name in cur.fetchall():
            studio_by_name[norm(name)].add(eid)

        ids = sorted(entry_ids, key=int)
        chunks = [ids[s:s + CHUNK] for s in range(0, len(ids), CHUNK)]
        jobs = [(f"{name}_{n}", q.replace("__VALUES__", " ".join(f'"{i}"' for i in chunk)))
                for name, q in ENTRY_QUERIES.items() for n, chunk in enumerate(chunks)]
        jobs += [("person", PERSON_QUERY), ("character", CHARACTER_QUERY)]
        # AniList 번호가 없는 주제가 가수는 MAL 인물 번호로 찾는다
        mal_people = store.external_map("mal", "people")
        mal_ids = sorted(mal_people, key=int)
        for n, s in enumerate(range(0, len(mal_ids), CHUNK)):
            values = " ".join(f'"{i}"' for i in mal_ids[s:s + CHUNK])
            jobs.append((f"person_mal_{n}", PERSON_MAL_QUERY.replace("__VALUES__", values)))
        fetch_all(jobs, raw_dir)

        items: list[tuple[str, int]] = []           # (위키데이터 항목, 우리 대상)
        others: dict[tuple[str, str], list] = defaultdict(list)  # (출처, 번호 종류) → [(번호, 대상)]
        item_of: dict[int, str] = {}

        def url(eid: int) -> str | None:
            return ITEM_URL.format(item_of[eid]) if eid in item_of else None

        # 위키데이터 항목 하나가 우리 시즌 여러 개에 붙은 경우(시리즈 전체 항목)가 있다.
        # 그 항목의 화수 · 날짜 · 제작사 · 감독은 시즌이 아니라 위 작품에 붙인다
        # (시즌에 붙이면 2기에 시리즈 전체 화수가 들어감)
        item_entries: dict[str, set[int]] = defaultdict(set)
        for _, b in read(raw_dir, "ids"):
            if b["al"] in entry_ids:
                item_entries[qid(b["item"])].add(entry_ids[b["al"]])

        def target(b: dict) -> int | None:
            q = qid(b["item"])
            entries = item_entries.get(q, set())
            if len(entries) == 1:
                eid = next(iter(entries))
            else:
                parents = {parent_of.get(e) for e in entries}
                if len(parents) != 1:
                    return None
                eid = next(iter(parents))
            item_of.setdefault(eid, q)
            return eid

        stats["시리즈 항목 → 작품"] = sum(1 for es in item_entries.values() if len(es) > 1)

        # 1) 시즌/극장판 번호 · 한국어 이름
        for _, b in read(raw_dir, "ids"):
            eid = target(b)
            if eid is None:
                continue
            q = qid(b["item"])
            items.append((q, eid))
            store.alias(eid, b.get("ko"), "ko", WD)
            if b.get("kowiki"):
                title = urllib.parse.unquote(b["kowiki"].rsplit("/", 1)[-1]).replace("_", " ")
                store.alias(eid, re.sub(r"\s*\([^)]*\)$", "", title), "ko", WD)
            entry = entry_ids.get(b["al"])  # 다른 사이트 번호는 시즌마다 (겹치면 아래에서 뺌)
            for key, src, kind in (("anidb", "anidb", "anime"), ("ann", "ann", "anime"),
                                   ("kitsu", "kitsu", "anime"), ("syoboi", "syoboi", "tid"),
                                   ("livechart", "livechart", "anime")):
                if b.get(key):
                    others[(src, kind)].append((b[key], entry))
        # 위 시리즈 이름은 우리 작품 하나에만 붙을 때만 쓴다. 여러 작품에 걸치면 묶음 이름
        # (예: '스튜디오 지브리의 장편 영화')이라 작품 제목이 아님. 원작 정발 제목은 후보라서 그대로 둠
        names = [(parent_of.get(entry_ids.get(b["al"])), b) for _, b in read(raw_dir, "names")]
        parent_works: dict[str, set[int]] = defaultdict(set)
        for work, b in names:
            if work and b.get("pko"):
                parent_works[b["pko"]].add(work)
        stats["묶음 이름이라 뺀 시리즈 이름"] = sum(1 for ws in parent_works.values() if len(ws) > 1)
        for work, b in names:
            if b.get("pko") and len(parent_works[b["pko"]]) == 1:
                store.alias(work, b["pko"], "ko", WD)
            store.alias(work, b.get("bko"), "ko", WD, candidate=True)

        # 2) 날짜 · 화수 (시작 시각 P580 이 있으면 그것, 없으면 공개일 P577)
        dates: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for at, b in read(raw_dir, "dates"):
            eid = target(b)
            if eid is None:
                continue
            if b["prop"] == "P1113" and b.get("eps"):
                store.fact(eid, "episodes", int(float(b["eps"])), at, url(eid), WD)
            elif b["prop"] in ("P580", "P577"):
                date = wd_date(b.get("t"), b.get("prec"))
                if date:
                    dates[eid][b["prop"]].append((at, date))
        for eid, by_prop in dates.items():
            for at, date in by_prop.get("P580") or by_prop.get("P577") or []:
                store.fact(eid, "start_date", date, at, url(eid), WD)
                store.fact(eid, "start_year", date["year"], at, url(eid), WD)

        # 3) 제작사 (이름으로 우리 스튜디오와 맞춤)
        for at, b in read(raw_dir, "studio"):
            eid = target(b)
            if eid is None:
                continue
            match = studio_by_name.get(norm(b.get("coLabel")), set())
            if len(match) == 1:
                sid = next(iter(match))
                store.fact(eid, "studio", {}, at, url(eid), WD, value_entity_id=sid)
                items.append((qid(b["co"]), sid))
                stats["제작사 맞춤"] += 1
            else:
                store.fact(eid, "studio", {"name": b.get("coLabel") or qid(b["co"])}, at, url(eid), WD)
                stats["제작사 못 맞춤"] += 1

        # 4) 감독 (위키데이터 인물 항목의 AniList 번호로 연결)
        for at, b in read(raw_dir, "director"):
            eid = target(b)
            pid = person_ids.get(b.get("dal") or "")
            if eid is None:
                continue
            if pid:
                store.fact(eid, "director", {}, at, url(eid), WD, value_entity_id=pid)
                stats["감독 연결"] += 1
            else:
                stats["감독 연결 못 함"] += 1

        # 5) 인물: 한국어 이름 · 생년월일
        person_seen = set()
        person_rows = [(at, b, person_ids.get(b["sid"])) for at, b in read(raw_dir, "person")]
        person_rows += [(at, b, mal_people.get(b["mal"])) for at, b in read(raw_dir, "person_mal")]
        for at, b, pid in person_rows:
            if pid is None:
                continue
            q = qid(b["item"])
            items.append((q, pid))
            item_of.setdefault(pid, q)
            person_seen.add(pid)
            if b.get("ko"):
                store.alias(pid, b["ko"], "ko", WD)
                store.fact(pid, "name_ko", b["ko"], at, ITEM_URL.format(q), WD)
            store.fact(pid, "birth_date", wd_date(b.get("t"), b.get("prec")), at, ITEM_URL.format(q), WD)
        stats["인물 연결"] = len(person_seen)

        # 6) 캐릭터: 한국어 이름 · 생일
        char_seen = set()
        for at, b in read(raw_dir, "character"):
            cid = char_ids.get(b["cid"])
            if cid is None:
                continue
            q = qid(b["item"])
            items.append((q, cid))
            item_of.setdefault(cid, q)
            char_seen.add(cid)
            if b.get("ko"):
                store.alias(cid, b["ko"], "ko", WD)
                store.fact(cid, "name_ko", b["ko"], at, ITEM_URL.format(q), WD)
            born = wd_date(b.get("t"), b.get("prec"))
            if born and "day" in born:
                store.fact(cid, "birthday", {"month": born["month"], "day": born["day"]}, at, ITEM_URL.format(q), WD)
            if born:
                store.fact(cid, "birth_year", born["year"], at, ITEM_URL.format(q), WD)
            store.fact(cid, "birthday", month_day(b.get("bday")), at, ITEM_URL.format(q), WD)
        stats["캐릭터 연결"] = len(char_seen)

        # 7) 번호 붙이기 · 넣기
        item_pairs, item_ambiguous = unique_pairs(items)
        added, clash = store.add_external_ids(WD, "item", item_pairs)
        stats["위키데이터 항목 새로 붙임"], stats["항목 겹침(뺌)"] = added, item_ambiguous + clash
        for (src, kind), pairs in others.items():
            p, amb = unique_pairs(pairs)
            a, c = store.add_external_ids(src, kind, p)
            stats[f"{src} 번호 새로 붙임"] = a
            stats[f"{src} 번호 겹침(뺌)"] = amb + c
        upserted, deleted = store.write_facts(WD, FIELDS)
        aliases_upserted = store.write_aliases(sync_source=WD)  # 잘못 붙었던 별칭도 이번 결과로 맞춰 지움
        cur.execute("""select field, count(*) from dict.fact where source_id = 'wikidata'
                       group by 1 order by 2 desc""")
        by_field = cur.fetchall()

    print("연결: " + " · ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("위키데이터 사실 칸별: " + " · ".join(f"{f} {n:,}" for f, n in by_field))
    print(f"별칭: 넣거나 고침 {aliases_upserted:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
