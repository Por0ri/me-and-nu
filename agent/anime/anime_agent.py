# # anime_agent v0.1 — 애니 콘텐츠 크리에이팅 에이전트
#
# 키는 `.env` 또는 환경변수 `LLM_KEY`에서 읽는다. 결과는 `agent/out/anime/올림|탈락/`에 쌓인다.
#
# `pip install -r ../requirements.txt`
#
# 유형 다섯을 한 스크립트로 만든다. 유형은 주문 칸 하나다. 마디 · 갈림길 · 상태 칸은 음악 v2.9와 이름이 같다.
#
# | 유형 | 글 모양 | 재료 |
# |---|---|---|
# | 사람 — 성우 · 제작진의 참여작 | 정보 | AniList |
# | 기념일 — 방영 N주년 · 캐릭터 생일 | 정보 | AniList + 위키백과 ko |
# | 감상순서 — 순서 · 본편 연결 · 필러 | 정보 | AniList 관계도 + Jikan 필러 + 용어집 편 |
# | 제작이야기 — 감독 · 원작자 인터뷰 | 심층 | 인터뷰 본문(검색) + 위키백과 |
# | 작품리뷰 — 평론 | 심층 | 리뷰 본문(검색) + 위키백과 평가 절 |
#
# ```
# 주제뽑기 → 재료모으기 → 표기맞추기 → 미리거르기 → 재료판정 ─┬─ 정보형 ──────────────→ 기획자 → 작가 → 교정자 → 형태검사 → 편집국장 → 저장
#                                                       └─ 심층형 → (재료보강) → 관점묶기 ─┘        ↑                         │
#                                                                                                └──── 걸리면 문제 목록만 (세 번) ──┘
# ```
#
# - 정보형은 모델을 부르는 재료판정이 없다. 코드가 재료가 충분한지 본다. 관점묶기를 건너뛴다.
# - 심층형은 음악 v2.9와 같다. 페이지마다 재료판정(모델) → 관점묶기 → 기획자.
# - 표기는 `glossary.json`(라프텔 자막 기준, 손으로 만든 용어집) → AniList 한글 별칭 → 위키백과 ko 순으로 맞춘다.
#   용어집에 없는 이름은 "임시"다. 임시 표기 이름은 글에 못 쓴다. 로마자 · 가나 이름이 본문에 나오면 형태검사에 걸린다.
# - 첫 판은 용어집에 있는 시리즈(블리치 · 진격의 거인 · 나루토 · 장송의 프리렌 · 원피스)만 뽑는다.
# - 편집국장이 걸면 기획자부터 다시 쓴다. 세 번까지다. 올림 · 탈락은 코드가 정한다.

# ## 1. 설정
#
# 아래 값만 고치면 된다.

import os, re, io, json, time, random, zipfile, datetime, pathlib, urllib.parse, sys, html as htmlmod
import requests
from dotenv import load_dotenv

# agent/.env → 이 파일 옆 .env 순서로 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다
HERE = pathlib.Path(__file__).resolve().parent if "__file__" in globals() else pathlib.Path.cwd()   # 노트북에서는 현재 폴더
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")

# ───── 여기서 고친다 ─────────────────────────────────────────────
MODEL       = "openai:gpt-5.6-luna"           # 영화 · 음악 에이전트와 같은 모델
KEY_ENV     = "OPENAI_API_KEY"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

유형표 = {                       # 유형 → 글 모양. 모양이 갈림길과 프롬프트를 정한다
    "사람":       "정보",
    "기념일":     "정보",
    "감상순서":   "정보",
    "제작이야기": "심층",
    "작품리뷰":   "심층",
}
목표편수      = 3      # 올린 글이 이만큼 될 때까지 돈다
시도상한      = 10     # 주제를 이만큼 봤는데 못 채우면 멈춘다
작품당상한    = 2      # 한 번 돌 때 같은 시리즈에서 올리는 글 상한. 원피스가 피드를 덮지 않게
기념일창      = 7      # 오늘 앞뒤 며칠까지 기념일로 볼지
주년들        = (1, 3, 5, 10, 15, 20, 25, 30)
최소참여작    = 2      # 사람 유형. 한국 표기가 있는 참여작이 이만큼 안 되면 그 사람은 버린다
최소순서작품  = 3      # 감상순서 유형. 시리즈 안 애니 작품이 이만큼 안 되면 버린다 (원피스는 편이 대신한다)
PAGE_LIMIT    = 10     # 심층형. 주제 하나에 재료로 읽어볼 페이지 수
후보링크배수  = 5      # robots로 막히는 곳이 많아 검색 후보를 넉넉히 모은다
PER_MEDIA     = 2      # 매체당 남길 글 개수
MIN_PAGES     = 2      # 심층형. 쓸 만한 글이 이만큼 안 모이면 그 주제를 버린다
보강목표      = 3      # 심층형. 이만큼 안 모이면 재료 보강 마디가 한 번 더 찾는다
보강페이지    = 6
동시판정      = 5      # 재료 판정을 한 번에 몇 페이지씩 모델에 보낼지
발췌글자      = 1800   # 기획자에게 주는 원문 발췌. 글마다 이만큼
REWRITE_LIMIT = 3      # 다시 쓰기 상한
PASS_SCORE    = 70     # 편집국장 점수가 이 아래면 다시 쓴다
LLM_CALL_CAP  = 40     # 주제 하나에 허용할 모델 요청 수 (판정 10 + 보강 6 + 한 바퀴 4 × 세 번 + 여유)
ANILIST_GAP   = 2.0    # AniList 요청 사이 간격(초). 분당 30회 제한
JIKAN_GAP     = 1.2    # Jikan 요청 사이 간격(초)
JIKAN_PAGES   = 15     # Jikan 회차 목록을 최대 몇 쪽까지 (100화씩. 원피스 12쪽)
캐시일수      = 7      # AniList · Jikan 응답을 디스크에 이만큼 둔다
표기조회상한  = 30     # 위키백과 ko로 표기를 찾아볼 이름 수 상한 (한 주제)
라프텔조회상한 = 12    # 라프텔로 작품 제목 표기를 찾아볼 작품 수 상한 (한 주제)
필러없어도진행 = True  # Jikan이 죽었을 때 필러 표시 없이 감상순서를 만들지. 본편이 하나뿐인 긴 작품(원피스)은 예외로 멈춘다
CONTACT       = "menu-project@example.com"
# ─────────────────────────────────────────────────────────────

def _secret(name):
    v = os.environ.get(name)
    if not v or not v.strip():
        raise RuntimeError(f"환경변수 '{name}'이 없다. agent/.env 파일에 {name}=... 을 적거나 셸에서 환경변수로 넣는다. (agent/.env.example 참고)")
    return v.strip()

# check · pick · glossary 는 모델을 안 부른다. 키 없이 돌아가게 둔다
_DRY = len(sys.argv) > 1 and sys.argv[1] in ("check", "pick", "glossary")
try:
    os.environ[KEY_ENV] = _secret("LLM_KEY")
    print("키 읽음. 길이:", len(os.environ[KEY_ENV]))
except RuntimeError:
    if not _DRY:
        raise
    os.environ[KEY_ENV] = "dry-run"
    print("키 없음. check · pick · glossary 만 된다")

UA = f"menu-anime-agent/0.1 ( {CONTACT} )"
HEADERS = {"User-Agent": UA}

OUT = HERE.parent / "out" / "anime"
for d in ("올림", "탈락", "_cache"):
    (OUT / d).mkdir(parents=True, exist_ok=True)

import asyncio
try:
    asyncio.get_running_loop()
    import nest_asyncio
    nest_asyncio.apply()
    print("노트북 안이다. nest_asyncio 걸었다")
except RuntimeError:
    pass

def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

오늘 = datetime.date.today()

# ## 2. 용어집
#
# `glossary.json`. 영화 에이전트 셀 3-2와 같은 다섯 칸 `[정식 표기, [틀린 표기], [별칭], 종류, 뜻]`.
# 시리즈는 `anilist_root`에서 관계도를 따라 코드가 묶는다. 표기 기준은 라프텔 자막이다.

GLOSSARY_PATH = HERE / "glossary.json"
GLOSSARIES = {k: v for k, v in json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")).items() if not k.startswith("_")}

def _norm(s):
    return re.sub(r"[\s\-_.'’\"“”:：·,!?()（）\[\]]", "", (s or "").lower())

def _norm_roma(s):
    # 로마자 장음 표기 차이를 지운다. Toushirou = Toshiro
    s = _norm(s)
    if re.fullmatch(r"[a-z0-9]+", s):
        s = re.sub(r"(ou|oo)", "o", s)
        s = re.sub(r"uu", "u", s)
        s = re.sub(r"ii", "i", s)
        s = s.replace("ō", "o").replace("ū", "u")
    return s

def _역색인(g):
    # 정식 · 틀린 · 별칭 표기 → (정식, 종류, 뜻). 짧은 것(2자 미만)은 뺀다
    idx = {}
    for fixed, wrong, alias, kind, meaning in g["항목"]:
        for x in [fixed] + list(wrong) + list(alias):
            k = _norm_roma(x)
            if len(k) >= 2 and k not in idx:
                idx[k] = (fixed, kind, meaning)
    return idx

for _name, _g in GLOSSARIES.items():
    _g["_idx"] = _역색인(_g)

def 용어집_찾기(이름, series=None):
    """이름(어느 표기든) → 정식 표기. 없으면 None."""
    k = _norm_roma(이름)
    if not k:
        return None
    for name, g in GLOSSARIES.items():
        if series and name != series:
            continue
        hit = g["_idx"].get(k)
        if hit:
            return hit[0]
    return None

def 용어집_편들(series, romaji):
    """작품 로마자 제목에 든 편 표기들. 나온 자리 순서다. 시리즈 이름 항목(정식 표기가 시리즈 이름인 것)은 뺀다."""
    g = GLOSSARIES.get(series)
    if not g:
        return []
    low = _norm_roma(romaji)
    hits, 길이 = {}, {}
    for fixed, wrong, alias, kind, meaning in g["항목"]:
        if kind != "편" or fixed == series:
            continue
        for x in wrong + alias:
            xk = _norm_roma(x)
            if len(xk) >= 5 and xk in low:
                pos = low.index(xk)
                if fixed not in hits or pos < hits[fixed]:
                    hits[fixed] = pos
                길이[fixed] = max(길이.get(fixed, 0), len(xk))
    out = sorted(hits, key=lambda f: hits[f])
    # 한쪽이 다른 쪽을 품으면 짧은 것을 뺀다 ("파이널 시즌" ⊂ "파이널 시즌 완결편")
    out = [f for f in out if not any(f != o and f in o for o in out)]
    남음 = len(low) - sum(길이[f] for f in out)              # 맞은 표기를 빼고 제목에 남는 글자 수. 부제가 있으면 크다
    return out, 남음

def 용어집_편목록(series):
    """편 항목에서 화수 범위를 읽는다. [(편 이름, 시작화, 끝화 또는 None, 뜻)]"""
    g = GLOSSARIES.get(series)
    out = []
    for fixed, wrong, alias, kind, meaning in (g["항목"] if g else []):
        if kind != "편":
            continue
        m = re.search(r"(\d+)\s*~\s*(\d+)\s*화", meaning) or re.search(r"(\d+)\s*화부터", meaning)
        if m:
            a = int(m.group(1)); b = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
            out.append((fixed, a, b, meaning))
    out.sort(key=lambda x: x[1])
    return out

def glossary_text(series, kinds=None, with_meaning=True):
    """기획자 · 작가 · 편집국장에게 붙일 표기표."""
    g = GLOSSARIES.get(series)
    if not g:
        return ""
    rows = []
    for fixed, wrong, alias, kind, meaning in g["항목"]:
        if kinds and kind not in kinds:
            continue
        line = f"- {fixed}"
        if alias:
            line += f"  (별칭: {' · '.join(alias)})"
        bad = [w for w in wrong if not re.search(r"[A-Za-z぀-ヿ一-鿿]", w)]     # 한글 틀린 표기만 보여준다
        if bad:
            line += f"  (이렇게 쓰지 않는다: {' · '.join(bad)})"
        if with_meaning:
            line += f"  — {kind}. {meaning}"
        rows.append(line)
    return (f"[{series} 표기표 — 기준: {g['기준']}]\n아래 정식 표기만 쓴다. 괄호 안 표기가 재료에 있으면 정식 표기로 바꾼다.\n" + "\n".join(rows))

def _glossary_bad_forms(series):
    g = GLOSSARIES.get(series)
    if not g:
        return []
    fixed_all = [row[0] for row in g["항목"]]
    pairs = []
    for fixed, wrong, _, _, _ in g["항목"]:
        for o in wrong:
            if re.search(r"[A-Za-z぀-ヿ一-鿿]", o):
                continue                      # 로마자 · 일본어는 따로 잡는다
            if len(o) < 3 or any(o in f for f in fixed_all):
                continue
            pairs.append((o, fixed))
    return pairs

def glossary_check(body, series):
    return [f"표기가 틀렸다: '{w}' → '{r}'" for w, r in _glossary_bad_forms(series) if w in body]

print(f"용어집 {len(GLOSSARIES)}개 시리즈: " + " · ".join(f"{k}({len(v['항목'])})" for k, v in GLOSSARIES.items()))

# ## 3. robots.txt · 요청
#
# 막아둔 곳은 본문을 안 읽는다. robots.txt를 못 가져오면 막힌 것으로 본다. 404면 허용으로 본다.

from urllib.robotparser import RobotFileParser
from concurrent.futures import ThreadPoolExecutor
import threading

_robots = {}

def _host(url):
    return urllib.parse.urlparse(url).netloc.lower().replace("www.", "")

def robots_ok(url):
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
            if r.status_code == 404:
                rp.parse([])
            elif r.status_code == 200 and "<html" not in r.text[:300].lower():
                rp.parse(r.text.splitlines())
            else:
                _robots[base] = None          # 봇 검사 화면이 나오거나 못 읽었다. 막힌 것으로 본다
                return False
        except Exception:
            _robots[base] = None
            return False
        _robots[base] = rp
    rp = _robots[base]
    if rp is None:
        return False
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return False

def crawl_delay(url):
    p = urllib.parse.urlparse(url)
    rp = _robots.get(f"{p.scheme}://{p.netloc}")
    if rp is None:
        return 1.0
    try:
        d = rp.crawl_delay(UA)
        return float(d) if d else 1.0
    except Exception:
        return 1.0

_site_lock, _site_last, _lock_guard = {}, {}, threading.Lock()

def _site_wait(url, gap=None):
    host = _host(url)
    with _lock_guard:
        lk = _site_lock.setdefault(host, threading.Lock())
    with lk:
        want = gap if gap is not None else crawl_delay(url)
        wait = want - (time.time() - _site_last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _site_last[host] = time.time()

def fetch(url, timeout=20):
    if not robots_ok(url):
        return None
    _site_wait(url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

def _cache_get(name):
    p = OUT / "_cache" / f"{name}.json"
    if p.exists() and (time.time() - p.stat().st_mtime) < 캐시일수 * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _cache_put(name, data):
    try:
        (OUT / "_cache" / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

print("요청 준비 끝")

# ## 4. AniList
#
# 키 없는 공개 GraphQL이다. 분당 30회. 응답은 디스크에 캐시한다.
# 작품 · 관계도 · 캐릭터(생일 · 성우) · 제작진 · 한 사람의 참여작을 여기서 가져온다. 한국 제목은 `synonyms`에 있을 때가 있다.

AL_URL = "https://graphql.anilist.co"

def al(query, variables=None, tries=2):
    for t in range(tries):
        _site_wait(AL_URL, gap=ANILIST_GAP)
        try:
            r = requests.post(AL_URL, json={"query": query, "variables": variables or {}},
                              headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"}, timeout=30)
        except Exception as e:
            print("   AniList 실패:", e)
            return None
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After") or 60)
            print(f"   AniList 429. {wait}초 쉰다")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            print("   AniList", r.status_code, r.text[:120])
            return None
        d = r.json()
        if d.get("errors"):
            print("   AniList 오류:", d["errors"][0].get("message"))
            return None
        return d.get("data")
    return None

_MEDIA_LIGHT = """
id idMal type format status episodes source siteUrl averageScore popularity
title { romaji english native } synonyms
startDate { year month day } endDate { year month day }
studios(isMain: true) { nodes { name } }
relations { edges { relationType node { id idMal type format status episodes title { romaji english native } synonyms startDate { year month day } studios(isMain: true) { nodes { name } } } } }
"""

_MEDIA_FULL = _MEDIA_LIGHT + """
characters(perPage: 25, sort: [ROLE, RELEVANCE]) { edges { role node { id name { full native } dateOfBirth { month day } }
  voiceActors(language: JAPANESE, sort: RELEVANCE) { id name { full native } } } }
staff(perPage: 25, sort: RELEVANCE) { edges { role node { id name { full native } } } }
"""

def _date(d):
    if not d or not d.get("year"):
        return None
    return {"y": d["year"], "m": d.get("month"), "d": d.get("day")}

def _한글별칭(syns, title=None):
    for s in (syns or []):
        if re.search(r"[가-힣]", s):
            return s.strip()
    return None

def _작품(m):
    t = m.get("title") or {}
    return {"id": m["id"], "idMal": m.get("idMal"), "mtype": m.get("type"), "format": m.get("format"), "status": m.get("status"),
            "romaji": t.get("romaji"), "english": t.get("english"), "native": t.get("native"),
            "synonyms": m.get("synonyms") or [], "한글별칭": _한글별칭(m.get("synonyms")),
            "episodes": m.get("episodes"), "source": m.get("source"), "siteUrl": m.get("siteUrl"),
            "score": m.get("averageScore"), "popularity": m.get("popularity"),
            "start": _date(m.get("startDate")), "end": _date(m.get("endDate")),
            "studios": [s["name"] for s in (m.get("studios") or {}).get("nodes", [])],
            "relations": [{"type": e["relationType"], **_작품({**e["node"], "relations": {"edges": []}})}
                          for e in (m.get("relations") or {}).get("edges", [])],
            "characters": [{"id": e["node"]["id"], "full": e["node"]["name"]["full"], "native": e["node"]["name"].get("native"),
                            "role": e.get("role"), "dob": e["node"].get("dateOfBirth") or {},
                            "va": [{"id": v["id"], "full": v["name"]["full"], "native": v["name"].get("native")} for v in (e.get("voiceActors") or [])]}
                           for e in (m.get("characters") or {}).get("edges", [])],
            "staff": [{"id": e["node"]["id"], "full": e["node"]["name"]["full"], "native": e["node"]["name"].get("native"), "role": e.get("role")}
                      for e in (m.get("staff") or {}).get("edges", [])]}

def al_media(mid, full=False):
    key = f"al_media_{mid}_{'full' if full else 'light'}"
    c = _cache_get(key)
    if c:
        return c
    d = al("query($id:Int){ Media(id:$id){ " + (_MEDIA_FULL if full else _MEDIA_LIGHT) + " } }", {"id": mid})
    if not d or not d.get("Media"):
        return None
    w = _작품(d["Media"])
    _cache_put(key, w)
    return w

def al_search(q):
    d = al("query($q:String){ Page(perPage:5){ media(search:$q, type:ANIME, sort:POPULARITY_DESC){ " + _MEDIA_LIGHT + " } } }", {"q": q})
    return [_작품(m) for m in (d or {}).get("Page", {}).get("media", [])]

def al_staff(sid):
    key = f"al_staff_{sid}"
    c = _cache_get(key)
    if c:
        return c
    d = al("""query($id:Int){ Staff(id:$id){ id name { full native } primaryOccupations dateOfBirth { year month day } siteUrl
      characterMedia(perPage: 25, sort: POPULARITY_DESC) { edges { characterRole characters { id name { full native } }
        node { id title { romaji english native } synonyms format startDate { year month } popularity studios(isMain: true) { nodes { name } } } } }
      staffMedia(perPage: 25, sort: POPULARITY_DESC, type: ANIME) { edges { staffRole node { id title { romaji english native } synonyms format startDate { year month } popularity studios(isMain: true) { nodes { name } } } } }
    } }""", {"id": sid})
    s = (d or {}).get("Staff")
    if not s:
        return None
    out = {"id": s["id"], "full": s["name"]["full"], "native": s["name"].get("native"), "직업": s.get("primaryOccupations") or [],
           "dob": s.get("dateOfBirth") or {}, "siteUrl": s.get("siteUrl"),
           "배역": [{"작품id": e["node"]["id"], "romaji": e["node"]["title"]["romaji"], "native": e["node"]["title"].get("native"),
                    "한글별칭": _한글별칭(e["node"].get("synonyms")), "format": e["node"].get("format"), "year": (e["node"].get("startDate") or {}).get("year"),
                    "month": (e["node"].get("startDate") or {}).get("month"), "studios": [x["name"] for x in (e["node"].get("studios") or {}).get("nodes", [])],
                    "popularity": e["node"].get("popularity"), "역할": e.get("characterRole"),
                    "캐릭터": [{"id": c["id"], "full": c["name"]["full"], "native": c["name"].get("native")} for c in (e.get("characters") or [])]}
                   for e in (s.get("characterMedia") or {}).get("edges", [])],
           "참여": [{"작품id": e["node"]["id"], "romaji": e["node"]["title"]["romaji"], "native": e["node"]["title"].get("native"),
                    "한글별칭": _한글별칭(e["node"].get("synonyms")), "format": e["node"].get("format"), "year": (e["node"].get("startDate") or {}).get("year"),
                    "month": (e["node"].get("startDate") or {}).get("month"), "studios": [x["name"] for x in (e["node"].get("studios") or {}).get("nodes", [])],
                    "popularity": e["node"].get("popularity"), "역할": e.get("staffRole")}
                   for e in (s.get("staffMedia") or {}).get("edges", [])]}
    _cache_put(key, out)
    return out

본편관계 = ("SEQUEL", "PREQUEL")
시리즈관계 = 본편관계 + ("SIDE_STORY", "SPIN_OFF", "ALTERNATIVE", "SUMMARY", "PARENT", "COMPILATION")   # OTHER · CHARACTER(크로스오버)는 뺀다

def al_series(root_id, 최대조회=12):
    """대표 작품에서 관계도를 따라 시리즈를 묶는다. 전편 · 후편 사슬은 더 파고, 외전 · 극장판은 사슬 마디에서 줍는다."""
    seen, 작품들, chain = {}, [], set()
    queue, fetched = [root_id], 0
    while queue and fetched < 최대조회:
        mid = queue.pop(0)
        if mid in seen and seen[mid].get("_fetched"):
            continue
        w = al_media(mid)
        fetched += 1
        if not w:
            continue
        w["_fetched"] = True
        seen[mid] = w
        chain.add(mid)
        for r in w["relations"]:
            if r["mtype"] != "ANIME":
                if r["type"] in ("ADAPTATION", "SOURCE"):
                    cand = {"romaji": r["romaji"], "native": r["native"], "format": r["format"], "year": (r["start"] or {}).get("y")}
                    if not w.get("원작") or (r["format"] == w.get("source") and w["원작"].get("format") != w.get("source")):
                        w["원작"] = cand
                continue
            if r["type"] not in 시리즈관계:
                continue
            if r["id"] not in seen:
                seen[r["id"]] = {**r, "_fetched": False}
            if r["type"] in 본편관계 and r["id"] not in chain and r["id"] not in queue:
                queue.append(r["id"])
    for mid, w in seen.items():
        w["본편사슬"] = mid in chain
        작품들.append(w)
    작품들.sort(key=lambda w: ((w.get("start") or {}).get("y") or 9999, (w.get("start") or {}).get("m") or 99, (w.get("start") or {}).get("d") or 99))
    return 작품들

print("AniList 준비 끝")

# ## 5. Jikan (MyAnimeList)
#
# 키 없는 공개 API. 회차 목록에 필러 · 총집편 표시가 있다. 감상순서 유형이 쓴다. 죽어 있으면 빈 목록을 돌려준다.

JIKAN = "https://api.jikan.moe/v4"

def jikan(path, params=None, tries=3):
    for t in range(tries):
        _site_wait(JIKAN, gap=JIKAN_GAP)
        try:
            r = requests.get(JIKAN + path, params=params, headers=HEADERS, timeout=20)
        except Exception:
            return None
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (t + 1))
            continue
        return None
    return None

def jikan_episodes(mal_id):
    """[{번호, 제목, 방영일, 필러, 총집편}]. 100화씩 쪽을 넘긴다."""
    key = f"jikan_eps_{mal_id}"
    c = _cache_get(key)
    if c is not None:
        return c
    out, page = [], 1
    while page <= JIKAN_PAGES:
        d = jikan(f"/anime/{mal_id}/episodes", {"page": page})
        if not d or (page == 1 and not d.get("data")):
            return None                        # 죽었거나 빈 응답이다. 캐시에 안 남긴다
        for e in d.get("data", []):
            out.append({"번호": e.get("mal_id"), "제목": e.get("title"), "방영일": (e.get("aired") or "")[:10],
                        "필러": bool(e.get("filler")), "총집편": bool(e.get("recap"))})
        if not (d.get("pagination") or {}).get("has_next_page"):
            break
        page += 1
    _cache_put(key, out)
    return out

def 구간묶기(nums):
    """[1,2,3,7,8] → ['1~3화', '7~8화']"""
    nums = sorted(set(n for n in nums if isinstance(n, int)))
    out, s, p = [], None, None
    for n in nums + [None]:
        if s is None:
            s = p = n; continue
        if n is not None and n == p + 1:
            p = n; continue
        out.append(f"{s}화" if s == p else f"{s}~{p}화")
        s = p = n
    return out

print("Jikan 준비 끝")

# ## 6. 라프텔
#
# 라프텔이 자기 화면을 그리는 데 쓰는 공개 JSON을 읽는다. 화면을 긁지 않는다. robots.txt는 전부 허용이다.
# 한국 제목 · 한국에서 볼 수 있는지 · 줄거리(한국어)를 가져온다. 표기 기준이 라프텔 자막이라 제목은 여기서 먼저 본다.

LAFTEL = "https://api.laftel.net/api"

def laftel_search(keyword, n=10):
    if not robots_ok(LAFTEL + "/"):
        return []
    _site_wait(LAFTEL)
    try:
        r = requests.get(LAFTEL + "/search/v1/keyword/", params={"keyword": keyword, "offset": 0, "size": n},
                         headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("results", []):
            name = it.get("name") or ""
            clean = re.sub(r"\s*\[.*?판권 만료작\]\s*", "", re.sub(r"^\((자막|더빙)\)\s*", "", name)).strip()
            out.append({"id": it["id"], "이름": clean, "원래이름": name, "더빙": bool(it.get("is_dubbed")),
                        "장르": it.get("genres") or [], "성인": bool(it.get("is_adult")), "링크": f"https://laftel.net/item/{it['id']}"})
        return out
    except Exception:
        return []

def laftel_item(item_id):
    _site_wait(LAFTEL)
    try:
        r = requests.get(f"{LAFTEL}/items/v2/{item_id}/", headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        return {"id": d["id"], "이름": re.sub(r"\s*\[.*?판권 만료작\]\s*", "", re.sub(r"^\((자막|더빙)\)\s*", "", d.get("name") or "")).strip(), "줄거리": (d.get("content") or "").strip(),
                "제작사": d.get("production") or "", "방영": d.get("air_year_quarter") or "", "매체": d.get("medium") or "",
                "링크": f"https://laftel.net/item/{d['id']}"}
    except Exception:
        return None

제작사표 = {   # 라프텔 한국 표기 ↔ AniList 영어. 용어집 제작진 항목이 먼저다
    "본즈": "Bones", "교토 애니메이션": "Kyoto Animation", "유포테이블": "ufotable", "프로덕션 I.G": "Production I.G", "선라이즈": "Sunrise",
    "제이씨스태프": "J.C. Staff", "J.C.Staff": "J.C. Staff", "A-1 픽쳐스": "A-1 Pictures", "클로버웍스": "CloverWorks", "가이낙스": "Gainax",
    "트리거": "Trigger", "샤프트": "Shaft", "P.A.WORKS": "P.A. Works", "실버링크": "Silver Link", "동화공방": "Doga Kobo", "라이덴 필름": "Lerche",
    "화이트 폭스": "White Fox", "데이비드 프로덕션": "David Production", "OLM": "OLM", "TMS 엔터테인먼트": "TMS Entertainment", "스튜디오 딘": "Studio Deen",
    "브레인즈 베이스": "Brain's Base", "사테라이트": "Satelight", "킨마 시트러스": "Kinema Citrus", "라이덴필름": "Lerche", "쇼가쿠칸 뮤직&디지털": "",
    "8bit": "8bit", "스튜디오 카페": "Studio Kafka", "고노": "Gonzo", "곤조": "Gonzo", "매드하우스": "Madhouse", "토에이 애니메이션": "Toei Animation",
    "스튜디오 피에로": "Studio Pierrot", "위트 스튜디오": "Wit Studio", "마파": "MAPPA", "CygamesPictures": "CygamesPictures", "사이언스 SARU": "Science SARU",
}

def 같은제작사(ko, en):
    """라프텔 제작사(한국 표기)와 AniList 제작사(영어)가 같은 곳인지."""
    if not ko or not en:
        return False
    a, b = _norm(ko), _norm(en)
    if a == b or a in b or b in a:
        return True
    g1, g2 = 용어집_찾기(ko), 용어집_찾기(en)
    if g1 and g1 == g2:
        return True
    for k, v in 제작사표.items():
        if _norm(k) == a and _norm(v) == b:
            return True
    return False

def 라프텔_표기(w):
    """영문 · 로마자 제목으로 라프텔을 찾고, 방영 연도 · 분기 · 제작사가 AniList와 맞을 때만 그 한국 제목을 돌려준다."""
    y = (w.get("start") or {}).get("y")
    if not y:
        return None
    key = f"laftel_q_{w.get('id')}"
    c = _cache_get(key)
    if c is not None:
        return c or None
    m = (w.get("start") or {}).get("m")
    q = (m - 1) // 3 + 1 if m else None
    fmt = w.get("format")
    found = None
    for 검색어 in dict.fromkeys(x for x in (w.get("english"), w.get("romaji")) if x):
        for it in laftel_search(검색어, n=4):
            if it["성인"] and fmt != "MOVIE":
                pass
            d = laftel_item(it["id"])
            if not d:
                continue
            ym = re.match(r"(\d{4})년\s*(\d)?", d["방영"] or "")
            if not ym or int(ym.group(1)) != y:
                continue
            if q and ym.group(2) and abs(int(ym.group(2)) - q) > 1:
                continue
            매체맞음 = (d["매체"] == "TVA" and fmt in ("TV", "TV_SHORT", "ONA")) or (d["매체"] == "극장판" and fmt == "MOVIE") or (d["매체"] not in ("TVA", "극장판"))
            # 제작사가 양쪽에 다 있어야 하고 같아야 한다. 라프텔 검색은 없는 제목에도 엉뚱한 것을 돌려줘서 연도만으로는 못 믿는다
            제작사맞음 = any(같은제작사(d["제작사"], s_) for s_ in (w.get("studios") or []))
            if 매체맞음 and 제작사맞음:
                found = d["이름"]; break
        if found:
            break
    _cache_put(key, found or "")
    return found

def 라프텔_시리즈(series):
    """시리즈 이름으로 라프텔에 있는 항목들. 한국에서 볼 수 있는 것 목록이 된다."""
    key = f"laftel_{_norm(series)}"
    c = _cache_get(key)
    if c is not None:
        return c
    items = [x for x in laftel_search(series, n=20) if not x["성인"]]
    _cache_put(key, items)
    return items

print("라프텔 준비 끝")

# ## 7. 위키백과
#
# ko는 표기 · 맥락 · 평가 절. en은 작품리뷰의 평가 절(막힌 매체의 평이 인용돼 있다).

평가절제목 = ["평가", "반응", "비평", "평론", "Reception", "Critical reception", "Critical response", "Reviews"]
등장인물절제목 = ["등장인물", "등장 인물", "주요 인물", "캐릭터"]
애니낱말 = re.compile(r"(애니|만화|성우|캐릭터|등장인물|감독|작가|작화|제작|anime|manga|voice)", re.I)

def _wiki_search(q, lang, n=5):
    try:
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                         params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n},
                         headers=HEADERS, timeout=15).json()
        return [(h["title"], re.sub(r"<.*?>", "", htmlmod.unescape(h.get("snippet") or ""))) for h in r.get("query", {}).get("search", [])]
    except Exception:
        return []

def _wiki_extract(lang, title, n=6000):
    try:
        p = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={"action": "query", "prop": "extracts", "explaintext": 1,
                         "titles": title, "format": "json", "redirects": 1}, headers=HEADERS, timeout=15).json()
        page = next(iter(p["query"]["pages"].values()))
        text = (page.get("extract") or "").strip()
        if len(text) > 200:
            return {"제목": page.get("title") or title, "링크": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(page.get('title') or title)}", "본문": text[:n]}
    except Exception:
        pass
    return None

def _wiki_section(lang, title, keys, n=8000):
    try:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        secs = requests.get(api, params={"action": "parse", "page": title, "prop": "sections", "format": "json", "redirects": 1}, headers=HEADERS, timeout=15).json()
        idx = None
        for sec in secs.get("parse", {}).get("sections", []):
            if any(k.lower() in sec["line"].lower() for k in keys):
                idx = sec["index"]; break
        if idx is None:
            return None
        html = requests.get(api, params={"action": "parse", "page": title, "section": idx, "prop": "text", "format": "json", "redirects": 1},
                            headers=HEADERS, timeout=15).json()["parse"]["text"]["*"]
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["table", "sup", "style"]):
            t.decompose()
        text = soup.get_text("\n", strip=True)
        return text[:n] if len(text) > 100 else None
    except Exception:
        return None

def 위키_작품문서(작품, series=None):
    """ko 위키백과 작품 문서. 한국 제목 → 시리즈 이름 → 로마자 순으로 찾는다."""
    for q in [작품.get("한국제목"), series, 작품.get("romaji")]:
        if not q:
            continue
        for title, snip in _wiki_search(q, "ko", 5):
            if 애니낱말.search(snip) or 애니낱말.search(title):
                return _wiki_extract("ko", title)
    return None

def 위키_평가절(작품, series=None, lang="ko"):
    q = 작품.get("한국제목") or series if lang == "ko" else (작품.get("english") or 작품.get("romaji"))
    if not q:
        return None
    for title, snip in _wiki_search(q, lang, 4):
        text = _wiki_section(lang, title, 평가절제목)
        if text:
            return {"url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}", "본문": text, "제목": f"{title} — 평가 절 (위키백과)", "매체": "위키백과", "막힘": False}
    return None

사람낱말 = re.compile(r"(성우|감독|만화가|작가|애니메이터|각본|작곡|연출|배우|프로듀서|일러스트)")

def 위키_표기(native, hint_kind="인물"):
    """일본어 이름으로 ko 위키백과 인물 문서를 찾아 한글 제목을 임시 표기로 쓴다. 목록 · 작품 문서는 안 받는다. 없으면 None."""
    if not native:
        return None
    for title, snip in _wiki_search(native, "ko", 3):
        t = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
        if not re.search(r"[가-힣]", t) or len(t) > 10:
            continue
        if re.search(r"(목록|등장인물|시리즈|애니메이션|만화|의 )", t):
            continue
        if 사람낱말.search(snip) or 사람낱말.search(title):
            return t
    return None

print("위키백과 준비 끝")

# ## 8. 검색 · 본문 읽기 — 심층형
#
# 인터뷰 · 리뷰 링크는 DuckDuckGo로 찾는다. 차단 도메인만 빼고 나머지는 읽는다. 쓸지 버릴지는 재료판정이 정한다.

차단도메인 = [
    "namu.wiki", "namu.moe", "thewiki.kr", "fandom.com", "wikia.com", "atwiki.jp", "wikiwiki.jp", "seesaawiki.jp", "dic.pixiv.net", "dic.nicovideo.jp",
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com", "tistory.com", "brunch.co.kr", "velog.io", "wordpress.com", "blogspot.com",
    "dcinside.com", "fmkorea.com", "theqoo.net", "ruliweb.com", "clien.net", "instiz.net", "pann.nate.com", "inven.co.kr", "arca.live",
    "reddit.com", "quora.com", "tumblr.com", "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com",
    "laftel.net", "netflix.com", "crunchyroll.com/watch", "myanimelist.net", "anilist.co", "kitsu.io", "anime-planet.com", "imdb.com",
    "amazon.com", "coupang.com", "aladin.co.kr", "yes24.com", "pinterest.com", "wikipedia.org",
]
매체이름표 = {
    "animenewsnetwork.com": "Anime News Network", "animeanime.jp": "아니메! 아니메!", "crunchyroll.com": "Crunchyroll",
    "animatetimes.com": "애니메이트 타임즈", "febri.jp": "Febri", "cinra.net": "CINRA", "realsound.jp": "Real Sound",
    "aniplustv.com": "애니플러스", "cine21.com": "씨네21", "polygon.com": "Polygon", "ign.com": "IGN", "theguardian.com": "The Guardian",
    "cbr.com": "CBR", "kotaku.com": "Kotaku", "vulture.com": "Vulture", "animefeminist.com": "Anime Feminist", "otaquest.com": "OTAQUEST",
}

def 차단됐나(url):
    h = _host(url); path = urllib.parse.urlparse(url).path
    return any(h == d or h.endswith("." + d) or (h + path).startswith(d) for d in 차단도메인)

def 매체이름(url):
    h = _host(url)
    for d, n in 매체이름표.items():
        if h == d or h.endswith("." + d):
            return n
    return "한 매체"

def 검색(q, n=15):
    try:
        from ddgs import DDGS
        with DDGS() as d:
            return [r["href"] for r in d.text(q, max_results=n) if r.get("href")]
    except Exception as e:
        if "No results" not in str(e):
            print("   검색 실패:", e)
        return []

def 검색말(주제):
    w, 유형 = 주제["작품"], 주제["유형"]
    ko, ro, en, ja = w.get("한국제목"), w.get("romaji"), w.get("english"), w.get("native")
    말 = []
    if 유형 == "제작이야기":
        사람들 = 주제.get("제작진") or []
        for p in 사람들[:2]:
            if p.get("native") and ja:
                말.append(f"{p['native']} インタビュー {ja}")
            if p.get("full") and (en or ro):
                말.append(f"{p['full']} interview {en or ro}")
        말 += [f"{ko} 감독 인터뷰", f"{ko} 제작 비화", f"{en or ro} anime interview director", f"{ja} 監督 インタビュー 制作"]
    else:
        말 += [f"{ko} 애니 리뷰", f"{ko} 애니 평론", f"{en or ro} anime review", f"site:animenewsnetwork.com {ro} review", f"{ja} アニメ レビュー 評価"]
    return [x for x in dict.fromkeys(말) if x and "None" not in x]

def collect_links(주제):
    links = []
    for q in 검색말(주제):
        links += 검색(q, n=12)
    seen, out = set(), []
    for u in links:
        u = u.split("#")[0]
        if u in seen or 차단됐나(u):
            continue
        seen.add(u); out.append(u)
    return out[: PAGE_LIMIT * 후보링크배수]

def fetch_nowait(url, timeout=15):
    _site_wait(url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

def robots_batch(links, workers=10):
    sites = list(dict.fromkeys(f"{urllib.parse.urlparse(u).scheme}://{urllib.parse.urlparse(u).netloc}" for u in links))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda b: robots_ok(b + "/"), sites))
    allowed = [u for u in links if robots_ok(u)]
    return allowed, [u for u in links if u not in allowed]

def _parse_page(url, html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    site = None
    tag = soup.find("meta", attrs={"property": "og:site_name"}) or soup.find("meta", attrs={"name": "application-name"})
    if tag and tag.get("content"):
        site = re.split(r"\s+[-|–—:]\s+", htmlmod.unescape(tag["content"]).strip())[0].strip()[:24]
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    body = soup.select_one("article") or soup.select_one("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    이름 = 매체이름(url)
    if 이름 == "한 매체" and site and not re.search(r"^(home|blog|news)$", site, re.I):
        이름 = site
    return {"url": url, "막힘": False, "본문": text[:12000], "제목": title, "매체": 이름}

def read_pages(links, 한도, workers=10):
    allowed, blocked = robots_batch(links)
    pages = []
    for i in range(0, len(allowed), workers):
        if len(pages) >= 한도:
            break
        chunk = allowed[i:i + workers]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            htmls = list(ex.map(fetch_nowait, chunk))
        for u, h in zip(chunk, htmls):
            if h:
                pages.append(_parse_page(u, h))
    return pages[:max(0, 한도)], blocked

print("검색 · 본문 읽기 준비 끝")

# ## 9. 표기맞추기 (코드)
#
# 이름마다 한국 표기를 정한다. 용어집 → AniList 한글 별칭 → 위키백과 ko 순이다. 모델을 안 부른다.
# 결과는 용어표 `{원어: {"표기": ..., "출처": 용어집|AniList|라프텔|위키|없음}}`. 출처가 위키면 임시다. 임시 · 없음은 글에 못 쓴다.

class 표기표:
    def __init__(self, series):
        self.series = series
        self.표 = {}
        self.조회 = 0
        self.라프텔조회 = 0

    def _put(self, 원어, 표기, 출처):
        if 원어 and 원어 not in self.표:
            self.표[원어] = {"표기": 표기, "출처": 출처}
        return self.표.get(원어)

    def 사람(self, full, native=None):
        """성우 · 제작진 · 캐릭터 이름. 용어집 → 위키백과 ko."""
        if full in self.표:
            return self.표[full]
        hit = 용어집_찾기(full, self.series) or 용어집_찾기(native or "", self.series) or 용어집_찾기(full)
        if hit:
            return self._put(full, hit, "용어집")
        if native and self.조회 < 표기조회상한:
            self.조회 += 1
            w = 위키_표기(native)
            if w:
                return self._put(full, w, "위키")
        return self._put(full, None, "없음")

    def 작품(self, w):
        """작품 제목. 용어집 편 → AniList 한글 별칭 → (본편 대표작만) 시리즈 이름 → 없음. 다른 시리즈 작품이면 그 시리즈 용어집으로 본다."""
        key = w.get("romaji") or str(w.get("id"))
        if key in self.표:
            return self.표[key]
        owner = self.series
        for name, g in GLOSSARIES.items():
            if w.get("id") == g.get("anilist_root") or any(len(_norm_roma(k)) >= 4 and _norm_roma(k) in _norm_roma(key) for k in g.get("title_keys", [])):
                owner = name; break
        편들, 남음 = 용어집_편들(owner, key) if owner else ([], 0)
        if 편들 and w.get("format") not in ("TV", "ONA", "TV_SHORT") and 남음 > 3:
            편들 = []      # 극장판 · OVA · 스페셜은 시즌 이름이 제목에 끼어 있어도 그 시즌이 아니다. 부제가 남으면 안 믿는다
        if 편들:
            이름 = " ".join(편들)
            if owner not in 이름:
                이름 = f"{owner} {이름}"
            m = re.search(r"part\s*(\d)", key, re.I)
            if m and "파트" not in 이름:
                이름 += f" 파트 {m.group(1)}"
            return self._put(key, 이름, "용어집")
        g = GLOSSARIES.get(owner) or {}
        if w.get("id") == g.get("anilist_root") or _norm_roma(key) in {_norm_roma(k) for k in g.get("title_keys", [])}:
            return self._put(key, owner, "용어집")
        if w.get("한글별칭"):
            return self._put(key, w["한글별칭"], "AniList")
        if w.get("id") and self.라프텔조회 < 라프텔조회상한 and w.get("format") in ("TV", "TV_SHORT", "ONA", "MOVIE"):
            self.라프텔조회 += 1
            이름 = 라프텔_표기(w)
            if 이름:
                return self._put(key, 이름, "라프텔")
        return self._put(key, None, "없음")

    def 확정(self):
        return {k: v for k, v in self.표.items() if v["표기"] and v["출처"] != "위키"}

    def 임시(self):
        return {k: v for k, v in self.표.items() if v["표기"] and v["출처"] == "위키"}

    def 없음(self):
        return [k for k, v in self.표.items() if not v["표기"]]

    def text(self, 임시포함=False):
        줄 = [f"- {k} → {v['표기']}  [{v['출처']}]" for k, v in self.표.items() if v["표기"] and v["출처"] != "위키"]
        임 = [f"- {k} → {v['표기']}  [위키 · 임시. 글에 안 쓴다]" for k, v in self.표.items() if v["표기"] and v["출처"] == "위키"] if 임시포함 else []
        없 = [f"- {k}  [표기 없음. 글에 안 쓴다]" for k in self.없음()]
        return "\n".join(줄 + 임 + 없)

print("표기맞추기 준비 끝")

# ## 10. 주제 후보 — 뽑기
#
# 후보는 용어집에 있는 시리즈에서만 나온다. 유형마다 후보를 만드는 법이 다르다. 결과 모양은 같다.
# `{"유형", "시리즈", "작품", "사람"?, "기념일"?, "한줄", "src"}`

_시리즈캐시 = {}

def 시리즈(name):
    if name in _시리즈캐시:
        return _시리즈캐시[name]
    g = GLOSSARIES[name]
    작품들 = al_series(g["anilist_root"])
    root = next((w for w in 작품들 if w["id"] == g["anilist_root"]), None)
    s = {"이름": name, "root": root, "작품들": 작품들, "라프텔": 라프텔_시리즈(name)}
    _시리즈캐시[name] = s
    return s

def _애니만(작품들):
    return [w for w in 작품들 if w.get("mtype") == "ANIME" and w.get("format") not in ("MUSIC",) and (w.get("start") or {}).get("y")
            and not re.search(r"\bPV\b|Preview|Teaser|Trailer", w.get("romaji") or "")]

def 후보_감상순서():
    out = []
    for name in GLOSSARIES:
        s = 시리즈(name)
        애니 = _애니만(s["작품들"])
        편 = 용어집_편목록(name)
        if len(애니) >= 최소순서작품 or len(편) >= 3:
            out.append({"유형": "감상순서", "시리즈": name, "작품": s["root"], "한줄": f"{name} 감상 순서", "src": "관계도"})
    return out

def 후보_사람():
    out = []
    for name in GLOSSARIES:
        s = 시리즈(name)
        root = al_media(GLOSSARIES[name]["anilist_root"], full=True)
        if not root:
            continue
        seen = set()
        def 표기있나(p):
            return bool(용어집_찾기(p["full"], name) or 용어집_찾기(p.get("native") or "", name))
        for c in root["characters"]:
            for v in c["va"]:
                if v["id"] in seen or not 표기있나(v):
                    continue
                seen.add(v["id"])
                배역 = 용어집_찾기(c["full"], name) or 용어집_찾기(c.get("native") or "", name) or c["full"]
                out.append({"유형": "사람", "시리즈": name, "작품": s["root"], "사람": {**v, "역할": "성우", "배역": c["full"], "배역native": c.get("native")},
                            "한줄": f"{name} {배역} 성우 {용어집_찾기(v['full'], name) or 용어집_찾기(v.get('native') or '', name)}", "src": "AniList 캐릭터"})
        for st in root["staff"]:
            if st["id"] in seen or not 표기있나(st):
                continue
            seen.add(st["id"])
            out.append({"유형": "사람", "시리즈": name, "작품": s["root"], "사람": {**st, "역할": 역할표기(st.get("role"))},
                        "한줄": f"{name} {역할표기(st.get('role'))} {용어집_찾기(st['full'], name) or 용어집_찾기(st.get('native') or '', name)}", "src": "AniList 제작진"})
    return out

def _며칠차(m, d):
    if not m or not d:
        return None
    try:
        t = datetime.date(오늘.year, m, d)
    except ValueError:
        return None
    diff = (t - 오늘).days
    for alt in (-1, 1):
        try:
            t2 = datetime.date(오늘.year + alt, m, d)
            if abs((t2 - 오늘).days) < abs(diff):
                diff = (t2 - 오늘).days
        except ValueError:
            pass
    return diff

def 후보_기념일(창=기념일창):
    out = []
    for name in GLOSSARIES:
        s = 시리즈(name)
        for w in _애니만(s["작품들"]):
            st = w.get("start") or {}
            diff = _며칠차(st.get("m"), st.get("d"))
            if diff is None or abs(diff) > 창:
                continue
            년 = (오늘 + datetime.timedelta(days=diff)).year      # 해가 바뀌는 자리(12월 말 ↔ 1월 초)를 맞춘다
            n = 년 - st["y"]
            if n in 주년들:
                이름 = 표기표(name).작품(w)["표기"] or w["romaji"]
                out.append({"유형": "기념일", "시리즈": name, "작품": w, "기념일": {"종류": "방영", "n": n, "날": f"{st['y']}-{st['m']:02d}-{st['d']:02d}", "며칠": diff},
                            "한줄": f"{이름} 방영 {n}주년", "src": "AniList 방영일"})
        root = al_media(GLOSSARIES[name]["anilist_root"], full=True)
        for c in (root or {}).get("characters", []):
            dob = c.get("dob") or {}
            diff = _며칠차(dob.get("month"), dob.get("day"))
            if diff is None or abs(diff) > 창:
                continue
            표기 = 용어집_찾기(c["full"], name) or 용어집_찾기(c.get("native") or "", name)
            if not 표기:
                continue                                   # 용어집에 없는 캐릭터의 생일은 안 뽑는다
            out.append({"유형": "기념일", "시리즈": name, "작품": s["root"], "기념일": {"종류": "생일", "캐릭터": c, "날": f"{dob['month']:02d}-{dob['day']:02d}", "며칠": diff},
                        "한줄": f"{name} {표기} 생일", "src": "AniList 생일"})
    return out

def 후보_심층(유형):
    out = []
    for name in GLOSSARIES:
        s = 시리즈(name)
        표 = 표기표(name)
        tv = [w for w in _애니만(s["작품들"]) if w.get("format") == "TV" and w["본편사슬"] and w.get("status") != "NOT_YET_RELEASED" and 표.작품(w)["표기"] and 표.작품(w)["출처"] != "위키"]
        tv.sort(key=lambda w: -((w.get("start") or {}).get("y") or 0))
        for w in tv[:3]:
            full = al_media(w["id"], full=True) or w
            제작진 = [p for p in full.get("staff", []) if re.search(r"Director|Original Creator|Series Composition|Script|Character Design", p.get("role") or "")]
            이름 = 표기표(name).작품(w)["표기"] or w["romaji"]
            out.append({"유형": 유형, "시리즈": name, "작품": full, "제작진": 제작진[:4],
                        "한줄": f"{이름} {'제작 이야기' if 유형 == '제작이야기' else '리뷰'}", "src": "관계도"})
    return out

def 주제_후보목록(유형):
    if 유형 == "감상순서":
        c = 후보_감상순서()
    elif 유형 == "사람":
        c = 후보_사람()
    elif 유형 == "기념일":
        c = 후보_기념일()
    else:
        c = 후보_심층(유형)
    random.shuffle(c)
    return c

_쓴주제 = set()
_올린시리즈 = {}

def _주제키(c):
    p = c.get("사람") or {}
    k = c.get("기념일") or {}
    return (c["유형"], c["시리즈"], (c.get("작품") or {}).get("id"), p.get("id"), k.get("종류"), k.get("날"), (k.get("캐릭터") or {}).get("id"))

print("주제 후보 준비 끝")

# ## 11. 재료 — 정보형
#
# 사람 · 기념일 · 감상순서는 남의 글이 없다. AniList · Jikan · 위키백과에서 사실을 모아 `사실표`(글)를 만든다.
# 사실마다 출처를 단다. 작가는 이 밖의 사실을 못 쓴다.

역할표 = [
    (r"^Director$|Chief Director", "감독"), (r"Original Creator|Original Story", "원작"), (r"Series Composition", "시리즈 구성"),
    (r"Screenplay|Script", "각본"), (r"Chief Animation Director", "총작화감독"), (r"Animation Director", "작화감독"),
    (r"Character Design", "캐릭터 디자인"), (r"^Music$|Music Composition|Composer", "음악"), (r"Theme Song Performance", "주제가 노래"),
    (r"Theme Song (Composition|Arrangement|Lyrics)", "주제가"), (r"Sound Director", "음향감독"), (r"Art Director", "미술감독"),
    (r"Episode Director", "연출"), (r"Storyboard", "콘티"), (r"Key Animation", "원화"), (r"Assistant Director", "조감독"),
    (r"Producer", "프로듀서"), (r"Editing", "편집"), (r"Color Design", "색채 설계"), (r"Director of Photography", "촬영감독"),
    (r"CG|3D", "CG"), (r"Background Art", "배경"), (r"Design", "디자인"), (r"Animation", "애니메이션"),
]

def 역할표기(role):
    """AniList 영어 역할 → 한국어. 모르면 '참여'."""
    if not role:
        return "참여"
    base = re.sub(r"\s*\(.*?\)\s*", "", role).strip()
    for pat, ko in 역할표:
        if re.search(pat, base, re.I):
            return ko
    return "참여"

배역표기 = {"MAIN": "주연", "SUPPORTING": "조연", "BACKGROUND": "단역"}

def 작품_한줄(w, 표):
    t = 표.작품(w)
    이름 = t["표기"] if t and t["표기"] and t["출처"] != "위키" else None
    y = (w.get("start") or {}).get("y")
    f = {"TV": "TV", "MOVIE": "극장판", "OVA": "OVA", "ONA": "웹 애니", "SPECIAL": "스페셜", "TV_SHORT": "TV 단편"}.get(w.get("format") or "", w.get("format") or "")
    상태 = {"NOT_YET_RELEASED": ", 방영 전", "RELEASING": ", 방영 중"}.get(w.get("status") or "", "")
    return 이름, f"{이름 or '(표기 없음: ' + (w.get('romaji') or '') + ')'} ({y or '연도 모름'}{', ' + f if f else ''}{', ' + str(w['episodes']) + '화' if w.get('episodes') else ''}{상태})"

def 재료_사람(st):
    p = st.주제["사람"]
    d = al_staff(p["id"])
    if not d:
        st.이유 = "AniList에서 사람을 못 찾았다"; return False
    표 = st.용어표
    이름 = 표.사람(d["full"], d.get("native"))
    if not 이름["표기"] or 이름["출처"] == "위키":
        st.이유 = f"사람 표기 없음: {d['full']}"; return False
    줄, 작품수 = [], set()
    합침 = {}
    for b in sorted(d["배역"], key=lambda x: -(x.get("popularity") or 0)):
        w = {"id": b["작품id"], "romaji": b["romaji"], "native": b["native"], "한글별칭": b["한글별칭"], "format": b["format"], "start": {"y": b["year"], "m": b.get("month")}, "studios": b.get("studios") or []}
        t = 표.작품(w)
        if not t["표기"] or t["출처"] == "위키":
            continue
        h = 합침.setdefault(b["작품id"], {"표기": t["표기"], "year": b["year"], "배역": [], "역할": b.get("역할")})
        for c in b["캐릭터"]:
            ct = 표.사람(c["full"], c.get("native"))
            if ct["표기"] and ct["출처"] != "위키" and ct["표기"] not in h["배역"]:
                h["배역"].append(ct["표기"])
        if b.get("역할") == "MAIN":
            h["역할"] = "MAIN"
    for wid, h in 합침.items():
        배역글 = " · ".join(h["배역"])
        줄.append(f"- {h['표기']} ({h['year'] or '연도 모름'}) — " + (f"{배역글} 역 ({배역표기.get(h.get('역할') or '', '')})".replace(" ()", "") if 배역글 else "배역 표기 없음. 작품 이름만 쓴다") + f"  ← AniList {d['siteUrl']}")
        작품수.add(wid)
    for s in sorted(d["참여"], key=lambda x: -(x.get("popularity") or 0)):
        w = {"id": s["작품id"], "romaji": s["romaji"], "native": s["native"], "한글별칭": s["한글별칭"], "format": s["format"], "start": {"y": s["year"], "m": s.get("month")}, "studios": s.get("studios") or []}
        t = 표.작품(w)
        if not t["표기"] or t["출처"] == "위키":
            continue
        줄.append(f"- {t['표기']} ({s['year'] or '연도 모름'}) — {역할표기(s['역할'])}  ← AniList {d['siteUrl']}")
        작품수.add(s["작품id"])
    if len(작품수) < 최소참여작:
        st.이유 = f"한국 표기 있는 참여작 {len(작품수)}개. {최소참여작}개 미만"; return False
    dob = d.get("dob") or {}
    직업 = " · ".join(dict.fromkeys(역할표기(x) if x != "Voice Actor" else "성우" for x in d["직업"])) or "모름"
    사실 = [f"[사람] {이름['표기']} — {p.get('역할')}  (표기 출처: {이름['출처']})",
            f"- 하는 일: {직업}  ← AniList",
            (f"- 태어난 해: {dob['year']}년  ← AniList" if dob.get("year") else "- 태어난 해: 모름"),
            f"- 이 시리즈에서: {st.주제['시리즈']}" + (f" {표.사람(p['배역'], p.get('배역native'))['표기'] or ''} 역" if p.get("배역") else ""),
            "", f"[참여작 — 한국 표기가 있는 것만. {len(작품수)}개]"] + 줄[:30]
    w = 위키_작품문서({"한국제목": 이름["표기"]}) if 이름["출처"] == "용어집" else None
    if w and 애니낱말.search(w["본문"][:800]):
        st.맥락 = w["본문"][:3000]; st.맥락링크.append(w["링크"])
    st.사실표 = "\n".join(사실)
    st.사실링크 = [d["siteUrl"]]
    return True

def 재료_기념일(st):
    k, w, 표 = st.주제["기념일"], st.주제["작품"], st.용어표
    이름, 한줄 = 작품_한줄(w, 표)
    if not 이름:
        st.이유 = f"작품 표기 없음: {w.get('romaji')}"; return False
    사실 = []
    if k["종류"] == "방영":
        사실 += [f"[기념일] {이름} 방영 {k['n']}주년. 첫 방영일 {k['날']}  ← AniList",
                 f"- 오늘: {오늘.isoformat()}. 기념일까지 {k['며칠']:+d}일"]
    else:
        c = k["캐릭터"]
        ct = 표.사람(c["full"], c.get("native"))
        if not ct["표기"] or ct["출처"] == "위키":
            st.이유 = f"캐릭터 표기 없음: {c['full']}"; return False
        사실 += [f"[기념일] {ct['표기']} 생일 {k['날']} (월-일. 공식 설정)  ← AniList",
                 f"- 오늘: {오늘.isoformat()}. 생일까지 {k['며칠']:+d}일", f"- 작품: {한줄}  ← AniList"]
        va = " · ".join(x["표기"] for x in (표.사람(v["full"], v.get("native")) for v in c["va"]) if x["표기"] and x["출처"] != "위키")
        if va:
            사실.append(f"- 성우: {va}  ← AniList")
    st_ = w.get("start") or {}
    사실 += [f"- 첫 방영일: {st_.get('y')}-{st_.get('m') or '?'}-{st_.get('d') or '?'}  ← AniList",
             f"- 화수: {w.get('episodes') or '모름'}  ← AniList",
             f"- 제작사: {', '.join(w.get('studios') or []) or '모름'}  ← AniList"]
    if w.get("원작"):
        사실.append(f"- 원작: {w['원작'].get('format') or ''} {w['원작'].get('year') or ''}년부터  ← AniList")
    full = al_media(w["id"], full=True) or w
    본 = set()
    for p in full.get("staff", []):
        if re.search(r"Director|Original Creator", p.get("role") or ""):
            pt = 표.사람(p["full"], p.get("native"))
            if pt["표기"] and pt["출처"] != "위키" and (역할표기(p["role"]), pt["표기"]) not in 본:
                본.add((역할표기(p["role"]), pt["표기"]))
                사실.append(f"- {역할표기(p['role'])}: {pt['표기']}  ← AniList")
    s = 시리즈(st.주제["시리즈"])
    다른 = []
    for x in _애니만(s["작품들"]):
        if x["id"] == w["id"]:
            continue
        n2, l2 = 작품_한줄(x, 표)
        if n2:
            다른.append(f"  - {l2}")
    if 다른:
        사실 += ["", "[같은 시리즈의 다른 작품 — AniList]"] + 다른[:12]
    라 = s["라프텔"]
    if 라:
        사실 += ["", "[라프텔에 있는 것 — 한국에서 볼 수 있다]"] + [f"  - {x['이름']}{' (더빙)' if x['더빙'] else ''}  ← {x['링크']}" for x in 라[:8]]
    wk = 위키_작품문서({"한국제목": 이름}, st.주제["시리즈"])
    if wk:
        st.맥락 = wk["본문"][:4000]; st.맥락링크.append(wk["링크"])
        rec = 위키_평가절({"한국제목": 이름}, st.주제["시리즈"], "ko")
        if rec:
            사실 += ["", "[당시 반응 — 위키백과 ko 평가 절. 여기 적힌 것만 반응으로 쓴다]", rec["본문"][:2500], f"  ← {rec['url']}"]
            st.맥락링크.append(rec["url"])
    st.사실표 = "\n".join(사실)
    st.사실링크 = [w.get("siteUrl")]
    return True

def 순서표_만들기(st):
    """관계도로 순서를 계산한다. 모델을 안 부른다."""
    s, 표 = 시리즈(st.주제["시리즈"]), st.용어표
    애니 = _애니만(s["작품들"])
    줄, 뺀것, 본편수 = [], [], 0
    for i, w in enumerate(애니, 1):
        이름, 한줄 = 작품_한줄(w, 표)
        rel = next((r["type"] for r in (s["root"]["relations"] if s["root"] else []) if r["id"] == w["id"]), None)
        if w["본편사슬"] and w.get("format") in ("TV", "ONA", "TV_SHORT"):
            갈래 = "본편"; 본편수 += 1
        elif rel == "SUMMARY" or re.search(r"recap|総集編|Compilation", w.get("romaji") or "", re.I):
            갈래 = "총집편"
        elif w.get("format") == "MOVIE":
            갈래 = "극장판 (본편 밖)"
        else:
            갈래 = "외전 (본편 밖)"
        if not 이름:
            뺀것.append(w.get("romaji")); continue
        줄.append({"번호": len(줄) + 1, "이름": 이름, "갈래": 갈래, "연도": (w.get("start") or {}).get("y"), "화수": w.get("episodes"), "idMal": w.get("idMal"), "한줄": 한줄})
    return 줄, 뺀것, 본편수

def 재료_감상순서(st):
    s, 표 = 시리즈(st.주제["시리즈"]), st.용어표
    순서, 뺀것, 본편수 = 순서표_만들기(st)
    편 = 용어집_편목록(st.주제["시리즈"])
    if 본편수 < 2 and len(편) < 3:
        st.이유 = f"본편 {본편수}개 · 편 {len(편)}개. 순서를 만들 것이 없다"; return False
    필러, 죽음 = {}, False
    for r in 순서:
        if r["갈래"] == "본편" and r.get("idMal"):
            eps = jikan_episodes(r["idMal"])
            if eps is None:
                죽음 = True; continue
            필러[r["이름"]] = ([e["번호"] for e in eps if e["필러"]], [e["번호"] for e in eps if e["총집편"]], len(eps))
    if 죽음 and (본편수 <= 1 or not 필러없어도진행):
        st.이유 = "Jikan이 죽어서 필러 표시를 못 받았다. 본편이 하나뿐인 작품은 필러 없이 못 만든다"; return False
    사실 = [f"[감상 순서 — 코드가 관계도로 계산했다. 이 순서와 갈래를 바꾸지 않는다]"]
    for r in 순서:
        사실.append(f"{r['번호']}. [{r['갈래']}] {r['한줄']}  ← AniList")
    if 편:
        사실 += ["", "[편 — 용어집. 본편 한 덩어리 안에서 편 순서로 본다]"] + [f"- {n}: {m}" for n, a, b, m in 편]
    if 필러:
        사실.append("")
        사실.append("[필러 · 총집편 — Jikan(MyAnimeList) 회차 표시. 여기 없는 화수를 필러라고 하지 않는다]")
        for 이름, (f, rc, n) in 필러.items():
            사실.append(f"- {이름}: 전체 {n}화. 필러 {len(f)}화" + (f" ({', '.join(구간묶기(f)[:25])})" if f else "") + (f". 총집편 {', '.join(구간묶기(rc)[:8])}" if rc else ""))
    elif 죽음:
        사실 += ["", "[필러] 회차 표시를 못 받았다. 필러 이야기는 안 쓴다"]
    if 뺀것:
        사실 += ["", f"[한국 표기가 없어 순서에서 뺀 작품 {len(뺀것)}개 — 글에 안 쓴다] " + " / ".join(뺀것[:8])]
    라 = s["라프텔"]
    if 라:
        사실 += ["", "[라프텔에 있는 것 — 한국에서 볼 수 있다. 제목은 라프텔 표기]"] + [f"- {x['이름']}{' (더빙)' if x['더빙'] else ''}  ← {x['링크']}" for x in 라[:12]]
    root = s["root"] or {}
    if root.get("원작"):
        사실.append(f"- 원작: {root['원작'].get('format') or ''} {root['원작'].get('year') or ''}년부터  ← AniList")
    st.순서표 = [{"번호": r["번호"], "이름": r["이름"], "갈래": r["갈래"]} for r in 순서]
    wk = 위키_작품문서({"한국제목": st.주제["시리즈"]}, st.주제["시리즈"])
    if wk:
        st.맥락 = wk["본문"][:3000]; st.맥락링크.append(wk["링크"])
    st.사실표 = "\n".join(사실)
    st.사실링크 = [root.get("siteUrl")] if root else []
    return True

print("정보형 재료 준비 끝")

# ## 12. 미리거르기 · 재료판정 — 심층형
#
# 모델을 부르기 전에 확실히 아닌 것만 코드로 뺀다. 그 다음 페이지마다 모델이 값을 낸다. 쓸지 버릴지는 코드가 정한다.

from typing import Literal, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

def pre_filter(pages, 주제):
    w = 주제["작품"]
    keys = [_norm(x) for x in [w.get("한국제목"), 주제["시리즈"], w.get("romaji"), w.get("english"), w.get("native")] + list(w.get("synonyms") or [])[:5] if x and len(_norm(x)) >= 2]
    out, seen = [], set()
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        if len(p["본문"]) < 400:
            continue
        low = _norm(p["본문"] + " " + p["제목"])
        if not any(k in low for k in keys):
            continue
        out.append(p)
    return out

class 재료판정(BaseModel):
    다루는_정도: Literal["작품 평", "제작 이야기", "인터뷰", "스치듯 언급", "관계없음"]
    평가있음: bool
    홍보문: bool
    관점: str = Field(description="글쓴이가 이 작품에서 무엇을 근거로 무엇을 봤는지 한 줄. 좋다 나쁘다만 적지 않는다")
    근거: List[Literal["연출", "작화", "각본", "음악", "성우", "원작 비교", "제작 배경", "장면"]]
    인용: str = Field(description="원문에서 그대로 옮긴 한 문장. 원문 언어 그대로. 없으면 빈 문자열")
    발언메모: List[str] = Field(default_factory=list, description="제작진 · 성우 · 원작자 본인이 한 말. '누가 — 무슨 말' 꼴. 원문 표현대로 짧게. 없으면 빈 목록")
    장면메모: List[str] = Field(default_factory=list, description="이 글이 특정 회차나 장면을 들어 말한 것. '몇 화 또는 어느 장면 — 무슨 말' 꼴. 없으면 빈 목록")

재료판정관 = Agent(
    MODEL,
    output_type=재료판정,
    system_prompt="""너는 애니 글 한 편을 읽고 아래 값을 낸다. 쓸지 버릴지는 네가 정하지 않는다. 값만 낸다.

[다루는_정도]
- "작품 평": 이 작품을 놓고 쓴 글이다. 리뷰, 시즌 결산, 회차 리뷰.
- "제작 이야기": 제작 배경 · 각색 · 연출 · 작화 과정을 다룬 글이다. 인터뷰가 아니라 기자가 쓴 것.
- "인터뷰": 감독 · 원작자 · 각본가 · 성우가 직접 말한 글이다. 평가가 없어도 이걸로 둔다.
- "스치듯 언급": 이름만 나온다. 방영 안내, 굿즈 소식, 순위표.
- "관계없음": 이 작품 이야기가 아니다. 이름이 같은 다른 작품이면 관계없음이다.

[평가있음] 글쓴이가 좋다 · 나쁘다 · 아쉽다 · 낫다 같은 판단을 하나라도 적었으면 참이다.
[홍보문] 방영일 · 캐스트 · 줄거리 · 굿즈만 나열하고 글쓴이 판단이 없으면 참이다. 보도자료를 옮긴 기사가 그렇다. 인터뷰는 홍보문이 아니다.
[관점] 글쓴이가 이 작품에서 무엇을 근거로 무엇을 봤는지 한 줄. 예: "원작의 긴 회상을 두 화로 줄였는데, 그래서 결말의 무게가 가벼워졌다고 봤다"
[근거] 관점이 기대는 것. 여럿 골라도 된다.
[인용] 관점을 가장 잘 보여주는 원문 문장 하나. 원문 언어 그대로, 한 글자도 안 고친다. 없으면 빈 문자열.
[발언메모] 제작진 · 성우 · 원작자 본인의 말. "감독 — 이 장면은 원작에 없어서 새로 그렸다"처럼. 글에 없는 말을 붙이지 않는다.
[장면메모] 특정 회차 · 장면을 들어 말한 것. "11화 — 작화가 달라졌다는 말이 많았다"처럼. 회차 번호는 원문대로.

주의
- 페이지에 다른 글 제목 · 댓글 · 광고가 붙어 있어도 본문만 본다.
- 이름은 로마자 · 일본어 · 한글이 섞여 있다. 표기가 달라도 같은 사람 · 작품이다.""",
)

def _judge_ask(p, 주제):
    w = 주제["작품"]
    return (f"작품: {w.get('한국제목') or 주제['시리즈']} / {w.get('romaji')} / {w.get('native')} ({(w.get('start') or {}).get('y')})\n"
            f"시리즈: {주제['시리즈']}\n유형: {주제['유형']}\n"
            f"페이지 제목: {p['제목']}\n주소: {p['url']}\n\n본문:\n{p['본문'][:8000]}")

async def _judge_all(pages, 주제):
    sem = asyncio.Semaphore(동시판정)
    async def one(p):
        async with sem:
            try:
                r = await 재료판정관.run(_judge_ask(p, 주제), usage_limits=UsageLimits(request_limit=2))
            except Exception as e:
                print("   판정 실패:", e)
                return None
            return {"매체": p.get("매체") or 매체이름(p["url"]), "도메인": _host(p["url"]), "url": p["url"], "판정": r.output, "원문": p["본문"]}
    return await asyncio.gather(*(one(p) for p in pages))

def judge_pages(pages, 주제, budget):
    pages = pages[: max(0, budget["남은콜"])]
    if not pages:
        return []
    got = _run_async(_judge_all(pages, 주제))
    budget["남은콜"] -= len(pages)
    return [g for g in got if g]

def keep_rules(판정들, 유형):
    재료, 발언 = [], []
    per = {}
    for r in 판정들:
        j = r["판정"]
        if j.다루는_정도 == "관계없음":
            continue
        if j.발언메모:
            발언.append({"매체": r["매체"], "말": [x.strip() for x in j.발언메모 if x.strip()][:8], "링크": r["url"], "원문": r["원문"]})
        if 유형 == "제작이야기":
            ok = j.다루는_정도 in ("인터뷰", "제작 이야기") and (j.발언메모 or j.관점.strip())
        else:
            ok = j.다루는_정도 in ("작품 평",) and j.평가있음 and not j.홍보문 and len(j.관점.strip()) >= 10
        if not ok:
            continue
        c = per.get(r["도메인"], 0)
        if c >= PER_MEDIA:
            continue
        per[r["도메인"]] = c + 1
        재료.append({"매체": r["매체"], "관점": j.관점.strip(), "인용": j.인용.strip(), "근거": j.근거,
                    "발언메모": [x.strip() for x in j.발언메모 if x.strip()][:8], "장면메모": [x.strip() for x in j.장면메모 if x.strip()][:8],
                    "링크": r["url"], "원문": r["원문"]})
    return 재료, [m for m in 발언 if m["말"]]

print("재료 판정 준비 끝")

# ## 13. 관점 묶기
#
# 임베딩으로 비슷한 관점을 묶는다. 모델을 안 부른다. sentence-transformers가 없으면 하나씩 따로 둔다.

_embed = None
def embedder():
    global _embed
    if _embed is None:
        from sentence_transformers import SentenceTransformer
        _embed = SentenceTransformer(EMBED_MODEL)
    return _embed

def group_views(재료, 문턱=0.62):
    if len(재료) <= 1:
        return [[r] for r in 재료]
    try:
        import numpy as np
        v = embedder().encode([r["관점"] for r in 재료], normalize_embeddings=True)
    except Exception as e:
        print("   임베딩 없음. 관점을 따로 둔다:", str(e)[:60])
        return [[r] for r in 재료]
    sim = np.asarray(v) @ np.asarray(v).T
    남음, 묶음 = list(range(len(재료))), []
    while 남음:
        i = 남음.pop(0); 덩어리 = [i]
        for j in list(남음):
            if sim[i][j] >= 문턱:
                덩어리.append(j); 남음.remove(j)
        묶음.append([재료[k] for k in 덩어리])
    묶음.sort(key=len, reverse=True)
    return 묶음

def views_text(묶음):
    줄 = []
    for n, g in enumerate(묶음, 1):
        매체들 = ", ".join(dict.fromkeys(r["매체"] for r in g))
        줄.append(f"[관점 {n}] ({매체들} / {len(g)}곳)")
        for r in g:
            줄.append(f"  - ({r['매체']}) {r['관점']}")
            if r["인용"]:
                줄.append(f'    인용: "{r["인용"]}"  ← {r["링크"]}')
            for m in r.get("장면메모") or []:
                줄.append(f"    장면: {m}  ({r['매체']})")
            for m in r.get("발언메모") or []:
                줄.append(f"    발언: {m}  ({r['매체']})")
    return "\n".join(줄)

def 발췌_text(재료, n=발췌글자):
    return "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:n]}" for r in 재료[:6])

def 말_text(말들):
    return "\n".join(f"- ({m['매체']}) {x}  ← {m['링크']}" for m in 말들 for x in m["말"])

def 작품_text(주제, 표):
    w = 주제["작품"]
    이름, 한줄 = 작품_한줄(w, 표)
    줄 = [f"작품: {한줄}", f"시리즈: {주제['시리즈']}", f"제작사: {', '.join(w.get('studios') or []) or '모름'}"]
    if w.get("원작"):
        줄.append(f"원작: {w['원작'].get('format') or ''} {w['원작'].get('year') or ''}년부터")
    for p in (주제.get("제작진") or []):
        t = 표.사람(p["full"], p.get("native"))
        if t["표기"] and t["출처"] != "위키":
            줄.append(f"{역할표기(p.get('role'))}: {t['표기']}")
    return "\n".join(줄)

print("관점 묶기 준비 끝")

# ## 14. 기획자
#
# 재료를 받아 뼈대를 짠다. 문장은 안 쓴다. 정보형은 사실표에서, 심층형은 관점표 · 발언 · 발췌에서 사실을 캔다.
# 두 번째부터는 지난 번 편집국장 문제 목록을 받는다.

온도들 = ["건조", "따뜻함", "짓궂음"]
시작점들 = {
    "정보": ["오늘 날짜에서", "숫자 하나에서", "사람 이름 하나에서", "가장 최근 작품에서", "가장 오래된 작품에서", "독자가 묻는 질문에서"],
    "심층": ["한 장면에서", "제작진의 말 한마디에서", "평이 갈린 지점에서", "원작과 다른 곳에서", "방영 당시의 자리에서", "독자가 묻는 질문에서"],
}

class 기획주문(BaseModel):
    유형: str
    모양: str
    주제한줄: str
    작품정보: str
    표기표: str
    사실표: str = ""
    관점표: str = ""
    발췌: str = ""
    발언: str = ""
    맥락: str = ""
    순서표: str = ""
    온도: str
    시작점: str
    평개수: int = 0
    지난문제: str = ""

class 인용계획(BaseModel):
    매체: str
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 원문이 한국어면 그대로")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것 한 줄")
    쓸이름: List[str] = Field(default_factory=list, description="이 문단에 나오는 사람 · 작품 · 캐릭터 이름. 표기표 · 재료에 적힌 한국 표기 그대로")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. 사실표 · 관점표 · 발췌 · 발언 · 맥락에 있는 것만. 구체(이름 · 연도 · 화수 · 장면)가 하나 이상")
    쓸관점: List[str] = Field(default_factory=list, description="이 문단이 기대는 남의 관점. 관점표에서 그대로. 정보형은 빈 목록")

class 개요(BaseModel):
    주제: str = Field(description="이 글이 하려는 말 한 줄")
    인용둘: List[인용계획] = Field(default_factory=list, max_length=2)
    문단들: List[문단계획] = Field(min_length=3, max_length=6)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. '사실 ← 출처' 꼴. 출처는 사실표 / 관점표 / 발췌 / 발언 / 맥락 중 하나. 여기 없는 사실은 작가가 못 쓴다")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
    if d.모양 == "정보":
        재료칸 = f"[사실표 — 이 글의 사실은 여기 있는 것뿐이다. 날짜 · 숫자 · 이름을 그대로 쓴다]\n{d.사실표}\n\n[맥락 — 위키백과. 지금 상태의 정보다. 연도가 같이 적힌 것만 당시 사실로 쓴다]\n{d.맥락 or '(없음)'}"
        규칙 = f"""[규칙 — 정보형]
- 사실목록은 여덟 개를 넘긴다. 사실표 · 맥락에 있는 것만. 기억으로 아는 것을 넣지 않는다. "사실 ← 사실표" / "사실 ← 맥락" 꼴로 출처를 적는다.
- 판단을 넣지 않는다. "좋다", "명작이다", "볼 만하다"는 이 글에 없다. 사실을 고르고 놓는 순서로만 말한다.
- 이름은 표기표 · 사실표에 적힌 한국 표기 그대로. "(표기 없음)" · "배역 표기 없음" · "임시"라고 된 이름은 쓸이름에 넣지 않는다. 그 이름이 필요한 문단은 만들지 않는다.
- 문단마다 구체가 하나 이상이다. 연도, 화수, 사람 이름, 작품 이름.
- 같은 사실을 두 문단에서 되풀이하지 않는다.
- 재료가 없다 · 확인되지 않는다는 말은 어느 문단에도 넣지 않는다. 모르는 것은 안 쓴다.
- 마지막 문단은 독자가 다음에 무엇을 보면 되는지로 닫는다. 부추기지 않는다. "~를 보면 된다" 정도다.
{'- 순서표의 번호와 갈래를 그대로 따른다. 본편 밖 작품을 본편 사이에 끼워 넣지 않는다. "본편 밖"은 "본편 이야기에 들어가지 않는다"로 쓴다. "정사"라는 말은 재료에 있을 때만 쓴다.' if d.순서표 else ''}
{'- 필러는 사실표의 화수만 쓴다. 사실표에 필러가 없으면 필러 이야기를 안 한다.' if d.유형 == '감상순서' else ''}
{'- 참여작은 사실표에 있는 것만. 배역 표기가 없는 작품은 작품 이름만 쓰고 배역을 지어내지 않는다.' if d.유형 == '사람' else ''}
{'- 기념일 글이다. 첫 문단에 무엇의 몇 주년(또는 누구의 생일)인지가 나온다. "당시 반응"은 사실표의 평가 절에 있는 것만 쓴다.' if d.유형 == '기념일' else ''}"""
    else:
        재료칸 = (f"[관점표 — 남의 평은 여기 있는 것만. \"발언:\" 줄이 제작진의 말, \"장면:\" 줄이 장면메모다]\n{d.관점표 or '(없음)'}\n\n"
                  f"[원문 발췌 — 사실과 장면 · 발언을 여기서 캐낸다. 판단은 글쓴이 것이고 사실은 가져다 쓴다]\n{d.발췌 or '(없음)'}\n\n"
                  f"[제작진 · 성우의 말 — \"감독은 ~라고 했다\"로 쓸 수 있다]\n{d.발언 or '(없음)'}\n\n"
                  f"[맥락 — 위키백과. 지금 상태의 정보다]\n{d.맥락 or '(없음)'}")
        평하나 = """
- 관점표에 매체가 하나뿐이다. 그 매체의 판단을 이 글의 결론으로 삼지 않는다. "평론가들은", "평단은"처럼 여럿의 평인 것처럼 적지 않는다. 매체 이름을 밝히고 그 매체가 봤다고 적는다.""" if d.평개수 <= 1 else ""
        규칙 = f"""[규칙 — 심층형]
- 사실목록은 열 개를 넘긴다. 작품 정보 · 관점표 · 발췌 · 발언 · 맥락에 있는 것만. 기억으로 아는 것을 넣지 않는다. 출처를 "사실 ← 관점표" 꼴로 적는다.
- 장면 · 발언이 제일 중요하다. 어느 화 어느 장면에서 무엇이 어떻게 됐는지, 감독 · 원작자 · 성우가 무슨 말을 했는지를 사실목록에 이름과 함께 넣는다.
- 문단마다 구체가 하나 이상이다. 장면, 회차, 발언, 연도, 사람 이름.
- 인용은 두 개까지. 관점표의 인용 중에서. 영어 · 일본어면 한국어로 옮기고 한국어면 그대로. 한 문장이다.
- 인용의 매체는 관점표에 적힌 이름 그대로. "한 매체"면 그대로 "한 매체"다. 도메인을 적지 않는다.
- 이름은 표기표에 적힌 한국 표기 그대로. 표기표에 없는 사람 · 캐릭터는 이름 없이 역할로만 부른다("감독은", "주인공은").
- 같은 말을 문단마다 되풀이하지 않는다. 주제는 한 번 말한다.
- 재료가 없다 · 정보가 제한된다는 말은 어느 문단에도 넣지 않는다.
- 결말을 밝히지 않는다. 마지막 화의 사건은 쓰지 않는다.
- 마지막 문단은 글쓴이 자신의 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 작품 중 하나다"처럼 정리하지 않는다.
{'- 제작 이야기 글이다. 발언이 글의 가운데다. 발언이 하나도 없는 문단은 셋 중 하나를 넘기지 않는다.' if d.유형 == '제작이야기' else ''}{평하나}"""
    return f"""너는 애니 글의 뼈대를 짠다. 문장은 안 쓴다. 무엇을 어떤 순서로 말할지, 어떤 사실과 이름으로 말할지를 정한다.
작가는 네가 적어 준 사실과 이름만 쓸 수 있다. 네가 적게 주면 글이 비고 추상어로 찬다. 많이, 구체적으로 준다.

[유형] {d.유형} ({d.모양}형)
[주제] {d.주제한줄}

[작품]
{d.작품정보}

[표기표 — 이름은 이것대로만]
{d.표기표 or '(없음)'}

{('[순서표 — 코드가 계산한 순서. 번호 · 갈래를 그대로 지킨다]' + chr(10) + d.순서표 + chr(10) + chr(10)) if d.순서표 else ''}{재료칸}

[이번 글]
온도: {d.온도} / 시작점: {d.시작점}
문단은 셋에서 여섯이다. 문단마다 할 말 하나다.

{규칙}{지난}"""

print("기획자 준비 끝")

# ## 15. 작가
#
# 개요만 받아 문장으로 쓴다. 재료 원문은 안 본다. 개요에 없는 사실은 못 쓴다.

class 집필주문(BaseModel):
    유형: str
    모양: str
    주제한줄: str
    표기표: str
    개요: 개요
    온도: str
    순서표: str = ""

class Article(BaseModel):
    제목: str
    본문: str

작가 = Agent(MODEL, deps_type=집필주문, output_type=Article)

def _사실만(s):
    return s.split(" ← ")[0].strip()

@작가.system_prompt
def _작가프롬프트(ctx) -> str:
    d = ctx.deps
    o = d.개요
    문단 = "\n".join(f"{i+1}. {p.할말}\n   이름: {', '.join(p.쓸이름) or '없음'}\n   사실: {'; '.join(_사실만(x) for x in p.쓸사실) or '없음'}\n   관점: {'; '.join(p.쓸관점) or '없음'}"
                    for i, p in enumerate(o.문단들))
    인용 = "\n".join(f"- {q.매체}: {q.옮긴문장}" for q in o.인용둘) or "없음"
    분량 = "800자에서 1400자 사이" if d.모양 == "정보" else "1200자에서 2000자 사이"
    if d.유형 == "감상순서":
        분량 = "1000자에서 1800자 사이"
    모양규칙 = ("- 판단을 쓰지 않는다. \"좋다\", \"명작\", \"볼 만하다\", \"추천한다\"는 없다. 사실만 놓는다. \"~로 보인다\", \"~일 것이다\"도 쓰지 않는다.\n"
               "- 날짜 · 숫자 · 화수는 사실 목록에 적힌 그대로다. 어림하지 않는다."
               if d.모양 == "정보" else
               "- 위 사실 목록과 인용 밖의 사실이나 남의 평을 넣지 않는다. 네 판단은 넣어도 된다. 판단은 \"~다\"로 끝낸다.\n"
               "- 인용은 \"아니메! 아니메!는 “…”라고 썼다\" 꼴로 매체 이름을 문장 안에 넣는다. 발언은 \"감독은 “…”라고 했다\" 꼴이다.")
    순서 = f"\n[순서표 — 이 번호 순서대로 본문에 나온다. 순서를 바꾸지 않는다]\n{d.순서표}\n" if d.순서표 else ""
    return f"""너는 애니 글을 쓴다. 뼈대는 이미 짜여 있다. 그것을 문장으로 옮긴다.

[유형] {d.유형} ({d.모양}형)
[주제] {d.주제한줄}
[글의 주제 한 줄] {o.주제}
[온도] {d.온도}
{순서}
[표기표 — 이름은 이것대로만. 여기와 사실 목록에 없는 이름은 쓰지 않는다]
{d.표기표 or '(없음)'}

[문단 계획]
{문단}

[쓸 수 있는 인용 — 이 문장 그대로]
{인용}

[쓸 수 있는 사실 — 이 밖의 사실은 쓰지 않는다]
{chr(10).join("- " + _사실만(f) for f in o.사실목록)}

[규칙]
- 존댓말을 쓰지 않는다. "~다"로 끝낸다.
{모양규칙}
- 문단 계획 순서를 지킨다. 문단마다 할 말 하나다. 문단 계획에 적힌 이름과 사실을 그 문단에서 실제로 쓴다.
- 구체로 쓴다. 어느 작품 몇 년, 몇 화, 누가 무엇을. "감동적이다", "긴장감이 있다" 같은 말만으로 문단을 채우지 않는다.
- 같은 말을 되풀이하지 않는다. 주제는 한 번만 말한다.
- 재료가 없다 · 정보가 제한된다 · 확인할 수 없다는 말을 쓰지 않는다. 아는 것만 쓴다.
- 이름은 한국 표기다. 로마자 이름 · 일본어 이름을 쓰지 않는다. 영어 제목을 쓰지 않는다. 작품 이름은 《 》로 감싼다. 편 · 시즌 이름은 그대로 쓴다.
- 캐릭터 · 사람 이름은 표기표의 정식 표기를 첫 등장에 쓰고, 그 뒤로는 별칭이 있으면 별칭을 써도 된다.
- 결말을 밝히지 않는다.
- 분량은 {분량}다."""

print("작가 준비 끝")

# ## 16. 교정자
#
# 작가 글을 받아 문장 짜임만 고친다. 뜻과 사실은 못 바꾼다. 음악 v2.9와 같다.

class 교정본(BaseModel):
    본문: str
    고친곳: List[str] = Field(description="무엇을 어떻게 고쳤는지 한 줄씩")

교정자 = Agent(
    MODEL,
    output_type=교정본,
    system_prompt="""너는 한국어 문장을 다듬는다. 뜻과 사실과 판단은 하나도 안 바꾼다. 문장 짜임만 고친다.

번역투는 영어 문장을 그대로 옮긴 것처럼 읽히는 문장이다. 아래가 그것이다. 보이면 고친다.
1. "~는 것이 아니라 ~다" 틀. 앞을 빼고 뒤만 말하거나, 두 문장으로 나눈다.
2. 한 문장에 절이 셋 넘게 겹쳐서 끝까지 가야 뜻이 잡히는 것. 뜻 단위로 끊는다.
3. 물건이 주어로 서서 스스로 무언가를 하는 것. "연출이 놓인다", "작화가 이야기를 밀어간다". 사람이나 화면에서 실제로 일어나는 일로 바꾼다.
4. 결론을 명사로 닫는 것. "~하는 이유다", "~의 결과에 가깝다", "~하는 방식이다". 동사로 끝낸다.
5. "~에 그치지 않는다", "~에 머물지 않는다", "단순히 ~가 아니다". 뒤에 오는 말만 남긴다.
6. "가장 먼저 ~하는 것은 ~다", "~을 만드는 것은 ~다" 강조 틀. 주어를 앞으로 빼서 보통 문장으로 만든다.
7. 물건이 주어가 되어 비유를 하는 것. 실제로 보이는 것으로 바꾼다.
8. "~라기보다 ~으로 보는 편이 맞다", "~라는 데 있다", "~을 제시한다", "설득력을 얻는다", "유효하다". 판단을 동사로 바로 말한다.

같이 본다.
- 한자어 동사(작동한다, 기능한다, 구축한다, 형성한다)는 일상어로. 한다, 된다, 만든다, 낸다.
- "~적", "~에 있어", "~에 대한", "~을 통해", "~에 의해"는 뺀다.
- 손에 안 잡히는 말(밀도, 결, 층위, 서사, 정체성, 세계관 구축, 몰입감, 완성도, 미학)은 그 문장이 실제로 가리키는 것으로 바꾼다. 가리키는 것이 문장에 없으면 그 말만 뺀다.
- 사람 · 작품 · 캐릭터 · 편 이름은 손대지 않는다. 다만 잘못 적힌 것은 바로잡고 고친 곳에 적는다.
- 영어 · 일본어 문장이 그대로 있으면 한국어로 옮긴다. 이름은 옮기지 않는다.

문장 길이는 내용이 정한다. 고친 곳마다 한 줄로 적는다.""",
)

print("교정자 준비 끝")

# ## 17. 형태 검사 (코드)
#
# 교정자가 놓친 것을 잡는다. 모델을 안 부른다. 공통 줄 + 유형별 줄.

존댓말 = re.compile(r"(습니다|합니다|입니다|세요|네요|해요|이에요|예요|드립니다)")
흐림 = re.compile(r"(인 듯하다|인 듯싶다|일 수도 있다|라고 할 수 있다|인 것 같다|일지도 모른다|라고 볼 수 있다|아닐까|로 보인다|으로 보인다|일 것이다|할 것이다)")
부추김 = re.compile(r"(꼭 봐야|봐야 한다|필견|강력히|추천한다|놓치지 마|정주행을 권|입문작으로 추천)")
판단말 = re.compile(r"(명작|걸작|수작|볼 만하다|훌륭하다|아쉽다|최고의|레전드|갓작|띵작)")
번역틀 = re.compile(r"(것이 아니라|라기보다|에 가깝다|하는 방식이다|하는 이유다|하는 지점|에 있어|을 통해|를 통해|에 의해"
                    r"|그치지 않|머물지 않|단순히 .{1,12}가 아니|는 것은 .{1,20}다\.|편이 맞다|데 있다|데서 생긴다|을 제시한다|를 제시한다|설득력을 얻|유효하다)")
추상어 = re.compile(r"(서사|정체성|밀도|층위|몰입감|완성도|미학|긴장감|다채로움|보편성|세계관 구축|카타르시스)")
다수평 = re.compile(r"(평론가들은|평단은|비평가들은|많은 이들이|대체로 .{0,6}평가|호평이 이어|평이 갈렸|팬들 사이에서)")
재료사정 = re.compile(r"(확인할 수 있는 정보|확인할 수 없|알 수 없다는 점|정보(는|가) .{0,12}제한|AniList|Jikan|MyAnimeList|자료가 (없|부족)|단정할 수는 없|판단의 범위|표기가 없)")
괄호인용 = re.compile(r"\(\s*[가-힣A-Za-z .!'’]{2,20}\s*\)\s*[“\"]")
따옴표안 = re.compile(r"[“\"]([^”\"]{10,})[”\"]")
영문덩어리 = re.compile(r"[A-Za-z][A-Za-z ,.'’\-]{25,}")
로마자이름 = re.compile(r"\b[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})+\b")
일본어 = re.compile(r"[぀-ヿ一-鿿]{2,}")

def _겹침(a, b, n=12):
    A = {a[i:i+n] for i in range(max(0, len(a)-n+1))}
    B = {b[i:i+n] for i in range(max(0, len(b)-n+1))}
    return len(A & B) / len(A) if A and B else 0.0

def 형태검사(본문, st):
    걸림, t = [], 본문
    모양, 유형 = st.모양, st.유형
    if 존댓말.search(t):
        걸림.append("존댓말")
    if not re.search(r"[.!?…\"'》]\s*$", t.strip()):
        걸림.append("문장 끊김")
    문단 = [p.strip() for p in t.split("\n") if p.strip()]
    머리 = [p.split()[0] for p in 문단 if p.split()]
    if 머리 and len(set(머리)) < len(머리) * 0.7:
        걸림.append("문단 시작 반복")
    # 표기
    if 일본어.search(t):
        걸림.append("일본어 원문: " + 일본어.search(t).group(0))
    로마 = [x for x in 로마자이름.findall(t) if x not in ("Anime News Network",)]
    if 로마:
        걸림.append("로마자 이름: " + ", ".join(dict.fromkeys(로마[:5])))
    if 영문덩어리.search(t):
        걸림.append("영어 원문")
    걸림 += glossary_check(t, st.주제["시리즈"])
    for k, v in st.용어표.임시().items():
        if v["표기"] and v["표기"] in t:
            걸림.append(f"임시 표기 이름이 글에 있다: {v['표기']} ({k})")
    # 문장
    흐 = 흐림.findall(t)
    if len(흐) >= (1 if 모양 == "정보" else 2):
        걸림.append("판단 흐리는 끝맺음: " + ", ".join(dict.fromkeys(흐)))
    틀 = 번역틀.findall(t)
    if len(틀) >= 3:
        걸림.append("번역투 틀: " + ", ".join(dict.fromkeys(틀)))
    추 = 추상어.findall(t)
    if len(추) >= 4:
        걸림.append(f"추상어 {len(추)}개: " + ", ".join(dict.fromkeys(추)))
    if 재료사정.search(t):
        걸림.append("재료 사정 언급: " + 재료사정.search(t).group(0))
    if 괄호인용.search(t):
        걸림.append("괄호 인용 표기")
    for q in 따옴표안.findall(t):
        if len(re.findall(r"[.!?]", q)) >= 2 or len(q) > 120:
            걸림.append("인용이 길다"); break
    if re.search(r"\(\s*[a-z0-9.-]+\.[a-z]{2,}\s*\)", t):
        걸림.append("도메인이 글에 나온다")
    if "!" in t:
        걸림.append("느낌표")
    if 부추김.search(t):
        걸림.append("부추기는 말: " + 부추김.search(t).group(0))
    # 정보형 — 판단 없음 · 숫자 대조
    if 모양 == "정보":
        if 판단말.search(t):
            걸림.append("판단하는 말: " + 판단말.search(t).group(0))
        if 다수평.search(t) and "평가 절" not in (st.사실표 or ""):
            걸림.append("없는 반응: " + 다수평.search(t).group(0))
        재료숫자 = set(re.findall(r"\d{4}", (st.사실표 or "") + (st.맥락 or "")))
        for y in set(re.findall(r"(?<!\d)((?:19|20)\d{2})(?=년)", t)):
            if y not in 재료숫자:
                걸림.append(f"재료에 없는 연도: {y}년")
        재료화 = set(re.findall(r"(\d+)\s*화", st.사실표 or ""))
        for n in set(re.findall(r"(\d+)\s*화", t)):
            if 재료화 and n not in 재료화:
                걸림.append(f"재료에 없는 화수: {n}화"); break
        if st.순서표:
            pos = [(r["번호"], t.find(r["이름"]), r["갈래"]) for r in st.순서표]
            나온 = [(n, i, g) for n, i, g in pos if i >= 0]
            본편 = [x for x in pos if x[2] == "본편"]
            본편나온 = [x for x in 본편 if x[1] >= 0]
            if 본편 and len(본편나온) < max(1, int(len(본편) * 0.6 + 0.5)):
                걸림.append(f"순서표의 본편이 글에 모자란다 ({len(본편나온)}/{len(본편)})")
            if any(b[1] < a[1] for a, b in zip(나온, 나온[1:])):
                걸림.append("순서표와 본문 순서가 다르다")
    else:
        재료글 = "\n".join(r["관점"] + " " + r["인용"] for r in st.재료)
        if 재료글.strip() and _겹침(t, 재료글) > 0.08:
            걸림.append("재료 옮겨 적기")
        if len(set(r["매체"] for r in st.재료)) <= 1 and 다수평.search(t):
            걸림.append("없는 다수 평: " + 다수평.search(t).group(0))
        메모 = sum(len(r["장면메모"]) + len(r["발언메모"]) for r in st.재료)
        if 메모 and not re.search(r"\d+\s*화|장면|“", t):
            걸림.append("장면 · 발언이 글에 없다")
        if 유형 == "제작이야기" and not re.search(r"(라고|고) (했다|말했다|밝혔다)", t):
            걸림.append("제작진 발언이 없다")
    # 분량
    하한, 상한 = (800, 1600) if 모양 == "정보" else (1000, 2400)
    if 유형 == "감상순서":
        하한, 상한 = 900, 2000
    if not (하한 <= len(t) <= 상한):
        걸림.append(f"분량 {len(t)}자")
    return list(dict.fromkeys(걸림))

print("형태 검사 준비 끝")

# ## 18. 편집국장
#
# 마지막에 전체를 다시 읽는다. 사실 · 문장 · 구조를 본다. 직접 고치지 않는다. 올릴지도 정하지 않는다.

class 문제(BaseModel):
    종류: Literal["사실", "문장", "구조"]
    어디: str = Field(description="문제가 있는 문장을 그대로")
    설명: str

class 편집판정(BaseModel):
    점수: int = Field(ge=0, le=100)
    문제들: List[문제]
    한줄평: str

편집국장 = Agent(
    MODEL,
    output_type=편집판정,
    system_prompt="""너는 편집국장이다. 완성된 애니 글을 마지막으로 읽는다. 세 가지를 본다.

[사실] 글에 적힌 사실 하나하나를 재료(사실표 · 관점표 · 원문 · 맥락 · 개요)와 대조한다.
- 어디에도 없는 사실이면 종류 "사실"로 잡는다. 남의 평이나 제작진의 말을 지어낸 것도 "사실"이다.
- 날짜 · 화수 · 연도가 재료와 다르면 "사실"이다.
- 이름이 표기표와 다르면 "사실"로 잡고 바른 표기를 적는다. 로마자 · 일본어 이름은 "사실"이다.
- 정보형 글에 판단("명작이다", "볼 만하다")이 있으면 "구조"로 잡는다.
- 글쓴이 자신의 판단과 관찰은(심층형에서) 사실이 아니다. 잡지 않는다.
- 원문이 하나뿐인데 "평론가들은"처럼 여럿의 평으로 적었으면 "사실"로 잡는다.
- 맥락(위키백과)은 지금 상태다. 지금 상황을 당시 사실처럼 적었으면 "사실"로 잡는다.
- 순서표가 있으면 본문의 순서가 순서표와 같은지 본다. 다르면 "구조"다. 본편 밖 작품을 본편처럼 적었으면 "사실"이다.
- 결말이 적혀 있으면 "구조"로 잡는다.

[문장] 뜻이 안 잡히는 문장, 앞뒤가 안 맞는 문장, 영어 · 일본어를 그대로 옮긴 듯한 문장. 종류 "문장".

[구조] 시작과 끝이 맞물리는지, 문단마다 할 말이 하나인지, 같은 말이 되풀이되는지, 구체 없이 "감동적이다" 같은 말로만 채운 문단이 있는지. 종류 "구조".
- 글이 자기 재료 사정을 말하면("확인할 수 없다", "표기가 없다") "구조"로 잡는다.
- 인용 앞에 매체 이름을 괄호로 붙인 것은 "문장"으로 잡는다.
- 부추기는 말("꼭 봐야 한다")은 "구조"로 잡는다.

직접 고치지 않는다. 올릴지 말지도 정하지 않는다. 점수와 문제 목록만 낸다. 문제마다 어느 문장인지 그대로 옮겨 적는다.""",
)

def 편집국장_읽기(st, budget):
    if budget["남은콜"] <= 0:
        return None
    if st.모양 == "정보":
        재료 = f"[사실표]\n{st.사실표}\n\n[맥락]\n{(st.맥락 or '(없음)')[:3000]}"
    else:
        원문들 = "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:4000]}" for r in st.재료) or "(없음)"
        원문들 += "".join(f"\n\n### (발언 · {m['매체']}) {m['링크']}\n" + "\n".join("- " + x for x in m["말"]) for m in st.발언들)
        재료 = f"[관점표]\n{st.관점표 or '(없음)'}\n\n[맥락]\n{(st.맥락 or '(없음)')[:3000]}\n\n[원문 — {len(st.재료)}편]\n{원문들}"
    순서 = ("\n\n[순서표]\n" + "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표)) if st.순서표 else ""
    ask = (f"[유형] {st.유형} ({st.모양}형)\n\n[작품]\n{작품_text(st.주제, st.용어표)}\n\n[표기표]\n{st.용어표.text()}{순서}\n\n"
           f"[개요]\n주제: {st.개요_.주제}\n사실목록:\n" + "\n".join("- " + f for f in st.개요_.사실목록) + "\n\n"
           f"{재료}\n\n[글]\n{st.글.제목}\n\n{st.글.본문}")
    try:
        r = 편집국장.run_sync(ask, usage_limits=UsageLimits(request_limit=2))
    except Exception as e:
        print("   편집국장 실패:", e)
        return None
    budget["남은콜"] -= 1
    return r.output

def 올릴까(걸림, p):
    if 걸림:
        return False, "형태 검사: " + ", ".join(걸림)
    if p is None:
        return False, "편집국장 실패"
    사실 = [x for x in p.문제들 if x.종류 == "사실"]
    if 사실:
        return False, f"사실 문제 {len(사실)}건"
    if p.점수 < PASS_SCORE:
        return False, f"점수 {p.점수} / 문제 {len(p.문제들)}건"
    return True, f"점수 {p.점수}"

def 문제목록_글(걸림, p):
    줄 = [f"- (형태 검사) {x}" for x in 걸림]
    if p:
        줄 += [f"- ({x.종류}) {x.어디} — {x.설명}" for x in p.문제들]
    return "\n".join(줄)

print("편집국장 준비 끝")

# ## 19. 상태 하나 — RunState
#
# 한 편을 만드는 동안 모든 마디가 같이 읽고 쓰는 꾸러미다. 마디는 자기 칸만 채운다.

START, END = "주제뽑기", "끝"
MAX_STEPS = 60

class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 입력
    유형: str
    모양: str = ""
    지정후보: Optional[dict] = None
    # 주제 뽑기
    후보목록: list = []
    후보번호: int = 0
    주제: Optional[dict] = None
    # 표기
    용어표: Optional[object] = None
    # 재료
    사실표: str = ""
    사실링크: list = []
    순서표: list = []
    links: list = []
    pages: list = []
    blocked: list = []
    재료: list = []
    판정원본: list = []
    발언들: list = []
    보강됨: bool = False
    관점표: str = ""
    맥락: str = ""
    맥락링크: list = []
    # 한 바퀴
    주문: Optional[기획주문] = None
    개요_: Optional[개요] = None
    글: Optional[Article] = None
    고친곳: list = []
    걸림: list = []
    판정: Optional[편집판정] = None
    올림: bool = False
    이유: str = ""
    지난문제: str = ""
    # 세는 것
    바퀴: int = 0
    steps: int = 0
    남은콜: int = LLM_CALL_CAP
    # 결과
    기록: list = []
    상태: str = "진행"          # 진행 / 올림 / 탈락 / 주제없음 / 상한
    로그: list = []
    저장경로: Optional[str] = None

    def log(self, s):
        self.로그.append(s)
        print(s)

print("상태 준비 끝")

# ## 20. 마디
#
# 함수 하나가 단계 하나다. 상태를 받아 자기 칸만 채우고 돌려준다. 다른 마디를 안 부른다. 다음에 어디로 갈지 안 정한다.

def n_주제뽑기(st: RunState) -> RunState:
    st.모양 = 유형표[st.유형]
    if not st.후보목록 and st.지정후보:
        st.후보목록 = [st.지정후보]
    if not st.후보목록:
        st.후보목록 = 주제_후보목록(st.유형)
        st.log(f"  후보 {len(st.후보목록)}개 [{st.유형}]")
    st.주제 = None
    while st.후보번호 < len(st.후보목록):
        c = st.후보목록[st.후보번호]
        st.후보번호 += 1
        if _주제키(c) in _쓴주제 and c.get("src") != "지정":
            continue
        if _올린시리즈.get(c["시리즈"], 0) >= 작품당상한 and c.get("src") != "지정":
            continue
        _쓴주제.add(_주제키(c))
        st.주제 = c
        st.log(f"■ [{st.유형}] {c['한줄']}  [{c['src']}]")
        break
    return st

def n_재료모으기(st: RunState) -> RunState:
    st.용어표 = 표기표(st.주제["시리즈"])
    w = st.주제["작품"]
    t = st.용어표.작품(w)
    w["한국제목"] = t["표기"] if t and t["표기"] and t["출처"] != "위키" else None
    if st.모양 == "정보":
        ok = {"사람": 재료_사람, "기념일": 재료_기념일, "감상순서": 재료_감상순서}[st.유형](st)
        if not ok:
            st.사실표 = ""
        return st
    # 심층형 — 검색해서 읽는다
    if not w["한국제목"]:
        st.이유 = f"작품 표기 없음: {w.get('romaji')}"
        return st
    st.links = collect_links(st.주제)
    st.pages, st.blocked = read_pages(st.links, PAGE_LIMIT)
    for lang in ("ko", "en"):
        rec = 위키_평가절(w, st.주제["시리즈"], lang)
        if rec:
            st.pages.append(rec)
    wk = 위키_작품문서(w, st.주제["시리즈"])
    if wk:
        st.맥락 = wk["본문"][:4000]; st.맥락링크.append(wk["링크"])
    return st

def n_표기맞추기(st: RunState) -> RunState:
    표 = st.용어표
    w = st.주제["작품"]
    # 작품의 사람들 표기를 미리 채운다. 심층형은 재료판정이 이름을 로마자로 낼 수 있어서 표가 필요하다
    full = al_media(w["id"], full=True) if st.모양 == "심층" else None
    for p in (full or {}).get("staff", [])[:12] + (st.주제.get("제작진") or []):
        표.사람(p["full"], p.get("native"))
    for c in (full or {}).get("characters", [])[:12]:
        표.사람(c["full"], c.get("native"))
        for v in c["va"][:1]:
            표.사람(v["full"], v.get("native"))
    확정, 임시, 없음 = 표.확정(), 표.임시(), 표.없음()
    st.log(f"  표기: 확정 {len(확정)} · 임시(위키) {len(임시)} · 없음 {len(없음)}" + (f" — 없음: {', '.join(없음[:4])}" if 없음 else ""))
    return st

def n_미리거르기(st: RunState) -> RunState:
    if st.모양 == "심층":
        st.pages = pre_filter(st.pages, st.주제)[:PAGE_LIMIT + 2]
    return st

def n_재료판정(st: RunState) -> RunState:
    if st.모양 == "정보":
        # 코드만 본다. 재료모으기가 이미 충분성을 봤다
        st.log(f"  사실표 {len(st.사실표)}자 · 맥락 {len(st.맥락)}자" + (f" · 순서 {len(st.순서표)}개" if st.순서표 else ""))
        return st
    b = {"남은콜": st.남은콜}
    st.판정원본 = judge_pages(st.pages, st.주제, b)
    st.남은콜 = b["남은콜"]
    st.재료, st.발언들 = keep_rules(st.판정원본, st.유형)
    매체들 = ", ".join(dict.fromkeys(r["매체"] for r in st.재료)) or "-"
    st.log(f"  검색 링크 {len(st.links)} / 읽은 페이지 {len(st.pages)} / robots 막힘 {len(st.blocked)} / 남은 글 {len(st.재료)} ({매체들}) / 발언 {sum(len(m['말']) for m in st.발언들)} / 장면메모 {sum(len(r['장면메모']) for r in st.재료)}")
    return st

def n_재료보강(st: RunState) -> RunState:
    st.보강됨 = True
    w = st.주제["작품"]
    말 = []
    for p in (st.주제.get("제작진") or [])[:3]:
        if p.get("native"):
            말.append(f"{p['native']} {w.get('native') or ''} インタビュー")
        말.append(f"{p['full']} {w.get('romaji') or ''} interview")
    if st.유형 == "작품리뷰":
        말 += [f"{w.get('한국제목') or st.주제['시리즈']} 애니 감상 평가", f"{w.get('english') or w.get('romaji')} season review"]
    links = []
    for q in dict.fromkeys(x for x in 말 if x):
        links += 검색(q, n=10)
    이미 = {p["url"] for p in st.pages} | set(st.links)
    새 = [u for u in dict.fromkeys(u.split("#")[0] for u in links) if u not in 이미 and not 차단됐나(u)]
    pages, blocked = read_pages(새, 보강페이지)
    pages = pre_filter(pages, st.주제)
    b = {"남은콜": st.남은콜}
    더 = judge_pages(pages, st.주제, b)
    st.남은콜 = b["남은콜"]
    st.판정원본 += 더
    st.pages += pages; st.blocked += blocked; st.links += 새
    st.재료, st.발언들 = keep_rules(st.판정원본, st.유형)
    st.log(f"  보강: 페이지 {len(pages)}개 더 읽음 → 남은 글 {len(st.재료)} / 발언 {sum(len(m['말']) for m in st.발언들)}")
    return st

def n_관점묶기(st: RunState) -> RunState:
    st.관점표 = views_text(group_views(st.재료))
    st.맥락링크 += [m["링크"] for m in st.발언들 if m["링크"] not in st.맥락링크]
    # 발언 · 장면메모의 로마자 이름에 표기를 붙인다
    for r in st.재료:
        for x in r["발언메모"] + r["장면메모"]:
            for name in 로마자이름.findall(x):
                st.용어표.사람(name)
    return st

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    순서 = "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표) if st.순서표 else ""
    st.주문 = 기획주문(유형=st.유형, 모양=st.모양, 주제한줄=st.주제["한줄"], 작품정보=작품_text(st.주제, st.용어표),
                     표기표=st.용어표.text(), 사실표=st.사실표, 관점표=st.관점표, 발췌=발췌_text(st.재료), 발언=말_text(st.발언들),
                     맥락=st.맥락[:5000], 순서표=순서, 온도=random.choice(온도들), 시작점=random.choice(시작점들[st.모양]),
                     평개수=len(set(r["매체"] for r in st.재료)), 지난문제=st.지난문제)
    st.개요_ = st.글 = st.판정 = None
    st.고친곳, st.걸림 = [], []
    try:
        st.개요_ = 기획자.run_sync("뼈대를 짠다.", deps=st.주문, usage_limits=UsageLimits(request_limit=3)).output
    except Exception as e:
        st.이유 = f"기획 실패: {e}"
        st.log(f"  {st.이유}")
    st.남은콜 -= 1
    if st.개요_:
        o = st.개요_
        st.log(f"  문단 {len(o.문단들)}개 / 사실 {len(o.사실목록)}개 / 인용 {len(o.인용둘)}개")
        if len(o.사실목록) < (6 if st.모양 == "정보" else 8):
            st.log("  사실목록이 적다. 글이 빌 것이다")
    return st

def n_작가(st: RunState) -> RunState:
    순서 = "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표) if st.순서표 else ""
    try:
        st.글 = 작가.run_sync("쓴다.", deps=집필주문(유형=st.유형, 모양=st.모양, 주제한줄=st.주제["한줄"], 표기표=st.용어표.text(),
                                                   개요=st.개요_, 온도=st.주문.온도, 순서표=순서),
                             usage_limits=UsageLimits(request_limit=3)).output
    except Exception as e:
        st.이유 = f"집필 실패: {e}"
        st.log(f"  {st.이유}")
    st.남은콜 -= 1
    return st

def n_교정자(st: RunState) -> RunState:
    try:
        교 = 교정자.run_sync(st.글.본문, usage_limits=UsageLimits(request_limit=2)).output
        st.글 = Article(제목=st.글.제목, 본문=교.본문)
        st.고친곳 = 교.고친곳
    except Exception as e:
        st.고친곳 = [f"교정 실패: {e}"]
        st.log(f"  교정 실패: {e}")
    st.남은콜 -= 1
    return st

def n_형태검사(st: RunState) -> RunState:
    st.걸림 = 형태검사(st.글.본문, st)
    return st

def n_편집국장(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.판정 = 편집국장_읽기(st, b)
    st.남은콜 = b["남은콜"]
    st.올림, st.이유 = 올릴까(st.걸림, st.판정)
    문제수 = len(st.판정.문제들) if st.판정 else 0
    st.기록.append({"차례": st.바퀴, "개요": st.개요_, "글": st.글, "고친곳": st.고친곳, "걸림": st.걸림, "판정": st.판정,
                   "올림": st.올림, "이유": st.이유, "조건": (st.주문.온도, st.주문.시작점)})
    st.log(f"  {st.바퀴}차: {'올림' if st.올림 else '탈락'} / {st.이유} / 교정 {len(st.고친곳)}곳 / 편집국장 문제 {문제수}건")
    if not st.올림:
        st.지난문제 = 문제목록_글(st.걸림, st.판정)
    return st

def n_저장(st: RunState) -> RunState:
    st.상태 = "올림" if st.올림 else "탈락"
    if st.올림:
        _올린시리즈[st.주제["시리즈"]] = _올린시리즈.get(st.주제["시리즈"], 0) + 1
    p = 저장(st)
    st.저장경로 = str(p) if p else None
    if p:
        st.log(f"  저장: {p}")
    return st

print("마디 준비 끝")

# ## 21. 갈림길
#
# 함수 하나가 상태를 보고 다음 마디 이름만 돌려준다. 모델을 안 부른다. 상한도 여기서 본다.

def after_주제뽑기(st):
    if st.주제 is None:
        st.상태 = "주제없음"
        return END
    return "재료모으기"

def after_재료모으기(st):
    if (st.모양 == "정보" and not st.사실표) or (st.모양 == "심층" and not st.pages):
        st.log(f"  {st.이유 or '읽은 페이지가 없다'}. 다음 후보로 간다")
        return "주제뽑기"
    return "표기맞추기"

def after_재료판정(st):
    if st.모양 == "정보":
        return "기획자"
    if not st.보강됨 and st.남은콜 >= 보강페이지 + 4:
        발언수 = sum(len(m["말"]) for m in st.발언들)
        if len(st.재료) < 보강목표 or (st.유형 == "제작이야기" and 발언수 == 0):
            st.log("  재료가 얇다. 제작진 이름 · 시즌 리뷰로 더 찾는다")
            return "재료보강"
    if len(st.재료) < MIN_PAGES:
        st.log(f"  쓸 만한 글이 {len(st.재료)}개. 이 주제는 버리고 다음 후보로 간다")
        return "주제뽑기"
    return "관점묶기"

def after_기획자(st):
    if st.개요_ is None:
        st.상태 = "탈락"
        return END
    return "작가"

def after_작가(st):
    if st.글 is None:
        st.상태 = "탈락"
        return END
    return "교정자"

def after_편집국장(st):
    if st.올림:
        return "저장"
    if st.바퀴 >= REWRITE_LIMIT:
        st.log(f"  {REWRITE_LIMIT}번 다 걸렸다. 탈락으로 저장한다")
        return "저장"
    if st.남은콜 < 4:
        st.log("  모델 요청 상한. 탈락으로 저장한다")
        return "저장"
    return "기획자"

def 그냥(다음):
    return lambda st: 다음

GRAPH = {
    "주제뽑기":   (n_주제뽑기,   after_주제뽑기),
    "재료모으기": (n_재료모으기, after_재료모으기),
    "표기맞추기": (n_표기맞추기, 그냥("미리거르기")),
    "미리거르기": (n_미리거르기, 그냥("재료판정")),
    "재료판정":   (n_재료판정,   after_재료판정),
    "재료보강":   (n_재료보강,   after_재료판정),
    "관점묶기":   (n_관점묶기,   그냥("기획자")),
    "기획자":     (n_기획자,     after_기획자),
    "작가":       (n_작가,       after_작가),
    "교정자":     (n_교정자,     그냥("형태검사")),
    "형태검사":   (n_형태검사,   그냥("편집국장")),
    "편집국장":   (n_편집국장,   after_편집국장),
    "저장":       (n_저장,       그냥(END)),
}

def run_graph(st: RunState, 멈출마디=None) -> RunState:
    node = START
    while node != END:
        if st.steps >= MAX_STEPS:
            st.상태 = "상한"
            st.log(f"  마디 수 상한 {MAX_STEPS}. 멈춘다")
            break
        st.steps += 1
        fn, edge = GRAPH[node]
        try:
            st = fn(st)
        except Exception as e:
            import traceback
            st.log(f"  마디 [{node}] 실패: {e}")
            traceback.print_exc()
            st.상태 = "탈락"
            break
        if 멈출마디 and node == 멈출마디:
            break
        node = edge(st)
    return st

print("갈림길 준비 끝. 마디", len(GRAPH), "개")

# ## 22. 저장
#
# 올린 글과 탈락 글을 모두 마크다운으로 남긴다. 원문은 안 남긴다. 표기 출처를 머리에 남긴다.

def 저장(st: RunState):
    if not st.기록:
        return None
    r = st.기록[-1]
    g = r["글"]
    폴더 = OUT / ("올림" if st.올림 else "탈락")
    이름 = re.sub(r"[^\w가-힣ㄱ-ㅎ -]", "", f"{st.유형}_{st.주제['시리즈']}_{st.주제['한줄']}")[:70].strip()
    stamp = datetime.datetime.now().strftime("%m%d_%H%M%S")
    path = 폴더 / f"{이름}_{stamp}.md"

    출처줄 = [f"- {u}" for u in st.사실링크 if u]
    출처줄 += [f"- {x['매체']} — {x['링크']}" for x in st.재료]
    출처줄 += [f"- (발언) {m['매체']} — {m['링크']}" for m in st.발언들]
    출처줄 += [f"- 맥락 — {u}" for u in st.맥락링크]
    출처줄 += [f"- (본문 안 읽음, 링크만) {u}" for u in st.blocked[:20]]

    표 = st.용어표
    표기줄 = [f"- {k} → {v['표기']}  [{v['출처']}]" for k, v in 표.표.items() if v["표기"]] + [f"- {k}  [없음]" for k in 표.없음()]

    로그 = []
    for k in st.기록:
        온도, 시작점 = k["조건"]
        로그.append(f"### {k['차례']}차 — {'올림' if k['올림'] else '탈락'} / {k['이유']}")
        로그.append(f"- 조건: {온도} · {시작점}")
        if k["걸림"]:
            로그.append(f"- 형태 검사: {', '.join(k['걸림'])}")
        if k["고친곳"]:
            로그.append("- 교정자가 고친 곳:")
            로그 += [f"  - {x}" for x in k["고친곳"][:12]]
        if k["판정"]:
            로그.append(f"- 편집국장 {k['판정'].점수}점: {k['판정'].한줄평}")
            로그 += [f"  - ({x.종류}) {x.어디[:60]} — {x.설명}" for x in k["판정"].문제들]

    lines = [f"# {g.제목}", "", g.본문, "", "---", "",
             "## 주제", f"- 유형: {st.유형} ({st.모양}형) · 시리즈: {st.주제['시리즈']} · {st.주제['한줄']}  [{st.주제['src']}]",
             f"- 표기 기준: {GLOSSARIES[st.주제['시리즈']]['기준']}",
             "", "## 표기 출처"] + (표기줄 or ["- 없음"]) + \
            ["", "## 출처"] + (출처줄 or ["- 없음"]) + \
            ["", "## 개요 (최종 바퀴)", f"- 주제: {r['개요'].주제}", "", "## 판정 기록"] + 로그 + \
            ["", f"마디 {st.steps}개 · 모델 요청 {LLM_CALL_CAP - st.남은콜}회"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

print("저장 준비 끝")

# ## 아직 비어 있는 것
#
# - 용어집 다섯 시리즈는 초안이다. 라프텔 자막과 대조하지 않았다. 대조한 항목은 `기준`에 날짜를 적는다
# - 원피스 편 경계 화수는 널리 쓰이는 구분을 옮긴 것이다. 확인이 필요하다
# - `매체이름표` · `차단도메인` — 돌려보고 더 넣는다
# - 위키 임시 표기는 외래어 표기법(구보 다이토)일 수 있다. 그래서 글에 못 쓰게 했다. 자주 나오는 이름은 용어집에 올린다
# - TMDB 회차 점수 · IMDb 회차 평점은 안 붙였다. 회차 · 데이터 유형은 2차다
# - `PASS_SCORE` 70, 분량 폭, 기념일창 7일 — 돌려보고 맞춘다
#
# 랭그래프로 옮길 때: 마디 함수는 `add_node`에, 갈림길 함수는 `add_conditional_edges`에 그대로 꽂는다. `RunState`가 상태 스키마다.


# ======================================================================
# 실행
#   python anime_agent.py check                       # 점검 — AniList · Jikan · 라프텔 · 위키백과 · 검색. 모델 안 부른다
#   python anime_agent.py glossary                    # 용어집 점검 — 시리즈마다 AniList 대표 작품 · 관계도 · 편 화수
#   python anime_agent.py pick --type 감상순서 --n 2   # 주제 뽑기 + 재료 모으기까지만. 모델 안 부른다
#   python anime_agent.py run --type 사람 --n 3        # 올린 글이 n편 될 때까지
#   python anime_agent.py one 블리치 --type 제작이야기  # 시리즈 지정. --person 으로 사람 지정
#   python anime_agent.py zip
# ======================================================================

# ## 23. 점검

def 점검():
    print("[AniList]")
    w = al_media(GLOSSARIES["장송의 프리렌"]["anilist_root"])
    print(f"  프리렌: {w['romaji'] if w else '실패'} / 한글 별칭 {w.get('한글별칭') if w else '-'} / 관계 {len(w['relations']) if w else 0}개")
    print("[Jikan]")
    eps = jikan_episodes(w["idMal"]) if w and w.get("idMal") else None
    print(f"  프리렌 회차: {len(eps) if eps is not None else '죽음(504)'}" + (f" / 필러 {sum(1 for e in eps if e['필러'])}" if eps else ""))
    print("[라프텔]")
    for name in GLOSSARIES:
        items = 라프텔_시리즈(name)
        print(f"  {name}: {len(items)}건" + (f" — {items[0]['이름']}" if items else ""))
    print("[위키백과 ko]")
    w2 = _wiki_extract("ko", "장송의 프리렌")
    print(f"  프리렌 문서: {'있음 ' + str(len(w2['본문'])) + '자' if w2 else '없음'}")
    print(f"  표기 찾기 '久保 帯人': {위키_표기('久保 帯人')}")
    print("[검색]")
    r = 검색("장송의 프리렌 감독 인터뷰", n=5)
    print(f"  결과 {len(r)}개")
    for u in r[:5]:
        print(f"   {'차단' if 차단됐나(u) else '통과'}  {u[:90]}")
    print("[robots]")
    for u in ("https://animeanime.jp/", "https://www.animenewsnetwork.com/", "https://natalie.mu/", "https://bleach.fandom.com/"):
        print(f"  {u:<42} {'O' if robots_ok(u) else 'X'}")

def 용어집점검():
    for name, g in GLOSSARIES.items():
        s = 시리즈(name)
        애니 = _애니만(s["작품들"])
        표 = 표기표(name)
        print(f"\n[{name}] 항목 {len(g['항목'])} · 대표 {s['root']['romaji'] if s['root'] else '실패'} · 시리즈 작품 {len(애니)}개 · 라프텔 {len(s['라프텔'])}건")
        for w in 애니[:14]:
            t = 표.작품(w)
            print(f"   {'본편' if w['본편사슬'] and w.get('format') in ('TV','ONA') else '    '} {(w.get('start') or {}).get('y')} {w.get('format'):<8} {w['romaji'][:44]:<46} → {t['표기'] or '(없음)'} [{t['출처']}]")
        편 = 용어집_편목록(name)
        if 편:
            print(f"   편 {len(편)}개: " + ", ".join(f"{n}({a}~{b or ''})" for n, a, b, _ in 편[:8]) + (" …" if len(편) > 8 else ""))

# ## 24. 뽑기만 — 모델 없이 주제 뽑기와 재료 모으기까지 본다

def 뽑기만(유형, n=2):
    멈춤 = "재료판정" if 유형표[유형] == "정보" else "미리거르기"
    for i in range(n):
        print(f"\n===== 뽑기 {i+1}/{n} · {유형} =====")
        st = RunState(유형=유형, 남은콜=0)
        st = run_graph(st, 멈출마디=멈춤)
        if st.주제 is None:
            print("쓸 만한 주제를 못 찾았다"); continue
        if st.모양 == "정보":
            print(st.사실표[:2500] if st.사실표 else f"  재료 없음: {st.이유}")
        else:
            print(f"  재료 후보 페이지 {len(st.pages)}개 (검색 링크 {len(st.links)}, robots 막힘 {len(st.blocked)}):")
            for p in st.pages:
                print(f"   - [{p.get('매체') or 매체이름(p['url'])}] {p['제목'][:60]}  {len(p['본문'])}자")
        print("  표기표:\n" + "\n".join("   " + x for x in st.용어표.text(임시포함=True).splitlines()[:24]) if st.용어표 else "")

# ## 25. 돌리기

def run(유형, 목표편수=목표편수, 시도상한=시도상한):
    결과, 시도, 올림수 = [], 0, 0
    시작 = time.time()
    while 올림수 < 목표편수 and 시도 < 시도상한:
        시도 += 1
        print(f"\n===== 시도 {시도}/{시도상한} · {유형} · 올림 {올림수}/{목표편수} · {int((time.time()-시작)/60)}분 =====")
        st = run_graph(RunState(유형=유형))
        결과.append(st)
        if st.상태 == "주제없음":
            print("쓸 만한 주제를 못 찾았다"); break
        if st.올림:
            올림수 += 1
    print(f"\n끝. 올림 {올림수}편 / 탈락 {sum(1 for s in 결과 if s.상태 == '탈락')}편 / 시도 {시도}회 / {int((time.time()-시작)/60)}분")
    return 결과

# ## 25-1. 시리즈를 지정해서 한 편
#
# 시리즈 이름(용어집 키)과 유형을 준다. 사람 유형은 --person 으로 이름(어느 표기든)을 준다.

def run_one(시리즈이름, 유형, 사람=None):
    name = next((k for k in GLOSSARIES if k == 시리즈이름 or 시리즈이름 in k or any(시리즈이름.lower() == x.lower() for x in GLOSSARIES[k]["title_keys"])), None)
    if not name:
        print(f"용어집에 없는 시리즈다: {시리즈이름}. 있는 것: {', '.join(GLOSSARIES)}")
        return None
    후보 = [c for c in 주제_후보목록(유형) if c["시리즈"] == name]
    if 유형 == "사람" and 사람:
        k = _norm_roma(사람)
        후보 = [c for c in 후보 if k in _norm_roma(c["사람"]["full"]) or k in _norm_roma(c["사람"].get("native") or "") or k == _norm_roma(용어집_찾기(c["사람"]["full"], name) or "")]
    if not 후보:
        print("그 시리즈로 만들 후보가 없다")
        return None
    c = {**후보[0], "src": "지정"}
    st = run_graph(RunState(유형=유형, 지정후보=c))
    print(f"\n끝. {st.상태} / {st.이유} / 모델 요청 {LLM_CALL_CAP - st.남은콜}회")
    return st

# ## 26. zip으로 묶기

def zip_outputs():
    zip_path = OUT.parent / f"anime_{datetime.datetime.now():%m%d_%H%M}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob("*.md"):
            z.write(p, p.relative_to(OUT))
    print("zip:", zip_path)
    return zip_path


def main(argv=None):
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="애니 콘텐츠 크리에이팅 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="AniList · Jikan · 라프텔 · 위키백과 · 검색 점검. 모델 안 부른다")
    sub.add_parser("glossary", help="용어집 점검. 시리즈마다 관계도 · 표기 매칭을 본다. 모델 안 부른다")
    p = sub.add_parser("pick", help="주제 뽑기 + 재료 모으기까지만. 모델 안 부른다")
    p.add_argument("--type", dest="유형", default="감상순서", choices=list(유형표))
    p.add_argument("--n", type=int, default=2)
    a = sub.add_parser("run", help="올린 글이 n편 될 때까지 돌린다")
    a.add_argument("--type", dest="유형", default="감상순서", choices=list(유형표))
    a.add_argument("--n", type=int, default=목표편수)
    a.add_argument("--tries", type=int, default=시도상한)
    o = sub.add_parser("one", help="시리즈를 지정해서 한 편. 예: one 블리치 --type 제작이야기")
    o.add_argument("series")
    o.add_argument("--type", dest="유형", default="감상순서", choices=list(유형표))
    o.add_argument("--person", default=None, help="사람 유형에서 사람 이름 (어느 표기든)")
    sub.add_parser("zip", help="out/anime 폴더를 zip으로 묶는다")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        점검()
    elif args.cmd == "glossary":
        용어집점검()
    elif args.cmd == "pick":
        뽑기만(args.유형, n=args.n)
    elif args.cmd == "run":
        run(args.유형, 목표편수=args.n, 시도상한=args.tries)
        zip_outputs()
    elif args.cmd == "one":
        run_one(args.series, args.유형, 사람=args.person)
    elif args.cmd == "zip":
        zip_outputs()


if __name__ == "__main__":
    main()
