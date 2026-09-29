# # Movie Review agent V3.1 — 영화 리뷰 에이전트 (평론 기반 · LangGraph)
#
# 키는 `.env` 또는 환경변수 `LLM_KEY` · `TMDB_KEY`에서 읽는다. 결과는 `agent/out/movie_review/올림|탈락/`에 쌓인다.
#
# `pip install -r ../requirements.txt`
#
# **V3.0 — 뼈대를 새로 짰다 (PM 9/28)**
#
# V2.0까지는 TMDB 줄거리와 위키 절만 보고 리뷰를 썼다. 평론을 한 편도 안 읽었다. 그래서 판단이 어디서 오는지가 없었고,
# 프롬프트가 "본 사람만 아는 것은 쓰지 마라"로 막으니 남는 것이 "가장 큰 힘이다" 같은 이름표 판단이었다. 원인은 재료였다.
# V3.0은 음악 v4.0 · 애니 작품리뷰와 같은 심층형 뼈대다. 평론을 모아 관점을 묶고, 기획자가 뼈대를 짜고, 작가가 쓰고,
# 편집국장이 평론 원문과 대조한다. 제품 원칙(근거 콘텐츠 안에서만 · 출처 표시)과 맞는다.
#
# ```
# 재료모으기(TMDB · 위키 · 씨네21 · 검색) → 미리거르기 → 재료판정 ─(평 모자람)→ 재료보강 → 관점묶기 → 기획자 → 작가 → 교정자 → 형태검사 → 편집국장
#                                                                                            ↑                                    │
#                                                                                            └──── 걸리면 문제 목록만 들고 (세 번) ──┘
#                                                                                                                                 └→ 저장 → 끝
# ```
#
# 재료
# - 사실: TMDB(개봉 · 감독 · 출연 · 갈래 · 상영시간 · 등급 · 줄거리) + 위키백과 ko(제작 · 반응 · 영향 절).
# - 평론: 씨네21(기사 검색 `/search/news/` + 영화 페이지의 전문가 한줄평 `.expert_star_list`) + 검색(DuckDuckGo)으로 찾은 매체.
#   robots.txt를 본다. AI 봇(GPTBot · ClaudeBot · CCBot 등)을 따로 막아 둔 매체는 우리 이름으로 통과되더라도 뺀다(PM 9/28).
#   Variety · Guardian · NYT · IndieWire · 한겨레 · 경향 · 중앙 · 한국일보 · 연합이 그렇다. 씨네21 · 맥스무비 · 무비스트 · 익스트림무비 · 동아 · 국제 · 한경 ·
#   Little White Lies · Screen Daily · The Playlist · BFI · MUBI · Cinema Scope는 열려 있다(9/28 확인).
#   나무위키 · 블로그 · 커뮤니티 · 유튜브 · 예매 사이트는 차단 목록이다.
# - 매체당 평론 두 편까지(PER_MEDIA). 씨네21 한줄평은 평론 수에 안 센다. 관점표에 따로 붙는다.
#
# 마디 · 갈림길 · 상태 이름은 음악 v4.0과 같다. 다른 것은 재료 모으기 마디 하나와 프롬프트의 낱말(곡 → 장면, 앨범 → 영화)이다.
# 흐름은 LangGraph StateGraph다. 갈림길은 마디 안에서 돌고 고른 이름을 `st.다음`에 적는다(음악 v4.0과 같은 이유).
#
# 백엔드(`backend/app/integrations/movie_review_agent.py`)가 부르는 모양은 V2.0과 같다.
#   `VERSION` · 동기 `fetch_material(title, year)` · `async produce(material)` → dict
#   {"상태": 대상아님 | 재료부족 | 판단보류 | 올림 | 탈락보관, "이유", "로그": [{"단계", "회차", "결과", ...}], "글": {title, body, sources}, "주문", "확인필요", "최고판"}
#
# **V3.1** — 주문 어김은 반려 · 감점 사유가 아니다(PM 9/29). 편집국장은 종류 "주문"으로 적고, 코드는 사소로 센다. 점수를 깎지 않는다.
# 주문끼리 부딪쳐(상황부터 + 사실 먼저 같은 것) 작가가 하나를 어길 수밖에 없는 조합이 나온다. 그걸로 글을 떨어뜨리지 않는다.
#
# 앞 판 기억(V1.9) · 글의 꼴(V2.0) · 최고 판 저장은 그대로 들고 왔다. 모델은 gpt-6-luna(9/24).

# ## 1. 설정
#
# 아래 값만 고치면 된다.

import os, re, io, json, time, random, zipfile, datetime, pathlib, urllib.parse, sys, html as htmlmod
import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# agent/.env → 이 파일 옆 .env 순서로 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다
_이파일 = globals().get("__file__")
HERE = pathlib.Path(_이파일).resolve().parent if _이파일 else pathlib.Path.cwd()   # 노트북에서는 지금 폴더
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")

# ───── 여기서 고친다 ─────────────────────────────────────────────
VERSION     = "V3.1"
MODEL       = "openai:gpt-6-luna"             # 음악 · 애니와 같은 모델. "openai:" 접두어는 Responses API로 간다
KEY_ENV     = "OPENAI_API_KEY"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

PAGE_LIMIT    = 10     # 영화 하나에 재료로 읽어볼 페이지 수 (씨네21 기사 + 검색으로 찾은 글)
후보링크배수  = 5      # robots · AI 봇 규칙으로 막히는 곳이 많아 검색 후보를 넉넉히 모은다
씨네21기사수  = 6      # 씨네21 기사 검색에서 읽어볼 기사 수. 한 쪽에 10건
PER_MEDIA     = 2      # 매체당 남길 평론 개수 (지침서 §5-3)
MIN_REVIEWS   = 2      # 평론이 이만큼 안 모이면 재료부족이다
보강목표      = 3      # 평론이 이만큼 안 모이면 재료 보강 마디가 한 번 더 찾는다
보강페이지    = 6      # 보강에서 더 읽어볼 페이지 수
동시판정      = 5      # 재료 판정을 한 번에 몇 페이지씩 모델에 보낼지
모델판정상한  = 8      # 큰 모델에 보낼 페이지 수 상한
발췌글자      = 1800   # 기획자에게 주는 평론 원문 발췌. 평론마다 이만큼
REWRITE_LIMIT = 3      # 다시 쓰기 상한
PASS_SCORE    = 65     # 편집국장 점수가 이 아래면 다시 쓴다
마지막안전점수 = 50     # 마지막 바퀴에서는 여기까지 봐준다. 치명이 없으면 올린다
기획재시도    = 1      # 기획자 · 작가가 실패했을 때 다시 부르는 횟수
LLM_CALL_CAP  = 36     # 영화 하나에 허용할 모델 요청 수 (재료 8 + 보강 6 + 한 바퀴 4 × 세 번 + 여유)
MAX_STEPS     = 60     # 마디 수 상한
CONTACT       = "menu-project@example.com"   # UA에 넣는 연락처. 실제 주소로 바꾼다

앞판기억       = True   # 편집국장이 앞 판 점수 · 문제 목록 · 앞 판 글을 받는다
최고판저장     = True   # 마지막 판이 탈락이면 기록 중 점수가 제일 높은 판의 글을 저장한다
조건돌려쓰기   = True   # 여러 편 돌릴 때 접근 · 온도 · 시작점을 돌려 쓴다
문장길이편차문턱 = 0.28  # 문장 길이의 편차 / 평균이 이 아래면 "문장 길이 단조"로 건다
문단길이편차문턱 = 0.15  # 문단 길이의 편차 / 평균이 이 아래면 "문단 길이 단조"로 건다
콜랩_끝나면zip = True   # 콜랩이면 끝날 때 out/movie_review 를 zip 으로 묶어 내려준다
AI봇확인       = True   # 모르는 매체는 robots.txt에서 AI 봇을 따로 막았는지 보고, 막았으면 뺀다

# 재료 조건 (V2.0 그대로)
MIN_SYNOPSIS_CHARS = 150
EXCLUDE_GENRES  = {"다큐멘터리", "가족", "애니메이션", "TV 영화"}
EXCLUDE_RATINGS = {"ALL", "전체관람가", "7", "7세이상관람가", "G"}
MIN_VOTES, PRAISE_LINE, CRITICISM_LINE = 500, 7.0, 5.5   # 대중 평가 갈래 — 임시 기준
분량 = (1200, 2000)     # 본문 글자 수. 유형 하나라 접근별 폭은 안 둔다
# ─────────────────────────────────────────────────────────────

def _secret(name):
    v = os.environ.get(name)
    if not v or not v.strip():
        raise RuntimeError(f"환경변수 '{name}'이 없다. agent/.env 파일에 {name}=... 을 적거나 셸에서 환경변수로 넣는다. (agent/env.env.example 참고)")
    return v.strip()

_DRY = len(sys.argv) > 1 and sys.argv[1] in ("check", "pick")
TMDB_KEY = os.environ.get("TMDB_KEY", "").strip() or ("dry-run" if _DRY else _secret("TMDB_KEY"))
try:
    os.environ[KEY_ENV] = _secret("LLM_KEY")
    print("키 읽음. 길이:", len(os.environ[KEY_ENV]))
except RuntimeError:
    if not _DRY:
        raise
    os.environ[KEY_ENV] = "dry-run"
    print("키 없음. check · pick 만 된다")

UA = f"menu-movie-review-agent/3.0 ( {CONTACT} )"
HEADERS = {"User-Agent": UA}

OUT = HERE.parent / "out" / "movie_review"
(OUT / "올림").mkdir(parents=True, exist_ok=True)
(OUT / "탈락").mkdir(parents=True, exist_ok=True)

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

random.seed()
print("설정 끝")

# ## 2. 매체 목록과 차단 목록
#
# 검색으로 재료를 모을 때는 차단 목록에 걸린 주소만 뺀다. 나머지는 robots.txt를 보고 읽는다.
# AI 봇을 따로 막은 매체는 우리 UA로는 통과돼도 뺀다. 아래 `AI봇막은매체`는 9/28에 확인한 것이고, 모르는 매체는 `AI봇확인`이 그때그때 본다.

from urllib.robotparser import RobotFileParser
from concurrent.futures import ThreadPoolExecutor
import threading

MEDIA = [
    {"name": "씨네21",             "domain": "cine21.com",         "lang": "ko"},
    {"name": "맥스무비",           "domain": "maxmovie.com",       "lang": "ko"},
    {"name": "무비스트",           "domain": "movist.com",         "lang": "ko"},
    {"name": "익스트림무비",       "domain": "extmovie.com",       "lang": "ko"},
    {"name": "동아일보",           "domain": "donga.com",          "lang": "ko"},
    {"name": "국제신문",           "domain": "kookje.co.kr",       "lang": "ko"},
    {"name": "한국경제",           "domain": "hankyung.com",       "lang": "ko"},
    {"name": "Little White Lies", "domain": "lwlies.com",         "lang": "en"},
    {"name": "Screen Daily",      "domain": "screendaily.com",    "lang": "en"},
    {"name": "The Playlist",      "domain": "theplaylist.net",    "lang": "en"},
    {"name": "BFI",               "domain": "bfi.org.uk",         "lang": "en"},
    {"name": "MUBI",              "domain": "mubi.com",           "lang": "en"},
    {"name": "Cinema Scope",      "domain": "cinema-scope.com",   "lang": "en"},
]
# 9/28 확인: AI 봇에 Disallow: / 를 건 곳. 우리 UA로는 열리지만 뜻이 분명해서 뺀다
AI봇막은매체 = ["variety.com", "theguardian.com", "nytimes.com", "indiewire.com", "hollywoodreporter.com", "slantmagazine.com",
             "avclub.com", "empireonline.com", "vulture.com", "ign.com", "collider.com", "filmcomment.com",
             "hani.co.kr", "khan.co.kr", "joongang.co.kr", "chosun.com", "hankookilbo.com", "mk.co.kr", "yna.co.kr",
             "rogerebert.com"]   # rogerebert 는 robots.txt 부터 403 이라 확인이 안 된다. 뺀다
MEDIA_BY_DOMAIN = {m["domain"]: m for m in MEDIA}

차단도메인 = [
    "namu.wiki", "namu.moe", "thewiki.kr",
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com", "in.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "wordpress.com", "blogspot.com", "medium.com", "note.com", "ameblo.jp", "hatenablog.com", "hatena.ne.jp",
    "dcinside.com", "fmkorea.com", "theqoo.net", "ruliweb.com", "clien.net", "instiz.net", "pann.nate.com", "reddit.com", "quora.com", "tumblr.com",
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com",
    "cgv.co.kr", "lottecinema.co.kr", "megabox.co.kr", "kobis.or.kr", "kmdb.or.kr",
    "watcha.com", "watchapedia.co.kr", "netflix.com", "tving.com", "wavve.com", "coupangplay.com", "disneyplus.com",
    "imdb.com", "themoviedb.org", "letterboxd.com", "rottentomatoes.com", "metacritic.com", "wikipedia.org",
    "amazon.com", "coupang.com", "aladin.co.kr", "yes24.com",
]
AI봇들 = ["GPTBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai", "CCBot", "Google-Extended", "PerplexityBot", "Bytespider", "Applebot-Extended", "meta-externalagent"]

def _host(url):
    return urllib.parse.urlparse(url).netloc.lower().replace("www.", "")

def _도메인맞나(h, d):
    return h == d or h.endswith("." + d)

def 차단됐나(url):
    h = _host(url)
    return any(_도메인맞나(h, d) for d in 차단도메인 + AI봇막은매체)

매체이름표 = {"cine21.com": "씨네21", "wikipedia.org": "위키백과"}

def media_of(url):
    h = _host(url)
    for dom, m in MEDIA_BY_DOMAIN.items():
        if _도메인맞나(h, dom):
            return m
    return None

def 매체이름(url):
    m = media_of(url)
    if m:
        return m["name"]
    h = _host(url)
    for d, n in 매체이름표.items():
        if _도메인맞나(h, d):
            return n
    return "한 매체"

print(f"열린 매체 {len(MEDIA)}곳, AI 봇 막은 매체 {len(AI봇막은매체)}곳, 차단 도메인 {len(차단도메인)}개")

# ## 3. robots.txt 확인
#
# 막아둔 곳은 본문을 안 읽는다. 링크만 남긴다. robots.txt를 못 가져오면 막힌 것으로 본다. 404면 허용이다.
# 모르는 매체는 AI 봇을 따로 막았는지도 본다. 막았으면 우리도 안 읽는다.

_robots = {}
_ai막음 = {}

def _robots_of(url):
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = RobotFileParser(); rp.set_url(base + "/robots.txt")
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
            if r.status_code == 404:
                rp.parse([])
            elif r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp = None
        except Exception:
            rp = None
        _robots[base] = rp
        if rp is not None and AI봇확인 and not media_of(url):
            try:
                _ai막음[base] = any(not rp.can_fetch(b, base + "/") for b in AI봇들)
            except Exception:
                _ai막음[base] = False
    return base, _robots[base]

def robots_ok(url):
    base, rp = _robots_of(url)
    if rp is None or _ai막음.get(base):
        return False
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return False

def crawl_delay(url):
    base, rp = _robots_of(url)
    if rp is None:
        return 1.0
    try:
        d = rp.crawl_delay(UA)
        return float(d) if d else 1.0
    except Exception:
        return 1.0

_site_lock, _site_last, _lock_guard = {}, {}, threading.Lock()

def _site_wait(url):
    host = _host(url)
    with _lock_guard:
        lk = _site_lock.setdefault(host, threading.Lock())
    with lk:
        gap = crawl_delay(url) - (time.time() - _site_last.get(host, 0))
        if gap > 0:
            time.sleep(gap)
        _site_last[host] = time.time()

def fetch(url, timeout=20, params=None):
    if not robots_ok(url):
        return None
    _site_wait(url)
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

print("robots 확인 준비 끝")

# ## 4. 이름 맞추기

def _norm(s):
    s = htmlmod.unescape(s or "").lower()
    return re.sub(r"[\W_]+", "", s)

def 같은이름(a, b, 느슨=False):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if 느슨:
        return (len(a) >= 4 and a in b) or (len(b) >= 4 and b in a)
    return False

# ## 5. 사실 재료 — TMDB + 위키백과 (V2.0 그대로)

from typing import Literal, List, Optional
from pydantic import BaseModel, Field, ConfigDict

Topic     = Literal["애니", "영화", "음악"]
Reception = Literal["호평이 많다", "평가가 갈린다", "혹평이 많다"]

class Material(BaseModel):
    tmdb_id: int | None = None
    title: str
    year: int | None = None
    director: str | None = None
    cast: list[str] = []
    genres: list[str] = []
    runtime_min: int | None = None
    rating: str | None = None
    release_date: str | None = None
    synopsis: str = ""
    director_past_works: list[str] = []
    source_work: str | None = None
    context: str = ""                    # 주변 이야기. 위키백과 제작·반응·영향·여담 절
    context_source: str | None = None
    reception: Reception | None = None
    reception_source: str | None = None
    source_links: list[str] = []

TMDB = "https://api.themoviedb.org/3"
WIKI_API = "https://ko.wikipedia.org/w/api.php"
CONTEXT_SECTIONS = ("제작", "개봉", "반응", "평가", "흥행", "영향", "여담", "기타", "논란", "패러디", "수상")
CONTEXT_MAX_CHARS = 3000

def _tmdb(path, **params):
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "ko-KR")
    r = requests.get(f"{TMDB}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def search_movie(title, year=None):
    params = {"query": title}
    if year:
        params["year"] = year
    hits = _tmdb("/search/movie", **params).get("results", [])
    if not hits and year:
        hits = _tmdb("/search/movie", query=title).get("results", [])
    return hits[0]["id"] if hits else None

def _kr_certification(movie_id):
    data = _tmdb(f"/movie/{movie_id}/release_dates", language="en-US")
    for row in data.get("results", []):
        if row.get("iso_3166_1") == "KR":
            for rel in row.get("release_dates", []):
                if rel.get("certification"):
                    return rel["certification"]
    return None

def _director_past_works(person_id, exclude_id, limit=4):
    data = _tmdb(f"/person/{person_id}/movie_credits")
    works = [c for c in data.get("crew", []) if c.get("job") == "Director" and c.get("id") != exclude_id]
    works.sort(key=lambda c: c.get("release_date") or "", reverse=True)
    return [c["title"] for c in works[:limit]]

def decide_reception(avg, votes):
    if avg is None or votes is None or votes < MIN_VOTES:
        return None
    if avg >= PRAISE_LINE:
        return "호평이 많다"
    if avg <= CRITICISM_LINE:
        return "혹평이 많다"
    return "평가가 갈린다"

_RE_REF, _RE_TEMPLATE = re.compile(r"<ref[^>]*/>|<ref.*?</ref>", re.S), re.compile(r"\{\{[^{}]*\}\}")
_RE_LINK, _RE_BOLD, _RE_HEAD = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]"), re.compile(r"'{2,}"), re.compile(r"^==+ ?(.+?) ?==+", re.M)

def fetch_wiki_context(title, year=None):
    """한국어 위키백과에서 제작 · 반응 · 영향 · 여담 절만 모아 온다. 줄거리 절은 안 가져온다."""
    try:
        q = f"{title} {year} 영화" if year else f"{title} 영화"
        r = requests.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": q, "format": "json"}, headers=HEADERS, timeout=20).json()
        hits = r.get("query", {}).get("search", [])
        if not hits:
            return "", None
        page = hits[0]["title"]
        r = requests.get(WIKI_API, params={"action": "parse", "page": page, "prop": "wikitext", "format": "json"}, headers=HEADERS, timeout=20).json()
        text = r.get("parse", {}).get("wikitext", {}).get("*", "")
    except Exception:
        return "", None
    text = _RE_REF.sub("", text)
    for _ in range(3):
        text = _RE_TEMPLATE.sub("", text)
    text = _RE_BOLD.sub("", _RE_LINK.sub(r"\1", text))
    heads = list(_RE_HEAD.finditer(text))
    chunks = []
    for i, h in enumerate(heads):
        name = h.group(1).strip()
        if not any(k in name for k in CONTEXT_SECTIONS):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"\n{3,}", "\n\n", text[h.end():end].strip())
        if body:
            chunks.append(f"[{name}]\n{body}")
    return "\n\n".join(chunks)[:CONTEXT_MAX_CHARS], "https://ko.wikipedia.org/wiki/" + page.replace(" ", "_")

def fetch_material(title, year=None, today=None):
    """제목으로 찾아서 재료를 만든다. 백엔드 어댑터가 이 이름으로 부른다(동기)."""
    mid = search_movie(title, year)
    if mid is None:
        return None
    return fetch_material_by_id(mid, today=today, fallback_title=title)

def fetch_material_by_id(mid, today=None, fallback_title=""):
    today = today or datetime.date.today().isoformat()
    d = _tmdb(f"/movie/{mid}", append_to_response="credits")
    credits = d.get("credits", {})
    director, director_id = None, None
    for c in credits.get("crew", []):
        if c.get("job") == "Director":
            director, director_id = c.get("name"), c.get("id")
            break
    year = int(d["release_date"][:4]) if d.get("release_date") else None
    reception = decide_reception(d.get("vote_average"), d.get("vote_count"))
    context, context_url = fetch_wiki_context(d.get("title") or fallback_title, year)
    links = [f"https://www.themoviedb.org/movie/{mid}"] + ([context_url] if context_url else [])
    return Material(
        tmdb_id=mid, title=d.get("title") or fallback_title, year=year, director=director,
        cast=[c["name"] for c in credits.get("cast", [])[:5]], genres=[g["name"] for g in d.get("genres", [])],
        runtime_min=d.get("runtime"), rating=_kr_certification(mid), release_date=d.get("release_date"),
        synopsis=d.get("overview", "") or "", director_past_works=(_director_past_works(director_id, mid) if director_id else []),
        source_work=None, context=context, context_source=context_url,
        reception=reception, reception_source=(f"TMDB 평점 분포 ({today} 기준)" if reception else None), source_links=links)

def genre_ok(m):
    hit = set(m.genres) & EXCLUDE_GENRES
    if hit:
        return False, f"제외 갈래다 ({', '.join(sorted(hit))})"
    if m.rating and m.rating.strip() in EXCLUDE_RATINGS:
        return False, f"제외 등급이다 ({m.rating})"
    return True, ""

def enough_material(m):
    if len(m.synopsis) < MIN_SYNOPSIS_CHARS:
        return False, f"줄거리가 짧아 리뷰를 못 쓴다 ({len(m.synopsis)}자)"
    if not m.genres and not m.director and not m.source_work:
        return False, "갈래 · 감독 · 원작이 모두 없어 근거를 댈 수 없다"
    return True, ""

def material_to_text(m):
    rows = []
    def add(label, value):
        if value:
            rows.append(f"{label}: {value}")
    add("제목", m.title); add("개봉", m.release_date); add("감독", m.director); add("출연", ", ".join(m.cast))
    add("갈래", ", ".join(m.genres)); add("상영시간", f"{m.runtime_min}분" if m.runtime_min else None); add("등급", m.rating)
    add("원작", m.source_work); add("감독 이전 작품", ", ".join(m.director_past_works)); add("줄거리", m.synopsis)
    rows.append(f"대중 평가 갈래: {m.reception} (출처: {m.reception_source})" if m.reception else "대중 평가 갈래: 없음 — 대중 평가를 쓰지 않는다")
    rows.append(f"주변 이야기 (출처: {m.context_source}):\n{m.context}" if m.context else "주변 이야기: 재료 없음 — 쓰지 않는다")
    return "\n".join(rows)

# 무작위 영화 뽑기 (V2.0 그대로)
TMDB_GENRE_ID = {"다큐멘터리": 99, "가족": 10751, "애니메이션": 16, "TV 영화": 10770}
DISCOVER_MIN_VOTES, DISCOVER_YEAR_RANGE, DISCOVER_MAX_PAGE = 100, (1980, 2026), 30
DISCOVER_SORTS = ["popularity.desc", "vote_average.desc", "vote_count.desc", "primary_release_date.desc", "revenue.desc"]

def pick_random_movies(n=5, seed=None):
    rng = random.Random(seed)
    without = ",".join(str(TMDB_GENRE_ID[g]) for g in EXCLUDE_GENRES if g in TMDB_GENRE_ID)
    picked, seen, tries = [], set(), 0
    while len(picked) < n and tries < n * 6:
        tries += 1
        y0 = rng.randint(*DISCOVER_YEAR_RANGE); y1 = min(y0 + rng.randint(0, 5), DISCOVER_YEAR_RANGE[1])
        params = dict(sort_by=rng.choice(DISCOVER_SORTS), page=rng.randint(1, DISCOVER_MAX_PAGE), without_genres=without, include_adult="false", language="ko-KR")
        params["vote_count.gte"] = DISCOVER_MIN_VOTES
        params["primary_release_date.gte"], params["primary_release_date.lte"] = f"{y0}-01-01", f"{y1}-12-31"
        try:
            rows = _tmdb("/discover/movie", **params).get("results", [])
        except Exception:
            continue
        rows = [r for r in rows if r.get("overview") and r["id"] not in seen]
        if not rows:
            continue
        r = rng.choice(rows); seen.add(r["id"])
        picked.append({"id": r["id"], "title": r.get("title"), "year": (r.get("release_date") or "")[:4]})
    return picked

print("사실 재료 준비 끝")

# ## 6. 평론 재료 — 씨네21
#
# 씨네21은 robots.txt에 막는 경로가 없다(9/28). 두 곳을 읽는다.
# - 영화 페이지 `/movie/info/?movie_id=` — 전문가 한줄평(`section.expert_star li .reviewer .name · .num · .review`). 이름 · 점수 · 한 줄.
# - 기사 검색 `/search/news/?query=&p=` — 항목마다 `.news_title` · `.byline span(날짜 · 필자)` · `a[href*=mag_id]`. 기사 본문은 `#news_content`, 필자는 `.byline .writer`.
# 통합 검색 `/search/?query=`에서 영화 항목(`/movie/info/?movie_id=`)을 찾아 movie_id를 얻는다. 제목과 연도로 고른다.

from bs4 import BeautifulSoup

CINE21 = "https://cine21.com"

def cine21_영화찾기(title, year=None, director=None):
    """통합 검색에서 영화 항목을 찾는다. 같은 제목이 여럿이면 감독 이름이 든 것을 고른다. 감독도 연도도 안 맞으면 None이다."""
    html = fetch(f"{CINE21}/search/", params={"query": title})
    if not html:
        return None
    s = BeautifulSoup(html, "lxml")
    후보 = []
    for a in s.select("a[href*='/movie/info/']"):
        m = re.search(r"movie_id=(\d+)", a.get("href") or "")
        if not m:
            continue
        블록 = a.find_parent("li") or a.find_parent("div") or a
        글 = 블록.get_text(" ", strip=True)
        이름 = a.get_text(" ", strip=True) or 글[:40]
        y = re.search(r"\((\d{4})\)", 글)
        d = re.search(r"감독\s+([^\s]+(?:\s+[^\s]+)?)\s+출연", 글)
        후보.append({"movie_id": m.group(1), "이름": 이름, "year": int(y.group(1)) if y else None, "감독": (d.group(1) if d else ""), "글": 글[:120]})
    if not 후보:
        return None
    같은 = [c for c in 후보 if 같은이름(c["이름"], title, 느슨=True)] or 후보
    if director:
        맞는 = [c for c in 같은 if _norm(director) in _norm(c["글"])]
        if 맞는:
            return 맞는[0]
        if any(c["감독"] for c in 같은):        # 감독이 적혀 있는데 하나도 안 맞으면 다른 영화다
            return None
    if year:
        같은.sort(key=lambda c: (0 if c["year"] == year else (1 if c["year"] is None else 2)))
    return 같은[0]

def cine21_한줄평(movie_id):
    """전문가 한줄평. [{이름, 점수, 한줄}]. 평론 수에 안 센다. 관점표에 따로 붙는다."""
    html = fetch(f"{CINE21}/movie/info/", params={"movie_id": movie_id})
    if not html:
        return [], None
    s = BeautifulSoup(html, "lxml")
    out = []
    for li in s.select("section.expert_star li"):
        이름 = li.select_one(".reviewer .name"); 점수 = li.select_one(".reviewer .num"); 한줄 = li.select_one(".review")
        if 이름 and 한줄 and 한줄.get_text(strip=True):
            out.append({"이름": 이름.get_text(strip=True), "점수": (점수.get_text(strip=True) if 점수 else ""), "한줄": 한줄.get_text(" ", strip=True)})
    감독 = ""
    for li in s.select(".movie_detail_info .info_list li, .info_list li"):
        t = li.select_one(".title, p.title")
        if t and "감독" in t.get_text(strip=True):
            감독 = li.get_text(" ", strip=True).replace(t.get_text(strip=True), "", 1).strip()
            break
    시놉 = ""
    node = s.find(string=re.compile(r"^\s*시놉시스\s*$"))
    if node:
        블록 = node.find_parent()
        다음 = 블록.find_next_sibling() if 블록 else None
        시놉 = 다음.get_text("\n", strip=True)[:1500] if 다음 else ""
    return out, {"url": f"{CINE21}/movie/info/?movie_id={movie_id}", "시놉시스": 시놉, "감독": 감독}

def cine21_기사찾기(title, director=None, n=씨네21기사수):
    """기사 검색. 제목이 기사 제목이나 요약에 든 것만. 최신 순이 아니라 관련 순이다."""
    말 = [title] + ([f"{title} {director.split()[0]}"] if director else []) + [f"{title} 리뷰", f"{title} 평론"]
    seen, out = set(), []
    for q in dict.fromkeys(말):
        for p in (1, 2):
            html = fetch(f"{CINE21}/search/news/", params={"query": q, "p": p})
            if not html:
                break
            s = BeautifulSoup(html, "lxml")
            items = s.select("div.list_with_thumb_item_l, .news_contents")
            찾음 = 0
            for it in s.select("a[href*='mag_id=']"):
                m = re.search(r"mag_id=(\d+)", it["href"])
                if not m or m.group(1) in seen:
                    continue
                제목 = it.select_one(".news_title"); 요약 = it.select_one(".news_article"); 바이 = it.select(".byline span")
                제목글 = 제목.get_text(" ", strip=True) if 제목 else it.get_text(" ", strip=True)[:80]
                본문미리 = 요약.get_text(" ", strip=True) if 요약 else ""
                if not (같은이름(title, 제목글, 느슨=True) or _norm(title) in _norm(본문미리)):
                    continue
                seen.add(m.group(1)); 찾음 += 1
                out.append({"mag_id": m.group(1), "제목": 제목글, "미리": 본문미리[:300],
                            "날짜": (바이[0].get_text(strip=True) if bar_ok(바이, 0) else ""), "필자": (바이[1].get_text(strip=True) if bar_ok(바이, 1) else ""),
                            "url": f"{CINE21}/news/view/?mag_id={m.group(1)}"})
            if 찾음 == 0 or len(out) >= n * 2:
                break
    # 리뷰 · 평론 · 20자평 · 프리뷰가 제목에 든 것을 앞에. 제작기 · 인터뷰도 재료다 (감독 말)
    def 순위(x):
        t = x["제목"] + " " + x["미리"]
        return (0 if re.search(r"리뷰|평론|평론가|비평|20자평|프리뷰|시사", t) else (1 if re.search(r"인터뷰|감독|제작기", t) else 2))
    out.sort(key=순위)
    return out[:n * 2]          # 본문을 읽고 감독 이름으로 거르면 줄어든다. 넉넉히 준다

def bar_ok(spans, i):
    return len(spans) > i and spans[i].get_text(strip=True)

def cine21_기사읽기(item):
    html = fetch(item["url"])
    if not html:
        return None
    s = BeautifulSoup(html, "lxml")
    body = s.select_one("#news_content")
    if not body:
        return None
    for t in body(["script", "style", "iframe", "figure"]):
        t.decompose()
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    writer = s.select_one(".byline .writer"); date = s.select_one(".byline .date")
    필자 = re.sub(r"^글\s*", "", writer.get_text(" ", strip=True)).strip() if writer else item.get("필자", "")
    return {"url": item["url"], "막힘": False, "본문": text[:12000], "제목": item["제목"], "매체": "씨네21",
            "필자": 필자, "날짜": (date.get_text(strip=True) if date else item.get("날짜", ""))}

print("씨네21 준비 끝")

# ## 7. 평론 재료 — 검색 엔진 + 본문 읽기
#
# 제목 · 감독 · 연도로 검색한다. 차단 목록과 AI 봇 막은 매체를 빼고, 남은 링크는 robots.txt를 보고 읽는다.

from ddgs import DDGS

def 검색(q, n=15):
    try:
        with DDGS() as d:
            return [r["href"] for r in d.text(q, max_results=n) if r.get("href")]
    except Exception as e:
        if "No results" not in str(e):
            print("   검색 실패:", e)
        return []

def 검색_링크(m: Material):
    t, y, d = m.title, m.year or "", (m.director or "")
    말 = [f"{t} {y} 영화 리뷰", f"{t} {d} 평론", f"{t} {y} 영화 평"]
    말 += [f"{t} {y} film review", f"{t} {d} review"] if d else [f"{t} {y} film review"]
    out = []
    for x in dict.fromkeys(말):
        out += 검색(x, n=15)
    return out

def collect_links(m: Material):
    seen, out = set(), []
    for u in 검색_링크(m):
        u = u.split("#")[0]
        if u in seen or 차단됐나(u) or "cine21.com" in u:     # 씨네21은 따로 읽었다
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
        pages += [_parse_page(u, h) for u, h in zip(chunk, htmls) if h]
    return pages[:max(0, 한도)], blocked

개인블로그 = re.compile(r"(님의 블로그|블로그|blog|일기|다이어리|끄적|잡담|수다|생활 꿀팁|꿀팁|정보를 알고|Honest Review|어니스트 리뷰)", re.I)

def pre_filter(pages, m: Material):
    keys = [_norm(m.title)] + ([_norm(m.director)] if m.director else [])
    keys = [k for k in keys if len(k) >= 2]
    out, seen = [], set()
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        if len(p["본문"]) < 400:
            continue
        글 = _norm(p["본문"] + " " + p["제목"])
        if not any(k in 글 for k in keys):
            continue
        if m.director and _norm(m.director) not in 글:      # 같은 제목 다른 영화(괴물 2006 · 2023)를 거른다
            continue
        if 개인블로그.search((p.get("매체") or "") + " " + p["제목"]):   # 차단 목록에 없는 개인 블로그. 이름으로 거른다
            continue
        out.append(p)
    return out

print("검색 · 본문 읽기 준비 끝")

# ## 8. 재료 판정
#
# 페이지마다 모델을 한 번 부른다. 쓸지 버릴지는 코드가 정한다. 원문은 재료에 같이 붙인다. 기획자 발췌와 편집국장 대조에 쓴다.

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

class 재료판정(BaseModel):
    다루는_정도: Literal["영화 평", "감독 이야기", "인터뷰", "스치듯 언급", "관계없음"]
    평가있음: bool
    홍보문: bool
    관점: str = Field(description="글쓴이가 이 영화에서 무엇을 근거로 무엇을 봤는지 한 줄. 좋다 나쁘다만 적지 않는다")
    근거: List[Literal["장면", "인물", "연출", "각본", "배우", "맥락"]]
    인용: str = Field(description="원문에서 그대로 옮긴 한 문장. 원문 언어 그대로. 없으면 빈 문자열")
    장면메모: List[str] = Field(default_factory=list, description="이 글이 장면이나 대목을 들어 말한 것. '장면 — 무슨 말' 꼴. 결말 장면은 적지 않는다. 없으면 빈 목록")
    감독말: List[str] = Field(default_factory=list, description="감독 · 배우 · 제작진 본인이 이 영화에 대해 한 말. 누가 한 말인지 앞에. 없으면 빈 목록")

재료판정관 = Agent(
    MODEL, output_type=재료판정,
    system_prompt="""너는 영화 글 한 편을 읽고 아래 값을 낸다. 쓸지 버릴지는 네가 정하지 않는다. 값만 낸다.

[다루는_정도]
- "영화 평": 이 영화를 놓고 쓴 글이다. 리뷰, 평론, 20자평 묶음, 개봉 뒤 다시 본 글.
- "감독 이야기": 감독이나 배우를 다루면서 이 영화를 한 대목 이상 말한다.
- "인터뷰": 감독 · 배우 · 제작진이 직접 말한 글이다. 인터뷰, 기자회견, 제작기. 평가가 없어도 이걸로 둔다.
- "스치듯 언급": 이름만 나온다. 개봉 소식 · 예매율 · 흥행 기사가 여기다.
- "관계없음": 이 영화 이야기가 아니다. 이름이 같은 다른 영화면 관계없음이다.
여러 영화를 한 글에서 다루는 모음글(주간 개봉작, 영화제 결산)이면 이 영화를 다룬 대목만 놓고 판정한다.
그 대목이 서너 문장 이상이고 글쓴이 판단이 있으면 "영화 평"이다. 한두 문장이면 "스치듯 언급"이다.

[평가있음] 글쓴이가 좋다 · 나쁘다 · 아쉽다 · 낫다 같은 판단을 하나라도 적었으면 참이다.

[홍보문] 개봉일 · 출연 · 줄거리 · 예매 정보만 나열하고 글쓴이 판단이 없으면 참이다.
보도자료를 옮긴 기사, 배급사 소개문, 예매 사이트 안내가 그렇다. 별점이 붙어 있어도 판단 문장이 없으면 홍보문이다.
인터뷰는 홍보문이 아니다. 제작진 말이 있으면 거짓으로 둔다.

[관점] 글쓴이가 이 영화에서 무엇을 근거로 무엇을 봤는지 한 줄로 적는다.
"좋다", "완성도가 높다"처럼 판단만 적지 않는다. 무엇을 보고 그렇게 봤는지까지 적는다.
예: "괴물을 첫 장면부터 대낮에 내놓는 선택 때문에 이 영화가 괴물이 아니라 가족을 보라는 영화가 됐다고 봤다"

[근거] 관점이 기대는 것. 장면 / 인물 / 연출 / 각본 / 배우 / 맥락 중에서 고른다. 여럿 골라도 된다.

[인용] 관점을 가장 잘 보여주는 원문 문장 하나. 원문 언어 그대로, 한 글자도 고치지 않는다. 문장 하나만 옮긴다. 없으면 빈 문자열.

[장면메모] 이 글이 장면이나 대목을 들어 말한 것을 하나에 한 줄씩. "장면 — 무슨 말" 꼴이다.
- 장면은 글이 부른 대로 적는다. 몇 분쯤이라는 숫자는 안 적는다.
- 무슨 말에는 글이 그 장면에 대해 적은 것을 넣는다. 무엇이 보이는지, 누가 무엇을 하는지, 글쓴이가 거기서 무엇을 읽었는지.
- 결말과 큰 반전이 드러나는 장면은 적지 않는다. 그 장면이 있다는 것만 "결말 — 적지 않음"으로 둔다.
- 글에 없는 말을 붙이지 않는다. 열 개가 넘으면 글이 길게 말한 것부터 열 개까지.

[감독말] 감독 · 배우 · 제작진 본인이 이 영화에 대해 한 말. 인터뷰 답변, 제작기의 발언. "감독 — 말" 꼴로 한 줄씩. 없으면 빈 목록.

주의
- 페이지에 별점 · 필자 이름 · 다른 글 제목 · 예매 단추가 같이 붙어 있어도 본문만 본다.
- 영화 제목은 한글 표기와 원제가 같이 쓰인다. 표기가 달라도 같은 영화다.
- 글이 감독을 오래 다루다 이 영화 이야기로 넘어가면 "감독 이야기"다. 이 영화 대목이 글의 중심이면 "영화 평"이다.""",
)

def _judge_ask(p, m: Material):
    return (f"영화: {m.title} ({m.year}) · 감독 {m.director or '모름'}\n"
            f"줄거리(TMDB): {m.synopsis[:600]}\n"
            f"페이지 제목: {p['제목']}\n주소: {p['url']}\n\n본문:\n{p['본문'][:8000]}")

async def _judge_all(pages, m):
    sem = asyncio.Semaphore(동시판정)
    async def one(p):
        async with sem:
            try:
                r = await 재료판정관.run(_judge_ask(p, m), usage_limits=UsageLimits(request_limit=2))
            except Exception as e:
                print("   판정 실패:", e)
                return None
            return {"매체": p.get("매체") or 매체이름(p["url"]), "도메인": _host(p["url"]), "url": p["url"], "판정": r.output, "원문": p["본문"],
                    "필자": p.get("필자", ""), "날짜": p.get("날짜", "")}
    return await asyncio.gather(*(one(p) for p in pages))

def judge_pages(pages, m, budget, log=print):
    pages = pages[: min(모델판정상한, max(0, budget["남은콜"]))]
    if not pages:
        return []
    log(f"  큰 모델이 {len(pages)}장을 읽는다")
    got = _run_async(_judge_all(pages, m))
    budget["남은콜"] -= len(pages)
    return [g for g in got if g]

def keep_rules(판정들):
    """평론과 제작진 말을 나눈다. 평론은 매체당 PER_MEDIA 개까지."""
    평론, 말, per = [], [], {}
    for r in 판정들:
        j = r["판정"]
        if j.다루는_정도 == "인터뷰" or (j.감독말 and j.다루는_정도 != "관계없음"):
            말.append({"매체": r["매체"], "말": [x.strip() for x in j.감독말 if x.strip()][:6], "링크": r["url"], "원문": r["원문"]})
        if j.다루는_정도 not in ("영화 평", "감독 이야기") or not j.평가있음 or j.홍보문 or len(j.관점.strip()) < 10:
            continue
        c = per.get(r["도메인"], 0)
        if c >= PER_MEDIA:
            continue
        per[r["도메인"]] = c + 1
        평론.append({"매체": r["매체"], "필자": r.get("필자", ""), "날짜": r.get("날짜", ""), "관점": j.관점.strip(), "인용": j.인용.strip(), "근거": j.근거,
                    "장면메모": [x.strip() for x in j.장면메모 if x.strip() and not x.startswith("결말")][:10], "링크": r["url"], "원문": r["원문"]})
    return 평론, [x for x in 말 if x["말"]]

print("재료 판정 준비 끝")

# ## 9. 관점 묶기
#
# 임베딩만 쓴다. sentence-transformers가 없으면(백엔드 서버) 매체별로 그냥 나열한다.

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
        sim = np.asarray(v) @ np.asarray(v).T
    except Exception:
        return [[r] for r in 재료]                       # 임베딩이 없으면 하나씩. 글은 그래도 된다
    남음, 묶음 = list(range(len(재료))), []
    while 남음:
        i = 남음.pop(0); 덩어리 = [i]
        for j in list(남음):
            if sim[i][j] >= 문턱:
                덩어리.append(j); 남음.remove(j)
        묶음.append([재료[k] for k in 덩어리])
    묶음.sort(key=len, reverse=True)
    return 묶음

def views_text(묶음, 한줄평=None):
    줄 = []
    for n, g in enumerate(묶음, 1):
        매체들 = ", ".join(dict.fromkeys(r["매체"] + (f"({r['필자']})" if r.get("필자") else "") for r in g))
        줄.append(f"[관점 {n}] ({매체들} / {len(g)}곳)")
        for r in g:
            줄.append(f"  - ({r['매체']}{' · ' + r['필자'] if r.get('필자') else ''}) {r['관점']}")
            if r["인용"]:
                줄.append(f'    인용: "{r["인용"]}"  ← {r["링크"]}')
            for x in r.get("장면메모") or []:
                줄.append(f"    장면: {x}  ({r['매체']})")
    if 한줄평:
        줄.append(f"[씨네21 전문가 한줄평 — 이름 · 10점 만점 점수 · 한 줄. 점수는 글에 안 쓴다. 한 줄은 그 사람 이름을 붙여 인용할 수 있다]")
        줄 += [f"  - {x['이름']} ({x['점수']}): {x['한줄']}" for x in 한줄평[:12]]
    return "\n".join(줄)

def 발췌_text(재료, n=발췌글자):
    return "\n\n".join(f"### ({r['매체']}{' · ' + r['필자'] if r.get('필자') else ''}) {r['링크']}\n{r['원문'][:n]}" for r in 재료[:6])

def 말_text(말들):
    return "\n".join(f"- ({m['매체']}) {x}  ← {m['링크']}" for m in 말들 for x in m["말"])

def 영화_text(m: Material):
    return material_to_text(m)

print("관점 묶기 준비 끝")

# ## 10. 글의 꼴 — 접근 · 온도 · 시작점 · 배치 (V2.0 프롬프트를 그대로 들고 왔다)

APPROACHES   = ["분석", "해석", "평가"]
TEMPERATURES = ["건조", "따뜻함", "짓궂음"]
PERSONAS     = ["장면부터", "대비", "관객", "맥락", "바꿔 말하기", "질문", "결론부터"]
LAYOUTS      = ["줄거리 먼저", "판단 먼저", "줄거리 나눠 넣기", "좋은 점과 아쉬운 점 섞기", "질문에서 출발"]

APPROACH = {
"분석": """[접근 — 분석]
이 글은 뜯어보는 글이다. 이야기가 어떻게 짜였는지, 인물이 왜 그렇게 놓였는지, 어디서 긴장이 생기고 어디서 풀리는지를 본다. "이 영화는 왜 이렇게 만들어졌나"에 답한다.
  예: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다. 그건 괴물을 숨길 생각이 없다는 뜻이고, 그 뒤로 거의 내내 괴물이 아니라 가족을 보라는 말이다.
다섯 요소는 다 들어가되 이 뜯어보기가 글의 중심이다. 뜯어볼 것을 하나 고른다. 그 하나를 끝까지 간다.""",
"해석": """[접근 — 해석]
이 글은 뜻을 읽는 글이다. 이 영화가 무엇을 말하려는지, 무엇에 빗대고 있는지를 본다. "이 영화는 무슨 이야기인가"에 답한다.
  예: 정부가 이 가족을 격리하는 대목에서 이 영화의 진짜 괴물이 누구인지 드러난다.
리뷰 안이라서 해석이 글의 절반을 넘기지 않는다. 볼지 말지에 필요한 만큼만 읽는다. 읽어낼 뜻을 하나 고른다. 근거 장면과 같은 문단에 둔다.""",
"평가": """[접근 — 평가]
이 글은 좋고 나쁨을 가르는 글이다. 무엇이 되고 무엇이 안 되는지, 누가 좋아하고 누가 싫어할지를 본다. "볼 만한가"에 답한다.
  예: 공포와 웃음을 오가는 게 이 영화의 힘인데, 그 오감이 피곤한 사람에게는 힘이 아니라 흠이다.
판단을 미루지 않는다. "관객마다 다르다"는 누구에게 다른지를 갈라 말할 때만 쓴다. 되는 것 하나, 안 되는 것 하나면 충분하다.""",
}

TEMPERATURE = {
"건조": """[온도 — 건조]
감정을 드러내지 않는다. 관찰과 판단만 놓는다.
좋다 · 싫다 대신 된다 · 안 된다로 말한다. "아깝다", "반갑다"를 쓰지 않는다. 웃긴 대목이 있어도 웃기다고 말하지 않고 그 대목만 보여준다.
판단은 그래도 한다. 건조는 판단이 없다는 뜻이 아니라 판단에 감정을 안 붙인다는 뜻이다.
  안 됨: 가족의 우왕좌왕이 아쉽다.
  됨: 가족이 우왕좌왕하는 동안 괴물은 화면에서 사라진다. 그 사이가 길다.""",
"따뜻함": """[온도 — 따뜻함]
이 영화를 아끼는 게 보인다. 놀라거나 감탄해도 된다. 다만 왜 그런지가 같은 문장에 있어야 한다.
  안 됨: 송강호의 연기가 정말 좋다.
  됨: 송강호는 딸을 잃은 뒤에도 제대로 울지 못하는 사람을 몸으로만 보여 준다. 우는 장면이 없어서 더 오래 남는다.
아쉬운 점을 말할 때도 깎는 게 아니라 아까워하는 쪽이다. 다만 아쉬운 점을 빼먹지는 않는다.
아끼는 마음을 "명작", "인생 영화"로 줄이지 않는다. 어느 장면 때문인지로 말한다.""",
"짓궂음": """[온도 — 짓궂음]
정확하게 봐서 웃긴 것을 놓치지 않는다. 한 편에 한두 군데다. 세 번째부터는 가벼워진다.
  비꼼: 괴물이 나오는데 가족이 티격태격해서 웃기다.
  짓궂음: 괴물이 사람을 물어가는 와중에도 이 집 식구들은 서로 탓할 사람부터 찾는다.
비꼬지 않는다. 영화가 스스로 드러낸 우스운 대목을 그대로 보여주는 것이지, 영화를 깎는 게 아니다.
사람을 놀려서 웃기지 않는다. 감독 · 배우 · 관객을 놀려서 웃기지 않는다. 농담을 한 뒤에 설명하지 않는다.""",
}

PERSONA = {
"장면부터":    "장면 하나를 먼저 그려 놓고 거기서 시작한다. 재료의 장면메모 · 줄거리에 있는 장면이다. 그 장면이 왜 거기 있는지가 글 전체다. 마지막 문단에서 그 장면을 한 번 다시 부르며 닫는다. 줄거리를 차례로 옮기지 않는다. 장면은 첫 문단과 마지막 문단에 하나씩이다.",
"대비":        "이 영화 안의 두 가지를 마주 놓고 그 차이로 글을 끌고 간다. 괴물과 가족, 공포와 웃음, 앞 작품과 이 작품, 평이 갈린 두 자리 같은 것이다. 끝까지 그 둘이 남고, 차이가 판단이 된다.",
"관객":        "이 영화를 보려는 사람이 어디서 막히는지에서 시작한다. 무섭나, 길다는데, 원작을 알아야 하나, 앞 편을 안 봤는데 같은 것이다. 그 막힘을 풀어 주는 것이 글이고, 풀린 자리가 누구에게 맞는지다.",
"맥락":        "이 영화가 어디에서 나왔는지에서 시작한다. 감독의 앞 작품 · 원작 · 시리즈의 자리 중 하나다. 재료에 있을 때만이다. 그 자리가 이 영화의 무엇을 정했는지가 글 전체다. 연혁을 읽어 주지 않는다. 앞 작품은 이 영화와 이어지는 이유가 같은 문장에 있을 때만 부른다.",
"바꿔 말하기": "이 갈래나 이 영화에 흔히 붙는 말(\"괴수 영화\", \"사회 비판\", \"신파\", \"속편은 뻔하다\")을 한 번 뒤집어 놓고 시작한다. 뒤집은 근거가 글 전체이고, 마지막 문단에서 뒤집은 말이 판단이 된다.",
"질문":        "독자가 할 법한 질문 하나를 첫 문단에 놓고 글 전체로 답한다. 답은 마지막 문단에 나온다. 그 앞은 전부 근거다. 답을 앞에서 흘리지 않는다. 답이라고 말하지 않고 답을 쓴다.",
"결론부터":    "누구에게 맞는 영화인지를 첫 문장에 놓고 나머지로 받친다. 마지막 문장은 첫 문장으로 돌아오되 같은 문장을 다시 적지 않는다. 첫 문장이 왜 그런지가 글 전체다.",
}

LAYOUT = {
"줄거리 먼저":           "줄거리를 앞쪽에 놓고 판단을 뒤에서 낸다. 다만 줄거리 문단에도 그 사건이 왜 여기 필요한지 한 문장이 붙는다. 사건만 있는 문단을 만들지 않는다.",
"판단 먼저":             "이 영화를 어떻게 봤는지를 먼저 드러내고, 줄거리는 그 판단을 받치는 자리에만 쓴다. 첫 문단의 판단은 근거가 같은 문장에 있는 판단이지 이름표가 아니다.",
"줄거리 나눠 넣기":       "줄거리를 한 덩어리로 적지 않는다. 되는 것과 안 되는 것을 말하는 사이사이에 나눠 넣는다. 이야기를 조금 꺼내고, 판단을 붙이고, 다시 이야기로 돌아온다. 이 걸음을 끝까지 반복한다.",
"좋은 점과 아쉬운 점 섞기": "되는 것과 안 되는 것을 따로 묶지 않는다. 같은 문단 안에서 이어 말한다. 같은 선택이 어디서는 되고 어디서는 안 되는지가 한 문단에 있다. 좋은 점 문단, 아쉬운 점 문단을 따로 두지 않는다.",
"질문에서 출발":          "이 영화를 두고 할 만한 질문 하나로 시작해서, 근거를 하나씩 놓고 마지막 문단에서 답한다. 마지막 문단 앞에서 \"그래서\", \"결국\"으로 답을 미리 말하지 않는다.",
}

def 주문_text(접근, 온도, 시작점, 배치=None):
    줄 = [f"접근 {접근} · 온도 {온도} · 시작점 {시작점}" + (f" · 배치 {배치}" if 배치 else ""),
          APPROACH.get(접근, f"[접근 — {접근}]").strip(), TEMPERATURE.get(온도, f"[온도 — {온도}]").strip(),
          f"[시작점 — {시작점}]\n{PERSONA.get(시작점, '')}"]
    if 배치:
        줄.append(f"[배치 — {배치}]\n{LAYOUT.get(배치, '')}")
    return "\n\n".join(줄)

def 배치후보_text(피할=()):
    return "\n".join(f"- {b}{'  (앞 편에서 썼다. 이번엔 피한다)' if b in (피할 or ()) else ''}: {LAYOUT[b]}" for b in LAYOUTS)

RECEPTION_GUIDE = {
"호평이 많다": "좋게 본 사람이 많다는 뜻이다. 반응이 좋은 편이다 / 좋게 본 사람이 많다 / 평이 나쁘지 않다. \"만장일치\", \"극찬\", \"역대급\"처럼 세게 말하지 않는다.",
"평가가 갈린다": "보는 사람마다 다르게 봤다는 뜻이다. 평이 갈린다 / 호불호가 나뉜다. \"논란\", \"혹평 세례\"처럼 한쪽으로 기울여 말하지 않는다. 무엇을 두고 갈리는지는 관점표에 있는 것만 쓴다.",
"혹평이 많다": "좋지 않게 본 사람이 많다는 뜻이다. 반응이 좋지 않다 / 아쉬워한 사람이 많다. \"망작\", \"최악\"처럼 세게 말하지 않는다.",
}

print("글의 꼴 준비 끝")

# ## 11. 기획자
#
# 관점표 · 장면메모 · 평론 발췌 · 사실 재료 · 감독 말을 받아 뼈대를 짠다. 문장은 안 쓴다.

class 기획주문(BaseModel):
    영화정보: str
    관점표: str
    발췌: str
    감독말: str
    접근: str
    온도: str
    시작점: str
    평개수: int = 0
    지난문제: str = ""
    피할배치: List[str] = Field(default_factory=list)

class 인용계획(BaseModel):
    매체: str = Field(description="관점표에 적힌 매체 이름 그대로. 씨네21 한줄평이면 '씨네21 ○○○'처럼 사람 이름까지")
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 영어를 그대로 두지 않는다. 원문이 한국어면 그대로")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것 한 줄. 첫 문장이 아니다. 판단 · 장면 · 질문을 섞은 메모다")
    쓸장면: List[str] = Field(default_factory=list, description="이 문단에서 다루는 장면. 장면메모 · 줄거리에 있는 것만")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. 영화 정보 · 관점표 · 발췌에 있는 것만. 구체(장면 · 인물 · 선택 · 연도)가 하나 이상")
    쓸관점: List[str] = Field(description="이 문단이 기대는 평론 관점. 관점표에서 그대로. 없으면 빈 목록")

class 개요(BaseModel):
    주제: str = Field(description="이 글이 하려는 말 한 줄. 이 영화에서 잡은 것 하나(장면 · 선택 · 인물)가 들어 있다")
    배치: str = Field(description="배치 후보 중 하나. 이름 그대로")
    장면셋: List[str] = Field(description="다룰 장면. 둘 이상 넷까지. 장면메모 · 줄거리에 있는 것에서. 결말 장면은 안 된다")
    인용둘: List[인용계획] = Field(max_length=2)
    문단들: List[문단계획] = Field(min_length=4, max_length=6)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. 열 개 넘게. '사실 ← 출처' 꼴. 출처는 영화 정보 / 관점표 / 발췌 / 감독 말 중 하나. 여기 없는 사실은 작가가 못 쓴다")
    누구에게: str = Field(description="누구에게 맞고 누구에게 안 맞는지 한 줄. 마지막 문단 자리")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
    평하나 = """
- 관점표에 매체가 하나뿐이다. 그 매체의 판단을 이 글의 결론으로 삼지 않는다. 그 관점은 한 문단에서 한 번만 기댄다.
  나머지 문단은 장면메모 · 발췌 · 영화 정보에 있는 사실과 글쓴이 자신의 판단으로 채운다.
  "평론가들은", "평단은"처럼 여럿의 평인 것처럼 적지 않는다. 매체 이름을 밝히고 그 매체가 봤다고 적는다.""" if d.평개수 <= 1 else ""
    return f"""너는 영화 리뷰의 뼈대를 짠다. 문장은 안 쓴다. 무엇을 어떤 순서로 말할지, 어떤 장면과 어떤 사실로 말할지를 정한다.
작가는 네가 적어 준 사실과 장면만 쓸 수 있다. 네가 적게 주면 글이 비고 이름표 판단("가장 큰 힘이다")으로 찬다. 많이, 구체적으로 준다.

[영화 — 사실 재료. 줄거리는 결말 앞까지다]
{d.영화정보}

[관점표 — 남의 평은 여기 있는 것만. "장면:" 줄이 장면메모다]
{d.관점표 or "(없음)"}

[평론 발췌 — 사실과 장면 이야기를 여기서 캐낸다. 판단은 글쓴이 것이고 사실은 가져다 쓴다]
{d.발췌 or "(없음)"}

[감독 · 배우 말 — 본인이 한 말. "감독은 ~라고 했다"로 쓸 수 있다]
{d.감독말 or "(없음)"}

[이번 글 — 주문. 작가와 편집국장도 같은 것을 받는다]
{주문_text(d.접근, d.온도, d.시작점)}

[배치 후보 — 하나 고른다. 이름 그대로 적는다]
{배치후보_text(d.피할배치)}

[리뷰가 무엇인지]
리뷰는 이 영화를 볼지 말지 정하는 데 필요한 글이다. 해석만 하면 평론이고, 다른 작품과 견주는 데 절반을 쓰면 심층 분석이다.
아래 다섯이 글 안에 들어간다. 한 문단씩 차례로 늘어놓지 않는다. 요소가 문단 순서와 같으면(줄거리 → 좋은 점 → 아쉬운 점 → 주변 → 누구에게) 표다.
- 이 영화를 어떻게 봤는지가 글 전체에서 드러난다
- 줄거리. 결말 앞까지. 판단을 받치는 자리에 나눠 넣는다
- 되는 것. 무엇을 보고 그렇게 말하는지 같은 문단에. 근거는 사건이 아니라 선택에서 댄다
- 안 되는 것. 같은 방식으로. 억지로 흠을 잡지 않는다
- 누구에게 맞는지. 맞지 않는 사람도 같이

[하나를 잡는다]
주제는 이 영화에서 잡은 것 하나(장면 하나 · 선택 하나 · 인물 하나)다. 첫 문단에서 잡고 마지막 문단에서 그것으로 돌아온다. 다섯 요소는 그 하나를 지나가며 나온다.

[문단 계획 — 뼈대가 글의 꼴을 정한다]
- 할말은 첫 문장이 아니다. 작가가 그대로 옮기지 않게 판단 · 장면 · 질문을 섞어 적는다. 문단마다 할말의 꼴이 다르다.
- 문단마다 같은 꼴(장면 → 설명 → 판단)이 되지 않게 한다. 어떤 문단은 장면 하나를 끝까지 가고, 어떤 문단은 두 대목을 마주 놓고, 어떤 문단은 장면 없이 영화 자리(감독 앞 작품 · 원작 · 개봉 때 반응)를 말한다.
- 첫 문단은 시작점대로 연다. 개봉일 · 감독 · 출연 · 상영시간으로 열지 않는다. 그 사실은 필요한 문단에 나눠 넣는다.
- 마지막 문단은 누구에게 맞는지로 닫는다. 여러 평을 합쳐 "가장 ~한 영화 중 하나다"로 정리하지 않는다.

[규칙]
- 사실목록은 열 개를 넘긴다. 영화 정보 · 관점표 · 발췌 · 감독 말에 있는 것만. 기억으로 아는 것을 넣지 않는다.
  사실마다 "사실 ← 영화 정보" / "사실 ← 관점표" / "사실 ← 발췌" / "사실 ← 감독 말" 꼴로 출처를 적는다.
  장면 이야기(이 장면에서 무엇이 보인다, 누가 무엇을 한다, 평론이 거기서 무엇을 읽었다)를 사실목록에 장면 이름과 함께 넣는다. 이게 제일 중요하다.
- 장면셋은 둘 이상 넷까지. 장면메모나 줄거리에 있는 장면에서 고른다. 장면메모가 있는 장면을 먼저 고른다. 결말 · 큰 반전 장면은 고르지 않는다.
- 문단마다 구체가 하나 이상이다. 장면, 인물이 한 선택, 배우가 한 것, 연도 · 사람 이름 같은 것. 쓸장면 · 쓸사실에 적는다. 구체가 없는 문단은 만들지 않는다.
- 같은 말을 문단마다 되풀이하지 않는다. 주제는 한 번 말한다. 문단마다 할 말이 다르다.
- 재료가 없다 · 확인할 수 없다는 말은 어느 문단에도 넣지 않는다. 모르는 것은 안 쓴다.
- 인용은 두 개까지다. 관점표의 인용이나 씨네21 한줄평 중에서 고른다. 영어면 한국어로 옮기고, 한국어면 그대로 둔다. 한 문장이다.
- 인용의 매체는 관점표에 적힌 이름 그대로 쓴다. 씨네21 한줄평은 "씨네21 ○○○"처럼 사람 이름까지 적는다. 도메인을 적지 않는다.
- 문단은 넷에서 여섯이다. 문단마다 할 말 하나다.
- 개봉일 · 출연 · 상영시간 · 등급 같은 사실은 한 문단에 몰지 않는다. 필요한 문단에 하나씩 나눈다. 안 필요한 것은 뺀다.
- 첫 문단과 마지막 문단이 같은 말을 되풀이하지 않게 한다. 별점 · 점수 · 관객 수를 옮겨 적는 문단을 두지 않는다.{평하나}{지난}"""

print("기획자 준비 끝")

# ## 12. 작가
#
# 개요만 받아 문장으로 쓴다. 재료 원문은 안 본다. 개요에 없는 사실은 못 쓴다.

class 집필주문(BaseModel):
    영화한줄: str
    개요: 개요
    온도: str
    접근: str = ""
    시작점: str = ""
    대중평가: str = ""
    표기: str = ""              # 인물(배우) 표기 — 영화 정보의 출연 그대로

class Article(BaseModel):
    title: str = Field(description="글 제목. 영화 제목을 그대로 쓰지 않는다")
    body: str = Field(description="본문. 문단은 빈 줄로 나눈다")
    sources: list[str] = Field(default_factory=list, description="출처 링크. 코드가 채운다. 비워 둬도 된다")

작가_공통 = """너는 영화 리뷰를 쓴다. 뼈대(개요)를 받지만 그것은 채워야 할 칸이 아니다. 무엇을 어떤 차례로 말할지의 메모다. 문장은 네가 만든다.

[쓰는 사람]
영화를 오래 많이 본 사람이 쓴다. 이 영화가 처음이 아니다.
설명하지 않고 말한다. 독자가 영화를 안 봤다고 해서 하나하나 풀어 주지 않는다.
  독후감: 이 영화는 재난 앞에서 가족이 어떻게 움직이는지를 보여주는 작품이다.
  평론가: 이 가족은 재난보다 서로를 더 못 견디고, 괴물은 그걸 드러내는 핑계에 가깝다.
무엇이 눈에 걸렸는지를 말한다. 놀라거나 감탄해도 된다. 다만 왜 그런지가 같은 문장에 있어야 한다.
유머는 관찰에서 나온다. 웃기려고 쓰지 않는다. 정확하게 보면 웃긴 것이 보인다. 농담을 한 뒤에 설명하지 않는다.
자기 판단이 있다. 판단을 남에게 미루지 않는다. "평론가들은", "관객들은" 뒤에 숨지 않는다.
싸우지 않는다. 감독 · 배우 · 관객을 사람으로 깎지 않는다.
느낌표를 쓰지 않는다. 물음표는 진짜 질문일 때만 쓴다.

[문체]
문어체로 쓴다. 존댓말을 쓰지 않는다. "~합니다", "~해요", "~세요"를 쓰지 않는다.
한 문장에 뜻 하나다. 왜 그런지를 붙이는 것까지가 하나다. 절이 셋 겹치면 두 문장으로 나눈다.

한 문단은 한 줄기 생각이다. 문장은 앞 문장이 남긴 것을 받아서 이어진다.
문장을 끝낼 때 다음 문장이 붙을 자리를 남긴다. 판단을 내렸으면 그 근거나 결과가 다음 문장이 된다.
문단의 마지막 문장은 그 문단이 말한 것을 한 번 더 밀어 준다. 새 화제를 던지고 끝내지 않는다.

문장 길이는 내용이 정한다.
구체적인 것 하나를 말할 때는 짧다. "괴물은 대낮에 나온다."
왜 그런지를 따라갈 때는 길다. 따라가는 동안 끊지 않는다.
  토막: 이 영화는 괴물을 오래 감춰 두지 않는다. 시작하자마자 대낮에 풀어놓는다.
  이음: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다.
모든 문장을 같은 길이로 쓰지 않는다. 같은 꼴의 문장을 두 번 연달아 쓰지 않는다.
  같은 꼴: 강두는 늦는다. 가족은 단단하지 않다. 괴물은 빠르다. (표다. 글이 아니다)
모든 문장에 "~인데", "~지만"을 붙이지 않는다. "~는데"로 잇는 문장은 한 문단에 한 번이다.
"~가 아니라 ~다" 틀은 한 편에 한 번이다. 두 번째부터는 뒤의 말만 남긴다.
한 줄짜리 문단을 한 편에 한 번까지 쓸 수 있다. 앞 문단을 받아 세게 끊을 때만이다.

판단은 끝까지 간다. "~다"로 끝낸다.
"~에 가깝다", "~인 셈이다", "~할 만하다", "~는 편이다", "~일 수 있다", "~로 보인다"는 판단을 흐리는 말이다. 한 편에 한 번을 넘기지 않는다.
  흐림: 그 서툶은 억지로 붙인 흠이라기보다 서툰 가족을 보여주려다 생긴 흔적에 가깝다.
  판단: 그 서툶은 흠이 아니다. 서툰 가족을 보여주려고 일부러 남긴 자리다.

비유를 써도 된다. 다만 비유 뒤에 실제 장면이나 선택이 따라와야 한다. 비유만으로 문단을 채우지 않는다.

쓰지 않는 말과 대신 쓸 말이다.
- "~에 있어서" → "~에서", "~할 때"
- "~적인" → 빼거나 풀어 쓴다. "인상적인 장면" → "기억에 남는 장면"
- "~로 인해" → "~때문에"
- "~에 대한" → "~에 관한", 또는 빼고 붙인다
- "~을 통해" → "~로", "~하면서"
- "~라고 할 수 있다", "~라고 볼 수 있다" → "~다"
- "앞서 말한", "위에서 본" 같은 문서 말투 → 그 말을 다시 쓴다
출연 배우 이름을 가운뎃점으로 셋 넘게 잇지 않는다. 그 배우 이야기를 할 때만 이름을 쓴다.

중학생이 읽어도 바로 이해되는 말로 쓴다.
어려운 말 대신 일상어를 쓴다. 관조 · 미학 · 서사 · 정체성 · 밀도 · 완성도 · 층위 같은 말은 실제로 보이는 것으로 바꿔 쓴다.
사람이 말하는 것처럼 읽혀야 한다. 소리 내어 읽었을 때 어색하면 고친다.
이 프롬프트에 든 예시 문장을 그대로 옮기지 않는다. 예시는 모양을 보여주는 것이지 재료가 아니다.

[하나를 잡는다]
글은 이 영화에서 잡은 것 하나(장면 하나 · 선택 하나 · 인물 하나)를 끝까지 끌고 간다. 첫 문단에서 잡고 마지막 문단에서 그것으로 돌아온다. 개요의 주제가 그것이다.
다섯 요소(어떻게 봤는지 · 줄거리 · 되는 것 · 안 되는 것 · 누구에게)는 그 하나를 지나가며 나온다. 요소가 문단 순서와 같으면 표다. 글이 아니다.
판단을 이름표로 다는 문장을 쓰지 않는다. "이 영화의 가장 큰 힘은", "~는 분명하다", "~가 중심을 잡는다", "매력으로 남는다", "약점으로 느껴진다"는 판단이 아니라 판단이 있다는 표시다. 무엇이 어떻게 되는지를 쓰면 판단이 보인다.
  이름표: 강두네가 특별한 사람들이 아니라는 점이 영화의 가장 큰 힘이다.
  판단: 강두네는 뭘 잘하는 사람들이 아니라서, 딸을 찾으러 나서는 길이 작전이 아니라 몸부림이 된다. 그 몸부림을 보는 게 이 영화다.
"좋은 점은", "아쉬운 점은", "장점은", "단점은"으로 문장을 시작하지 않는다. "다만", "그럼에도", "그러나"로 문단을 시작해서 방향을 바꾸지 않는다.
"~를 다룬 작품이다", "~한 이야기다"로 영화를 규정하는 문장을 첫 문단에 쓰지 않는다. 그건 독후감이 하는 일이다.

[글이 이어지게 쓴다]
문단 계획의 할 말 · 장면 · 사실은 채워야 할 칸이 아니다. 하나의 글 안에서 서로를 부르며 나온다.
앞 문장이 다음 문장을 부른다. 앞 문장에서 꺼낸 것을 다음 문장이 받아서 이어 간다.
  안 됨: 이 영화는 2006년에 나왔다. 감독은 봉준호다. 관객이 천만이 넘었다. (서로 상관없는 사실을 붙였다)
  됨: 봉준호가 괴물을 첫 장면부터 대낮에 내놓은 것은 괴물을 숨길 생각이 없어서고, 그래서 이 영화는 괴물이 아니라 가족을 보라는 영화가 된다.
사실 하나를 말했으면 그 사실이 이 영화에서 무엇을 뜻하는지 이어서 말한다.
장면 하나를 적으면 그 장면이 왜 여기 필요한지 한 문장 붙인다. "그 뒤에", "이후", "한편", "결국"으로 사건을 잇는 문장을 연달아 쓰지 않는다.
줄거리를 사건 순서대로 늘어놓지 않는다. 같은 사건을 줄거리에서 한 번, 근거에서 또 한 번 쓰지 않는다.
개봉일 · 출연 · 상영시간 · 등급을 한 곳에 몰아 적지 않는다. 필요한 문단에 하나씩 나온다. 안 필요한 것은 뺀다.
다 넣으려 하지 않는다. 짧은 글은 요약이 아니라 선택이다. 장면 하나를 한 문장으로 처리하느니 그 장면을 뺀다.
문단은 생각이 바뀌는 자리에서만 나눈다. 장면이 바뀐다고 나누지 않는다.
글 하나에 주제 하나, 흐름 하나다. 처음에 잡은 것이 끝까지 간다.

[문단 여는 법]
문단마다 시작하는 방식을 바꾼다. 같은 방식으로 두 문단 연속 시작하지 않는다.
- 상황을 그리며 시작한다. "대낮 한강 둔치에서, 사람들은 아직 아무것도 모른다."
- 질문으로 시작한다. "왜 이 가족은 도망치지 않았을까."
- 흔한 말을 받아 뒤집으며 시작한다. "괴수 영화라고들 하는데, 괴물이 화면에 있는 시간은 생각보다 짧다."
- 앞 문단 끝을 받아서 시작한다.
- 판단으로 시작할 때는 근거를 같은 문장 안에 붙인다.
"X는 Y다"처럼 짧게 못 박는 문장으로 문단을 열지 않는다. 그건 구호지 문장이 아니다.
영화 제목이나 "이 영화는", "감독은"처럼 같은 주어로 문단을 두 번 연속 열지 않는다. 영화 제목으로 문단을 여는 것은 한 편에 두 번까지다.
문단 계획의 "할 말" 한 줄을 첫 문장으로 그대로 옮기지 않는다.

[독자 앞에는 표가 없다]
영화 정보 · 관점표 · 발췌 · 장면메모는 네가 본 재료다. 독자는 못 봤다. 그 이름을 부르지 않는다.
재료가 없다 · 정보가 제한된다 · 확인할 수 없다 · 단정할 수 없다는 말을 쓰지 않는다. 없으면 그 이야기를 안 하고 넘어간다.
재료 문장을 어순만 바꿔 옮기지 않는다. 내 문장으로 다시 쓴다.

[네 판단 · 남의 평]
좋다 · 아깝다 · 안 된다 같은 말을 써도 된다. 다만 왜 그런지를 같은 문장에 붙인다.
관찰은 네 몫이다. 어느 장면에서 영화가 꺾이는지는 네가 말한다. 다만 장면은 재료(장면메모 · 줄거리)에 있는 것만이다. 에이전트는 영화를 못 봤다. 화면 · 소리 · 편집이 어떻더라는 평론 · 장면메모에 있는 것만 쓴다.
남의 평은 사실 목록과 인용에 있는 것만. 매체 이름을 붙인다. "이즘은 “…”라고 썼다" 꼴로 문장 안에 넣는다. 매체 이름을 괄호로 앞에 붙이지 않는다. 매체가 하나면 "평론가들은"으로 늘리지 않는다.
근거 없이 세게 말하지 않는다. "역대급", "레전드", "명작", "놓치면 후회"를 쓰지 않는다.
보러 가라고 부추기지 않는다. "꼭 봐야", "추천한다", "예매"를 쓰지 않는다.
별점 · 점수 · 관객 수 · 순위를 쓰지 않는다. 결말과 큰 반전을 밝히지 않는다. 재료의 줄거리에 이미 적힌 것까지는 괜찮다.

[숫자]
본문에 숫자를 쓰지 않는다. 개봉 연도만 예외다. 시간 · 횟수 · 크기는 말로 쓴다.
  숫자: 괴물이 20분 만에 나온다.
  말: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다."""

작가 = Agent(MODEL, deps_type=집필주문, output_type=Article)

def _사실만(s):
    return s.split(" ← ")[0].strip()

@작가.system_prompt
def _작가프롬프트(ctx) -> str:
    d = ctx.deps
    o = d.개요
    문단 = "\n".join(f"{i+1}. {p.할말}\n   장면: {', '.join(p.쓸장면) or '없음'}\n   사실: {'; '.join(_사실만(x) for x in p.쓸사실) or '없음'}\n   관점: {'; '.join(p.쓸관점) or '없음'}"
                    for i, p in enumerate(o.문단들))
    인용 = "\n".join(f"- {q.매체}: {q.옮긴문장}" for q in o.인용둘) or "없음"
    대중 = f"\n[대중 평가 쓰는 법 — 한 편에 한 번, 갈래를 넘지 않는다]\n{RECEPTION_GUIDE[d.대중평가]}" if d.대중평가 in RECEPTION_GUIDE else "\n[대중 평가] 재료에 갈래 값이 없다. 대중 평가를 쓰지 않는다."
    return 작가_공통 + f"""

[주문 — 기획자와 편집국장도 같은 것을 받았다]
{주문_text(d.접근, d.온도, d.시작점, o.배치)}

[영화] {d.영화한줄}
[인물 표기 — 배우 이름은 이대로. 역할 이름은 줄거리에 적힌 대로] {d.표기 or "출연 정보 없음"}
[주제 — 이 영화에서 잡은 것 하나] {o.주제}
[다룰 장면] {", ".join(o.장면셋) or "정하지 않았다. 장면을 새로 꺼내지 않는다"}
[누구에게] {o.누구에게}
{대중}

[문단 계획]
{문단}

[쓸 수 있는 인용 — 이 문장 그대로]
{인용}

[쓸 수 있는 사실 — 이 밖의 사실은 쓰지 않는다]
{chr(10).join("- " + _사실만(f) for f in o.사실목록)}

[규칙]
- 존댓말을 쓰지 않는다. 판단은 "~다"로 끝낸다.
- 위 사실 목록과 인용 밖의 사실이나 남의 평을 넣지 않는다. 네 판단은 넣어도 된다.
- 문단 계획 순서를 지킨다. 문단마다 할 말 하나다. 문단 계획에 적힌 장면과 사실을 그 문단에서 실제로 쓴다.
- 구체로 쓴다. 어느 장면에서 누가 무엇을 하는지, 어떤 선택이 무엇을 만드는지. "긴장이 있다", "감정이 오르내린다" 같은 말만으로 문단을 채우지 않는다.
- 같은 말을 되풀이하지 않는다. 주제는 한 번만 말한다.
- 영화 제목은 『 』로 감싼다. 제목 줄에서는 안 감싼다. 인용은 “ ”로 감싼다.
- 분량은 {분량[0]}자에서 {분량[1]}자 사이다. 문단은 빈 줄로 나눈다."""

print("작가 준비 끝")

# ## 13. 교정자 (음악 v3.3과 같다. 낱말만 영화로)

class 교정본(BaseModel):
    본문: str
    고친곳: List[str] = Field(description="무엇을 어떻게 고쳤는지 한 줄씩")

교정자 = Agent(
    MODEL, output_type=교정본,
    system_prompt="""너는 한국어 문장을 다듬는다. 뜻과 사실과 판단은 하나도 안 바꾼다. 문장 짜임만 고친다.

번역투는 영어 문장을 그대로 옮긴 것처럼 읽히는 문장이다. 아래가 그것이다. 보이면 고친다.
1. "~는 것이 아니라 ~다" 틀. 앞을 빼고 뒤만 말하거나, 두 문장으로 나눈다.
2. 한 문장에 절이 셋 넘게 겹쳐서 끝까지 가야 뜻이 잡히는 것. 뜻 단위로 끊는다.
3. 물건이 주어로 서서 스스로 무언가를 하는 것. "연출이 놓인다", "카메라가 감정을 밀어간다". 사람이나 화면이 실제로 하는 일로 바꾼다.
4. 결론을 명사로 닫는 것. "~하는 이유다", "~의 결과에 가깝다", "~하는 방식이다". 동사로 끝낸다.
5. "~에 그치지 않는다", "~에 머물지 않는다", "단순히 ~가 아니다". 뒤에 오는 말만 남긴다.
6. "가장 먼저 ~하는 것은 ~다" 강조 틀. 주어를 앞으로 빼서 보통 문장으로 만든다.
7. 물건이 주어가 되어 비유만 하는 것. 실제로 보이는 것으로 바꾼다.
8. "~라기보다 ~으로 보는 편이 맞다", "~라는 데 있다", "~을 제시한다", "설득력을 얻는다", "유효하다". 판단을 동사로 바로 말한다.

같이 본다.
- 한자어 동사(작동한다, 기능한다, 구축한다, 형성한다)는 일상어로. 한다, 된다, 만든다, 낸다.
- "~적", "~에 있어", "~에 대한", "~을 통해", "~에 의해"는 뺀다.
- 손에 안 잡히는 말(밀도, 결, 층위, 텍스처, 긴장, 세계, 지점, 서사, 정체성, 미학, 완성도)은 그 문장이 실제로 가리키는 것으로 바꾼다. 가리키는 것이 문장에 없으면 그 말만 뺀다.
- 영화 · 사람 · 인물 이름은 손대지 않는다. 잘못 적힌 것만 바로잡고 고친 곳에 적는다.
- 영어 문장이 그대로 있으면 한국어로 옮긴다. 영어로 된 이름은 옮기지 않는다.

문장 길이는 내용이 정한다. 짧은 것을 억지로 늘리거나 긴 것을 억지로 자르지 않는다.
고친 곳마다 한 줄로 적는다.""",
)

print("교정자 준비 끝")

# ## 14. 형태 검사 (코드)
#
# V2.0의 검사와 음악의 검사를 합쳤다. 모델을 안 부른다.

존댓말 = re.compile(r"(습니다|합니다|입니다|됩니다|세요|네요|해요|이에요|예요|드립니다|거죠|죠)\s*[.?!\n]")
흐림 = re.compile(r"(에 가깝다|인 셈이다|할 만하다|는 편이다|일 수 있다|로 보인다|로 느껴진다|인 듯하다|인 것 같다|일지도 모른다|아닐까)")
부추김 = re.compile(r"(예매|극장에서 꼭|놓치지 마|강력 추천|무조건 봐|꼭 봐야|추천한다)")
번역틀 = re.compile(r"(것이 아니라|라기보다|하는 방식이다|하는 이유다|하는 지점|에 있어|을 통해|를 통해|에 의해|그치지 않|머물지 않|단순히 .{1,12}가 아니|편이 맞다|데 있다|데서 생긴다|을 제시한다|를 제시한다|설득력을 얻|유효하다)")
추상어 = re.compile(r"(서사|정체성|미학|밀도|층위|텍스처|낙차|긴장감|다채로움|보편성|완성도|관조)")
다수평 = re.compile(r"(평론가들은|평단은|비평가들은|많은 이들이|관객들은 .{0,6}평가|호평이 이어|평이 갈렸)")
재료사정 = re.compile(r"(확인할 수 있는 정보|확인할 수 없|알 수 없다는 점|정보(는|가) .{0,12}제한|TMDB|자료가 (없|부족)|단정할 수는 없|판단의 범위|재료에)")
괄호인용 = re.compile(r"\(\s*[가-힣A-Za-z0-9 .!'’]{2,20}\s*\)\s*[“\"]")
따옴표안 = re.compile(r"[“\"]([^”\"]{10,})[”\"]")
영문덩어리 = re.compile(r"[A-Za-z][A-Za-z ,.'’\-]{39,}")
숫자단위 = re.compile(r"\d+\s*(분|초|시간|번|회|차례|퍼센트|%|명|미터|m|배|만 명|만명|억)")
별점표시 = ("★", "☆", "⭐", "/10", "/5", "점 만점")
요소이름 = ("좋은 점은", "아쉬운 점은", "장점은", "단점은", "한계는", "좋은 점이라면", "아쉬운 점이라면")
규정끝 = ("작품이다", "영화이다", "영화다", "이야기다", "이야기이다")
이름표 = ("가장 큰 힘이", "힘은 분명하", "힘이 분명하", "매력으로 남는다", "약점으로 느껴진다", "답답함을 남긴다", "중심을 잡는다", "선명하게 드러난다")
접속머리 = ("그래서", "그러나", "다만", "하지만", "또한", "그럼에도", "그리고", "그만큼", "그래도")

def _겹침(a, b, n=12):
    A = {a[i:i+n] for i in range(max(0, len(a)-n+1))}
    B = {b[i:i+n] for i in range(max(0, len(b)-n+1))}
    return len(A & B) / len(A) if A and B else 0.0

def _문장들(t):
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+", t) if len(x.strip()) >= 8]

def 형태검사(본문, 재료, m: Material, 장면셋=None, 제목=""):
    걸림, t = [], 본문
    if 제목.strip() and 제목.strip() == m.title:
        걸림.append("제목이 영화 제목과 똑같다")
    if 존댓말.search(t):
        걸림.append("존댓말")
    if not re.search(r"[.!?…\"'”』]\s*$", t.strip()):
        걸림.append("문장 끊김")
    문단 = [p.strip() for p in t.split("\n") if p.strip()]
    머리 = [p.split()[0] for p in 문단 if p.split()]
    if len(머리) >= 3 and len(set(머리)) < len(머리) * 0.7:
        걸림.append("문단 시작 반복")
    if sum(1 for h in 머리 if h in 접속머리) >= 3:
        걸림.append("접속사로 문단 열기")
    재료글 = "\n".join(r["관점"] + " " + r["인용"] for r in 재료) + "\n" + m.synopsis + "\n" + (m.context or "")
    if 재료글.strip() and _겹침(t, 재료글) > 0.08:
        걸림.append("재료 옮겨 적기")
    if len(흐림.findall(t)) >= 2:
        걸림.append("판단 흐리는 끝맺음")
    틀 = 번역틀.findall(t)
    if len(틀) >= 3:
        걸림.append("번역투 틀: " + ", ".join(dict.fromkeys(틀)))
    t_이름뺌 = t
    for x in [m.title] + list(m.cast) + list(장면셋 or []):
        if x and len(x) >= 3:
            t_이름뺌 = t_이름뺌.replace(x, "")
    if 영문덩어리.search(t_이름뺌):
        걸림.append("영어 원문")
    추 = 추상어.findall(t)
    if len(추) >= 4:
        걸림.append("추상어 " + str(len(추)) + "개: " + ", ".join(dict.fromkeys(추)))
    if len(set(r["매체"] for r in 재료)) <= 1 and 다수평.search(t):
        걸림.append("없는 다수 평: " + 다수평.search(t).group(0))
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
    for mark in 별점표시:
        if mark in t:
            걸림.append(f"점수 표시 ({mark})"); break
    for w in 요소이름:
        if w in t:
            걸림.append(f"요소 이름을 문장에 그대로 썼다 ({w})"); break
    첫문장 = _문장들(문단[0])[:1] if 문단 else []
    if 첫문장 and 첫문장[0].rstrip(".?!").endswith(규정끝):
        걸림.append("첫 문장이 영화를 규정하는 문장이다")
    수 = 숫자단위.findall(t)
    if 수:
        걸림.append(f"숫자로 말한 곳 {len(수)}군데")
    for s in _문장들(t):
        if sum(1 for c in m.cast if c and c in s) >= 3:
            걸림.append("출연진 이름을 한 문장에 늘어놓았다"); break
    low = _norm(t)
    정한장면 = [x for x in (장면셋 or []) if _norm(x)]
    if 정한장면 and not any(_norm(x)[:6] in low for x in 정한장면):
        걸림.append("정한 장면이 글에 없다")
    if not (분량[0] - 200 <= len(t) <= 분량[1] + 400):
        걸림.append(f"분량 {len(t)}자")
    if m.reception is None and re.search(r"(호평|혹평|평이 갈|평가가 갈|호불호|평점|관객 평|평이 박)", t):
        걸림.append("갈래 값이 없는데 대중 평가를 썼다")
    걸림 += 꼴검사(t, 문단, m)
    return list(dict.fromkeys(걸림))

def 꼴검사(t, 문단, m: Material):
    걸림 = []
    문장들 = _문장들(t)
    if 문장길이편차문턱 and len(문장들) >= 10:
        길이 = [len(x) for x in 문장들]; 평균 = sum(길이) / len(길이)
        편차 = (sum((x - 평균) ** 2 for x in 길이) / len(길이)) ** 0.5
        if 평균 and 편차 / 평균 < 문장길이편차문턱:
            걸림.append(f"문장 길이 단조 (편차 {편차 / 평균:.2f})")
    if 문단길이편차문턱 and len(문단) >= 4:
        길이 = [len(x) for x in 문단]; 평균 = sum(길이) / len(길이)
        편차 = (sum((x - 평균) ** 2 for x in 길이) / len(길이)) ** 0.5
        if 평균 and 편차 / 평균 < 문단길이편차문턱:
            걸림.append(f"문단 길이 단조 (편차 {편차 / 평균:.2f})")
    머리들 = ("『" + m.title, "《" + m.title, m.title, "영화 『", "영화 《", "이 영화")
    n = sum(1 for p in 문단 if p.startswith(머리들))
    if n >= 3:
        걸림.append(f"영화 제목이나 '이 영화'로 문단 열기 {n}번")
    hit = [w for w in 이름표 if w in t]
    if len(hit) >= 2:
        걸림.append("판단을 이름표로 달았다: " + ", ".join(hit))
    return 걸림

print("형태 검사 준비 끝")

# ## 15. 편집국장

class 문제(BaseModel):
    종류: Literal["사실", "문장", "구조", "주문"]   # V3.1: 주문은 사소로 센다
    어디: str = Field(description="문제가 있는 문장을 그대로")
    설명: str

class 편집판정(BaseModel):
    점수: int = Field(ge=0, le=100)
    문제들: List[문제]
    한줄평: str
    스포일러: bool = Field(default=False, description="결말이나 큰 반전을 밝혔으면 참. 재료의 줄거리에 이미 적힌 것은 아니다")
    확인목록: List[str] = Field(default_factory=list, description="재료 밖이지만 널리 알려진 사실로 보이는 문장. 사람이 나중에 본다. 점수는 안 깎는다")
    앞판대조: List[str] = Field(default_factory=list, description="앞 판 판정이 있을 때만. 앞 판 문제마다 한 줄. '고쳐짐 — 무엇' / '남음 — 무엇'. 이번 글에서 새로 생긴 문제는 '새로 생김 — 무엇'. 앞 판 글에도 있었는데 앞 판에서 안 잡은 문제는 '앞 판에서 못 봄 — 무엇'")
    점수설명: str = Field(default="", description="앞 판 판정이 있을 때만. 앞 점수를 기준으로 왜 올랐는지 · 내렸는지 한 줄")

편집국장 = Agent(
    MODEL, output_type=편집판정,
    system_prompt="""너는 편집국장이다. 완성된 영화 리뷰를 마지막으로 읽는다. 세 가지를 본다.

[사실] 글에 적힌 사실 하나하나를 영화 정보 · 개요 · 관점표 · 평론 원문과 대조한다.
- 어디에도 없는 사실이면 종류 "사실"로 잡는다. 남의 평을 지어낸 것도 "사실"이다.
- 영화 정보에 있는 개봉일 · 감독 · 출연 · 상영시간 · 등급 · 줄거리 사건은 재료 안이다.
- 화면 · 소리 · 편집이 어떻더라는 말(어느 곡이 깔린다, 카메라가 어떻게 움직인다)은 평론 원문 · 장면메모에 있을 때만 재료 안이다. 없으면 "사실"로 잡는다. 에이전트는 영화를 못 봤다.
- 글쓴이 자신의 판단과 관찰(이 선택이 왜 먹히는지, 누구에게 맞는지)은 사실이 아니다. 잡지 않는다.
- 원문에 있는 평을 글이 다르게 옮겼으면 "사실"로 잡고 원문이 뭐라 했는지 적는다.
- 원문이 하나뿐인데 "평론가들은", "평단은"처럼 여럿의 평으로 적었으면 "사실"로 잡는다.
- 대중 평가를 재료의 갈래 값보다 세게 말했으면 "사실"로 잡는다. 갈래 값이 없는데 대중 평가를 적었으면 "사실"이다.
- 널리 알려진 사실(원작 · 시리즈 순서 · 개봉 뒤 반응)이 재료에 없으면 "사실"로 잡지 않는다. 확인목록에 문장 그대로 적는다. 점수를 깎지 않는다.
- 사람 · 인물 이름의 한글 표기 차이, 띄어쓰기 차이는 사실 문제가 아니다.

[문장] 뜻이 안 잡히는 문장, 앞뒤가 안 맞는 문장, 고치다 망가진 문장, 영어를 그대로 옮긴 듯한 문장. 종류 "문장".
- 판단을 이름표로 단 문장("가장 큰 힘이다", "~는 분명하다", "매력으로 남는다", "약점으로 느껴진다")은 "문장"이다. 무엇이 어떻게 되는지가 없다.
- 인용 앞에 매체 이름을 괄호로 붙인 것("(씨네21) “…”")은 "문장"이다. "씨네21은 “…”라고 썼다" 꼴이어야 한다.

[구조] 하나를 잡아 끝까지 갔는지, 시작과 끝이 맞물리는지, 다섯 요소가 있는지, 문단마다 할 말이 하나인지. 종류 "구조".
- 다섯 요소가 문단 순서와 같으면(줄거리 → 좋은 점 → 아쉬운 점 → 주변 → 누구에게) 표다. "구조"로 잡는다.
- 마지막 문단이 글쓴이 판단이 아니라 여러 평을 합친 요약("가장 ~한 영화 중 하나다")이면 "구조"로 잡는다.
- 개봉일 · 출연 · 상영시간 · 등급이 한 문단에 몰려 목록처럼 읽히면 "구조"로 잡는다.
- 첫 문단과 마지막 문단이 같은 말을 되풀이하면 "구조"로 잡는다.
- 줄거리가 글의 대부분이고 판단이 마지막에만 붙어 있으면 "구조"로 잡는다.
- 장면 · 인물 · 선택 같은 구체가 하나도 없이 "긴장이 있다", "감정이 오르내린다" 같은 말로만 채운 문단은 "구조"로 잡는다.
- 글이 자기 재료 사정을 말하면("확인할 수 있는 정보가 제한된다") "구조"로 잡는다. 독자는 그걸 알 필요가 없다.
- "좋은 점은", "아쉬운 점은"으로 시작하는 문장이 있으면 "구조"로 잡는다.

[스포일러] 결말이나 큰 반전을 밝혔으면 스포일러를 참으로 둔다. 재료의 줄거리에 이미 적힌 것은 아니다.

[주문대로 갔는가]
[주문]의 접근 · 온도 · 시작점 · 배치와 그 뜻을 받는다. 주문대로 안 갔으면 종류를 "주문"으로 적는다. "구조"가 아니다. 점수를 깎지 않는다.
주문끼리 서로 부딪쳐 하나를 어길 수밖에 없을 때가 있다. 글이 읽히면 그걸로 된다. 주문 어김은 올림을 막지 않는다.
- 짓궂음인데 웃긴 대목이 하나도 없다. 장면부터 열라고 했는데 감독 이름으로 열었다. 평가인데 판단이 마지막 문단에만 있다. 해석이 글의 절반을 넘는다.
- 시작점대로 열었는지는 첫 문단에서 본다. 배치대로 갔는지는 문단 순서에서 본다.

[되풀이 — 글의 꼴]
- 같은 꼴 문단이 두 번 이상 이어진다(장면 → 설명 → 판단 → 다음 장면). "구조"다.
- 문장 길이가 다 비슷하고 같은 꼴 문장("A는 B한다")이 이어진다. "문장"이다.
- 문단 첫 문장이 같은 방식(영화 제목으로 열기, "이 영화는")으로 두 번 연속이다. "구조"다.
- "~가 아니라 ~다" 틀이 셋 넘는다. "문장"이다.
- 문단 계획의 할 말 한 줄이 첫 문장에 그대로 보인다. "문장"이다.

[앞 판이 있으면]
[앞 판 판정]과 [앞 판 글]이 붙어 오면 [이번 글]은 그 판정을 받고 다시 쓴 글이다. 처음 보는 것처럼 채점하지 않는다. 채점하는 것은 [이번 글]이다. [앞 판 글]은 대조하려고 주는 것이다.
- [이번 글] 머리에 판 종류가 적혀 있다. "새로 씀"이면 앞 판 문제 목록만 받고 처음부터 다시 쓴 것이다. 앞 판 문제가 다른 문장으로 옮겨 와 남았는지 본다.
- 앞 판 문제마다 이번 글에서 고쳐졌는지 본다. 앞판대조에 한 줄씩 적는다. "고쳐짐 — 무엇", "남음 — 무엇". 남은 것은 문제들에 다시 넣는다. 고쳐진 것은 다시 잡지 않는다.
- 이번 글에서 새로 생긴 문제는 "새로 생김 — 무엇"으로 적고 문제들에 넣는다. 앞 판 글에도 그대로 있었는데 앞 판 판정에 없는 문제면 "앞 판에서 못 봄 — 무엇"으로 적고 문제들에 넣는다. 둘을 섞지 않는다.
- 점수는 앞 점수에서 출발한다. 고쳐진 것이 있고 새로 생긴 문제가 없으면 앞 점수보다 낮게 주지 않는다. 새 문제가 생겼거나 고친 자리가 다른 자리를 망가뜨렸으면 내릴 수 있다. 내리면 점수설명에 어느 문제 때문인지 적는다. 못 봄만 있고 새로 생김이 없으면 내리지 않는다.
- 점수설명은 한 줄이다. "앞 판 62점. 사실 둘 고쳐짐, 되풀이 남음, 새 문제 없음. 71점"처럼.
앞 판이 없으면 앞판대조와 점수설명은 비워 둔다.

직접 고치지 않는다. 올릴지 말지도 정하지 않는다. 점수와 문제 목록과 확인 목록을 낸다.
문제마다 어느 문장인지 그대로 옮겨 적는다.""",
)

def 앞판_text(k):
    if not k:
        return ""
    p = k.get("판정")
    줄 = [f"[앞 판 판정 — {k.get('차례', '?')}차" + (f", {p.점수}점" if p else ", 편집국장 실패") + "]"]
    if k.get("걸림"):
        줄.append("코드 검사에 걸린 것: " + ", ".join(k["걸림"]))
    if p:
        줄 += [f"- ({x.종류}) {x.어디[:80]} — {x.설명}" for x in p.문제들] or ["- 문제 없음"]
        줄.append(f"한줄평: {p.한줄평}")
    g = k.get("글")
    if g is not None:
        줄 += ["", f"[앞 판 글 — {k.get('차례', '?')}차]", g.title, "", g.body]
    return "\n".join(줄)

def 이번글_머리(차례, 종류, 앞판):
    if not (앞판기억 and 앞판):
        return "[글]"
    뜻 = {"고침": "앞 글을 받아 손본 것", "새로 씀": "앞 판 문제 목록만 받고 처음부터 다시 쓴 것"}.get(종류, "")
    return f"[이번 글 — {차례}차 · {종류}" + (f" · {뜻}" if 뜻 else "") + "]"

def 편집국장_읽기(글: Article, 개요_: 개요, 재료, 관점표, m: Material, budget, 감독말=None, 앞판=None, 차례=None, 종류="", 주문=None):
    if budget["남은콜"] <= 0:
        return None
    원문들 = "\n\n".join(f"### ({r['매체']}{' · ' + r['필자'] if r.get('필자') else ''}) {r['링크']}\n{r['원문'][:4000]}" for r in 재료) or "(없음)"
    원문들 += "".join(f"\n\n### (감독 · 배우 말 · {x['매체']}) {x['링크']}\n" + "\n".join("- " + y for y in x["말"]) for x in (감독말 or []))
    앞 = (앞판_text(앞판) + "\n\n") if (앞판기억 and 앞판) else ""
    주문글 = (f"[주문 — 기획자 · 작가가 받은 것]\n{주문_text(주문.접근, 주문.온도, 주문.시작점, 개요_.배치)}\n\n" if 주문 is not None else "")
    ask = (f"{앞}{주문글}[영화 정보]\n{영화_text(m)}\n\n"
           f"[개요]\n주제: {개요_.주제}\n누구에게: {개요_.누구에게}\n사실목록:\n" + "\n".join("- " + f for f in 개요_.사실목록) + "\n\n"
           f"[관점표]\n{관점표 or '(없음)'}\n\n"
           f"[평론 원문 — {len(재료)}편]\n{원문들}\n\n"
           f"{이번글_머리(차례, 종류, 앞판)}\n{글.title}\n\n{글.body}")
    try:
        r = 편집국장.run_sync(ask, usage_limits=UsageLimits(request_limit=2))
    except Exception as e:
        print("   편집국장 실패:", e)
        return None
    budget["남은콜"] -= 1
    return r.output

print("편집국장 준비 끝")

# ## 16. 올릴지 정하기 — 무게로 나눈다 (음악 v3.0 방식)

형태_치명 = ["재료 옮겨 적기", "없는 다수 평", "재료 사정 언급", "정한 장면이 글에 없다", "문장 끊김", "점수 표시", "부추기는 말"]
형태_고칠것 = ["존댓말", "번역투 틀", "판단 흐리는 끝맺음", "괄호 인용 표기", "인용이 길다", "도메인이 글에 나온다", "느낌표", "추상어", "영어 원문",
               "요소 이름", "첫 문장이 영화를 규정", "숫자로 말한", "출연진 이름", "갈래 값이 없는데", "문장 길이 단조", "문단 길이 단조", "영화 제목이나", "판단을 이름표로", "접속사로 문단 열기"]
분량_치명아래 = 800
고칠것_상한   = 3

def 형태무게(걸림):
    치명, 고칠것, 사소 = [], [], []
    for x in 걸림:
        머리 = x.split(":")[0].split("(")[0].strip()
        if x.startswith("분량"):
            mm = re.search(r"(\d+)자", x)
            (치명 if (mm and int(mm.group(1)) < 분량_치명아래) else 사소).append(x)
        elif any(머리.startswith(k) or k in x for k in 형태_치명):
            치명.append(x)
        elif any(머리.startswith(k) or k in x for k in 형태_고칠것):
            고칠것.append(x)
        else:
            사소.append(x)
    return 치명, 고칠것, 사소

def _무게_규칙(x):
    if x.종류 == "주문":                    # V3.1: 주문 어김은 반려 사유가 아니다(PM 9/29)
        return "사소"
    설명 = (x.설명 or "") + " " + (x.어디 or "")
    if re.search(r"(표기|오타|철자|띄어쓰기|대소문자)", 설명):
        return "사소"
    if x.종류 == "사실":
        return "치명"
    return "고칠것"

def 올릴까(걸림, p, 바퀴=1):
    형_치명, 형_고칠것, 형_사소 = 형태무게(걸림)
    if p is None:
        if 형_치명:
            return False, "형태 치명: " + ", ".join(형_치명), {"형태치명": 형_치명}
        return True, f"편집국장 못 부름 · 형태 치명 없음(고칠것 {len(형_고칠것)})", {}
    무게표 = [(x, _무게_규칙(x)) for x in (p.문제들 or [])]
    편_치명 = [x for x, w in 무게표 if w == "치명"]
    편_고칠것 = [x for x, w in 무게표 if w == "고칠것"]
    편_사소 = [x for x, w in 무게표 if w == "사소"]
    치명 = 형_치명 + [f"{x.종류}: {x.설명}" for x in 편_치명] + (["스포일러: 결말을 밝혔다"] if p.스포일러 else [])
    고칠것수 = len(형_고칠것) + len(편_고칠것)
    상세 = {"형태치명": 형_치명, "형태고칠것": 형_고칠것, "형태사소": 형_사소,
            "편집치명": [x.설명 for x in 편_치명] + (["결말을 밝혔다"] if p.스포일러 else []), "편집고칠것": [x.설명 for x in 편_고칠것],
            "편집사소": [x.설명 for x in 편_사소], "점수": p.점수}
    마지막 = 바퀴 >= REWRITE_LIMIT
    if 치명:
        return False, f"치명 {len(치명)}건: " + "; ".join(치명[:3]), 상세
    기준 = 마지막안전점수 if 마지막 else PASS_SCORE
    if p.점수 < 기준:
        return False, f"점수 {p.점수} (기준 {기준}) · 고칠것 {고칠것수}건", 상세
    if not 마지막 and 고칠것수 > 고칠것_상한:
        return False, f"고칠것 {고칠것수}건 · 점수 {p.점수}", 상세
    return True, f"점수 {p.점수} · 고칠것 {고칠것수} · 사소 {len(형_사소) + len(편_사소)}" + (" (마지막 바퀴)" if 마지막 else ""), 상세

def 문제목록_글(걸림, p, 상세=None):
    if 상세:
        줄 = [f"- (형태 검사 · 꼭 고친다) {x}" for x in 상세.get("형태치명", [])]
        줄 += [f"- (형태 검사) {x}" for x in 상세.get("형태고칠것", [])]
        줄 += [f"- (사실 · 꼭 고친다) {x}" for x in 상세.get("편집치명", [])]
        줄 += [f"- (문장 · 짜임) {x}" for x in 상세.get("편집고칠것", [])]
        return "\n".join(줄)
    줄 = [f"- (형태 검사) {x}" for x in 걸림]
    if p:
        줄 += [f"- ({x.종류}) {x.어디} — {x.설명}" for x in p.문제들]
    return "\n".join(줄)

print("올림 판정 준비 끝")

# ## 17. 상태 하나 — RunState

START, END = "재료모으기", "끝"

class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    # 입력
    material: Material
    지정: dict = {}                  # 접근 · 온도 · 시작점 · 배치를 손으로 정할 때
    # 재료
    links: list = []
    pages: list = []
    blocked: list = []
    재료: list = []                  # 원문 포함
    판정원본: list = []
    감독말: list = []
    한줄평: list = []                # 씨네21 전문가 한줄평
    씨네21: Optional[dict] = None
    보강됨: bool = False
    관점표: str = ""
    # 한 바퀴
    주문: Optional[기획주문] = None
    개요_: Optional[개요] = None
    글: Optional[Article] = None
    고친곳: list = []
    걸림: list = []
    판정: Optional[편집판정] = None
    무게상세: dict = {}
    기획재시도한: int = 0
    올림: bool = False
    이유: str = ""
    지난문제: str = ""
    # 세는 것
    바퀴: int = 0
    steps: int = 0
    남은콜: int = LLM_CALL_CAP
    # 결과
    기록: list = []
    상태: str = "진행"              # 진행 / 올림 / 탈락 / 평없음 / 상한
    로그: list = []
    저장경로: Optional[str] = None
    멈춤: str = ""
    다음: str = ""

    def log(self, s):
        self.로그.append(s)
        print(s)

print("상태 준비 끝")

# ## 18. 마디

def n_재료모으기(st: RunState) -> RunState:
    m = st.material
    pages = []
    # 씨네21 — 영화 페이지 한줄평 + 기사
    c = cine21_영화찾기(m.title, m.year, m.director)
    if c:
        st.한줄평, st.씨네21 = cine21_한줄평(c["movie_id"])
        페이지감독 = (st.씨네21 or {}).get("감독", "")
        if m.director and 페이지감독 and _norm(m.director) not in _norm(페이지감독):
            st.log(f"  씨네21: {c['이름']} (movie_id {c['movie_id']}) 감독이 {페이지감독}다. 다른 영화라 한줄평을 버린다")
            st.한줄평, st.씨네21 = [], None
        else:
            st.log(f"  씨네21: {c['이름']} (movie_id {c['movie_id']}) · 한줄평 {len(st.한줄평)}개")
    else:
        st.log("  씨네21: 영화 페이지를 못 찾았다")
    기사들 = cine21_기사찾기(m.title, m.director)
    with ThreadPoolExecutor(max_workers=3) as ex:                 # 같은 사이트는 _site_wait가 1초씩 띄운다
        읽은 = list(ex.map(cine21_기사읽기, 기사들))
    씨네기사 = pre_filter([p for p in 읽은 if p], m)[:씨네21기사수]
    pages += 씨네기사
    st.log(f"  씨네21 기사 {len(기사들)}건 찾아 {sum(1 for p in 읽은 if p)}건 읽음 → 이 영화 기사 {len(씨네기사)}건")
    # 검색으로 다른 매체
    st.links = collect_links(m)
    남는칸 = PAGE_LIMIT - len(pages)
    읽음, st.blocked = read_pages(st.links, 남는칸) if 남는칸 > 0 else ([], [])
    pages += 읽음
    seen, st.pages = set(), []
    for p in pages:
        if p["url"] not in seen:
            seen.add(p["url"]); st.pages.append(p)
    return st

def n_미리거르기(st: RunState) -> RunState:
    st.pages = pre_filter(st.pages, st.material)[:PAGE_LIMIT + 2]
    return st

def n_재료판정(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.판정원본 = judge_pages(st.pages, st.material, b, log=st.log)
    st.남은콜 = b["남은콜"]
    st.재료, st.감독말 = keep_rules(st.판정원본)
    매체들 = ", ".join(dict.fromkeys(r["매체"] for r in st.재료)) or "-"
    st.log(f"  검색 링크 {len(st.links)} / 읽은 페이지 {len(st.pages)} / robots 막힘 {len(st.blocked)} / 남은 평론 {len(st.재료)} ({매체들}) / 장면메모 {sum(len(r['장면메모']) for r in st.재료)} / 감독 말 {sum(len(x['말']) for x in st.감독말)} / 한줄평 {len(st.한줄평)}")
    return st

def 보강_링크(m: Material):
    t, d = m.title, (m.director or "")
    말 = [f"{t} 인터뷰 감독", f"{t} 제작기", f"{t} 영화 해석", f"{t} {d} interview", f"{t} {m.year} review analysis"]
    out = []
    for q in dict.fromkeys(말):
        out += 검색(q, n=10)
    seen, uniq = set(), []
    for u in out:
        u = u.split("#")[0]
        if u in seen or 차단됐나(u) or "cine21.com" in u:
            continue
        seen.add(u); uniq.append(u)
    return uniq

def n_재료보강(st: RunState) -> RunState:
    st.보강됨 = True
    있던 = {p["url"] for p in st.pages}
    링크 = [u for u in 보강_링크(st.material) if u not in 있던]
    읽음, 막힘 = read_pages(링크, 보강페이지)
    st.blocked += 막힘
    새페이지 = pre_filter([p for p in 읽음 if p["url"] not in 있던], st.material)[:보강페이지]
    if not 새페이지:
        st.log("  보강: 더 찾은 것이 없다")
        return st
    b = {"남은콜": st.남은콜}
    더 = judge_pages(새페이지, st.material, b, log=st.log)
    st.남은콜 = b["남은콜"]
    st.pages += 새페이지
    st.판정원본 += 더
    st.재료, st.감독말 = keep_rules(st.판정원본)
    st.log(f"  보강: 페이지 {len(새페이지)}장 더 읽음 → 평론 {len(st.재료)} / 장면메모 {sum(len(r['장면메모']) for r in st.재료)} / 감독 말 {sum(len(x['말']) for x in st.감독말)}")
    return st

def n_관점묶기(st: RunState) -> RunState:
    st.관점표 = views_text(group_views(st.재료), st.한줄평)
    return st

_조건자물쇠, _조건판 = threading.Lock(), {}

def _조건판_새로():
    with _조건자물쇠:
        _조건판.clear(); _조건판["앞배치"] = []

def _조건뽑기():
    if not 조건돌려쓰기:
        return random.choice(APPROACHES), random.choice(TEMPERATURES), random.choice(PERSONAS)
    with _조건자물쇠:
        out = []
        for 이름, 목록 in (("접근", APPROACHES), ("온도", TEMPERATURES), ("시작점", PERSONAS)):
            남은 = _조건판.get(이름) or []
            if not 남은:
                남은 = list(목록); random.shuffle(남은)
            out.append(남은.pop(0)); _조건판[이름] = 남은
        return tuple(out)

def _배치기록(배치):
    with _조건자물쇠:
        l = _조건판.setdefault("앞배치", []); l.append(배치); del l[:-2]

def _피할배치():
    with _조건자물쇠:
        return list(_조건판.get("앞배치", []))

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    m = st.material
    if st.주문 is None:
        접근, 온도, 시작점 = _조건뽑기()
        접근 = st.지정.get("approach") or 접근; 온도 = st.지정.get("temperature") or 온도; 시작점 = st.지정.get("persona") or 시작점
    else:
        접근, 온도, 시작점 = st.주문.접근, st.주문.온도, st.주문.시작점
    st.주문 = 기획주문(영화정보=영화_text(m), 관점표=st.관점표, 발췌=발췌_text(st.재료), 감독말=말_text(st.감독말),
                     접근=접근, 온도=온도, 시작점=시작점, 피할배치=_피할배치(),
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
        if st.지정.get("layout") in LAYOUTS:
            o.배치 = st.지정["layout"]
        if o.배치 not in LAYOUTS:
            o.배치 = random.choice(LAYOUTS)
        o.장면셋 = o.장면셋[:4]
        _배치기록(o.배치)
        st.log(f"  주문: {접근} · {온도} · {시작점} / 배치: {o.배치} / 장면 {len(o.장면셋)}개 ({', '.join(x[:20] for x in o.장면셋)}) / 사실 {len(o.사실목록)}개")
        if len(o.사실목록) < 8:
            st.log("  사실목록이 여덟 개도 안 된다. 글이 빌 것이다")
    return st

def n_작가(st: RunState) -> RunState:
    m = st.material
    try:
        st.글 = 작가.run_sync("쓴다.", deps=집필주문(영화한줄=f"{m.title} ({m.year}) · 감독 {m.director or '모름'}", 개요=st.개요_, 온도=st.주문.온도,
                                                   접근=st.주문.접근, 시작점=st.주문.시작점, 대중평가=m.reception or "", 표기=", ".join(m.cast)),
                             usage_limits=UsageLimits(request_limit=3)).output
        st.글.sources = _출처(st)
    except Exception as e:
        st.이유 = f"집필 실패: {e}"
        st.log(f"  {st.이유}")
    st.남은콜 -= 1
    return st

def _출처(st: RunState):
    out = list(st.material.source_links)
    if st.씨네21:
        out.append(st.씨네21["url"])
    out += [r["링크"] for r in st.재료] + [x["링크"] for x in st.감독말]
    return list(dict.fromkeys(u for u in out if u))

def _검사(st):
    return 형태검사(st.글.body, st.재료, st.material, 장면셋=st.개요_.장면셋 if st.개요_ else None, 제목=st.글.title)

def n_교정자(st: RunState) -> RunState:
    치명, 고칠것, _ = 형태무게(_검사(st))
    if not 치명 and not 고칠것:
        st.고친곳 = []
        st.log("  교정자를 건너뛴다. 형태 검사에 걸린 것이 없다")
        return st
    try:
        교 = 교정자.run_sync(st.글.body, usage_limits=UsageLimits(request_limit=2)).output
        st.글 = Article(title=st.글.title, body=교.본문, sources=st.글.sources)
        st.고친곳 = 교.고친곳
    except Exception as e:
        st.고친곳 = [f"교정 실패: {e}"]
        st.log(f"  교정 실패: {e}")
    st.남은콜 -= 1
    return st

def n_형태검사(st: RunState) -> RunState:
    st.걸림 = _검사(st)
    return st

def n_편집국장(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    앞판 = st.기록[-1] if st.기록 else None
    종류 = "첫 판" if not 앞판 else "새로 씀"
    st.판정 = 편집국장_읽기(st.글, st.개요_, st.재료, st.관점표, st.material, b, 감독말=st.감독말, 앞판=앞판, 차례=st.바퀴, 종류=종류, 주문=st.주문)
    st.남은콜 = b["남은콜"]
    st.올림, st.이유, st.무게상세 = 올릴까(st.걸림, st.판정, 바퀴=st.바퀴)
    if 앞판 and st.판정 and 앞판.get("판정"):
        대조 = st.판정.앞판대조
        세기 = lambda 말: sum(1 for x in 대조 if x.strip().startswith(말))
        st.log(f"  앞 판 {앞판['판정'].점수}점 → {st.판정.점수}점 / 고쳐짐 {세기('고쳐짐')} · 남음 {세기('남음')} · 새로 생김 {세기('새로 생김')} · 못 봄 {세기('앞 판에서 못 봄')}" + (f" / {st.판정.점수설명}" if st.판정.점수설명 else ""))
    문제수 = len(st.판정.문제들) if st.판정 else 0
    d = st.무게상세
    무게줄 = (f" / 치명 {len(d.get('형태치명', [])) + len(d.get('편집치명', []))} · 고칠것 {len(d.get('형태고칠것', [])) + len(d.get('편집고칠것', []))} · 사소 {len(d.get('형태사소', [])) + len(d.get('편집사소', []))}") if d else ""
    st.기록.append({"차례": st.바퀴, "종류": 종류, "개요": st.개요_, "글": st.글, "고친곳": st.고친곳, "걸림": st.걸림, "판정": st.판정,
                   "올림": st.올림, "이유": st.이유, "무게": d, "조건": (st.주문.접근, st.주문.온도, st.주문.시작점, st.개요_.배치 if st.개요_ else "")})
    st.log(f"  {st.바퀴}차: {'올림' if st.올림 else '탈락'} / {st.이유} / 교정 {len(st.고친곳)}곳 / 편집국장 문제 {문제수}건{무게줄}")
    if not st.올림:
        st.지난문제 = 문제목록_글(st.걸림, st.판정, st.무게상세)
    return st

def n_저장(st: RunState) -> RunState:
    st.상태 = "올림" if st.올림 else "탈락"
    p = 저장(st)
    st.저장경로 = str(p) if p else None
    if p:
        st.log(f"  저장: {p}")
    return st

print("마디 준비 끝")

# ## 19. 갈림길 + LangGraph (음악 v4.0과 같은 방식. 갈림길은 마디 안에서 돈다)

def after_재료판정(st):
    if not st.보강됨 and st.남은콜 >= 보강페이지 + 4 and (len(st.재료) < 보강목표 or sum(len(r["장면메모"]) for r in st.재료) == 0):
        st.log("  재료가 얇다. 인터뷰 · 제작기 · 해석 글로 더 찾는다")
        return "재료보강"
    if len(st.재료) < MIN_REVIEWS:
        st.이유 = f"평론 {len(st.재료)}편 (최소 {MIN_REVIEWS}편). 한줄평 {len(st.한줄평)}개"
        st.log(f"  평이 모자란다. {st.이유}")
        st.상태 = "평없음"
        return END
    return "관점묶기"

def after_기획자(st):
    if st.개요_ is None:
        if st.기획재시도한 < 기획재시도 and st.남은콜 >= 4:
            st.기획재시도한 += 1; st.바퀴 -= 1
            st.log(f"  기획을 다시 부른다 ({st.기획재시도한}/{기획재시도})")
            return "기획자"
        st.상태 = "탈락"
        return END
    return "작가"

def after_작가(st):
    if st.글 is None:
        if st.기획재시도한 < 기획재시도 and st.남은콜 >= 3:
            st.기획재시도한 += 1
            st.log(f"  집필을 다시 부른다 ({st.기획재시도한}/{기획재시도})")
            return "작가"
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
    "재료모으기": (n_재료모으기, 그냥("미리거르기")),
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

from langgraph.graph import StateGraph, START as 그래프시작, END as 그래프끝

_앱들, _앱잠금 = {}, threading.Lock()

def _마디(이름, fn, edge, 멈출마디):
    def 돌기(st: RunState) -> RunState:
        if st.steps >= MAX_STEPS:
            st.상태 = "상한"; st.log(f"  마디 수 상한 {MAX_STEPS}. 멈춘다"); st.멈춤 = "상한"
            return st
        st.steps += 1
        try:
            st = fn(st)
        except Exception as e:
            st.log(f"  마디 [{이름}] 실패: {e}"); st.상태 = "탈락"; st.이유 = st.이유 or f"마디 [{이름}] 실패: {e}"; st.멈춤 = "실패"
            return st
        st.다음 = END if (멈출마디 and 이름 == 멈출마디) else edge(st)
        return st
    return 돌기

def _고르기(st: RunState):
    return 그래프끝 if (st.멈춤 or st.다음 == END) else st.다음

def 그래프(멈출마디=None):
    with _앱잠금:
        if 멈출마디 not in _앱들:
            g = StateGraph(RunState)
            for 이름, (fn, edge) in GRAPH.items():
                g.add_node(이름, _마디(이름, fn, edge, 멈출마디))
                g.add_conditional_edges(이름, _고르기)
            g.add_edge(그래프시작, START)
            _앱들[멈출마디] = g.compile()
        return _앱들[멈출마디]

def run_graph(st: RunState, 멈출마디=None) -> RunState:
    out = 그래프(멈출마디).invoke(st, config={"recursion_limit": MAX_STEPS + 10})
    return RunState.model_validate(out)

print("갈림길 준비 끝. 마디", len(GRAPH), "개")

# ## 20. 저장 — 마크다운

def _safe_name(text):
    return re.sub(r"\s+", "_", re.sub(r'[\\/:*?"<>|]', "", text).strip())[:60]

def _최고판(st: RunState):
    """탈락이면 기록 중 편집국장 점수가 제일 높은 판. 마지막 판이 최고면 (None, "")."""
    r = st.기록[-1] if st.기록 else None
    if not (최고판저장 and not st.올림 and len(st.기록) > 1):
        return None, ""
    후보 = [k for k in st.기록 if k.get("판정") is not None and k.get("글") is not None]
    best = max(후보, key=lambda k: (k["판정"].점수, k["차례"])) if 후보 else None
    if best is None or best is r:
        return None, ""
    return best, (f"최고 판 {best['차례']}차({best['판정'].점수}점) 글을 저장했다. 마지막 판은 {r['차례']}차" + (f"({r['판정'].점수}점)" if r.get("판정") else "(편집국장 실패)"))

def 저장(st: RunState):
    if not st.기록:
        return None
    m, r = st.material, st.기록[-1]
    best, 최고줄 = _최고판(st)
    if best:
        st.log("  탈락 — " + 최고줄); r = best
    g = r["글"]
    폴더 = OUT / ("올림" if st.올림 else "탈락")
    path = 폴더 / f"{_safe_name(m.title)}_{datetime.datetime.now():%m%d_%H%M%S}.md"
    출처줄 = [f"- {x['매체']}{' · ' + x['필자'] if x.get('필자') else ''} — {x['링크']}" for x in st.재료]
    출처줄 += [f"- (감독 · 배우 말) {x['매체']} — {x['링크']}" for x in st.감독말]
    출처줄 += [f"- {u}" for u in m.source_links] + ([f"- 씨네21 영화 페이지 — {st.씨네21['url']}"] if st.씨네21 else [])
    출처줄 += [f"- (본문 안 읽음, 링크만) {u}" for u in st.blocked[:20]]
    로그 = []
    for k in st.기록:
        접근, 온도, 시작점, 배치 = k["조건"]
        로그.append(f"### {k['차례']}차 ({k.get('종류', '')}) — {'올림' if k['올림'] else '탈락'} / {k['이유']}")
        로그.append(f"- 조건: {접근} · {온도} · {시작점} · {배치}")
        if k["걸림"]:
            로그.append(f"- 형태 검사: {', '.join(k['걸림'])}")
        if k["고친곳"]:
            로그.append("- 교정자가 고친 곳:"); 로그 += [f"  - {x}" for x in k["고친곳"][:12]]
        if k["판정"]:
            로그.append(f"- 편집국장 {k['판정'].점수}점: {k['판정'].한줄평}")
            if k["판정"].점수설명:
                로그.append(f"- 앞 판 대비: {k['판정'].점수설명}")
            로그 += [f"  - (앞 판) {x}" for x in k["판정"].앞판대조]
            로그 += [f"  - ({x.종류}) {x.어디[:60]} — {x.설명}" for x in k["판정"].문제들]
            if k["판정"].확인목록:
                로그.append("- 확인 목록 (재료 밖이지만 널리 알려진 것으로 봄. 사람이 본다):"); 로그 += [f"  - {x}" for x in k["판정"].확인목록]
    lines = [f"# {g.title}", "", g.body, "", "---", "", "## 영화",
             f"- {m.title} ({m.year}) · 감독 {m.director or '모름'} · {', '.join(m.genres)} · {m.runtime_min or '?'}분 · {m.rating or '등급 모름'}",
             f"- 개봉: {m.release_date or '모름'}", f"- 대중 평가 갈래: {m.reception or '없음'}",
             "", "## 출처"] + (출처줄 or ["- 없음"]) + \
            ["", f"## 개요 ({r['차례']}차)", f"- 주제: {r['개요'].주제}", f"- 장면: {', '.join(r['개요'].장면셋) or '-'}", f"- 누구에게: {r['개요'].누구에게}",
             "", "## 판정 기록"] + ([f"- {최고줄}"] if 최고줄 else []) + 로그 + ["", f"마디 {st.steps}개 · 모델 요청 {LLM_CALL_CAP - st.남은콜}회 · {VERSION}"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

print("저장 준비 끝")

# ## 21. 한 편 돌리기 — 백엔드가 부르는 모양 (V2.0과 같다)
#
# `produce(material)` → dict. 상태는 다섯 값 중 하나다. 백엔드 `movie_agent_persistence`가 그 이름으로 저장한다.
# 로그 줄의 단계 이름도 계약대로 "형태검사" · "판정관"이다. 판정관 줄이 편집국장 판정이다(백엔드가 "판정관"만 judgment_log로 옮긴다).
# 그래프는 동기(run_sync)라 async 루프 안에서 못 돈다. 스레드 하나에서 새 루프와 새 모델 클라이언트로 돌린다(애니 v0.6 `_새모델`과 같은 이유).

상태_이름 = {"올림": "올림", "탈락": "탈락보관", "평없음": "재료부족", "상한": "탈락보관", "진행": "탈락보관"}

def _새모델():
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    return OpenAIResponsesModel(MODEL.split(":", 1)[1], provider=OpenAIProvider(api_key=os.environ[KEY_ENV], http_client=httpx.AsyncClient(timeout=httpx.Timeout(600.0))))

모델에이전트들 = ("재료판정관", "기획자", "작가", "교정자", "편집국장")

def _돌리기(st: RunState, 멈출마디=None, 새루프=False) -> RunState:
    if not 새루프:
        return run_graph(st, 멈출마디)
    import contextlib
    asyncio.set_event_loop(asyncio.new_event_loop())
    with contextlib.ExitStack() as es:
        mdl = _새모델()
        for name in 모델에이전트들:
            es.enter_context(globals()[name].override(model=mdl))
        return run_graph(st, 멈출마디)

def _결과(st: RunState, 이유=None) -> dict:
    상태 = 상태_이름.get(st.상태, "탈락보관")
    best, 최고줄 = _최고판(st)
    r = best or (st.기록[-1] if st.기록 else None)
    글 = r["글"] if r else st.글
    확인 = list(dict.fromkeys(x for k in st.기록 if k.get("판정") for x in k["판정"].확인목록))
    로그 = []
    for k in st.기록:
        로그.append({"회차": k["차례"] - 1, "단계": "형태검사", "종류": k.get("종류"), "결과": k["걸림"]})
        p = k.get("판정")
        row = {"회차": k["차례"] - 1, "단계": "판정관", "종류": k.get("종류"), "결과": "올림" if k["올림"] else "탈락", "이유": k["이유"],
               "점수": (p.점수 if p else None), "문제": ([f"({x.종류}) {x.어디[:60]} — {x.설명}" for x in p.문제들] if p else []),
               "조건": {"접근": k["조건"][0], "온도": k["조건"][1], "시작점": k["조건"][2], "배치": k["조건"][3]}}
        if p and p.점수설명:
            row["앞 판 대비"] = p.점수설명
        if p and p.앞판대조:
            row["앞 판 대조"] = p.앞판대조
        로그.append(row)
    if 최고줄:
        로그.append({"회차": len(st.기록), "단계": "최고판", "결과": 최고줄})
    out = {"상태": 상태, "이유": 이유 or st.이유, "로그": 로그, "재료": {"평론": len(st.재료), "한줄평": len(st.한줄평), "감독말": sum(len(x["말"]) for x in st.감독말), "읽은 페이지": len(st.pages)},
           "확인필요": 확인, "최고판": 최고줄, "판": VERSION, "저장경로": st.저장경로}
    if 글 is not None:
        o = r["개요"] if r else st.개요_
        out["글"] = 글
        out["주문"] = {"approach": st.주문.접근, "temperature": st.주문.온도, "persona": st.주문.시작점, "layout": (o.배치 if o else ""),
                       "topic": "영화", "주제": (o.주제 if o else ""), "장면셋": (o.장면셋 if o else []), "누구에게": (o.누구에게 if o else "")} if st.주문 else None
    return out

def _produce_sync(material: Material, 지정=None, 새루프=False) -> dict:
    ok, why = genre_ok(material)
    if not ok:
        return {"상태": "대상아님", "이유": why, "로그": []}
    ok, why = enough_material(material)
    if not ok:
        return {"상태": "재료부족", "이유": why, "로그": []}
    st = _돌리기(RunState(material=material, 지정=dict(지정 or {})), 새루프=새루프)
    return _결과(st)

async def produce(material: Material, topic="영화", tag_topic=None, approach=None, temperature=None, persona=None, layout=None) -> dict:
    """백엔드 · 노트북 · 명령줄이 다 이걸 부른다. 그래프는 스레드에서 새 루프로 돈다."""
    지정 = {k: v for k, v in (("approach", approach), ("temperature", temperature), ("persona", persona), ("layout", layout)) if v}
    return await asyncio.to_thread(_produce_sync, material, 지정, True)

def show(out: dict):
    print("상태:", out["상태"])
    if out.get("이유"):
        print("이유:", out["이유"])
    for row in out.get("로그", []):
        print(json.dumps(row, ensure_ascii=False, default=str)[:300])
    if out.get("글"):
        o = out.get("주문") or {}
        print(f"\n[{o.get('approach')} · {o.get('temperature')} · {o.get('persona')} · {o.get('layout')}]")
        print("=" * 60); print(out["글"].title); print("-" * 60); print(out["글"].body); print("-" * 60)
        print("출처:", ", ".join(out["글"].sources))
        if out.get("확인필요"):
            print("\n사람이 확인할 문장:"); [print(" -", t) for t in out["확인필요"]]

def to_markdown(out: dict) -> str:
    a = out.get("글")
    lines = [f"# {a.title}" if a else "# (글 없음)", "", f"**상태** {out['상태']} · **판** {VERSION}" + (f" · **이유** {out['이유']}" if out.get("이유") else "")]
    if out.get("최고판"):
        lines.append(f"**최고 판** {out['최고판']}")
    if a:
        lines += ["", "---", "", a.body.strip(), "", "---", "", "## 출처", ""] + [f"- {u}" for u in a.sources]
    if out.get("확인필요"):
        lines += ["", "## 사람이 확인할 문장", ""] + [f"- [ ] {t}" for t in out["확인필요"]]
    return "\n".join(lines)

def save_md(out: dict):
    """백엔드 호환. 그래프 저장(`저장()`)이 이미 판정 기록까지 남겼으면 그 경로를 돌려준다."""
    if out.get("저장경로"):
        return pathlib.Path(out["저장경로"])
    path = OUT / f"{_safe_name(out['글'].title if out.get('글') else '글없음')}_{datetime.datetime.now():%m%d_%H%M%S}.md"
    path.write_text(to_markdown(out), encoding="utf-8")
    return path

print("한 편 돌리기 준비 끝")

# ## 22. 점검 · 뽑기만 · 돌리기 · zip

def 점검():
    print("[씨네21]")
    c = cine21_영화찾기("헤어질 결심", 2022, "박찬욱")
    print("  영화 찾기:", c)
    if c:
        h, page = cine21_한줄평(c["movie_id"])
        print(f"  한줄평 {len(h)}개 · 페이지 감독 {page['감독'] or '?'}" + (f" — {h[0]['이름']} {h[0]['점수']}: {h[0]['한줄']}" if h else ""))
    기사 = cine21_기사찾기("헤어질 결심", "박찬욱", n=3)
    print(f"  기사 {len(기사)}건", [(x['제목'][:30], x['필자'], x['날짜']) for x in 기사])
    print("\n[검색 엔진]")
    r = 검색("헤어질 결심 2022 영화 리뷰", n=8)
    for u in r[:8]:
        print(f"   {'차단' if 차단됐나(u) else ('열림' if robots_ok(u) else '막힘')}  {매체이름(u):<14} {u[:80]}")
    print("\n[매체 robots]")
    for m in MEDIA:
        u = f"https://www.{m['domain']}/"
        print(f"  {m['name']:<18} {'열림' if robots_ok(u) else '막힘'}")

def 뽑기만(title, year=None):
    """모델 없이 재료 모으기까지. 무엇을 읽어 오는지 본다."""
    m = fetch_material(title, year)
    if m is None:
        print("TMDB에서 못 찾았다"); return None
    print(material_to_text(m)[:800])
    st = run_graph(RunState(material=m, 남은콜=0), 멈출마디="미리거르기")
    print(f"\n  재료 후보 페이지 {len(st.pages)}개 (검색 링크 {len(st.links)}, robots 막힘 {len(st.blocked)}, 한줄평 {len(st.한줄평)}):")
    for p in st.pages:
        print(f"   - [{p.get('매체')}{' · ' + p['필자'] if p.get('필자') else ''}] {p['제목'][:60]}  {len(p['본문'])}자")
    return st

def zip_outputs():
    zip_path = OUT.parent / f"movie_review_{datetime.datetime.now():%m%d_%H%M}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob("*.md"):
            z.write(p, p.relative_to(OUT))
    print("zip:", zip_path)
    return zip_path

def download_all():
    return zip_outputs()

def 끝나면zip():
    if not 콜랩_끝나면zip or "google.colab" not in sys.modules or not any(OUT.rglob("*.md")):
        return
    from google.colab import files
    files.download(str(zip_outputs()))

TEST_TITLES = [("괴물", 2006), ("헤어질 결심", 2022), ("프로젝트 헤일메리", 2026), ("케빈에 대하여", 2011), ("판의 미로", 2006)]

async def cmd_one(title, year, **지정):
    m = fetch_material(title, year)
    if m is None:
        print(f"[{title}] TMDB에서 못 찾았다"); return None
    print(material_to_text(m)[:600]); print()
    out = await produce(m, **지정)
    show(out)
    끝나면zip()
    return out

async def cmd_list(titles=TEST_TITLES):
    _조건판_새로()
    results = []
    for t, y in titles:
        m = fetch_material(t, y)
        if m is None:
            print(f"[{t}] TMDB에서 못 찾았다"); continue
        print(f"\n===== {m.title} ({m.year}) · 감독 {m.director} =====")
        r = await produce(m)
        results.append((m.title, r))
        print(f"[{m.title}] {r['상태']}" + (f" — {r.get('이유', '')}" if r.get("이유") else ""))
    끝나면zip()
    return results

async def cmd_random(n=5, seed=None):
    _조건판_새로()
    cands = pick_random_movies(n, seed=seed)
    print("뽑힌 영화:", [(c["title"], c["year"]) for c in cands])
    for c in cands:
        m = fetch_material_by_id(c["id"], fallback_title=c["title"] or "")
        print(f"\n===== {m.title} ({m.year}) · 감독 {m.director} =====")
        r = await produce(m)
        print(f"[{m.title}] {r['상태']}" + (f" — {r.get('이유', '')}" if r.get("이유") else ""))
    끝나면zip()

def main(argv=None):
    import argparse
    명령들 = ("check", "pick", "one", "list", "random", "zip")
    if argv is None and (len(sys.argv) < 2 or sys.argv[1] not in 명령들):
        print("노트북에서는 명령줄 대신 함수를 바로 부른다.")
        print("  점검()                              씨네21 · 검색 엔진 · 매체 robots. 모델 안 부른다")
        print('  뽑기만("괴물", 2006)                 재료 모으기까지. 모델 안 부른다')
        print('  await cmd_one("괴물", 2006)          한 편')
        print("  await cmd_list()                    테스트 목록 다섯 편")
        print("  await cmd_random(5)                 무작위 다섯 편")
        return
    ap = argparse.ArgumentParser(description="영화 리뷰 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="씨네21 · 검색 · robots 점검. 모델 안 부른다")
    p = sub.add_parser("pick", help="재료 모으기까지. 모델 안 부른다"); p.add_argument("title"); p.add_argument("year", type=int, nargs="?")
    a = sub.add_parser("one", help="한 편"); a.add_argument("title"); a.add_argument("year", type=int)
    sub.add_parser("list", help="테스트 목록 다섯 편")
    r = sub.add_parser("random", help="무작위 N편"); r.add_argument("n", type=int, nargs="?", default=5); r.add_argument("--seed", type=int, default=None)
    sub.add_parser("zip", help="out/movie_review 를 zip으로 묶는다")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        점검()
    elif args.cmd == "pick":
        뽑기만(args.title, args.year)
    elif args.cmd == "one":
        asyncio.run(cmd_one(args.title, args.year))
    elif args.cmd == "list":
        asyncio.run(cmd_list())
    elif args.cmd == "random":
        asyncio.run(cmd_random(args.n, seed=args.seed))
    elif args.cmd == "zip":
        zip_outputs()

if __name__ == "__main__":
    main()
