"""일본 미디어예술 데이터베이스(MADB) → 정보사전: 방영 시작 · 종료일 · 화수 · 감독 · 제작사 · 주제가 (세 번째 검산 출처)

  uv run python -m ani_rag.load_madb        (agents/rag 폴더에서)

- GitHub 공식 데이터셋의 애니 파일 3개만 받는다 (TV 시리즈 207 · TV 특별편 208 · 극장판 시리즈 210)
- 자유로운 2차 이용 가능. 가공한 데이터임을 밝히고, 데이터셋 버전을 적어 달라는 부탁이 있다 (출처 표 참고)
- 받은 원본은 .cache/raw/madb/<날짜>/ 에 저장
- 우리 시즌에 맞추기: 일본어 제목이 우리 원어 제목과 똑같고(기호 · 띄어쓰기 무시), 시작 연도가 1년 안쪽,
  형식이 맞을 때만 (TV ↔ 207 · 208, 극장판 ↔ 210). 후보가 둘 이상이면 넣지 않는다
- 이 스크립트가 맡는 칸 (출처 madb): start_date · start_year · end_date · episodes · director ·
  animation_studio_ja · track_text_ja
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, norm, unique_pairs

MADB = "madb"
UA = "menu-dict/0.1 (school project)"
ZIP_URL = "https://github.com/mediaarts-db/dataset/releases/latest/download/metadata{}_json.zip"
ITEM_URL = "https://mediaarts-db.artmuseums.go.jp/id/{}"
FILES = {"207": ("TV", "TV_SHORT", "ONA"), "208": ("SPECIAL", "TV"), "210": ("MOVIE",)}
FIELDS = ("start_date", "start_year", "end_date", "episodes", "director", "animation_studio_ja", "track_text_ja")
ROLE = re.compile(r"\[(.+?)\]\s*(.+)")


def titles_of(item: dict) -> list[str]:
    names = [item.get("rdfs:label")]
    raw = item.get("schema:name")
    for n in raw if isinstance(raw, list) else [raw]:
        if isinstance(n, str):
            names.append(n)
    return [n for n in names if n]


def date_of(text: str | None) -> dict | None:
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text or "")
    if not m:
        return None
    return {k: int(v) for k, v in zip(("year", "month", "day"), m.groups(), strict=True) if v}


def roles(text: str | None) -> list[tuple[str, str]]:
    out = []
    for part in re.split(r"\s*／\s*", text or ""):
        m = ROLE.match(part.strip())
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "madb" / dt.date.today().strftime("%Y%m%d")
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for code, formats in FILES.items():
        path = raw_dir / f"metadata{code}_json.zip"
        if not path.exists():
            req = urllib.request.Request(ZIP_URL.format(code), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as res:
                path.write_bytes(res.read())
        at = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
        with zipfile.ZipFile(path) as z:
            graph = json.loads(z.read(z.namelist()[0]).decode("utf-8"))["@graph"]
        records += [(at, formats, item) for item in graph]
    print(f"MADB 기록 {len(records):,}개", flush=True)

    stats: Counter = Counter()
    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        cur.execute("""select a.entity_id, a.alias from dict.alias a join dict.entity e using (entity_id)
                       where e.kind = 'entry' and a.lang = 'native' and a.source_id = 'anilist'""")
        by_title: dict[str, set[int]] = defaultdict(set)
        for eid, alias in cur.fetchall():
            by_title[norm(alias)].add(eid)
        cur.execute("""select entity_id, field, value #>> '{}' from dict.fact
                       where source_id = 'anilist' and field in ('format', 'start_year')""")
        fmt, year = {}, {}
        for eid, field, value in cur.fetchall():
            (fmt if field == "format" else year)[eid] = value
        cur.execute("""select a.entity_id, a.alias from dict.alias a join dict.entity e using (entity_id)
                       where e.kind = 'person' and a.lang in ('native', 'ko') and a.source_id = 'anilist'""")
        person_by_name: dict[str, set[int]] = defaultdict(set)
        for eid, alias in cur.fetchall():
            person_by_name[norm(alias)].add(eid)

        pairs = []
        for at, formats, item in records:
            start = date_of(item.get("schema:startDate") or item.get("schema:datePublished"))
            if not start:
                stats["날짜 없음"] += 1
                continue
            found = set()
            for title in titles_of(item):
                found |= by_title.get(norm(title), set())
            found = {e for e in found if fmt.get(e) in formats and year.get(e)
                     and abs(int(year[e]) - start["year"]) <= 1}
            if len(found) != 1:
                stats["맞음 없음" if not found else "애매함"] += 1
                continue
            stats["맞음"] += 1
            eid, mid = next(iter(found)), item["schema:identifier"]
            url = ITEM_URL.format(mid)
            pairs.append((mid, eid))
            store.fact(eid, "start_date", start, at, url, MADB)
            store.fact(eid, "start_year", start["year"], at, url, MADB)
            store.fact(eid, "end_date", date_of(item.get("schema:endDate")), at, url, MADB)
            eps = item.get("schema:numberOfEpisodes")
            if eps and str(eps).isdigit():
                store.fact(eid, "episodes", int(eps), at, url, MADB)
            for role, name in roles(item.get("schema:contributor")):
                if role in ("監督", "総監督"):
                    who = person_by_name.get(norm(name), set())
                    if len(who) == 1:
                        store.fact(eid, "director", {}, at, url, MADB, value_entity_id=next(iter(who)))
                        stats["감독 연결"] += 1
                    else:
                        stats["감독 연결 못 함"] += 1
            for role, name in roles(item.get("schema:productionCompany")):
                if role == "アニメーション制作":
                    store.fact(eid, "animation_studio_ja", name, at, url, MADB)
            if item.get("schema:track"):
                store.fact(eid, "track_text_ja", item["schema:track"], at, url, MADB)
        mapped, _ = unique_pairs(pairs)
        stats["MADB 번호 새로 붙임"] = store.add_external_ids(MADB, "item", mapped)[0]
        upserted, deleted = store.write_facts(MADB, FIELDS)
        cur.execute("""select field, count(*), count(distinct entity_id) from dict.fact
                       where source_id = 'madb' group by 1 order by 2 desc""")
        by_field = cur.fetchall()

    print("맞추기: " + " · ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("칸별 (줄 · 대상): " + " · ".join(f"{f} {n:,} · {e:,}" for f, n, e in by_field))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
