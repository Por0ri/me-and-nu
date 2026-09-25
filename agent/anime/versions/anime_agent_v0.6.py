# # anime_agent v0.6 — 애니 콘텐츠 크리에이팅 에이전트
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
# | 기념일 — 방영 N주년 · 캐릭터 생일 | 정보 | AniList + 위키백과 ko | (v0.4: run에서 뺐다. 생일 자체에 재료가 없다. run_one으로는 된다) |
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
#
# v0.2에서 바뀐 것 — "1기 다음 2기" 같은 글이 나오던 원인을 고쳤다
# - 정보형 재료에 이야기를 넣는다. 라프텔 회차 목록 API의 회차 제목 · 줄거리(자막 표기 그대로), 위키백과 ko 줄거리 · 등장인물 절, 용어집 편 항목의 "어디서 어디까지".
#   사실표만으로는 "차례로 보면 된다"밖에 안 나왔다.
# - 감상순서는 순서에 갈림이 있는 시리즈만 뽑는다. 표기 있는 본편 밖 작품 둘 이상, 또는 필러 열 화 이상, 또는 편 셋 이상. 프리렌(1기 → 2기 → 3기)은 안 뽑힌다.
# - 정보형은 고유 사실이 열둘 아래거나 이야기가 없으면 글을 안 쓰고 주제를 버린다. "여덟 개 채우라"는 규칙을 뺐다.
# - 기획자 뼈대: 감상순서는 편 · 시즌마다 문단 하나(어디서 어디까지 + 기억할 인물 · 사건). 문단 다섯에서 일곱.
# - 형태검사에 되풀이(문단 간 14자 겹침 · 같은 숫자 다섯 번 · "~하면 된다" 넷) · 재료 옮겨 적기 · 재료 표시("←" · "[") 검사.
# - 필러 0이면 사실표에 줄을 안 넣는다. 라프텔 줄은 링크를 빼고 제목만.
# - 분량은 유형 다섯 다 1800자 기준, 1500~2000자.
#
# v0.3에서 바뀐 것 — "표를 문장으로 옮긴 글"이 나오던 원인을 고쳤다
# - 영화 정보 전달 · 리뷰 에이전트의 프롬프트를 옮겼다. 쓰는 사람 · 문체(토막/이음 · 판단은 끝까지 · 쓰지 않는 말) · 글이 이어지게 쓴다 · 네 판단.
#   정보형도 판단을 쓴다. 근거가 같은 문장에 있으면 된다. "판단 없음"을 못 박았던 네 겹(기획자 · 작가 · 형태검사 판단말 · 편집국장)을 지웠다.
# - 온도 · 시작점 · 배치에 뜻이 붙었다. 기획자 · 작가 · 편집국장이 같은 주문(낱말 + 뜻)을 받는다. 작품리뷰는 접근(분석 · 해석 · 평가)도 받는다.
# - 정보형 기획자는 독자의 질문과 답을 먼저 정한다. 문단의 할 말은 관찰 · 계산 · 주장이다. "X편은 N~M화"는 할 말이 아니다. 편마다 문단 하나가 아니다.
# - 편집국장은 주문대로 갔는지 · 읽을 이유 · 표 옮겨 적기 · 답 문단 되풀이를 본다. 확인 목록(재료 밖이지만 널리 알려진 사실)은 점수를 안 깎고 사람에게 넘긴다.
# - 문장 · 구조 문제면 작가가 앞 글과 문제 목록을 받아 지적된 부분만 고친다. 사실 문제일 때만 기획자부터 다시 간다.
# - 형태검사에 표 옮겨 적기 · 화수 범위 개수 · 편 표 문장 · 회차 줄거리 옮기기 · 극장판 화수 · 볼 화수 셈 · 재료 말투 · 글이 자기를 말함 · 질문/답 · 결말 낱말 · 회차 장면 검사.
# - run 에 유형을 안 주면 유형 다섯을 섞는다.
# - 워크플로(영화 프롬프트 분석 → 설계 셋 → 심사 셋 → 종합)로 만든 프롬프트다. 나루토 재료로 쓴 샘플 글은 versions/sample_v0.3_naruto.md.
#
# v0.4에서 바뀐 것 — "회차 줄거리를 옮기고 판단 한 줄"이던 글에 해석을 넣었다 (PM: 단순 전달만 있고 심층 분석이 없다)
# - 기념일을 run에서 뺐다(유형비율 0). 생일 · 주년 자체에는 장면도 발언도 없어서 첫 문장에 날짜만 붙고 끝났다.
# - 정보형(사람 · 감상순서)도 리뷰 · 인터뷰를 검색해 읽는다(정보형검색). 재료판정관이 장면메모 · 발언 · 남의 해석을 뽑고 관점표로 기획자에게 간다.
#   v0.3 정보형 재료는 회차 줄거리 · 위키뿐이라 "무슨 일이 있었다"만 있고 "왜 · 어떻게"가 없었다.
# - 심층형(제작이야기 · 작품리뷰)도 회차 줄거리 · 위키 줄거리를 받는다. 발언을 화면 어디에 붙일지 장면이 있어야 한다.
# - 기획자 질문은 "왜 · 어떻게"다. "몇 화를 보나 · 무엇부터 보나"는 답이 목록이라 막았다.
#   문단 계획에 해석 칸이 생겼다. 그 장면이 왜 거기 있는지 · 무엇을 미리 심는지 · 인물의 무엇이 드러나는지. 절반 넘게 차야 한다. 개요에 해석들(둘 이상).
# - 작가: 줄거리 문장 뒤에 그 장면이 하는 일이 온다. 문단마다 해석 문장 하나 이상. 편집국장: 줄거리에 판단 한 줄만 붙은 문단은 "구조", 근거 장면이 붙은 해석은 "사실"로 안 잡는다.
# - 제작이야기는 발언이 셋 미만이면 기획자 앞에서 버린다(심층최소발언). 장면메모 0 · 발언 7로 세 바퀴를 다 돌고 떨어지던 것.
# - 형태검사에 같은 종류로 두 바퀴 연속 걸리면 세 번째를 안 돈다(같은걸림중단). 1차 · 2차 · 3차가 같은 자리(화수 되풀이)에서 걸리던 것.
#
# v0.5에서 바뀐 것 — 여섯 편에 77분 걸리던 것을 동시에 돌린다 (PM: 통과율 · 글쓰기 · 판정 전부 같이 돌려도 된다)
# - run(동시=N): 주제 N개를 스레드로 같이 돈다. 음악 v3.3의 동시앨범과 같다. 스레드마다 이벤트 루프 · 모델 · HTTP 클라이언트를 따로 둔다.
#   같은 주제 · 같은 시리즈를 겹쳐 잡지 않게 주제뽑기 · 저장에 자물쇠. 로그 앞에 [유형 번호]가 붙는다.
# - 첫 바퀴에 작가가 초안 셋을 온도를 달리해 동시에 쓴다(초안수). 형태검사(코드)로 거르고, 남은 것만 편집국장이 동시에 읽는다. 올림 · 점수 순으로 하나를 고른다.
#   세 바퀴 직렬(작가 → 떨어짐 → 다시)이 한 바퀴 병렬이 된다. 다시 쓰기는 한 번(REWRITE_LIMIT 2).
# - 교정자를 작가에 합쳤다(교정자쓰기 False). 교정 규칙이 작가 프롬프트 끝에 붙는다. 바퀴마다 호출 하나가 준다.
# - 재료 모으기에서 호스트가 다른 것은 같이 받는다. 검색 페이지 읽기 ∥ 위키 평가 절 ko · en ∥ 위키 작품 문서 ∥ 라프텔 회차. 감상순서는 Jikan ∥ 라프텔 ∥ 위키.
#   같은 호스트는 그대로 줄을 선다(호스트별 간격 자물쇠). AniList 2초 · 라프텔 · 위키 1초.
# - 캐시 파일은 임시 파일에 쓰고 바꿔 넣는다. 두 스레드가 같은 파일을 동시에 쓰던 것.
# - 회차 줄거리 한 줄을 140자에서 300자로 늘렸다(줄거리글자). 콜랩에서는 run · run_one이 끝나면 zip으로 묶어 내려준다(콜랩_끝나면zip).
#
# v0.6에서 바뀐 것 — 편집국장이 앞 판을 기억한다 (음악 v3.4와 같은 방식. 영화 정보 V2.4 · 영화 리뷰 V1.9도 같다)
# - 편집국장이 앞 판 판정(점수 · 문제 목록 · 형태검사 걸림 · 한줄평)과 앞 판 글 본문을 받는다(앞판기억).
#   전에는 판마다 처음 보는 것처럼 채점해서 같은 글을 고쳐도 점수가 흔들렸다. 올랐는지 내렸는지 판단할 근거가 없었다.
# - [앞 판 글]과 [이번 글]을 나란히 준다. 이번 글 머리에 판 종류를 적는다.
#   첫 판 / 고침(문장 · 구조 문제 → 작가가 앞 글을 받아 손본 것) / 새로 씀(사실 문제 → 기획자부터 다시 쓴 것).
# - 편집국장 출력에 앞판대조(앞 문제마다 고쳐짐 / 남음 / 새로 생김 / 앞 판에서 못 봄)와 점수설명 한 줄이 붙는다.
#   점수는 앞 점수에서 출발한다. 고쳐진 게 있고 새 문제가 없으면 앞 점수 아래로 안 내린다. 내리면 점수설명에 왜인지 적는다.
#   "못 봄"은 앞 판 글에도 있었는데 앞 판이 안 잡은 것이다. 글이 나빠진 것(새로 생김)과 가른다. 못 봄만으로는 점수를 안 내린다.
# - 기록마다 판 종류를 남긴다. 저장 파일의 판정 기록에 판마다 점수 · 앞 판 대비 · 대조 줄이 남는다.
# - 마지막 판이 탈락이면 기록 중 점수가 제일 높은 판(최고 판)의 글을 저장한다(최고판저장). 다시 쓰다 나빠진 글이 남지 않게 한다.
# - (9/22 밤) `_새모델()`을 OpenAIChatModel → OpenAIResponsesModel로. `"openai:..."` 문자열은 Responses API로 가는데 스레드용 모델만
#   chat completions로 만들어서, run(동시=N)에서 gpt-5.6-luna가 "Function tools with reasoning_effort ... /v1/chat/completions" 400을 냈다.
#   동시=1이면 안 나던 이유다. 실제 호출 A(문자열) · C(Responses) 됨, D(_새모델 chat) 실패로 확인했다.

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
목표편수      = 6      # 올린 글이 이만큼 될 때까지 돈다 (PM: 최소 여섯)
유형비율      = {"감상순서": 1, "사람": 1, "기념일": 0, "제작이야기": 1, "작품리뷰": 1}   # run 에 유형을 안 주면 이 비율로 섞는다. 0이면 안 뽑는다. 기념일은 v0.4에서 뺐다
시도상한      = 18     # 주제를 이만큼 봤는데 못 채우면 멈춘다. 탈락이 섞이니 목표의 세 배
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
REWRITE_LIMIT = 2      # 바퀴 상한. v0.5: 1차는 초안 셋을 동시에, 2차는 앞 글 고치기 (v0.4는 3)
PASS_SCORE    = 70     # 편집국장 점수가 이 아래면 다시 쓴다
분량기준      = 1800   # 글 길이. 유형 다섯 다 같다 (PM 결정 2026-09-21)
분량상한      = 2000   # 이 위는 형태검사에 걸린다
분량하한      = 1500   # 이 아래도 걸린다 (PM 결정)
LLM_CALL_CAP  = 48     # 주제 하나에 허용할 모델 요청 수 (판정 10 + 보강 6 + 기획자 2 + 초안 3 + 편집국장 3 + 고치기 2 + 여유)
ANILIST_GAP   = 2.0    # AniList 요청 사이 간격(초). 분당 30회 제한
JIKAN_GAP     = 1.2    # Jikan 요청 사이 간격(초)
JIKAN_PAGES   = 15     # Jikan 회차 목록을 최대 몇 쪽까지 (100화씩. 원피스 12쪽)
캐시일수      = 7      # AniList · Jikan 응답을 디스크에 이만큼 둔다
표기조회상한  = 30     # 위키백과 ko로 표기를 찾아볼 이름 수 상한 (한 주제)
라프텔조회상한 = 12    # 라프텔로 작품 제목 표기를 찾아볼 작품 수 상한 (한 주제)
필러없어도진행 = True  # Jikan이 죽었을 때 필러 표시 없이 감상순서를 만들지. 본편이 하나뿐인 긴 작품(원피스)은 예외로 멈춘다
최소사실      = 12     # 정보형. 사실표 고유 사실이 이만큼 안 되면 글을 안 쓴다
회차재료글자  = 9000   # 라프텔 회차 줄거리를 기획자에게 주는 상한 (글자)
회차항목상한  = 8      # 라프텔 항목(시즌 · 기)을 이만큼까지만 읽는다
회차쪽수     = 2      # 라프텔 항목 하나에 회차 목록 쪽수 (100화씩)
# v0.4
정보형검색     = True   # 정보형(사람 · 감상순서)도 리뷰 · 인터뷰를 검색해 읽는다. 장면메모 · 발언 · 남의 해석이 재료에 들어간다. False면 v0.3과 같다
정보형페이지   = 8      # 정보형에서 읽어볼 페이지 수. 페이지마다 모델 판정 한 번
심층최소발언   = 3      # 제작이야기. 제작진 발언이 이만큼 안 모이면 그 주제를 버린다
같은걸림중단   = True   # 형태검사에 같은 종류로 두 바퀴 연속 걸리면 세 번째를 안 돌고 탈락시킨다
같은걸림개수   = 3      # 앞 바퀴와 겹치는 걸림 종류가 이만큼이면 "같은 데서 걸렸다"로 본다
줄거리글자     = 300    # 회차 줄거리 한 줄을 이만큼까지 준다 (v0.3은 140)
콜랩_끝나면zip = True   # 콜랩이면 run · run_one 이 끝날 때 out/anime 을 zip 으로 묶어 브라우저로 내려준다. 로컬에서는 아무 일 없다
# v0.5
동시주제       = 3      # run 에서 주제를 몇 개씩 같이 돌릴지. 1이면 v0.4처럼 한 줄로 돈다. AniList · 라프텔 · 위키는 호스트별 간격 자물쇠라 그만큼은 줄을 선다
초안수         = 3      # 첫 바퀴에 작가가 온도를 달리해 동시에 쓰는 초안 수. 형태검사(코드)로 거르고 남은 것만 편집국장이 읽는다. 1이면 v0.4와 같다
교정자쓰기     = False  # True면 v0.4처럼 교정자 마디를 따로 부른다. False면 교정 규칙을 작가 프롬프트 끝에 넣고 마디를 건너뛴다 (바퀴마다 호출 하나가 준다)
스레드별모델   = True   # 동시주제 > 1 이면 스레드마다 모델 · HTTP 클라이언트를 따로 만든다. 한 클라이언트를 여러 이벤트 루프가 나눠 쓰면 "bound to a different event loop"가 난다
# v0.6
앞판기억       = True   # 편집국장이 앞 판 점수 · 문제 목록 · 앞 판 글을 받는다. 문제마다 고쳐짐 / 남음 / 새로 생김 / 못 봄. False면 판마다 처음 보는 것처럼 본다
최고판저장     = True   # 마지막 판이 탈락이면 기록 중 편집국장 점수가 제일 높은 판의 글을 저장한다. False면 마지막 판을 저장한다
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
        if kind != "편" or fixed == series:
            continue
        m = re.match(r"^\s*(?:[가-힣A-Za-z0-9 ]{1,12}\s)?(\d+)\s*~\s*(\d+)\s*화", meaning) or re.match(r"^\s*(?:[가-힣A-Za-z0-9 ]{1,12}\s)?(\d+)\s*화부터", meaning)
        if m:
            a = int(m.group(1)); b = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
            out.append((fixed, a, b, meaning))
    return out            # 용어집에 적힌 순서 그대로. 시리즈마다 화수 번호가 따로 시작해서(나루토 · 질풍전) 숫자로 정렬하지 않는다

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
    # v0.5: 스레드 둘이 같은 파일을 동시에 쓰면 반쪽짜리가 남는다. 임시 파일에 쓰고 바꿔 넣는다
    p = OUT / "_cache" / f"{name}.json"
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
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

def laftel_episodes(item_id, 쪽수=회차쪽수):
    """라프텔 항목의 회차 목록. [{번호, 제목, 줄거리}]. 자막 표기 그대로다. 사람 · 캐릭터 표기의 근거로도 쓴다."""
    key = f"laftel_eps_{item_id}"
    c = _cache_get(key)
    if c is not None:
        return c
    out, offset = [], 0
    for _ in range(쪽수):
        _site_wait(LAFTEL)
        try:
            r = requests.get(f"{LAFTEL}/episodes/v2/list/", params={"item_id": item_id, "sort": "oldest", "offset": offset, "limit": 100},
                             headers={**HEADERS, "Accept": "application/json"}, timeout=15)
            if r.status_code != 200:
                break
            d = r.json()
        except Exception:
            break
        for e in d.get("results", []):
            try:
                n = int(str(e.get("episode_num") or "").strip())
            except ValueError:
                n = None
            out.append({"번호": n, "제목": (e.get("subject") or "").strip(), "줄거리": (e.get("description") or "").strip()})
        if not d.get("next"):
            break
        offset += 100
    _cache_put(key, out)
    return out

def _라프텔이름정리(name):
    n = re.sub(r"\((자막|더빙)\)|\[.*?\]|\bHD\b", "", name)
    n = re.sub(r"시즌\s*(\d+)", r"\1기", n)
    n = re.sub(r"[Pp]art\s*(\d+)|파트\s*(\d+)", lambda m: "파트" + (m.group(1) or m.group(2)), n)
    n = re.sub(r"[Tt]he\s+FINAL|[Tt]he\s+[Ff]inal|파이널\s*시즌|FINAL|Final", "파이널", n)
    n = re.sub(r"\(.*?\)", "", n)                       # "(완결편 후편)" 같은 꼬리
    return _norm(n)

def 라프텔_항목찾기(이름, items):
    """순서표의 한국 제목과 맞는 라프텔 항목. 자막판을 먼저 고른다. 없으면 None."""
    k = _라프텔이름정리(이름)
    후보 = [it for it in items if not it["더빙"] and not re.search(r"극장판|총집편|스페셜|SPECIAL|OVA", it["이름"])] or items
    for it in 후보:
        if _라프텔이름정리(it["이름"]) == k:
            return it
    # "장송의 프리렌" 처럼 시리즈 이름만인 것은 "1기" 가 붙은 항목과 같다
    for it in 후보:
        if _라프텔이름정리(it["이름"]) in (k + "1기", k):
            return it
    # 한쪽이 다른 쪽을 품으면(“파이널파트1” ⊂ “파이널파트1완결편”) 가장 짧은 것
    품음 = sorted([it for it in 후보 if k in _라프텔이름정리(it["이름"]) and len(k) >= 6], key=lambda it: len(it["이름"]))
    return 품음[0] if 품음 else None

def 회차재료_text(eps, 이름, 상한=회차재료글자):
    """회차 제목은 다 주고 줄거리는 처음 셋 · 끝 둘 · 그 사이 넷째마다. 글자 상한 안에서."""
    # 회차 제목이 "OO 1화"처럼 자리표시자뿐이고 줄거리도 없으면 재료가 아니다
    eps = [e for e in (eps or []) if e.get("줄거리") or (e.get("제목") and not re.fullmatch(r".*\d+\s*화", e["제목"]))]
    if not eps:
        return ""
    줄 = [f"[{이름} — 회차 제목 · 줄거리. 라프텔 자막 표기. 사건과 이름은 여기 있는 것만]"]
    n = len(eps)
    골라 = set(range(min(3, n))) | set(range(max(0, n - 2), n)) | set(range(3, n, 4))
    for i, e in enumerate(eps):
        head = f"{e['번호']}화 {e['제목']}" if e.get("번호") else e["제목"]
        if i in 골라 and e["줄거리"]:
            줄.append(f"- {head}: {e['줄거리'][:300]}")
        else:
            줄.append(f"- {head}")
    text = "\n".join(줄)
    return text[:상한]

def 라프텔_시리즈(series):
    """시리즈 이름으로 라프텔에 있는 항목들. 한국에서 볼 수 있는 것 목록이 된다.
    라프텔 검색은 이름만 넣으면 몇 개만 준다. "1기" · "극장판" · "시즌"을 붙여 여러 번 찾아 합친다."""
    key = f"laftel_{_norm(series)}"
    c = _cache_get(key)
    if c is not None:
        return c
    seen, items = set(), []
    for q in (series, f"{series} 1기", f"{series} 2기", f"{series} 극장판", f"{series} 시즌", f"{series} 편"):
        for x in laftel_search(q, n=20):
            if x["id"] in seen:
                continue                                  # 19세 등급(진격의 거인)도 받는다. 타겟이 20~60대다
            # 검색어와 상관없는 것이 섞여 온다. 시리즈 이름의 앞 두 글자가 들어 있는 것만
            if _norm(series)[:2] not in _norm(x["이름"]):
                continue
            seen.add(x["id"]); items.append(x)
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
        text = "\n".join(l for l in text.splitlines() if l.strip() not in ("[", "]", "|", "편집", "원본 편집"))   # 위키 화면의 편집 단추가 섞여 온다
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

줄거리절제목 = ["줄거리", "개요", "이야기", "스토리", "배경"]

def 위키_절들(작품, series=None):
    """ko 작품 문서의 줄거리 절과 등장인물 절. {'줄거리': 글, '등장인물': 글, '링크': url}. 없으면 빈 값."""
    out = {"줄거리": "", "등장인물": "", "링크": None}
    for q in [작품.get("한국제목"), series]:
        if not q:
            continue
        for title, snip in _wiki_search(q, "ko", 4):
            if not (애니낱말.search(snip) or 애니낱말.search(title)):
                continue
            줄 = _wiki_section("ko", title, 줄거리절제목, n=5000)
            인 = _wiki_section("ko", title, 등장인물절제목, n=5000)
            if not 인:
                # 등장인물이 따로 목록 문서로 있을 때
                for t2, sn2 in _wiki_search(f"{q}의 등장인물 목록", "ko", 3):
                    if "등장인물" in t2 and _norm(q)[:2] in _norm(t2):
                        w2 = _wiki_extract("ko", t2, n=5000)
                        if w2:
                            인 = w2["본문"]; break
            if 줄 or 인:
                # 결말이 되는 문장은 기획자에게 주기 전에 뺀다
                인 = re.sub(r"[^.\n]*(?:에게 죽|사망|살해|목숨을 잃)[^.\n]*[.\n]", "", 인 or "")
                out.update({"줄거리": 줄 or "", "등장인물": 인 or "", "링크": f"https://ko.wikipedia.org/wiki/{urllib.parse.quote(title)}"})
                return out
    return out

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
    elif 유형 == "사람":
        # v0.4 정보형. 성우 · 제작진 인터뷰와 배역 이야기
        p = 주제.get("사람") or {}
        표기 = 용어집_찾기(p.get("full") or "", 주제["시리즈"]) or 용어집_찾기(p.get("native") or "", 주제["시리즈"])
        배역 = 용어집_찾기(p.get("배역") or "", 주제["시리즈"]) or 용어집_찾기(p.get("배역native") or "", 주제["시리즈"])
        if p.get("full") and (en or ro):
            말.append(f"{p['full']} interview {en or ro}")
        if p.get("native") and ja:
            말.append(f"{p['native']} インタビュー {ja}")
        if 표기:
            말 += [f"{표기} 인터뷰 {ko}", f"{표기} {배역 or ''} 성우 인터뷰".replace("  ", " ")]
        말 += [f"{ko} 성우 인터뷰", f"{en or ro} voice actor interview"]
    elif 유형 == "감상순서":
        # v0.4 정보형. 회차 · 편 리뷰에서 장면과 해석을 얻는다
        말 += [f"{ko} 애니 리뷰", f"{ko} 회차 리뷰", f"{ko} 편 평가 필러", f"{en or ro} anime review", f"{en or ro} episode review arc",
               f"site:animenewsnetwork.com {ro} review", f"{ja} アニメ 感想 考察"]
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
_주제잠금 = threading.Lock()     # v0.5: 동시에 돌 때 _쓴주제 · _올린시리즈 · _진행시리즈를 지킨다
_진행시리즈 = {}                 # v0.5: 지금 돌고 있는 주제의 시리즈. 작품당상한을 올린 것 + 진행 중으로 센다

def _진행놓기(st):
    """이 상태가 잡고 있던 시리즈 진행 수를 놓는다. 여러 번 불러도 한 번만 놓는다."""
    if not getattr(st, "진행잡음", False) or not st.주제:
        return
    with _주제잠금:
        s = st.주제["시리즈"]
        if _진행시리즈.get(s, 0) > 0:
            _진행시리즈[s] -= 1
    st.진행잡음 = False
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
    배역표기_ = 표.사람(p["배역"], p.get("배역native"))["표기"] if p.get("배역") else None
    배역뜻 = 용어집_뜻(st.주제["시리즈"], 배역표기_) if 배역표기_ else ""
    사실 = [f"[사람] {이름['표기']} — {p.get('역할')}  (표기 출처: {이름['출처']})",
            f"- 하는 일: {직업}  ← AniList",
            (f"- 태어난 해: {dob['year']}년  ← AniList" if dob.get("year") else "- 태어난 해: 모름"),
            f"- 이 시리즈에서: {st.주제['시리즈']}" + (f" {배역표기_ or ''} 역" if p.get("배역") else "") + (f" — {배역뜻}  ← 용어집" if 배역뜻 else ""),
            "", f"[참여작 — 한국 표기가 있는 것만. {len(작품수)}개]"] + 줄[:30]
    # 배역이 나오는 회차 — 라프텔 회차 줄거리
    이야기 = []
    if 배역표기_:
        라 = 시리즈(st.주제["시리즈"])["라프텔"]
        it = 라프텔_항목찾기(st.주제["시리즈"], 라)
        eps = laftel_episodes(it["id"]) if it else []
        줄들 = 이야기_줄(eps, 배역표기_)
        if 줄들:
            이야기.append(f"[{배역표기_}이(가) 나오는 회차 — 라프텔 회차 줄거리. 사건은 여기 있는 것만]\n" + "\n".join(줄들))
            st.맥락링크.append(it["링크"])
    절 = 위키_절들({"한국제목": st.주제["시리즈"]}, st.주제["시리즈"])
    if 절["등장인물"] and 배역표기_ and 배역표기_.split()[-1] in 절["등장인물"]:
        이야기.append("[등장인물 — 위키백과 ko]\n" + 절["등장인물"][:2000])
        if 절["링크"]:
            st.맥락링크.append(절["링크"])
    st.이야기 = "\n\n".join(이야기)
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
        사실 += ["", "[라프텔에 있는 것 — 한국에서 볼 수 있다]"] + [f"  - {x['이름']}{' (더빙)' if x['더빙'] else ''}" for x in 라[:8]]
    wk = 위키_작품문서({"한국제목": 이름}, st.주제["시리즈"])
    if wk:
        st.맥락 = wk["본문"][:4000]; st.맥락링크.append(wk["링크"])
        rec = 위키_평가절({"한국제목": 이름}, st.주제["시리즈"], "ko")
        if rec:
            사실 += ["", "[당시 반응 — 위키백과 ko 평가 절. 여기 적힌 것만 반응으로 쓴다]", rec["본문"][:2500]]
            st.맥락링크.append(rec["url"])
    이야기 = []
    라 = s["라프텔"]
    it = 라프텔_항목찾기(이름, 라) or 라프텔_항목찾기(st.주제["시리즈"], 라)
    eps = laftel_episodes(it["id"]) if it else []
    if k["종류"] == "생일":
        ct = 표.사람(k["캐릭터"]["full"], k["캐릭터"].get("native"))
        뜻 = 용어집_뜻(st.주제["시리즈"], ct["표기"])
        if 뜻:
            사실.insert(1, f"- {ct['표기']}: {뜻}  ← 용어집")
        줄들 = 이야기_줄(eps, ct["표기"])
        if 줄들:
            이야기.append(f"[{ct['표기']}이(가) 나오는 회차 — 라프텔 회차 줄거리. 사건은 여기 있는 것만]\n" + "\n".join(줄들))
    elif eps:
        이야기.append(회차재료_text(eps, 이름, 상한=4000))
    if it:
        st.맥락링크.append(it["링크"])
    절 = 위키_절들({"한국제목": 이름}, st.주제["시리즈"])
    if 절["줄거리"]:
        이야기.append("[줄거리 — 위키백과 ko]\n" + 절["줄거리"][:3000])
    if 절["등장인물"]:
        이야기.append("[등장인물 — 위키백과 ko. 이름 표기는 표기표를 따른다]\n" + 절["등장인물"][:2000])
    if 절["링크"]:
        st.맥락링크.append(절["링크"])
    st.이야기 = "\n\n".join(이야기)
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
        if any(r["이름"] == 이름 for r in 줄):
            continue                                    # 라프텔 묶음 이름("극장판 블리치 1~4기")이 여러 작품에 붙을 수 있다
        줄.append({"번호": len(줄) + 1, "이름": 이름, "갈래": 갈래, "연도": (w.get("start") or {}).get("y"), "화수": w.get("episodes"), "idMal": w.get("idMal"), "한줄": 한줄})
    return 줄, 뺀것, 본편수

def 용어집_뜻(series, 이름):
    g = GLOSSARIES.get(series)
    for fixed, wrong, alias, kind, meaning in (g["항목"] if g else []):
        if fixed == 이름:
            return meaning
    return ""

def 이야기_줄(eps, 이름, 최대=8):
    """회차 줄거리 가운데 이 이름이 나오는 줄. 캐릭터 · 배역이 어느 회차에 무엇을 했는지."""
    if not 이름 or not eps:
        return []
    성 = 이름.split()[-1] if " " in 이름 else 이름
    out = []
    for e in eps:
        if 성 in e["줄거리"] or 성 in e["제목"]:
            out.append(f"- {e['번호']}화 {e['제목']}: {e['줄거리'][:줄거리글자]}")
        if len(out) >= 최대:
            break
    return out

def 이야기_모으기(st, 이름):
    """작품의 회차 줄거리(라프텔)와 위키 줄거리 · 등장인물 절. v0.4: 심층형도 이것을 받는다. 발언을 붙일 장면이 있어야 한다."""
    s = 시리즈(st.주제["시리즈"])
    이야기 = []
    it = 라프텔_항목찾기(이름, s["라프텔"]) or 라프텔_항목찾기(st.주제["시리즈"], s["라프텔"])
    eps = laftel_episodes(it["id"]) if it else []
    if eps:
        글 = 회차재료_text(eps, 이름, 상한=4000)
        if 글:
            이야기.append(글)
            if it["링크"] not in st.맥락링크:
                st.맥락링크.append(it["링크"])
    절 = 위키_절들({"한국제목": 이름}, st.주제["시리즈"])
    if 절["줄거리"]:
        이야기.append("[줄거리 — 위키백과 ko]\n" + 절["줄거리"][:3000])
    if 절["등장인물"]:
        이야기.append("[등장인물 — 위키백과 ko. 이름 표기는 표기표를 따른다]\n" + 절["등장인물"][:2000])
    if 절["링크"] and 절["링크"] not in st.맥락링크:
        st.맥락링크.append(절["링크"])
    return "\n\n".join(이야기)

def _감상순서_회차(순서, 라):
    """라프텔 회차 줄거리(본편마다). [(글, 링크)]. v0.5: Jikan · 위키와 같이 받으려고 떼어냈다. st는 안 건드린다."""
    out = []
    본편들 = [r for r in 순서 if r["갈래"] == "본편"]
    for r in 본편들[:회차항목상한]:
        it = 라프텔_항목찾기(r["이름"], 라)
        if not it:
            continue
        eps = laftel_episodes(it["id"])
        if eps:
            out.append((회차재료_text(eps, r["이름"], 상한=회차재료글자 // max(1, min(len(본편들), 회차항목상한))), it["링크"]))
    if len(본편들) == 1 and len(라) > 1:                      # 원피스처럼 라프텔이 "N기"로 나눈 것. 고르게 몇 개만
        기들 = sorted([x for x in 라 if not x["더빙"] and re.search(r"\d+기", x["이름"]) and "극장판" not in x["이름"]],
                     key=lambda x: int(re.search(r"(\d+)기", x["이름"]).group(1)))
        step = max(1, len(기들) // 회차항목상한)
        for it in 기들[::step][:회차항목상한]:
            eps = laftel_episodes(it["id"], 쪽수=1)
            if eps:
                out.append((회차재료_text(eps, it["이름"], 상한=회차재료글자 // 회차항목상한), None))
    return out

def 재료_감상순서(st):
    s, 표 = 시리즈(st.주제["시리즈"]), st.용어표
    순서, 뺀것, 본편수 = 순서표_만들기(st)
    편 = 용어집_편목록(st.주제["시리즈"])
    본편밖 = [r for r in 순서 if r["갈래"] != "본편"]
    # v0.5: 라프텔 회차 · 위키는 호스트가 달라 Jikan과 같이 받는다. 갈림이 없어 버리게 돼도 캐시에는 남는다
    ex = ThreadPoolExecutor(max_workers=3)
    f_회차 = ex.submit(_감상순서_회차, 순서, s["라프텔"])
    f_절 = ex.submit(위키_절들, {"한국제목": st.주제["시리즈"]}, st.주제["시리즈"])
    f_wk = ex.submit(위키_작품문서, {"한국제목": st.주제["시리즈"]}, st.주제["시리즈"])
    필러, 죽음 = {}, False
    for r in 순서:
        if r["갈래"] == "본편" and r.get("idMal"):
            eps = jikan_episodes(r["idMal"])
            if eps is None:
                st.log(f"  Jikan 회차 못 받음: {r['이름']} (mal {r['idMal']})")
                죽음 = True; continue
            필러[r["이름"]] = ([e["번호"] for e in eps if e["필러"]], [e["번호"] for e in eps if e["총집편"]], len(eps))
    필러수 = sum(len(f) for f, _, _ in 필러.values())
    # 순서에 갈림이 있어야 글이 된다. 1기 → 2기 → 3기뿐이면 쓸 것이 없다
    if len(본편밖) < 2 and 필러수 < 10 and len(편) < 3:
        st.이유 = f"순서에 갈림이 없다. 본편 밖 {len(본편밖)}개 · 필러 {필러수}화 · 편 {len(편)}개"; ex.shutdown(wait=False); return False
    if 죽음 and (본편수 <= 1 or not 필러없어도진행):
        st.이유 = "Jikan이 죽어서 필러 표시를 못 받았다. 본편이 하나뿐인 작품은 필러 없이 못 만든다"; ex.shutdown(wait=False); return False
    사실 = ["[감상 순서 — 코드가 관계도로 계산했다. 이 순서와 갈래를 바꾸지 않는다]"]
    for r in 순서:
        뜻 = 용어집_뜻(st.주제["시리즈"], r["이름"].replace(st.주제["시리즈"] + " ", "", 1)) or 용어집_뜻(st.주제["시리즈"], r["이름"])
        사실.append(f"{r['번호']}. [{r['갈래']}] {r['한줄']}" + (f" — {뜻}" if 뜻 else "") + "  ← AniList")
    if 편:
        사실 += ["", "[편 — 용어집. 본편 한 덩어리 안에서 편 순서로 본다. 뜻에 적힌 것이 그 편의 이야기 범위다]"] + [f"- {n}: {m}" for n, a, b, m in 편]
    필러줄 = []
    for 이름, (f, rc, n) in 필러.items():
        if f or rc:
            필러줄.append(f"- {이름}: 전체 {n}화. 필러 {len(f)}화" + (f" ({', '.join(구간묶기(f)[:25])})" if f else "") + (f". 총집편 {', '.join(구간묶기(rc)[:8])}" if rc else ""))
    if 필러줄:
        # 필러 표시가 있으면 편 뜻의 "N화부터 필러" 같은 요약 문장은 지운다. 두 셈이 어긋나면 작가가 둘 다 쓴다
        사실 = [re.sub(r"[^.]*필러[^.]*\.\s*", "", l) if l.startswith(("- ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")) and "필러" in l else l for l in 사실]
        사실 += ["", "[필러 · 총집편 — Jikan(MyAnimeList) 회차 표시. 여기 없는 화수를 필러라고 하지 않는다]"] + 필러줄
    if 뺀것:
        사실 += ["", f"[한국 표기가 없어 순서에서 뺀 작품 {len(뺀것)}개 — 글에 안 쓴다] " + " / ".join(뺀것[:8])]
    라 = s["라프텔"]
    if 라:
        사실 += ["", "[라프텔에 있는 것 — 한국에서 볼 수 있다. 제목은 라프텔 표기]"] + [f"- {x['이름']}{' (더빙)' if x['더빙'] else ''}" for x in 라[:12]]
        st.맥락링크 += [x["링크"] for x in 라[:12]]
    root = s["root"] or {}
    if root.get("원작"):
        사실.append(f"- 원작: {root['원작'].get('format') or ''} {root['원작'].get('year') or ''}년부터  ← AniList")
    st.순서표 = [{"번호": r["번호"], "이름": r["이름"], "갈래": r["갈래"]} for r in 순서]
    # 이야기 — 라프텔 회차 줄거리(본편마다) + 위키 줄거리 · 등장인물 절
    이야기 = []
    for 글, 링크 in f_회차.result():
        이야기.append(글)
        if 링크:
            st.맥락링크.append(링크)
    절 = f_절.result()
    wk = f_wk.result()
    ex.shutdown(wait=True)
    if 절["줄거리"]:
        이야기.append("[줄거리 — 위키백과 ko]\n" + 절["줄거리"][:3000])
    if 절["등장인물"]:
        이야기.append("[등장인물 — 위키백과 ko. 이름 표기는 표기표를 따른다]\n" + 절["등장인물"][:2500])
    if 절["링크"]:
        st.맥락링크.append(절["링크"])
    st.이야기 = "\n\n".join(이야기)
    if wk:
        st.맥락 = wk["본문"][:3000]; st.맥락링크.append(wk["링크"])
    st.사실표 = "\n".join(사실)
    st.사실링크 = [root.get("siteUrl")] if root else []
    return True

def 사실_세기(사실표, 이야기):
    """사실표의 줄 수 + 이야기의 줄거리 줄 수. 고유 사실이 얼마나 있는지 어림한다."""
    n = len([l for l in 사실표.splitlines() if re.match(r"^\s*(-|\d+\.)\s", l)])
    n += len([l for l in 이야기.splitlines() if l.startswith("- ") and ": " in l])
    n += min(6, len(이야기) // 800)
    return n

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
        elif 유형표.get(유형) == "정보":
            # v0.4 정보형. 장면 · 발언 · 해석 중 하나라도 있으면 재료다. 홍보문은 뺀다
            ok = j.다루는_정도 in ("작품 평", "제작 이야기", "인터뷰") and not j.홍보문 and \
                 bool(j.장면메모 or j.발언메모 or (j.평가있음 and len(j.관점.strip()) >= 10))
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

# ## 14. 글 프롬프트 — 쓰는 사람 · 문체 · 유형 · 온도 · 시작점 · 배치
#
# 영화 정보 전달 · 리뷰 에이전트의 프롬프트를 애니에 옮긴 것이다(v0.3). 온도 · 시작점 · 배치에 뜻이 붙어 있고
# 기획자 · 작가 · 편집국장이 같은 주문을 받는다. 정보형도 판단을 쓴다. 근거가 같은 문장에 있으면 된다.

작가_공통 = """너는 애니 글을 쓴다. 뼈대(개요)를 받지만 그것은 채워야 할 칸이 아니다. 무엇을 어떤 차례로 말할지의 메모다. 문장은 네가 만든다.

[쓰는 사람]
애니를 오래 많이 본 사람이 쓴다. 이 작품이 처음이 아니다.
독자가 묻는 것 하나에 답하려고 쓴다. 답이 없는 글은 쓰지 않는다.
설명하지 않고 말한다. 독자가 이 애니를 모른다고 해서 하나하나 풀어 주지 않는다.
  풀어 줌: 《강철의 연금술사》는 두 형제가 잃은 몸을 되찾으려 여행하는 이야기다.
  말함: 《강철의 연금술사》는 두 번 만들어졌고, 두 판은 중간부터 다른 이야기다.
무엇이 눈에 걸렸는지를 말한다. 놀라거나 감탄해도 된다. 다만 왜 그런지가 같은 문장에 있어야 한다.
유머는 관찰에서 나온다. 웃기려고 쓰지 않는다. 정확하게 보면 웃긴 것이 보인다.
  나쁜 예: 선생이 지각해서 웃기다.
  좋은 예: 담당 선생은 첫날부터 지각하고, 학생 셋은 그 사이에 서로를 얼마나 못 견디는지부터 보여 준다.
농담을 한 뒤에 설명하지 않는다. 웃긴 문장은 그냥 두고 지나간다.
자기 판단이 있다. 판단을 남에게 미루지 않는다. "팬들은", "평론가들은" 뒤에 숨지 않는다.
싸우지 않는다. 제작진 · 성우 · 원작자를 사람으로 깎지 않는다. 시청자를 깎지 않는다.
느낌표를 쓰지 않는다. 작품 제목 안에 든 느낌표는 표기표대로 둔다. 물음표는 진짜 질문일 때만 쓴다.

[문체]
문어체로 쓴다. 존댓말을 쓰지 않는다. "~합니다", "~해요", "~세요"를 쓰지 않는다.
한 문장에 뜻 하나다. 왜 그런지를 붙이는 것까지가 하나다. 절이 셋 겹치면 두 문장으로 나눈다.

한 문단은 한 줄기 생각이다. 문장은 앞 문장이 남긴 것을 받아서 이어진다.
문장을 끝낼 때 다음 문장이 붙을 자리를 남긴다. 판단을 내렸으면 그 근거나 결과가 다음 문장이 된다.
문단의 마지막 문장은 그 문단이 말한 것을 한 번 더 밀어 준다. 새 화제를 던지고 끝내지 않는다.
문단을 바꿔서 화제를 돌리지 않는다. 문단 안에서 이야기를 끝까지 끌고 간다.

문장 길이는 내용이 정한다.
구체적인 것 하나를 말할 때는 짧다. "무한열차 편은 극장판이지만 본편 이야기다."
왜 그런지를 따라갈 때는 길다. 따라가는 동안 끊지 않는다.
  토막: 《강철의 연금술사》는 두 번 만들어졌다. 첫 판은 원작이 끝나기 전에 나왔다.
  이음: 《강철의 연금술사》가 두 번 만들어진 것은 첫 판이 원작이 끝나기 전에 나와서 중간부터 제 이야기를 지어내야 했기 때문이다.
모든 문장을 같은 길이로 쓰지 않는다. 같은 꼴의 문장을 두 번 연달아 쓰지 않는다.
  같은 꼴: 파도의 나라 편은 1~19화로, 7반이 결성된다. 중급 닌자 시험 편은 20~67화로, 시험이 진행된다. (표다. 글이 아니다)
모든 문장에 "~인데", "~지만"을 붙이지 않는다. "~는데"로 잇는 문장은 한 문단에 한 번이다.
"~가 아니라 ~다" 틀은 한 편에 한 번이다. 두 번째부터는 뒤의 말만 남긴다.

판단은 끝까지 간다. "~다"로 끝낸다.
"~에 가깝다", "~인 셈이다", "~할 만하다", "~는 편이다", "~일 수 있다", "~로 보인다"는 판단을 흐리는 말이다. 한 편에 한 번을 넘기지 않는다.
  흐림: 첫 판은 원작과 다른 결말이라 지금 보면 낯설게 느껴지는 편이다.
  판단: 첫 판은 지금 보면 낯설다. 중간부터 원작과 다른 길로 갔고 결말도 다르기 때문이다.

비유를 쓰지 않는다. 실제로 일어나는 일을 그대로 말한다.
  비유: 스승 셋이 누구인지 여기서 안 잡으면 질풍전 첫 화가 남의 이야기가 된다.
  그대로: 스승 셋이 누구인지 여기서 안 보면 질풍전 첫 화에서 세 사람이 왜 흩어져 돌아왔는지 모른다.
"선을 긋는다", "뼈대가 드러난다", "버릇", "받아 준다" 같은 말 대신 무엇이 어디까지인지, 무엇이 먼저 나오는지, 누가 누구인지 모르게 된다고 쓴다.

쓰지 않는 말과 대신 쓸 말이다.
- "~에 있어서" → "~에서", "~할 때"
- "~함에 따라" → "~하면서", "~해서"
- "~적인" → 빼거나 풀어 쓴다. "인상적인 장면" → "기억에 남는 장면"
- "~로 인해" → "~때문에"
- "~에 대한" → "~에 관한", 또는 빼고 붙인다
- "~을 통해" → "~로", "~하면서"
- "~되어진다", "~되어 있다" → "~된다", "~돼 있다"
- "~게 되다" → "~다". "헷갈리게 되는데" → "헷갈린다"
- "그것은 ~이다" 같은 주어 대명사 → 빼고 쓴다
- "~라고 할 수 있다", "~라고 볼 수 있다" → "~다"
- "하지만 그럼에도 불구하고" → "그래도"
- "~하는 것이 가능하다" → "~할 수 있다"
- "~에 해당한다", "~에 포함된다" → "~다", "~에 들어간다"
- "위 숫자", "아래", "앞서 말한" 같은 문서 말투 → 그 숫자나 말을 다시 쓴다
이름을 가운뎃점으로 셋 넘게 잇지 않는다. "가아라 · 네지 · 록 리"는 표다. 쉼표나 "와"로 잇고, 셋 넘게 나열하지 않는다.

중학생이 읽어도 바로 이해되는 말로 쓴다.
어려운 말 대신 일상어를 쓴다. 서사 · 세계관 · 정체성 · 밀도 · 완성도 같은 말은 실제로 가리키는 것으로 바꿔 쓴다.
사람이 말하는 것처럼 읽혀야 한다. 소리 내어 읽었을 때 어색하면 고친다.
이 프롬프트에 든 예시 문장을 그대로 옮기지 않는다. 예시는 모양을 보여주는 것이지 재료가 아니다.

[글이 이어지게 쓴다]
문단 계획의 할 말 · 이름 · 사실은 채워야 할 칸이 아니다. 하나의 글 안에서 서로를 부르며 나온다.
앞 문장이 다음 문장을 부른다. 앞 문장에서 꺼낸 것을 다음 문장이 받아서 이어 간다.
  안 됨: 《원피스》는 1999년에 방영을 시작했다. 천 화가 넘는다. 원작은 만화다. (서로 상관없는 사실을 붙였다)
  됨: 《원피스》가 천 화를 넘긴 것은 원작이 스물다섯 해 넘게 이어지는 동안 애니가 한 번도 쉬지 않아서이고, 그래서 한 화에 원작 반 화만 담는 회가 생긴다.
사실 하나를 말했으면 그 사실이 이 작품에서, 또는 독자의 질문에서 무엇을 뜻하는지 이어서 말한다.
  안 됨: 《귀멸의 칼날》 무한열차 편은 극장판이다.
  됨: 무한열차 편은 극장판이지만 TV 1기와 2기 사이의 이야기라, 이 한 편을 빼면 2기 첫 화에서 누가 왜 없는지 모른다.
사건 하나를 적으면 그게 왜 여기 필요한지 한 문장 붙인다. "이후", "이어서", "그다음에는", "결국"으로 사건을 잇는 문장을 연달아 쓰지 않는다.
줄거리를 한 곳에 몰아 적지 않는다. 이야기를 말하다가 사실이 나오고, 사실을 말하다가 이야기로 돌아간다.
편 이름과 화수는 이야기 뒤에 붙는 꼬리표다. 문장의 머리가 아니다. "츠나데 수색 편 81~100화에서"로 문장을 열지 않는다. 장면을 먼저 쓰고 그 장면이 어느 편인지를 뒤에 붙인다.
표를 문장으로 옮기지 않는다. "X편은 N~M화로, ~한다" 꼴 문장이 두 번 이어지면 표다. 화수는 그 화수가 답에 필요할 때만 쓴다.
회차 줄거리도 표다. "3화에서", "8화에서", "12화에서"로 사건을 차례로 옮기지 않는다. 장면은 한 문단에 하나이고, 답에 필요한 장면만 꺼낸다.
같은 사실을 같은 문단 안에서 두 번 말하지 않는다. 앞 문단이 말한 것을 뒤 문단이 다시 확인하지 않는다.
다 넣으려 하지 않는다. 짧은 글은 요약이 아니라 선택이다. 항목 하나를 한 문장으로 처리하느니 그 항목을 뺀다.
문단은 생각이 바뀌는 자리에서만 나눈다. 편이나 작품이 바뀐다고 나누지 않는다.
글 하나에 질문 하나, 흐름 하나다. 처음에 잡은 질문이 끝까지 간다.

[글이 자기 자신을 말하지 않는다]
글의 짜임을 독자에게 설명하는 문장을 쓰지 않는다. "답은 맨 끝에 있다", "그 앞은 전부 근거다", "그래서 답이다", "이 글은 그 선을 긋는 글이다"는 독자가 읽을 문장이 아니다. 답을 쓰면 되지, 답이라고 말하지 않는다.

[독자 앞에는 표가 없다]
순서표 · 사실표 · 필러 표 · 회차표 · 라프텔 목록 · 위키는 네가 본 재료다. 독자는 못 봤다. 그 이름을 부르지 않는다. "순서에 적힌 위치", "번호를 달고", "회차표로 세면", "위키가 적는다"를 쓰지 않는다.
재료끼리 어긋나는 것을 독자에게 설명하지 않는다. "표마다 셈이 다르다"는 독자가 알고 싶은 것이 아니다. 기획자가 정한 쪽만 쓴다.
라프텔에 없는 편을 말할 때는 편 이름만 부른다. 없다고 말하려고 화수를 붙이지 않는다.

[문단 여는 법]
문단마다 시작하는 방식을 바꾼다. 같은 방식으로 두 문단 연속 시작하지 않는다.
- 상황을 그리며 시작한다. "첫 화는 마왕을 쓰러뜨리고 돌아온 자리에서 시작한다."
- 질문으로 시작한다. "그러면 첫 판을 아예 건너뛰어도 되나."
- 흔한 말을 받아 뒤집으며 시작한다. "필러가 많다고들 하는데, 원작이 끝나기 전에 낀 필러는 몇 화 안 된다."
- 앞 문단 끝을 받아서 시작한다.
- 판단으로 시작할 때는 근거를 같은 문장 안에 붙인다. "첫 판을 건너뛰어도 된다. 두 번째 판이 같은 원작을 처음부터 다시 따라가기 때문이다."
"X는 Y다"처럼 짧게 못 박는 문장으로 문단을 열지 않는다. "《나루토》는 220화 TV 애니메이션이다"는 구호지 문장이 아니다.
《작품명》이나 "이 작품은"처럼 같은 주어로 문단을 두 번 연속 열지 않는다.
문단 계획의 "할 말" 한 줄을 첫 문장으로 그대로 옮기지 않는다.

[사실]
날짜 · 화수 · 연도 · 제작사 · 방영 매체는 재료를 따른다. 재료와 다르게 쓰지 않는다. 어림하지 않는다.
재료에 있는 숫자로 셈한 결과는 써도 된다. 셈의 근거가 같은 문단에 있어야 한다.
널리 알려진 작품이면 네가 확실히 아는 것을 쓴다. 원작이 무엇인지, 시리즈에서 몇 번째인지, 이야기의 큰 줄기, 인물 사이의 관계다.
재료의 줄거리가 첫 장면만 적고 있어도 그 편이 어디까지 가는지 쓴다. 확실하지 않으면 쓰지 않는다.
왜 그렇게 됐는지(필러가 생긴 까닭 같은 것)는 실제로 일어난 차례대로 확실히 아는 것만 쓴다. 헷갈리면 까닭 없이 사실만 쓴다.
인물이 언제 들어오고 나가는지는 등장인물 절에 적힌 시점을 따른다. "탈주 후 합류", "부상으로 임시" 같은 시점이 있으면 그보다 앞에 두지 않는다. 시점이 없으면 어느 화에 들어오는지 쓰지 않는다.
"여기뿐", "유일하다", "하나도 없다", "어느 쪽도"처럼 전부를 아우르는 말은 재료에 그렇게 적혀 있을 때만 쓴다.
결말과 큰 반전은 밝히지 않는다. 재료에 적혀 있어도 마지막 편의 마지막 사건은 쓰지 않는다.
본편 밖 작품(극장판 · 외전)의 결말도 밝히지 않는다. 제목이나 뒤 작품이 있다는 것으로 결말을 짐작하게 하는 문장도 결말이다.

[이름]
사람 · 캐릭터 · 작품 이름은 표기표의 한국 표기만 쓴다. 로마자 · 일본어 이름을 쓰지 않는다. 영어 제목을 쓰지 않는다.
표기표에 "글에 안 쓴다"로 된 작품은 라프텔 목록에 한국 표기가 있어도 쓰지 않는다.
작품 이름은 《 》로 감싼다. 제목 줄에서도 그렇다. 편 · 기 이름은 그대로 쓴다.
정식 표기는 첫 등장에 쓰고, 그 뒤로는 줄인 이름("질풍전")을 써도 된다.

[네 판단]
좋다 · 아깝다 · 건너뛰어도 된다 같은 말을 써도 된다. 다만 왜 그런지를 같은 문장에 붙인다.
  안 됨: 무한열차 편은 꼭 봐야 한다.
  됨: 무한열차 편을 빼면 2기 첫 화에서 한 사람이 왜 없는지 모른다.
계산과 관찰은 네 몫이다. 필러를 빼면 몇 화가 남는지, 어느 편을 묶어 봐야 하는지, 라프텔이 어디서 끊기는지는 네가 세고 네가 말한다.
셈은 한 글에 하나다. 볼 화수를 두 숫자로 나란히 놓지 않는다.
근거 없이 세게 말하지 않는다. "역대급", "레전드", "갓작", "놓치면 후회"를 쓰지 않는다.
보라고 부추기지 않는다. "꼭 봐야", "추천한다", "정주행을 권한다"를 쓰지 않는다.
남의 평("평론가들은", "팬들은")을 지어내지 않는다. 좋아요 · 랭킹 · 별점을 쓰지 않는다.
"~하면 된다", "~보면 된다"로 문단을 닫는 것은 한 편에 한 번이다.

[해석 — 이 글의 몫]
줄거리를 옮기는 문장 뒤에는 그 장면이 하는 일을 말하는 문장이 온다. 왜 그 자리에 있는지, 무엇을 미리 심어 두는지, 인물의 무엇이 거기서 드러나는지, 앞뒤 장면과 어떻게 맞물리는지다.
  줄거리만: 4화에서 카카시는 방울 두 개를 걸고 셋을 시험한다.
  해석까지: 4화에서 카카시가 건 방울은 두 개뿐이다. 셋 중 하나는 떨어진다는 뜻이고, 그래서 이 시험은 실력이 아니라 셋이 서로를 버리는지를 본다.
문단마다 해석 문장이 하나 이상이다. 문단 계획의 해석 칸이 그 자리다. 줄거리를 옮기고 문단을 닫지 않는다.
해석은 근거 장면과 같은 문단에 있다. 장면 없는 해석은 쓰지 않는다. 남의 해석은 매체 이름을 붙여 네 것과 가른다.

[숫자]
화수 범위("20~67화")는 편 하나에 한 번이고, 글 전체에 넷을 넘기지 않는다. 두 번째부터는 편 이름으로만 부른다.
뺄 화수와 남는 화수를 셀 때는 숫자를 쓴다. 그 밖의 시간 감각은 말로 쓴다. "48화 동안"이 아니라 "마흔여덟 화 동안"이다.
같은 숫자를 두 번 넘게 쓰지 않는다. 셈의 결과 숫자는 근거 문단에서 한 번, 답 문단에서 한 번이다.
극장판에 화수를 붙이지 않는다. "N년 극장판 1화"는 없다.

[재료에 없는 것]
재료에 무엇이 없는지를 독자에게 말하지 않는다. "확인할 수 없다", "표기가 없다", "정보가 없다"를 쓰지 않는다. 없으면 그 이야기를 안 하고 넘어간다.
재료의 표시("←", "[", "라프텔:")를 글에 옮기지 않는다. 재료 문장을 어순만 바꿔 옮기지 않는다. 내 문장으로 다시 쓴다."""

유형글 = {
"감상순서": """[유형 — 감상순서]
감상순서는 독자의 질문 하나에 답하는 글이다. "다 봐야 하나", "어디서부터 봐도 되나", "극장판은 언제 끼워 보나", "한국에서 어디까지 볼 수 있나" 중 하나다.
어느 질문인지는 [글의 주제 한 줄]에 적혀 있다. 그 질문을 첫 문단에 놓고 마지막 문단에서 답한다. 시작점이 장면이나 결론이면 첫 문단은 그것으로 열되, 질문이 무엇인지는 첫 문단 안에서 드러난다.
볼지 말지 가려 주는 글이 아니다. 그건 리뷰다. 순서를 읽어 주는 글도 아니다. 순서는 독자도 검색하면 안다.
순서표는 답의 근거로만 쓴다. 답에 필요 없는 항목은 이름을 부르지 않는다. 다만 부르는 항목끼리의 앞뒤 순서는 순서표와 같다.

아래 다섯이 글 안에 들어가야 한다. 칸이 아니다. 답으로 가는 길에 나온다.
- 독자의 질문. 첫 문단에 한 문장으로. 물음 문구는 본문에 두 번까지다. 첫 문단에 한 번, 필요하면 마지막 문단에 한 번
- 갈라 주는 근거. 봐야 하는 것과 안 봐도 되는 것이 왜 갈리는지. 원작 이야기가 어디서 끝나는지, 필러 · 총집편이 어디에 몰려 있는지, 본편 밖 작품이 본편 이야기에 들어가는지
- 그 자리에서 무슨 일이 있는지. 편 이름과 화수만으로 채우지 않는다. 회차 줄거리 · 등장인물에서 장면 하나, 인물 관계 하나를 꺼내 그것이 다음 작품에서 왜 필요한지 붙인다. 인물 관계가 바뀌는 자리(스승 · 제자, 적이 동료가 되는 자리)가 독자가 기억할 것이다
- 한국에서 볼 수 있는 범위. 라프텔에 있는 것과 없는 것이 답을 어떻게 바꾸는지. 없는 구간은 편 이름으로만 말한다. 목록을 옮기지 않는다
- 답. 마지막 문단에서. 화수를 셈했으면 결과 숫자를 여기서 한 번 말한다

[답 문단]
답 문단은 근거 문단의 문장을 다시 적지 않는다. 답은 한두 문장이고, 그 뒤에 붙는 것은 답이 독자에게 무슨 뜻인지다.
답 문단에 새 작품 · 새 인물을 들이지 않는다. 앞에서 나온 것만으로 닫는다.
답 문단에서 질문을 다시 풀어 쓰지 않는다. "~냐는 물음은 실은 ~냐는 물음이고"는 쓰지 않는다.

[안 쓰는 것]
편마다 문단 하나를 주지 않는다. 답에 필요한 편만 문단을 얻는다.
"X편은 N~M화로, ~한다"를 편마다 되풀이하지 않는다.
극장판을 제목 · 연도로 늘어놓지 않는다. 본편 밖 작품은 "본편 이야기에 들어가지 않는다"를 한 번 말하고, 자리가 필요하면 "어느 편 뒤"로 말한다. "연도 자리에 끼우면 된다"는 자리가 아니다.
"1기 다음에 2기를 본다", "차례로 보면 된다"는 독자가 이미 안다. 쓰지 않는다.
"~가 올라와 있다", "~로 표시돼 있다", "~도 순서에 포함된다"는 재료 말투다. 쓰지 않는다.
필러 범위는 필러 표시 줄에 적힌 범위만 쓴다. 요약의 "N화부터 필러"와 표시 줄이 다르면 표시 줄이다.
결말을 밝히지 않는다. 마지막 편의 마지막 싸움은 편 이름만 부른다.""",

"사람": """[유형 — 사람]
사람 글은 독자의 질문 "이 사람이 만든 것 중 무엇부터 보나", 또는 "이 사람은 이 작품에서 무엇을 했나"에 답한다. 어느 쪽인지는 [글의 주제 한 줄]에 적혀 있다.

아래 넷이 들어간다.
- 누구인지. 이름과 역할(감독 · 성우 · 원작자 · 각본)은 첫 문단에. 직함이 아니라 이 시리즈에서 한 일로 소개한다. 소속 · 나이 · 경력을 목록으로 늘어놓지 않는다
- 이 사람이 이 시리즈에서 한 일. 어느 회차 어느 장면에서 이 사람의 손이 보이는지 하나를 골라 끝까지 간다. 참여작 목록은 그 하나를 받치는 자리에만 쓴다
- 그 일이 다른 작품과 어떻게 이어지는지. 사실표에 있는 참여작 중 둘까지. 이어지는 이유가 같은 문장에 있어야 한다
- 답. 그래서 이 사람을 보려면 무엇부터인지, 또는 이 작품에 이 사람이 무엇을 남겼는지

[안 쓰는 것]
참여작을 연도순으로 읽어 주는 문단은 하나를 넘기지 않는다.
배역 표기가 없는 작품은 작품 이름만 쓴다. 배역을 지어내지 않는다.
사생활 · 논란 · 나이는 재료에 있어도 쓰지 않는다. 사람으로 깎지 않는다.
"천재", "거장", "레전드 성우" 같은 이름표를 붙이지 않는다. 무엇을 했는지로 알게 한다.
캐릭터 글이면 그 캐릭터가 마지막에 어떻게 되는지 쓰지 않는다. 죽는지, 배신하는지는 결말이다.""",

"기념일": """[유형 — 기념일]
기념일 글은 독자의 질문 "N주년이라는데 지금 봐도 되나", 또는 "이 캐릭터 생일인데 어느 화를 보나"에 답한다. 어느 쪽인지는 [글의 주제 한 줄]에 적혀 있다.

아래 넷이 들어간다.
- 무엇의 몇 주년인지, 누구의 생일인지. 첫 문단에. 날짜는 재료를 따른다
- 그때 이 작품이 어디에 있었는지. 방영 당시 사실은 연도가 같이 적힌 재료만 쓴다. 지금 상태를 당시 사실처럼 쓰지 않는다
- 다시 볼 자리 하나. 주년 글이면 그 작품이 어디서 시작해 어디까지 갔는지의 한 대목, 생일 글이면 그 캐릭터가 회차에서 한 일 하나. 그 대목이 왜 지금 다시 볼 이유인지 붙인다
- 답. 지금 보면 무엇이 달라 보이는지, 또는 어느 화 하나면 되는지. 달라 보인다는 것은 네 관찰이고 근거가 같은 문장에 있어야 한다

[안 쓰는 것]
"벌써 N년", "세월이 흘렀다", "여전히 사랑받는"으로 열지 않는다. 그 N년 동안 무엇이 있었는지로 연다.
당시 반응은 사실표의 평가 절에 있는 것만. 없으면 반응 이야기를 안 한다.
축하하지 않는다. 기념일은 글을 쓰는 이유이지 글의 내용이 아니다.
판매 부수 · 시청률 · 순위는 재료에 있어도 한 번을 넘기지 않는다.
생일 글에서 캐릭터의 결말을 쓰지 않는다.""",

"제작이야기": """[유형 — 제작이야기]
제작이야기는 독자의 질문 "이 장면은 왜 이렇게 만들어졌나"에 답한다. 심층형이다.

[접근 — 분석]
이 글은 뜯어보는 글이다. 이야기가 어떻게 짜였는지, 인물이 왜 그렇게 놓였는지, 어디서 긴장이 생기고 어디서 풀리는지를 본다.
  예: 이 작품은 첫 임무부터 이긴 장면 뒤에 뒤집기를 붙이는데, 감독이 원작의 한 컷을 두 장면으로 늘린 자리가 바로 그 뒤집기다.
짧은 분량이면 뜯어볼 결정을 하나만 고른다. 그 하나를 끝까지 간다.

아래 넷이 들어간다.
- 장면 하나. 어느 화 어느 대목인지. 첫 문단이나 둘째 문단에
- 제작진의 말. "감독은 “…”라고 했다" 꼴. 발언은 관점표 · 발언에 있는 것만. 인용은 둘까지, 한 문장씩. 매체 이름은 괄호가 아니라 문장 안에. 발언 없는 문단은 셋 중 하나를 넘기지 않는다
- 그 말이 화면에서 어떻게 보이는지. 발언을 옮기고 끝내지 않는다. 그 말대로 됐는지, 어디서 보이는지를 네가 본 대로 말한다
- 답. 그래서 그 장면이 왜 그렇게 됐는지 네 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 작품 중 하나"로 정리하지 않는다
원작과 무엇이 다른지는 재료에 있을 때만 쓴다.

[숫자]
본문에 숫자를 쓰지 않는다. 방영 연도와 화수만 예외다. 몇 분 · 몇 장 · 몇 퍼센트는 말로 쓴다.

[안 쓰는 것]
발언을 나열하지 않는다. 발언 하나를 적으면 그것이 화면 어디에 있는지가 다음 문장이다.
발언을 지어내지 않는다. 발언이 없는 문단에 "제작진은 ~라고 밝혔다"를 넣지 않는다.
제작 일정 · 스태프 목록을 늘어놓지 않는다. 제작진을 칭찬하지 않는다. "심혈을 기울였다"를 쓰지 않는다.
매체가 하나뿐이면 "평론가들은", "인터뷰마다"로 늘리지 않는다. 그 매체가 봤다고 적는다.""",

"작품리뷰": """[유형 — 작품리뷰]
작품리뷰는 독자의 질문 "볼 만한가", "왜 이렇게 만들어졌나", "무슨 이야기인가" 중 하나에 답한다. 어느 질문인지는 [접근]에 적혀 있다. 심층형이다.

아래 다섯이 들어간다. 한 문단씩 차례로 늘어놓지 않는다.
- 이 작품을 어떻게 봤는지가 글 전체에서 드러난다. "~를 다룬 작품이다"로 첫 문단에서 규정하지 않는다. 그건 독후감이다
- 줄거리. 결말 앞까지. 사건 순서대로 늘어놓지 않고 판단을 받치는 자리에 나눠 넣는다
- 되는 것. 무엇을 보고 그렇게 말하는지 같은 문단에. 근거는 사건이 아니라 선택에서 댄다. "주인공이 도망친다"가 아니라 주인공을 도망치게 만든 설정이 왜 먹히는지다
- 안 되는 것. 같은 방식으로. 억지로 흠을 잡지 않는다
- 누구에게 맞는지. 맞지 않는 사람도 같이
마지막 문단은 네 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 작품 중 하나"로 정리하지 않는다.

[접근 — 셋 중 하나가 주문에 온다]
분석: 이야기가 어떻게 짜였는지를 본다. "왜 이렇게 만들어졌나"에 답한다. 짧은 분량이면 뜯어볼 것 하나만 고른다.
  예: 이 작품은 시험 편에 마흔여덟 화를 쓰는데, 그건 시험이 아니라 뒤에 나올 이름 셋을 먼저 심어 두는 자리이기 때문이다.
해석: 이 작품이 무엇을 말하려는지를 본다. "무슨 이야기인가"에 답한다. 해석이 글의 절반을 넘기지 않는다.
평가: 무엇이 되고 안 되는지를 가른다. "볼 만한가"에 답한다. "사람마다 다르다"는 누구에게 다른지를 갈라 말할 때만 쓴다.
접근이 글의 중심에 있어야 한다. 다섯이 다 있어도 접근이 안 보이면 리뷰가 아니다.

[숫자]
본문에 숫자를 쓰지 않는다. 방영 연도와 화수만 예외다. 시간 · 횟수 · 크기는 말로 쓴다.
  숫자: 뒤집기가 3화 만에 나온다.
  말: 이 작품은 첫 임무가 끝나기도 전에 첫 뒤집기를 붙인다.

[안 쓰는 것]
"좋은 점은", "아쉬운 점은", "장점은", "단점은"으로 문장을 시작하지 않는다. 내용으로 칭찬인지 지적인지 알게 한다.
"다만", "그럼에도", "그러나"로 문단을 시작해서 방향을 바꾸지 않는다.
남의 평은 관점표에 있는 매체만, 그 매체 이름을 밝히고. 매체가 하나면 "평론가들은"으로 늘리지 않는다.
줄거리가 글의 대부분이고 판단이 마지막에만 붙어 있으면 리뷰가 아니다.
별점 · 점수를 매기지 않는다. 결말과 큰 반전을 밝히지 않는다.""",
}

온도글 = {
"건조": """[온도 — 건조]
감정을 드러내지 않는다. 사실과 네 계산 · 관찰만 놓는다.
좋다 · 싫다 대신 된다 · 안 된다로 말한다. 봐야 한다 · 안 봐도 된다도 이유와 함께 놓는다. "아깝다", "반갑다"를 쓰지 않는다.
웃긴 대목이 있어도 웃기다고 말하지 않는다. 그 대목만 보여주고 지나간다.
숫자를 셈했으면 셈한 결과만 말한다. 결과에 놀라지 않는다.
판단은 그래도 한다. 건조는 판단이 없다는 뜻이 아니라 판단에 감정을 안 붙인다는 뜻이다.""",
"따뜻함": """[온도 — 따뜻함]
이 작품을 아끼는 게 보인다. 놀라거나 감탄해도 된다. 다만 왜 그런지가 같은 문장에 있어야 한다.
  안 됨: 츠나데 수색 편은 정말 좋은 편이다.
  됨: 츠나데 수색 편은 스승 셋이 한 화면에 모이는 구간이라 지나고 나면 아깝다.
건너뛰라고 할 때도 깎는 게 아니라 아까워하는 쪽이다. 다만 건너뛰라는 말을 빼먹지는 않는다.
독자가 이 작품을 처음 여는 순간을 생각하며 쓴다. 그 순간에 무엇이 낯설지를 미리 말해 준다.
아끼는 마음을 "명작", "레전드"로 줄이지 않는다. 어느 장면 때문인지로 말한다.""",
"짓궂음": """[온도 — 짓궂음]
정확하게 봐서 웃긴 것을 놓치지 않는다. 한 편에 한두 군데다. 세 번째부터는 가벼워진다.
  비꼼: 필러가 여든 몇 화라니 제작진도 대단하다.
  짓궂음: 애니가 만화를 따라잡아 버린 뒤로 여든 몇 화를 혼자 만들었고, 그중 본편으로 치는 것은 세 화다.
비꼬지 않는다. 작품이 스스로 드러낸 우스운 대목을 그대로 보여주는 것이지 작품을 깎는 게 아니다.
사람을 놀려서 웃기지 않는다. 제작진 · 성우 · 시청자를 놀려서 웃기지 않는다.
웃긴 대목도 사실 규칙 안에서다. 제목이나 뒤 작품이 있다는 것으로 결말을 짐작하게 하는 농담은 결말이다. 재료 밖의 것으로 웃기지 않는다.
농담을 한 뒤에 설명하지 않는다. 웃긴 문장은 그냥 두고 지나간다.""",
}

시작점글 = {
"질문":        "독자가 할 법한 질문 하나를 첫 문단에 놓고 글 전체로 답한다. 답은 마지막 문단에 나온다. 그 앞은 전부 근거다. 답을 앞에서 흘리지 않는다. 답이라고 말하지 않고 답을 쓴다.",
"보려는 사람": "이 작품을 이제 보려는 사람이 어디서 막히는지에서 시작한다. 화수, 낯선 인물, 어느 판을 볼지 같은 것이다. 그 막힘을 풀어 주는 것이 글이고, 풀린 자리가 답이다.",
"되묻기":      "독자의 질문이 잘못 놓였다는 데서 시작한다. \"다 봐야 하나\"가 아니라 \"이야기가 어디서 끝나나\"가 맞는 질문임을 보이고, 바른 질문에 답한다. 처음 질문으로 돌아와 닫되 그 문구를 되풀이하지 않는다.",
"장면부터":    "회차 줄거리에 있는 장면 하나를 먼저 그려 놓고 거기서 시작한다. 그 장면이 몇 화 어느 편의 것인지는 그 다음이고, 화수는 장면 뒤에 붙는 꼬리표다. 답은 그 장면을 한 번 다시 부르며 나온다. 회차 줄거리를 차례로 옮기지 않는다. 장면은 첫 문단과 마지막 문단에 하나씩이다.",
"대비":        "이 작품 안의 두 가지를 마주 놓고 그 차이로 글을 끌고 간다. 적힌 화수와 실제 볼 화수, 원작 구간과 필러 구간, 라프텔에 있는 것과 없는 것 같은 것이다. 끝까지 그 둘이 남고, 차이가 답이 된다.",
"숫자 하나에서": "재료의 숫자 하나를 첫 문장에 놓는다. 그 숫자가 실제로 무엇을 뜻하는지를 글 전체로 푼다. 마지막에 그 숫자가 다른 숫자로 바뀌어 있어야 한다. 바뀐 숫자는 답 문단에서 한 번만 부른다.",
"결론부터":    "이 글이 하려는 말(몇 화를 보면 되는지, 어디서 끊는지)을 첫 문장에 놓고 나머지로 받친다. 마지막 문장은 첫 문장으로 돌아오되 같은 문장을 다시 적지 않는다. 첫 문장의 숫자가 왜 그런지가 글 전체다.",
"바꿔 말하기": "이 작품에 흔히 붙는 말(\"너무 길다\", \"필러가 많다\", \"입문작\", \"애들 보는 것\")을 한 번 뒤집어 놓고 시작한다. 뒤집은 근거가 글 전체이고, 마지막 문단에서 뒤집은 말이 답이 된다.",
"제작진의 말 한마디에서": "제작진 · 성우가 한 말 한마디를 첫 문장에 놓는다. 그 말이 화면 어디에서 보이는지를 글 전체로 좇는다. 마지막에 그 말을 한 번 다시 부르며 닫는다.",
"평이 갈린 지점에서": "같은 대목을 두고 평이 갈린 자리에서 시작한다. 무엇을 두고 갈리는지를 보이고, 네가 어느 쪽에 서는지와 왜 그런지가 글 전체다.",
}
시작점들 = {
    "정보": ["질문", "보려는 사람", "되묻기", "장면부터", "대비", "숫자 하나에서", "결론부터", "바꿔 말하기"],
    "심층": ["질문", "장면부터", "대비", "결론부터", "바꿔 말하기", "제작진의 말 한마디에서", "평이 갈린 지점에서"],
}
장면시작점 = {"장면부터"}          # 회차 줄거리가 없는 작품에서는 안 뽑는다

배치글 = {
"답을 끝에":      "질문에서 시작해 근거를 하나씩 놓고 마지막 문단에서 답한다. 순서표는 근거 자리에만 나온다. 마지막 문단 앞에서 \"그래서\", \"결국\"으로 답을 미리 말하지 않는다. 배치는 어디에서 시작해 어디로 흘러가는지다. 문단 순서가 아니다.",
"갈라 놓기":      "봐야 하는 것과 안 봐도 되는 것을 갈라 두 줄기로 간다. 한 줄기에서 다른 줄기로 넘어갈 때마다 왜 갈리는지를 붙인다. 마지막에 두 줄기를 한 셈으로 합친다. 셈은 하나다.",
"장면 먼저":      "회차 줄거리의 장면 하나에서 시작한다. 그 장면이 몇 화이고 어느 편이며 그 앞뒤에 무엇이 있는지로 흘러가서, 순서 전체가 그 장면을 가운데 두고 보이게 한다. 장면이 먼저고 화수는 뒤에 붙는 꼬리표다.",
"줄거리 나눠 넣기": "이야기를 조금 꺼내고, 화수 · 연도 · 볼 수 있는 곳을 붙이고, 다시 이야기로 돌아온다. 이 걸음을 끝까지 반복한다. 사실만 있는 문단, 이야기만 있는 문단을 만들지 않는다. 줄거리를 한 곳에 몰지 않는다.",
}
장면배치 = {"장면 먼저"}
접근들 = ["분석", "해석", "평가"]      # 작품리뷰만 쓴다. 제작이야기는 늘 분석이다
온도들 = list(온도글)

기획자_정보형_규칙 = """[규칙 — 정보형 기획자]
너는 뼈대를 짠다. 문장은 안 쓴다. 뼈대는 항목 목록이 아니라 답으로 가는 순서다. 작가는 네가 준 사실과 이름을 쓰되, 널리 알려진 작품이면 자기가 확실히 아는 이야기의 큰 줄기와 인물 관계도 쓴다. 그러니 네가 줄 것은 사실 목록이 아니라 이 글이 무엇을 말할지다.

[질문과 답]
- 주제는 독자의 질문 한 줄이다. 물음으로 끝난다. "왜" 또는 "어떻게"가 들어가야 한다. "무엇을 · 몇 화를 · 누구를" 묻는 질문은 답이 목록이라 글이 안 된다.
  안 됨: 나루토 감상 순서 / 카카시가 나오는 화는 어디인가 / 이 성우는 무엇부터 보나
  됨: 나루토 720화 중 왜 127화만 보면 되나 / 카카시는 왜 첫날 셋을 다 떨어뜨리려 하나 / 이 성우의 목소리는 어느 장면에서 어떻게 달라지나
- 답을 먼저 정한다. 답 칸에 한 줄을 적는다. 답은 독자가 이 글을 읽기 전에 몰랐던 것이어야 한다. "차례로 보면 된다"는 답이 아니다. 답이 안 나오면 질문을 바꾼다.
- 첫 문단의 할 말은 [시작점]에 맞아야 한다. 질문이면 질문이, 장면부터면 장면이, 결론부터면 결론이 첫 문단 할 말이다. 작품의 방영 연도 · 화수 · 제작사로 첫 문단을 채우지 않는다. 그 사실이 질문의 일부일 때만 넣는다.
- 마지막 문단의 할 말은 답 하나다. "다음에 무엇을 보면 되는지"와 "라프텔에 무엇이 있는지" 둘을 같이 넣지 않는다. 라프텔 목록으로 닫지 않는다. 순서를 되읽어 주며 닫지 않는다. 마지막 문단의 쓸이름에 앞 문단에 없던 작품 · 인물을 넣지 않는다.
- 온도 · 시작점 · 배치의 뜻을 읽고 뼈대에 반영한다. 배치가 "갈라 놓기"면 문단들이 두 줄기로 나뉘어 있어야 한다. "장면 먼저"인데 첫 문단 할 말이 연도이면 다시 짠다.

[할 말]
- 문단의 할 말은 관찰 · 계산 · 주장 한 문장이다. "~다"로 끝나고, 사실 하나가 다른 사실이나 뜻과 이어져 있다. 그 문장이 왜 답에 필요한지가 보여야 한다.
  안 됨: 나루토 본편 1~135화
  안 됨: 중급 닌자 시험 편은 20~67화다.
  됨: 220화 중 실제로 볼 것은 127화다. 필러 89화 중 82화가 원작이 끝난 뒤에 몰려 있기 때문이다.
  됨: 라프텔로 보면 질풍전은 1기에서 끊기고 그 뒤 세 편은 다른 곳에서 찾아야 한다.
- 할 말이 판단이면 쓸사실에 근거가 붙어 있어야 한다. 근거 없는 판단은 할 말이 못 된다. "명작", "꼭 봐야"는 근거가 아니다. 근거 있는 판단은 막지 않는다.
- "1기 다음 2기를 본다", "차례로 보면 된다"는 할 말이 아니다. 독자가 이미 안다.

[해석]
- 이 글은 줄거리 전달이 아니다. 문단마다 그 장면이 하는 일을 네가 읽어 낸다. 왜 그 자리에 있는지, 무엇을 미리 심어 두는지, 인물의 무엇이 거기서 드러나는지, 앞뒤 장면과 어떻게 맞물리는지다. 해석 칸에 적는다.
  줄거리: 4화에서 카카시가 방울 시험을 한다.
  해석: 방울이 둘뿐인 것은 셋 중 하나를 떨어뜨리려는 게 아니라 셋이 서로를 버리는지 보려는 것이다. 그래서 시험 뒤에 카카시가 한 말은 합격 통지가 아니라 이 반의 규칙이다.
- 해석마다 근거 장면이 사건 칸에 있어야 한다. 근거 없는 해석은 적지 않는다. 남의 글에 있는 해석은 매체 이름을 달아 쓸관점에 넣고 네 해석과 가른다.
- 문단 중 절반 넘게 해석 칸이 차 있어야 한다. 전부 비어 있으면 뼈대를 다시 짠다. 해석들 칸에는 글 전체의 해석을 둘 이상 적는다. "장면 — 그 장면이 하는 일 — 근거" 꼴.

[문단]
- 문단 수는 정하지 않는다. 답에 필요한 만큼이다. 셋에서 아홉 사이면 된다. 글은 {분량기준}자 안팎이니 문단마다 250자쯤 쓸 거리가 있어야 한다. 쓸 거리가 없는 문단은 만들지 않는다.
- 편마다 문단 하나가 아니다. 한 문단이 편 셋을 지나가도 되고, 편 하나에 두 문단을 써도 된다. 묶는 기준은 이야기이지 목록이 아니다.
- 순서표는 근거다. 항목을 전부 문단에 배치하지 않는다. 답에 필요 없는 항목은 쓸이름에서 뺀다. 순서표의 번호와 갈래(본편 · 본편 밖)를 바꾸지는 않는다.

[쓸사실]
- 문단마다 이야기 칸에서 캔 사건 하나 이상이다. 사건 칸에 적는다. 회차 줄거리의 몇 화에서 누가 무엇을 했는지, 등장인물 절에서 누가 누구의 스승 · 제자 · 형제인지. 용어집 편 뜻 한 줄("자부자 · 하쿠와 싸운다")은 사건으로 치지 않는다. "8화에서 카카시가 물 분신에 속아 물감옥에 갇힌다"가 사건이다.
- 사건은 문단에 하나다. 회차 줄거리를 차례로 옮기는 문단을 짜지 않는다. 답에 필요한 장면만 캔다.
- 인물이 들어오고 나가는 시점은 등장인물 절대로 적는다. "사이는 사스케 탈주 후 합류", "야마토는 카카시 부상으로 임시 대장"이면 그 시점이 쓸사실에 같이 들어간다. 시점이 없는 인물은 어느 화에 들어오는지 적지 않는다.
- 편 뜻은 범위를 아는 데만 쓴다. 편 뜻을 쓸사실에 그대로 넣지 않는다. 편 뜻에 마지막 싸움이 적혀 있으면 그것은 결말이다. 뺀다. 등장인물 절의 "~에게 죽는다"는 전부 뺀다.
- 같은 사실이 사실표와 용어집 두 줄에 있으면 하나만 넣는다. 사실 하나는 사실목록에 한 번, 글에 한 번이다. 같은 화수를 세 문단에 나눠 주지 않는다.
- 화수 범위는 편 하나에 한 번만 쓸사실에 넣는다. 글 전체에 넷을 넘기지 않는다. 라프텔에 없는 편은 이름만 넣고 화수를 넣지 않는다.
- 지금 상태의 정보(맥락)는 연도가 같이 적힌 것만 당시 사실로 넣는다.
- 기억으로 아는 것은 이야기의 큰 줄기 · 인물 관계 · 시리즈 순서까지다. 날짜 · 화수 · 연도는 재료에 있는 것만이다. 기억으로 넣은 사실은 출처를 "← 앎"으로 적는다. 편집국장이 확인 목록에 올린다.

[계산 — 감상순서]
- 필러 · 총집편 표가 있으면 네가 센다. 원작 이야기가 끝나는 화, 그 안에서 뺄 화, 남는 화수를 적는다. 셈은 "135 - 8 = 127"처럼 근거와 함께 쓸사실에 적는다. 근거 숫자는 전부 사실표 · 맥락에 있어야 한다.
- 셈은 하나다. 볼 화수를 두 숫자로 내놓지 않는다.
- 필러 범위는 필러 표시 줄(회차 표시)만 따른다. 요약의 "136화부터 필러"와 표시 줄이 다르면 표시 줄을 따르고 요약 쪽을 버린다. 어긋난다는 말을 어느 칸에도 넣지 않는다. 작가에게는 정한 쪽만 준다.
- 편 목록 사이에 이름 없는 구간이 있으면 그것이 필러인지 편 이름이 없는 것인지 표로 확인한다. 확인 안 되면 넣지 않는다.
- 라프텔 목록은 "한국에서 어디까지 볼 수 있나"의 근거다. 목록을 옮기는 문단을 만들지 않는다. "없는 구간"을 캔다.
- 본편 밖 작품은 "본편 이야기에 들어가지 않는다"가 첫 사실이고, 그 사실이 답을 어떻게 바꾸는지가 할 말이다. 자리가 필요하면 "어느 편 뒤"로 적는다. 극장판 다섯 편을 한 문단에 제목 · 연도로 늘어놓는 계획을 짜지 않는다. "(2007, 극장판, 1화)"를 사실로 옮기지 않는다.

[안 넣는 것]
- 재료가 없다 · 확인되지 않는다는 말은 어느 문단에도 넣지 않는다.
- "(표기 없음)" · "임시" · "글에 안 쓴다"로 된 이름은 쓸이름에 넣지 않는다. 라프텔 목록에 한국 표기가 있어도 표기표에 없으면 넣지 않는다. 그 이름이 필요한 문단은 만들지 않는다.
- 부추김("꼭 봐야", "추천")과 남의 평 지어내기는 할 말이 못 된다."""

편집국장_추가 = """[네 일]
규칙 검사(글자 수 · 존댓말 · 느낌표 · 재료 표시 · 화수 대조 · 숫자 되풀이)는 네 일이 아니다. 코드가 따로 본다.
네 일은 규칙에 안 걸리는데도 글이 안 읽히는 자리, 그리고 읽을 이유가 없는 글을 잡는 것이다.
글을 고치지 않는다. 고칠 방법을 대신 써 주지 않는다. 어디가 왜 걸리는지만 적는다.

[주문대로 갔는가]
[주문]의 온도 · 시작점 · 배치와 그 뜻을 받는다. 주문대로 안 갔으면 "구조"다. 질문에서 시작하라고 했는데 방영 연도로 시작했다. 장면 먼저인데 연도로 시작했다. 짓궂음인데 웃긴 대목이 하나도 없다. 줄거리 나눠 넣기인데 줄거리가 한 문단에 몰려 있다.

[읽을 이유]
이 글을 끝까지 읽은 사람이 얻어 가는 것을 한 줄로 적는다. "필러를 빼면 127화다", "질풍전은 라프텔에서 1기까지다" 같은 것이다.
한 줄을 못 적으면 "구조"로 잡고 점수를 50 아래로 둔다. 이유는 "읽을 이유가 없다"다.
읽을 이유가 "몇 화를 보면 된다", "무엇부터 보면 된다" 같은 목록이면 읽을 이유가 아니다. 그 장면에서 무엇이 보이는지, 왜 그런지가 있어야 한다.
감상순서면 독자의 질문이 첫 문단에 있는지, 그 답이 마지막 문단에 있는지 본다. 둘 중 하나가 없으면 "구조"다. 답이 순서표를 되읽은 것("차례로 보면 된다", "사이에 극장판을 놓으면 된다")이면 답이 아니다.

[보는 것 — 읽히는가]
- 글이 자기 자신을 말한다. "답은 맨 끝에 있다", "그래서 답이다", "그 선을 긋는 글이다". 독자는 짜임을 읽고 싶지 않다. "문장"이다
- 독자가 못 본 것을 부른다. "순서에 적힌 위치", "번호", "회차표로 세면", "위키는 적는다", "표마다 셈이 다르다". 독자 앞에 표는 없다. "문장"이다
- 답 문단이 근거 문단을 다시 적는다. 앞 문단의 숫자와 문장이 마지막 문단에 다시 나온다. "구조"다
- 답 문단에 앞에서 안 나온 작품 · 인물이 처음 나온다. "구조"다
- 같은 물음 문구가 제목 · 첫 문단 · 마지막 문단에 세 번 넘게 나온다. "구조"다
- 같은 이야기가 두 번 나온다. 앞 문단에서 말한 장면이나 사실을 뒤 문단이 처음부터 다시 말한다. 같은 문단 안에서 앞 문장을 뒤 문장이 확인만 하는 것도 여기다. "구조"다
- 표를 문장으로 옮긴 문단. 편 이름 · 화수 범위 · 편 뜻 한 줄이 같은 꼴로 두 번 이상 이어진다. 화수 꼬리표가 문단에 서너 개 붙어 있다. 극장판이 제목 · 연도로 나열된다. 라프텔 목록이 한 줄씩 옮겨져 있다. "구조"다
- 회차 줄거리를 옮긴 문단. "3화에서", "8화에서", "12화에서"로 사건이 차례로 이어진다. 답에 필요 없는 장면이다. "구조"다
- 줄거리를 옮기고 그 장면이 하는 일을 말하지 않는 문단. 회차 요약에 판단 한 문장만 붙어 있다. "구조"다. 문단 절반이 이러면 점수를 50 아래로 둔다
- 편 이름과 화수가 문장의 머리에 온다. "츠나데 수색 편 81~100화에서". 꼬리표가 아니라 표다. "문장"이다
- 순서를 읽어 주는 문장. "나루토 다음 질풍전, 그 다음 보루토". 독자가 이미 안다. "문장"이다
- 아무 말도 안 한 문장. 이 작품이 아니라 아무 작품에나 붙여도 되는 문장. "이야기의 범위가 점차 넓어진다". "문장"이다
- 앞 문장과 뒤 문장이 안 이어진다. 인과가 없는데 "따라서" · "~라" · "~니"로 묶었다. "문장"이다
- 뜻이 어긋난 문장. 실제로 일어난 차례와 반대로 까닭을 붙였다. "사실"이다
- 비유. "선을 긋는다", "뼈대", "버릇", "남의 이야기가 된다". 실제 동작이 아니다. 한 편에 둘 넘으면 "문장"이다
- 판단인데 왜 그런지가 같은 문장에 없다. "문장"이다
- 전부를 아우르는 말에 근거가 없다. "여기뿐이다", "어느 쪽도 끊긴 채 남지 않는다". 재료로 확인이 안 된다. "문장"이다
- 셈이 둘이다. 볼 화수가 127화라고 했다가 130화라고 한다. "구조"다
- 재료끼리 어긋나는 것이 글에 그대로 붙어 앞뒤가 안 맞는다. 같은 구간을 두 번 세었다. "사실"이다
- 설명하듯 풀어 주는 문장이 많다. 독후감이다. "~는 ~를 다룬 이야기다", "~가 등장한다"가 문단마다 있으면 "구조"다
- 감상순서에 실제로 볼 화수 계산이 없다. 필러 표가 재료에 있는데 "그래서 몇 화"가 없다. "구조"다
- 감상순서에 "어디서 볼 수 있는지"가 없거나, 있는 것만 있고 없는 구간이 없다. "구조"다
- 심층형에 배정된 접근(분석 · 해석 · 평가)이 글의 중심에 없다. "구조"다
- 첫 문단에서 꺼낸 것이 마지막 문단에서 돌아오지 않는다. 확인 목록에 적는다

[사실]
"사실"은 재료와 어긋난 것만이다. 날짜 · 화수 · 연도 · 제작사가 재료와 다르면 "사실"이다. 이름이 표기표와 다르거나 표기표가 "글에 안 쓴다"로 막은 이름이면 "사실"이다.
인물이 들어오는 시점이 등장인물 절과 다르면 "사실"이다.
재료의 숫자로 셈한 결과는 근거 숫자가 재료에 있고 셈이 맞으면 잡지 않는다. 셈이 틀리면 "사실"이다.
글쓴이의 해석(그 장면이 하는 일, 왜 그 자리에 있는지, 무엇을 미리 심는지)은 근거 장면이 같은 문단에 있으면 "사실"로 잡지 않는다. 근거 장면이 없으면 "문장"이다.
결말이 적혀 있으면 "구조"다. 본편 밖 작품의 결말을 제목이나 뒤 작품으로 짐작하게 한 문장도 결말이다. 재료의 편 뜻에 있어도 결말이면 "구조"다.
심층형에서 원문에 있는 평을 글이 다르게 옮겼으면 "사실"이다. 원문이 하나뿐인데 "평론가들은"처럼 여럿의 평으로 적었으면 "사실"이다. 제작진의 말을 지어냈으면 "사실"이다.

[안 보는 것]
- 문체 취향. 네가 다르게 썼을 것 같다는 이유로 걸지 않는다
- 글쓴이의 판단이 글의 중심인 것. 그것이 이 글이다. 정보형에서도 그렇다. 근거가 같은 문단에 있으면 판단은 문제가 아니다. 근거가 없을 때만 "문장"이다
- 널리 알려진 사실을 재료에 없다고 거는 것. 이야기의 큰 줄기 · 인물 관계 · 시리즈 순서 · 필러가 생긴 까닭은 재료 밖이어도 "사실"로 잡지 않는다. 대신 확인 목록에 문장 그대로 적어 낸다. 점수를 깎지 않는다. 사람이 나중에 본다

[앞 판이 있으면]
[앞 판 판정]과 [앞 판 글]이 붙어 오면 [이번 글]은 그 판정을 받고 다시 쓴 글이다. 처음 보는 것처럼 채점하지 않는다. 채점하는 것은 [이번 글]이다. [앞 판 글]은 대조하려고 주는 것이다.
- [이번 글] 머리에 판 종류가 적혀 있다. "고침"이면 작가가 앞 글을 받아 지적된 자리만 손본 것이다. 두 글을 문단 단위로 나란히 놓고 바뀐 자리를 본다. "새로 씀"이면 앞 판 문제 목록만 받고 기획자부터 다시 쓴 것이다. 앞 판 문제가 다른 문장으로 옮겨 와 남았는지 본다.
- 앞 판 문제마다 이번 글에서 고쳐졌는지 본다. 앞판대조에 한 줄씩 적는다. "고쳐짐 — 무엇", "남음 — 무엇". 남은 것은 문제들에 다시 넣는다. 고쳐진 것은 다시 잡지 않는다.
- 이번 글에서 새로 생긴 문제는 "새로 생김 — 무엇"으로 적고 문제들에 넣는다. 앞 판 글에도 그대로 있었는데 앞 판 판정에 없는 문제면 "앞 판에서 못 봄 — 무엇"으로 적고 문제들에 넣는다. 둘을 섞지 않는다. 새로 생김은 글이 나빠진 것이고, 못 봄은 앞 판 채점이 놓친 것이다.
- 점수는 앞 점수에서 출발한다. 고쳐진 것이 있고 새로 생긴 문제가 없으면 앞 점수보다 낮게 주지 않는다. 새 문제가 생겼거나 고친 자리가 다른 자리를 망가뜨렸으면 내릴 수 있다. 내리면 점수설명에 어느 문제 때문인지 적는다. 못 봄만 있고 새로 생김이 없으면 내리지 않는다. 그 문제는 앞 판 점수에 이미 들어 있었어야 했다.
- 점수설명은 한 줄이다. "앞 판 62점. 구조 둘 고쳐짐, 되풀이 남음, 새 문제 없음. 71점"처럼.
앞 판이 없으면 앞판대조와 점수설명은 비워 둔다.

[정하는 법]
종류는 사실 · 문장 · 구조 셋이고, 그 밖에 확인 목록과 읽을 이유 한 줄을 낸다.
사소한 것 하나로 반려하지 않는다. 읽는 사람이 걸려 넘어질 것만 적는다.
문제마다 "어느 문단 어느 문장 — 왜" 꼴로 적는다.
  예: 1문단 "필러 표마다 셈은 조금 다르다" — 독자는 표를 못 봤다. 어느 표가 맞는지가 아니라 어디서 끊을지를 알고 싶다
  예: 4문단 "이야기의 범위가 점차 넓어진다" — 어느 이야기가 어디로 넓어지는지 없다. 아무 작품에나 맞는 말이다
  예: 7문단 "그래서 답이다. 720화를 다 볼 필요는 없다. … 127화로 줄이고" — 2문단을 그대로 다시 적는다. 127화가 세 번째다
  예: 6문단 "뒤에 보루토가 있으니 제목대로 되지 않는다는 것은 열기 전에 이미 안다" — 극장판의 결말을 뒤 작품으로 짐작하게 한다
직접 고치지 않는다. 올릴지 말지도 정하지 않는다. 점수와 문제 목록과 확인 목록과 읽을 이유를 낸다."""

def 주문_text(온도, 시작점, 배치, 접근=""):
    """기획자 · 작가 · 편집국장이 같은 것을 받는다. 낱말만 넘기지 않고 뜻까지 넘긴다."""
    줄 = [f"온도 {온도} · 시작점 {시작점} · 배치 {배치}" + (f" · 접근 {접근}" if 접근 else ""),
          온도글[온도].strip(),
          f"[시작점 — {시작점}]\n{시작점글[시작점]}",
          f"[글 배치 — {배치}]\n{배치글[배치]}"]
    return "\n\n".join(줄)

print("글 프롬프트 준비 끝. 온도", len(온도글), "· 시작점", len(시작점글), "· 배치", len(배치글))

# ## 15. 기획자
#
# 재료를 받아 뼈대를 짠다. 문장은 안 쓴다. 정보형은 독자의 질문과 답을 먼저 정한다.
# 두 번째부터는 지난 번 편집국장 문제 목록을 받는다. 문장 · 구조 문제만 있으면 기획자를 안 부르고 작가가 앞 글을 고친다.

class 기획주문(BaseModel):
    유형: str
    모양: str
    주제한줄: str
    작품정보: str
    표기표: str
    사실표: str = ""
    이야기: str = ""
    관점표: str = ""
    발췌: str = ""
    발언: str = ""
    맥락: str = ""
    순서표: str = ""
    온도: str
    시작점: str
    배치: str = "답을 끝에"
    접근: str = ""
    평개수: int = 0
    지난문제: str = ""

class 인용계획(BaseModel):
    매체: str
    옮긴문장: str = Field(description="한국어로 옮긴 문장. 원문이 한국어면 그대로")
    링크: str

class 문단계획(BaseModel):
    할말: str = Field(description="이 문단이 말하는 것. 관찰 · 계산 · 주장 한 문장. '~다'로 끝난다. 'X편은 N~M화'는 할 말이 아니다")
    사건: str = Field(default="", description="이 문단에서 꺼낼 장면 하나. 회차 줄거리 · 등장인물 절에서. '8화에서 카카시가 물감옥에 갇힌다' 꼴. 없으면 빈 문자열")
    쓸이름: List[str] = Field(default_factory=list, description="이 문단에 나오는 사람 · 작품 · 캐릭터 이름. 표기표 · 재료에 적힌 한국 표기 그대로")
    쓸사실: List[str] = Field(description="이 문단에서 쓸 사실. '사실 ← 출처' 꼴. 출처는 사실표 / 이야기 / 관점표 / 발췌 / 발언 / 맥락 / 앎")
    쓸관점: List[str] = Field(default_factory=list, description="이 문단이 기대는 남의 관점. 관점표에서 그대로. 없으면 빈 목록")
    해석: str = Field(default="", description="이 문단이 장면에서 읽어 내는 것 한 문장. 줄거리가 아니라 그 장면이 왜 거기 있는지 · 무엇을 미리 심는지 · 인물의 무엇이 드러나는지. 근거 장면이 사건 칸에 있어야 한다. 없으면 빈 문자열")

class 개요(BaseModel):
    주제: str = Field(description="독자의 질문 한 줄. '왜' 또는 '어떻게'가 들어간다. 물음으로 끝난다")
    답: str = Field(default="", description="그 질문의 답 한 줄. 독자가 읽기 전에 몰랐던 것. 심층형은 글이 하려는 말 한 줄")
    해석들: List[str] = Field(default_factory=list, description="글 전체의 해석. '장면 — 그 장면이 하는 일 — 근거' 꼴. 둘 이상")
    인용둘: List[인용계획] = Field(default_factory=list, max_length=2)
    문단들: List[문단계획] = Field(min_length=3, max_length=9)
    사실목록: List[str] = Field(description="글 전체에 쓸 사실 전부. '사실 ← 출처' 꼴. 출처는 사실표 / 이야기 / 관점표 / 발췌 / 발언 / 맥락 / 앎 중 하나")

기획자 = Agent(MODEL, deps_type=기획주문, output_type=개요)

@기획자.system_prompt
def _기획프롬프트(ctx) -> str:
    d = ctx.deps
    지난 = f"\n[지난 글에서 편집국장이 건 것]\n{d.지난문제}\n이 문제가 안 나게 뼈대를 새로 짠다. 지난 글은 안 본다." if d.지난문제 else ""
    주문 = 주문_text(d.온도, d.시작점, d.배치, d.접근)
    if d.모양 == "정보":
        재료칸 = (f"[사실표 — 날짜 · 숫자 · 이름은 여기 있는 것만]\n{d.사실표}\n\n"
                  f"[이야기 — 회차 줄거리 · 줄거리 절 · 등장인물 절. 사건과 인물은 여기서 캐낸다. 이 글의 살이다]\n{d.이야기 or '(없음)'}\n\n"
                  f"[맥락 — 위키백과. 지금 상태의 정보다. 연도가 같이 적힌 것만 당시 사실로 쓴다]\n{d.맥락 or '(없음)'}")
        if d.관점표:
            재료칸 += (f"\n\n[남의 글 — 리뷰 · 인터뷰에서 캔 것. \"장면:\" 줄이 장면메모, \"발언:\" 줄이 제작진 · 성우의 말. "
                      f"장면은 사건 칸의 재료이고 남의 해석은 네 해석의 근거다. 남의 평은 매체 이름과 함께만 쓴다]\n{d.관점표}"
                      f"\n\n[원문 발췌 — 장면 · 발언을 여기서 더 캔다]\n{d.발췌 or '(없음)'}")
        규칙 = 기획자_정보형_규칙.replace("{분량기준}", str(분량기준))
        if d.유형 == "사람":
            규칙 += "\n\n[사람]\n- 참여작 목록을 읽어 주는 문단은 하나까지다. 이 시리즈의 배역이 어느 회차에 무엇을 했는지(이야기)가 글의 가운데다. 배역 표기가 없는 작품은 작품 이름만 쓴다."
        if d.유형 == "기념일":
            규칙 += "\n\n[기념일]\n- 첫 문단에 무엇의 몇 주년(또는 누구의 생일)인지가 나온다. 당시 반응은 사실표의 평가 절에 있는 것만. 생일 글이면 그 캐릭터가 회차에서 무엇을 했는지, 주년 글이면 작품이 어디서 시작해 어디까지 갔는지가 가운데다."
    else:
        재료칸 = (f"[관점표 — 남의 평은 여기 있는 것만. \"발언:\" 줄이 제작진의 말, \"장면:\" 줄이 장면메모다]\n{d.관점표 or '(없음)'}\n\n"
                  f"[원문 발췌 — 사실과 장면 · 발언을 여기서 캐낸다. 판단은 글쓴이 것이고 사실은 가져다 쓴다]\n{d.발췌 or '(없음)'}\n\n"
                  f"[제작진 · 성우의 말 — \"감독은 ~라고 했다\"로 쓸 수 있다]\n{d.발언 or '(없음)'}\n\n"
                  f"[맥락 — 위키백과. 지금 상태의 정보다]\n{d.맥락 or '(없음)'}")
        if d.이야기:
            재료칸 += f"\n\n[이야기 — 회차 줄거리 · 줄거리 절 · 등장인물 절. 발언과 평을 붙일 장면을 여기서 캔다]\n{d.이야기}"
        평하나 = "\n- 관점표에 매체가 하나뿐이다. 그 매체의 판단을 이 글의 결론으로 삼지 않는다. \"평론가들은\", \"평단은\"처럼 여럿의 평인 것처럼 적지 않는다. 매체 이름을 밝히고 그 매체가 봤다고 적는다." if d.평개수 <= 1 else ""
        규칙 = f"""[규칙 — 심층형 기획자]
- 주제는 독자의 질문 한 줄이다. 답 칸에는 이 글이 하려는 말 한 줄을 적는다. 접근({d.접근 or '분석'})이 글의 중심이어야 한다.
- 사실목록은 작품 정보 · 관점표 · 발췌 · 발언 · 맥락에 있는 것만. 널리 알려진 이야기의 큰 줄기 · 인물 관계는 "← 앎"으로 적는다. 출처를 "사실 ← 관점표" 꼴로 적는다.
- 장면 · 발언이 제일 중요하다. 어느 화 어느 장면에서 무엇이 어떻게 됐는지, 감독 · 원작자 · 성우가 무슨 말을 했는지를 사건 칸과 사실목록에 이름과 함께 넣는다.
- 문단마다 해석 칸을 채운다. 그 장면이 하는 일, 발언이 화면 어디에서 어떻게 보이는지를 네가 읽어 낸다. 남의 해석은 쓸관점에 매체 이름과 함께. 해석들 칸에 글 전체의 해석을 둘 이상.
- 문단마다 사건 하나. 회차 · 장면 · 발언 · 연도 · 사람 이름. 문단 수는 답에 필요한 만큼이다. 셋에서 아홉.
- 인용은 두 개까지. 관점표의 인용 중에서. 영어 · 일본어면 한국어로 옮기고 한국어면 그대로. 한 문장이다. 인용의 매체는 관점표에 적힌 이름 그대로. "한 매체"면 그대로 "한 매체"다.
- 이름은 표기표에 적힌 한국 표기 그대로. 표기표에 없는 사람 · 캐릭터는 이름 없이 역할로만 부른다("감독은", "주인공은").
- 같은 말을 문단마다 되풀이하지 않는다. 재료가 없다 · 정보가 제한된다는 말은 어느 문단에도 넣지 않는다.
- 결말을 밝히지 않는다. 마지막 화의 사건은 쓰지 않는다.
- 마지막 문단은 글쓴이 자신의 판단으로 닫는다. 여러 평을 합쳐 "가장 ~한 작품 중 하나다"처럼 정리하지 않는다.
- 온도 · 시작점 · 배치의 뜻을 읽고 뼈대에 반영한다. 첫 문단의 할 말은 시작점에 맞아야 한다.{'- 제작 이야기 글이다. 발언이 글의 가운데다. 발언이 하나도 없는 문단은 셋 중 하나를 넘기지 않는다.' if d.유형 == '제작이야기' else ''}{평하나}"""
    return f"""너는 애니 글의 뼈대를 짠다. 문장은 안 쓴다. 무엇을 어떤 차례로 말할지, 어떤 사건과 이름으로 말할지를 정한다.
작가는 네가 적어 준 사실과 이름을 쓰고, 널리 알려진 작품이면 자기가 확실히 아는 이야기의 큰 줄기도 쓴다. 네가 줄 것은 사실 목록이 아니라 이 글이 무엇을 말할지다.

[유형] {d.유형} ({d.모양}형)
[주제 후보] {d.주제한줄} — 이것을 독자의 질문으로 바꿔 주제 칸에 적는다

[작품]
{d.작품정보}

[표기표 — 이름은 이것대로만]
{d.표기표 or '(없음)'}

{('[순서표 — 코드가 계산한 순서. 근거로만 쓴다. 다 부르지 않아도 된다. 부르는 것끼리의 순서와 갈래는 지킨다]' + chr(10) + d.순서표 + chr(10) + chr(10)) if d.순서표 else ''}{재료칸}

[이번 글의 주문]
{주문}

{규칙}{지난}"""

print("기획자 준비 끝")

# ## 16. 작가
#
# 개요만 받아 문장으로 쓴다. 재료 원문은 안 본다. 널리 알려진 작품이면 자기가 확실히 아는 이야기의 큰 줄기 · 인물 관계는 쓴다.
# 다시 쓸 때는 앞 글과 문제 목록을 받아 지적된 부분만 고친다. 사실 문제였으면 기획자가 새로 짠 개요로 처음부터 쓴다.

class 집필주문(BaseModel):
    유형: str
    모양: str
    주제한줄: str
    표기표: str
    개요: 개요
    온도: str
    시작점: str
    배치: str = "답을 끝에"
    접근: str = ""
    순서표: str = ""
    앞글: str = ""
    문제목록: str = ""

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
    문단 = "\n".join(f"{i+1}. {p.할말}" + (f"\n   사건: {p.사건}" if p.사건 else "") + (f"\n   해석: {p.해석}" if p.해석 else "") + f"\n   이름: {', '.join(p.쓸이름) or '없음'}"
                    f"\n   사실: {'; '.join(_사실만(x) for x in p.쓸사실) or '없음'}" + (f"\n   관점: {'; '.join(p.쓸관점)}" if p.쓸관점 else "")
                    for i, p in enumerate(o.문단들))
    해석들 = "\n".join("- " + x for x in o.해석들) or "없음"
    인용 = "\n".join(f"- {q.매체}: {q.옮긴문장}" for q in o.인용둘) or "없음"
    순서 = f"\n[순서표 — 본편이 나오는 차례다. 부르는 것끼리만 이 순서를 지킨다. 다 부르지 않아도 된다]\n{d.순서표}\n" if d.순서표 else ""
    접근 = f"\n[접근] {d.접근}\n" if d.접근 else ""
    parts = [작가_공통, 유형글[d.유형], 주문_text(d.온도, d.시작점, d.배치, d.접근),
             f"""[글의 주제 한 줄] {o.주제}
[답] {o.답 or '(없음)'}
{접근}{순서}
[표기표 — 이름은 이것대로만. 여기와 사실 목록에 없는 이름은 쓰지 않는다]
{d.표기표 or '(없음)'}

[문단 계획 — 칸이 아니라 차례 메모다. "해석:"이 그 문단에서 네가 말할 것이다]
{문단}

[이 글의 해석 — 이것이 글의 뼈다. 줄거리는 이것을 받치는 자리에만 나온다]
{해석들}

[쓸 수 있는 인용 — 이 문장 그대로]
{인용}

[쓸 수 있는 사실 — 날짜 · 화수 · 연도 · 제작사는 이 밖의 것을 쓰지 않는다]
{chr(10).join("- " + _사실만(f) for f in o.사실목록)}

[분량]
{분량기준}자 안팎. {분량하한}자보다 짧거나 {분량상한}자보다 길면 안 된다. 문단은 빈 줄로 나눈다. 제목 줄에도 작품 이름은 《 》로 감싼다."""]
    if not 교정자쓰기:
        # v0.5: 교정자 마디를 안 부른다. 다 쓴 뒤 스스로 한 번 읽는다
        parts.append("[마지막 손질 — 교정자가 따로 없다. 다 쓴 뒤 한 번 읽고 아래가 보이면 고쳐서 낸다. 뜻 · 사실 · 판단은 안 바꾼다]\n" + 교정규칙)
    if d.문제목록:
        block = "[다시 쓰기]\n앞 글이 아래 이유로 통과하지 못했다.\n" + d.문제목록
        if d.앞글:
            block += "\n앞에 쓴 글은 이것이다.\n---\n" + d.앞글 + "\n---\n지적된 부분만 고친다. 통과한 부분은 그대로 둔다."
        else:
            block += "\n앞 글은 주지 않는다. 같은 구조로 돌아가지 말고 처음부터 다시 쓴다."
        parts.append(block)
    return "\n\n".join(p.strip() for p in parts)

print("작가 준비 끝")

# ## 17. 교정자
#
# 작가 글을 받아 문장 짜임만 고친다. 뜻과 사실은 못 바꾼다. 음악 v2.9와 같다.

class 교정본(BaseModel):
    본문: str
    고친곳: List[str] = Field(description="무엇을 어떻게 고쳤는지 한 줄씩")

교정규칙 = """번역투는 영어 문장을 그대로 옮긴 것처럼 읽히는 문장이다. 아래가 그것이다. 보이면 고친다.
1. "~는 것이 아니라 ~다" 틀. 앞을 빼고 뒤만 말하거나, 두 문장으로 나눈다. 한 편에 한 번은 둔다.
2. 한 문장에 절이 셋 넘게 겹쳐서 끝까지 가야 뜻이 잡히는 것. 뜻 단위로 끊는다.
3. 물건이 주어로 서서 스스로 무언가를 하는 것. "연출이 놓인다", "작화가 이야기를 밀어간다". 사람이나 화면에서 실제로 일어나는 일로 바꾼다.
4. 결론을 명사로 닫는 것. "~하는 이유다", "~의 결과에 가깝다", "~하는 방식이다". 동사로 끝낸다.
5. "~에 그치지 않는다", "~에 머물지 않는다", "단순히 ~가 아니다". 뒤에 오는 말만 남긴다.
6. "가장 먼저 ~하는 것은 ~다", "~을 만드는 것은 ~다" 강조 틀. 주어를 앞으로 빼서 보통 문장으로 만든다.
7. 물건이 주어가 되어 비유를 하는 것. 실제로 보이는 것으로 바꾼다.
8. "~라기보다 ~으로 보는 편이 맞다", "~라는 데 있다", "~을 제시한다", "설득력을 얻는다", "유효하다". 판단을 동사로 바로 말한다.
9. "~게 되다"("헷갈리게 되는데") → "~다"("헷갈린다").

같이 본다.
- 한자어 동사(작동한다, 기능한다, 구축한다, 형성한다)는 일상어로. 한다, 된다, 만든다, 낸다.
- "~적", "~에 있어", "~에 대한", "~을 통해", "~에 의해"는 뺀다.
- 손에 안 잡히는 말(밀도, 결, 층위, 서사, 정체성, 세계관 구축, 몰입감, 완성도, 미학)은 그 문장이 실제로 가리키는 것으로 바꾼다. 가리키는 것이 문장에 없으면 그 말만 뺀다.
- 사람 · 작품 · 캐릭터 · 편 이름은 손대지 않는다. 다만 잘못 적힌 것은 바로잡고 고친 곳에 적는다.
- 영어 · 일본어 문장이 그대로 있으면 한국어로 옮긴다. 이름은 옮기지 않는다."""

교정자 = Agent(
    MODEL,
    output_type=교정본,
    system_prompt="너는 한국어 문장을 다듬는다. 뜻과 사실과 판단은 하나도 안 바꾼다. 문장 짜임만 고친다. 글쓴이의 농담과 관찰은 그대로 둔다.\n\n" + 교정규칙 + "\n\n문장 길이는 내용이 정한다. 짧은 것을 억지로 늘리거나 긴 것을 억지로 자르지 않는다. 고친 곳마다 한 줄로 적는다.",
)

print("교정자 준비 끝")

# ## 18. 형태 검사 (코드)
#
# 교정자가 놓친 것을 잡는다. 모델을 안 부른다. 걸림은 반려, 경고는 편집국장에게만 넘긴다.
# 영화 에이전트의 표 옮겨 적기 · 되풀이 · 재료 말투 검사를 애니에 맞게 넣었다(v0.3).

존댓말 = re.compile(r"(습니다|합니다|입니다|세요|네요|해요|이에요|예요|드립니다)")
흐림 = re.compile(r"(인 듯하다|인 듯싶다|일 수도 있다|라고 할 수 있다|인 것 같다|일지도 모른다|라고 볼 수 있다|아닐까|로 보인다|으로 보인다|일 것이다|할 것이다|에 가깝다|인 셈이다|는 편이다)")
부추김 = re.compile(r"(꼭 봐야|반드시 봐야|필견|강력히|추천한다|놓치지 마|정주행을 권|입문작으로 추천|역대급|레전드|갓작|띵작|놓치면 후회|올해 최고)")
번역틀 = re.compile(r"(것이 아니라|라기보다|하는 방식이다|하는 이유다|하는 지점|에 있어|을 통해|를 통해|에 의해"
                    r"|그치지 않|머물지 않|단순히 .{1,12}가 아니|는 것은 .{1,20}다\.|편이 맞다|데 있다|데서 생긴다|을 제시한다|를 제시한다|설득력을 얻|유효하다)")
추상어 = re.compile(r"(서사|정체성|밀도|층위|몰입감|완성도|미학|긴장감|다채로움|보편성|세계관 구축|카타르시스)")
다수평 = re.compile(r"(평론가들은|평단은|비평가들은|많은 이들이|대체로 .{0,6}평가|호평이 이어|평이 갈렸|팬들 사이에서|팬들은)")
재료사정 = re.compile(r"(확인할 수 있는 정보|확인할 수 없|알 수 없다는 점|정보(는|가) .{0,12}제한|AniList|Jikan|MyAnimeList|자료가 (없|부족)|단정할 수는 없|판단의 범위|표기가 없"
                      r"|순서표|사실표|표기표|용어집|관점표|회차표|필러 표|위키|올라와 있|올라와있|로 표시돼|표기를 기준으로|에 포함된다|확인할 수 있다|목록에는|순서에 (?:적힌|올라)|번호를 달고|표마다)")
자기말 = re.compile(r"(답은 (?:맨 |글 )?(?:끝|뒤)에 있|그래서 답이다|이 글(?:은|이) .{0,20}(?:글이다|답한다)|그 앞은 전부|앞서 말한|위 숫자|아래 숫자)")
괄호인용 = re.compile(r"\(\s*[가-힣A-Za-z .!'’]{2,20}\s*\)\s*[“\"]")
따옴표안 = re.compile(r"[“\"]([^”\"]{10,})[”\"]")
영문덩어리 = re.compile(r"[A-Za-z][A-Za-z ,.'’\-]{25,}")
로마자이름 = re.compile(r"\b[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})+\b")
일본어 = re.compile(r"[぀-ヿ一-鿿]{2,}")
화수범위 = re.compile(r"\d+\s*~\s*\d+\s*화|\d+\s*화(?:부터|에서)\s*\d+\s*화(?:까지)?")
편표문장 = re.compile(r"[가-힣 ]{2,20}편은\s*(?:질풍전\s*)?\d+\s*~\s*\d+\s*화(?:로|이며|이고|다|에)")
편머리 = re.compile(r"^[가-힣 ]{2,20}편\s*(?:질풍전\s*)?\d+\s*~\s*\d+\s*화(?:에서|에|은|는)")
회차머리 = re.compile(r"^\d+\s*화(?:에서|에|는)")
극장판화수 = re.compile(r"극장판[^.《》]{0,30}\d+\s*화|\(\s*(?:19|20)\d{2}\s*,\s*극장판|(?:19|20)\d{2}년\s*극장판\s*1화")
잇는말 = re.compile(r"^(?:이후|이어서|그다음에는|그 뒤에|그 뒤로|한편|결국|따라서|그러고 나서)")
순서읽기 = re.compile(r"(?:다음에 (?:는 )?《[^》]+》(?:을|를) (?:본다|보면 된다)|차례로 보면|순서대로 보면|이어서 보면 된다)")
결말낱말 = re.compile(r"(?:에게 죽는다|사망한다|목숨을 잃는다|최종 결전|마지막 싸움에서 .{0,10}(?:이긴다|진다))")
전부말 = re.compile(r"(?:여기뿐|유일하|하나도 없|어느 쪽도)")
질문표시 = re.compile(r"\?|(?:봐야|봐도|해도|되|보|하|이|인)(?: 하)?(?:나|냐)[.?]|(?:냐|나)다\.|어디(?:서부터|까지|쯤)|무엇부터|언제 끼워|몇 화")
답표시 = re.compile(r"(?:그래서|답은|다 볼 필요|멈출 자리|남는 것은|보면 된다|보면 되|남는다|남긴다|끊|까지다|하면 된다)")

def _세기(xs):
    d = {}
    for x in xs:
        k = re.sub(r"\s", "", x)
        d[k] = d.get(k, 0) + 1
    return d

def _문장들(p):
    return [x.strip() for x in re.split(r"(?<=[.?!])\s+", p) if x.strip()]

def _문단겹침(문단, n=14):
    """두 문단이 n자를 그대로 나눠 가지면 그 조각을 돌려준다. 《제목》만 겹치는 것은 안 잡는다."""
    조각들 = []
    for p in 문단:
        조각들.append({p[i:i+n] for i in range(max(0, len(p)-n+1))})
    for i in range(len(조각들)):
        for j in range(i+1, len(조각들)):
            공통 = [c for c in (조각들[i] & 조각들[j]) if not re.search(r"《[^》]*》?$", c) and "》" not in c]
            if 공통:
                return sorted(공통)[0]
    return ""

def _겹침(a, b, n=12):
    A = {a[i:i+n] for i in range(max(0, len(a)-n+1))}
    B = {b[i:i+n] for i in range(max(0, len(b)-n+1))}
    return len(A & B) / len(A) if A and B else 0.0

def _문단안겹침(p, n=12):
    문장 = _문장들(p)
    for a, b in zip(문장, 문장[1:]):
        a2, b2 = re.sub(r"《[^》]*》", "", a), re.sub(r"《[^》]*》", "", b)
        if len(a2) >= n and len(b2) >= n and _겹침(a2, b2, n) > 0.3:
            return a[:30]
    return ""

def 형태검사(본문, st, 제목=""):
    걸림, 경고, t = [], [], 본문
    모양, 유형 = st.모양, st.유형
    t_제목뺌 = re.sub(r"《[^》]*》", "", t)
    문단 = [p.strip() for p in t.split("\n") if p.strip()]
    문장 = [x for p in 문단 for x in _문장들(p)]
    첫문단, 끝문단 = (문단[0] if 문단 else ""), (문단[-1] if 문단 else "")
    if 존댓말.search(t):
        걸림.append("존댓말")
    if not re.search(r"[.!?…\"'》]\s*$", t.strip()):
        걸림.append("문장 끊김")
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
    if 제목:
        작품이름들 = [v["표기"] for v in st.용어표.확정().values() if v["표기"] and v["표기"] in 제목]
        if 작품이름들 and "《" not in 제목:
            걸림.append("제목에 《 》가 없다")
    # 문장
    흐 = 흐림.findall(t)
    if len(흐) >= 2:
        걸림.append("판단 흐리는 끝맺음: " + ", ".join(dict.fromkeys(흐)))
    틀 = 번역틀.findall(t)
    if len(틀) >= 3:
        걸림.append("번역투 틀: " + ", ".join(dict.fromkeys(틀)))
    if len(re.findall(r"(?:가|이) 아니라", t)) >= 2:
        걸림.append("'~가 아니라' 되풀이")
    if re.search(r"게 되(?:는데|고|어|었|며|지만)", t):
        걸림.append("'~게 되다' 번역투")
    추 = 추상어.findall(t)
    if len(추) >= 4:
        걸림.append(f"추상어 {len(추)}개: " + ", ".join(dict.fromkeys(추)))
    if 재료사정.search(t):
        걸림.append("재료 말투: " + 재료사정.search(t).group(0))
    if re.search(r"[←\[\]]|라프텔:", t):
        걸림.append("재료 표시가 글에 있다")
    if 자기말.search(t):
        걸림.append("글이 자기 짜임을 말한다: " + 자기말.search(t).group(0))
    if 괄호인용.search(t):
        걸림.append("괄호 인용 표기")
    for q in 따옴표안.findall(t):
        if len(re.findall(r"[.!?]", q)) >= 2 or len(q) > 120:
            걸림.append("인용이 길다"); break
    if re.search(r"\(\s*[a-z0-9.-]+\.[a-z]{2,}\s*\)", t):
        걸림.append("도메인이 글에 나온다")
    if "!" in t_제목뺌:
        걸림.append("느낌표")
    if 부추김.search(t):
        걸림.append("부추기는 말: " + 부추김.search(t).group(0))
    if re.search(r"[가-힣]{2,5}\s*·\s*[가-힣]{2,5}\s*·\s*[가-힣]{2,5}", t):
        걸림.append("이름을 가운뎃점으로 이었다")
    # 되풀이
    for tok, cnt in _세기(re.findall(r"\d+\s*화|(?:19|20)\d{2}년", t_제목뺌)).items():
        if cnt >= 3:
            걸림.append(f"같은 숫자 되풀이: {tok} {cnt}번"); break
    if len(re.findall(r"(?:보|하|가|되|두|놓|얹|보태)면 된다", t)) >= 2:
        걸림.append("'~하면 된다' 되풀이")
    겹 = _문단겹침(문단)
    if 겹:
        걸림.append("문단 사이 되풀이: " + 겹)
    for p in 문단:
        g = _문단안겹침(p)
        if g:
            걸림.append("같은 문단 안 되풀이: " + g); break
        if p.count("는데") >= 2:
            걸림.append("'~는데'가 한 문단에 둘"); break
    잇 = [x for x in 문장 if 잇는말.match(x)]
    if 문장 and (len(잇) / len(문장) > 0.2 or any(sum(1 for x in _문장들(p) if 잇는말.match(x)) >= 2 for p in 문단)):
        걸림.append("사건 잇는 말 되풀이")
    if len(문장) >= 10:
        L = [len(x) for x in 문장]
        평균 = sum(L) / len(L); 편차 = (sum((l - 평균) ** 2 for l in L) / len(L)) ** 0.5
        if 평균 and 편차 / 평균 < 0.3:
            걸림.append("문장 길이가 같다")
    # 문단 여는 법
    머리 = [p.split()[0] for p in 문단 if p.split()]
    if 머리 and len(set(머리)) < len(머리) * 0.7:
        걸림.append("문단 시작 반복")
    if 문단 and sum(1 for p in 문단 if p.startswith("《")) > len(문단) * 0.4:
        걸림.append("문단을 작품명으로 연다")
    if any(a == b for a, b in zip(머리, 머리[1:])):
        걸림.append("같은 주어로 문단을 연속 열었다")
    for p in 문단:
        첫 = _문장들(p)[0] if _문장들(p) else ""
        if re.fullmatch(r"《[^》]+》\s*(?:은|는)\s*(?:19|20)\d{2}년.{0,30}(?:다|이다)\.", 첫):
            걸림.append("연도 구호로 문단을 열었다"); break
    # 표 옮겨 적기
    범위들 = 화수범위.findall(t)
    표문장 = [x for x in 문장 if 화수범위.search(x)]
    if 문장 and len(표문장) / len(문장) > (0.3 if 유형 == "감상순서" else 0.15):
        걸림.append(f"표 옮겨 적기 ({len(표문장)}/{len(문장)} 문장)")
    if len(범위들) > 4:
        걸림.append(f"화수 범위가 많다 ({len(범위들)}개)")
    쌍 = _세기([re.sub(r"[^\d~]", "", x) for x in 범위들])
    if any(c >= 2 for c in 쌍.values()):
        걸림.append("화수 범위 되풀이")
    if len(편표문장.findall(t)) >= 3 or any(len(편표문장.findall(p)) >= 2 for p in 문단):
        걸림.append("편 표 문장 되풀이")
    if any(편머리.match(x) for x in 문장):
        걸림.append("편 이름과 화수가 문장 머리에 있다")
    if any(회차머리.match(a) and 회차머리.match(b) for a, b in zip(문장, 문장[1:])):
        걸림.append("회차 줄거리를 차례로 옮겼다")
    if 극장판화수.search(t):
        걸림.append("극장판에 화수를 붙였다")
    if 순서읽기.search(t):
        걸림.append("순서를 읽어 주는 문장")
    # 정보형 — 사실 대조 · 질문과 답 · 셈
    if 모양 == "정보":
        if 다수평.search(t) and "평가 절" not in (st.사실표 or "") and len(set(r["매체"] for r in st.재료)) <= 1:
            걸림.append("없는 반응: " + 다수평.search(t).group(0))
        재료글 = (st.사실표 or "") + (st.맥락 or "") + (st.이야기 or "") + (st.관점표 or "") + "".join(r["원문"] for r in st.재료)
        재료숫자 = set(re.findall(r"\d{4}", 재료글))
        for y in set(re.findall(r"(?<!\d)((?:19|20)\d{2})(?=년)", t)):
            if y not in 재료숫자:
                걸림.append(f"재료에 없는 연도: {y}년")
        범위 = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*~\s*(\d+)\s*화", 재료글)]
        상한 = max([b for _, b in 범위] + [int(x) for x in re.findall(r"(\d+)\s*화", 재료글)] + [0])
        계산동사 = re.compile(r"(빼면|덜어 내면|덜면|남는다|남긴다|합치면|더하면|얹으면|보태면)")
        for x in 문장:
            for n in re.findall(r"(?<![\d~])(\d+)\s*화", x):
                if int(n) > 상한 and not 계산동사.search(x):
                    걸림.append(f"재료에 없는 화수: {n}화"); break
            else:
                continue
            break
        if 결말낱말.search(t):
            걸림.append("결말 낱말: " + 결말낱말.search(t).group(0))
        if st.시작점 in ("질문", "보려는 사람", "되묻기", "숫자 하나에서") and not 질문표시.search(첫문단):
            걸림.append("첫 문단에 질문이 없다")
        if not 답표시.search(끝문단):
            걸림.append("마지막 문단에 답이 없다")
        앞 = "\n".join(문단[:-1])
        새제목 = [x for x in re.findall(r"《[^》]+》", 끝문단) if x not in 앞]
        if 새제목:
            걸림.append("마지막 문단에 새 작품: " + 새제목[0])
        물음 = [x for x in _문장들(첫문단) if re.search(r"(?:나|하나)[.?]$", x)]
        if 물음 and t.count(물음[0][-8:]) >= 3:
            걸림.append("물음 되풀이")
        if st.순서표:
            이름들 = [r["이름"] for r in st.순서표]
            if len([n for n in 이름들 if n in 끝문단]) > 3:
                걸림.append("마지막 문단이 목록을 되읽는다")
            for r in st.순서표:
                if t.count(f"《{r['이름']}》") > (5 if r["갈래"] == "본편" else 2):      # 《 》로 감싼 것만 센다. "나루토"는 인물 이름이기도 하다
                    걸림.append("순서표 이름 되풀이: " + r["이름"]); break
            본편 = [(r["이름"], t.find(r["이름"])) for r in st.순서표 if r["갈래"] == "본편"]
            나온 = [x for x in 본편 if x[1] >= 0]
            if any(b[1] < a[1] for a, b in zip(나온, 나온[1:])):
                걸림.append("순서표와 본문 순서가 다르다")
        if 유형 == "감상순서" and "[필러" in (st.사실표 or ""):
            셈 = set(int(n) for n in re.findall(r"(?:빼면|덜어 내면|덜면|얹으면|보태면)[^.]*?(\d+)\s*화", t))
            if len(셈) >= 2:
                걸림.append(f"볼 화수 셈이 둘이다: {sorted(셈)}")
            if not re.search(r"(빼면|덜어 내면|남는다|남긴다|실제로 볼)", t):
                걸림.append("볼 화수 계산이 없다")
            필러범위 = [(int(a), int(b)) for x in 문장 if "필러" in x for a, b in re.findall(r"(\d+)\s*~\s*(\d+)\s*화", x)]
            if any(a[0] <= b[1] and b[0] <= a[1] for i, a in enumerate(필러범위) for b in 필러범위[i+1:]):
                걸림.append("필러 구간을 두 번 세었다")
        if 유형 in ("감상순서", "사람", "기념일") and re.search(r"^- \d+화 ", st.이야기 or "", re.M):
            명사 = set(re.findall(r"[가-힣]{2,5}", "\n".join(l for l in (st.이야기 or "").splitlines() if re.match(r"^- \d+화 ", l))))
            명사 = {n for n in 명사 if n not in ("이야기", "그리고", "하지만", "그러나", "위해", "때문", "사이", "자신")}
            if len(명사) >= 8 and len([n for n in 명사 if n in t]) < 3:      # 회차 줄거리가 제목뿐이면 안 본다
                걸림.append("회차 장면이 글에 없다")
        if 전부말.search(t):
            경고.append("전부를 아우르는 말: " + 전부말.search(t).group(0))
        첫명사 = set(re.findall(r"[가-힣]{2,5}", 첫문단)) - {"이야기", "그리고", "하지만"}
        if 첫명사 and not any(n in 끝문단 for n in 첫명사):
            경고.append("첫 문단의 것이 마지막 문단에 안 돌아온다")
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
    for x in 문장:
        if x.count(",") >= 3 and len(x) > 60:
            경고.append("절이 많다: " + x[:40]); break
    if not (분량하한 <= len(t) <= 분량상한):
        걸림.append(f"분량 {len(t)}자")
    st.코드경고 = list(dict.fromkeys(경고))
    return list(dict.fromkeys(걸림))

print("형태 검사 준비 끝")

# ## 19. 편집국장
#
# 마지막에 전체를 다시 읽는다. 주문(온도 · 시작점 · 배치)대로 갔는지, 읽을 이유가 있는지, 사실 · 문장 · 구조를 본다.
# 직접 고치지 않는다. 올릴지도 정하지 않는다. 확인 목록(널리 알려진 사실인데 재료 밖인 것)은 점수를 안 깎고 사람에게 넘긴다.

class 문제(BaseModel):
    종류: Literal["사실", "문장", "구조"]
    어디: str = Field(description="어느 문단 어느 문장인지. 문장을 그대로")
    설명: str

class 편집판정(BaseModel):
    점수: int = Field(ge=0, le=100)
    문제들: List[문제]
    확인목록: List[str] = Field(default_factory=list, description="재료 밖이지만 널리 알려진 사실로 보이는 문장. 사람이 나중에 본다. 점수는 안 깎는다")
    읽을이유: str = Field(default="", description="이 글을 끝까지 읽은 사람이 얻어 가는 것 한 줄. 없으면 빈 문자열")
    한줄평: str
    앞판대조: List[str] = Field(default_factory=list, description="앞 판 판정이 있을 때만. 앞 판 문제마다 한 줄. '고쳐짐 — 무엇' / '남음 — 무엇'. 이번 글에서 새로 생긴 문제는 '새로 생김 — 무엇'. 앞 판 글에도 있었는데 앞 판에서 안 잡은 문제는 '앞 판에서 못 봄 — 무엇'")
    점수설명: str = Field(default="", description="앞 판 판정이 있을 때만. 앞 점수를 기준으로 왜 올랐는지 · 내렸는지 한 줄")

편집국장 = Agent(
    MODEL,
    output_type=편집판정,
    system_prompt="너는 편집국장이다. 완성된 애니 글을 마지막으로 읽는다.\n\n" + 편집국장_추가,
)

def 앞판_text(k):
    """v0.6: 앞 바퀴 기록 하나를 편집국장에게 줄 글로. 점수 · 문제 목록 · 형태 검사 · 한줄평 · 앞 판 글 본문."""
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
    if g is not None:                                   # 앞 판 글 본문. 대조용
        줄 += ["", f"[앞 판 글 — {k.get('차례', '?')}차]", g.제목, "", g.본문]
    return "\n".join(줄)

def 이번글_머리(차례, 종류, 앞판):
    """v0.6: 편집국장에게 주는 [이번 글] 머리. 앞 판이 있을 때만 차례와 판 종류를 붙인다."""
    if not (앞판기억 and 앞판):
        return "[글]"
    뜻 = {"고침": "작가가 앞 글을 받아 지적된 자리만 손본 것", "새로 씀": "앞 판 문제 목록만 받고 기획자부터 다시 쓴 것"}.get(종류, "")
    return f"[이번 글 — {차례}차 · {종류}" + (f" · {뜻}" if 뜻 else "") + "]"

def _편집국장_물음(st, 글):
    원문들 = "\n\n".join(f"### ({r['매체']}) {r['링크']}\n{r['원문'][:4000]}" for r in st.재료) or "(없음)"
    원문들 += "".join(f"\n\n### (발언 · {m['매체']}) {m['링크']}\n" + "\n".join("- " + x for x in m["말"]) for m in st.발언들)
    if st.모양 == "정보":
        재료 = f"[사실표]\n{st.사실표}\n\n[이야기]\n{(st.이야기 or '(없음)')[:9000]}\n\n[맥락]\n{(st.맥락 or '(없음)')[:3000]}"
        if st.재료:
            재료 += f"\n\n[남의 글 — 관점표]\n{st.관점표 or '(없음)'}\n\n[원문 — {len(st.재료)}편]\n{원문들}"
    else:
        재료 = f"[관점표]\n{st.관점표 or '(없음)'}\n\n[맥락]\n{(st.맥락 or '(없음)')[:3000]}\n\n[원문 — {len(st.재료)}편]\n{원문들}"
        if st.이야기:
            재료 += f"\n\n[이야기 — 회차 줄거리 · 줄거리 절]\n{st.이야기[:6000]}"
    순서 = ("\n\n[순서표]\n" + "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표)) if st.순서표 else ""
    경고 = ("\n\n[코드 경고 — 반려 사유는 아니다. 읽을 때 같이 본다]\n" + "\n".join("- " + x for x in st.코드경고)) if st.코드경고 else ""
    앞판 = st.기록[-1] if (앞판기억 and st.기록) else None
    앞 = (앞판_text(앞판) + "\n\n") if 앞판 else ""
    ask = (f"{앞}[주문]\n{주문_text(st.주문.온도, st.주문.시작점, st.주문.배치, st.주문.접근)}\n\n"
           f"[유형] {st.유형} ({st.모양}형)\n\n[작품]\n{작품_text(st.주제, st.용어표)}\n\n[표기표]\n{st.용어표.text()}{순서}{경고}\n\n"
           f"[개요]\n주제: {st.개요_.주제}\n답: {st.개요_.답}\n사실목록:\n" + "\n".join("- " + f for f in st.개요_.사실목록) + "\n\n"
           f"{재료}\n\n{이번글_머리(st.바퀴, st.판종류, 앞판)}\n{글.제목}\n\n{글.본문}")
    return ask

def 편집국장_읽기(st, budget):
    if budget["남은콜"] <= 0:
        return None
    try:
        r = 편집국장.run_sync(_편집국장_물음(st, st.글), usage_limits=UsageLimits(request_limit=2))
    except Exception as e:
        st.log(f"   편집국장 실패: {e}")
        return None
    budget["남은콜"] -= 1
    return r.output

async def _편집국장_여럿(st, 초안들):
    """v0.5: 초안 여럿을 한 루프에서 같이 읽는다. 실패한 것은 None."""
    async def one(g):
        try:
            r = await 편집국장.run(_편집국장_물음(st, g["글"]), usage_limits=UsageLimits(request_limit=2))
            return r.output
        except Exception as e:
            st.log(f"   편집국장 실패 ({g['온도']}): {e}")
            return None
    return await asyncio.gather(*(one(g) for g in 초안들))

def 올릴까(걸림, p):
    if 걸림:
        return False, "형태 검사: " + ", ".join(걸림)
    if p is None:
        return False, "편집국장 실패"
    사실 = [x for x in p.문제들 if x.종류 == "사실"]
    if 사실:
        return False, f"사실 문제 {len(사실)}건"
    if p.점수 < PASS_SCORE:
        return False, f"점수 {p.점수} / 문제 {len(p.문제들)}건" + (" / 읽을 이유 없음" if not p.읽을이유 else "")
    return True, f"점수 {p.점수}"

def 문제목록_글(걸림, p):
    줄 = [f"- (형태 검사) {x}" for x in 걸림]
    if p:
        줄 += [f"- ({x.종류}) {x.어디} — {x.설명}" for x in p.문제들]
    return "\n".join(줄)

print("편집국장 준비 끝")

# ## 20. 상태 하나 — RunState
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
    이야기: str = ""             # 정보형. 회차 줄거리 · 위키 줄거리 · 등장인물 절
    사실수: int = 0
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
    시작점: str = ""
    배치: str = ""
    접근: str = ""
    코드경고: list = []
    앞글: str = ""               # 문장 · 구조 문제로 다시 쓸 때 작가에게 주는 앞 글
    문제목록: str = ""
    주문: Optional[기획주문] = None
    개요_: Optional[개요] = None
    글: Optional[Article] = None
    고친곳: list = []
    걸림: list = []
    판정: Optional[편집판정] = None
    올림: bool = False
    이유: str = ""
    지난문제: str = ""
    판종류: str = "첫 판"        # v0.6: 이번 바퀴 글이 어떻게 나왔는지. 첫 판 / 고침 / 새로 씀. 편집국장에게 알려 준다
    # 세는 것
    바퀴: int = 0
    steps: int = 0
    남은콜: int = LLM_CALL_CAP
    # 결과
    기록: list = []
    상태: str = "진행"          # 진행 / 올림 / 탈락 / 주제없음 / 상한
    로그: list = []
    저장경로: Optional[str] = None
    # v0.5
    초안들: list = []            # 첫 바퀴 초안들 [{온도, 글, 걸림, 경고, 판정, 올림, 이유}]. 다시 쓰기 바퀴에서는 빈다
    표식: str = ""               # 동시에 돌 때 로그 앞에 붙는 "[유형] "
    진행잡음: bool = False       # 이 상태가 시리즈 진행 수(_진행시리즈)를 잡고 있는지

    def log(self, s):
        self.로그.append(s)
        print(self.표식 + s if self.표식 else s)

print("상태 준비 끝")

# ## 21. 마디
#
# 함수 하나가 단계 하나다. 상태를 받아 자기 칸만 채우고 돌려준다. 다른 마디를 안 부른다. 다음에 어디로 갈지 안 정한다.

def n_주제뽑기(st: RunState) -> RunState:
    st.모양 = 유형표[st.유형]
    if not st.후보목록 and st.지정후보:
        st.후보목록 = [st.지정후보]
    if not st.후보목록:
        st.후보목록 = 주제_후보목록(st.유형)
        st.log(f"  후보 {len(st.후보목록)}개 [{st.유형}]")
    _진행놓기(st)
    st.주제 = None
    st.사실표, st.이야기, st.순서표, st.맥락, st.맥락링크, st.사실링크, st.pages, st.links, st.blocked = "", "", [], "", [], [], [], [], []
    while st.후보번호 < len(st.후보목록):
        c = st.후보목록[st.후보번호]
        st.후보번호 += 1
        with _주제잠금:                                   # v0.5: 동시에 돌 때 같은 주제 · 같은 시리즈를 겹쳐 잡지 않는다
            if _주제키(c) in _쓴주제 and c.get("src") != "지정":
                continue
            if _올린시리즈.get(c["시리즈"], 0) + _진행시리즈.get(c["시리즈"], 0) >= 작품당상한 and c.get("src") != "지정":
                continue
            _쓴주제.add(_주제키(c))
            _진행시리즈[c["시리즈"]] = _진행시리즈.get(c["시리즈"], 0) + 1
            st.진행잡음 = True
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
        # v0.4: 정보형도 리뷰 · 인터뷰를 읽는다. 장면 · 발언 · 남의 해석이 여기서 온다
        if 정보형검색 and w.get("한국제목") and st.유형 != "기념일":
            st.links = collect_links(st.주제)
            with ThreadPoolExecutor(max_workers=3) as ex:         # v0.5: 검색 페이지 ∥ 위키 ko ∥ 위키 en. 호스트가 달라 같이 받는다
                f_pages = ex.submit(read_pages, st.links, 정보형페이지)
                f_wiki = [ex.submit(위키_평가절, w, st.주제["시리즈"], lang) for lang in ("ko", "en")]
                st.pages, st.blocked = f_pages.result()
                for f in f_wiki:
                    rec = f.result()
                    if rec:
                        st.pages.append(rec)
        return st
    # 심층형 — 검색해서 읽는다
    if not w["한국제목"]:
        st.이유 = f"작품 표기 없음: {w.get('romaji')}"
        return st
    st.links = collect_links(st.주제)
    with ThreadPoolExecutor(max_workers=5) as ex:                 # v0.5: 검색 페이지 ∥ 위키 평가 절 ko · en ∥ 위키 작품 문서 ∥ 라프텔 회차 + 위키 줄거리
        f_pages = ex.submit(read_pages, st.links, PAGE_LIMIT)
        f_wiki = [ex.submit(위키_평가절, w, st.주제["시리즈"], lang) for lang in ("ko", "en")]
        f_wk = ex.submit(위키_작품문서, w, st.주제["시리즈"])
        f_이야기 = ex.submit(이야기_모으기, st, w["한국제목"])   # v0.4: 발언을 붙일 장면
        st.pages, st.blocked = f_pages.result()
        for f in f_wiki:
            rec = f.result()
            if rec:
                st.pages.append(rec)
        wk = f_wk.result()
        st.이야기 = f_이야기.result()
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
    if st.pages:
        st.pages = pre_filter(st.pages, st.주제)[:(PAGE_LIMIT if st.모양 == "심층" else 정보형페이지) + 2]
    return st

def n_재료판정(st: RunState) -> RunState:
    if st.모양 == "정보":
        # 코드가 본다. 사실이 몇 개인지, 이야기가 있는지
        st.사실수 = 사실_세기(st.사실표, st.이야기)
        st.log(f"  사실표 {len(st.사실표)}자 · 이야기 {len(st.이야기)}자 · 맥락 {len(st.맥락)}자 · 고유 사실 {st.사실수}개" + (f" · 순서 {len(st.순서표)}개" if st.순서표 else ""))
        if st.pages:
            # v0.4: 검색해 읽은 글은 모델이 판정한다. 장면메모 · 발언 · 해석을 뽑는다
            b = {"남은콜": st.남은콜}
            st.판정원본 = judge_pages(st.pages, st.주제, b)
            st.남은콜 = b["남은콜"]
            st.재료, st.발언들 = keep_rules(st.판정원본, st.유형)
            st.log(f"  검색 링크 {len(st.links)} / 읽은 페이지 {len(st.pages)} / robots 막힘 {len(st.blocked)} / 남은 글 {len(st.재료)} / 장면메모 {sum(len(r['장면메모']) for r in st.재료)} / 발언 {sum(len(m['말']) for m in st.발언들)}")
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

def _주문뽑기(st):
    """온도 · 시작점 · 배치 · 접근을 뽑는다. 회차 줄거리가 없으면 장면에서 여는 시작점 · 배치는 안 뽑는다."""
    회차있음 = bool(re.search(r"^- \d+화 ", st.이야기 or "", re.M))
    시작후보 = [x for x in 시작점들[st.모양] if 회차있음 or x not in 장면시작점]
    if st.모양 == "심층" and st.유형 == "제작이야기":
        시작후보 = [x for x in 시작후보 if x != "평이 갈린 지점에서"]
    배치후보 = [x for x in 배치글 if 회차있음 or x not in 장면배치]
    st.시작점 = random.choice(시작후보)
    st.배치 = random.choice(배치후보)
    st.접근 = "분석" if st.유형 == "제작이야기" else (random.choice(접근들) if st.유형 == "작품리뷰" else "")
    return random.choice(온도들)

def n_기획자(st: RunState) -> RunState:
    st.바퀴 += 1
    st.판종류 = "첫 판" if not st.기록 else "새로 씀"     # v0.6: 사실 문제로 기획자부터 다시 오면 새로 씀
    순서 = "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표) if st.순서표 else ""
    온도 = st.주문.온도 if st.주문 else _주문뽑기(st)
    if not st.시작점:
        온도 = _주문뽑기(st)
    st.주문 = 기획주문(유형=st.유형, 모양=st.모양, 주제한줄=st.주제["한줄"], 작품정보=작품_text(st.주제, st.용어표),
                     표기표=st.용어표.text(), 사실표=st.사실표, 이야기=st.이야기[:회차재료글자 + 6000], 관점표=st.관점표, 발췌=발췌_text(st.재료), 발언=말_text(st.발언들),
                     맥락=st.맥락[:5000], 순서표=순서, 온도=온도, 시작점=st.시작점, 배치=st.배치, 접근=st.접근,
                     평개수=len(set(r["매체"] for r in st.재료)), 지난문제=st.지난문제)
    st.개요_ = st.글 = st.판정 = None
    st.고친곳, st.걸림, st.앞글, st.문제목록 = [], [], "", ""
    for 번째 in (1, 2):
        try:
            st.개요_ = 기획자.run_sync("뼈대를 짠다.", deps=st.주문, usage_limits=UsageLimits(request_limit=3)).output
        except Exception as e:
            st.이유 = f"기획 실패: {e}"
            st.log(f"  {st.이유}")
        st.남은콜 -= 1
        if not st.개요_ or 번째 == 2 or st.남은콜 < 6:
            break
        # v0.4: 해석 칸이 절반도 안 차면 줄거리 글이 된다. 한 번만 다시 짠다
        찬것 = sum(1 for x in st.개요_.문단들 if x.해석.strip())
        if 찬것 * 2 >= len(st.개요_.문단들) and len(st.개요_.해석들) >= 2:
            break
        st.log(f"  해석 칸 {찬것}/{len(st.개요_.문단들)} · 해석들 {len(st.개요_.해석들)}개. 줄거리 뼈대다. 한 번 더 짠다")
        st.주문 = st.주문.model_copy(update={"지난문제": (st.주문.지난문제 + "\n" if st.주문.지난문제 else "") +
                                            "- (뼈대) 해석 칸이 비어 있다. 문단마다 그 장면이 하는 일을 적는다. 해석들에 둘 이상"})
    if st.개요_:
        o = st.개요_
        찬것 = sum(1 for x in o.문단들 if x.해석.strip())
        st.log(f"  주문: {온도} · {st.시작점} · {st.배치}" + (f" · {st.접근}" if st.접근 else "") + f" / 질문: {o.주제[:40]} / 답: {(o.답 or '-')[:40]} / 문단 {len(o.문단들)}개 (해석 {찬것}) / 사실 {len(o.사실목록)}개")
        if len(o.사실목록) < 10:
            st.log("  사실목록이 열 개도 안 된다. 글이 빌 것이다")
    return st

def _집필주문(st, 온도, 순서):
    return 집필주문(유형=st.유형, 모양=st.모양, 주제한줄=st.주제["한줄"], 표기표=st.용어표.text(),
                  개요=st.개요_, 온도=온도, 시작점=st.주문.시작점, 배치=st.주문.배치, 접근=st.주문.접근,
                  순서표=순서, 앞글=st.앞글, 문제목록=st.문제목록)

async def _초안들_쓰기(st, 온도목록, 순서):
    """v0.5: 온도를 달리한 초안 여럿을 한 루프에서 같이 쓴다."""
    async def one(온도):
        try:
            r = await 작가.run("쓴다.", deps=_집필주문(st, 온도, 순서), usage_limits=UsageLimits(request_limit=3))
            return {"온도": 온도, "글": r.output, "걸림": [], "경고": [], "판정": None, "올림": False, "이유": ""}
        except Exception as e:
            st.log(f"  집필 실패 ({온도}): {e}")
            return None
    return await asyncio.gather(*(one(t) for t in 온도목록))

def n_작가(st: RunState) -> RunState:
    순서 = "\n".join(f"{r['번호']}. [{r['갈래']}] {r['이름']}" for r in st.순서표) if st.순서표 else ""
    st.초안들 = []
    n = 1 if st.문제목록 else max(1, min(초안수, st.남은콜 - 1))   # 다시 쓰기는 앞 글 하나만 고친다. 편집국장 몫 하나는 남긴다
    if st.문제목록:
        st.바퀴 += 1                                   # 앞 글을 고쳐 쓰는 것도 한 바퀴다
        st.판종류 = "고침"                              # v0.6
        st.log(f"  다시 쓰기 {st.바퀴}차: 앞 글을 받아 지적된 부분만 고친다")
    if n == 1:
        try:
            st.글 = 작가.run_sync("쓴다.", deps=_집필주문(st, st.주문.온도, 순서), usage_limits=UsageLimits(request_limit=3)).output
        except Exception as e:
            st.이유 = f"집필 실패: {e}"
            st.log(f"  {st.이유}")
        st.남은콜 -= 1
    else:
        # v0.5: 첫 바퀴는 초안 n개를 온도를 달리해 동시에. 기획자 온도가 첫째, 나머지 온도가 뒤에
        온도목록 = ([st.주문.온도] + [t for t in 온도들 if t != st.주문.온도]) * n
        온도목록 = 온도목록[:n]
        got = _run_async(_초안들_쓰기(st, 온도목록, 순서))
        st.남은콜 -= n
        st.초안들 = [g for g in got if g]
        st.글 = st.초안들[0]["글"] if st.초안들 else None
        if not st.초안들:
            st.이유 = "집필 실패"
        else:
            st.log(f"  초안 {len(st.초안들)}개를 같이 썼다: {' · '.join(g['온도'] for g in st.초안들)}")
    st.앞글, st.문제목록 = "", ""
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
    if len(st.초안들) > 1:
        # v0.5: 초안마다 본다. 덜 걸린 순으로 세운다. 편집국장이 고르기 전까지 첫째가 st.글이다
        for g in st.초안들:
            g["걸림"] = 형태검사(g["글"].본문, st, 제목=g["글"].제목)
            g["경고"] = list(st.코드경고)
        st.초안들.sort(key=lambda g: len(g["걸림"]))
        st.log("  형태검사: " + " / ".join(f"{g['온도']} {len(g['걸림'])}개" for g in st.초안들))
        g = st.초안들[0]
        st.글, st.걸림, st.코드경고 = g["글"], g["걸림"], g["경고"]
        return st
    st.걸림 = 형태검사(st.글.본문, st, 제목=st.글.제목)
    if st.코드경고:
        st.log("  코드 경고: " + " / ".join(st.코드경고))
    return st

def n_편집국장(st: RunState) -> RunState:
    초안요약 = []
    if len(st.초안들) > 1:
        # v0.5: 형태검사를 통과한 초안만 편집국장이 같이 읽는다. 다 걸렸으면 가장 덜 걸린 하나만 — 문제 목록을 받으려고
        통과 = [g for g in st.초안들 if not g["걸림"]]
        읽을 = (통과 or st.초안들[:1])[:max(1, st.남은콜)]
        판정들 = _run_async(_편집국장_여럿(st, 읽을)) if st.남은콜 > 0 else [None] * len(읽을)
        st.남은콜 -= len(읽을) if st.남은콜 > 0 else 0
        for g, p in zip(읽을, 판정들):
            g["판정"] = p
            g["올림"], g["이유"] = 올릴까(g["걸림"], p)
        # 올림이 먼저, 그다음 형태검사 덜 걸린 것, 점수 높은 것, 문제 적은 것
        읽을.sort(key=lambda g: (0 if g["올림"] else 1, len(g["걸림"]), -(g["판정"].점수 if g["판정"] else -1), len(g["판정"].문제들) if g["판정"] else 99))
        g = 읽을[0]
        st.글, st.걸림, st.코드경고, st.판정 = g["글"], g["걸림"], g["경고"], g["판정"]
        st.주문 = st.주문.model_copy(update={"온도": g["온도"]})
        st.올림, st.이유 = g["올림"], g["이유"]
        초안요약 = [f"{x['온도']}: 형태 {len(x['걸림'])}" + (f" · 편집국장 {x['판정'].점수}점 {len(x['판정'].문제들)}건" if x["판정"] else (" · 안 읽음" if x not in 읽을 else " · 편집국장 실패")) for x in st.초안들]
        st.log(f"  편집국장이 {len(읽을)}개를 같이 읽었다 → {g['온도']} 고름 / " + " / ".join(초안요약))
        if st.코드경고:
            st.log("  코드 경고: " + " / ".join(st.코드경고))
    else:
        b = {"남은콜": st.남은콜}
        st.판정 = 편집국장_읽기(st, b)
        st.남은콜 = b["남은콜"]
        st.올림, st.이유 = 올릴까(st.걸림, st.판정)
    앞판 = st.기록[-1] if (앞판기억 and st.기록) else None
    if 앞판 and st.판정 and 앞판.get("판정"):
        대조 = st.판정.앞판대조
        세기 = lambda 말: sum(1 for x in 대조 if x.strip().startswith(말))
        st.log(f"  앞 판 {앞판['판정'].점수}점 → {st.판정.점수}점 ({st.판종류}) / 고쳐짐 {세기('고쳐짐')} · 남음 {세기('남음')} · 새로 생김 {세기('새로 생김')} · 못 봄 {세기('앞 판에서 못 봄')}"
               + (f" / {st.판정.점수설명}" if st.판정.점수설명 else ""))
    문제수 = len(st.판정.문제들) if st.판정 else 0
    st.기록.append({"차례": st.바퀴, "종류": st.판종류, "개요": st.개요_, "글": st.글, "고친곳": st.고친곳, "걸림": st.걸림, "판정": st.판정,
                   "올림": st.올림, "이유": st.이유, "조건": (st.주문.온도, st.주문.시작점, st.주문.배치, st.주문.접근), "초안들": 초안요약})
    st.log(f"  {st.바퀴}차: {'올림' if st.올림 else '탈락'} / {st.이유} / 교정 {len(st.고친곳)}곳 / 편집국장 문제 {문제수}건"
           + (f" / 읽을 이유: {st.판정.읽을이유[:50]}" if st.판정 and st.판정.읽을이유 else "") + (f" / 확인 목록 {len(st.판정.확인목록)}건" if st.판정 and st.판정.확인목록 else ""))
    if not st.올림:
        목록 = 문제목록_글(st.걸림, st.판정)
        사실문제 = st.판정 is not None and any(x.종류 == "사실" for x in st.판정.문제들)
        if 사실문제 or st.판정 is None:
            st.지난문제, st.앞글, st.문제목록 = 목록, "", ""            # 기획자부터 다시
        else:
            st.앞글, st.문제목록 = st.글.본문, 목록                       # 작가가 앞 글을 고친다
    return st

def n_저장(st: RunState) -> RunState:
    st.상태 = "올림" if st.올림 else "탈락"
    with _주제잠금:
        if st.올림:
            _올린시리즈[st.주제["시리즈"]] = _올린시리즈.get(st.주제["시리즈"], 0) + 1
    _진행놓기(st)
    p = 저장(st)
    st.저장경로 = str(p) if p else None
    if p:
        st.log(f"  저장: {p}")
    return st

print("마디 준비 끝")

# ## 22. 갈림길
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
        if st.사실수 < 최소사실 or not st.이야기:
            st.log(f"  재료가 얇다 (고유 사실 {st.사실수}개 · 이야기 {len(st.이야기)}자). 이 주제는 버리고 다음 후보로 간다")
            return "주제뽑기"
        return "관점묶기" if st.재료 else "기획자"          # v0.4: 남의 글이 있으면 관점표를 만든다
    발언수 = sum(len(m["말"]) for m in st.발언들)
    if not st.보강됨 and st.남은콜 >= 보강페이지 + 4:
        if len(st.재료) < 보강목표 or (st.유형 == "제작이야기" and 발언수 < 심층최소발언):
            st.log("  재료가 얇다. 제작진 이름 · 시즌 리뷰로 더 찾는다")
            return "재료보강"
    if len(st.재료) < MIN_PAGES:
        st.log(f"  쓸 만한 글이 {len(st.재료)}개. 이 주제는 버리고 다음 후보로 간다")
        return "주제뽑기"
    if st.유형 == "제작이야기" and 발언수 < 심층최소발언:
        # v0.4: 발언 없는 제작 이야기는 감상순서 글이 된다. 세 바퀴 돌기 전에 버린다
        st.log(f"  제작진 발언 {발언수}개. {심층최소발언}개는 있어야 제작 이야기를 쓴다. 이 주제는 버리고 다음 후보로 간다")
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
    return "교정자" if 교정자쓰기 else "형태검사"          # v0.5: 교정 규칙이 작가 프롬프트에 있으면 마디를 건너뛴다

def _걸림종류(걸림):
    """'같은 숫자 되풀이: 135화 5번' → '같은 숫자 되풀이'. 종류만 남긴다."""
    return {re.split(r"[:(]", x)[0].strip() for x in (걸림 or [])}

def after_편집국장(st):
    if st.올림:
        return "저장"
    if st.바퀴 >= REWRITE_LIMIT:
        st.log(f"  {REWRITE_LIMIT}번 다 걸렸다. 탈락으로 저장한다")
        return "저장"
    if st.남은콜 < 4:
        st.log("  모델 요청 상한. 탈락으로 저장한다")
        return "저장"
    if 같은걸림중단 and len(st.기록) >= 2:
        # v0.4: 앞 바퀴와 같은 종류로 또 걸렸으면 다시 써도 같은 데서 걸린다. 여기서 멈춘다
        겹 = _걸림종류(st.기록[-2]["걸림"]) & _걸림종류(st.기록[-1]["걸림"])
        if len(겹) >= 같은걸림개수:
            st.log(f"  같은 데서 또 걸렸다 ({', '.join(sorted(겹))}). 더 안 돌고 탈락으로 저장한다")
            return "저장"
    return "작가" if st.문제목록 else "기획자"

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

def run_graph(st: RunState, 멈출마디=None, 시작마디=None) -> RunState:
    node = 시작마디 or START
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

# ## 23. 저장
#
# 올린 글과 탈락 글을 모두 마크다운으로 남긴다. 원문은 안 남긴다. 표기 출처를 머리에 남긴다.

def 저장(st: RunState):
    if not st.기록:
        return None
    r = st.기록[-1]
    최고줄 = []
    if 최고판저장 and not st.올림 and len(st.기록) > 1:      # v0.6: 탈락이면 점수 제일 높은 판 글을 남긴다
        후보 = [k for k in st.기록 if k.get("판정") is not None and k.get("글") is not None]
        best = max(후보, key=lambda k: (k["판정"].점수, k["차례"])) if 후보 else None
        if best is not None and best is not r:
            최고줄 = [f"- 최고 판 {best['차례']}차({best['판정'].점수}점) 글을 저장했다. 마지막 판은 {r['차례']}차"
                      + (f"({r['판정'].점수}점)" if r.get("판정") else "(편집국장 실패)")]
            st.log(f"  탈락 — 최고 판 {best['차례']}차({best['판정'].점수}점) 글을 저장한다. 마지막 {r['차례']}차"
                   + (f"({r['판정'].점수}점)" if r.get("판정") else ""))
            r = best
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
        온도, 시작점, 배치, 접근 = (list(k["조건"]) + ["", ""])[:4]
        로그.append(f"### {k['차례']}차 ({k.get('종류', '')}) — {'올림' if k['올림'] else '탈락'} / {k['이유']}")
        로그.append(f"- 조건: {온도} · {시작점} · {배치}" + (f" · {접근}" if 접근 else ""))
        if k.get("초안들"):
            로그.append("- 초안: " + " / ".join(k["초안들"]))
        if k["걸림"]:
            로그.append(f"- 형태 검사: {', '.join(k['걸림'])}")
        if k["고친곳"]:
            로그.append("- 교정자가 고친 곳:")
            로그 += [f"  - {x}" for x in k["고친곳"][:12]]
        if k["판정"]:
            로그.append(f"- 편집국장 {k['판정'].점수}점: {k['판정'].한줄평}")
            if k["판정"].읽을이유:
                로그.append(f"- 읽을 이유: {k['판정'].읽을이유}")
            if k["판정"].점수설명:
                로그.append(f"- 앞 판 대비: {k['판정'].점수설명}")
            로그 += [f"  - (앞 판) {x}" for x in k["판정"].앞판대조]
            로그 += [f"  - ({x.종류}) {x.어디[:60]} — {x.설명}" for x in k["판정"].문제들]
            if k["판정"].확인목록:
                로그.append("- 확인 목록 (재료 밖이지만 널리 알려진 것으로 봄. 사람이 본다):")
                로그 += [f"  - {x}" for x in k["판정"].확인목록]

    lines = [f"# {g.제목}", "", g.본문, "", "---", "",
             "## 주제", f"- 유형: {st.유형} ({st.모양}형) · 시리즈: {st.주제['시리즈']} · {st.주제['한줄']}  [{st.주제['src']}]",
             f"- 표기 기준: {GLOSSARIES[st.주제['시리즈']]['기준']}",
             "", "## 표기 출처"] + (표기줄 or ["- 없음"]) + \
            ["", "## 출처"] + (출처줄 or ["- 없음"]) + \
            ["", f"## 개요 ({r['차례']}차)", f"- 질문: {r['개요'].주제}", f"- 답: {r['개요'].답 or '-'}", "", "## 판정 기록"] + 최고줄 + 로그 + \
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
# - `정보형페이지` 8, `심층최소발언` 3, `같은걸림개수` 3 — 돌려보고 맞춘다 (v0.4)
# - `동시주제` 3, `초안수` 3 — 콜랩에서 돌려보고 맞춘다. 스레드별 모델은 TestModel 흐름만 봤다 (v0.5)
#
# 랭그래프로 옮길 때: 마디 함수는 `add_node`에, 갈림길 함수는 `add_conditional_edges`에 그대로 꽂는다. `RunState`가 상태 스키마다.


# ======================================================================
# 실행
#   python anime_agent.py check                       # 점검 — AniList · Jikan · 라프텔 · 위키백과 · 검색. 모델 안 부른다
#   python anime_agent.py glossary                    # 용어집 점검 — 시리즈마다 AniList 대표 작품 · 관계도 · 편 화수
#   python anime_agent.py pick --type 감상순서 --n 2   # 주제 뽑기 + 재료 모으기까지만. 모델 안 부른다
#   python anime_agent.py run --n 6 --at-once 3         # 유형을 섞어 n편. 주제 3개씩 같이. --type 사람 처럼 하나만도 된다
#   python anime_agent.py one 블리치 --type 제작이야기  # 시리즈 지정. --person 으로 사람 지정
#   python anime_agent.py zip
# ======================================================================

# ## 24. 점검

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

# ## 25. 뽑기만 — 모델 없이 주제 뽑기와 재료 모으기까지 본다

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
            if st.이야기:
                print(f"\n  [이야기 {len(st.이야기)}자 · 고유 사실 {사실_세기(st.사실표, st.이야기)}개] 앞부분:\n" + st.이야기[:1200])
        else:
            print(f"  재료 후보 페이지 {len(st.pages)}개 (검색 링크 {len(st.links)}, robots 막힘 {len(st.blocked)}):")
            for p in st.pages:
                print(f"   - [{p.get('매체') or 매체이름(p['url'])}] {p['제목'][:60]}  {len(p['본문'])}자")
        print("  표기표:\n" + "\n".join("   " + x for x in st.용어표.text(임시포함=True).splitlines()[:24]) if st.용어표 else "")

# ## 26. 돌리기

모델에이전트들 = ("재료판정관", "기획자", "작가", "교정자", "편집국장")   # v0.5: 스레드별 모델을 끼울 자리

def _새모델():
    """v0.5: 스레드 하나가 혼자 쓸 모델. HTTP 클라이언트를 따로 만든다.
    pydantic-ai 기본 클라이언트는 프로세스에 하나라 이벤트 루프 여럿이 나눠 쓰면 "bound to a different event loop"가 난다.
    v0.6 고침: `"openai:..."` 문자열은 pydantic-ai 2.x에서 Responses API(OpenAIResponsesModel)로 간다. 여기서 OpenAIChatModel을 쓰면
    chat completions로 가고, gpt-5.6-luna는 거기서 추론 기본 켜짐 + tool 조합을 400으로 막는다
    ("Function tools with reasoning_effort are not supported ... in /v1/chat/completions"). 같은 길(Responses)로 맞춘다."""
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    name = MODEL.split(":", 1)[1]
    return OpenAIResponsesModel(name, provider=OpenAIProvider(api_key=os.environ[KEY_ENV], http_client=httpx.AsyncClient(timeout=httpx.Timeout(600.0))))

def _한편(유형, 표식="", 새루프=False):
    """주제 하나를 끝까지. 스레드에서 부르면 새루프=True — 루프 · 모델을 이 스레드 것으로 둔다."""
    st = RunState(유형=유형, 표식=표식)
    if not 새루프:
        st = run_graph(st)
    else:
        asyncio.set_event_loop(asyncio.new_event_loop())
        import contextlib
        with contextlib.ExitStack() as es:
            if 스레드별모델:
                m = _새모델()
                for name in 모델에이전트들:
                    es.enter_context(globals()[name].override(model=m))
            st = run_graph(st)
    _진행놓기(st)
    return st

def run(유형=None, 목표편수=목표편수, 시도상한=시도상한, 동시=None):
    """올린 글이 목표 편수가 될 때까지 돈다. 유형을 안 주면 유형비율대로 섞는다.
    한 유형이 후보를 다 쓰면(주제없음) 그 유형은 빼고 나머지로 계속 간다.
    v0.5: 동시 개씩 같이 돈다(기본 동시주제). 한 묶음 안에서는 유형이 겹치지 않게 덜 올린 유형부터 고른다.
    묶음 크기는 모자란 편수의 두 배까지다 — 통과율이 반쯤이라 그만큼 띄워야 한 묶음에 채워진다. 목표를 조금 넘길 수 있다."""
    import contextvars
    동시 = max(1, int(동시 or 동시주제))
    남은유형 = {k: v for k, v in 유형비율.items() if v > 0} if 유형 is None else {유형: 1}
    결과, 시도, 올림수, 올린유형 = [], 0, 0, {}
    시작 = time.time()
    if 동시 > 1:
        print(f"주제를 {동시}개씩 같이 돈다. 로그 앞의 [유형]으로 가른다")
    while 올림수 < 목표편수 and 시도 < 시도상한 and 남은유형:
        n = min(동시, 시도상한 - 시도, max(1, 2 * (목표편수 - 올림수)))
        # 비율 대비 덜 올린 유형부터. 한 묶음 안에서는 고른 것을 센 셈으로 다음을 고른다
        셈, 이번들 = dict(올린유형), []
        for _ in range(n):
            k = min(남은유형, key=lambda k: 셈.get(k, 0) / 남은유형[k])
            이번들.append(k); 셈[k] = 셈.get(k, 0) + 1
        시도 += len(이번들)
        print(f"\n===== 시도 {시도}/{시도상한} · {' · '.join(이번들)} · 올림 {올림수}/{목표편수} · {int((time.time()-시작)/60)}분 =====")
        if len(이번들) == 1:
            묶음 = [_한편(이번들[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(이번들)) as ex:
                futs = [ex.submit(contextvars.copy_context().run, _한편, k, f"[{k} {i+1}] ", True) for i, k in enumerate(이번들)]   # 같은 유형이 둘이면 번호로 가른다
                묶음 = [f.result() for f in futs]
        for 이번, st in zip(이번들, 묶음):
            결과.append(st)
            if st.상태 == "주제없음":
                print(f"  {이번}: 쓸 만한 주제가 더 없다. 이 유형은 뺀다")
                남은유형.pop(이번, None)
                continue
            if st.올림:
                올림수 += 1
                올린유형[이번] = 올린유형.get(이번, 0) + 1
    print(f"\n끝. 올림 {올림수}편 ({' · '.join(f'{k} {v}' for k, v in 올린유형.items()) or '-'}) / 탈락 {sum(1 for s_ in 결과 if s_.상태 == '탈락')}편 / 시도 {시도}회 / {int((time.time()-시작)/60)}분")
    if 올림수 < 목표편수:
        print(f"목표 {목표편수}편에 {목표편수 - 올림수}편 모자란다.")
    끝나면zip()
    return 결과

# ## 26-1. 시리즈를 지정해서 한 편
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
    _진행놓기(st)
    print(f"\n끝. {st.상태} / {st.이유} / 모델 요청 {LLM_CALL_CAP - st.남은콜}회")
    끝나면zip()
    return st

# ## 27. zip으로 묶기

def zip_outputs():
    zip_path = OUT.parent / f"anime_{datetime.datetime.now():%m%d_%H%M}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob("*.md"):
            z.write(p, p.relative_to(OUT))
    print("zip:", zip_path)
    return zip_path


def 콜랩_내려받기(path) -> bool:
    """콜랩이면 브라우저로 내려준다. 로컬 스크립트에서는 아무 일도 안 하고 False 를 돌려준다."""
    if "google.colab" not in sys.modules:
        return False
    from google.colab import files
    files.download(str(path))
    return True


def 끝나면zip():
    """run · run_one 이 끝날 때 부른다. 콜랩 + 설정이 켜져 있을 때만 zip 을 묶어 내려준다.
    zip 에는 이번 세션에서 쌓인 out/anime 의 글이 전부 들어간다."""
    if not 콜랩_끝나면zip or "google.colab" not in sys.modules:
        return
    if not any(OUT.rglob("*.md")):
        print("내려받을 글이 없다")
        return
    콜랩_내려받기(zip_outputs())


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
    a = sub.add_parser("run", help="올린 글이 n편 될 때까지 돌린다. --type 을 안 주면 유형 다섯을 섞는다")
    a.add_argument("--type", dest="유형", default=None, choices=list(유형표))
    a.add_argument("--n", type=int, default=목표편수)
    a.add_argument("--tries", type=int, default=시도상한)
    a.add_argument("--at-once", dest="동시", type=int, default=None, help=f"주제를 몇 개씩 같이 돌릴지 (기본 {동시주제})")
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
        run(args.유형, 목표편수=args.n, 시도상한=args.tries, 동시=args.동시)
        zip_outputs()
    elif args.cmd == "one":
        run_one(args.series, args.유형, 사람=args.person)
    elif args.cmd == "zip":
        zip_outputs()


if __name__ == "__main__":
    main()
