"""영화관입장권 통합전산망(KOBIS) 오픈 API → 정보사전: 일본 애니메이션 극장판의 한국 누적 관객 수

  uv run python -m ani_rag.load_kobis              (agents/rag 폴더에서)
  uv run python -m ani_rag.load_kobis --no-fetch   (요청 없이, 이미 뽑아 둔 숫자로 맞추기만 다시)

- 약관 (11/10 확인). 서원 11/11 결정으로 아래처럼 최소한으로만 쓴다
  · 제6조: 결과는 실시간으로 쓰고 복제 · 저장 · 재전송을 제한함 → 받은 응답 원본은 디스크에 저장하지 않는다.
    일본 애니 극장판의 영화 코드 · 제목 · 개봉일 · 누적 관객 수만 뽑아 둔다 (끊겨도 이어 받으려고 .cache/kobis/)
  · 제6조 "통합전산망 오픈 API" 이용 명시 · 제3조 출처 표시 → 출처 이름에 넣고 글 출처 목록에 붙인다
  · 영진위 문의 답이 '안 됨'이면 kobis 사실을 지우고 그 글을 내린다
- 요청 (1초 간격, 하루 한도 3,000번 안. 키 · 요청 주소는 화면에 찍지 않는다)
  1) 나라 코드 1번 → 일본 코드
  2) 일본 영화 목록 (100편씩) → 장르에 애니메이션이 있는 영화만 후보
  3) 주간 박스오피스 (외국 영화 상위 10), 2004년 첫 주부터 지난주까지 → 후보 영화마다 누적 관객 수의 최댓값
- 우리 사전 극장판(AniList 형식 MOVIE)에 맞추기 (틀리게 붙는 걸 막으려고 엄격하게)
  · 한국 제목 · 영어 제목을 정리해서(기호 · 띄어쓰기 무시, '극장판' 머리 뗌) 별칭과 똑같을 때
  · 제작 연도가 방영 시작 연도 ±1, 또는 한국 개봉 연도가 방영 시작 연도부터 3년 안
  · 후보가 둘 이상이면 넣지 않는다
- 이 스크립트가 맡는 칸 (출처 kobis): kr_audience
"""

from __future__ import annotations

import argparse
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
from ani_rag.store import Store, norm

KOBIS = "kobis"
BASE = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
SITE = "https://www.kobis.or.kr/kobisopenapi/homepg/main/main.do"  # 사실의 출처 주소 (요청 주소에는 키가 있어서 안 씀)
UA = "menu-dict/0.1 (school project)"
PAUSE = 1.0
FIRST_SUNDAY = dt.date(2004, 1, 4)
CACHE = PACKAGE_ROOT / ".cache" / "kobis"
FIELDS = ("kr_audience",)
HEAD = re.compile(r"^\s*(?:극장판|극장\s판|劇場版)\s*[:：]?\s*")
TAIL = re.compile(r"\s*[(（\[]\s*(?:자막|더빙|재개봉|디지털|IMAX|4DX|3D|2D|감독판|리마스터|확장판)[^)）\]]*[)）\]]\s*$", re.I)

_last = 0.0
calls = 0


def call(path: str, params: dict, key: str) -> dict:
    """요청 하나. 오류 응답이면 바로 멈춘다 (오류 내용에서 키는 지운다)"""
    global _last, calls
    req = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode({'key': key, **params})}",
                                 headers={"User-Agent": UA})
    for attempt in range(4):
        time.sleep(max(0.0, _last + PAUSE - time.time()))
        _last = time.time()
        calls += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read().decode("utf-8", "replace").replace(key, "***"))
        except urllib.error.HTTPError as e:  # 오류 내용에 요청 주소(키)가 섞이지 않게 숫자만 남김
            status = e.code
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            status = "연결 실패"
        else:
            fault = data.get("faultInfo")
            if fault:
                message = str(fault.get("message") or "").replace(key, "***")[:120]
                raise SystemExit(f"KOBIS 오류 코드 {fault.get('errorCode')}: {message} → 멈춤 (요청 {calls}번째)")
            return data
        print(f"  요청 실패 ({status}) → {20 * (attempt + 1)}초 쉼", flush=True)
        time.sleep(20 * (attempt + 1))
    raise SystemExit("KOBIS 요청이 4번 연속 실패함 → 멈춤")


def load_json(name: str, default):
    path = CACHE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(name: str, data) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def ymd(text: str | None) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def fetch_films(key: str) -> dict[str, dict]:
    """일본 영화 중 장르에 애니메이션이 있는 영화 → {영화 코드: 필요한 칸만}"""
    films = load_json("films.json", None)
    if films is not None:
        return films
    codes = call("code/searchCodeList.json", {"comCode": "2204"}, key).get("codes") or []
    japan = next((c["fullCd"] for c in codes if c.get("korNm") == "일본"), None)
    if not japan:
        raise SystemExit("나라 코드 목록에서 일본을 못 찾음")
    films, page, total = {}, 1, None
    while True:
        result = call("movie/searchMovieList.json", {"curPage": page, "itemPerPage": 100, "repNationCd": japan,
                                                     "openStartDt": 2003, "openEndDt": dt.date.today().year}, key)
        block = result.get("movieListResult") or {}
        total = int(block.get("totCnt") or 0)
        items = block.get("movieList") or []
        for m in items:
            if "애니메이션" in (m.get("genreAlt") or "") or m.get("repGenreNm") == "애니메이션":
                films[m["movieCd"]] = {"title": m.get("movieNm") or "", "title_en": m.get("movieNmEn") or "",
                                       "year": int(m["prdtYear"]) if str(m.get("prdtYear") or "").isdigit() else None,
                                       "open": ymd(m.get("openDt")),
                                       "directors": [d.get("peopleNm") for d in m.get("directors") or []]}
        print(f"  일본 영화 목록 {page}쪽 · 지금까지 애니메이션 {len(films):,}편 / 전체 {total:,}편", flush=True)
        if not items or page * 100 >= total:
            break
        page += 1
    save_json("films.json", films)
    return films


def fetch_weeks(key: str, films: dict[str, dict]) -> dict[str, list]:
    """일요일 → [[영화 코드, 누적 관객, 그 주 순위, 집계 기간]] (후보 영화만 남김)"""
    weeks: dict[str, list] = load_json("weeks.json", {})
    today = dt.date.today()
    last_sunday = today - dt.timedelta(days=today.weekday() + 1)
    sunday, todo = FIRST_SUNDAY, []
    while sunday <= last_sunday:
        if sunday.isoformat() not in weeks:
            todo.append(sunday)
        sunday += dt.timedelta(days=7)
    print(f"주간 박스오피스: 받을 주 {len(todo):,}개 (이미 받은 주 {len(weeks):,}개)", flush=True)
    for n, sunday in enumerate(todo, 1):
        result = call("boxoffice/searchWeeklyBoxOfficeList.json",
                      {"targetDt": sunday.strftime("%Y%m%d"), "weekGb": "0", "repNationCd": "F"}, key)
        block = result.get("boxOfficeResult") or {}
        span = block.get("showRange") or ""
        weeks[sunday.isoformat()] = [[r["movieCd"], int(r.get("audiAcc") or 0), int(r.get("rank") or 0), span]
                                     for r in block.get("weeklyBoxOfficeList") or [] if r.get("movieCd") in films]
        if n % 25 == 0 or n == len(todo):
            save_json("weeks.json", weeks)
            print(f"  {n:,}/{len(todo):,}주 · {sunday} · 요청 {calls:,}번", flush=True)
    return weeks


def clean(title: str) -> str:
    return TAIL.sub("", HEAD.sub("", title or "")).strip(" :：-")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="요청 없이 이미 뽑아 둔 숫자로 맞추기만")
    args = parser.parse_args()
    started = time.time()
    settings = load_settings()
    if not args.no_fetch and not settings.kobis_key:
        raise SystemExit(".env 의 KOBIS_API_KEY 가 비어 있음")
    films = load_json("films.json", {}) if args.no_fetch else fetch_films(settings.kobis_key)
    weeks = load_json("weeks.json", {}) if args.no_fetch else fetch_weeks(settings.kobis_key, films)
    best: dict[str, dict] = {}
    for sunday in sorted(weeks):  # 날짜 순서로 봐야 '처음 10위 안에 든 주'를 알 수 있다
        for code, audience, rank, span in weeks[sunday]:
            b = best.setdefault(code, {"audience": 0, "best_rank": 99, "weeks": 0, "last_week": "",
                                       "first_week": audience})
            b["audience"] = max(b["audience"], audience)
            b["best_rank"] = min(b["best_rank"], rank or 99)
            b["weeks"] += 1
            b["last_week"] = max(b["last_week"], span)
    print(f"후보(일본 애니메이션) {len(films):,}편 · 외국 영화 주간 10위 안에 든 영화 {len(best):,}편 · 요청 {calls:,}번")

    with psycopg.connect(settings.dict_conninfo) as conn, conn.cursor() as cur:
        store = Store(cur)
        cur.execute("select entity_id, kind, parent_id from dict.entity where kind in ('work', 'entry')")
        kind_of, parent_of, children = {}, {}, defaultdict(list)
        for eid, kind, parent in cur.fetchall():
            kind_of[eid], parent_of[eid] = kind, parent
            if parent:
                children[parent].append(eid)
        cur.execute("select entity_id, value #>> '{}' from dict.fact where source_id = 'anilist' and field = 'format'")
        fmt = dict(cur.fetchall())
        cur.execute("""select entity_id, (value #>> '{}')::int from dict.fact
                       where source_id = 'anilist' and field = 'start_year'""")
        start = dict(cur.fetchall())
        cur.execute("select entity_id, value #>> '{}' from dict.fact where source_id = 'anilist' and field = 'country'")
        country = dict(cur.fetchall())
        cur.execute("""select a.entity_id, a.alias from dict.alias a join dict.entity e using (entity_id)
                       where e.kind in ('work', 'entry') and not a.is_candidate and a.source_id <> 'kobis'""")
        by_alias: dict[str, set[int]] = defaultdict(set)
        for eid, alias in cur.fetchall():
            by_alias[norm(alias)].add(eid)

        def year_ok(entry: int, film: dict) -> bool:
            y0 = start.get(entry)
            if y0 is None:
                return False
            opened = int(film["open"][:4]) if film.get("open") else None
            return (film.get("year") is not None and abs(film["year"] - y0) <= 1) or (
                opened is not None and 0 <= opened - y0 <= 3)

        def movie_entries(eid: int) -> list[int]:
            """별칭이 붙은 대상 → 그 대상이 가리키는 극장판 시즌들 (작품이면 그 작품의 극장판들)"""
            if kind_of.get(eid) == "entry":
                return [eid] if fmt.get(eid) == "MOVIE" else []
            return [c for c in children.get(eid, []) if fmt.get(c) == "MOVIE"]

        stats: Counter = Counter()
        matched: dict[int, tuple[str, dict]] = {}
        misses: list[tuple[int, str]] = []
        for code, b in sorted(best.items(), key=lambda kv: -kv[1]["audience"]):
            film = films.get(code) or {}
            titles = {t for t in (film.get("title"), clean(film.get("title", "")), film.get("title_en"),
                                  clean(film.get("title_en", ""))) if t and norm(t)}
            found = {m for t in titles for eid in by_alias.get(norm(t), ()) for m in movie_entries(eid)}
            found = {e for e in found if year_ok(e, film) and country.get(e, "JP") == "JP"}
            if len(found) != 1:
                stats["애매함" if found else "없음"] += 1
                misses.append((b["audience"], f"{film.get('title')} ({film.get('open') or film.get('year')})"
                                              + (f" · 후보 {len(found)}" if found else "")))
                continue
            entry = next(iter(found))
            if entry in matched and matched[entry][1]["audience"] >= b["audience"]:
                stats["같은 극장판에 두 기록 (작은 쪽 버림)"] += 1
                continue
            matched[entry] = (code, b)
            stats["맞음"] += 1
        now = dt.datetime.now(dt.UTC)
        for entry, (code, b) in matched.items():
            film = films[code]
            store.fact(entry, "kr_audience", {"audience": b["audience"], "open_date": film.get("open"),
                                              "movie_cd": code, "title_ko": film.get("title"),
                                              "best_rank_foreign": b["best_rank"], "last_week": b["last_week"],
                                              # 11/11 추가: 외국 영화 주간 10위 안에 든 주 수, 처음 든 주까지의 누적 관객
                                              "weeks_in_top10": b["weeks"], "first_week_audience": b["first_week"]},
                       now, SITE, KOBIS)
        upserted, deleted = store.write_facts(KOBIS, FIELDS)
        cur.execute("select entity_id, coalesce(name_ko, name_romaji, name_native) from dict.entity")
        names = dict(cur.fetchall())

    print("맞추기: " + " · ".join(f"{k} {v:,}" for k, v in sorted(stats.items())))
    print(f"사실: 넣거나 고침 {upserted:,} · 지움 {deleted:,}")
    print("한국 관객 수 상위 25 (맞춘 극장판):")
    for entry, (code, b) in sorted(matched.items(), key=lambda kv: -kv[1][1]["audience"])[:25]:
        parent = parent_of.get(entry)
        print(f"  {b['audience']:>10,}명 | {films[code]['title']} ({films[code].get('open')}) → "
              f"{names.get(entry)} / 작품 {names.get(parent)}")
    print("못 맞춘 영화 (관객 많은 순 15):")
    for audience, label in misses[:15]:
        print(f"  {audience:>10,}명 | {label}")
    print(f"걸린 시간 {time.time() - started:.0f}초")


if __name__ == "__main__":
    main()
