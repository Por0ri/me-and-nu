"""위키백과 → 정보사전: 작품 문서 본문(한국어 · 영어)과 인물 문서 첫 문단(한국어) — 참고용 글

  uv run python -m ani_rag.load_wikipedia        (agents/rag 폴더에서)

- 어느 문서인지는 위키데이터 항목의 위키백과 연결(사이트링크)로 찾는다 (제목 맞추기 없음)
- 요청: 위키미디어 로봇 정책(초당 5회 미만, 동시 1개)보다 느리게 1초 간격
  · 작품 본문: 문서마다 1번 · 인물 첫 문단: 20명씩 1번
- 받은 원본은 .cache/raw/wikipedia/<날짜>/ 에 저장. 끊겨도 다시 돌리면 이어서 받음
- 이 스크립트가 맡는 칸: wiki_text_ref (시즌 · 작품, 출처 kowiki · enwiki), wiki_intro_ref (인물, 출처 kowiki)
- 라이선스 CC BY-SA 4.0. 사전엔 참고용으로만 두고, 글에 문장을 옮기지 않는다 (옮기면 출처 표시 · 같은 조건이 붙음)
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store

SPARQL = "https://query.wikidata.org/sparql"
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.0
SITES = {"ko": ("https://ko.wikipedia.org/", "kowiki"), "en": ("https://en.wikipedia.org/", "enwiki")}
SPARQL_CHUNK = 800
INTRO_BATCH = 20
INTRO_DIR = "intro_person_ko"


def http(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 60)
                time.sleep(wait + 1)
            elif e.code >= 500:
                time.sleep(20 * (attempt + 1))
            else:
                raise
        except (OSError, HTTPException):  # 인터넷 끊김 · 연결 끊김 · 시간 초과
            time.sleep(20 * (attempt + 1))
    raise SystemExit("요청이 5번 연속 실패함")


def sitelinks(items: list[str], lang: str, raw_dir: pathlib.Path, name: str = "sitelinks") -> dict[str, str]:
    """위키데이터 항목 → 위키백과 문서 제목. name 은 저장 파일 이름 (작품 · 인물이 겹치면 안 됨)"""
    out = {}
    for n, s in enumerate(range(0, len(items), SPARQL_CHUNK)):
        path = raw_dir / f"{name}_{lang}_{n:03d}.json"
        if not path.exists():
            values = " ".join(f"wd:{q}" for q in items[s:s + SPARQL_CHUNK])
            query = f"""SELECT ?item ?title WHERE {{ VALUES ?item {{ {values} }}
              ?a schema:about ?item ; schema:isPartOf <{SITES[lang][0]}> ; schema:name ?title }}"""
            data = http(SPARQL, urllib.parse.urlencode({"query": query}).encode(),
                        {"Accept": "application/sparql-results+json",
                         "Content-Type": "application/x-www-form-urlencoded"})
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(2)
        for b in json.loads(path.read_text(encoding="utf-8"))["results"]["bindings"]:
            out[b["item"]["value"].rsplit("/", 1)[-1]] = b["title"]["value"]
    return out


def api(lang: str, params: dict) -> dict:
    query = urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": 2, **params})
    return http(f"https://{lang}.wikipedia.org/w/api.php?{query}")


def fetch_full(titles: list[str], lang: str, raw_dir: pathlib.Path) -> None:
    (raw_dir / f"full_{lang}").mkdir(parents=True, exist_ok=True)
    done = 0
    for i, title in enumerate(titles):
        path = raw_dir / f"full_{lang}" / f"{i:05d}.json"
        if path.exists():
            continue
        data = api(lang, {"prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title})
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "title": title, "data": data}
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        done += 1
        if done % 200 == 0:
            print(f"  {lang} 본문 {done}개 받음 ({i + 1}/{len(titles)})", flush=True)
        time.sleep(PAUSE)


def fetch_intros(titles: list[str], raw_dir: pathlib.Path) -> None:
    (raw_dir / INTRO_DIR).mkdir(parents=True, exist_ok=True)
    for n, s in enumerate(range(0, len(titles), INTRO_BATCH)):
        path = raw_dir / INTRO_DIR / f"{n:04d}.json"
        if path.exists():
            continue
        batch = titles[s:s + INTRO_BATCH]
        data = api("ko", {"prop": "extracts", "explaintext": 1, "exintro": 1, "exlimit": "max", "redirects": 1,
                          "titles": "|".join(batch)})
        record = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "data": data}
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        time.sleep(PAUSE)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "wikipedia" / dt.date.today().strftime("%Y%m%d")
    raw_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        cur.execute("""select x.external_id, e.entity_id, e.kind from dict.external_id x
                       join dict.entity e using (entity_id) where x.source_id = 'wikidata' and x.id_kind = 'item'
                         and e.kind in ('work', 'entry', 'person')""")
        rows = cur.fetchall()
    work_items = sorted(q for q, _, k in rows if k in ("work", "entry"))
    person_items = sorted(q for q, _, k in rows if k == "person")
    entity_of = {q: eid for q, eid, _ in rows}

    work_titles = {lang: sitelinks(work_items, lang, raw_dir) for lang in SITES}
    person_ko = sitelinks(person_items, "ko", raw_dir, "person_sitelinks")
    print("문서 수: " + " · ".join(f"작품 {lang} {len(v):,}" for lang, v in work_titles.items())
          + f" · 인물 ko {len(person_ko):,}", flush=True)
    for lang, links in work_titles.items():
        fetch_full(sorted(set(links.values())), lang, raw_dir)
    fetch_intros(sorted(set(person_ko.values())), raw_dir)

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        stored = 0
        for lang, links in work_titles.items():
            source = SITES[lang][1]
            by_title: dict[str, list[str]] = {}
            for q, title in links.items():
                by_title.setdefault(title, []).append(q)
            for i, title in enumerate(sorted(by_title)):
                path = raw_dir / f"full_{lang}" / f"{i:05d}.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                page = (record["data"].get("query") or {}).get("pages", [{}])[0]
                text = re.sub(r"\n{3,}", "\n\n", (page.get("extract") or "").strip())
                if not text:
                    continue
                at = dt.datetime.fromisoformat(record["fetched_at"])
                url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                value = {"lang": lang, "title": title, "text": text}
                for q in by_title[title]:
                    store.fact(entity_of[q], "wiki_text_ref", value, at, url, source)
                    stored += 1
        title_to_person: dict[str, list[str]] = {}
        for q, title in person_ko.items():
            title_to_person.setdefault(title, []).append(q)
        intros = 0
        for path in sorted((raw_dir / INTRO_DIR).glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            at = dt.datetime.fromisoformat(record["fetched_at"])
            query = record["data"].get("query") or {}
            renamed = {r["to"]: r["from"] for r in query.get("redirects", []) + query.get("normalized", [])}
            for page in query.get("pages", []):
                title, text = page.get("title"), (page.get("extract") or "").strip()
                if not text:
                    continue
                for name in (title, renamed.get(title)):
                    for q in title_to_person.get(name or "", []):
                        url = f"https://ko.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                        store.fact(entity_of[q], "wiki_intro_ref", {"title": title, "text": text}, at, url, "kowiki")
                        intros += 1
        upserted = 0
        deleted = 0
        for source in ("kowiki", "enwiki"):
            u, d = store.write_facts(source, ("wiki_text_ref", "wiki_intro_ref"))
            upserted += u
            deleted += d

    print(f"본문 붙임 {stored:,} · 인물 첫 문단 붙임 {intros:,}")
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
