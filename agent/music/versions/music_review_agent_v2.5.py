# # music_review_agent_v2.7 — 음악 리뷰 에이전트
#
# 키는 `.env` 또는 환경변수 `LLM_KEY`에서 읽는다. 결과는 `agent/out/music/올림|탈락/`에 쌓인다.
#
# `pip install -r ../requirements.txt`
#
# 앨범 하나를 뽑아 리뷰 한 편을 쓴다.
# 상태 하나 · 마디 · 갈림길 모양이다. 랭그래프로 옮길 때 마디와 갈림길 함수를 그대로 꽂는다.
#
# ```
# 앨범뽑기 → 평론모으기 → 미리거르기 → 재료판정 ─(평 모자람)→ 앨범뽑기
#                                           └→ 관점묶기 → 기획자 → 작가 → 교정자 → 형태검사 → 편집국장
#                                                             ↑                                  │
#                                                             └──── 걸리면 문제 목록만 들고 (세 번) ──┘
#                                                                                                └→ 저장 → 끝
# ```
#
# 편집국장이 걸면 기획자부터 다시 쓴다. 세 번까지다.
# 평론 원문은 상태에 들고 간다. 편집국장이 대조하는 데 쓴다. 디스크에는 안 남긴다.
# 올린 글이 다섯 편이 될 때까지 앨범을 계속 뽑는다.
#
# v2.6에서 바뀐 것
# - 한국 앨범 후보와 재료를 이즘 API(api.izm.co.kr)와 아이돌로지 API(WordPress)에서 가져온다.
#   화면을 읽어 오지 않는다. 사이트가 자기 화면을 그리는 데 쓰는 공개 JSON을 그대로 읽는다. robots.txt는 그대로 본다.
# - 뽑은 리뷰의 본문이 재료 첫 번째로 들어간다. 검색으로 다시 찾지 않는다.
# - 명반 목록은 이즘 명반(국내 86 · 해외 322)을 쓴다. 롤링스톤 500은 위키백과에 목록이 없어서 뺐다.
# - 한국 앨범의 유명도는 위키백과 아티스트 문서로 본다. 앨범 문서는 한국 앨범 대부분에 없다.
# - MusicBrainz에서 못 찾아도 이즘 앨범 리뷰가 있으면 매체 정보로 간다. 한국은 EP(미니앨범)도 앨범으로 친다.
# - 프롬프트: 재료 판정(모음글 · 홍보문 · 표기), 기획자(평 하나일 때 · 수록곡 모를 때 · 사실마다 출처), 작가(표기), 편집국장(표기 차이 · 없는 다수 평)
#
# v2.7에서 바뀐 것
# - 콜랩처럼 asyncio 루프가 이미 도는 곳에서는 nest_asyncio를 건다. 기획자가 "This event loop is already running"으로 실패하던 원인이다.
# - 재료 판정 동시 요청을 스레드에서 같은 루프의 asyncio.gather로 바꿨다. "bound to a different event loop"가 나던 원인이다.
# - 기획 · 집필 · 교정 실패 이유를 로그에 찍는다. 검색 결과 0건은 실패로 안 찍는다.
# - 이즘 후보도 1970년 앞 앨범은 뺀다. MusicBrainz는 제목이 꼭 같고 연도가 가까운 것을 먼저 고른다.

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
MODEL       = "openai:gpt-5.6-luna"           # 영화 에이전트와 같은 모델
KEY_ENV     = "OPENAI_API_KEY"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

목표편수      = 5      # 올린 글이 이만큼 될 때까지 돈다
시도상한      = 15     # 앨범을 이만큼 봤는데 못 채우면 멈춘다
KR_RATIO      = 0.5    # 한국 앨범 비율. 5편이면 한국 3 · 해외 2
YEAR_FROM     = 1970
YEAR_TO       = datetime.date.today().year
PAGE_LIMIT    = 10     # 앨범 하나에 재료로 읽어볼 페이지 수 (API로 온 글 + 검색으로 찾은 글)
후보링크배수  = 6      # robots로 막히는 곳이 많아 검색 후보를 넉넉히 모은다
PER_MEDIA     = 2      # 매체당 남길 평론 개수
MIN_REVIEWS   = 2      # 해외 앨범. 이만큼 안 모이면 그 앨범을 버린다
한국최소평    = 1      # 한국 앨범. 열린 한국 매체가 이즘·아이돌로지뿐이라 하나로 둔다
동시판정      = 5      # 재료 판정을 한 번에 몇 페이지씩 모델에 보낼지
맥락만쓰기    = False  # 평이 없을 때 맥락만으로 쓸지. 지어낸 평이 계속 나와서 False로 둔다
최대후보앨범  = 12     # 한 편 만들 때 훑어볼 앨범 후보 수
REWRITE_LIMIT = 3      # 다시 쓰기 상한
PASS_SCORE    = 70     # 편집국장 점수가 이 아래면 다시 쓴다
LLM_CALL_CAP  = 30     # 앨범 하나에 허용할 모델 요청 수 (재료 10 + 한 바퀴 4 × 세 번)
CONTACT       = "menu-project@example.com"   # MusicBrainz가 요구하는 연락처. 실제 주소로 바꾼다

# v2.6
이즘후보페이지 = 3      # 이즘 리뷰 목록에서 무작위로 볼 페이지 수. 한 페이지 30건
이즘명반페이지 = 2      # 이즘 명반 목록에서 볼 페이지 수
MB없어도진행   = True   # MusicBrainz에서 못 찾아도 이즘 앨범 리뷰가 있으면 매체 정보로 간다
한국EP허용     = True   # 한국은 미니앨범(EP)도 앨범으로 친다
유명도기준     = {"KR": "아티스트", "기타": "앨범"}   # 위키백과에 이 문서가 있어야 통과
# ─────────────────────────────────────────────────────────────

def _secret(name):
    v = os.environ.get(name)
    if not v or not v.strip():
        raise RuntimeError(f"환경변수 '{name}'이 없다. agent/.env 파일에 {name}=... 을 적거나 셸에서 환경변수로 넣는다. (agent/.env.example 참고)")
    return v.strip()

# check · pick 은 모델을 안 부른다. 키 없이 돌아가게 둔다
_DRY = len(sys.argv) > 1 and sys.argv[1] in ("check", "pick")
try:
    os.environ[KEY_ENV] = _secret("LLM_KEY")
    print("키 읽음. 길이:", len(os.environ[KEY_ENV]))
except RuntimeError:
    if not _DRY:
        raise
    os.environ[KEY_ENV] = "dry-run"
    print("키 없음. check · pick 만 된다")

UA = f"menu-music-review-agent/1.0 ( {CONTACT} )"
HEADERS = {"User-Agent": UA}

OUT = HERE.parent / "out" / "music"
(OUT / "올림").mkdir(parents=True, exist_ok=True)
(OUT / "탈락").mkdir(parents=True, exist_ok=True)

# 노트북(콜랩)은 이벤트 루프가 이미 돌고 있어서 run_sync가 막힌다. 그때만 nest_asyncio를 건다
import asyncio
try:
    asyncio.get_running_loop()
    import nest_asyncio
    nest_asyncio.apply()
    print("노트북 안이다. nest_asyncio 걸었다")
except RuntimeError:
    pass

def _run_async(coro):
    # 코루틴 하나를 끝까지 돌린다. pydantic-ai의 run_sync와 같은 루프를 쓴다.
    # asyncio.run으로 루프를 새로 만들면 모델 클라이언트가 앞 루프에 묶여 있어서 다음 요청이 실패한다
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
# 매체 목록은 **앨범을 뽑을 때** 쓴다. 한국은 이즘·아이돌로지 API, 해외는 이즘 API와 아래 매체의 리뷰 목록이다.
#
# 검색으로 재료를 모을 때는 매체 목록을 안 쓴다. 검색 엔진이 찾아온 것 중에서 차단 목록에 걸린 주소만 뺀다.
# 나무위키·개인 블로그·커뮤니티·유튜브·스트리밍 소개문·가사 사이트가 차단 대상이다.
# 나머지는 다 읽고, 쓸지 버릴지는 여섯 값 판정이 정한다.

MEDIA = [
    # 한국 — API가 있는 곳. 리뷰 목록과 본문을 JSON으로 받는다
    {"name": "이즘",        "domain": "izm.co.kr",          "lang": "ko", "api": "izm"},
    {"name": "아이돌로지",  "domain": "idology.kr",         "lang": "ko", "api": "idology"},
    # 해외 — 점검에서 열린 곳. 첫 화면에서 리뷰 링크를 훑는다
    {"name": "Rolling Stone",    "domain": "rollingstone.com",   "lang": "en"},
    {"name": "Stereogum",        "domain": "stereogum.com",      "lang": "en"},
    {"name": "Consequence",      "domain": "consequence.net",    "lang": "en"},
    {"name": "Paste",            "domain": "pastemagazine.com",  "lang": "en"},
    {"name": "Angry Metal Guy",  "domain": "angrymetalguy.com",  "lang": "en"},
    {"name": "NME",              "domain": "nme.com",            "lang": "en"},
    {"name": "The Quietus",      "domain": "thequietus.com",     "lang": "en"},
    {"name": "The Guardian",     "domain": "theguardian.com",    "lang": "en"},
    {"name": "Kerrang!",         "domain": "kerrang.com",        "lang": "en"},
    {"name": "Mikiki",           "domain": "mikiki.tokyo.jp",    "lang": "ja"},
    {"name": "CINRA",            "domain": "cinra.net",          "lang": "ja"},
]

# 점검에서 막힌 곳. HTML은 못 읽는다. RSS가 열려 있으면 그것만 쓴다
막힌매체 = [
    {"name": "이즘 옛글",   "domain": "archive.izm.co.kr", "lang": "ko"},
    {"name": "음악취향Y",   "domain": "weiv.co.kr",        "lang": "ko"},
    {"name": "리드머",      "domain": "rhythmer.net",      "lang": "ko"},
    {"name": "온음",        "domain": "5unc.com",          "lang": "ko"},
    {"name": "힙합엘이",    "domain": "hiphople.com",      "lang": "ko"},
    {"name": "힙합플레이야","domain": "hiphopplaya.com",   "lang": "ko"},
    {"name": "Pitchfork",   "domain": "pitchfork.com",     "lang": "en"},
    {"name": "AllMusic",    "domain": "allmusic.com",      "lang": "en"},
    {"name": "Sputnikmusic","domain": "sputnikmusic.com",  "lang": "en"},
]

MEDIA_BY_DOMAIN = {m["domain"]: m for m in MEDIA + 막힌매체}

차단도메인 = [
    "namu.wiki", "namu.moe", "thewiki.kr",
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "wordpress.com", "blogspot.com",
    "dcinside.com", "fmkorea.com", "theqoo.net", "ruliweb.com", "clien.net",
    "instiz.net", "pann.nate.com", "reddit.com", "quora.com", "tumblr.com",
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com",
    "music.bugs.co.kr", "bugs.co.kr", "genie.co.kr", "melon.com", "flo.co.kr", "vibe.naver.com",
    "spotify.com", "music.apple.com", "soundcloud.com", "bandcamp.com",
    "genius.com", "azlyrics.com", "lyrics.co.kr", "klyrics.net", "musixmatch.com",
    "amazon.com", "coupang.com", "aladin.co.kr", "yes24.com", "discogs.com",
]

def _host(url):
    return urllib.parse.urlparse(url).netloc.lower().replace("www.", "")

def 차단됐나(url):
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in 차단도메인)

# 매체 목록에 없는 곳을 글에서 뭐라고 부를지. 없으면 "한 매체"로 쓴다
매체이름표 = {
    "atthebarrier.com": "At The Barrier",
    "music.mxdwn.com": "mxdwn",
    "bbc.co.uk": "BBC",
    "metalinjection.net": "Metal Injection",
    "distortedsoundmag.com": "Distorted Sound",
    "loudersound.com": "Louder",
    "blabbermouth.net": "Blabbermouth",
    "wikipedia.org": "위키백과",
}

def media_of(url):
    host = _host(url)
    for dom, m in MEDIA_BY_DOMAIN.items():
        if host == dom or host.endswith("." + dom):
            return m
    return None

def 매체이름(url):
    m = media_of(url)
    if m:
        return m["name"]
    h = _host(url)
    for d, n in 매체이름표.items():
        if h == d or h.endswith("." + d):
            return n
    return "한 매체"

# 해외 매체 안의 리뷰 목록 주소. 비어 있으면 도메인 첫 화면에서 시작한다
목록주소 = {}

def list_url(m):
    return 목록주소.get(m["domain"]) or f"https://www.{m['domain']}/"

print(f"열린 매체 {len(MEDIA)}곳, 막힌 매체 {len(막힌매체)}곳, 차단 도메인 {len(차단도메인)}개")

# ## 3. robots.txt 확인
#
# 막아둔 곳은 본문을 안 읽는다. 링크만 남긴다.
# robots.txt를 아예 못 가져오면 막힌 것으로 본다. 404면 허용으로 본다.

from urllib.robotparser import RobotFileParser

_robots = {}

def robots_ok(url):
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
            if r.status_code == 404:
                rp.parse([])              # 규칙이 없으면 다 열린 것이다
            elif r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                _robots[base] = None      # 못 읽었다. 막힌 것으로 본다
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

# 같은 사이트는 crawl_delay(기본 1초)만큼 띄운다. 다른 사이트는 안 기다린다
from concurrent.futures import ThreadPoolExecutor
import threading

_site_lock = {}
_site_last = {}
_lock_guard = threading.Lock()

def _site_wait(url):
    host = _host(url)
    with _lock_guard:
        lk = _site_lock.setdefault(host, threading.Lock())
    with lk:
        gap = crawl_delay(url) - (time.time() - _site_last.get(host, 0))
        if gap > 0:
            time.sleep(gap)
        _site_last[host] = time.time()

def api_get(url, params=None, timeout=20):
    # JSON API 한 번. robots를 먼저 보고, 같은 사이트는 1초에 한 번만 부른다
    if not robots_ok(url):
        return None
    _site_wait(url)
    try:
        r = requests.get(url, params=params, headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

print("robots 확인 준비 끝")

# ## 4. 이름 맞추기
#
# 한국 아티스트는 한글 이름과 영문 이름이 같이 쓰인다. 어디서 온 이름이든 같은 것으로 보게 한다.

def _norm(s):
    s = htmlmod.unescape(s or "").lower()
    s = re.sub(r"[\s\-_–—·.,:;!?'\"“”‘’()\[\]<>《》〈〉/&+*]", "", s)
    return s

def 같은이름(a, b, 느슨=False):
    # 기본은 완전 일치(띄어쓰기 · 기호 · 대소문자 무시). 느슨=True면 한쪽이 다른 쪽에 들어 있어도 같다고 본다
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if 느슨:
        return (len(a) >= 5 and a in b) or (len(b) >= 5 and b in a)
    return False

def _문서제목(t):
    # "Purple Heart (음반)" → "Purple Heart"
    return re.sub(r"\s*[\(（].*?[\)）]\s*$", "", t or "").strip()

def _괄호(t):
    # "Purple Heart (음반)" → "음반"
    m = re.search(r"[\(（]([^\)）]*)[\)）]\s*$", t or "")
    return m.group(1) if m else ""

def 이름들(album):
    # 앨범 정보에 들어 있는 아티스트 이름 전부. 한글 · 영문 · MusicBrainz 표기
    out = [album.get("artist")] + list(album.get("별칭", {}).get("아티스트", []))
    return [x for x in dict.fromkeys(x for x in out if x)]

def 제목들(album):
    out = [album.get("title")] + list(album.get("별칭", {}).get("앨범", []))
    return [x for x in dict.fromkeys(x for x in out if x)]

def 표기(album):
    # 글에 쓰는 표기. "자우림 (Jaurim)" 꼴. 영문 이름이 따로 있으면 한 번 붙인다
    kr = album.get("별칭", {}).get("한글")
    en = album.get("별칭", {}).get("영문")
    if kr and en and _norm(kr) != _norm(en):
        return f"{kr} ({en})"
    return kr or en or album.get("artist")

print("이름 맞추기 준비 끝")

# ## 5. 이즘 API
#
# 이즘(izm.co.kr)은 화면을 자바스크립트로 그린다. 화면이 부르는 공개 JSON 주소를 그대로 읽는다.
# 리뷰 목록 · 리뷰 본문 · 검색 · 아티스트 소개가 다 여기서 온다. 점수는 안 쓴다. 조회수는 후보 순서에만 쓴다.

from bs4 import BeautifulSoup

IZM_API  = "https://api.izm.co.kr/api"
IZM_POST = "https://www.izm.co.kr/posts?id={id}"
IZM_ARTIST = "https://www.izm.co.kr/detail/artist?id={id}"
_이즘쪽수 = {}

def _html_text(h):
    soup = BeautifulSoup(htmlmod.unescape(h or ""), "lxml")
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)

def izm_목록(detail, page=1, 명반=False):
    # detail: "국내" / "해외". 명반=True면 이즘이 명반으로 묶은 글만
    j = api_get(f"{IZM_API}/content/", {
        "main_category": "Review", "sub_category": "앨범", "detail_category": detail,
        "alum_category": "true" if 명반 else "", "search_keyword": "", "search_writer": "",
        "created_after": "", "created_before": "", "score_min": "", "score_max": "", "page": page})
    if not j:
        return [], 0
    n = j.get("count") or 0
    _이즘쪽수[(detail, 명반)] = max(1, -(-n // 30))
    return j.get("results") or [], n

def izm_쪽수(detail, 명반=False):
    if (detail, 명반) not in _이즘쪽수:
        izm_목록(detail, 1, 명반)
    return _이즘쪽수.get((detail, 명반), 1)

def izm_검색(keyword):
    j = api_get(f"{IZM_API}/content/search/", {"keyword": keyword})
    if not j:
        return [], []
    c = j.get("contents") or {}
    return (c.get("album_review_contents") or []), (j.get("artists") or [])

def izm_이름(item):
    arts = item.get("artists") or []
    kr = " · ".join(a.get("kr_name") for a in arts if a.get("kr_name")) or None
    en = " · ".join(a.get("en_name") for a in arts if a.get("en_name")) or None
    return kr, en

def izm_page(item):
    kr, en = izm_이름(item)
    who = kr or en or ""
    return {"url": IZM_POST.format(id=item["id"]), "막힘": False,
            "본문": _html_text(item.get("content"))[:12000],
            "제목": f"{who} - {item.get('title')} (이즘 앨범 리뷰)", "매체": "이즘"}

def izm_후보(item, src):
    kr, en = izm_이름(item)
    if not (kr or en) or not item.get("title"):
        return None
    y = None
    m = re.match(r"(\d{4})", str(item.get("publish_date") or ""))
    if m:
        y = int(m.group(1))
    if y and not (YEAR_FROM <= y <= YEAR_TO):
        return None                                        # 연도 범위 밖. 명반 목록에 1960년대가 섞여 있다
    title = item["title"].strip()
    # "#willpower (Deluxe Edition)" → 앨범 이름은 앞부분, 꼬리는 별칭으로
    본이름 = _문서제목(title) if _괄호(title) and len(_문서제목(title)) >= 2 else title
    return {"artist": kr or en, "artist_en": en, "artist_kr": kr, "title": 본이름, "title_원래": title,
            "src": src, "url": IZM_POST.format(id=item["id"]), "page": izm_page(item),
            "views": item.get("views") or 0, "year": y, "앨범확실": True}

def izm_같은앨범(album):
    # 검색 API로 같은 앨범을 다룬 이즘 글을 더 찾는다. 아티스트 소개는 맥락으로 돌린다
    정확, 비슷, 소개 = [], [], []
    seen = set()
    for kw in 제목들(album)[:2]:
        items, artists = izm_검색(kw)
        for it in items:
            if it.get("id") in seen or it.get("sub_category") != "앨범":
                continue
            kr, en = izm_이름(it)
            if not any(같은이름(n, a, 느슨=True) for n in 이름들(album) for a in (kr, en) if a):
                continue
            if any(같은이름(it.get("title"), t) for t in 제목들(album)):
                seen.add(it["id"]); 정확.append(izm_page(it))
            elif any(같은이름(it.get("title"), t, 느슨=True) for t in 제목들(album)):
                seen.add(it["id"]); 비슷.append(izm_page(it))
        for a in artists:
            if any(같은이름(n, x, 느슨=True) for n in 이름들(album) for x in (a.get("kr_name"), a.get("en_name")) if x):
                t = _html_text(a.get("content"))
                if len(t) > 200:
                    소개.append({"본문": t[:4000], "링크": IZM_ARTIST.format(id=a.get("id"))})
    # 이름이 꼭 같은 글이 있으면 그것만. 없으면 비슷한 것(같은 이름의 다른 판 등)까지. 앨범 판정관이 걸러 준다
    pages = 정확 if 정확 else 비슷
    return pages, 소개[:1]

print("이즘 API 준비 끝")

# ## 6. 아이돌로지 API
#
# 아이돌로지(idology.kr)는 WordPress다. robots.txt가 열어 둔 REST API로 글 목록과 본문을 받는다.
# Review는 앨범 하나를 다룬 글이고, Monthly · Weekly · 1st Listen은 여러 앨범을 한 글에서 다룬 모음글이다.

IDOLOGY_API = "https://idology.kr/wp-json/wp/v2"
IDOLOGY_CAT = {"review": 5, "monthly": 10032, "weekly": 10456, "1stlisten": 8}

def _wp_pages(posts, 꼬리):
    out = []
    for p in posts or []:
        t = htmlmod.unescape(BeautifulSoup(p.get("title", {}).get("rendered", ""), "lxml").get_text(" ", strip=True))
        body = _html_text(p.get("content", {}).get("rendered", ""))
        if len(body) < 200:
            continue
        out.append({"url": p.get("link"), "막힘": False, "본문": body[:12000],
                    "제목": f"{t} ({꼬리})", "매체": "아이돌로지", "wp제목": t})
    return out

def idology_목록(cat="review", page=1, n=20):
    j = api_get(f"{IDOLOGY_API}/posts", {"categories": IDOLOGY_CAT[cat], "per_page": n, "page": page,
                                          "_fields": "id,title,link,date,content"})
    return _wp_pages(j, "아이돌로지 리뷰")

def idology_검색(album, n=10):
    out, seen = [], set()
    for kw in 이름들(album)[:2]:
        j = api_get(f"{IDOLOGY_API}/posts", {"search": kw, "per_page": n,
                                              "_fields": "id,title,link,date,content,categories"})
        for p in _wp_pages(j, "아이돌로지"):
            if p["url"] in seen:
                continue
            low = (p["본문"] + " " + p["제목"]).lower()
            # 앨범 이름이 본문에 있어야 한다. 모음글은 그 대목만 판정관이 본다
            if any(_norm(t) and _norm(t) in _norm(low) for t in 제목들(album)):
                seen.add(p["url"])
                out.append(p)
    return out

print("아이돌로지 API 준비 끝")

# ## 7. MusicBrainz
#
# 점수는 안 쓴다. 발매일·참여자·레이블·수록곡만 가져온다.
# 한국 아티스트는 한글 이름으로 찾고, 안 되면 영문 이름으로 찾는다. 그래도 없으면 매체 정보로 간다.

MB = "https://musicbrainz.org/ws/2"
_last_mb = [0.0]

def mb_get(path, **params):
    wait = 1.1 - (time.time() - _last_mb[0])       # MusicBrainz는 1초에 한 번만 받는다
    if wait > 0:
        time.sleep(wait)
    params["fmt"] = "json"
    r = requests.get(f"{MB}/{path}", params=params, headers=HEADERS, timeout=20)
    _last_mb[0] = time.time()
    r.raise_for_status()
    return r.json()

def _year(s):
    m = re.match(r"(\d{4})", s or "")
    return int(m.group(1)) if m else None

def _lucene(s):
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s or "")

def _credit_names(g):
    out = []
    for c in g.get("artist-credit") or []:
        if c.get("name"):
            out.append(c["name"])
        if (c.get("artist") or {}).get("name"):
            out.append(c["artist"]["name"])
    return out

def mb_찾기(cand, want_kr):
    # cand: {"artist", "artist_en", "artist_kr", "title", ...}
    names = [n for n in dict.fromkeys([cand.get("artist_kr"), cand.get("artist_en"), cand.get("artist")]) if n]
    title = cand["title"]
    types = "(album OR ep)" if (want_kr and 한국EP허용) else "album"
    def _ok(g):
        y = _year(g.get("first-release-date"))
        return y and YEAR_FROM <= y <= YEAR_TO
    def _pack(g):
        return {"mbid": g["id"], "title": g["title"], "artist": _credit_names(g)[0] if _credit_names(g) else cand["artist"],
                "year": _year(g.get("first-release-date")), "mb유형": g.get("primary-type")}
    def _순위(g):
        # 이름이 꼭 같은 것, 매체가 적은 연도에 가까운 것을 앞에
        y = _year(g.get("first-release-date")) or 0
        거리 = abs(y - cand["year"]) if cand.get("year") and y else 99
        return (0 if 같은이름(g.get("title"), title) else 1, 거리, -g.get("score", 0))
    # 1) 아티스트 + 앨범 이름
    for name in names:
        q = f'artist:"{_lucene(name)}" AND releasegroup:"{_lucene(title)}" AND primarytype:{types}'
        try:
            rgs = mb_get("release-group", query=q, limit=8).get("release-groups", [])
        except Exception:
            continue
        hits = [g for g in rgs if g.get("score", 0) >= 80 and _ok(g)]
        if hits:
            return _pack(sorted(hits, key=_순위)[0])
    # 2) 앨범 이름만으로 찾고 아티스트를 맞춰 본다
    try:
        rgs = mb_get("release-group", query=f'releasegroup:"{_lucene(title)}" AND primarytype:{types}', limit=10).get("release-groups", [])
    except Exception:
        rgs = []
    hits = [g for g in rgs if _ok(g) and 같은이름(g.get("title"), title, 느슨=True)
            and any(같은이름(n, c, 느슨=True) for n in names for c in _credit_names(g))]
    if hits:
        return _pack(sorted(hits, key=_순위)[0])
    return None

def album_detail(album):
    # 발매일·참여자·레이블·수록곡만 가져온다. 점수는 안 본다
    d = mb_get(f"release-group/{album['mbid']}", inc="releases+artist-credits")
    rels = d.get("releases", [])
    first = sorted(rels, key=lambda r: r.get("date") or "9999")[0] if rels else None
    out = dict(album)
    종류 = list(d.get("secondary-types") or [])
    설명 = (d.get("disambiguation") or "").strip()
    판종 = []
    if d.get("primary-type") == "EP": 판종.append("EP·미니앨범")
    if "Live" in 종류: 판종.append("실황")
    if "Compilation" in 종류: 판종.append("모음")
    if "Remix" in 종류: 판종.append("리믹스")
    if "Demo" in 종류: 판종.append("데모")
    if "Soundtrack" in 종류: 판종.append("사운드트랙")
    low = (설명 + " " + d.get("title", "")).lower()
    if any(w in low for w in ["deluxe", "expanded", "remaster", "anniversary", "edition", "reissue"]):
        판종.append("확장판·재발매")
    out["판종"] = " / ".join(판종) if 판종 else "정규 앨범"
    out["판설명"] = 설명
    out["발매일"] = d.get("first-release-date") or (first or {}).get("date")
    out["레이블"] = []
    out["참여자"] = [c.get("name") for c in (d.get("artist-credit") or []) if c.get("name")]
    if first:
        try:
            r = mb_get(f"release/{first['id']}", inc="labels+recordings")
            out["레이블"] = [(li.get("label") or {}).get("name") for li in r.get("label-info", []) if li.get("label")]
            out["수록곡"] = [t["title"] for m in r.get("media", []) for t in m.get("tracks", [])][:30]
        except Exception:
            out["수록곡"] = []
    else:
        out["수록곡"] = []
    return out

def 매체정보로_앨범(cand):
    # MusicBrainz에 없을 때. 매체가 준 것만 적고 모르는 것은 모른다고 둔다
    t = cand["title"]
    low = t.lower()
    판종 = []
    if re.search(r"\b(ost|o\.s\.t)\b|사운드트랙", low): 판종.append("사운드트랙")
    if re.search(r"\blive\b|라이브|콘서트|실황", low): 판종.append("실황")
    if re.search(r"\bbest\b|베스트|모음|컴필|compilation|anthology", low): 판종.append("모음")
    if re.search(r"remix|리믹스", low): 판종.append("리믹스")
    if re.search(r"\bep\b|미니", low): 판종.append("EP·미니앨범")
    return {"mbid": None, "title": t, "artist": cand["artist"], "year": cand.get("year"),
            "판종": " / ".join(판종) if 판종 else "정규 앨범 (판종은 매체 표기로만 봤다)",
            "판설명": "", "발매일": str(cand["year"]) if cand.get("year") else None,
            "레이블": [], "참여자": [cand["artist"]], "수록곡": []}

print("MusicBrainz 준비 끝")

# ## 8. 위키백과 — 유명도 · 맥락 · 평가 절
#
# 유명도: 해외는 앨범 문서, 한국은 아티스트 문서가 있으면 통과한다. 한국 앨범은 문서가 있는 것이 드물다.
# 맥락: 앨범 문서 → 없으면 아티스트 문서. 평가 절은 재료로 따로 넣는다. 막힌 매체의 평이 거기 인용돼 있다.

평가절제목 = ["평가", "반응", "비평", "평론", "Reception", "Critical reception", "Critical response", "Reviews"]
음악낱말 = re.compile(r"(가수|밴드|그룹|음악|래퍼|뮤지션|작곡|보컬|듀오|앨범|음반|프로듀서|아이돌|singer|band|group|musician|rapper|album|duo|producer|songwriter|\bdj\b|idol)", re.I)

def _wiki_search(q, lang, n=5):
    try:
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                         params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n},
                         headers=HEADERS, timeout=15).json()
        out = []
        for h in r.get("query", {}).get("search", []):
            out.append((h["title"], re.sub(r"<.*?>", "", htmlmod.unescape(h.get("snippet") or ""))))
        return out
    except Exception:
        return []

_위키앨범캐시 = {}

def 위키_앨범문서(album):
    key = (album.get("mbid"), _norm(album.get("title")), _norm(album.get("artist")))
    if key in _위키앨범캐시:
        return _위키앨범캐시[key]
    year = album.get("year")
    후보, 아티스트문서 = [], None
    for lang in (["ko", "en"] if album["country"] == "KR" else ["en", "ko"]):
        for name in 이름들(album)[:2]:
            for title in 제목들(album)[:2]:
                for t, snip in _wiki_search(f"{name} {title}", lang, 5):
                    head, 괄호 = _문서제목(t), _괄호(t)
                    if re.search(r"(노래|싱글|song|single)", 괄호, re.I):
                        continue                       # 곡 문서다. 앨범 문서가 아니다
                    # 앨범 문서: 제목이 앨범 이름 그대로거나 "아티스트 앨범이름" 꼴
                    if 같은이름(head, title) or 같은이름(head, f"{name} {title}") or 같은이름(head, f"{title} {name}"):
                        연도맞음 = bool(year) and str(year) in (t + " " + snip)
                        후보.append((0 if 연도맞음 else 1, len(후보), {"lang": lang, "제목": t}))
                    # 아티스트 문서 안에 이 앨범이 적혀 있는 경우. 맥락으로는 쓰되 앨범 문서로는 안 친다
                    elif _norm(title) and _norm(title) in _norm(snip) and re.search(r"(음반|앨범|album|EP)", snip, re.I) and 같은이름(head, name) and not 아티스트문서:
                        아티스트문서 = {"lang": lang, "제목": t, "아티스트문서": True}
        if 후보:
            break
    out = None
    if 후보:
        후보.sort(key=lambda x: (x[0], x[1]))
        out = 후보[0][2]                                   # 연도가 적힌 문서를 먼저, 같으면 먼저 나온 것
    elif 아티스트문서:
        out = 아티스트문서
    _위키앨범캐시[key] = out
    return out

def 위키_아티스트문서(album):
    for lang in (["ko", "en"] if album["country"] == "KR" else ["en", "ko"]):
        for name in 이름들(album)[:3]:
            for t, snip in _wiki_search(name, lang, 5):
                head = _문서제목(t)
                # 제목이 이름 그대로고 안내문에 음악 낱말이 있어야 한다. "서울"로 "서울특별시"가 잡히지 않게
                if 같은이름(head, name) and 음악낱말.search(t + " " + snip):
                    return {"lang": lang, "제목": t}
                # "전기뱀장어"처럼 동물 문서가 먼저 나오고 안내문에 "록밴드 전기뱀장어 (밴드)"라고 적힌 경우
                if 같은이름(head, name) and re.search(r"(밴드|가수|그룹|음악|band|singer|group)", snip):
                    return {"lang": lang, "제목": t}
    return None

def 알만한가(album):
    if 위키_앨범문서(album):
        return True, "앨범 문서"
    기준 = 유명도기준["KR" if album["country"] == "KR" else "기타"]
    if 기준 == "아티스트" and 위키_아티스트문서(album):
        return True, "아티스트 문서"
    return False, None

def _wiki_extract(lang, title, n=6000):
    try:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        p = requests.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                      "titles": title, "format": "json"}, headers=HEADERS, timeout=15).json()
        page = next(iter(p["query"]["pages"].values()))
        text = (page.get("extract") or "").strip()
        if len(text) > 300:
            return {"제목": title, "링크": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}", "본문": text[:n]}
    except Exception:
        pass
    return None

def wiki_context(album):
    # 앨범 문서가 있으면 그것, 없으면 아티스트 문서
    doc = 위키_앨범문서(album)
    if doc and not doc.get("아티스트문서"):
        w = _wiki_extract(doc["lang"], doc["제목"])
        if w:
            return w
    doc = 위키_아티스트문서(album)
    if doc:
        w = _wiki_extract(doc["lang"], doc["제목"])
        if w:
            w["제목"] += " (아티스트 문서)"
            return w
    return None

def wiki_reception(album):
    # 앨범 문서의 평가 절. 막힌 매체의 평이 여기 인용돼 있다
    doc = 위키_앨범문서(album)
    if not doc or doc.get("아티스트문서"):
        return None
    lang, title = doc["lang"], doc["제목"]
    try:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        secs = requests.get(api, params={"action": "parse", "page": title, "prop": "sections", "format": "json"},
                            headers=HEADERS, timeout=15).json()
        idx = None
        for sec in secs.get("parse", {}).get("sections", []):
            if any(k.lower() in sec["line"].lower() for k in 평가절제목):
                idx = sec["index"]; break
        if idx is None:
            return None
        html = requests.get(api, params={"action": "parse", "page": title, "section": idx, "prop": "text", "format": "json"},
                            headers=HEADERS, timeout=15).json()["parse"]["text"]["*"]
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["table", "sup", "style"]):
            t.decompose()
        text = soup.get_text("\n", strip=True)
        if len(text) > 200:
            return {"url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}#평가",
                    "막힘": False, "본문": text[:8000], "제목": f"{title} — 평가 절 (위키백과)", "매체": "위키백과"}
    except Exception:
        pass
    return None

print("위키백과 준비 끝")

# ## 9. 평론 링크 모으기 — 검색 엔진
#
# 앨범 이름으로 검색한다. 매체를 지정하지 않는다.
# 검색 결과에서 차단 도메인만 빼고, 나머지는 다 후보로 삼는다.
# 검색은 DuckDuckGo로 넣었다. 다른 걸 쓰려면 `검색()` 함수 한 개만 갈면 된다.
# 본문을 실제로 읽을 때는 여전히 robots.txt를 먼저 본다.

def fetch(url, timeout=20):
    if not robots_ok(url):
        return None
    _site_wait(url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None

from ddgs import DDGS

def 검색(q, n=20):
    try:
        with DDGS() as d:
            return [r["href"] for r in d.text(q, max_results=n) if r.get("href")]
    except Exception as e:
        if "No results" not in str(e):
            print("   검색 실패:", e)
        return []

def 검색_링크(album):
    말 = []
    for name in 이름들(album)[:2]:
        for title in 제목들(album)[:1]:
            q = f"{name} {title}"
            if album["country"] == "KR":
                말 += [f"{q} 앨범 리뷰", f"{q} 평론", f"{q} album review"]
            else:
                말 += [f"{q} album review", f"{q} 앨범 리뷰"]
    out = []
    for x in dict.fromkeys(말):
        out += 검색(x, n=15)
    return out

def collect_links(album):
    links = 검색_링크(album)
    seen, out = set(), []
    for u in links:
        u = u.split("#")[0]
        if u in seen or 차단됐나(u) or media_of(u) and media_of(u).get("api"):   # API 매체는 API로 이미 봤다
            continue
        seen.add(u)
        out.append(u)
    return out[: PAGE_LIMIT * 후보링크배수]

print("링크 모으기 준비 끝")

# ## 10. 본문 읽기
#
# 먼저 링크의 사이트마다 robots.txt를 한꺼번에 받아서 막힌 링크를 지운다.
# 남은 링크는 열 개씩 동시에 연다. 같은 사이트끼리만 1초씩 띄운다. 다른 사이트는 동시에 열어도 규칙에 안 걸린다.

def fetch_nowait(url, timeout=15):
    # robots는 이미 본 뒤라고 치고 연다
    _site_wait(url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

def robots_batch(links, workers=10):
    # 사이트마다 robots.txt를 한 번씩, 동시에 받는다
    sites = list(dict.fromkeys(f"{urllib.parse.urlparse(u).scheme}://{urllib.parse.urlparse(u).netloc}" for u in links))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda b: robots_ok(b + "/"), sites))
    allowed = [u for u in links if robots_ok(u)]
    blocked = [u for u in links if u not in allowed]
    return allowed, blocked

def _parse_page(url, html):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")
    body = soup.select_one("article") or soup.select_one("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    return {"url": url, "막힘": False, "본문": text[:12000], "제목": title, "매체": 매체이름(url)}

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

print("본문 읽기 준비 끝")

# ## 10-1. 막힌 매체의 RSS
#
# HTML은 막았어도 RSS는 열어 둔 곳이 있다. RSS는 프로그램이 읽으라고 만든 것이다.
# robots.txt가 RSS 주소까지 막았으면 그것도 안 읽는다.
# 앨범 이름이 제목이나 본문에 있는 글만 재료로 넣는다.

import feedparser

RSS후보경로 = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/index.xml", "/?feed=rss2", "/atom.xml", "/rss/all.xml"]
_rss캐시 = {}

def rss_find(domain):
    if domain in _rss캐시:
        return _rss캐시[domain]
    found = None
    for base in (f"https://www.{domain}", f"https://{domain}"):
        for p in RSS후보경로:
            u = base + p
            if not robots_ok(u):
                continue
            try:
                r = requests.get(u, headers=HEADERS, timeout=10)
                if r.status_code == 200 and ("<rss" in r.text[:2000] or "<feed" in r.text[:2000]):
                    found = u; break
            except Exception:
                continue
        if found:
            break
    _rss캐시[domain] = found
    return found

def rss_pages(album):
    out = []
    keys = [_norm(x) for x in 제목들(album) + 이름들(album) if _norm(x)]
    for m in 막힌매체:
        if (m["lang"] == "ko") != (album["country"] == "KR"):
            continue
        u = rss_find(m["domain"])
        if not u:
            continue
        try:
            feed = feedparser.parse(u)
        except Exception:
            continue
        for e in feed.entries[:60]:
            body = ""
            if e.get("content"):
                body = " ".join(c.get("value", "") for c in e["content"])
            body = body or e.get("summary", "") or ""
            text = BeautifulSoup(body, "lxml").get_text(" ", strip=True)
            low = _norm(e.get("title", "") + " " + text)
            if any(k in low for k in keys) and len(text) > 200:
                out.append({"url": e.get("link", u), "막힘": False, "본문": text[:12000],
                            "제목": f"{e.get('title','')} ({m['name']} RSS)", "매체": m["name"]})
    return out

def rss_점검():
    for m in 막힌매체:
        print(f"  {m['name']:<14} RSS {rss_find(m['domain']) or 'X'}")

print("RSS 준비 끝")

# ## 11. 코드로 미리 거르기
#
# 모델을 부르기 전에 확실히 아닌 것만 뺀다. 글자 수로 좋고 나쁨을 가리지는 않는다.

def pre_filter(pages, album):
    keys = [_norm(x) for x in 제목들(album) + 이름들(album) if len(_norm(x)) >= 2]
    out, seen = [], set()
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        low = _norm(p["본문"] + " " + p["제목"])
        if len(p["본문"]) < 400:          # 본문을 못 읽어 온 것이다
            continue
        if not any(k in low for k in keys):
            continue
        out.append(p)
    return out

print("미리 거르기 준비 끝")

# ## 12. 재료 판정
#
# 페이지마다 모델을 한 번 부른다. 여섯 값을 낸다. 쓸지 버릴지는 코드가 정한다.
# 원문은 재료에 같이 붙여 둔다. 편집국장이 대조할 때 쓴다. 저장할 때는 뺀다.

from typing import Literal, List, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

class 재료판정(BaseModel):
    다루는_정도: Literal["앨범 평", "아티스트 이야기", "스치듯 언급", "관계없음"]
    평가있음: bool
    홍보문: bool
    관점: str = Field(description="글쓴이가 이 앨범에서 무엇을 근거로 무엇을 봤는지 한 줄. 좋다 나쁘다만 적지 않는다")
    근거: List[Literal["곡", "가사", "사운드", "아티스트 이력", "장르 맥락"]]
    인용: str = Field(description="원문에서 그대로 옮긴 한 문장. 원문 언어 그대로. 없으면 빈 문자열")

재료판정관 = Agent(
    MODEL,
    output_type=재료판정,
    system_prompt="""너는 음악 글 한 편을 읽고 여섯 가지를 판정한다. 쓸지 버릴지는 네가 정하지 않는다. 값만 낸다.

[다루는_정도]
- "앨범 평": 이 앨범을 놓고 쓴 글이다. 앨범 리뷰, 앨범을 다시 꺼내 본 글.
- "아티스트 이야기": 아티스트 전체를 다루면서 이 앨범을 한 대목 이상 말한다.
- "스치듯 언급": 이름만 나온다.
- "관계없음": 이 앨범 이야기가 아니다. 이름이 같은 다른 앨범이나 곡이면 관계없음이다.
여러 앨범을 한 글에서 다루는 모음글(월간 결산, 주간 리뷰, 1st Listen)이면 이 앨범을 다룬 대목만 놓고 판정한다.
그 대목이 서너 문장 이상이고 글쓴이 판단이 있으면 "앨범 평"이다. 한두 문장이면 "스치듯 언급"이다.

[평가있음] 글쓴이가 좋다 · 나쁘다 · 아쉽다 · 낫다 같은 판단을 하나라도 적었으면 참이다.

[홍보문] 발매일 · 참여자 · 수록곡 · 활동 계획만 나열하고 글쓴이 판단이 없으면 참이다.
보도자료를 옮긴 기사, 음원 사이트 소개문이 그렇다. 별점이나 점수가 붙어 있어도 판단 문장이 없으면 홍보문이다.

[관점] 글쓴이가 이 앨범에서 무엇을 근거로 무엇을 봤는지 한 줄로 적는다.
"좋다", "완성도가 높다"처럼 판단만 적지 않는다. 무엇을 보고 그렇게 봤는지까지 적는다.
예: "앞 앨범의 밴드 연주를 걷어내고 신시사이저로 채웠는데, 그래서 목소리가 더 또렷하게 들린다고 봤다"
예: "가사가 자기 이야기에서 남 이야기로 옮겨 갔고, 그 때문에 앞 앨범보다 힘이 빠졌다고 봤다"

[근거] 관점이 기대는 것. 곡 / 가사 / 사운드 / 아티스트 이력 / 장르 맥락 중에서 고른다. 여럿 골라도 된다.

[인용] 관점을 가장 잘 보여주는 원문 문장 하나. 원문 언어 그대로, 한 글자도 고치지 않는다. 문장 하나만 옮긴다.
마땅한 문장이 없으면 빈 문자열로 둔다.

주의
- 페이지에 점수 · 필자 이름 · 수록곡 목록 · 다른 글 제목이 같이 붙어 있어도 본문만 본다.
- 아티스트 이름은 한글 표기와 영문 표기가 같이 쓰인다. 표기가 달라도 같은 사람이다.
- 글이 아티스트를 오래 다루다 이 앨범 이야기로 넘어가면 "아티스트 이야기"다. 앨범 대목이 글의 중심이면 "앨범 평"이다.""",
)

async def _judge_many(pages, album):
    # 페이지 여러 장을 한 루프 안에서 동시에 보낸다. 스레드를 쓰면 모델 클라이언트가 루프에 묶여서 안 된다
    sem = asyncio.Semaphore(동시판정)
    async def one(p):
        ask = (f"앨범: {표기(album)} - {album['title']} ({album.get('year')})\n"
               f"아티스트의 다른 표기: {', '.join(이름들(album))}\n"
               f"페이지 제목: {p['제목']}\n주소: {p['url']}\n\n본문:\n{p['본문'][:8000]}")
        async with sem:
            try:
                r = await 재료판정관.run(ask, usage_limits=UsageLimits(request_limit=2))
            except Exception as e:
                print("   판정 실패:", e)
                return None
        return {"매체": p.get("매체") or 매체이름(p["url"]), "도메인": _host(p["url"]),
                "url": p["url"], "판정": r.output, "원문": p["본문"]}
    return await asyncio.gather(*(one(p) for p in pages))

def judge_pages(pages, album, budget):
    pages = pages[: max(0, budget["남은콜"])]
    if not pages:
        return []
    got = _run_async(_judge_many(pages, album))
    budget["남은콜"] -= len(pages)
    return [g for g in got if g]

def keep_rules(재료):
    쓸것 = [r for r in 재료
            if r["판정"].다루는_정도 in ("앨범 평", "아티스트 이야기")
            and r["판정"].평가있음 and not r["판정"].홍보문
            and len(r["판정"].관점.strip()) >= 10]
    per, out = {}, []
    for r in 쓸것:
        c = per.get(r["도메인"], 0)
        if c >= PER_MEDIA:          # 같은 매체는 두 개까지
            continue
        per[r["도메인"]] = c + 1
        out.append({"매체": r["매체"], "관점": r["판정"].관점.strip(),
                    "인용": r["판정"].인용.strip(), "근거": r["판정"].근거,
                    "링크": r["url"], "원문": r["원문"]})
    return out

print("재료 판정 준비 끝")

# ## 13. 관점 묶기
#
# 모델을 안 부르고 임베딩만 쓴다. 임베딩 모델은 처음 쓸 때 한 번 내려받는다.

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
    import numpy as np
    v = embedder().encode([r["관점"] for r in 재료], normalize_embeddings=True)
    sim = np.asarray(v) @ np.asarray(v).T
    남음 = list(range(len(재료)))
    묶음 = []
    while 남음:
        i = 남음.pop(0)
        덩어리 = [i]
        for j in list(남음):
            if sim[i][j] >= 문턱:
                덩어리.append(j)
                남음.remove(j)
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
    return "\n".join(줄)

def album_text(a):
    return (f"아티스트: {표기(a)}  (다른 표기: {', '.join(이름들(a))})\n"
            f"앨범: {a['title']} ({a.get('year')})\n"
            f"판종: {a.get('판종', '정규 앨범')}{(' — ' + a['판설명']) if a.get('판설명') else ''}\n"
            f"발매일: {a.get('발매일') or '모름'}\n"
            f"레이블: {', '.join(a.get('레이블') or []) or '모름'}\n"
            f"수록곡: {', '.join((a.get('수록곡') or [])[:20]) or '모름 (MusicBrainz에 없다. 곡 이름은 관점표 · 맥락에 나온 것만 쓴다)'}")

print("관점 묶기 준비 끝")

# ## 14. 기획자
#
# 관점표 · 맥락 · 앨범 정보 · 판종을 받아 뼈대를 짠다. 문체는 모른다.
# 접근 · 온도 · 시작점 · 배치는 여기서 받는다.
# 두 번째부터는 지난 번 편집국장 문제 목록을 받는다. 지난 개요와 지난 글은 안 받는다.

접근들 = ["분석", "해석", "평가"]
온도들 = ["건조", "따뜻함", "짓궂음"]
시작점들 = [                       # 일곱. PM이 고친다
    "곡 하나의 특정 대목", "앨범이 나온 때의 자리", "아티스트의 앞 앨범",
    "평이 갈린 지점", "소리의 한 요소", "앨범 제목이나 표지", "듣는 사람이 서는 자리",
]
배치들 = [                         # 다섯. PM이 고친다
    "시간 순으로 따라간다", "곡 하나를 파고든 다음 앨범 전체로 넓힌다",
    "평이 갈린 지점에서 시작해 양쪽을 본다", "소리 요소별로 나눠 본다",
    "앨범 전체 인상을 먼저 말하고 근거를 댄다",
]

class 기획주문(BaseModel):
    앨범정보: str
    관점표: str
    맥락: str
    접근: str
    온도: str
    시작점: str
    배치: str
    평개수: int = 0
    지난문제: str = ""

class 인용계획(BaseModel):
    매체: str
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 영어를 그대로 두지 않는다. 원문이 한국어면 그대로")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것 한 줄")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. 앨범 정보·맥락·관점표에 있는 것만")
    쓸관점: List[str] = Field(description="이 문단에서 기대는 평론 관점. 관점표에서 그대로")

class 개요(BaseModel):
    주제: str = Field(description="이 글이 하려는 말 한 줄")
    판종처리: str = Field(description="판종이 정규 앨범이 아니면 어떻게 다룰지 한 줄. 정규면 빈 문자열")
    곡셋: List[str] = Field(max_length=3, description="다룰 곡. 세 개까지. 수록곡에서. 수록곡을 모르면 관점표·맥락에 이름이 나온 곡에서만")
    인용둘: List[인용계획] = Field(max_length=2)
    문단들: List[문단계획] = Field(min_length=4, max_length=6)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. '사실 ← 출처' 꼴. 출처는 앨범 정보 / 맥락 / 관점표 중 하나. 여기 없는 사실은 작가가 못 쓴다")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
    평하나 = """
- 관점표에 매체가 하나뿐이다. 그 매체의 판단을 이 글의 결론으로 삼지 않는다. 그 관점은 한 문단에서 한 번만 기댄다.
  나머지 문단은 앨범 정보 · 맥락에 있는 사실과 글쓴이 자신의 판단으로 채운다.
  "평론가들은", "평단은"처럼 여럿의 평인 것처럼 적지 않는다. 매체 이름을 밝히고 그 매체가 봤다고 적는다.""" if d.평개수 <= 1 else ""
    return f"""너는 앨범 리뷰의 뼈대를 짠다. 문장은 안 쓴다. 무엇을 어떤 순서로 말할지만 정한다.

[앨범]
{d.앨범정보}

[관점표 — 남의 평은 여기 있는 것만]
{d.관점표 or "(없음)"}

[맥락 — 사실은 여기와 앨범 정보에 있는 것만]
{d.맥락 or "(없음)"}

[이번 글]
접근: {d.접근} / 온도: {d.온도}
시작점: {d.시작점}에서 연다
배치: {d.배치}

[규칙]
- 사실목록에는 앨범 정보 · 맥락 · 관점표에 있는 것만 넣는다. 기억으로 아는 것을 넣지 않는다.
  사실마다 어디서 왔는지 "사실 ← 앨범 정보" / "사실 ← 맥락" / "사실 ← 관점표" 꼴로 적는다.
- 아티스트 · 앨범 · 곡 이름은 앨범 정보에 적힌 표기를 쓴다. 곡 이름은 관점표나 맥락에 적힌 표기 그대로다.
- 곡은 세 개까지다. 수록곡에서 고른다. 수록곡을 모르면 관점표 · 맥락에 이름이 나온 곡에서만 고른다. 거기에도 없으면 곡셋은 비운다.
- 인용은 두 개까지다. 관점표의 인용 중에서 고른다. 영어면 한국어로 옮기고, 한국어면 그대로 둔다. 한 문장이다. 두 문장 넘는 것은 고르지 않는다.
- 인용의 매체는 관점표에 적힌 이름 그대로 쓴다. "한 매체"라고 돼 있으면 그대로 "한 매체"다. 도메인을 적지 않는다.
- 판종이 정규 앨범이 아니면 그 판을 다루는 글이다. 관점표의 평이 원판 이야기면 그렇다고 적는다.
- 문단은 넷에서 여섯이다. 문단마다 할 말 하나다.
- 결성 연도 · 발매일 · 레이블 같은 사실은 한 문단에 몰지 않는다. 필요한 문단에 하나씩 나눈다. 백과사전 문장이 되지 않게 한다.
- 마지막 문단은 글쓴이 자신의 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 앨범 중 하나다"처럼 정리하는 결론은 쓰지 않는다.
- 첫 문단과 마지막 문단이 같은 말을 되풀이하지 않게 한다. 점수 · 별점 · 차트 순위를 옮겨 적는 문단을 두지 않는다.{평하나}{지난}"""

print("기획자 준비 끝")

# ## 15. 작가
#
# 개요만 받아 문장으로 쓴다. 재료 원문은 안 본다. 개요에 없는 사실은 못 쓴다.
# 문체 규칙은 둘만 준다. 존댓말 금지, 판단은 "~다"로 끝낸다. 표기 규칙은 문체가 아니라 사실이라 같이 준다.

class 집필주문(BaseModel):
    앨범한줄: str
    아티스트표기: str
    개요: 개요
    온도: str

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
    문단 = "\n".join(f"{i+1}. {p.할말}\n   사실: {'; '.join(_사실만(x) for x in p.쓸사실) or '없음'}\n   관점: {'; '.join(p.쓸관점) or '없음'}"
                    for i, p in enumerate(o.문단들))
    인용 = "\n".join(f"- ({q.매체}) {q.옮긴문장}" for q in o.인용둘) or "없음"
    return f"""너는 앨범 리뷰를 쓴다. 뼈대는 이미 짜여 있다. 그것을 문장으로 옮긴다.

[앨범] {d.앨범한줄}
[아티스트 표기] {d.아티스트표기}
[주제] {o.주제}
[판종] {o.판종처리 or "정규 앨범이다"}
[온도] {d.온도}
[다룰 곡] {", ".join(o.곡셋) or "정하지 않았다. 곡 이름을 새로 꺼내지 않는다"}

[문단 계획]
{문단}

[쓸 수 있는 인용 — 이 문장 그대로, 매체 이름과 함께]
{인용}

[쓸 수 있는 사실 — 이 밖의 사실은 쓰지 않는다]
{chr(10).join("- " + _사실만(f) for f in o.사실목록)}

[규칙]
- 존댓말을 쓰지 않는다.
- 판단은 "~다"로 끝낸다.
- 위 사실 목록과 인용 밖의 사실이나 남의 평을 넣지 않는다. 네 판단은 넣어도 된다.
- 문단 계획 순서를 지킨다. 문단마다 할 말 하나다.
- 표기: 아티스트는 [아티스트 표기]대로 첫 등장에 한 번 적고 그 뒤로는 앞 이름만 쓴다. 앨범 이름은 《 》, 곡 이름은 ' '로 감싼다. 이름 철자는 개요에 적힌 그대로다.
- 분량은 1200자에서 2000자 사이다."""

print("작가 준비 끝")

# ## 16. 교정자
#
# 작가 글을 받아 문장 짜임만 고친다. 뜻과 사실은 못 바꾼다.
# 번역투가 무엇인지는 이 에이전트만 안다.

class 교정본(BaseModel):
    본문: str
    고친곳: List[str] = Field(description="무엇을 어떻게 고쳤는지 한 줄씩")

교정자 = Agent(
    MODEL,
    output_type=교정본,
    system_prompt="""너는 한국어 문장을 다듬는다. 뜻과 사실과 판단은 하나도 안 바꾼다. 문장 짜임만 고친다.

번역투는 영어 문장을 그대로 옮긴 것처럼 읽히는 문장이다. 아래가 그것이다. 보이면 고친다.
1. "~는 것이 아니라 ~다" 틀. 영어 not A but B다. 앞을 빼고 뒤만 말하거나, 두 문장으로 나눈다.
2. 한 문장에 절이 셋 넘게 겹쳐서 끝까지 가야 뜻이 잡히는 것. 뜻 단위로 끊는다.
3. 물건이 주어로 서서 스스로 무언가를 하는 것. "편곡이 놓인다", "에너지가 곡을 밀어간다". 사람이나 소리가 실제로 하는 일로 바꾼다. "편곡을 얹었다", "연주가 커지면서 곡이 빨라진다".
4. 결론을 명사로 닫는 것. "~하는 이유다", "~의 결과에 가깝다", "~하는 방식이다". 동사로 끝낸다.
5. "~에 그치지 않는다", "~에 머물지 않는다", "단순히 ~가 아니다". 영어 not only다. 뒤에 오는 말만 남긴다.
6. "가장 먼저 ~하는 것은 ~다", "~을 만드는 것은 ~다" 강조 틀. 주어를 앞으로 빼서 보통 문장으로 만든다. "기타 리프가 먼저 나선다".
7. 물건이 주어가 되어 비유를 하는 것. "후크가 어둠의 표면에 균열을 낸다". 실제로 들리는 것으로 바꾼다. "후크가 나오면 분위기가 가벼워진다".

같이 본다.
- 한자어 동사(작동한다, 기능한다, 구축한다, 형성한다)는 일상어로. 한다, 된다, 만든다, 낸다.
- "~적", "~에 있어", "~에 대한", "~을 통해", "~에 의해"는 뺀다.
- 손에 안 잡히는 말(밀도, 결, 층위, 텍스처, 긴장, 세계, 지점, 서사, 정체성, 육체성, 즉각성, 응집력, 접근성, 타격감, 미학)은 그 문장이 실제로 가리키는 것으로 바꾼다. 가리키는 것이 문장에 없으면 그 말만 뺀다.
- 아티스트 · 앨범 · 곡 이름과 장르 이름은 손대지 않는다. 다만 잘못 적힌 것(스톤더 → 스토너, 프로토고스 → 프로토 고스)은 바로잡고 고친 곳에 적는다.
- 영어 문장이 그대로 있으면 한국어로 옮긴다. 영어로 된 이름은 옮기지 않는다.

문장 길이는 내용이 정한다. 짧은 것을 억지로 늘리거나 긴 것을 억지로 자르지 않는다.
고친 곳마다 한 줄로 적는다.""",
)

print("교정자 준비 끝")

# ## 17. 형태 검사 (코드)
#
# 교정자가 놓친 것을 잡는다. 모델을 안 부른다.

존댓말 = re.compile(r"(습니다|합니다|입니다|세요|네요|해요|이에요|예요|드립니다)")
흐림 = re.compile(r"(인 듯하다|인 듯싶다|일 수도 있다|라고 할 수 있다|인 것 같다|일지도 모른다|라고 볼 수 있다|아닐까)")
부추김 = re.compile(r"(꼭 들어|들어봐야|필청|강력히|추천한다|놓치지 마)")
번역틀 = re.compile(r"(것이 아니라|라기보다|에 가깝다|하는 방식이다|하는 이유다|하는 지점|에 있어|을 통해|를 통해|에 의해"
                    r"|그치지 않|머물지 않|단순히 .{1,12}가 아니|는 것은 .{1,20}다\.)")
추상어 = re.compile(r"(서사|정체성|육체성|즉각성|응집력|접근성|타격감|미학|밀도|층위|텍스처)")
다수평 = re.compile(r"(평론가들은|평단은|비평가들은|많은 이들이|대체로 .{0,6}평가|호평이 이어|평이 갈렸)")
따옴표안 = re.compile(r"[“\"]([^”\"]{10,})[”\"]")
영문덩어리 = re.compile(r"[A-Za-z][A-Za-z ,.'’\-]{25,}")

def _겹침(a, b, n=12):
    A = {a[i:i+n] for i in range(max(0, len(a)-n+1))}
    B = {b[i:i+n] for i in range(max(0, len(b)-n+1))}
    return len(A & B) / len(A) if A and B else 0.0

def 형태검사(본문, 재료, 맥락, album):
    걸림 = []
    t = 본문
    if 존댓말.search(t):
        걸림.append("존댓말")
    if not re.search(r"[.!?…\"']\s*$", t.strip()):
        걸림.append("문장 끊김")
    문단 = [p.strip() for p in t.split("\n") if p.strip()]
    머리 = [p.split()[0] for p in 문단 if p.split()]
    if 머리 and len(set(머리)) < len(머리) * 0.7:
        걸림.append("문단 시작 반복")
    재료글 = "\n".join(r["관점"] + " " + r["인용"] for r in 재료) + "\n" + (맥락 or "")
    if 재료글.strip() and _겹침(t, 재료글) > 0.08:
        걸림.append("재료 옮겨 적기")
    if len(흐림.findall(t)) >= 2:
        걸림.append("판단 흐리는 끝맺음")
    틀 = 번역틀.findall(t)
    if len(틀) >= 3:
        걸림.append("번역투 틀: " + ", ".join(dict.fromkeys(틀)))
    # 이름은 영어여도 된다. 이름을 지운 뒤에 영어 덩어리를 본다
    t_이름뺌 = t
    for x in 이름들(album) + 제목들(album) + list(album.get("수록곡") or []):
        if x and len(x) >= 3:
            t_이름뺌 = t_이름뺌.replace(x, "")
    if 영문덩어리.search(t_이름뺌):
        걸림.append("영어 원문")
    추 = 추상어.findall(t)
    if len(추) >= 4:
        걸림.append("추상어 " + str(len(추)) + "개: " + ", ".join(dict.fromkeys(추)))
    if len(set(r["매체"] for r in 재료)) <= 1 and 다수평.search(t):
        걸림.append("없는 다수 평: " + 다수평.search(t).group(0))
    for q in 따옴표안.findall(t):
        if len(re.findall(r"[.!?]", q)) >= 2 or len(q) > 120:
            걸림.append("인용이 길다")
            break
    if re.search(r"\(\s*[a-z0-9.-]+\.[a-z]{2,}\s*\)", t):
        걸림.append("도메인이 글에 나온다")
    if "!" in t:
        걸림.append("느낌표")
    if 부추김.search(t):
        걸림.append("부추기는 말")
    곡들 = [c for c in (album.get("수록곡") or []) if len(c) >= 3]
    나온곡 = [c for c in 곡들 if c.lower() in t.lower()]
    if len(나온곡) > 3:
        걸림.append(f"곡 나열 {len(나온곡)}개")
    if not (1000 <= len(t) <= 2400):
        걸림.append("분량")
    return list(dict.fromkeys(걸림))

print("형태 검사 준비 끝")

# ## 18. 편집국장
#
# 마지막에 전체를 다시 읽는다. 평론 원문 · 개요 · 앨범 정보 · 관점표를 다 들고 대조한다.
# 사실 · 문장 · 글 전체 셋을 본다. 직접 고치지 않는다. 올릴지도 정하지 않는다.
# 문제가 있으면 목록을 낸다. 그러면 기획자부터 다시 쓴다.

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
    system_prompt="""너는 편집국장이다. 완성된 앨범 리뷰를 마지막으로 읽는다. 세 가지를 본다.

[사실] 글에 적힌 사실 하나하나를 앨범 정보 · 개요 · 관점표 · 평론 원문과 대조한다.
- 어디에도 없는 사실이면 종류 "사실"로 잡는다. 남의 평을 지어낸 것도 "사실"이다.
- 앨범 정보에 있는 연도 · 레이블 · 곡 이름은 재료 안이다.
- 글쓴이 자신의 판단과 관찰은 사실이 아니다. 잡지 않는다.
- 원문에 있는 평을 글이 다르게 옮겼으면 "사실"로 잡고 원문이 뭐라 했는지 적는다.
- 원문이 하나뿐인데 "평론가들은", "평단은"처럼 여럿의 평으로 적었으면 "사실"로 잡는다.
- 아티스트 · 앨범 이름의 한글 표기와 영문 표기, 대소문자, 띄어쓰기 차이는 사실 문제가 아니다.
- 원문이 여러 앨범을 다룬 모음글이면 이 앨범을 다룬 대목만 원문으로 본다.

[문장] 뜻이 안 잡히는 문장, 앞뒤가 안 맞는 문장, 고치다 망가진 문장, 영어를 그대로 옮긴 듯한 문장. 종류 "문장".

[구조] 판종에 맞게 썼는지, 시작과 끝이 맞물리는지, 곡 셋 안에서 갔는지, 문단마다 할 말이 하나인지. 종류 "구조".
- 마지막 문단이 글쓴이 판단이 아니라 여러 평을 합친 요약("가장 ~한 앨범 중 하나다")이면 "구조"로 잡는다.
- 결성 연도 · 발매일 · 레이블이 한 문장에 몰려 백과사전처럼 읽히면 "구조"로 잡는다.
- 첫 문단과 마지막 문단이 같은 말을 되풀이하면 "구조"로 잡는다.

[낱말] 오타, 없는 말, 장르 · 밴드 · 사람 이름을 틀리게 적은 것. "스톤더 둠", "프로토고스" 같은 것. 종류 "문장"으로 잡고 바른 말을 설명에 적는다.

직접 고치지 않는다. 올릴지 말지도 정하지 않는다. 점수와 문제 목록만 낸다.
문제마다 어느 문장인지 그대로 옮겨 적는다.""",
)

def 편집국장_읽기(글: Article, 개요_: 개요, 재료, 관점표, 맥락, album, budget):
    if budget["남은콜"] <= 0:
        return None
    원문들 = "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:4000]}" for r in 재료) or "(없음)"
    ask = (f"[앨범 정보]\n{album_text(album)}\n\n"
           f"[개요]\n주제: {개요_.주제}\n사실목록:\n" + "\n".join("- " + f for f in 개요_.사실목록) + "\n\n"
           f"[관점표]\n{관점표 or '(없음)'}\n\n"
           f"[맥락]\n{(맥락 or '(없음)')[:3000]}\n\n"
           f"[평론 원문 — {len(재료)}편]\n{원문들}\n\n"
           f"[글]\n{글.제목}\n\n{글.본문}")
    try:
        r = 편집국장.run_sync(ask, usage_limits=UsageLimits(request_limit=2))
    except Exception as e:
        print("   편집국장 실패:", e)
        return None
    budget["남은콜"] -= 1
    return r.output

def 올릴까(걸림, p):
    # 코드가 정한다
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

# ## 19. 앨범 후보 모으기
#
# 무작위 뽑기는 없다. 후보는 매체가 리뷰한 앨범에서만 나온다.
#
# - 한국: 이즘 국내 앨범 리뷰(무작위 페이지) + 이즘 국내 명반 + 아이돌로지 Review
# - 해외: 이즘 해외 명반 + 이즘 해외 앨범 리뷰(무작위 페이지) + 열린 해외 매체의 첫 화면 리뷰 링크
#
# 후보마다 그 리뷰 본문을 같이 들고 간다. 재료 첫 번째로 들어간다.
# 이즘 조회수가 높은 쪽을 먼저 본다. "알 만한 앨범"을 앞에 세우는 것이다. 그 다음 위키백과 문서로 한 번 더 거른다.

class 제목한줄(BaseModel):
    아티스트: str
    앨범: str
    앨범리뷰인가: bool

class 제목묶음(BaseModel):
    목록: List[제목한줄]

제목파서 = Agent(
    MODEL,
    output_type=제목묶음,
    system_prompt="""너는 음악 웹진의 글 제목 목록을 받는다. 제목마다 아티스트 이름과 앨범 이름을 뽑는다.

- 앨범 리뷰가 아닌 제목(인터뷰, 공연, 차트, 특집, 대담, 부고, 월간 · 주간 결산, 세대론)은 앨범리뷰인가를 거짓으로 둔다.
- 곡 하나만 다룬 싱글 리뷰도 거짓이다. 제목에 "싱글"이나 곡 하나만 적혀 있고 앨범 표시가 없으면 싱글로 본다.
- 이름을 지어내지 않는다. 제목에 없으면 빈 문자열로 둔다.
- 따옴표(" ", ‘ ’, 《 》, < >) 안이 앨범이나 곡 이름이다. 그 앞이 아티스트다.
- 입력 순서를 그대로 지킨다.

예
- NCT 위시 "Ode to Love" : 견고한 레거시에 내는 작은 균열 → 아티스트 "NCT 위시", 앨범 "Ode to Love", 참
- 방탄소년단 "ARIRANG" Track by Track 리뷰 → 아티스트 "방탄소년단", 앨범 "ARIRANG", 참
- 리뷰 : 캣츠아이 'Internet Girl', 도둑맞은 '플롭트로피카' → 아티스트 "캣츠아이", 앨범 "Internet Girl", 거짓 (싱글)
- 대담 : 에이티즈 콘서트 〈TOWARDS THE LIGHT〉 → 거짓
- Monthly : 2026년 8월 → 거짓""",
)

def 글링크_모으기(m, 최대=40):
    url = list_url(m)
    html = fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href]"):
        u = urllib.parse.urljoin(url, a["href"]).split("#")[0]
        if not media_of(u):
            continue
        t = a.get_text(" ", strip=True)
        if len(t) < 4:
            continue
        out.append((u, t[:120]))
    seen, uniq = set(), []
    for u, t in out:
        if u in seen or u.rstrip("/") == url.rstrip("/"):
            continue
        seen.add(u)
        uniq.append((u, t))
    return uniq[:최대]

def _제목들_뽑기(제목목록, budget):
    # 제목 여러 개를 모델에 한 번에 넣는다
    if not 제목목록 or budget["남은콜"] <= 0:
        return []
    제목들 = "\n".join(f"{i+1}. {t}" for i, t in enumerate(제목목록))
    try:
        묶음 = 제목파서.run_sync(제목들, usage_limits=UsageLimits(request_limit=2)).output
    except Exception as e:
        print("   제목 뽑기 실패:", e)
        return []
    budget["남은콜"] -= 1
    return 묶음.목록

def 후보_이즘(want_kr):
    detail = "국내" if want_kr else "해외"
    후보 = []
    # 명반 목록
    n = izm_쪽수(detail, 명반=True)
    for pg in random.sample(range(1, n + 1), min(이즘명반페이지, n)):
        items, _ = izm_목록(detail, pg, 명반=True)
        후보 += [c for c in (izm_후보(it, "이즘 명반") for it in items) if c]
    # 일반 리뷰 목록. 무작위 페이지
    n = izm_쪽수(detail, 명반=False)
    for pg in random.sample(range(1, n + 1), min(이즘후보페이지, n)):
        items, _ = izm_목록(detail, pg, 명반=False)
        후보 += [c for c in (izm_후보(it, "이즘 리뷰") for it in items) if c]
    return 후보

def 후보_아이돌로지(budget):
    pages = idology_목록("review", page=1, n=20)
    if not pages:
        return []
    뽑힘 = _제목들_뽑기([p["wp제목"] for p in pages], budget)
    후보 = []
    for x, p in zip(뽑힘, pages):
        if x.앨범리뷰인가 and x.아티스트.strip() and x.앨범.strip():
            후보.append({"artist": x.아티스트.strip(), "artist_en": None, "artist_kr": x.아티스트.strip(),
                         "title": x.앨범.strip(), "src": "아이돌로지 리뷰", "url": p["url"], "page": p,
                         "views": 0, "year": None, "앨범확실": False})   # 싱글일 수 있다. MusicBrainz로 확인한다
    return 후보

def 후보_해외매체(budget, 매체수=2):
    후보 = []
    ms = [m for m in MEDIA if m["lang"] != "ko"]
    random.shuffle(ms)
    for m in ms[:매체수]:
        링크 = 글링크_모으기(m)
        if not 링크:
            continue
        뽑힘 = _제목들_뽑기([t for u, t in 링크], budget)
        for x, (u, t) in zip(뽑힘, 링크):
            if x.앨범리뷰인가 and x.아티스트.strip() and x.앨범.strip():
                후보.append({"artist": x.아티스트.strip(), "artist_en": x.아티스트.strip(), "artist_kr": None,
                             "title": x.앨범.strip(), "src": m["name"], "url": u, "page": None,
                             "views": 0, "year": None, "앨범확실": False})
    return 후보

_후보캐시 = {}      # 한 번 돌리는 동안 한국 · 해외 후보를 한 번만 모은다
_쓴후보 = set()      # 이미 써 본 앨범은 다시 안 뽑는다

def _후보키(c):
    return (_norm(c["artist"]), _norm(c["title"]))

def 앨범_후보목록(want_kr, budget):
    if want_kr not in _후보캐시:
        후보 = 후보_이즘(want_kr)
        if want_kr:
            후보 += 후보_아이돌로지(budget)
        else:
            후보 += 후보_해외매체(budget)
        seen, out = set(), []
        for c in 후보:
            k = _후보키(c)
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
        # 조회수 높은 쪽을 앞에 둔다. 앞쪽 절반 안에서는 섞는다
        out.sort(key=lambda c: -(c.get("views") or 0))
        앞 = out[: max(최대후보앨범 * 2, len(out) // 2)]
        random.shuffle(앞)
        out = 앞 + out[len(앞):]
        _후보캐시[want_kr] = out
        출처 = {}
        for c in out:
            출처[c["src"]] = 출처.get(c["src"], 0) + 1
        print(f"  {'한국' if want_kr else '해외'} 앨범 후보 {len(out)}개 (이번 실행 동안 돌려쓴다): " + ", ".join(f"{k} {v}" for k, v in 출처.items()))
    out = [c for c in _후보캐시[want_kr] if _후보키(c) not in _쓴후보]
    return out[:최대후보앨범]

print("후보 모으기 준비 끝")

# ## 20. 상태 하나 — RunState
#
# 한 편을 만드는 동안 모든 마디가 같이 읽고 쓰는 꾸러미다.
# 변수를 함수에서 함수로 넘기지 않는다. 마디는 자기 칸만 채운다.
# 평론 원문은 `재료[*]["원문"]`에 들어 있다. 편집국장이 대조할 때 쓰고 저장할 때는 뺀다.

from pydantic import ConfigDict

START, END = "앨범뽑기", "끝"
MAX_STEPS = 60          # 마디 수 상한. 어떤 경우에도 이 위로는 안 돈다

class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 입력
    want_kr: bool
    # 앨범 뽑기
    후보목록: list = []
    후보번호: int = 0
    album: Optional[dict] = None
    뽑기출처: str = ""
    뽑기url: Optional[str] = None   # 매체 리뷰에서 뽑았으면 그 리뷰 페이지
    뽑기page: Optional[dict] = None # 그 리뷰 본문. 재료 첫 번째
    유명도: str = ""
    # 재료
    links: list = []
    pages: list = []
    blocked: list = []
    재료: list = []              # 원문 포함
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
    상태: str = "진행"          # 진행 / 올림 / 탈락 / 앨범없음 / 상한
    로그: list = []
    저장경로: Optional[str] = None

    def log(self, s):
        self.로그.append(s)
        print(s)

print("상태 준비 끝")

# ## 21. 마디
#
# 함수 하나가 단계 하나다. 상태를 받아 자기 칸만 채우고 돌려준다. 다른 마디를 안 부른다. 다음에 어디로 갈지 안 정한다.
# 모델을 부르는 마디는 `남은콜`을 하나씩 깎는다.

def n_앨범뽑기(st: RunState) -> RunState:
    if not st.후보목록:
        b = {"남은콜": st.남은콜}
        st.후보목록 = 앨범_후보목록(st.want_kr, b)
        st.남은콜 = b["남은콜"]
    st.album = None
    while st.후보번호 < len(st.후보목록):
        cand = st.후보목록[st.후보번호]
        st.후보번호 += 1
        _쓴후보.add(_후보키(cand))
        이름 = f"{cand['artist']} - {cand['title']}"
        got = mb_찾기(cand, st.want_kr)
        if not got:
            if cand.get("year") and not (YEAR_FROM <= cand["year"] <= YEAR_TO):
                st.log(f"  - {이름}: {cand['year']}년. 범위 밖  [{cand['src']}]")
                continue
            if cand.get("앨범확실") and MB없어도진행:
                got = 매체정보로_앨범(cand)
                st.log(f"  - {이름}: MusicBrainz에 없다. 매체 정보로 간다  [{cand['src']}]")
            else:
                st.log(f"  - {이름}: MusicBrainz에서 못 찾음  [{cand['src']}]")
                continue
        got["country"] = "KR" if st.want_kr else "기타"
        got["별칭"] = {"아티스트": [x for x in (cand.get("artist_kr"), cand.get("artist_en"), cand.get("artist"), got.get("artist")) if x],
                     "앨범": [x for x in (cand["title"], cand.get("title_원래"), got.get("title")) if x],
                     "한글": cand.get("artist_kr"), "영문": cand.get("artist_en")}
        ok, 근거 = 알만한가(got)
        if not ok:
            st.log(f"  - {이름}: 위키백과 문서 없음  [{cand['src']}]")
            continue
        st.album = album_detail(got) if got.get("mbid") else got
        st.album["별칭"] = got["별칭"]
        st.album["country"] = got["country"]
        st.뽑기출처, st.뽑기url, st.뽑기page, st.유명도 = cand["src"], cand["url"], cand.get("page"), 근거
        st.log(f"■ {표기(st.album)} - {st.album['title']} ({st.album.get('year')}) · {st.album.get('판종')}  [{cand['src']} · 조회 {cand.get('views') or 0} · 위키 {근거}]")
        break
    return st

def n_평론모으기(st: RunState) -> RunState:
    a = st.album
    pages = []
    if st.뽑기page:
        pages.append(st.뽑기page)                              # 뽑을 때 쓴 리뷰가 첫 번째다
    같은, 소개 = izm_같은앨범(a)                                 # 이즘에서 같은 앨범을 다룬 다른 글
    pages += 같은
    if a["country"] == "KR":
        pages += idology_검색(a)                                # 아이돌로지 리뷰 · 모음글
    st.맥락링크 = [s["링크"] for s in 소개]
    st.맥락 = "\n\n".join(s["본문"] for s in 소개)             # 이즘 아티스트 소개. 위키가 있으면 그 뒤에 붙는다
    # 검색으로 더 찾는다. API로 온 글 수만큼 빼고 채운다
    st.links = collect_links(a)
    남는칸 = PAGE_LIMIT - len(pages)
    읽음, st.blocked = read_pages(st.links, 남는칸) if 남는칸 > 0 else ([], [])
    pages += 읽음
    pages += rss_pages(a)                                      # 막힌 매체는 RSS로
    rec = wiki_reception(a)                                    # 위키백과 평가 절
    if rec:
        pages.append(rec)
    seen, st.pages = set(), []
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        st.pages.append(p)
    return st

def n_미리거르기(st: RunState) -> RunState:
    st.pages = pre_filter(st.pages, st.album)[:PAGE_LIMIT + 2]
    return st

def n_재료판정(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.재료 = keep_rules(judge_pages(st.pages, st.album, b))
    st.남은콜 = b["남은콜"]
    매체들 = ", ".join(dict.fromkeys(r["매체"] for r in st.재료)) or "-"
    st.log(f"  검색 링크 {len(st.links)} / 읽은 페이지 {len(st.pages)} / robots 막힘 {len(st.blocked)} / 남은 평론 {len(st.재료)} ({매체들})")
    return st

def n_관점묶기(st: RunState) -> RunState:
    st.관점표 = views_text(group_views(st.재료))
    w = wiki_context(st.album)
    if w:
        st.맥락 = (w["본문"] + ("\n\n[이즘 아티스트 소개]\n" + st.맥락 if st.맥락 else ""))
        st.맥락링크 = [w["링크"]] + st.맥락링크
    return st

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    st.주문 = 기획주문(앨범정보=album_text(st.album), 관점표=st.관점표, 맥락=st.맥락[:5000],
                     접근=random.choice(접근들), 온도=random.choice(온도들),
                     시작점=random.choice(시작점들), 배치=random.choice(배치들),
                     평개수=len(set(r["매체"] for r in st.재료)), 지난문제=st.지난문제)
    st.개요_ = st.글 = st.판정 = None
    st.고친곳, st.걸림 = [], []
    try:
        st.개요_ = 기획자.run_sync("뼈대를 짠다.", deps=st.주문, usage_limits=UsageLimits(request_limit=3)).output
    except Exception as e:
        st.이유 = f"기획 실패: {e}"
        st.log(f"  {st.이유}")
    st.남은콜 -= 1
    return st

def n_작가(st: RunState) -> RunState:
    a = st.album
    try:
        st.글 = 작가.run_sync("쓴다.",
                             deps=집필주문(앨범한줄=f"{표기(a)} - {a['title']} ({a.get('year')})",
                                          아티스트표기=표기(a), 개요=st.개요_, 온도=st.주문.온도),
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
    st.걸림 = 형태검사(st.글.본문, st.재료, st.맥락, st.album)
    return st

def n_편집국장(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.판정 = 편집국장_읽기(st.글, st.개요_, st.재료, st.관점표, st.맥락, st.album, b)
    st.남은콜 = b["남은콜"]
    st.올림, st.이유 = 올릴까(st.걸림, st.판정)
    문제수 = len(st.판정.문제들) if st.판정 else 0
    st.기록.append({"차례": st.바퀴, "개요": st.개요_, "글": st.글, "고친곳": st.고친곳,
                   "걸림": st.걸림, "판정": st.판정, "올림": st.올림, "이유": st.이유,
                   "조건": (st.주문.접근, st.주문.온도, st.주문.시작점, st.주문.배치)})
    st.log(f"  {st.바퀴}차: {'올림' if st.올림 else '탈락'} / {st.이유} / 교정 {len(st.고친곳)}곳 / 편집국장 문제 {문제수}건")
    if not st.올림:
        st.지난문제 = 문제목록_글(st.걸림, st.판정)   # 다음 바퀴는 이것만 들고 처음부터
    return st

def n_저장(st: RunState) -> RunState:
    st.상태 = "올림" if st.올림 else "탈락"
    p = 저장(st)
    st.저장경로 = str(p) if p else None
    if p:
        st.log(f"  저장: {p}")
    return st

print("마디 준비 끝")

# ## 22. 갈림길
#
# 함수 하나가 상태를 보고 다음 마디 이름만 돌려준다. 모델을 안 부른다. 상한도 여기서 본다.

def after_앨범뽑기(st):
    if st.album is None:
        st.상태 = "앨범없음"
        return END
    return "평론모으기"

def after_재료판정(st):
    최소 = 한국최소평 if st.want_kr else MIN_REVIEWS
    if len(st.재료) < 최소 and not 맥락만쓰기:
        st.log("  평이 모자란다. 이 앨범은 버리고 다음 후보로 간다")
        return "앨범뽑기"
    return "관점묶기"

def after_기획자(st):
    if st.개요_ is None:
        st.log(f"  {st.이유}")
        st.상태 = "탈락"
        return END
    return "작가"

def after_작가(st):
    if st.글 is None:
        st.log(f"  {st.이유}")
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
    "앨범뽑기":   (n_앨범뽑기,   after_앨범뽑기),
    "평론모으기": (n_평론모으기, 그냥("미리거르기")),
    "미리거르기": (n_미리거르기, 그냥("재료판정")),
    "재료판정":   (n_재료판정,   after_재료판정),
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
            st.log(f"  마디 [{node}] 실패: {e}")
            st.상태 = "탈락"
            break
        if 멈출마디 and node == 멈출마디:
            break
        node = edge(st)
    return st

print("갈림길 준비 끝. 마디", len(GRAPH), "개")

# ## 23. 저장
#
# 올린 글과 탈락 글을 모두 마크다운으로 남긴다. 평론 원문은 안 남긴다.

def 저장(st: RunState):
    if not st.기록:
        return None
    a, r = st.album, st.기록[-1]
    g = r["글"]
    폴더 = OUT / ("올림" if st.올림 else "탈락")
    이름 = re.sub(r"[^\w가-힣ㄱ-ㅎ -]", "", f"{a['artist']} - {a['title']}")[:60].strip()
    stamp = datetime.datetime.now().strftime("%m%d_%H%M%S")
    path = 폴더 / f"{이름}_{stamp}.md"

    출처줄 = [f"- {x['매체']} — {x['링크']}" for x in st.재료]
    출처줄 += [f"- 맥락 — {u}" for u in st.맥락링크]
    출처줄 += [f"- (본문 안 읽음, 링크만) {u}" for u in st.blocked]

    로그 = []
    for k in st.기록:
        접근, 온도, 시작점, 배치 = k["조건"]
        로그.append(f"### {k['차례']}차 — {'올림' if k['올림'] else '탈락'} / {k['이유']}")
        로그.append(f"- 조건: {접근} · {온도} · {시작점} · {배치}")
        if k["걸림"]:
            로그.append(f"- 형태 검사: {', '.join(k['걸림'])}")
        if k["고친곳"]:
            로그.append("- 교정자가 고친 곳:")
            로그 += [f"  - {x}" for x in k["고친곳"][:12]]
        if k["판정"]:
            로그.append(f"- 편집국장 {k['판정'].점수}점: {k['판정'].한줄평}")
            로그 += [f"  - ({x.종류}) {x.어디[:60]} — {x.설명}" for x in k["판정"].문제들]

    lines = [f"# {g.제목}", "", g.본문, "", "---", "", "## 앨범",
             f"- {표기(a)} - {a['title']} ({a.get('year')}) · {a.get('판종')}  [{st.뽑기출처} · 위키 {st.유명도}]",
             f"- 발매일: {a.get('발매일') or '모름'}",
             f"- 레이블: {', '.join(a.get('레이블') or []) or '모름'}",
             f"- MusicBrainz: {a.get('mbid') or '없음'}",
             "", "## 출처"] + (출처줄 or ["- 없음"]) + \
            ["", "## 개요 (최종 바퀴)", f"- 주제: {r['개요'].주제}", f"- 곡: {', '.join(r['개요'].곡셋) or '-'}",
             "", "## 판정 기록"] + 로그 + \
            ["", f"마디 {st.steps}개 · 모델 요청 {LLM_CALL_CAP - st.남은콜}회"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

print("저장 준비 끝")

# ## 아직 비어 있는 것
#
# - `목록주소` — 해외 매체의 리뷰 목록 주소. 비어 있으면 도메인 첫 화면에서 링크를 훑는다
# - `차단도메인` — 검색 결과에서 뺄 곳. 관계없는 글이 섞이면 더 넣는다
# - `매체이름표` — 매체 목록에 없는 곳을 글에서 뭐라 부를지. 없으면 "한 매체"로 나간다
# - 시작점 일곱, 배치 다섯 — 영화 쪽 목록을 음악에 맞춰 옮긴 것이다
# - `CONTACT` — MusicBrainz가 연락처를 요구한다
# - 관점 묶는 문턱값 0.62, `PASS_SCORE` 70, `시도상한` 15 — 돌려보고 맞춘다
# - 교정자의 번역투 일곱 — 빠진 것이 있으면 16번에 더 넣는다
#
# 랭그래프로 옮길 때: 마디 함수는 `add_node`에, 갈림길 함수는 `add_conditional_edges`에 그대로 꽂는다. `RunState`가 상태 스키마다.


# ======================================================================
# 실행
#   python music_review_agent.py check              # 점검 — 이즘 · 아이돌로지 API, 검색 엔진, 해외 매체, RSS. 모델 안 부른다
#   python music_review_agent.py pick [--kr|--world] [--n 3]   # 앨범 뽑기 + 재료 모으기까지만. 모델 안 부른다
#   python music_review_agent.py run [--n 5]        # 돌리기 — 올린 글이 n편 될 때까지
#   python music_review_agent.py zip                # out/music 폴더를 zip으로
# ======================================================================

# ## 24. 점검

def 점검():
    print("[이즘 API]")
    for detail in ("국내", "해외"):
        for 명반 in (False, True):
            items, n = izm_목록(detail, 1, 명반)
            print(f"  {detail} {'명반' if 명반 else '앨범 리뷰'}: {n}건 / 첫 페이지 {len(items)}건 / 쪽수 {izm_쪽수(detail, 명반)}")
    items, arts = izm_검색("자우림")
    print(f"  검색 '자우림': 앨범 리뷰 {len(items)}건, 아티스트 {len(arts)}명")
    print("\n[아이돌로지 API]")
    for cat in IDOLOGY_CAT:
        ps = idology_목록(cat, 1, 5)
        print(f"  {cat:<10} 최근 {len(ps)}건" + (f" — {ps[0]['wp제목'][:50]}" if ps else ""))
    print("\n[검색 엔진]")
    r = 검색("자우림 Purple Heart 앨범 리뷰", n=5)
    print(f"  결과 {len(r)}개")
    for u in r[:5]:
        print(f"   {'차단' if 차단됐나(u) else '통과'}  {u[:90]}")
    print("\n[해외 매체 — 앨범 뽑기용]")
    for m in MEDIA:
        if m["lang"] == "ko":
            continue
        u = list_url(m)
        ok = robots_ok(u)
        html = fetch(u) if ok else None
        n = len(글링크_모으기(m)) if html else 0
        print(f"  {m['name']:<14} robots {'O' if ok else 'X'}  본문 {'O' if html else 'X'}  글링크 {n}")
    print("\n[막힌 매체의 RSS]")
    rss_점검()


# ## 25. 뽑기만 — 모델 없이 앨범 뽑기와 재료 모으기까지 본다

def 뽑기만(want_kr, n=3):
    for i in range(n):
        print(f"\n===== 뽑기 {i+1}/{n} · {'한국' if want_kr else '해외'} =====")
        st = RunState(want_kr=want_kr, 남은콜=0)      # 남은콜 0 → 제목파서(모델)를 안 부른다. 이즘 후보만 쓴다
        st = run_graph(st, 멈출마디="미리거르기")
        if st.album is None:
            print("쓸 만한 앨범을 못 찾았다")
            continue
        print(f"  재료 후보 페이지 {len(st.pages)}개 (검색 링크 {len(st.links)}, robots 막힘 {len(st.blocked)}):")
        for p in st.pages:
            print(f"   - [{p.get('매체') or 매체이름(p['url'])}] {p['제목'][:60]}  {len(p['본문'])}자")
        if st.맥락:
            print(f"  맥락(이즘 아티스트 소개) {len(st.맥락)}자")
        w = wiki_context(st.album)
        print(f"  위키 맥락: {w['제목'] if w else '없음'}")


# ## 26. 돌리기
#
# 올린 글이 목표 편수가 될 때까지 돈다. 앨범을 시도 상한만큼 봤는데 못 채우면 멈춘다.

def run(목표편수=목표편수, 시도상한=시도상한, 쪽만=None):
    한국편수 = int(목표편수 * KR_RATIO + 0.5)        # 5편이면 한국 3 · 해외 2
    남은 = {"KR": 한국편수, "기타": 목표편수 - 한국편수}
    if 쪽만 == "KR":
        남은 = {"KR": 목표편수, "기타": 0}
    elif 쪽만 == "기타":
        남은 = {"KR": 0, "기타": 목표편수}
    결과, 시도 = [], 0
    시작 = time.time()

    while sum(남은.values()) > 0 and 시도 < 시도상한:
        시도 += 1
        쪽 = [k for k, v in 남은.items() if v > 0]
        want_kr = (random.choice(쪽) == "KR")
        print(f"\n===== 시도 {시도}/{시도상한} · 남은 한국 {남은['KR']} · 해외 {남은['기타']} · {int((time.time()-시작)/60)}분 =====")
        st = run_graph(RunState(want_kr=want_kr))
        결과.append(st)
        if st.상태 == "앨범없음":
            print("쓸 만한 앨범을 못 찾았다")
        if st.올림:
            남은["KR" if want_kr else "기타"] -= 1

    올림수 = sum(1 for s in 결과 if s.올림)
    print(f"\n끝. 올림 {올림수}편 / 탈락 {sum(1 for s in 결과 if s.상태 == '탈락')}편 / 시도 {시도}회 / {int((time.time()-시작)/60)}분")
    if 올림수 < 목표편수:
        print(f"목표 {목표편수}편에 {목표편수 - 올림수}편 모자란다.")
    return 결과


# ## 27. zip으로 묶기

def zip_outputs():
    zip_path = OUT.parent / f"music_review_{datetime.datetime.now():%m%d_%H%M}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob("*.md"):
            z.write(p, p.relative_to(OUT))
    print("zip:", zip_path)
    return zip_path


def main(argv=None):
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # 윈도우 콘솔에서 — · 같은 글자가 깨지지 않게

    ap = argparse.ArgumentParser(description="음악 리뷰 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="이즘 · 아이돌로지 API, 검색 엔진, 해외 매체, RSS 점검. 모델 안 부른다")
    p = sub.add_parser("pick", help="앨범 뽑기 + 재료 모으기까지만. 모델 안 부른다")
    p.add_argument("--kr", action="store_true", help="한국 앨범")
    p.add_argument("--world", action="store_true", help="해외 앨범")
    p.add_argument("--n", type=int, default=3)
    a = sub.add_parser("run", help="올린 글이 n편 될 때까지 돌린다")
    a.add_argument("--n", type=int, default=목표편수)
    a.add_argument("--tries", type=int, default=시도상한)
    a.add_argument("--kr", action="store_true", help="한국 앨범만")
    a.add_argument("--world", action="store_true", help="해외 앨범만")
    sub.add_parser("zip", help="out/music 폴더를 zip으로 묶는다")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        점검()
    elif args.cmd == "pick":
        뽑기만(want_kr=not args.world, n=args.n)
    elif args.cmd == "run":
        쪽만 = "KR" if args.kr else ("기타" if args.world else None)
        run(목표편수=args.n, 시도상한=args.tries, 쪽만=쪽만)
        zip_outputs()
    elif args.cmd == "zip":
        zip_outputs()


if __name__ == "__main__":
    main()
