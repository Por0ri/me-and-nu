"""영상물등급위원회 → 정보사전: 극장판 한국 공식 제목 · 국내 관람등급, OTT 국내 서비스
(공공데이터포털, 이용허락범위 제한 없음)

  uv run python -m ani_rag.load_kmrb        (agents/rag 폴더에서)

- 요청: 영화 등급분류 전체(2009~) ÷ 1,000 + 자체등급분류 전체(2023-06~) ÷ 1,000 (지금 67번), 1초 간격
- 받은 원본은 .cache/raw/kmrb/<날짜>/ 에 저장. 인증키는 저장하지도 화면에 찍지도 않는다
- 애니메이션만 쓴다 (영화의 종별 · 영상물의 종류 = 애니메이션)
- 우리 작품에 맞추기 (틀리게 붙는 걸 막으려고 엄격하게):
  · 제목(원제 또는 한국 제목)을 정리해서(대소문자 · 기호 · 띄어쓰기 무시) 별칭과 똑같고, 제작 연도가 1년 안쪽
  · 극장판 기록은 우리 사전의 극장판(MOVIE)에만 붙인다 (원제가 시리즈 이름이라 TV 1기에 잘못 붙는 걸 막음)
  · OTT 기록은 시즌에 먼저 맞추고, 안 되면 작품에 붙인다
  · 후보가 둘 이상이면 넣지 않는다
- 이 스크립트가 맡는 칸 (출처 kmrb): kr_title · kr_rating · kr_ott
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import psycopg

from ani_rag.config import PACKAGE_ROOT, load_settings
from ani_rag.store import Store, norm

KMRB = "kmrb"
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.0
ROWS = 1000
FIELDS = ("kr_title", "kr_rating", "kr_ott")
APIS = {
    "movie": ("https://apis.data.go.kr/B551008/movie_v3/movie_search_v3", "20090101",
              "https://www.data.go.kr/data/15127668/openapi.do"),
    "ott": ("https://apis.data.go.kr/B551008/irating_v1/ir_search", "20230601",
            "https://www.data.go.kr/data/15142196/openapi.do"),
}
OTT_NAMES = [  # 자체등급분류 사업자 이름 → 서비스 이름
    ("넷플릭스", "넷플릭스"), ("디즈니", "디즈니+"), ("티빙", "티빙"), ("쿠팡", "쿠팡플레이"),
    ("웨이브", "웨이브"), ("왓챠", "왓챠"), ("애플", "Apple TV+"), ("아마존", "프라임 비디오"),
    ("라프텔", "라프텔"), ("엘지유플러스", "U+"), ("케이티", "KT"), ("에스케이", "SK"),
]
NATION = {"일본": "JP", "한국": "KR", "중국": "CN", "대만": "TW"}  # 영상물등급위원회 나라 → AniList 나라
PAREN_TAIL = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")
SEASON_TAIL = re.compile(r"\s*[:：\-]?\s*(?:시즌|season)\s*\d+.*$|\s*[:：\-]?\s*\d+\s*기\s*$", re.I)
EPISODE_TAIL = re.compile(r"\s*[:：\-]?\s*(?:제\s*)?\d+\s*(?:화|회)\s*$"
                          r"|\s*[:：\-]?\s*(?:ep|episode)\.?\s*[\d\-~ ]+\s*$", re.I)


def clean(title: str | None) -> str:
    """시즌 · 회차 · 괄호 꼬리를 벗긴 제목
    ('이수라 시즌 1 (12회)' → '이수라', 'Tokyo Revengers: Season 2' → 'Tokyo Revengers')"""
    t = (title or "").strip()
    while True:
        u = SEASON_TAIL.sub("", EPISODE_TAIL.sub("", PAREN_TAIL.sub("", t)).strip(" :：-")).strip(" :：-")
        if u == t:
            return t
        t = u


def get(url: str, params: dict, key: str) -> str:
    query = urllib.parse.urlencode({"serviceKey": key, **params})
    req = urllib.request.Request(url + "?" + query, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return res.read().decode("utf-8", "replace").replace(key, "***")
        except urllib.error.HTTPError as e:  # 오류 내용에 주소(인증키)가 섞이지 않게 숫자만 남김
            status = e.code
        except (urllib.error.URLError, TimeoutError):
            status = "연결 실패"
        print(f"  요청 실패 ({status}) → {20 * (attempt + 1)}초 쉼", flush=True)
        time.sleep(20 * (attempt + 1))
    raise SystemExit("영상물등급위원회 요청이 4번 연속 실패함")


def result_code(text: str) -> str | None:
    m = re.search(r"<resultCode>(.*?)</resultCode>", text)
    return m.group(1) if m else None


def parse(text: str) -> tuple[int, list[dict]]:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:  # 이상한 XML 은 읽지 않음
        raise SystemExit("응답 XML 에 DOCTYPE/ENTITY 가 있어서 읽지 않음")
    root = ET.fromstring(text)
    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("item")]
    return int(root.findtext(".//totalCount") or 0), items


def fetch_page(url: str, params: dict, key: str) -> str | None:
    """결과 코드가 00(정상)이면 응답. 아니면 쉬었다가 3번까지 다시. 끝내 안 되면 None"""
    for attempt in range(3):
        text = get(url, params, key)
        if result_code(text) == "00":
            return text
        time.sleep(5 * (attempt + 1))
    return None


def fetch_block(url: str, base: dict, key: str, row_start: int, size: int, path_stem: str,
                raw_dir, lost: Counter, name: str) -> None:
    """row_start 번째부터 size 건. 안 되면 10조각으로 나눠 받고(1,000 → 100 → 10), 10건짜리도 안 되면 건너뛰고 셈"""
    text = fetch_page(url, {**base, "numOfRows": size, "pageNo": (row_start - 1) // size + 1}, key)
    time.sleep(PAUSE)
    if text is not None:
        (raw_dir / f"{path_stem}.xml").write_text(text, encoding="utf-8")
        return
    if size <= 10:
        lost[name] += size
        print(f"  {name} {row_start:,}~{row_start + size - 1:,}번째 기록을 못 받음 (API 오류) → 건너뜀", flush=True)
        return
    print(f"  {name} {row_start:,}번째부터 {size:,}건이 API 오류 → {size // 10:,}건씩 나눠 받음", flush=True)
    for k in range(10):
        fetch_block(url, base, key, row_start + k * (size // 10), size // 10, f"{path_stem}_{k}",
                    raw_dir, lost, name)


def fetch_all(key: str, raw_dir) -> Counter:
    """전부 받기. 못 받은 기록 수(API 오류)를 돌려줌"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    lost: Counter = Counter()
    for name, (url, start, _) in APIS.items():
        base = {"stDate": start, "edDate": today}
        first = raw_dir / f"{name}_001.xml"
        if not first.exists():
            text = fetch_page(url, {**base, "pageNo": 1, "numOfRows": ROWS}, key)
            if text is None:
                raise SystemExit(f"{name} 첫 쪽을 못 받음 (API 오류)")
            first.write_text(text, encoding="utf-8")
            time.sleep(PAUSE)
        total, _ = parse(first.read_text(encoding="utf-8"))
        pages = -(-total // ROWS)
        print(f"{name}: 전체 {total:,}건 · {pages}쪽", flush=True)
        for page in range(2, pages + 1):
            stem = f"{name}_{page:03d}"
            if (raw_dir / f"{stem}.xml").exists() or any(raw_dir.glob(f"{stem}_*.xml")):
                continue
            fetch_block(url, base, key, (page - 1) * ROWS + 1, ROWS, stem, raw_dir, lost, name)
    return lost


def year(text: str | None) -> int | None:
    m = re.match(r"(\d{4})", text or "")
    return int(m.group(1)) if m else None


def ymd(text: str | None) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def service_name(corp: str) -> str:
    for keyword, name in OTT_NAMES:
        if keyword in corp:
            return name
    return re.sub(r"\s*(유한회사|주식회사|\(주\)|㈜)\s*", "", corp).strip()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    settings = load_settings()
    if not settings.kmrb_key:
        raise SystemExit(".env 의 KMRB_SERVICE_KEY 가 비어 있음")
    raw_dir = PACKAGE_ROOT / ".cache" / "raw" / "kmrb" / dt.date.today().strftime("%Y%m%d")
    lost = fetch_all(settings.kmrb_key, raw_dir)
    if lost:
        print("API 오류로 못 받은 기록: " + " · ".join(f"{k} {v:,}건" for k, v in lost.items()))

    records = {"movie": [], "ott": []}
    for name in records:
        for path in sorted(raw_dir.glob(f"{name}_*.xml")):
            text = path.read_text(encoding="utf-8")
            if result_code(text) != "00":  # 예전에 저장된 오류 응답은 건너뜀
                continue
            at = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)  # 받은 시각 = 원본 저장 시각
            records[name] += [{**r, "_at": at} for r in parse(text)[1]]
    movies = [r for r in records["movie"] if r.get("mvAssoName") == "애니메이션"]
    otts = [r for r in records["ott"] if r.get("vidKndNm") == "애니메이션"]
    print(f"애니메이션: 영화 {len(movies):,} / {len(records['movie']):,} · OTT {len(otts):,} / {len(records['ott']):,}")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        cur.execute("select entity_id, kind, parent_id from dict.entity where kind in ('work', 'entry')")
        kind_of, parent_of = {}, {}
        for eid, kind, parent in cur.fetchall():
            kind_of[eid], parent_of[eid] = kind, parent
        cur.execute("""select entity_id, (value #>> '{}')::int from dict.fact
                       where source_id = 'anilist' and field in ('start_year', 'season_year')""")
        years: dict[int, set[int]] = defaultdict(set)
        for eid, y in cur.fetchall():
            years[eid].add(y)
            if parent_of.get(eid):
                years[parent_of[eid]].add(y)
        cur.execute("select entity_id, value #>> '{}' from dict.fact where source_id = 'anilist' and field = 'format'")
        fmt = dict(cur.fetchall())
        cur.execute("select entity_id, value #>> '{}' from dict.fact where source_id = 'anilist' and field = 'country'")
        countries: dict[int, set[str]] = defaultdict(set)
        for eid, c in cur.fetchall():
            countries[eid].add(c)
            if parent_of.get(eid):
                countries[parent_of[eid]].add(c)
        # 맞추기 재료에서 이 스크립트가 만든 별칭(kmrb)은 뺀다 — 자기 결과를 다시 재료로 쓰면 돌릴 때마다 결과가 달라짐
        cur.execute("""select a.entity_id, a.alias from dict.alias a join dict.entity e using (entity_id)
                       where e.kind in ('work', 'entry') and not a.is_candidate and a.source_id <> 'kmrb'""")
        by_alias: dict[tuple[str, str], set[int]] = defaultdict(set)
        for eid, alias in cur.fetchall():
            by_alias[(kind_of[eid], norm(alias))].add(eid)

        def near(eid: int, y: int | None, slack: int = 1) -> bool:
            if y is None or not years.get(eid):
                return False
            ys = years[eid]
            return min(ys) - slack <= y <= max(ys) + slack

        def find(kind: str, titles: list[str | None], y: int | None, nation: str | None,
                 only=None) -> tuple[int | None, str]:
            found = set()
            for t in titles:
                if t and norm(t):
                    found |= by_alias.get((kind, norm(t)), set())
            if only:
                found = {e for e in found if only(e)}
            found = {e for e in found if near(e, y)}
            if nation:  # 제작 나라가 우리 쪽(AniList 나라)과 다르면 뺌
                found = {e for e in found if not countries.get(e) or nation in countries[e]}
            if len(found) == 1:
                return next(iter(found)), "맞음"
            return None, ("애매함" if found else "없음")

        stats: Counter = Counter()
        # 1) 영화 등급: 극장판은 우리 극장판(MOVIE)에만. 시즌 · 회차 꼬리가 붙은 기록(TV 시리즈)은 작품에
        for r in movies:
            nation = NATION.get(r.get("prodcNatnlName") or "")
            raw = [r.get("oriTitle"), r.get("useTitle"), re.sub(r"^극장판\s*", "", r.get("useTitle") or "")]
            cleaned = [clean(t) for t in raw]
            y = year(r.get("prodYear"))
            eid, why = find("entry", raw + cleaned, y, nation, only=lambda e: fmt.get(e) == "MOVIE")
            level = "극장판"
            had_tail = any(norm(c) != norm(t) for c, t in zip(cleaned, raw, strict=True))  # 끝 기호만 다른 건 꼬리 아님
            if not eid and had_tail:
                eid2, why2 = find("work", cleaned, y, nation)
                if eid2 or why2 != "없음":
                    eid, why, level = eid2, why2, "시리즈"
            stats[f"영화 등급 · {level} {why}"] += 1
            if not eid:
                continue
            ko = clean(r["useTitle"]) if level == "시리즈" else r["useTitle"]
            store.fact(eid, "kr_title", ko, r["_at"], APIS["movie"][2], KMRB)
            store.alias(eid, ko, "ko", KMRB)
            store.fact(eid, "kr_rating", {"grade": r.get("gradeName"), "date": ymd(r.get("rtDate")),
                                          "rt_no": r.get("rtNo"), "applicant": r.get("aplcName") or None,
                                          "title": r.get("useTitle")},
                       r["_at"], APIS["movie"][2], KMRB)

        # 2) OTT: 시즌에 먼저, 안 되면 작품에. 회차마다 나뉜 기록은 서비스마다 가장 이른 날짜로 묶음
        ott_first: dict[tuple[int, str, str], tuple[str, dt.datetime]] = {}
        for r in otts:
            nation = NATION.get(r.get("ntnNm") or "")
            title = clean(r.get("wrksNm"))
            titles = [title, clean(r.get("oriwkTtl")), r.get("oriwkTtl")]
            y = year(r.get("fbctnYr"))
            eid, why = find("entry", titles, y, nation)
            if not eid:
                eid2, why2 = find("work", titles, y, nation)
                if eid2 or why2 != "없음":
                    eid, why = eid2, f"작품 {why2}"
            stats[f"OTT {why}"] += 1
            if not eid:
                continue
            service, since = service_name(r.get("corpNm") or ""), ymd(r.get("srvcPvsnYmd"))
            key = (eid, service, title)
            if since and (key not in ott_first or since < ott_first[key][0]):
                ott_first[key] = (since, r["_at"])
            store.fact(eid, "kr_title", title, r["_at"], APIS["ott"][2], KMRB)
            store.alias(eid, title, "ko", KMRB)
        for (eid, service, title), (since, at) in ott_first.items():
            store.fact(eid, "kr_ott", {"service": service, "since": since, "title": title},
                       at, APIS["ott"][2], KMRB)

        upserted, deleted = store.write_facts(KMRB, FIELDS)
        aliases = store.write_aliases(sync_source=KMRB)
        cur.execute("""select count(distinct w.entity_id) from dict.entity w
                       join dict.entity e on e.parent_id = w.entity_id or e.entity_id = w.entity_id
                       join dict.fact f on f.entity_id = e.entity_id and f.source_id = 'kmrb' and f.field = 'kr_title'
                       where w.kind = 'work' and w.name_ko is null""")
        new_ko = cur.fetchone()[0]
        cur.execute("""select value->>'service', count(distinct entity_id) from dict.fact
                       where source_id = 'kmrb' and field = 'kr_ott' group by 1 order by 2 desc""")
        services = cur.fetchall()

    print("맞추기: " + " · ".join(f"{k} {v:,}" for k, v in sorted(stats.items())))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,} · 별칭 넣거나 고침 {aliases:,}")
    print(f"한국어 제목이 비어 있던 작품 중 한국 공식 제목이 생긴 작품: {new_ko:,}")
    print("OTT 서비스별 (대상 수): " + " · ".join(f"{s} {n:,}" for s, n in services))
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
