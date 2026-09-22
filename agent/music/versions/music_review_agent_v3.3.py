# # music_review_agent_v3.3 — 음악 리뷰 에이전트
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
#
# v2.8에서 바뀐 것
# - 배치에 "곡 순서를 따라간다"를 더했다. 기획자가 재료를 보고 배치를 고른다(무작위 아님).
#   이 배치는 곡 상한이 없다. 대신 곡마다 무슨 일이 있고 다음 곡으로 어떻게 넘어가는지를 재료에서 가져와 붙인다.
#   에이전트는 음악을 안 듣는다. 곡 사이 이어짐은 평론이 적어 놓은 것만 쓴다.
# - 앨범을 지정해서 돌리는 run_one을 넣었다. 중심에 둘 곡도 줄 수 있다. 예: run_one("우즈", "OO-LI", 곡="Drowning")
#
# v2.9에서 바뀐 것 — 글이 비어 있던 원인을 고쳤다
# - 작가가 재료를 못 봤다. 관점 한 줄 + 인용 하나 + 사실 대여섯 개로 글을 쓰니 추상어로 채웠다.
#   재료 판정관이 페이지마다 곡메모("곡 이름 — 무슨 말")와 아티스트 말을 같이 뽑고, 기획자가 관점표 + 곡메모 + 평론 원문 발췌를 본다.
#   기획자는 사실을 열 개 넘게, 곡을 둘 이상, 문단마다 구체(곡 · 소리 · 가사)를 하나 이상 넣어야 한다.
# - 재료가 모자라면(평 셋 미만, 중심 곡 이야기 없음) 재료 보강 마디가 돈다. 곡 이름 검색 · 인터뷰 검색 · 이즘 싱글 리뷰 · 위키백과 수록곡 표.
# - 인터뷰는 평이 아니라 아티스트 말로 맥락에 넣는다.
# - 앨범 정보 칸에서 지시문을 뺐다. 판종을 모르면 "앨범"이라고만 쓴다. 맥락은 지금 상태라 당시 사실로 못 쓴다.
# - 형태 검사: 곡 없음 · 중심 곡 없음 · 재료 사정 언급 · 괄호 인용 표기 · 번역틀 추가. 편집국장: 구체 없는 문단 · 되풀이 · 자기 사정 언급.
# - 매체 이름은 페이지의 og:site_name을 읽는다. 없을 때만 "한 매체".
#
# v3.0에서 바뀐 것 — 다섯 편을 못 채우던 것과 느린 것을 고쳤다
# - 올림 관문을 열었다. 걸린 것을 치명 · 고칠것 · 사소 셋으로 나눈다.
#   v2.9는 형태 검사에 하나라도 걸리거나 편집국장이 사실 문제를 한 건만 잡아도 떨어뜨렸다.
#   곡 이름 표기가 하나 다르다고, 느낌표 하나가 들어갔다고 멀쩡한 글이 나가지 못했다.
#   v3.0은 치명만 막는다. 마지막 바퀴에서는 치명이 없으면 올린다.
# - Jev를 두 군데에 넣었다. 고르기와 점수 매기기만 하는 모델이라 빠르고 싸다. 글은 못 쓴다.
#   (1) 재료 거르기 — 페이지 열두 장을 전부 큰 모델에 보내지 않는다. Jev가 먼저 버릴 것을 버린다.
#   (2) 편집국장 문제 무게 — 잡힌 문제 하나하나가 글을 막을 정도인지 잰다.
#   키가 없거나 부르다 실패하면 스스로 꺼진다. 그러면 v2.9와 똑같이 돌아간다. 느려질 뿐이다.
# - 기획자 · 작가가 실패해도 앨범을 안 버린다. 한 번 더 부른다. v2.9는 호출 한 번 실패에 앨범을 통째로 버렸다.
# - 후보가 마르면 이즘 페이지를 더 뽑아 다시 채운다.
# - 한국 앨범은 이즘 명반 목록에 있으면 위키백과 문서가 없어도 통과시킨다. 한국 앨범은 위키 문서가 대부분 없다.
# - 시도 상한을 15에서 24로 올렸다.
#
# v3.1에서 바뀐 것 — Jev 붙이는 부분을 실제 라이브러리에 맞춰 다시 짰다
# - v3.0은 공개된 글만 보고 추측으로 짰다. langchain-typesafe를 실제로 받아 보고 고쳤다.
#   판정기를 만들 때 키를 직접 넘긴다. 환경변수 이름에 안 묶인다.
#   예 · 아니오 질문에 "이럴 때 맞다 / 이럴 때 아니다" 설명을 붙일 수 있다. 홍보문 판정에 붙였다.
#   페이지를 한 장씩 차례로 묻던 것을 여덟 장씩 동시에 묻게 바꿨다. 라이브러리에 비동기 호출이 있다.
#   키가 틀렸거나 권한이 없으면 세 번 기다리지 않고 바로 끈다. 다시 불러도 같은 답이라서다.
# - Jev 키를 JEV · JEV_KEY · JEV_API_KEY · TYPESAFE_API_KEY 어느 이름으로 넣어도 찾는다.
#   라이브러리가 읽는 이름이 TYPESAFE_API_KEY인 것은 Jev를 만든 회사 이름이 TypeSafe라서다.
# - 노트북 첫 셀을 고쳤다. pydantic-ai와 openai를 짝으로 올린다.
#   콜랩에 미리 깔린 openai가 오래된 판이라 "이미 있으니 건너뛴다"로 하면 판이 어긋나 실패한다.
#   판이 바뀌면 런타임을 다시 시작하라고 알려 주고 거기서 멈춘다.
#   키를 빈 칸으로 덮어쓰지 않는다. 폴더에 두 번 들어가지 않는다.
#
# v3.2에서 바뀐 것
# - 노트북 마지막 셀에 명령줄 실행 코드가 남아 있었다. 셀을 돌리면 main이 돌아 터졌다. 뺐다.
# - main을 노트북에서 불러도 안 터진다. 무엇을 부르면 되는지 알려 주고 끝낸다.
# - 노트북을 낼 때 셀을 위에서부터 실제로 돌려 본다. 전에는 문법만 봤다.
# - 파일 자리를 잡는 줄을 고쳤다. 노트북처럼 __file__이 없는 데서도 지금 폴더를 쓴다.
#
# v3.3에서 바뀐 것
# - Jev 줄을 늘 찍는다. v3.2는 버린 것이 있을 때만 찍어서, 돌고 있는지 안 돌고 있는지 알 수 없었다.
#   이제 몇 장을 보고 몇 장을 버렸는지, 몇 밀리초 걸렸는지 매번 나온다. 꺼져 있으면 꺼졌다고 나온다.
# - 교정자를 걸린 것이 있을 때만 부른다. 형태 검사를 먼저 해 보고 깨끗하면 건너뛴다.
#   교정자는 글 전체를 다시 써서 30초에서 60초가 든다. 깨끗한 글에 그 시간을 안 쓴다.
# - 영어가 남은 것을 치명에서 고칠것으로 내렸다. 해외 앨범은 평론 원문이 전부 영어라
#   곡 제목 하나에도 걸려 글이 계속 떨어졌다. 잡는 기준도 26자에서 40자로 올렸다.
# - 앨범을 여러 편 같이 돌릴 수 있다. 동시앨범을 2나 3으로 올린다. 기본값은 1이라 지금과 같다.
#   같은 앨범을 두 번 뽑지 않게 후보 뽑는 자리에 자물쇠를 걸었다.


# ## 1. 설정
#
# 아래 값만 고치면 된다.

import os, re, io, json, time, random, zipfile, datetime, pathlib, urllib.parse, sys, html as htmlmod
import requests
from dotenv import load_dotenv

# agent/.env → 이 파일 옆 .env 순서로 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다
_이파일 = globals().get("__file__")
HERE = pathlib.Path(_이파일).resolve().parent if _이파일 else pathlib.Path.cwd()   # 노트북에서는 지금 폴더
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")

# ───── 여기서 고친다 ─────────────────────────────────────────────
MODEL       = "openai:gpt-5.6-luna"           # 영화 에이전트와 같은 모델
KEY_ENV     = "OPENAI_API_KEY"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

목표편수      = 5      # 올린 글이 이만큼 될 때까지 돈다
시도상한      = 24     # 앨범을 이만큼 봤는데 못 채우면 멈춘다
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
PASS_SCORE    = 65     # 편집국장 점수가 이 아래면 다시 쓴다
마지막안전점수 = 50     # 마지막 바퀴에서는 여기까지 봐준다. 치명이 없으면 올린다
기획재시도    = 1      # 기획자 · 작가가 실패했을 때 다시 부르는 횟수
LLM_CALL_CAP  = 40     # 앨범 하나에 허용할 모델 요청 수 (재료 10 + 보강 8 + 한 바퀴 4 × 세 번 + 여유)
CONTACT       = "menu-project@example.com"   # MusicBrainz가 요구하는 연락처. 실제 주소로 바꾼다

# v2.6
이즘후보페이지 = 3      # 이즘 리뷰 목록에서 무작위로 볼 페이지 수. 한 페이지 30건
이즘명반페이지 = 2      # 이즘 명반 목록에서 볼 페이지 수
MB없어도진행   = True   # MusicBrainz에서 못 찾아도 이즘 앨범 리뷰가 있으면 매체 정보로 간다
한국EP허용     = True   # 한국은 미니앨범(EP)도 앨범으로 친다
유명도기준     = {"KR": "아티스트", "기타": "앨범"}   # 위키백과에 이 문서가 있어야 통과
# v2.9
보강목표       = 3      # 평론이 이만큼 안 모이면 재료 보강 마디가 한 번 더 찾는다
# v3.0
이즘명반유명도 = True   # 한국 앨범은 이즘 명반 목록에 있으면 위키백과 문서가 없어도 통과시킨다
후보다시채우기 = True   # 후보가 마르면 이즘 페이지를 더 뽑아 채운다
# v3.3
동시앨범       = 1      # 앨범을 한 번에 몇 편씩 돌릴지.
                       # 1이면 지금까지처럼 하나씩 차례로 돈다. 한 편에 3분에서 8분이 든다.
                       # 2나 3으로 올리면 그만큼 줄어든다. 앨범끼리 서로 쓰는 것이 없어 같이 돌려도 된다.
                       # 다만 모델을 부르는 자리가 스레드마다 자기 루프를 써야 해서,
                       # 쓰는 모델에 따라 실패할 수 있다. 1로 두고 돌려 본 뒤 올린다.
보강페이지     = 8      # 보강에서 더 읽어볼 페이지 수
발췌글자       = 1800   # 기획자에게 주는 평론 원문 발췌. 평론마다 이만큼
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
    "genius.com", "azlyrics.com", "lyrics.co.kr", "klyrics.net", "musixmatch.com", "colorcodedlyrics.com", "kprofiles.com", "maniadb.com",
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
    # 글자와 숫자만 남긴다. 띄어쓰기 · 기호 · 대소문자 · 하이픈 종류(- – — ‐)를 다 무시한다
    s = htmlmod.unescape(s or "").lower()
    return re.sub(r"[\W_]+", "", s)

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

def mb_아티스트(name, want_kr):
    # 이름이나 별칭이 꼭 같은 아티스트. 한글 이름으로 영문 이름을, 영문 이름으로 한글 이름을 찾는다
    try:
        arts = mb_get("artist", query=f'artist:"{_lucene(name)}" OR alias:"{_lucene(name)}"', limit=6).get("artists", [])
    except Exception:
        return None
    후보 = []
    for a in arts:
        전부 = [a.get("name")] + [x.get("name") for x in a.get("aliases", []) if x.get("name")]
        if any(같은이름(name, x) for x in 전부):
            후보.append((0 if (a.get("country") == "KR") == want_kr else 1, -(a.get("score") or 0), a, 전부))
    if not 후보:
        return None
    후보.sort(key=lambda x: (x[0], x[1]))
    a, 전부 = 후보[0][2], 후보[0][3]
    return {"name": a.get("name"), "aliases": list(dict.fromkeys(x for x in 전부 if x))}

def _영문(names):
    return next((x for x in names if re.fullmatch(r"[A-Za-z0-9 .'&!\-]+", x or "")), None)

def _한글(names):
    return next((x for x in names if re.search(r"[가-힣]", x or "")), None)

def 표기_채우기(cand, want_kr):
    # 한글 · 영문 중 한쪽만 있으면 MusicBrainz 별칭으로 다른 쪽을 채운다. "우즈" → "WOODZ"
    if cand.get("artist_kr") and cand.get("artist_en"):
        return False
    ma = mb_아티스트(cand.get("artist_kr") or cand.get("artist_en") or cand.get("artist"), want_kr)
    if not ma:
        return False
    전부 = [ma["name"]] + ma["aliases"]
    if not cand.get("artist_en") and _영문(전부):
        cand["artist_en"] = _영문(전부)
        cand["영문MB"] = True            # MusicBrainz 별칭은 표기가 거칠 수 있다("Jaoorimm"). 찾는 데만 쓰고 글에는 안 쓴다
    cand["artist_kr"] = cand.get("artist_kr") or _한글(전부)
    return True

def mb_찾기(cand, want_kr):
    # cand: {"artist", "artist_en", "artist_kr", "title", ...}
    if not (cand.get("artist_kr") and cand.get("artist_en")):
        표기_채우기(cand, want_kr)
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
            "판종": " / ".join(판종) if 판종 else "모름",
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

def 알만한가(album, src=""):
    if 위키_앨범문서(album):
        return True, "앨범 문서"
    기준 = 유명도기준["KR" if album["country"] == "KR" else "기타"]
    if 기준 == "아티스트" and 위키_아티스트문서(album):
        return True, "아티스트 문서"
    # 한국 앨범은 위키백과 문서가 대부분 없다. 이즘이 명반으로 꼽았으면 그것으로 갈음한다
    if 이즘명반유명도 and album.get("country") == "KR" and "명반" in (src or ""):
        return True, "이즘 명반"
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

수록곡절제목 = ["수록곡", "트랙", "Track listing", "Tracklist", "Track list"]

def wiki_tracklist(album):
    # 앨범 문서의 수록곡 표. MusicBrainz에 없는 앨범의 곡 이름을 여기서 채운다
    doc = 위키_앨범문서(album)
    if not doc or doc.get("아티스트문서"):
        return []
    lang, title = doc["lang"], doc["제목"]
    try:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        secs = requests.get(api, params={"action": "parse", "page": title, "prop": "sections", "format": "json"},
                            headers=HEADERS, timeout=15).json()
        idx = None
        for sec in secs.get("parse", {}).get("sections", []):
            if any(k.lower() in sec["line"].lower() for k in 수록곡절제목):
                idx = sec["index"]; break
        if idx is None:
            return []
        html = requests.get(api, params={"action": "parse", "page": title, "section": idx, "prop": "text", "format": "json"},
                            headers=HEADERS, timeout=15).json()["parse"]["text"]["*"]
        soup = BeautifulSoup(html, "lxml")
        out = []
        def _제목(t):
            return re.sub(r'^[\s"“「《〈\']+|[\s"”」》〉\']+$', "", t).strip()
        for tr in soup.select("table.tracklist tr"):
            # 첫 칸은 번호("1.")다. 번호 · 시간이 아닌 첫 칸이 제목이다
            for td in tr.find_all("td"):
                t = _제목(td.get_text(" ", strip=True))
                if t and not re.fullmatch(r"[\d:.\s]+", t):
                    out.append(t); break
        if not out:
            for li in soup.select("ol li"):
                t = _제목(re.sub(r"\s*[–—-]\s*\d+:\d+\s*$", "", li.get_text(" ", strip=True)))
                if t:
                    out.append(t)
        return list(dict.fromkeys(out))[:30]
    except Exception:
        return []

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
    # 사이트가 스스로 부르는 이름. 매체 목록에 없는 곳은 이걸로 부른다
    site = None
    tag = soup.find("meta", attrs={"property": "og:site_name"}) or soup.find("meta", attrs={"name": "application-name"})
    if tag and tag.get("content"):
        site = htmlmod.unescape(tag["content"]).strip()
        site = re.split(r"\s+[-|–—:]\s+", site)[0].strip()[:24]      # "TheKMeal - The Best of ..." → "TheKMeal"
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")
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


# ## 11-A. Jev — 고르기와 점수 매기기 전용 모델
#
# Jev는 글을 쓰지 않는다. 미리 정해 둔 보기 중 하나를 고르거나, 점수를 매기거나, 예 · 아니오를 답한다.
# 답마다 확신도가 같이 온다. 보기를 미리 박아 두기 때문에 목록에 없는 답이 나오지 않는다.
#
# 두 군데에 쓴다.
#   1) 재료 거르기 — 읽어 온 페이지 열두 장 중 쓸 것만 골라 낸다. 그다음 큰 모델이 관점 · 인용 · 곡메모를 뽑는다
#   2) 편집국장 문제 무게 — 편집국장이 낸 문제 하나하나가 글을 막을 정도인지 잰다
#
# 키가 없거나 부르다 실패하면 스스로 꺼지고 큰 모델이 다 한다. 결과는 같고 느려질 뿐이다.
#
# 키 이름 — Jev를 만든 회사 이름이 TypeSafe라 라이브러리가 읽는 환경변수는 TYPESAFE_API_KEY다.
# 여기서는 JEV · JEV_KEY · JEV_API_KEY로 넣어도 찾아 쓴다. 아래 목록을 앞에서부터 본다.

JEV_ON        = True                     # False로 두면 Jev를 아예 안 쓴다
JEV_키이름들  = ["TYPESAFE_API_KEY", "JEV_API_KEY", "JEV_KEY", "JEV"]
JEV_모델      = "jev-latest"
JEV_확신문턱  = 0.60                     # 확신이 이보다 낮으면 Jev 답을 안 믿고 큰 모델에 넘긴다
JEV_시간제한  = 20.0
JEV_실패상한  = 3                        # 이만큼 연달아 실패하면 끈다
JEV_동시      = 8                        # 한 번에 몇 장을 동시에 물을지

_jev = {"경로": None, "판정기": None, "부름": 0, "실패": 0, "연속실패": 0,
        "쓴질문": 0, "입력토큰": 0}
_jev_짜임 = {}


def _jev_키():
    for 이름 in JEV_키이름들:
        v = (os.environ.get(이름) or "").strip()
        if v:
            return v
    return ""


def _jev_준비():
    # 경로를 한 번만 고른다. 라이브러리가 없거나 키가 없으면 안 쓴다.
    if _jev["경로"] is not None:
        return _jev["경로"]
    if not JEV_ON:
        _jev["경로"] = "없음"
        return _jev["경로"]
    키 = _jev_키()
    if not 키:
        _jev["경로"] = "없음"
        print(f"Jev: 키가 없다. {' · '.join(JEV_키이름들)} 중 아무 이름으로나 넣으면 된다. 큰 모델이 다 한다")
        return _jev["경로"]
    try:
        from langchain_typesafe import TypeSafeClassifier, Choice, Score, Noul, NoulCriteria
    except Exception as e:
        _jev["경로"] = "없음"
        print(f"Jev: langchain-typesafe가 없다 ({e}). pip install langchain-typesafe 를 하면 쓴다. 지금은 큰 모델이 다 한다")
        return _jev["경로"]
    try:
        _jev["판정기"] = TypeSafeClassifier(api_key=키, model=JEV_모델, timeout=JEV_시간제한)
    except Exception as e:
        _jev["경로"] = "없음"
        print(f"Jev: 판정기를 못 만들었다 ({e}). 큰 모델이 다 한다")
        return _jev["경로"]
    _jev_짜임.update({"Choice": Choice, "Score": Score, "Noul": Noul, "NoulCriteria": NoulCriteria})
    _jev["경로"] = "켬"
    print(f"Jev: 켰다. 모델 {JEV_모델}")
    return _jev["경로"]


def jev_켜짐():
    return _jev_준비() == "켬"


def jev_끄기(왜):
    if _jev["경로"] == "켬":
        print(f"Jev를 끈다: {왜}. 남은 일은 큰 모델이 한다")
    _jev["경로"] = "없음"


# ── 질문 만들기 ─────────────────────────────────────────
# 부르는 쪽은 아래 셋만 쓴다. 라이브러리 자료형은 여기서만 만진다.

def 고르기(설명, 보기):
    # 보기는 {이름: 무엇인지} 꼴이다. 답은 이름 하나와 확신도다
    return ("choice", 설명, 보기)


def 점수(설명, 단계들):
    # 단계들은 낮은 것부터 늘어놓은 목록이다. 둘 이상이어야 한다. 답은 0부터 센 자리다
    return ("score", 설명, 단계들)


def 예아니오(설명, 맞을때=None, 아닐때=None):
    return ("noul", 설명, (맞을때, 아닐때))


def _질문만들기(질문들):
    C, S, N, NC = _jev_짜임["Choice"], _jev_짜임["Score"], _jev_짜임["Noul"], _jev_짜임["NoulCriteria"]
    out = {}
    for k, (종류, 설명, 딸림) in 질문들.items():
        if 종류 == "choice":
            out[k] = C(instructions=설명, criteria=딸림)
        elif 종류 == "score":
            out[k] = S(instructions=설명, criteria=list(딸림))
        else:
            맞을때, 아닐때 = 딸림
            기준 = NC(true=맞을때, false=아닐때) if (맞을때 or 아닐때) else None
            out[k] = N(instructions=설명, criteria=기준)
    return out


def _답풀기(응답, 질문들):
    # 돌아오는 것은 {이름: (답, 확신도)}다.
    # 예 · 아니오는 확신도를 따로 안 준다. 0.5에서 얼마나 떨어졌는지로 센다
    out = {}
    for k, (종류, _, _) in 질문들.items():
        a = 응답.answers.get(k)
        if a is None:
            raise RuntimeError(f"답에 '{k}'가 없다")
        if 종류 == "noul":
            p = float(a.noul)
            out[k] = (p >= 0.5, abs(p - 0.5) * 2)
        elif 종류 == "choice":
            out[k] = (a.choice, float(a.confidence))
        else:
            out[k] = (float(a.score), float(a.confidence))
    _jev["쓴질문"] += 1
    return out


def _실패처리(e):
    _jev["실패"] += 1
    _jev["연속실패"] += 1
    이름 = type(e).__name__
    print(f"   Jev 실패({_jev['연속실패']}) {이름}: {e}")
    if "Authentication" in 이름 or "PermissionDenied" in 이름 or "NotFound" in 이름:
        jev_끄기("키나 권한 문제라 다시 불러도 같다")   # 재시도해도 소용없다
    elif _jev["연속실패"] >= JEV_실패상한:
        jev_끄기(f"{JEV_실패상한}번 연달아 실패")


def _성공처리(응답):
    _jev["부름"] += 1
    _jev["연속실패"] = 0
    try:
        _jev["입력토큰"] += int(응답.usage.input_tokens or 0)
    except Exception:
        pass


def jev_묻기(글, 질문들):
    # 글 하나에 여러 질문을 한 번에 던진다. 못 부르면 None이다.
    # 부르는 쪽은 None을 받으면 큰 모델 쪽으로 간다. 버리는 쪽으로 기울지 않는다.
    if not jev_켜짐():
        return None
    try:
        r = _jev["판정기"].invoke({"state": 글, "questions": _질문만들기(질문들)})
        _성공처리(r)
        return _답풀기(r, 질문들)
    except Exception as e:
        _실패처리(e)
        return None


async def _jev_하나(글, 질문들, 문):
    async with 문:
        try:
            r = await _jev["판정기"].ainvoke({"state": 글, "questions": _질문만들기(질문들)})
            _성공처리(r)
            return _답풀기(r, 질문들)
        except Exception as e:
            _실패처리(e)
            return None


def jev_묻기_여럿(글목록, 질문들):
    # 글 여러 개를 동시에 물어본다. 한 장씩 차례로 묻는 것보다 훨씬 빠르다.
    # 돌아오는 것은 글목록과 같은 길이의 목록이고, 못 부른 자리는 None이다.
    if not jev_켜짐() or not 글목록:
        return [None] * len(글목록)

    async def 다(): 
        문 = asyncio.Semaphore(JEV_동시)
        return await asyncio.gather(*(_jev_하나(g, 질문들, 문) for g in 글목록))

    try:
        return _run_async(다())
    except Exception as e:
        _실패처리(e)
        return [None] * len(글목록)


def jev_현황():
    if _jev["경로"] != "켬":
        return "Jev 안 씀"
    토큰 = f" · 입력 {_jev['입력토큰']:,}토큰" if _jev["입력토큰"] else ""
    return f"Jev {_jev['부름']}번{토큰} · 실패 {_jev['실패']}번"


print("Jev 준비 끝")


# ## 12. 재료 판정
#
# 페이지마다 모델을 한 번 부른다. 여섯 값에 곡메모와 아티스트 말을 더해 낸다. 쓸지 버릴지는 코드가 정한다.
# 곡메모는 그 글이 곡 이름을 들어 말한 것이다. 기획자가 여기서 곡 이야기를 가져간다. 호출 수는 안 는다.
# 원문은 재료에 같이 붙여 둔다. 기획자 발췌와 편집국장 대조에 쓴다. 저장할 때는 뺀다.

from typing import Literal, List, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

class 재료판정(BaseModel):
    다루는_정도: Literal["앨범 평", "아티스트 이야기", "인터뷰", "스치듯 언급", "관계없음"]
    평가있음: bool
    홍보문: bool
    관점: str = Field(description="글쓴이가 이 앨범에서 무엇을 근거로 무엇을 봤는지 한 줄. 좋다 나쁘다만 적지 않는다")
    근거: List[Literal["곡", "가사", "사운드", "아티스트 이력", "장르 맥락"]]
    인용: str = Field(description="원문에서 그대로 옮긴 한 문장. 원문 언어 그대로. 없으면 빈 문자열")
    곡메모: List[str] = Field(default_factory=list, description="이 글이 곡 이름을 들어 말한 것. '곡 이름 — 무슨 말' 꼴. 곡 이름은 원문 표기 그대로. 없으면 빈 목록")
    아티스트말: List[str] = Field(default_factory=list, description="아티스트 본인이 이 앨범이나 곡에 대해 한 말. 원문 표현대로 짧게. 없으면 빈 목록")

재료판정관 = Agent(
    MODEL,
    output_type=재료판정,
    system_prompt="""너는 음악 글 한 편을 읽고 아래 값을 낸다. 쓸지 버릴지는 네가 정하지 않는다. 값만 낸다.

[다루는_정도]
- "앨범 평": 이 앨범을 놓고 쓴 글이다. 앨범 리뷰, 앨범을 다시 꺼내 본 글, 앨범의 곡 하나를 놓고 쓴 리뷰.
- "아티스트 이야기": 아티스트 전체를 다루면서 이 앨범을 한 대목 이상 말한다.
- "인터뷰": 아티스트가 직접 말한 글이다. 인터뷰, 기자회견, 라이너 노트, 아티스트 코멘트. 평가가 없어도 이걸로 둔다.
- "스치듯 언급": 이름만 나온다.
- "관계없음": 이 앨범 이야기가 아니다. 이름이 같은 다른 앨범이나 곡이면 관계없음이다.
여러 앨범을 한 글에서 다루는 모음글(월간 결산, 주간 리뷰, 1st Listen)이면 이 앨범을 다룬 대목만 놓고 판정한다.
그 대목이 서너 문장 이상이고 글쓴이 판단이 있으면 "앨범 평"이다. 한두 문장이면 "스치듯 언급"이다.

[평가있음] 글쓴이가 좋다 · 나쁘다 · 아쉽다 · 낫다 같은 판단을 하나라도 적었으면 참이다.

[홍보문] 발매일 · 참여자 · 수록곡 · 활동 계획만 나열하고 글쓴이 판단이 없으면 참이다.
보도자료를 옮긴 기사, 음원 사이트 소개문이 그렇다. 별점이나 점수가 붙어 있어도 판단 문장이 없으면 홍보문이다.
인터뷰는 홍보문이 아니다. 아티스트 말이 있으면 거짓으로 둔다.

[관점] 글쓴이가 이 앨범에서 무엇을 근거로 무엇을 봤는지 한 줄로 적는다.
"좋다", "완성도가 높다"처럼 판단만 적지 않는다. 무엇을 보고 그렇게 봤는지까지 적는다.
예: "앞 앨범의 밴드 연주를 걷어내고 신시사이저로 채웠는데, 그래서 목소리가 더 또렷하게 들린다고 봤다"

[근거] 관점이 기대는 것. 곡 / 가사 / 사운드 / 아티스트 이력 / 장르 맥락 중에서 고른다. 여럿 골라도 된다.

[인용] 관점을 가장 잘 보여주는 원문 문장 하나. 원문 언어 그대로, 한 글자도 고치지 않는다. 문장 하나만 옮긴다.
마땅한 문장이 없으면 빈 문자열로 둔다.

[곡메모] 이 글이 곡 이름을 들어 말한 것을 곡마다 한 줄씩 적는다. "곡 이름 — 무슨 말" 꼴이다.
- 곡 이름은 원문 표기 그대로 적는다. 영어 곡을 한국어로 옮기지 않는다. 'Abyss'를 '심연'으로 적지 않는다.
- 무슨 말에는 글이 그 곡에 대해 적은 것을 넣는다. 소리(악기 · 목소리 · 빠르기 · 편곡), 가사 내용, 곡의 자리(여는 곡 · 끝 곡 · 타이틀), 앞뒤 곡과 이어지는 말, 글쓴이 판단.
- 글에 없는 말을 붙이지 않는다. 곡 이름만 나열된 수록곡 목록은 곡메모가 아니다.
- 열 곡이 넘으면 글이 길게 말한 곡부터 열 개까지.

[아티스트말] 아티스트 본인이 이 앨범이나 곡에 대해 한 말. 인터뷰 답변, 코멘트. 원문 표현대로 한 줄씩. 없으면 빈 목록.

주의
- 페이지에 점수 · 필자 이름 · 수록곡 목록 · 다른 글 제목이 같이 붙어 있어도 본문만 본다.
- 아티스트 이름은 한글 표기와 영문 표기가 같이 쓰인다. 표기가 달라도 같은 사람이다.
- 글이 아티스트를 오래 다루다 이 앨범 이야기로 넘어가면 "아티스트 이야기"다. 앨범 대목이 글의 중심이면 "앨범 평"이다.""",
)

def _judge_ask(p, album):
    return (f"앨범: {표기(album)} - {album['title']} ({album.get('year')})\n"
            f"아티스트의 다른 표기: {', '.join(이름들(album))}\n"
            f"수록곡(알면): {', '.join((album.get('수록곡') or [])[:20]) or '모름'}\n"
            f"페이지 제목: {p['제목']}\n주소: {p['url']}\n\n본문:\n{p['본문'][:8000]}")

async def _judge_all(pages, album):
    # 같은 루프 안에서 동시에 보낸다. 스레드로 보내면 루프가 갈려서 모델 클라이언트가 실패한다
    sem = asyncio.Semaphore(동시판정)
    async def one(p):
        async with sem:
            try:
                r = await 재료판정관.run(_judge_ask(p, album), usage_limits=UsageLimits(request_limit=2))
            except Exception as e:
                print("   판정 실패:", e)
                return None
            return {"매체": p.get("매체") or 매체이름(p["url"]), "도메인": _host(p["url"]),
                    "url": p["url"], "판정": r.output, "원문": p["본문"]}
    return await asyncio.gather(*(one(p) for p in pages))


# ## 12-A. 재료 거르기 (Jev)
#
# 읽어 온 페이지가 열두 장쯤 된다. v2.9는 이걸 전부 큰 모델에 보냈다. 페이지 한 장에 한 번씩, 열두 번이다.
# 그런데 그중 절반은 관계없는 글이거나 홍보문이라 어차피 버려진다. 버릴 것에 큰 모델을 쓴 셈이다.
#
# 여기서 Jev가 먼저 읽는다. 네 가지만 묻는다. 무엇을 다루는 글인지, 판단이 있는지, 홍보문인지, 재료로 쓸 값이 있는지.
# 전부 보기가 정해진 질문이라 Jev가 답할 수 있다. 남은 페이지만 큰 모델이 읽고 관점 · 인용 · 곡메모를 뽑는다.
#
# 확신이 낮으면 안 버린다. 애매한 것은 큰 모델이 본다. Jev는 확실히 버릴 것만 버린다.

모델판정상한 = 7        # Jev가 거른 뒤 큰 모델에 보낼 페이지 수 상한
JEV거르기본문 = 6000    # Jev에 넣을 본문 길이

_다루는보기 = {
    "앨범평": "이 앨범을 놓고 쓴 글이다. 앨범 리뷰, 앨범을 다시 꺼내 본 글, 이 앨범의 곡 하나를 놓고 쓴 리뷰. 여러 앨범을 다룬 모음글이라도 이 앨범 대목이 서너 문장 이상이고 글쓴이 판단이 있으면 여기다",
    "아티스트이야기": "아티스트 전체를 다루면서 이 앨범을 한 대목 이상 말한다",
    "인터뷰": "아티스트가 직접 말한 글이다. 인터뷰, 기자회견, 라이너 노트, 아티스트 코멘트. 평가가 없어도 여기다",
    "스치듯언급": "이 앨범 이름이 한두 문장에만 나온다. 목록에 끼어 있거나 지나가며 언급한다",
    "관계없음": "이 앨범 이야기가 아니다. 이름이 같은 다른 앨범이나 곡, 다른 아티스트다",
}

_값어치단계 = [
    "재료로 못 쓴다. 이 앨범 이야기가 없거나 나열뿐이다",
    "이름과 발매 정보 정도만 있다",
    "판단이 한두 줄 있다",
    "판단이 있고 곡이나 가사나 소리를 들어 말한다",
    "곡을 여러 개 짚어 말하고 왜 그렇게 보는지까지 적혀 있다",
]


def _거르기_질문():
    return {
        "다루는정도": 고르기("이 글이 아래 앨범을 어떻게 다루는가", _다루는보기),
        "평가있음":   예아니오("글쓴이가 이 앨범에 대해 판단을 하나라도 적었다",
                              맞을때="좋다 · 나쁘다 · 아쉽다 · 앞 앨범보다 낫다 같은 말이 한 번이라도 나온다",
                              아닐때="사실만 적혀 있다. 발매일 · 참여자 · 수록곡 · 활동 계획 나열뿐이다"),
        "홍보문":     예아니오("이 글이 홍보문이다",
                              맞을때="보도자료를 옮긴 기사이거나 음원 사이트 소개문이다. 글쓴이 판단이 없다",
                              아닐때="글쓴이가 자기 판단을 적었다. 또는 아티스트 말이 들어 있는 인터뷰다"),
        "값어치":     점수("이 글을 앨범 리뷰의 재료로 쓸 값이 얼마나 되는가", _값어치단계),
    }


def _거르기_글(p, album):
    return (f"[다루려는 앨범] {표기(album)} - {album['title']} ({album.get('year')})\n"
            f"[아티스트의 다른 표기] {', '.join(이름들(album))}\n"
            f"[페이지 제목] {p['제목']}\n"
            f"[주소] {p['url']}\n\n"
            f"[본문]\n{p['본문'][:JEV거르기본문]}")


def jev_거르기(pages, album):
    # 남길 페이지를 고른다. 돌아오는 것은 (남긴 목록, 버린 수)다.
    # 페이지를 한 장씩 차례로 묻지 않는다. 한꺼번에 동시에 묻는다.
    # 못 물은 장은 남긴다. 버리는 쪽으로 기울지 않는다.
    if not jev_켜짐() or not pages:
        return pages, 0
    답들 = jev_묻기_여럿([_거르기_글(p, album) for p in pages], _거르기_질문())
    남김, 버림 = [], 0
    for p, 답 in zip(pages, 답들):
        if 답 is None:
            남김.append((p, 2.0))
            continue
        정도, 정도확신 = 답["다루는정도"]
        평가, 평가확신 = 답["평가있음"]
        홍보, 홍보확신 = 답["홍보문"]
        값 = 답["값어치"][0]
        정도 = str(정도)
        인터뷰 = 정도 == "인터뷰"
        # 확신이 낮으면 안 버린다. 애매한 것은 큰 모델이 본다
        if 정도 in ("관계없음", "스치듯언급") and 정도확신 >= JEV_확신문턱:
            버림 += 1
            continue
        if 홍보 and not 인터뷰 and 홍보확신 >= JEV_확신문턱:
            버림 += 1
            continue
        if not 평가 and not 인터뷰 and 평가확신 >= JEV_확신문턱 and 값 <= 1:
            버림 += 1
            continue
        남김.append((p, 값 + (0.5 if 정도 == "앨범평" else 0.0)))
    남김.sort(key=lambda x: -x[1])            # 값이 높은 쪽을 먼저 큰 모델에 보낸다
    return [p for p, _ in 남김], 버림


print("재료 거르기 준비 끝")

def judge_pages(pages, album, budget, log=print):
    # Jev가 먼저 버릴 것을 버린다. 남은 것만 큰 모델이 읽는다.
    # Jev가 꺼져 있으면 v2.9와 같다. 전부 큰 모델에 간다.
    들어온수 = len(pages)
    잰때 = time.time()
    pages, 버림 = jev_거르기(pages, album)
    if jev_켜짐():
        log(f"  Jev가 {들어온수}장을 보고 {버림}장 버렸다 ({int((time.time()-잰때)*1000)}ms). "
            f"큰 모델은 {min(len(pages), 모델판정상한)}장 읽는다")
    else:
        log(f"  Jev를 안 쓴다. 큰 모델이 {min(len(pages), 모델판정상한)}장을 다 읽는다")
    pages = pages[: min(모델판정상한, max(0, budget["남은콜"]))]
    if not pages:
        return []
    got = _run_async(_judge_all(pages, album))
    budget["남은콜"] -= len(pages)
    return [g for g in got if g]

def keep_rules(판정들):
    # 평론과 아티스트 말을 나눈다. 평론은 매체당 두 개까지
    평론, 말 = [], []
    per = {}
    for r in 판정들:
        j = r["판정"]
        if j.다루는_정도 == "인터뷰" or (j.아티스트말 and j.다루는_정도 != "관계없음"):
            말.append({"매체": r["매체"], "말": [x.strip() for x in j.아티스트말 if x.strip()][:6], "링크": r["url"], "원문": r["원문"]})
        if j.다루는_정도 not in ("앨범 평", "아티스트 이야기") or not j.평가있음 or j.홍보문 or len(j.관점.strip()) < 10:
            continue
        c = per.get(r["도메인"], 0)
        if c >= PER_MEDIA:
            continue
        per[r["도메인"]] = c + 1
        평론.append({"매체": r["매체"], "관점": j.관점.strip(), "인용": j.인용.strip(), "근거": j.근거,
                    "곡메모": [x.strip() for x in j.곡메모 if x.strip()][:10],
                    "링크": r["url"], "원문": r["원문"]})
    return 평론, [m for m in 말 if m["말"]]

def 곡_언급됐나(재료, 곡):
    k = _norm(곡)
    return bool(k) and any(k in _norm(" ".join(r["곡메모"]) + " " + r["관점"] + " " + r["원문"][:6000]) for r in 재료)

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
            for m in r.get("곡메모") or []:
                줄.append(f"    곡: {m}  ({r['매체']})")
    return "\n".join(줄)

def 발췌_text(재료, n=발췌글자):
    # 기획자에게 주는 원문 발췌. 평론마다 앞부분
    return "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:n]}" for r in 재료[:6])

def 말_text(말들):
    줄 = []
    for m in 말들:
        for x in m["말"]:
            줄.append(f"- ({m['매체']}) {x}  ← {m['링크']}")
    return "\n".join(줄)

def album_text(a):
    return (f"아티스트: {표기(a)}  (다른 표기: {', '.join(이름들(a))})\n"
            f"앨범: {a['title']} ({a.get('year')})\n"
            f"판종: {a.get('판종') or '모름'}{(' — ' + a['판설명']) if a.get('판설명') else ''}\n"
            f"발매일: {a.get('발매일') or '모름'}\n"
            f"레이블: {', '.join(a.get('레이블') or []) or '모름'}\n"
            f"수록곡: {', '.join((a.get('수록곡') or [])[:25]) or '모름'}")

print("관점 묶기 준비 끝")


# ## 14. 기획자
#
# 관점표 · 곡메모 · 평론 발췌 · 맥락 · 아티스트 말 · 앨범 정보를 받아 뼈대를 짠다. 문장은 안 쓴다.
# 사실을 열 개 넘게, 곡을 둘 이상, 문단마다 구체를 하나 이상 넣어야 한다. 그래야 작가가 쓸 것이 생긴다.
# 접근 · 온도 · 시작점은 무작위로 받고 배치는 기획자가 고른다.
# 두 번째부터는 지난 번 편집국장 문제 목록을 받는다. 지난 개요와 지난 글은 안 받는다.

접근들 = ["분석", "해석", "평가"]
온도들 = ["건조", "따뜻함", "짓궂음"]
시작점들 = [                       # 일곱. PM이 고친다
    "곡 하나의 특정 대목", "앨범이 나온 때의 자리", "아티스트의 앞 앨범",
    "평이 갈린 지점", "소리의 한 요소", "앨범 제목이나 표지", "듣는 사람이 서는 자리",
]
곡순서 = "곡 순서를 따라간다"
배치들 = [                         # 여섯. PM이 고친다. 기획자가 재료를 보고 하나 고른다
    "시간 순으로 따라간다", "곡 하나를 파고든 다음 앨범 전체로 넓힌다",
    "평이 갈린 지점에서 시작해 양쪽을 본다", "소리 요소별로 나눠 본다",
    "앨범 전체 인상을 먼저 말하고 근거를 댄다", 곡순서,
]

class 기획주문(BaseModel):
    앨범정보: str
    관점표: str
    발췌: str
    맥락: str
    아티스트말: str
    접근: str
    온도: str
    시작점: str
    배치후보: str
    중심곡: str = ""
    평개수: int = 0
    지난문제: str = ""

class 인용계획(BaseModel):
    매체: str
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 영어를 그대로 두지 않는다. 원문이 한국어면 그대로")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것 한 줄")
    쓸곡: List[str] = Field(default_factory=list, description="이 문단에서 다루는 곡. 원문 표기 그대로")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. 앨범 정보 · 맥락 · 관점표 · 발췌에 있는 것만. 구체(곡 · 소리 · 가사 · 연도)가 하나 이상")
    쓸관점: List[str] = Field(description="이 문단에서 기대는 평론 관점. 관점표에서 그대로")

class 곡흐름항목(BaseModel):
    곡: str
    무슨일: str = Field(description="이 곡에서 무슨 일이 있는지 한 줄. 곡메모 · 발췌에 있는 것만")
    다음곡으로: str = Field(description="다음 곡으로 어떻게 넘어가는지. 재료에 있으면 적고 없으면 빈 문자열")

class 개요(BaseModel):
    주제: str = Field(description="이 글이 하려는 말 한 줄")
    배치: str = Field(description="배치 후보 중 하나. 이름 그대로")
    판종처리: str = Field(description="판종이 정규 앨범이 아니면 어떻게 다룰지 한 줄. 정규면 빈 문자열. 모르면 '앨범이라고만 부른다'")
    곡셋: List[str] = Field(description="다룰 곡. 둘 이상. 곡 순서 배치가 아니면 세 개까지. 곡메모 · 수록곡에 있는 곡에서. 원문 표기 그대로")
    곡흐름: List[곡흐름항목] = Field(default_factory=list, description="곡 순서 배치일 때만. 곡셋의 곡마다 하나")
    인용둘: List[인용계획] = Field(max_length=2)
    문단들: List[문단계획] = Field(min_length=4, max_length=6)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. 열 개 넘게. '사실 ← 출처' 꼴. 출처는 앨범 정보 / 맥락 / 관점표 / 발췌 / 아티스트 말 중 하나. 여기 없는 사실은 작가가 못 쓴다")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
    평하나 = """
- 관점표에 매체가 하나뿐이다. 그 매체의 판단을 이 글의 결론으로 삼지 않는다. 그 관점은 한 문단에서 한 번만 기댄다.
  나머지 문단은 곡메모 · 발췌 · 앨범 정보 · 맥락에 있는 사실과 글쓴이 자신의 판단으로 채운다.
  "평론가들은", "평단은"처럼 여럿의 평인 것처럼 적지 않는다. 매체 이름을 밝히고 그 매체가 봤다고 적는다.""" if d.평개수 <= 1 else ""
    중심 = f"\n중심 곡: {d.중심곡} — 이 곡을 곡셋 첫 번째에 두고 글의 가운데에 놓는다. 이 곡의 곡메모 · 발췌를 가장 많이 쓴다. 앨범은 이 곡을 둘러싼 자리로 다룬다." if d.중심곡 else ""
    return f"""너는 앨범 리뷰의 뼈대를 짠다. 문장은 안 쓴다. 무엇을 어떤 순서로 말할지, 어떤 곡과 어떤 사실로 말할지를 정한다.
작가는 네가 적어 준 사실과 곡만 쓸 수 있다. 네가 적게 주면 글이 비고 추상어로 찬다. 많이, 구체적으로 준다.

[앨범]
{d.앨범정보}

[관점표 — 남의 평은 여기 있는 것만. "곡:" 줄이 곡메모다]
{d.관점표 or "(없음)"}

[평론 발췌 — 사실과 곡 이야기를 여기서 캐낸다. 판단은 글쓴이 것이고 사실은 가져다 쓴다]
{d.발췌 or "(없음)"}

[아티스트 말 — 아티스트 본인이 한 말. "아티스트는 ~라고 했다"로 쓸 수 있다]
{d.아티스트말 or "(없음)"}

[맥락 — 지금 상태의 백과 정보다. 멤버 수 · 활동 상황은 앨범 당시와 다를 수 있다. 연도가 같이 적힌 것만 당시 사실로 쓴다]
{d.맥락 or "(없음)"}

[이번 글]
접근: {d.접근} / 온도: {d.온도}
시작점: {d.시작점}에서 연다
배치 후보: {d.배치후보}{중심}

[배치 고르기]
- 배치 후보 중 하나를 고른다. 이름 그대로 적는다.
- 재료가 앨범을 하나의 흐름으로 말하면(곡 순서, 앞뒤 곡의 이어짐, 앨범 전체의 이야기를 평론이 적어 놓았으면) "{곡순서}"를 고른다.
- 재료가 곡 하나하나를 안 다루면 "{곡순서}"는 고르지 않는다. 이어짐을 지어낼 수 없다.

[규칙]
- 사실목록은 열 개를 넘긴다. 앨범 정보 · 맥락 · 관점표 · 발췌 · 아티스트 말에 있는 것만. 기억으로 아는 것을 넣지 않는다.
  사실마다 "사실 ← 앨범 정보" / "사실 ← 맥락" / "사실 ← 관점표" / "사실 ← 발췌" / "사실 ← 아티스트 말" 꼴로 출처를 적는다.
  곡 이야기(이 곡은 어떤 소리다, 가사가 무엇을 말한다, 어디에 놓였다)를 사실목록에 곡 이름과 함께 넣는다. 이게 제일 중요하다.
- 곡셋은 둘 이상이다. 곡메모나 수록곡에 있는 곡에서 고른다. 곡메모가 있는 곡을 먼저 고른다. 곡 이름은 원문 표기 그대로. 영어 곡을 한국어로 옮기지 않는다.
  "{곡순서}" 배치가 아니면 세 개까지다. "{곡순서}" 배치일 때만 상한이 없고, 대신 곡셋의 곡마다 곡흐름을 하나씩 적는다.
  무슨일은 곡메모 · 발췌에 있는 것만. 다음곡으로는 재료에 그 이어짐이 적혀 있을 때만 적고 없으면 빈 문자열이다.
- 문단마다 구체가 하나 이상이다. 곡 이름, 소리(악기 · 목소리 · 빠르기 · 편곡), 가사 내용, 연도 · 사람 이름 같은 것. 쓸곡 · 쓸사실에 적는다. 구체가 없는 문단은 만들지 않는다.
- 같은 말을 문단마다 되풀이하지 않는다. 주제는 한 번 말한다. 문단마다 할 말이 다르다.
- 재료가 없다 · 정보가 제한된다 · 확인할 수 없다는 말은 어느 문단에도 넣지 않는다. 모르는 것은 안 쓴다. 아는 것만 쓴다.
- 판종은 앨범 정보대로다. "모름"이면 판종처리에 "앨범이라고만 부른다"라고 적는다. 정규라고 확인되지 않은 것을 정규 앨범이라 하지 않는다.
- 아티스트 · 앨범 이름은 앨범 정보에 적힌 표기를 쓴다.
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
# 문체 규칙은 둘만 준다. 존댓말 금지, 판단은 "~다"로 끝낸다. 표기와 구체 규칙은 문체가 아니라 내용이라 같이 준다.

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
    문단 = "\n".join(f"{i+1}. {p.할말}\n   곡: {', '.join(p.쓸곡) or '없음'}\n   사실: {'; '.join(_사실만(x) for x in p.쓸사실) or '없음'}\n   관점: {'; '.join(p.쓸관점) or '없음'}"
                    for i, p in enumerate(o.문단들))
    인용 = "\n".join(f"- {q.매체}: {q.옮긴문장}" for q in o.인용둘) or "없음"
    흐름 = ""
    if o.배치 == 곡순서 and o.곡흐름:
        흐름 = "\n[곡 흐름 — 이 순서대로 따라간다. 곡마다 무슨 일이 있는지 적고, 다음 곡으로 넘어가는 말이 있으면 그것으로 잇는다. 없으면 잇는 말을 지어내지 않는다]\n" + \
               "\n".join(f"{i+1}. '{x.곡}' — {x.무슨일}" + (f" → {x.다음곡으로}" if x.다음곡으로 else "") for i, x in enumerate(o.곡흐름)) + "\n"
    return f"""너는 앨범 리뷰를 쓴다. 뼈대는 이미 짜여 있다. 그것을 문장으로 옮긴다.

[앨범] {d.앨범한줄}
[아티스트 표기] {d.아티스트표기}
[주제] {o.주제}
[판종] {o.판종처리 or "정규 앨범이다"}
[온도] {d.온도}
[배치] {o.배치}
[다룰 곡] {", ".join(o.곡셋) or "정하지 않았다. 곡 이름을 새로 꺼내지 않는다"}
{흐름}
[문단 계획]
{문단}

[쓸 수 있는 인용 — 이 문장 그대로]
{인용}

[쓸 수 있는 사실 — 이 밖의 사실은 쓰지 않는다]
{chr(10).join("- " + _사실만(f) for f in o.사실목록)}

[규칙]
- 존댓말을 쓰지 않는다.
- 판단은 "~다"로 끝낸다.
- 위 사실 목록과 인용 밖의 사실이나 남의 평을 넣지 않는다. 네 판단은 넣어도 된다.
- 문단 계획 순서를 지킨다. 문단마다 할 말 하나다. 문단 계획에 적힌 곡과 사실을 그 문단에서 실제로 쓴다. 곡 이름을 빼먹지 않는다.
- 구체로 쓴다. 어떤 곡에서 무슨 소리가 나는지, 가사가 무엇을 말하는지, 누가 언제 무엇을 했는지. "감정이 오르내린다", "긴장이 있다" 같은 말만으로 문단을 채우지 않는다.
- 같은 말을 되풀이하지 않는다. 한 문단에서 한 말은 다른 문단에서 다시 하지 않는다. 주제는 한 번만 말한다.
- 재료가 없다 · 정보가 제한된다 · 확인할 수 없다 · 단정할 수 없다는 말을 쓰지 않는다. 아는 것만 쓴다.
- 인용은 "이즘은 “…”라고 썼다" 꼴로 매체 이름을 문장 안에 넣는다. 매체 이름을 괄호로 앞에 붙이지 않는다.
- 표기: 아티스트는 [아티스트 표기]대로 첫 등장에 한 번 적고 그 뒤로는 앞 이름만 쓴다. 앨범 이름은 《 》, 곡 이름은 ' '로 감싼다. 이름 철자는 개요에 적힌 그대로다. 영어 곡 이름을 옮기지 않는다.
- 판종: [판종]에 적힌 대로 부른다. "앨범이라고만 부른다"면 정규 · 미니를 붙이지 않는다.
- 곡 흐름이 있으면 곡 이름만 적고 지나가지 않는다. 곡마다 무슨 일이 있는지 한 문장 이상 적는다.
- 분량은 1200자에서 2000자 사이다. 곡 흐름이 있으면 2400자까지 된다."""

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
8. "~라기보다 ~으로 보는 편이 맞다", "~라는 데 있다", "~라는 데서 생긴다", "~을 제시한다", "설득력을 얻는다", "유효하다". 판단을 동사로 바로 말한다. "~다".

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
                    r"|그치지 않|머물지 않|단순히 .{1,12}가 아니|는 것은 .{1,20}다\.|편이 맞다|데 있다|데서 생긴다|을 제시한다|를 제시한다|설득력을 얻|유효하다)")
추상어 = re.compile(r"(서사|정체성|육체성|즉각성|응집력|접근성|타격감|미학|밀도|층위|텍스처|낙차|긴장감|다채로움|보편성)")
다수평 = re.compile(r"(평론가들은|평단은|비평가들은|많은 이들이|대체로 .{0,6}평가|호평이 이어|평이 갈렸)")
재료사정 = re.compile(r"(확인할 수 있는 정보|확인할 수 없|알 수 없다는 점|정보(는|가) .{0,12}제한|MusicBrainz|자료가 (없|부족)|단정할 수는 없|판단의 범위)")
괄호인용 = re.compile(r"\(\s*[가-힣A-Za-z .!'’]{2,20}\s*\)\s*[“\"]")
따옴표안 = re.compile(r"[“\"]([^”\"]{10,})[”\"]")
영문덩어리 = re.compile(r"[A-Za-z][A-Za-z ,.'’\-]{39,}")   # v3.3: 26자에서 40자로. 곡 제목 정도는 봐준다

def _겹침(a, b, n=12):
    A = {a[i:i+n] for i in range(max(0, len(a)-n+1))}
    B = {b[i:i+n] for i in range(max(0, len(b)-n+1))}
    return len(A & B) / len(A) if A and B else 0.0

def 형태검사(본문, 재료, 맥락, album, 배치="", 곡셋=None, 중심곡=None):
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
    for x in 이름들(album) + 제목들(album) + list(album.get("수록곡") or []) + list(곡셋 or []):
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
            걸림.append("인용이 길다")
            break
    if re.search(r"\(\s*[a-z0-9.-]+\.[a-z]{2,}\s*\)", t):
        걸림.append("도메인이 글에 나온다")
    if "!" in t:
        걸림.append("느낌표")
    if 부추김.search(t):
        걸림.append("부추기는 말")
    # 곡 — 정한 곡이 글에 있어야 한다. 수록곡을 알면 하나는 나와야 한다
    low = _norm(t)
    정한곡 = [c for c in (곡셋 or []) if _norm(c)]
    if 정한곡 and not any(_norm(c) in low for c in 정한곡):
        걸림.append("정한 곡이 글에 없다")
    if 중심곡 and _norm(중심곡) not in low:
        걸림.append(f"중심 곡 없음: {중심곡}")
    곡들 = [c for c in (album.get("수록곡") or []) if len(c) >= 3]
    나온곡 = [c for c in 곡들 if _norm(c) in low]
    if 곡들 and not 나온곡 and not 정한곡:
        걸림.append("곡 이름 없음")
    if len(나온곡) > 3 and 배치 != 곡순서:          # 곡 순서 배치는 곡을 다 다룬다
        걸림.append(f"곡 나열 {len(나온곡)}개")
    if not (1000 <= len(t) <= (2800 if 배치 == 곡순서 else 2400)):
        걸림.append(f"분량 {len(t)}자")
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
- 맥락(백과 정보)은 지금 상태다. 지금 멤버 수 · 지금 활동 상황을 앨범 당시 사실처럼 적었으면("1997년 3인조 밴드는") "사실"로 잡는다. 연도가 같이 적힌 것만 당시 사실이다.
- 판종을 모르는 앨범을 "정규 앨범"이라고 적었으면 "사실"로 잡는다.
- 곡 이름을 옮겨 적은 것('Abyss'를 '심연')은 "사실"로 잡고 원래 표기를 적는다.

[문장] 뜻이 안 잡히는 문장, 앞뒤가 안 맞는 문장, 고치다 망가진 문장, 영어를 그대로 옮긴 듯한 문장. 종류 "문장".

[구조] 판종에 맞게 썼는지, 시작과 끝이 맞물리는지, 곡 셋 안에서 갔는지(곡 순서 배치는 예외), 문단마다 할 말이 하나인지. 종류 "구조".
- 마지막 문단이 글쓴이 판단이 아니라 여러 평을 합친 요약("가장 ~한 앨범 중 하나다")이면 "구조"로 잡는다.
- 결성 연도 · 발매일 · 레이블이 한 문장에 몰려 백과사전처럼 읽히면 "구조"로 잡는다.
- 첫 문단과 마지막 문단이 같은 말을 되풀이하면 "구조"로 잡는다.
- 곡 순서 배치 글에서 곡 이름만 나열하고 그 곡에서 무슨 일이 있는지 없으면 "구조"로 잡는다.
- 곡 · 소리 · 가사 · 연도 같은 구체가 하나도 없이 "감정이 오르내린다", "긴장이 있다" 같은 말로만 채운 문단은 "구조"로 잡는다.
- 같은 뜻의 말("개인적인 이야기가 보편으로 넓어진다")이 세 번 넘게 되풀이되면 "구조"로 잡는다.
- 글이 자기 재료 사정을 말하면("확인할 수 있는 정보가 제한된다", "수록곡을 알 수 없다", "단정할 수는 없다") "구조"로 잡는다. 독자는 그걸 알 필요가 없다.
- 인용 앞에 매체 이름을 괄호로 붙인 것("(이즘) “…”")은 "문장"으로 잡는다. "이즘은 “…”라고 썼다" 꼴이어야 한다.
- 곡 사이 이어짐("A가 끝나면 B로 넘어간다", "A의 잔향 위로 B가 시작한다")을 적었는데 평론 원문 · 관점표 · 맥락 어디에도 없으면 "사실"로 잡는다. 에이전트는 음악을 못 듣는다.

[낱말] 오타, 없는 말, 장르 · 밴드 · 사람 이름을 틀리게 적은 것. "스톤더 둠", "프로토고스" 같은 것. 종류 "문장"으로 잡고 바른 말을 설명에 적는다.

직접 고치지 않는다. 올릴지 말지도 정하지 않는다. 점수와 문제 목록만 낸다.
문제마다 어느 문장인지 그대로 옮겨 적는다.""",
)

def 편집국장_읽기(글: Article, 개요_: 개요, 재료, 관점표, 맥락, album, budget, 아티스트말=None):
    if budget["남은콜"] <= 0:
        return None
    원문들 = "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:4000]}" for r in 재료) or "(없음)"
    원문들 += "".join(f"\n\n### (아티스트 말 · {m['매체']}) {m['링크']}\n" + "\n".join("- " + x for x in m["말"]) for m in (아티스트말 or []))
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


# ## 18-A. 올릴지 정하기 — 무게로 나눈다
#
# v2.9는 형태 검사에 하나라도 걸리면 떨어뜨렸다. 편집국장이 사실 문제를 한 건만 잡아도 떨어뜨렸다.
# 그래서 곡 이름 표기가 하나 다르다고, 느낌표 하나가 들어갔다고 멀쩡한 글이 나가지 못했다.
# 목표가 다섯 편인데 관문이 다섯 개 겹쳐 있어 구조상 못 채웠다.
#
# v3.0은 걸린 것을 셋으로 나눈다.
#   치명   — 글을 못 내보낸다. 없는 사실, 지어낸 평, 베껴 적기, 곡 이야기가 아예 없는 글
#   고칠것 — 한 번 더 다듬으면 된다. 존댓말, 번역투, 느낌표, 추상어
#   사소   — 그냥 올린다. 표기 차이, 오타, 분량이 조금 모자란 것
#
# 치명이 하나라도 있으면 다시 쓴다. 치명이 없으면 고칠 것 개수와 점수로 정한다.
# 마지막 바퀴에서는 치명만 없으면 올린다. 세 번 고쳐도 안 되는 글을 네 번째에 버리지 않는다.

형태_치명 = ["재료 옮겨 적기", "없는 다수 평", "재료 사정 언급",
             "정한 곡이 글에 없다", "곡 이름 없음", "중심 곡 없음", "문장 끊김"]
형태_고칠것 = ["존댓말", "번역투 틀", "판단 흐리는 끝맺음", "괄호 인용 표기",
               "인용이 길다", "도메인이 글에 나온다", "느낌표", "부추기는 말", "추상어",
               "영어 원문"]
# "영어 원문"은 v3.2까지 치명이었다. 해외 앨범은 평론 원문이 전부 영어라
# 곡 제목이나 짧은 구절 하나에도 걸려 글이 계속 떨어졌다. 교정자가 받아 옮기게 내렸다
# 나머지(문단 시작 반복, 곡 나열, 분량)는 사소로 본다. 분량은 아래에서 따로 잰다

분량_치명아래 = 800      # 이보다 짧으면 치명이다. 글이 빈 것이다
고칠것_상한   = 3        # 고칠 것이 이보다 많으면 한 번 더 쓴다


def 형태무게(걸림):
    치명, 고칠것, 사소 = [], [], []
    for x in 걸림:
        머리 = x.split(":")[0].split("(")[0].strip()
        if x.startswith("분량"):
            m = re.search(r"(\d+)자", x)
            (치명 if (m and int(m.group(1)) < 분량_치명아래) else 사소).append(x)
        elif any(머리.startswith(k) or k in x for k in 형태_치명):
            치명.append(x)
        elif any(머리.startswith(k) for k in 형태_고칠것):
            고칠것.append(x)
        else:
            사소.append(x)
    return 치명, 고칠것, 사소


_무게보기 = {
    "치명": "재료 어디에도 없는 사실을 적었다. 남의 평을 지어냈다. 원문이 한 말을 다르게 옮겼다. 다른 앨범 이야기를 이 앨범 것으로 적었다. 음악을 들어야만 알 수 있는 것을 적었다. 지금 상황을 앨범 나올 당시 사실처럼 적었다",
    "고칠것": "사실은 맞는데 문장이나 짜임이 어긋났다. 뜻이 안 잡히는 문장, 같은 말 되풀이, 문단 순서, 백과사전처럼 읽히는 대목",
    "사소": "표기 차이, 오타, 띄어쓰기, 한글과 영문 표기가 섞인 것. 뜻은 그대로 읽힌다",
}


def _무게_규칙(x):
    # Jev를 못 부를 때 쓰는 말 규칙이다
    설명 = (x.설명 or "") + " " + (x.어디 or "")
    if re.search(r"(표기|오타|철자|띄어쓰기|대소문자|한글|영문)", 설명):
        return "사소"
    if x.종류 == "사실":
        if re.search(r"(지어|없는 사실|재료에 없|원문에 없|어디에도 없|다르게 옮|허위|근거 ?없|확인되지)", 설명):
            return "치명"
        return "치명"
    return "고칠것"


def 문제무게(문제들, album):
    # 편집국장이 낸 문제마다 무게를 매긴다. Jev가 재고, 못 부르면 말 규칙이 대신 잰다.
    out = []
    for x in 문제들 or []:
        무게 = None
        if jev_켜짐():
            글 = (f"[앨범] {표기(album)} - {album['title']}\n"
                  f"[편집국장이 잡은 종류] {x.종류}\n"
                  f"[문제가 있는 대목] {x.어디}\n"
                  f"[편집국장 설명] {x.설명}")
            답 = jev_묻기(글, {"무게": 고르기("이 문제가 글을 못 내보낼 정도인가", _무게보기)})
            if 답:
                값, 확신 = 답["무게"]
                if 확신 >= JEV_확신문턱:
                    무게 = str(값)
        out.append((x, 무게 or _무게_규칙(x)))
    return out


def 올릴까(걸림, p, 바퀴=1, album=None):
    # 코드가 정한다. 치명만 막는다.
    형_치명, 형_고칠것, 형_사소 = 형태무게(걸림)
    if p is None:
        # 편집국장을 못 불렀다. 형태 검사만으로 본다. 치명이 없으면 올린다
        if 형_치명:
            return False, "형태 치명: " + ", ".join(형_치명), {"형태치명": 형_치명}
        return True, f"편집국장 못 부름 · 형태 치명 없음(고칠것 {len(형_고칠것)})", {}
    무게표 = 문제무게(p.문제들, album) if album else [(x, _무게_규칙(x)) for x in (p.문제들 or [])]
    편_치명 = [x for x, w in 무게표 if w == "치명"]
    편_고칠것 = [x for x, w in 무게표 if w == "고칠것"]
    편_사소 = [x for x, w in 무게표 if w == "사소"]
    치명 = 형_치명 + [f"{x.종류}: {x.설명}" for x in 편_치명]
    고칠것수 = len(형_고칠것) + len(편_고칠것)
    상세 = {"형태치명": 형_치명, "형태고칠것": 형_고칠것, "형태사소": 형_사소,
            "편집치명": [x.설명 for x in 편_치명], "편집고칠것": [x.설명 for x in 편_고칠것],
            "편집사소": [x.설명 for x in 편_사소], "점수": p.점수}
    마지막 = 바퀴 >= REWRITE_LIMIT
    if 치명:
        return False, f"치명 {len(치명)}건: " + "; ".join(치명[:3]), 상세
    기준 = 마지막안전점수 if 마지막 else PASS_SCORE
    if p.점수 < 기준:
        return False, f"점수 {p.점수} (기준 {기준}) · 고칠것 {고칠것수}건", 상세
    if not 마지막 and 고칠것수 > 고칠것_상한:
        return False, f"고칠것 {고칠것수}건 · 점수 {p.점수}", 상세
    꼬리 = f" · 고칠것 {고칠것수} · 사소 {len(형_사소) + len(편_사소)}"
    return True, f"점수 {p.점수}{꼬리}" + (" (마지막 바퀴)" if 마지막 else ""), 상세


print("올림 판정 준비 끝")

def 문제목록_글(걸림, p, 상세=None):
    # 다시 쓸 때 들고 가는 목록이다. 치명과 고칠 것만 적는다.
    # 사소한 것(표기 차이 · 오타)까지 들고 가면 기획자가 그걸 고치느라 뼈대를 망친다.
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
_후보자물쇠 = threading.Lock()   # 앨범을 여러 편 같이 돌릴 때 같은 앨범을 두 번 뽑지 않게 한다

def _후보키(c):
    return (_norm(c["artist"]), _norm(c["title"]))

def 앨범_후보목록(want_kr, budget):
    with _후보자물쇠:
        return _후보목록_안(want_kr, budget)


def _후보목록_안(want_kr, budget):
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
    if not out and 후보다시채우기:
        # 후보를 다 써 버렸다. 이즘 리뷰 목록에서 다른 페이지를 뽑아 채운다
        새것 = _후보더모으기(want_kr, budget)
        print(f"  {'한국' if want_kr else '해외'} 후보를 다 썼다. {len(새것)}개 더 모았다")
        out = [c for c in _후보캐시[want_kr] if _후보키(c) not in _쓴후보]
    out = out[:최대후보앨범]
    for c in out:
        _쓴후보.add(_후보키(c))      # 미리 찍어 둔다. 같이 도는 다른 편이 같은 앨범을 안 뽑는다
    return out


def _후보더모으기(want_kr, budget):
    더 = 후보_이즘(want_kr)                       # 무작위 페이지라 부를 때마다 다른 것이 온다
    더 += 후보_아이돌로지(budget) if want_kr else 후보_해외매체(budget)
    있던 = {_후보키(c) for c in _후보캐시.get(want_kr, [])}
    새것 = []
    for c in 더:
        k = _후보키(c)
        if k in 있던 or k in _쓴후보:
            continue
        있던.add(k)
        새것.append(c)
    _후보캐시.setdefault(want_kr, [])
    _후보캐시[want_kr] = _후보캐시[want_kr] + 새것
    return 새것

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
    지정후보: Optional[dict] = None   # run_one으로 앨범을 지정했을 때
    중심곡: Optional[str] = None      # 글의 가운데에 둘 곡
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
    판정원본: list = []          # 판정한 것 전부. 보강 뒤에 다시 거른다
    아티스트말: list = []        # 인터뷰에서 나온 아티스트 본인의 말
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
    무게상세: dict = {}          # 걸린 것을 치명 · 고칠것 · 사소로 나눈 것
    기획재시도한: int = 0        # 기획자 · 작가를 다시 부른 횟수
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
    if not st.후보목록 and st.지정후보:
        st.후보목록 = [st.지정후보]
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
                     "한글": cand.get("artist_kr"), "영문": None if cand.get("영문MB") else cand.get("artist_en")}
        if cand["src"] == "지정":
            ok, 근거 = True, "지정 앨범"                 # 사람이 고른 앨범은 유명도를 안 본다
        else:
            ok, 근거 = 알만한가(got, cand["src"])
        if not ok:
            st.log(f"  - {이름}: 위키백과 문서 없음  [{cand['src']}]")
            continue
        st.album = album_detail(got) if got.get("mbid") else got
        st.album["별칭"] = got["별칭"]
        st.album["country"] = got["country"]
        if not st.album.get("수록곡"):
            곡 = wiki_tracklist(st.album)                    # MusicBrainz에 없으면 위키백과 수록곡 표
            if 곡:
                st.album["수록곡"] = 곡
                st.log(f"  수록곡 {len(곡)}곡은 위키백과에서")
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
    st.판정원본 = judge_pages(st.pages, st.album, b, log=st.log)
    st.남은콜 = b["남은콜"]
    st.재료, st.아티스트말 = keep_rules(st.판정원본)
    매체들 = ", ".join(dict.fromkeys(r["매체"] for r in st.재료)) or "-"
    곡메모수 = sum(len(r["곡메모"]) for r in st.재료)
    st.log(f"  검색 링크 {len(st.links)} / 읽은 페이지 {len(st.pages)} / robots 막힘 {len(st.blocked)} / 남은 평론 {len(st.재료)} ({매체들}) / 곡메모 {곡메모수} / 아티스트 말 {sum(len(m['말']) for m in st.아티스트말)}")
    return st

def 보강_링크(album, 중심곡):
    # 곡 이름 · 인터뷰 · 곡별 리뷰로 다시 찾는다
    곡들 = [중심곡] if 중심곡 else []
    곡들 += [c for c in (album.get("수록곡") or [])[:4] if c not in 곡들]
    KR = album["country"] == "KR"
    말 = []
    for name in 이름들(album)[:2]:
        t = album["title"]
        말 += ([f"{name} {t} 인터뷰", f"{name} {t} 앨범 소개 수록곡", f"{name} {t} 트랙 리뷰"] if KR
               else [f"{name} {t} interview", f"{name} {t} track by track", f"{name} {t} 앨범 리뷰"])
        for c in 곡들[:3]:
            말 += ([f"{name} {c} 리뷰", f"{name} {c} 가사 해석"] if KR else [f"{name} {c} review", f"{name} {c} lyrics meaning"])
    out = []
    for q in dict.fromkeys(말):
        out += 검색(q, n=10)
    seen, uniq = set(), []
    for u in out:
        u = u.split("#")[0]
        if u in seen or 차단됐나(u) or (media_of(u) and media_of(u).get("api")):
            continue
        seen.add(u); uniq.append(u)
    return uniq

def izm_싱글리뷰(album):
    # 이 앨범 곡의 이즘 싱글 리뷰
    out, seen = [], set()
    for c in (album.get("수록곡") or [])[:6]:
        j = api_get(f"{IZM_API}/content/search/", {"keyword": c})
        for it in ((j or {}).get("contents") or {}).get("single_review_contents") or []:
            if it.get("id") in seen:
                continue
            kr, en = izm_이름(it)
            if not any(같은이름(n, a, 느슨=True) for n in 이름들(album) for a in (kr, en) if a):
                continue
            if not 같은이름(it.get("title"), c, 느슨=True):
                continue
            seen.add(it["id"])
            pg = izm_page(it)
            pg["제목"] = f"{kr or en} - {it.get('title')} (이즘 싱글 리뷰)"
            out.append(pg)
    return out

def n_재료보강(st: RunState) -> RunState:
    st.보강됨 = True
    a = st.album
    있던 = {p["url"] for p in st.pages}
    새페이지 = [p for p in izm_싱글리뷰(a) if p["url"] not in 있던]
    링크 = [u for u in 보강_링크(a, st.중심곡) if u not in 있던]
    읽음, 막힘 = read_pages(링크, 보강페이지)
    st.blocked += 막힘
    새페이지 += [p for p in 읽음 if p["url"] not in 있던]
    새페이지 = pre_filter(새페이지, a)[:보강페이지]
    if not 새페이지:
        st.log("  보강: 더 찾은 것이 없다")
        return st
    b = {"남은콜": st.남은콜}
    더 = judge_pages(새페이지, a, b, log=st.log)
    st.남은콜 = b["남은콜"]
    st.pages += 새페이지
    st.판정원본 += 더
    st.재료, st.아티스트말 = keep_rules(st.판정원본)
    매체들 = ", ".join(dict.fromkeys(r["매체"] for r in st.재료)) or "-"
    st.log(f"  보강: 페이지 {len(새페이지)}장 더 읽음 → 평론 {len(st.재료)} ({매체들}) / 곡메모 {sum(len(r['곡메모']) for r in st.재료)} / 아티스트 말 {sum(len(m['말']) for m in st.아티스트말)}")
    return st

def n_관점묶기(st: RunState) -> RunState:
    st.관점표 = views_text(group_views(st.재료))
    w = wiki_context(st.album)
    if w:
        st.맥락 = (w["본문"] + ("\n\n[이즘 아티스트 소개]\n" + st.맥락 if st.맥락 else ""))
        st.맥락링크 = [w["링크"]] + st.맥락링크
    st.맥락링크 += [m["링크"] for m in st.아티스트말 if m["링크"] not in st.맥락링크]
    return st

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    st.주문 = 기획주문(앨범정보=album_text(st.album), 관점표=st.관점표, 맥락=st.맥락[:5000],
                     발췌=발췌_text(st.재료), 아티스트말=말_text(st.아티스트말),
                     접근=random.choice(접근들), 온도=random.choice(온도들),
                     시작점=random.choice(시작점들), 배치후보=" / ".join(배치들),
                     중심곡=st.중심곡 or "",
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
        if o.배치 not in 배치들:                       # 이름을 틀리게 적었으면 무작위로
            o.배치 = random.choice([b for b in 배치들 if b != 곡순서])
        if o.배치 != 곡순서 and len(o.곡셋) > 3:
            st.log(f"  기획자가 곡을 {len(o.곡셋)}개 골랐다. 셋으로 자른다")
            o.곡셋 = o.곡셋[:3]
        if o.배치 == 곡순서 and not o.곡흐름:
            st.log("  곡 순서 배치인데 곡 흐름이 없다. 다른 배치로 바꾼다")
            o.배치 = random.choice([b for b in 배치들 if b != 곡순서])
            o.곡셋 = o.곡셋[:3]
        if st.중심곡 and not any(같은이름(c, st.중심곡, 느슨=True) for c in o.곡셋):
            o.곡셋 = [st.중심곡] + o.곡셋[:2]
        st.log(f"  배치: {o.배치} / 곡 {len(o.곡셋)}개 ({', '.join(o.곡셋[:5])}) / 사실 {len(o.사실목록)}개" + (f" / 곡 흐름 {len(o.곡흐름)}개" if o.곡흐름 else ""))
        if len(o.사실목록) < 8:
            st.log("  사실목록이 여덟 개도 안 된다. 글이 빌 것이다")
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
    # 먼저 형태 검사를 해 본다. 고칠 것이 하나도 없으면 교정자를 안 부른다.
    # 형태 검사는 규칙이라 공짜고, 교정자는 글 전체를 다시 써서 30초에서 60초가 든다
    미리 = 형태검사(st.글.본문, st.재료, st.맥락, st.album,
                   배치=st.개요_.배치 if st.개요_ else "",
                   곡셋=st.개요_.곡셋 if st.개요_ else None, 중심곡=st.중심곡)
    치명, 고칠것, _ = 형태무게(미리)
    if not 치명 and not 고칠것:
        st.고친곳 = []
        st.log("  교정자를 건너뛴다. 형태 검사에 걸린 것이 없다")
        return st
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
    st.걸림 = 형태검사(st.글.본문, st.재료, st.맥락, st.album, 배치=st.개요_.배치 if st.개요_ else "",
                       곡셋=st.개요_.곡셋 if st.개요_ else None, 중심곡=st.중심곡)
    return st

def n_편집국장(st: RunState) -> RunState:
    b = {"남은콜": st.남은콜}
    st.판정 = 편집국장_읽기(st.글, st.개요_, st.재료, st.관점표, st.맥락, st.album, b, 아티스트말=st.아티스트말)
    st.남은콜 = b["남은콜"]
    st.올림, st.이유, st.무게상세 = 올릴까(st.걸림, st.판정, 바퀴=st.바퀴, album=st.album)
    문제수 = len(st.판정.문제들) if st.판정 else 0
    d = st.무게상세
    무게줄 = (f" / 치명 {len(d.get('형태치명', [])) + len(d.get('편집치명', []))}"
             f" · 고칠것 {len(d.get('형태고칠것', [])) + len(d.get('편집고칠것', []))}"
             f" · 사소 {len(d.get('형태사소', [])) + len(d.get('편집사소', []))}") if d else ""
    st.기록.append({"차례": st.바퀴, "개요": st.개요_, "글": st.글, "고친곳": st.고친곳,
                   "걸림": st.걸림, "판정": st.판정, "올림": st.올림, "이유": st.이유, "무게": d,
                   "조건": (st.주문.접근, st.주문.온도, st.주문.시작점, st.개요_.배치 if st.개요_ else "")})
    st.log(f"  {st.바퀴}차: {'올림' if st.올림 else '탈락'} / {st.이유} / 교정 {len(st.고친곳)}곳 / 편집국장 문제 {문제수}건{무게줄}")
    if not st.올림:
        st.지난문제 = 문제목록_글(st.걸림, st.판정, st.무게상세)   # 치명과 고칠 것만 들고 다음 바퀴로
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
    # 모자라면 한 번은 더 찾는다. 평이 셋 미만이거나, 중심 곡 이야기가 없거나, 곡메모가 하나도 없을 때
    if not st.보강됨 and st.남은콜 >= 보강페이지 + 4:
        곡메모수 = sum(len(r["곡메모"]) for r in st.재료)
        if len(st.재료) < 보강목표 or 곡메모수 == 0 or (st.중심곡 and not 곡_언급됐나(st.재료, st.중심곡)):
            st.log("  재료가 얇다. 곡 이름 · 인터뷰 · 싱글 리뷰로 더 찾는다")
            return "재료보강"
    if len(st.재료) < 최소 and not 맥락만쓰기:
        st.log("  평이 모자란다. 이 앨범은 버리고 다음 후보로 간다")
        return "앨범뽑기"
    return "관점묶기"

def after_기획자(st):
    if st.개요_ is None:
        # 호출이 실패한 것이지 앨범이 나쁜 것이 아니다. 한 번 더 부른다
        if st.기획재시도한 < 기획재시도 and st.남은콜 >= 4:
            st.기획재시도한 += 1
            st.바퀴 -= 1                     # 다시 부르는 것은 바퀴로 세지 않는다
            st.log(f"  기획을 다시 부른다 ({st.기획재시도한}/{기획재시도})")
            return "기획자"
        st.log(f"  {st.이유}")
        st.상태 = "탈락"
        return END
    return "작가"

def after_작가(st):
    if st.글 is None:
        if st.기획재시도한 < 기획재시도 and st.남은콜 >= 3:
            st.기획재시도한 += 1
            st.log(f"  집필을 다시 부른다 ({st.기획재시도한}/{기획재시도})")
            return "작가"
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
    이름 = re.sub(r"[^\w가-힣ㄱ-ㅎ -]", "", f"{a.get('별칭', {}).get('한글') or a['artist']} - {a['title']}")[:60].strip()
    stamp = datetime.datetime.now().strftime("%m%d_%H%M%S")
    path = 폴더 / f"{이름}_{stamp}.md"

    출처줄 = [f"- {x['매체']} — {x['링크']}" for x in st.재료]
    출처줄 += [f"- (아티스트 말) {m['매체']} — {m['링크']}" for m in st.아티스트말]
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
        d = k.get("무게") or {}
        if d:
            로그.append("- 무게: " + " / ".join(
                f"{이름} {len(d.get(칸, []))}건" for 이름, 칸 in
                (("형태 치명", "형태치명"), ("형태 고칠것", "형태고칠것"), ("형태 사소", "형태사소"),
                 ("사실 치명", "편집치명"), ("문장 고칠것", "편집고칠것"), ("사실 사소", "편집사소"))))
            for 이름, 칸 in (("치명", "형태치명"), ("치명", "편집치명")):
                로그 += [f"  - ({이름}) {str(x)[:90]}" for x in d.get(칸, [])]

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
# - 관점 묶는 문턱값 0.62, `PASS_SCORE` 65, `마지막안전점수` 50, `시도상한` 24 — 돌려보고 맞춘다
# - `JEV_확신문턱` 0.60, `모델판정상한` 7 — 돌려보고 맞춘다. Jev가 너무 많이 버리면 문턱을 올린다
# - `형태_치명` · `형태_고칠것` 목록 — 어느 것이 글을 정말 막는지는 돌려보고 옮긴다
# - 교정자의 번역투 일곱 — 빠진 것이 있으면 16번에 더 넣는다
#
# 랭그래프로 옮길 때: 마디 함수는 `add_node`에, 갈림길 함수는 `add_conditional_edges`에 그대로 꽂는다. `RunState`가 상태 스키마다.


# ======================================================================
# 실행
#   python music_review_agent_v3.3.py check         # 점검 — 이즘 · 아이돌로지 API, 검색 엔진, 해외 매체, RSS, Jev. 큰 모델은 안 부른다
#   python music_review_agent_v3.3.py pick [--kr|--world] [--n 3]   # 앨범 뽑기 + 재료 모으기까지만
#   python music_review_agent_v3.3.py run [--n 5]   # 돌리기 — 올린 글이 n편 될 때까지
#   python music_review_agent_v3.3.py one 우즈 OO-LI --song Drowning   # 앨범 지정. 곡을 주면 그 곡이 가운데
#   python music_review_agent_v3.3.py zip           # out/music 폴더를 zip으로
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
    print("\n[Jev]")
    jev_점검()


def jev_점검():
    # 키가 있는지, 실제로 답이 오는지 본다. 짧은 글 하나로 물어본다.
    if not JEV_ON:
        print("  JEV_ON이 False다. 안 쓴다")
        return
    if not _jev_키():
        print(f"  키가 없다. {' · '.join(JEV_키이름들)} 중 아무 이름으로나 넣으면 된다")
        print("  없어도 에이전트는 돈다. 큰 모델이 다 하고 그만큼 느리다")
        return
    시작 = time.time()
    답 = jev_묻기("이 글은 어느 앨범을 놓고 쓴 리뷰다. 여는 곡의 기타 소리가 거칠어졌고 가사가 전보다 짧아졌다고 적었다. 글쓴이는 앞 앨범보다 낫다고 본다.",
                 {"다루는정도": 고르기("이 글이 앨범을 어떻게 다루는가", _다루는보기),
                  "평가있음": 예아니오("글쓴이가 좋다 · 나쁘다 같은 판단을 적었다"),
                  "값어치": 점수("이 글을 앨범 리뷰의 재료로 쓸 값이 얼마나 되는가", _값어치단계)})
    걸린 = int((time.time() - 시작) * 1000)
    if 답 is None:
        print("  못 불렀다. 위 실패 줄을 본다. 에이전트는 Jev 없이 돈다")
        return
    print(f"  답 옴 · {걸린}ms")
    for k, (v, c) in 답.items():
        print(f"    {k}: {v}  (확신 {c:.2f})")
    print("  기대값 — 다루는정도 '앨범평', 평가있음 참, 값어치 3 이상")
    print(f"  {jev_현황()}")


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

def _한편(want_kr, 새루프=False):
    # 앨범 한 편을 끝까지 돈다. 스레드에서 부를 때는 그 스레드만의 루프를 먼저 깔아 둔다.
    # 루프 없이 들어가면 모델 호출이 "no running event loop"로 실패한다
    if 새루프:
        asyncio.set_event_loop(asyncio.new_event_loop())
    return run_graph(RunState(want_kr=want_kr))


def run(목표편수=목표편수, 시도상한=시도상한, 쪽만=None, 동시=None):
    동시 = max(1, int(동시 or 동시앨범))
    한국편수 = int(목표편수 * KR_RATIO + 0.5)        # 5편이면 한국 3 · 해외 2
    남은 = {"KR": 한국편수, "기타": 목표편수 - 한국편수}
    if 쪽만 == "KR":
        남은 = {"KR": 목표편수, "기타": 0}
    elif 쪽만 == "기타":
        남은 = {"KR": 0, "기타": 목표편수}
    결과, 시도 = [], 0
    시작 = time.time()
    print(f"  {jev_현황() if jev_켜짐() else 'Jev 안 씀 — 큰 모델이 판정까지 다 한다'}"
          f" · 앨범을 {동시}편씩 돈다")

    while sum(남은.values()) > 0 and 시도 < 시도상한:
        # 이번에 같이 돌릴 편들을 고른다. 남은 편수와 시도 상한을 넘지 않는다
        쪽들, 남은복사 = [], dict(남은)
        for _ in range(min(동시, sum(남은.values()), 시도상한 - 시도)):
            고를수있는 = [k for k, v in 남은복사.items() if v > 0]
            if not 고를수있는:
                break
            k = random.choice(고를수있는)
            남은복사[k] -= 1
            쪽들.append(k == "KR")
        if not 쪽들:
            break
        시도 += len(쪽들)
        print(f"\n===== 시도 {시도}/{시도상한} · 남은 한국 {남은['KR']} · 해외 {남은['기타']}"
              f" · {int((time.time()-시작)/60)}분" + (f" · 이번에 {len(쪽들)}편 같이" if len(쪽들) > 1 else "") + " =====")
        if len(쪽들) == 1:
            묶음 = [_한편(쪽들[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(쪽들)) as ex:
                묶음 = list(ex.map(lambda w: _한편(w, 새루프=True), 쪽들))
        for want_kr, st in zip(쪽들, 묶음):
            결과.append(st)
            if st.상태 == "앨범없음":
                print("쓸 만한 앨범을 못 찾았다")
            if st.올림:
                남은["KR" if want_kr else "기타"] -= 1

    올림수 = sum(1 for s in 결과 if s.올림)
    print(f"\n끝. 올림 {올림수}편 / 탈락 {sum(1 for s in 결과 if s.상태 == '탈락')}편 / 시도 {시도}회 / {int((time.time()-시작)/60)}분")
    print("  " + jev_현황())
    떨어진곳 = {}
    for s in 결과:
        if s.올림:
            continue
        칸 = s.상태 if s.상태 != "탈락" else (s.이유.split(":")[0].split("(")[0].strip() or "탈락")
        떨어진곳[칸] = 떨어진곳.get(칸, 0) + 1
    if 떨어진곳:
        print("  떨어진 곳: " + ", ".join(f"{k} {v}건" for k, v in sorted(떨어진곳.items(), key=lambda x: -x[1])))
    if 올림수 < 목표편수:
        print(f"목표 {목표편수}편에 {목표편수 - 올림수}편 모자란다.")
    return 결과


# ## 26-1. 앨범을 지정해서 한 편
#
# 무작위로 안 뽑고 사람이 고른 앨범으로 쓴다. 유명도는 안 본다. 곡을 주면 그 곡을 글의 가운데에 둔다.
# 이즘에서 아티스트를 찾아 한글 · 영문 이름을 채운다. MusicBrainz에 없으면 매체 정보로 간다.

def run_one(artist, title, 곡=None, artist_en=None, want_kr=True):
    kr, en = artist, artist_en
    _, arts = izm_검색(artist)
    for a in arts:
        if any(같은이름(artist, x, 느슨=True) for x in (a.get("kr_name"), a.get("en_name")) if x):
            kr, en = a.get("kr_name") or kr, a.get("en_name") or en
            break
    cand = {"artist": kr or en, "artist_kr": kr, "artist_en": en, "title": title,
            "src": "지정", "url": None, "page": None, "views": 0, "year": None, "앨범확실": True}
    if 표기_채우기(cand, want_kr):
        print(f"  표기: {cand['artist_kr']} / {cand['artist_en']}  (MusicBrainz 별칭)")
    print(f"\n===== 지정: {cand['artist_kr'] or cand['artist']}{(' (' + cand['artist_en'] + ')') if cand.get('artist_en') and cand['artist_en'] != cand.get('artist_kr') else ''} - {title}" + (f" · 중심 곡 {곡}" if 곡 else "") + " =====")
    st = run_graph(RunState(want_kr=want_kr, 지정후보=cand, 중심곡=곡))
    if st.상태 == "앨범없음":
        print("앨범을 못 잡았다. 이름을 확인한다")
    print(f"\n끝. {st.상태} / {st.이유} / 모델 요청 {LLM_CALL_CAP - st.남은콜}회 · {jev_현황()}")
    return st


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
    # 노트북에서는 sys.argv에 커널 파일 이름이 들어 있다. 그걸 명령으로 읽으면 터진다.
    # 명령이 아니면 무엇을 부르면 되는지 알려 주고 끝낸다
    명령들 = ("check", "pick", "run", "one", "zip")
    if argv is None and (len(sys.argv) < 2 or sys.argv[1] not in 명령들):
        print("노트북에서는 명령줄 대신 함수를 바로 부른다.")
        print("  점검()                          매체 · 검색 엔진 · Jev가 살아 있는지 본다")
        print("  뽑기만(want_kr=True, n=3)       앨범 뽑기와 재료 모으기까지만 본다")
        print("  run(목표편수=5)                 다섯 편이 올라갈 때까지 돈다")
        print('  run_one("우즈", "OO-LI")        앨범을 지정해서 한 편')
        print("  zip_outputs()                   out/music 폴더를 zip으로 묶는다")
        return
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
    a.add_argument("--at-once", type=int, default=동시앨범, dest="at_once", help="앨범을 한 번에 몇 편씩 돌릴지")
    a.add_argument("--kr", action="store_true", help="한국 앨범만")
    a.add_argument("--world", action="store_true", help="해외 앨범만")
    o = sub.add_parser("one", help="앨범을 지정해서 한 편. 예: one 우즈 OO-LI --song Drowning")
    o.add_argument("artist")
    o.add_argument("title")
    o.add_argument("--song", default=None, help="글의 가운데에 둘 곡")
    o.add_argument("--en", default=None, help="아티스트 영문 이름 (이즘에 없을 때)")
    o.add_argument("--world", action="store_true", help="해외 앨범")
    sub.add_parser("zip", help="out/music 폴더를 zip으로 묶는다")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        점검()
    elif args.cmd == "pick":
        뽑기만(want_kr=not args.world, n=args.n)
    elif args.cmd == "run":
        쪽만 = "KR" if args.kr else ("기타" if args.world else None)
        run(목표편수=args.n, 시도상한=args.tries, 쪽만=쪽만, 동시=args.at_once)
        zip_outputs()
    elif args.cmd == "one":
        run_one(args.artist, args.title, 곡=args.song, artist_en=args.en, want_kr=not args.world)
    elif args.cmd == "zip":
        zip_outputs()


if __name__ == "__main__":
    main()
