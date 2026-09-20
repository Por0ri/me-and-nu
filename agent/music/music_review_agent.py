# # music_review_agent_v2.5 — 음악 리뷰 에이전트
#
# 콜랩 노트북(music_review_agent_v2.5.ipynb)을 스크립트로 옮긴 것이다.
# 키는 `.env` 또는 환경변수 `LLM_KEY`에서 읽는다. 결과는 `agent/out/music/올림|탈락/`에 쌓인다.
#
# `pip install -r ../requirements.txt`
#
# 앨범 하나를 뽑아 리뷰 한 편을 쓴다.
# v2.5는 상태 하나 · 마디 · 갈림길 모양이다. 랭그래프로 옮길 때 마디와 갈림길 함수를 그대로 꽂는다.
#
# ```
# 앨범뽑기 → 평론모으기 → 싸게거르기 → 재료판정 ─(평 모자람)→ 앨범뽑기
#                                           └→ 관점묶기 → 기획자 → 작가 → 교정자 → 형태검사 → 편집국장
#                                                             ↑                                  │
#                                                             └──── 걸리면 문제 목록만 들고 (세 번) ──┘
#                                                                                                └→ 저장 → 끝
# ```
#
# 편집국장이 걸면 기획자부터 다시 쓴다. 세 번까지다.
# 평론 원문은 상태에 들고 간다. 편집국장이 대조하는 데 쓴다. 디스크에는 안 남긴다.
# 올린 글이 다섯 편이 될 때까지 앨범을 계속 뽑는다.

# ## 1. 설정
#
# 아래 값만 고치면 된다.

import os, re, io, json, time, random, zipfile, datetime, pathlib, urllib.parse, sys
import requests
from dotenv import load_dotenv

# agent/.env → 이 파일 옆 .env 순서로 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다
HERE = pathlib.Path(__file__).resolve().parent
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
PAGE_LIMIT    = 10     # 앨범 하나에 읽어볼 페이지 수
후보링크배수  = 6      # robots로 막히는 곳이 많아 후보를 넉넉히 모은다
PER_MEDIA     = 2      # 매체당 남길 평론 개수
MIN_REVIEWS   = 2      # 해외 앨범. 이만큼 안 모이면 그 앨범을 버린다
한국최소평    = 1      # 한국 앨범. 열린 한국 매체가 이즘·아이돌로지뿐이라 하나로 둔다
동시판정      = 5      # 재료 판정을 한 번에 몇 페이지씩 모델에 보낼지
맥락만쓰기    = False  # 평이 없을 때 맥락만으로 쓸지. 앞서 True로 정했는데 지어낸 평이 계속 나와서 False로 둔다
최대후보앨범  = 12     # 한 편 만들 때 훑어볼 앨범 후보 수
REWRITE_LIMIT = 3      # 다시 쓰기 상한
PASS_SCORE    = 70     # 판정관 점수가 이 아래면 다시 쓴다
LLM_CALL_CAP  = 30     # 앨범 하나에 허용할 모델 요청 수 (재료 10 + 한 바퀴 4 × 세 번)
CONTACT       = "menu-project@example.com"   # MusicBrainz가 요구하는 연락처. 실제 주소로 바꾼다
# ─────────────────────────────────────────────────────────────

def _secret(name):
    v = os.environ.get(name)
    if not v or not v.strip():
        raise RuntimeError(f"환경변수 '{name}'이 없다. agent/.env 파일에 {name}=... 을 적거나 셸에서 환경변수로 넣는다. (agent/.env.example 참고)")
    return v.strip()

os.environ[KEY_ENV] = _secret("LLM_KEY")
print("키 읽음. 길이:", len(os.environ[KEY_ENV]))

UA = f"menu-music-review-agent/1.0 ( {CONTACT} )"
HEADERS = {"User-Agent": UA}

OUT = HERE.parent / "out" / "music"
(OUT / "올림").mkdir(parents=True, exist_ok=True)
(OUT / "탈락").mkdir(parents=True, exist_ok=True)

random.seed()
print("설정 끝")

# ## 2. 매체 목록과 차단 목록
#
# 매체 목록은 이제 **앨범을 뽑을 때만** 쓴다. 이 매체들의 리뷰 목록에서 앨범을 고른다.
#
# 재료(평론)를 모을 때는 매체 목록을 안 쓴다. 검색 엔진이 찾아온 것 중에서 아래 차단 목록에 걸린 주소만 뺀다.
# 나무위키·개인 블로그·커뮤니티·유튜브·스트리밍 소개문·가사 사이트가 차단 대상이다.
# 나머지는 다 읽고, 쓸지 버릴지는 여섯 값 판정이 정한다.

MEDIA = [
    # 한국 — 점검에서 열린 곳
    {"name": "이즘",        "domain": "izm.co.kr",          "lang": "ko"},
    {"name": "아이돌로지",  "domain": "idology.kr",         "lang": "ko"},
    # 해외 — 점검에서 열린 곳
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
}

def 매체이름(url):
    m = media_of(url)
    if m:
        return m["name"]
    h = _host(url)
    for d, n in 매체이름표.items():
        if h == d or h.endswith("." + d):
            return n
    return "한 매체"

def media_of(url):
    # 앨범 뽑기에서만 쓴다
    host = _host(url)
    for dom, m in MEDIA_BY_DOMAIN.items():
        if host == dom or host.endswith("." + dom):
            return m
    return None

# 매체 안의 리뷰 목록 주소. 비어 있으면 도메인 첫 화면에서 시작한다
목록주소 = {
    # "izm.co.kr": "https://www.izm.co.kr/review",
    # "rhythmer.net": "https://www.rhythmer.net/...",
}

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

print("robots 확인 준비 끝")

# ## 4. 앨범 뽑기
#
# MusicBrainz에서 뽑는다. 점수는 안 쓴다. 발매일·참여자·레이블만 가져온다.
# 한국 앨범은 한국 아티스트를 먼저 뽑고 그 아티스트의 앨범에서 고른다.

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

def 음악하는_사람인가(a):
    # 태그나 장르가 붙은 아티스트만 본다. 배우·성우는 대부분 여기서 걸러진다
    if a.get("tags") or a.get("genres"):
        return True
    dis = (a.get("disambiguation") or "").lower()
    if any(w in dis for w in ["actor", "배우", "voice", "성우", "comedian", "개그"]):
        return False
    return a.get("type") == "Group"

def pick_kr_album():
    off = random.randint(0, 900)
    arts = mb_get("artist", query="country:KR", limit=25, offset=off).get("artists", [])
    arts = [a for a in arts if 음악하는_사람인가(a)]
    random.shuffle(arts)
    for a in arts:
        try:
            rgs = mb_get("release-group", artist=a["id"], type="album", limit=50).get("release-groups", [])
        except Exception:
            continue
        cand = [g for g in rgs
                if g.get("primary-type") == "Album"
                and not g.get("secondary-types")
                and YEAR_FROM <= (_year(g.get("first-release-date")) or 0) <= YEAR_TO]
        if cand:
            g = random.choice(cand)
            return {"mbid": g["id"], "title": g["title"], "artist": a["name"],
                    "year": _year(g.get("first-release-date")), "country": "KR"}
    return None

def pick_world_album():
    off = random.randint(0, 900)
    q = f"primarytype:album AND firstreleasedate:[{YEAR_FROM} TO {YEAR_TO}]"
    rgs = mb_get("release-group", query=q, limit=25, offset=off).get("release-groups", [])
    cand = [g for g in rgs if not g.get("secondary-types") and (g.get("tags") or g.get("genres"))]
    if not cand:
        cand = [g for g in rgs if not g.get("secondary-types")]
    if not cand:
        return None
    g = random.choice(cand)
    artist = (g.get("artist-credit") or [{}])[0].get("name", "")
    return {"mbid": g["id"], "title": g["title"], "artist": artist,
            "year": _year(g.get("first-release-date")), "country": "기타"}

def album_detail(album):
    # 발매일·참여자·레이블만 가져온다. 점수는 안 본다
    d = mb_get(f"release-group/{album['mbid']}", inc="releases+artist-credits")
    rels = d.get("releases", [])
    first = sorted(rels, key=lambda r: r.get("date") or "9999")[0] if rels else None
    out = dict(album)
    종류 = list(d.get("secondary-types") or [])
    설명 = (d.get("disambiguation") or "").strip()
    판종 = []
    if "Live" in 종류: 판종.append("실황")
    if "Compilation" in 종류: 판종.append("모음")
    if "Remix" in 종류: 판종.append("리믹스")
    if "Demo" in 종류: 판종.append("데모")
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

def pick_album(want_kr):
    # 무작위 뽑기. 매체에서 거꾸로 못 뽑았을 때만 쓴다
    for _ in range(8):
        try:
            a = pick_kr_album() if want_kr else pick_world_album()
        except Exception as e:
            print("  앨범 뽑기 실패, 다시:", e)
            a = None
        if a and a.get("title") and a.get("artist"):
            return album_detail(a)
    return None

print("앨범 뽑기 준비 끝")

# ## 5. 위키백과 맥락
#
# 제작 배경, 발매 당시 반응, 영향을 가져온다. 평이 하나도 안 붙었을 때 이것만으로 글을 쓴다.

평가절제목 = ["평가", "반응", "비평", "평론", "Reception", "Critical reception", "Critical response", "Reviews"]

def wiki_reception(album):
    # 앨범 문서의 평가 절. 막힌 매체의 평이 여기 인용돼 있다
    for lang in (["ko", "en"] if album["country"] == "KR" else ["en", "ko"]):
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            s = requests.get(api, params={"action": "query", "list": "search",
                                          "srsearch": f'{album["artist"]} {album["title"]} album',
                                          "format": "json", "srlimit": 1},
                             headers=HEADERS, timeout=15).json()
            hits = s.get("query", {}).get("search", [])
            if not hits:
                continue
            title = hits[0]["title"]
            secs = requests.get(api, params={"action": "parse", "page": title, "prop": "sections", "format": "json"},
                                headers=HEADERS, timeout=15).json()
            idx = None
            for sec in secs.get("parse", {}).get("sections", []):
                if any(k.lower() in sec["line"].lower() for k in 평가절제목):
                    idx = sec["index"]; break
            if idx is None:
                continue
            html = requests.get(api, params={"action": "parse", "page": title, "section": idx,
                                             "prop": "text", "format": "json"},
                                headers=HEADERS, timeout=15).json()["parse"]["text"]["*"]
            soup = BeautifulSoup(html, "lxml")
            for t in soup(["table", "sup", "style"]):
                t.decompose()
            text = soup.get_text("\n", strip=True)
            if len(text) > 200:
                return {"url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}#평가",
                        "막힘": False, "본문": text[:8000], "제목": f"{title} — 평가 절 (위키백과)"}
        except Exception:
            continue
    return None

def wiki_context(album):
    for lang in (["ko", "en"] if album["country"] == "KR" else ["en", "ko"]):
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            s = requests.get(api, params={"action": "query", "list": "search",
                                          "srsearch": f'{album["artist"]} {album["title"]} album',
                                          "format": "json", "srlimit": 1},
                             headers=HEADERS, timeout=15).json()
            hits = s.get("query", {}).get("search", [])
            if not hits:
                continue
            title = hits[0]["title"]
            p = requests.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                          "titles": title, "format": "json"},
                             headers=HEADERS, timeout=15).json()
            page = next(iter(p["query"]["pages"].values()))
            text = (page.get("extract") or "").strip()
            if len(text) > 300:
                return {"제목": title,
                        "링크": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                        "본문": text[:6000]}
        except Exception:
            continue
    return None

def 위키_앨범문서(artist, title):
    # 앨범 문서가 있으면 웬만큼 알려진 앨범이다
    for lang in ("ko", "en"):
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            s = requests.get(api, params={"action": "query", "list": "search",
                                          "srsearch": f'{artist} {title}',
                                          "format": "json", "srlimit": 3},
                             headers=HEADERS, timeout=15).json()
            for h in s.get("query", {}).get("search", []):
                t = h["title"].lower()
                if title.lower()[:12] in t or "album" in (h.get("snippet") or "").lower():
                    return {"lang": lang, "제목": h["title"]}
        except Exception:
            continue
    return None

def 알만한가(album):
    return 위키_앨범문서(album["artist"], album["title"]) is not None

print("위키백과 준비 끝")

# ## 6. 평론 링크 모으기 — 검색 엔진
#
# 앨범 이름으로 검색한다. 매체를 지정하지 않는다.
# 검색 결과에서 차단 도메인만 빼고, 나머지는 다 후보로 삼는다.
# 해외 앨범은 Album of the Year에서 걸린 링크를 더 붙인다.
#
# 검색은 DuckDuckGo로 넣었다. 다른 걸 쓰려면 `검색()` 함수 한 개만 갈면 된다.
# 본문을 실제로 읽을 때는 여전히 robots.txt를 먼저 본다.

from bs4 import BeautifulSoup

def fetch(url, timeout=20):
    if not robots_ok(url):
        return None
    time.sleep(crawl_delay(url))
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
        print("   검색 실패:", e)
        return []

def 검색_링크(album):
    q = f'{album["artist"]} {album["title"]}'
    말 = [f'{q} 앨범 리뷰', f'{q} 평론', f'{q} album review'] if album["country"] == "KR" \
         else [f'{q} album review', f'{q} 리뷰']
    # 열린 매체 안에서도 따로 찾는다. 첫 화면에 없는 옛 앨범을 잡는다
    for m in MEDIA:
        if (m["lang"] == "ko") == (album["country"] == "KR"):
            말.append(f'{q} site:{m["domain"]}')
    out = []
    for x in 말:
        out += 검색(x, n=15)
    return out

def collect_links(album, 먼저=None):
    links = ([먼저] if 먼저 else []) + 검색_링크(album)
    seen, out = set(), []
    for u in links:
        u = u.split("#")[0]
        if u in seen or 차단됐나(u):
            continue
        seen.add(u)
        out.append(u)
    return out[: PAGE_LIMIT * 후보링크배수]

print("링크 모으기 준비 끝")

# ## 7. 본문 읽기
#
# 먼저 링크의 사이트마다 robots.txt를 한꺼번에 받아서 막힌 링크를 지운다.
# 남은 링크는 열 개씩 동시에 연다. 같은 사이트끼리만 1초씩 띄운다. 다른 사이트는 동시에 열어도 규칙에 안 걸린다.
# 앨범 하나에 열 개까지 읽는다.

from concurrent.futures import ThreadPoolExecutor
import threading

_site_lock = {}
_site_last = {}
_lock_guard = threading.Lock()

def _site_wait(url):
    # 같은 사이트는 crawl_delay(기본 1초)만큼 띄운다. 다른 사이트는 안 기다린다
    host = _host(url)
    with _lock_guard:
        lk = _site_lock.setdefault(host, threading.Lock())
    with lk:
        gap = crawl_delay(url) - (time.time() - _site_last.get(host, 0))
        if gap > 0:
            time.sleep(gap)
        _site_last[host] = time.time()

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
    return {"url": url, "막힘": False, "본문": text[:12000], "제목": title}

def read_pages(links, workers=10):
    allowed, blocked = robots_batch(links)
    pages = []
    # 열 개씩 동시에. 읽힌 게 PAGE_LIMIT을 채우면 멈춘다
    for i in range(0, len(allowed), workers):
        if len(pages) >= PAGE_LIMIT:
            break
        chunk = allowed[i:i + workers]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            htmls = list(ex.map(fetch_nowait, chunk))
        for u, h in zip(chunk, htmls):
            if h:
                pages.append(_parse_page(u, h))
    return pages[:PAGE_LIMIT], blocked

print("본문 읽기 준비 끝")

# ## 7-1. 막힌 매체의 RSS
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
    keys = [album["title"].lower(), album["artist"].lower()]
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
            low = (e.get("title", "") + " " + text).lower()
            if any(k and k in low for k in keys) and len(text) > 200:
                out.append({"url": e.get("link", u), "막힘": False, "본문": text[:12000],
                            "제목": f"{e.get('title','')} ({m['name']} RSS)"})
    return out

def rss_점검():
    for m in 막힌매체:
        print(f"  {m['name']:<14} RSS {rss_find(m['domain']) or 'X'}")

print("RSS 준비 끝")

# ## 8. 코드로 싸게 거르기
#
# 모델을 부르기 전에 확실히 아닌 것만 뺀다. 글자 수로 좋고 나쁨을 가리지는 않는다.

def cheap_filter(pages, album):
    keys = [album["title"].lower(), album["artist"].lower()]
    out, seen = [], set()
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        low = (p["본문"] + " " + p["제목"]).lower()
        if len(p["본문"]) < 400:          # 본문을 못 긁은 것이다
            continue
        if not any(k and k in low for k in keys):
            continue
        out.append(p)
    return out

print("싸게 거르기 준비 끝")

# ## 9. 재료 판정
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
    관점: str = Field(description="무엇을 봤는지 한 줄. 좋다 나쁘다만 적지 않는다")
    근거: List[Literal["곡", "가사", "사운드", "아티스트 이력", "장르 맥락"]]
    인용: str = Field(description="원문에서 그대로 옮긴 한 문장. 없으면 빈 문자열")

재료판정관 = Agent(
    MODEL,
    output_type=재료판정,
    system_prompt=(
        "너는 음악 평론 페이지를 읽고 여섯 가지를 판정한다.\n"
        "쓸지 버릴지는 네가 정하지 않는다. 값만 낸다.\n"
        "관점은 글쓴이가 이 앨범에서 무엇을 봤는지 한 줄로 적는다. 좋다 나쁘다만 적지 않는다.\n"
        "발매일·참여자·수록곡만 나열하고 판단이 없으면 홍보문이다.\n"
        "인용은 원문 문장 하나를 그대로 옮긴다. 고치지 않는다. 마땅한 문장이 없으면 빈 문자열로 둔다."
    ),
)

def _judge_one(p, album):
    ask = (f"앨범: {album['artist']} - {album['title']} ({album.get('year')})\n"
           f"페이지 제목: {p['제목']}\n주소: {p['url']}\n\n본문:\n{p['본문'][:8000]}")
    try:
        r = 재료판정관.run_sync(ask, usage_limits=UsageLimits(request_limit=2))
    except Exception as e:
        print("   판정 실패:", e)
        return None
    return {"매체": 매체이름(p["url"]), "도메인": _host(p["url"]),
            "url": p["url"], "판정": r.output, "원문": p["본문"]}

def judge_pages(pages, album, budget):
    # 페이지 여러 장을 동시에 보낸다. 판정 하나하나는 그대로다
    pages = pages[: max(0, budget["남은콜"])]
    재료 = []
    for i in range(0, len(pages), 동시판정):
        chunk = pages[i:i + 동시판정]
        with ThreadPoolExecutor(max_workers=동시판정) as ex:
            got = list(ex.map(lambda p: _judge_one(p, album), chunk))
        budget["남은콜"] -= len(chunk)
        재료 += [g for g in got if g]
    return 재료

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

# ## 10. 관점 묶기
#
# 모델을 안 부르고 임베딩만 쓴다.

from sentence_transformers import SentenceTransformer
import numpy as np

_embed = None
def embedder():
    global _embed
    if _embed is None:
        _embed = SentenceTransformer(EMBED_MODEL)
    return _embed

def group_views(재료, 문턱=0.62):
    if len(재료) <= 1:
        return [[r] for r in 재료]
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
    return (f"{a['artist']} - {a['title']} ({a.get('year')})\n"
            f"판종: {a.get('판종', '정규 앨범')}{(' — ' + a['판설명']) if a.get('판설명') else ''}\n"
            f"발매일: {a.get('발매일')}\n"
            f"레이블: {', '.join(a.get('레이블') or []) or '모름'}\n"
            f"수록곡: {', '.join((a.get('수록곡') or [])[:20]) or '모름'}")

print("관점 묶기 준비 끝")

# ## 11. 기획자
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
    지난문제: str = ""

class 인용계획(BaseModel):
    매체: str
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 영어를 그대로 두지 않는다")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것 한 줄")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. 앨범 정보·맥락·관점표에 있는 것만")
    쓸관점: List[str] = Field(description="이 문단에서 기대는 평론 관점. 관점표에서 그대로")

class 개요(BaseModel):
    주제: str = Field(description="이 글이 하려는 말 한 줄")
    판종처리: str = Field(description="판종이 정규 앨범이 아니면 어떻게 다룰지 한 줄. 정규면 빈 문자열")
    곡셋: List[str] = Field(max_length=3, description="다룰 곡. 세 개까지. 수록곡에서")
    인용둘: List[인용계획] = Field(max_length=2)
    문단들: List[문단계획] = Field(min_length=4, max_length=6)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. 여기 없는 사실은 작가가 못 쓴다")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
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
- 사실목록에는 앨범 정보·맥락·관점표에 있는 것만 넣는다. 기억으로 아는 것을 넣지 않는다.
- 곡은 세 개까지다. 수록곡에서 고른다.
- 인용은 두 개까지다. 관점표의 인용 중에서 고르고, 영어면 한국어로 옮긴다.
- 판종이 정규 앨범이 아니면 그 판을 다루는 글이다. 관점표의 평이 원판 이야기면 그렇다고 적는다.
- 문단은 넷에서 여섯이다. 문단마다 할 말 하나다.
- 결성 연도·발매일·레이블 같은 사실은 한 문단에 몰지 않는다. 필요한 문단에 하나씩 나눈다. 백과사전 문장이 되지 않게 한다.
- 인용의 매체는 관점표에 적힌 이름 그대로 쓴다. "한 매체"라고 돼 있으면 그대로 "한 매체"다. 도메인을 적지 않는다.
- 인용은 한 문장이다. 두 문장 넘는 것은 고르지 않는다.
- 마지막 문단은 글쓴이 자신의 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 앨범 중 하나다"처럼 정리하는 결론은 쓰지 않는다.{지난}"""

print("기획자 준비 끝")

# ## 12. 작가
#
# 개요만 받아 문장으로 쓴다. 재료 원문은 안 본다. 개요에 없는 사실은 못 쓴다.
# 문체 규칙은 둘만 준다. 존댓말 금지, 판단은 "~다"로 끝낸다.

class 집필주문(BaseModel):
    앨범한줄: str
    개요: 개요
    온도: str

class Article(BaseModel):
    제목: str
    본문: str

작가 = Agent(MODEL, deps_type=집필주문, output_type=Article)

@작가.system_prompt
def _작가프롬프트(ctx) -> str:
    d = ctx.deps
    o = d.개요
    문단 = "\n".join(f"{i+1}. {p.할말}\n   사실: {'; '.join(p.쓸사실) or '없음'}\n   관점: {'; '.join(p.쓸관점) or '없음'}"
                    for i, p in enumerate(o.문단들))
    인용 = "\n".join(f"- ({q.매체}) {q.옮긴문장}" for q in o.인용둘) or "없음"
    return f"""너는 앨범 리뷰를 쓴다. 뼈대는 이미 짜여 있다. 그것을 문장으로 옮긴다.

[앨범] {d.앨범한줄}
[주제] {o.주제}
[판종] {o.판종처리 or "정규 앨범이다"}
[온도] {d.온도}
[다룰 곡] {", ".join(o.곡셋)}

[문단 계획]
{문단}

[쓸 수 있는 인용 — 이 문장 그대로, 매체 이름과 함께]
{인용}

[쓸 수 있는 사실 — 이 밖의 사실은 쓰지 않는다]
{chr(10).join("- " + f for f in o.사실목록)}

[규칙]
- 존댓말을 쓰지 않는다.
- 판단은 "~다"로 끝낸다.
- 위 사실 목록과 인용 밖의 사실이나 남의 평을 넣지 않는다. 네 판단은 넣어도 된다.
- 문단 계획 순서를 지킨다. 문단마다 할 말 하나다.
- 분량은 1200자에서 2000자 사이다."""

print("작가 준비 끝")

# ## 13. 교정자
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

번역투는 영어 문장을 그대로 옮긴 것처럼 읽히는 문장이다. 아래 넷이 그것이다. 보이면 고친다.
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
- 장르 이름과 밴드 이름은 손대지 않는다. 다만 잘못 적힌 것(스톤더 → 스토너, 프로토고스 → 프로토 고스)은 바로잡고 고친 곳에 적는다.
- 영어 문장이 그대로 있으면 한국어로 옮긴다.

문장 길이는 내용이 정한다. 짧은 것을 억지로 늘리거나 긴 것을 억지로 자르지 않는다.
고친 곳마다 한 줄로 적는다.""",
)

print("교정자 준비 끝")

# ## 14. 형태 검사 (코드)
#
# 교정자가 놓친 것을 잡는다. 모델을 안 부른다.

존댓말 = re.compile(r"(습니다|합니다|입니다|세요|네요|해요|이에요|예요|드립니다)")
흐림 = re.compile(r"(인 듯하다|인 듯싶다|일 수도 있다|라고 할 수 있다|인 것 같다|일지도 모른다|라고 볼 수 있다|아닐까)")
부추김 = re.compile(r"(꼭 들어|들어봐야|필청|강력히|추천한다|놓치지 마)")
번역틀 = re.compile(r"(것이 아니라|라기보다|에 가깝다|하는 방식이다|하는 이유다|하는 지점|에 있어|을 통해|를 통해|에 의해"
                    r"|그치지 않|머물지 않|단순히 .{1,12}가 아니|는 것은 .{1,20}다\.)")
추상어 = re.compile(r"(서사|정체성|육체성|즉각성|응집력|접근성|타격감|미학|밀도|층위|텍스처)")
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
    if 영문덩어리.search(t):
        걸림.append("영어 원문")
    추 = 추상어.findall(t)
    if len(추) >= 4:
        걸림.append("추상어 " + str(len(추)) + "개: " + ", ".join(dict.fromkeys(추)))
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

# ## 15. 편집국장
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

[사실] 글에 적힌 사실 하나하나를 앨범 정보·개요·관점표·평론 원문과 대조한다.
- 어디에도 없는 사실이면 종류 "사실"로 잡는다. 남의 평을 지어낸 것도 "사실"이다.
- 앨범 정보에 있는 연도·레이블·곡 이름은 재료 안이다.
- 글쓴이 자신의 판단과 관찰은 사실이 아니다. 잡지 않는다.
- 원문에 있는 평을 글이 다르게 옮겼으면 "사실"로 잡고 원문이 뭐라 했는지 적는다.

[문장] 뜻이 안 잡히는 문장, 앞뒤가 안 맞는 문장, 고치다 망가진 문장, 영어를 그대로 옮긴 듯한 문장. 종류 "문장".

[구조] 판종에 맞게 썼는지, 시작과 끝이 맞물리는지, 곡 셋으로 갔는지, 문단마다 할 말이 하나인지. 종류 "구조".
- 마지막 문단이 글쓴이 판단이 아니라 여러 평을 합친 요약("가장 ~한 앨범 중 하나다")이면 "구조"로 잡는다.
- 결성 연도·발매일·레이블이 한 문장에 몰려 백과사전처럼 읽히면 "구조"로 잡는다.

[낱말] 오타, 없는 말, 장르·밴드·사람 이름을 틀리게 적은 것. "스톤더 둠", "프로토고스" 같은 것. 종류 "문장"으로 잡고 바른 말을 설명에 적는다.

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
           f"[평론 원문]\n{원문들}\n\n"
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

# ## 16. 앨범 후보 모으기
#
# 무작위 뽑기를 뺐다. 아무도 모를 앨범이 나오던 원인이다.
# 후보를 세 군데서 모은 뒤, 위키백과에 앨범 문서가 있는 것만 남긴다.
#
# - 매체 리뷰 목록 — 이즘·아이돌로지·Rolling Stone 같은 열린 곳이 최근 다룬 앨범. 그 리뷰 페이지를 재료로 같이 들고 간다
# - 명반 목록 — 위키백과의 롤링스톤 500대 명반 (해외만)
#
# 후보를 섞은 다음 하나씩 본다. 위키백과에 앨범 문서가 없으면 버린다.


명반목록 = [
    # 한국 명반 목록은 뺐다. 한국 앨범은 매체 리뷰 목록에서만 뽑는다
    {"lang": "en", "page": "Rolling Stone's 500 Greatest Albums of All Time"},
]

class 제목한줄(BaseModel):
    아티스트: str
    앨범: str
    앨범리뷰인가: bool

class 제목묶음(BaseModel):
    목록: List[제목한줄]

제목파서 = Agent(
    MODEL,
    output_type=제목묶음,
    system_prompt=(
        "너는 음악 웹진의 글 제목 목록을 받는다.\n"
        "제목마다 아티스트 이름과 앨범 이름을 뽑는다.\n"
        "앨범 리뷰가 아닌 제목(인터뷰, 공연, 차트, 특집, 부고)은 앨범리뷰인가를 거짓으로 둔다.\n"
        "이름을 지어내지 않는다. 제목에 없으면 빈 문자열로 둔다.\n"
        "입력 순서를 그대로 지킨다."
    ),
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

def 후보_매체(want_kr, budget, 매체수=2):
    후보 = []
    ms = [m for m in MEDIA if (m["lang"] == "ko") == want_kr]
    random.shuffle(ms)
    for m in ms[:매체수]:
        링크 = 글링크_모으기(m)
        if not 링크 or budget["남은콜"] <= 0:
            continue
        제목들 = "\n".join(f"{i+1}. {t}" for i, (u, t) in enumerate(링크))
        try:
            묶음 = 제목파서.run_sync(제목들, usage_limits=UsageLimits(request_limit=2)).output
        except Exception as e:
            print("   제목 뽑기 실패:", e)
            continue
        budget["남은콜"] -= 1
        for x, (u, t) in zip(묶음.목록, 링크):
            if x.앨범리뷰인가 and x.아티스트.strip() and x.앨범.strip():
                후보.append((x.아티스트.strip(), x.앨범.strip(), m["name"], u))   # 그 리뷰 페이지를 같이 들고 간다
    return 후보

def 위키목록_앨범들(page, lang, 최대=600):
    try:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        r = requests.get(api, params={"action": "parse", "page": page, "prop": "text",
                                      "redirects": 1, "format": "json"},
                         headers=HEADERS, timeout=30).json()
        html = r["parse"]["text"]["*"]
    except Exception as e:
        print("  명반 목록 못 읽음:", page, e)
        return []
    soup = BeautifulSoup(html, "lxml")
    out = []
    # 표에서: 기울임(앨범) 칸과 그 옆 칸(아티스트)
    for tr in soup.select("table.wikitable tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue
        it = tr.find("i")
        if not it:
            continue
        album = it.get_text(" ", strip=True)
        cells = [td.get_text(" ", strip=True) for td in tds]
        artist = ""
        for c in cells:
            c2 = re.sub(r"\[\d+\]", "", c).strip()
            if c2 and c2 != album and not c2.isdigit() and not re.fullmatch(r"[\d,.\s]+", c2) and album not in c2 and len(c2) < 80:
                artist = c2; break
        if album and artist:
            out.append((artist, album, page, None))
    # 목록에서: "Artist – Album" 꼴
    if len(out) < 20:
        for li in soup.select("ol li, ul li"):
            t = li.get_text(" ", strip=True)
            m = re.match(r"^(.{2,60}?)\s+[–—-]\s+(.{2,80})$", t)
            if m and li.find("i"):
                out.append((m.group(1).strip(), m.group(2).strip(), page, None))
    seen, uniq = set(), []
    for a, t, p, u in out:
        k = (a.lower(), t.lower())
        if k not in seen:
            seen.add(k); uniq.append((a, t, p, u))
    return uniq[:최대]

_명반캐시 = {}
def 후보_명반(want_kr):
    후보 = []
    for spec in 명반목록:
        if (spec["lang"] == "ko") != want_kr:
            continue
        key = (spec["lang"], spec["page"])
        if key not in _명반캐시:
            _명반캐시[key] = 위키목록_앨범들(spec["page"], spec["lang"])
            print(f"  명반 목록 {spec['page']}: {len(_명반캐시[key])}개")
        후보 += _명반캐시[key]
    return 후보

def mb_찾기(artist, title):
    q = f'artist:"{artist}" AND releasegroup:"{title}" AND primarytype:album'
    try:
        rgs = mb_get("release-group", query=q, limit=5).get("release-groups", [])
    except Exception:
        return None
    for g in rgs:
        if g.get("score", 0) < 80:      # 검색 일치도다. 이용자 평점이 아니다
            continue
        y = _year(g.get("first-release-date"))
        if not y or not (YEAR_FROM <= y <= YEAR_TO):
            continue
        name = (g.get("artist-credit") or [{}])[0].get("name", artist)
        return {"mbid": g["id"], "title": g["title"], "artist": name, "year": y}
    return None

_후보캐시 = {}      # 한 번 돌리는 동안 한국 · 해외 후보를 한 번만 모은다
_쓴후보 = set()      # 이미 써 본 앨범은 다시 안 뽑는다

def 앨범_후보목록(want_kr, budget):
    if want_kr not in _후보캐시:
        후보 = 후보_매체(want_kr, budget) + 후보_명반(want_kr)      # Album of the Year는 막혀서 뺐다
        seen, out = set(), []
        for a, t, src, u in 후보:
            k = (a.lower(), t.lower())
            if k in seen:
                continue
            seen.add(k)
            out.append((a, t, src, u))
        random.shuffle(out)
        _후보캐시[want_kr] = out
        print(f"  {'한국' if want_kr else '해외'} 앨범 후보 {len(out)}개 (이번 실행 동안 돌려쓴다)")
    out = [c for c in _후보캐시[want_kr] if (c[0].lower(), c[1].lower()) not in _쓴후보]
    return out[:최대후보앨범]

print("후보 모으기 준비 끝")

# ## 18. 상태 하나 — RunState
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
    뽑기url: Optional[str] = None   # 매체 리뷰 목록에서 뽑았으면 그 리뷰 페이지
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

# ## 19. 마디
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
        artist, title, src, url = st.후보목록[st.후보번호]
        st.후보번호 += 1
        _쓴후보.add((artist.lower(), title.lower()))
        got = mb_찾기(artist, title)
        if not got:
            st.log(f"  - {artist} - {title}: MusicBrainz에서 못 찾음  [{src}]")
            continue
        got["country"] = "KR" if st.want_kr else "기타"
        if not 알만한가(got):
            st.log(f"  - {got['artist']} - {got['title']}: 위키백과 문서 없음  [{src}]")
            continue
        st.album = album_detail(got)
        st.뽑기출처, st.뽑기url = src, url
        st.log(f"■ {st.album['artist']} - {st.album['title']} ({st.album.get('year')}) · {st.album.get('판종')}  [{src}]")
        break
    return st

def n_평론모으기(st: RunState) -> RunState:
    st.links = collect_links(st.album, 먼저=st.뽑기url)      # 뽑을 때 쓴 리뷰 페이지가 첫 번째다
    st.pages, st.blocked = read_pages(st.links)
    st.pages += rss_pages(st.album)                            # 막힌 매체는 RSS로
    rec = wiki_reception(st.album)                             # 위키백과 평가 절
    if rec:
        st.pages.append(rec)
    return st

def n_싸게거르기(st: RunState) -> RunState:
    st.pages = cheap_filter(st.pages, st.album)
    return st

def n_재료판정(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.재료 = keep_rules(judge_pages(st.pages, st.album, b))
    st.남은콜 = b["남은콜"]
    st.log(f"  후보 링크 {len(st.links)} / 읽음 {len(st.pages)+len(st.blocked)} / robots 막힘 {len(st.blocked)} / 남은 평론 {len(st.재료)}")
    return st

def n_관점묶기(st: RunState) -> RunState:
    st.관점표 = views_text(group_views(st.재료))
    w = wiki_context(st.album)
    st.맥락 = w["본문"] if w else ""
    st.맥락링크 = [w["링크"]] if w else []
    return st

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    st.주문 = 기획주문(앨범정보=album_text(st.album), 관점표=st.관점표, 맥락=st.맥락[:5000],
                     접근=random.choice(접근들), 온도=random.choice(온도들),
                     시작점=random.choice(시작점들), 배치=random.choice(배치들),
                     지난문제=st.지난문제)
    st.개요_ = st.글 = st.판정 = None
    st.고친곳, st.걸림 = [], []
    try:
        st.개요_ = 기획자.run_sync("뼈대를 짠다.", deps=st.주문, usage_limits=UsageLimits(request_limit=3)).output
    except Exception as e:
        st.이유 = f"기획 실패: {e}"
    st.남은콜 -= 1
    return st

def n_작가(st: RunState) -> RunState:
    a = st.album
    try:
        st.글 = 작가.run_sync("쓴다.",
                             deps=집필주문(앨범한줄=f"{a['artist']} - {a['title']} ({a.get('year')})",
                                          개요=st.개요_, 온도=st.주문.온도),
                             usage_limits=UsageLimits(request_limit=3)).output
    except Exception as e:
        st.이유 = f"집필 실패: {e}"
    st.남은콜 -= 1
    return st

def n_교정자(st: RunState) -> RunState:
    try:
        교 = 교정자.run_sync(st.글.본문, usage_limits=UsageLimits(request_limit=2)).output
        st.글 = Article(제목=st.글.제목, 본문=교.본문)
        st.고친곳 = 교.고친곳
    except Exception as e:
        st.고친곳 = [f"교정 실패: {e}"]
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

# ## 20. 갈림길
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
    "앨범뽑기":   (n_앨범뽑기,   after_앨범뽑기),
    "평론모으기": (n_평론모으기, 그냥("싸게거르기")),
    "싸게거르기": (n_싸게거르기, 그냥("재료판정")),
    "재료판정":   (n_재료판정,   after_재료판정),
    "관점묶기":   (n_관점묶기,   그냥("기획자")),
    "기획자":     (n_기획자,     after_기획자),
    "작가":       (n_작가,       after_작가),
    "교정자":     (n_교정자,     그냥("형태검사")),
    "형태검사":   (n_형태검사,   그냥("편집국장")),
    "편집국장":   (n_편집국장,   after_편집국장),
    "저장":       (n_저장,       그냥(END)),
}

def run_graph(st: RunState) -> RunState:
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
        node = edge(st)
    return st

print("갈림길 준비 끝. 마디", len(GRAPH), "개")

# ## 21. 저장
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
    출처줄 += [f"- 위키백과 — {u}" for u in st.맥락링크]
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
             f"- {a['artist']} - {a['title']} ({a.get('year')}) · {a.get('판종')}  [{st.뽑기출처}]",
             f"- 발매일: {a.get('발매일')}",
             f"- 레이블: {', '.join(a.get('레이블') or []) or '모름'}",
             "", "## 출처"] + (출처줄 or ["- 없음"]) + \
            ["", "## 개요 (최종 바퀴)", f"- 주제: {r['개요'].주제}", f"- 곡: {', '.join(r['개요'].곡셋)}",
             "", "## 판정 기록"] + 로그 + \
            ["", f"마디 {st.steps}개 · 모델 요청 {LLM_CALL_CAP - st.남은콜}회"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

print("저장 준비 끝")

# ## 아직 비어 있는 것
#
# - `목록주소` — 앨범을 뽑을 매체의 리뷰 목록 주소. 비어 있으면 도메인 첫 화면에서 링크를 훑는다. 17번 점검 셀 결과를 보고 채운다
# - `차단도메인` — 검색 결과에서 뺄 곳. 잡글이 섞이면 더 넣는다
# - `매체이름표` — 매체 목록에 없는 곳을 글에서 뭐라 부를지. 없으면 "한 매체"로 나간다
# - `예시문장` — 안 쓴다. 필요하면 기획자나 작가 프롬프트에 넣는다
# - 시작점 일곱, 배치 다섯 — 영화 쪽 목록을 음악에 맞춰 옮긴 것이다
# - `CONTACT` — MusicBrainz가 연락처를 요구한다
# - 관점 묶는 문턱값 0.62, `PASS_SCORE` 70, `시도상한` 15 — 돌려보고 맞춘다
# - 교정자의 번역투 일곱 — 빠진 것이 있으면 13번 셀에 더 넣는다
#
# 랭그래프로 옮길 때: 마디 함수는 `add_node`에, 갈림길 함수는 `add_conditional_edges`에 그대로 꽂는다. `RunState`가 상태 스키마다.


# ======================================================================
# 실행 — 콜랩 셀 17 · 22 · 23을 서브커맨드로 옮겼다
#   python music_review_agent.py check              # 17. 점검 — 검색 엔진 · 명반 목록 · 매체 · RSS
#   python music_review_agent.py run [--n 5]        # 22. 돌리기 — 올린 글이 n편 될 때까지
#   python music_review_agent.py zip                # 23. out/music 폴더를 zip으로
# ======================================================================

# ## 17. 점검

def 점검():
    print("[검색 엔진]")
    r = 검색("Radiohead In Rainbows album review", n=5)
    print(f"  결과 {len(r)}개")
    for u in r[:5]:
        print(f"   {'차단' if 차단됐나(u) else '통과'}  {u[:90]}")
    print("\n[명반 목록]")
    for spec in 명반목록:
        n = len(위키목록_앨범들(spec["page"], spec["lang"]))
        print(f"  {spec['page'][:40]:<42} {n}개")
    print("\n[앨범 뽑기용 매체]")
    for m in MEDIA:
        u = list_url(m)
        ok = robots_ok(u)
        html = fetch(u) if ok else None
        n = len(글링크_모으기(m)) if html else 0
        print(f"  {m['name']:<14} robots {'O' if ok else 'X'}  본문 {'O' if html else 'X'}  글링크 {n}")
    print("\n[막힌 매체의 RSS]")
    rss_점검()


# ## 22. 돌리기
#
# 올린 글이 목표 편수가 될 때까지 돈다. 앨범을 시도 상한만큼 봤는데 못 채우면 멈춘다.

def run(목표편수=목표편수, 시도상한=시도상한):
    한국편수 = int(목표편수 * KR_RATIO + 0.5)        # 5편이면 한국 3 · 해외 2
    남은 = {"KR": 한국편수, "기타": 목표편수 - 한국편수}
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


# ## 23. zip으로 묶기 — 콜랩의 files.download 자리다

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
    sub.add_parser("check", help="검색 엔진 · 명반 목록 · 매체 · RSS 점검 (셀 17)")
    a = sub.add_parser("run", help="올린 글이 n편 될 때까지 돌린다 (셀 22)")
    a.add_argument("--n", type=int, default=목표편수)
    a.add_argument("--tries", type=int, default=시도상한)
    sub.add_parser("zip", help="out/music 폴더를 zip으로 묶는다 (셀 23)")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        점검()
    elif args.cmd == "run":
        run(목표편수=args.n, 시도상한=args.tries)
        zip_outputs()
    elif args.cmd == "zip":
        zip_outputs()


if __name__ == "__main__":
    main()
