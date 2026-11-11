"""위키데이터 추가 칸 (구조화 데이터 CC0): 공식 사이트 · SNS · 수상 · 배경 장소 · 흥행 수입 · 원작 정보 ·
인물 직업 · 출신지 · 소속 · 스튜디오 설립일

  uv run python -m ani_rag.load_wikidata_extra        (agents/rag 폴더에서)

- 대상: 사전에 위키데이터 항목이 붙은 시즌 · 작품 · 인물 · 스튜디오
- 질의: 항목 800개씩, 칸 묶음마다 1번. 앞 질의가 끝나고 2초 쉼. 받은 원본은 .cache/raw/wikidata_extra/<날짜>/ 에 저장
- 이 스크립트가 맡는 칸 (출처 wikidata): official_site · x_account · award · narrative_location · box_office ·
  screenwriter · broadcaster · distributor · genre_wd · main_subject · source_info · occupation ·
  birthplace · affiliation · record_label · alma_mater · death_date · founded · headquarters · founder · parent_org
- MusicBrainz 번호(P434)는 다음 연결에 쓰려고 외부 번호로 붙여 둔다
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
from collections import defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, unique_pairs

WD = "wikidata"
URL = "https://query.wikidata.org/sparql"
UA = "menu-dict/0.1 (school project)"
ITEM_URL = "https://www.wikidata.org/wiki/{}"
PAUSE = 2.0
CHUNK = 800
FIELDS = ("official_site", "x_account", "award", "narrative_location", "box_office", "screenwriter", "broadcaster",
          "distributor", "genre_wd", "main_subject", "source_info", "occupation", "birthplace", "affiliation",
          "record_label", "alma_mater", "death_date", "founded", "headquarters", "founder", "parent_org")
LABELS = """OPTIONAL { ?v rdfs:label ?ko FILTER(lang(?ko) = "ko") }
  OPTIONAL { ?v rdfs:label ?en FILTER(lang(?en) = "en") }"""


def union(pairs: dict[str, str]) -> str:
    return "\n  UNION ".join(f'{{ ?item wdt:{prop} ?v . BIND("{name}" AS ?p) }}' for name, prop in pairs.items())


LINKS = {"official_site": "P856", "x_account": "P2002", "musicbrainz": "P434"}
WORK_ENTITIES = {"narrative_location": "P840", "screenwriter": "P58", "broadcaster": "P449", "distributor": "P750",
                 "genre_wd": "P136", "main_subject": "P921"}
PERSON_ENTITIES = {"occupation": "P106", "birthplace": "P19", "affiliation": "P108", "affiliation2": "P1416",
                   "record_label": "P264", "alma_mater": "P69"}
STUDIO_ENTITIES = {"headquarters": "P159", "founder": "P112", "parent_org": "P749"}
QUERIES = {
    "links": f"SELECT ?item ?p ?v WHERE {{ VALUES ?item {{ __VALUES__ }}\n  {union(LINKS)} }}",
    "award": f"""SELECT ?item ?v ?ko ?en ?year WHERE {{ VALUES ?item {{ __VALUES__ }}
  ?item p:P166 ?st . ?st ps:P166 ?v .
  OPTIONAL {{ ?st pq:P585 ?t BIND(year(?t) AS ?year) }}
  {LABELS} }}""",
}
WORK_QUERIES = {
    "work_entities": f"""SELECT ?item ?p ?v ?ko ?en WHERE {{ VALUES ?item {{ __VALUES__ }}
  {union(WORK_ENTITIES)}
  {LABELS} }}""",
    "box_office": """SELECT ?item ?amount ?unit ?unitEn WHERE { VALUES ?item { __VALUES__ }
  ?item p:P2142/psv:P2142 [ wikibase:quantityAmount ?amount ; wikibase:quantityUnit ?unit ] .
  OPTIONAL { ?unit rdfs:label ?unitEn FILTER(lang(?unitEn) = "en") } }""",
    "source": """SELECT ?item ?src ?srcKo ?p ?v ?ko ?en WHERE { VALUES ?item { __VALUES__ }
  ?item wdt:P144 ?src .
  OPTIONAL { ?src rdfs:label ?srcKo FILTER(lang(?srcKo) = "ko") }
  { ?src wdt:P50 ?v . BIND("author" AS ?p) } UNION { ?src wdt:P123 ?v . BIND("publisher" AS ?p) }
  UNION { ?src wdt:P1433 ?v . BIND("magazine" AS ?p) }
  OPTIONAL { ?v rdfs:label ?ko FILTER(lang(?ko) = "ko") }
  OPTIONAL { ?v rdfs:label ?en FILTER(lang(?en) = "en") } }""",
}
PERSON_QUERIES = {
    "person_entities": f"""SELECT ?item ?p ?v ?ko ?en WHERE {{ VALUES ?item {{ __VALUES__ }}
  {union(PERSON_ENTITIES)}
  {LABELS} }}""",
    "death": "SELECT ?item ?t ?prec WHERE { VALUES ?item { __VALUES__ } "
             "?item p:P570/psv:P570 [ wikibase:timeValue ?t ; wikibase:timePrecision ?prec ] }",
}
STUDIO_QUERIES = {
    "studio_entities": f"""SELECT ?item ?p ?v ?ko ?en WHERE {{ VALUES ?item {{ __VALUES__ }}
  {union(STUDIO_ENTITIES)}
  {LABELS} }}""",
    "founded": "SELECT ?item ?t ?prec WHERE { VALUES ?item { __VALUES__ } "
               "?item p:P571/psv:P571 [ wikibase:timeValue ?t ; wikibase:timePrecision ?prec ] }",
}


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


def read(raw_dir, prefix: str):
    for path in sorted(raw_dir.glob(f"{prefix}_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        at = dt.datetime.fromisoformat(record["fetched_at"])
        for b in record["data"]["results"]["bindings"]:
            yield at, {k: v["value"] for k, v in b.items()}


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def wd_date(t: str | None, prec: str | None) -> dict | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t or "")
    if not m or not prec:
        return None
    y, mo, d = map(int, m.groups())
    p = int(prec)
    return ({"year": y, "month": mo, "day": d} if p >= 11 else {"year": y, "month": mo} if p == 10
            else {"year": y} if p == 9 else None)


def label(b: dict) -> dict:
    return {"ko": b.get("ko"), "en": b.get("en"), "wikidata": qid(b["v"])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "wikidata_extra" / dt.date.today().strftime("%Y%m%d")
    raw_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select x.external_id, e.entity_id, e.kind from dict.external_id x
                       join dict.entity e using (entity_id) where x.source_id = 'wikidata' and x.id_kind = 'item'""")
        rows = cur.fetchall()
    entity_of = {q: eid for q, eid, _ in rows}
    by_kind: dict[str, list[str]] = defaultdict(list)
    for q, _, kind in rows:
        by_kind[{"work": "work", "entry": "work", "person": "person", "studio": "studio"}.get(kind, "other")].append(q)
    print("항목 수: " + " · ".join(f"{k} {len(v):,}" for k, v in by_kind.items()), flush=True)

    jobs = []
    for kind, queries in (("work", {**QUERIES, **WORK_QUERIES}), ("person", {**QUERIES, **PERSON_QUERIES}),
                          ("studio", {**QUERIES, **STUDIO_QUERIES})):
        items = sorted(by_kind.get(kind, []))
        for n, s in enumerate(range(0, len(items), CHUNK)):
            values = " ".join(f"wd:{q}" for q in items[s:s + CHUNK])
            jobs += [(f"{kind}_{name}_{n:02d}", q.replace("__VALUES__", values)) for name, q in queries.items()]
    todo = [(name, q) for name, q in jobs if not (raw_dir / f"{name}.json").exists()]
    print(f"질의: 전체 {len(jobs)}번 중 남은 {len(todo)}번", flush=True)
    for i, (name, query) in enumerate(todo, 1):
        data = post(query)
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "data": data}
        (raw_dir / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        if i % 10 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(PAUSE)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)

        def add(b, field, value, at):
            eid = entity_of.get(qid(b["item"]))
            store.fact(eid, field, value, at, ITEM_URL.format(qid(b["item"])), WD)

        music = []
        for prefix in ("work", "person", "studio"):
            for at, b in read(raw_dir, f"{prefix}_links"):
                if b["p"] == "official_site":
                    add(b, "official_site", {"url": b["v"]}, at)
                elif b["p"] == "x_account":
                    add(b, "x_account", b["v"], at)
                elif b["p"] == "musicbrainz" and qid(b["item"]) in entity_of:
                    music.append((b["v"], entity_of[qid(b["item"])]))
            for at, b in read(raw_dir, f"{prefix}_award"):
                value = label(b)
                if b.get("year"):
                    value["year"] = int(float(b["year"]))
                add(b, "award", value, at)
        for name in ("work_entities", "person_entities", "studio_entities"):
            prefix = name.split("_")[0]
            for at, b in read(raw_dir, f"{prefix}_{name}"):
                add(b, "affiliation" if b["p"] == "affiliation2" else b["p"], label(b), at)
        for at, b in read(raw_dir, "work_box_office"):
            add(b, "box_office", {"amount": float(b["amount"]), "unit": b.get("unitEn") or qid(b["unit"])}, at)
        sources = defaultdict(lambda: {"author": [], "publisher": [], "magazine": []})
        src_title = {}
        src_at = {}
        for at, b in read(raw_dir, "work_source"):
            key = (qid(b["item"]), qid(b["src"]))
            src_title[key], src_at[key] = b.get("srcKo"), at
            sources[key][b["p"]].append(label(b))
        for (item, src), parts in sources.items():
            store.fact(entity_of.get(item), "source_info", {"source": src, "ko": src_title[(item, src)], **parts},
                       src_at[(item, src)], ITEM_URL.format(item), WD)
        for field, prefix in (("death_date", "person_death"), ("founded", "studio_founded")):
            for at, b in read(raw_dir, prefix):
                add(b, field, wd_date(b.get("t"), b.get("prec")), at)
        pairs, ambiguous = unique_pairs(music)
        added, clash = store.add_external_ids("musicbrainz", "artist", pairs)
        upserted, deleted = store.write_facts(WD, FIELDS)
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'wikidata' and field = any(%s) group by 1 order by 2 desc""", (list(FIELDS),))
        by_field = cur.fetchall()

    print(f"MusicBrainz 번호: 새로 붙임 {added:,} · 겹침 {ambiguous + clash}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
