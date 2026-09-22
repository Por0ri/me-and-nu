# # Movie Review agent V1.8 — 영화 리뷰 에이전트
#
# 콜랩 노트북(Review_agent_V1.8.ipynb)을 스크립트로 옮긴 것이다.
# 키는 `.env` 또는 환경변수 `LLM_KEY` · `TMDB_KEY`에서 읽는다. 결과는 `agent/out/movie_review/`에 쌓인다.
#
# 위에서부터 차례로 실행한다.
#
# **미리 넣을 것**
# - 왼쪽 열쇠 아이콘(보안 비밀)에 `TMDB_KEY`, `OPENAI_API_KEY` 두 개를 넣는다.
# - 셀 2의 모델 이름이 실제 쓸 수 있는 것인지 확인한다.
# - 추론 모델이 아니면 `USE_REASONING_EFFORT = False`로 둔다.
#
# **흐름**
#
# TMDB에서 재료 받기 → 갈래·등급 거르기 → 재료 모자란지 보기 → 문체·페르소나·배치·분량 무작위로 뽑기 → 글쓰기 → 형태 검사 → 판정관 → 업로드 규칙
#
# **상태 네 가지**
#
# - 대상아님 — 다큐멘터리·가족·애니메이션·TV 영화이거나 전체관람가다
# - 재료부족 — 줄거리가 짧다
# - 올림 — 다 통과했다
# - 탈락보관 — 세 번 다시 써도 못 넘겼거나 탈락 점수가 나왔다

# ## 셀 1 — 설치
#
# `pip install -r ../requirements.txt`

# ## 셀 2 — 키와 설정 (여기만 고치면 된다)

import os, re, json, random, asyncio, sys
from pathlib import Path

from dotenv import load_dotenv

# agent/.env → 이 파일 옆 .env 순서로 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다
HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")


def _secret(name: str) -> str:
    v = os.environ.get(name)
    if not v or not v.strip():
        raise RuntimeError(
            f"환경변수 '{name}'이 없다. agent/.env 파일에 {name}=... 을 적거나 "
            f"셸에서 환경변수로 넣는다. (agent/.env.example 참고)"
        )
    return v.strip()


TMDB_KEY = _secret("TMDB_KEY")
os.environ["OPENAI_API_KEY"] = _secret("LLM_KEY")   # 보안 비밀 이름은 LLM_KEY다
print("키 읽음. 길이:", len(os.environ["OPENAI_API_KEY"]))

VERSION = "V1.8"

# ── 모델 ──────────────────────────────────────────────────
WRITER_MODEL = "openai:gpt-5.6-luna"
JUDGE_MODEL  = "openai:gpt-5.6-luna"
USE_REASONING_EFFORT = True      # 추론 모델이 아니면 False로 둔다
REASONING_EFFORT = "low"

# ── 상한 ──────────────────────────────────────────────────
MAX_REWRITE = 3                  # 다시 쓰기 횟수 상한
REQUEST_LIMIT_PER_RUN = 3        # 한 번 부를 때 모델 요청 횟수 상한

# ── 재료 조건 ─────────────────────────────────────────────
MIN_SYNOPSIS_CHARS = 150
EXCLUDE_GENRES  = {"다큐멘터리", "가족", "애니메이션", "TV 영화"}
EXCLUDE_RATINGS = {"ALL", "전체관람가", "7", "7세이상관람가", "G"}
# 상영시간 기준은 두지 않는다. 단편도 만든다.

# ── 글마다 무작위로 뽑는 것 ──────────────────────────────
APPROACHES = ["분석", "해석", "평가"]                 # 글이 무엇을 하나
TEMPERATURES = ["건조", "따뜻함", "짓궂음"]           # 쓰는 사람의 온도
PERSONAS = ["장면부터", "대비", "관객", "맥락", "바꿔 말하기", "질문", "결론부터"]   # 시작점
LAYOUTS  = ["줄거리 먼저", "판단 먼저", "줄거리 나눠 넣기",
            "좋은 점과 아쉬운 점 섞기", "질문에서 출발"]
LENGTH_RANGE = {                 # 접근마다 어울리는 길이가 다르다. 2026-09-18 기준점
    "분석": (1400, 1900),
    "해석": (1700, 2000),
    "평가": (1300, 1700),
}
SHORT_LIMIT = 1700               # 최대 글자 수가 이 아래면 "짧은 분량"이다. 넣을 것을 줄인다
# 분량별 문단 수 (최소, 최대). 문단이 많으면 문단마다 짧아지고 문장도 따라 짧아진다
PARAGRAPH_RANGE = [(1700, 4, 5), (2100, 5, 6), (99999, 6, 8)]


def paragraph_range(max_chars: int) -> tuple[int, int]:
    for limit, lo, hi in PARAGRAPH_RANGE:
        if max_chars <= limit:
            return lo, hi
    return 6, 9


def is_short(max_chars: int) -> bool:
    return max_chars <= SHORT_LIMIT

# ── 대중 평가 갈래 — 임시 기준 ───────────────────────────
MIN_VOTES      = 500
PRAISE_LINE    = 7.0
CRITICISM_LINE = 5.5

# ── 위키백과 주변 이야기 ─────────────────────────────────
WIKI_API = "https://ko.wikipedia.org/w/api.php"
CONTEXT_SECTIONS = ("제작", "개봉", "반응", "평가", "흥행", "영향",
                    "여담", "기타", "논란", "패러디", "수상")
CONTEXT_MAX_CHARS = 3000

# ── 판정관 통과선 — 임시 ─────────────────────────────────
PASS = {
    "topic_fit":    4,
    "form_fit":     4,
    "element_fit":  4,
    "balance":      3,
    "promo":        2,   # 낮을수록 좋다
    "completeness": 3,
}
UNSOURCED_PASS    = 0
UNSOURCED_REWRITE = 2


def pick_length(approach: str) -> tuple[int, int]:
    lo, hi = LENGTH_RANGE[approach]
    target = random.randint(lo, hi)
    return target - 200, target + 200

# ## 셀 3 — 주고받는 값의 모양

from typing import Literal
from pydantic import BaseModel, Field

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


class WriteOrder(BaseModel):
    """한 편을 쓰라는 지시. 에이전트의 deps로 들어간다."""
    material: Material
    topic: Topic = "영화"
    tag_topic: Topic | None = None
    approach: str
    temperature: str
    persona: str
    layout: str
    min_chars: int
    max_chars: int
    rewrite_note: str | None = None
    previous_body: str | None = None


class Article(BaseModel):
    title: str = Field(description="글 제목. 영화 제목을 그대로 쓰지 않는다")
    body:  str = Field(description="본문. 문단은 빈 줄로 나눈다")
    sources: list[str] = Field(description="출처 링크 목록")


class JudgeScore(BaseModel):
    topic_fit:    int = Field(ge=1, le=5)
    form_fit:     int = Field(ge=1, le=5)
    element_fit:  int = Field(ge=1, le=5)
    balance:      int = Field(ge=1, le=5)
    promo:        int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    unsourced_claims: int = Field(ge=0, description="기본 사실이 재료와 어긋난 문장 개수")
    unverified_context: list[str] = Field(
        default_factory=list,
        description="재료에 없는 주변 이야기 문장. 틀린 게 아니라 사람이 확인할 것")
    spoiler: bool
    reason: str

# ## 셀 4 — 프롬프트

COMMON = """
너는 영화 리뷰를 쓴다.

[문체]
문어체로 쓴다. 존댓말을 쓰지 않는다. "~합니다", "~해요", "~세요"를 쓰지 않는다.

[문장 흐름]
한 문단은 한 줄기 생각이다. 문장은 앞 문장이 남긴 것을 받아서 이어진다.
문장을 끝낼 때 다음 문장이 붙을 자리를 남긴다. 판단을 내렸으면 그 근거나 결과가 다음 문장이 된다.

문장 길이는 내용이 정한다.
구체적인 것 하나를 말할 때는 짧다. "괴물은 대낮에 나온다."
왜 그런지를 따라갈 때는 길다. 따라가는 동안 끊지 않는다.
  토막: 이 영화는 괴물을 오래 감춰 두지 않는다. 시작하자마자 대낮에 풀어놓는다.
  이음: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다.
모든 문장을 같은 길이로 쓰지 않는다. 모든 문장에 "~인데", "~지만"을 붙이지 않는다.
"~는데"로 잇는 문장은 한 문단에 한 번이다.

판단은 끝까지 간다. "~다"로 끝낸다.
"~에 가깝다", "~인 셈이다", "~할 만하다", "~는 편이다", "~일 수 있다", "~로 보인다"는
판단을 흐리는 말이다. 한 편에 한 번을 넘기지 않는다.
  흐림: 그 서툶은 억지로 붙인 흠이라기보다 서툰 가족을 보여주려다 생긴 흔적에 가깝다.
  판단: 그 서툶은 흠이 아니다. 서툰 가족을 보여주려고 일부러 남긴 자리다.

문단의 마지막 문장은 그 문단이 말한 것을 한 번 더 밀어 준다. 새 화제를 던지고 끝내지 않는다.
문단을 바꿔서 화제를 돌리지 않는다. 문단 안에서 이야기를 끝까지 끌고 간다.

사람이 말하는 것처럼 읽혀야 한다. 소리 내어 읽었을 때 어색하면 고친다.
이 프롬프트에 든 예시 문장을 그대로 옮기지 않는다. 예시는 모양을 보여주는 것이지 재료가 아니다.

쓰지 않는 말과 대신 쓸 말이다.
- "~에 있어서" → "~에서", "~할 때"
- "~함에 따라" → "~하면서", "~해서"
- "~적인" → 빼거나 풀어 쓴다. "인상적인 장면" → "기억에 남는 장면"
- "~로 인해" → "~때문에"
- "~에 대한" → "~에 관한", 또는 빼고 붙인다. "인물에 대한 묘사" → "인물 묘사"
- "~을 통해" → "~로", "~하면서"
- "~되어진다", "~되어 있다" → "~된다", "~돼 있다"
- "그것은 ~이다" 같은 주어 대명사 → 빼고 쓴다
- "~라고 할 수 있다", "~라고 볼 수 있다" → "~다"
- "하지만 그럼에도 불구하고" → "그래도"
- "~하는 것이 가능하다" → "~할 수 있다"

중학생이 읽어도 바로 이해되는 말로 쓴다.
어려운 말 대신 일상어를 쓴다. 관조 · 미학 · 서사 같은 말은 풀어서 쓴다.

[숫자]
본문에 숫자를 쓰지 않는다. 개봉 연도만 예외다.
몇 분 · 몇 번 · 몇 퍼센트 · 몇 시간 · 몇 명을 쓰지 않는다.
시간의 감각은 말로 쓴다.
  숫자: 괴물이 20분 만에 나온다.
  말: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다.
횟수도 말로 쓴다. "세 번 반복된다"가 아니라 "몇 번이고 돌아온다"다.
크기도 말로 쓴다. "5미터짜리 괴물"이 아니라 "사람을 한입에 물어가는 크기"다.

[기본 사실]
개봉일 · 감독 · 출연 · 상영시간 · 등급 · 원작 · 인물 이름 · 줄거리 사건은 아래 재료를 따른다.
재료와 다르게 쓰지 않는다. 재료에 없는 항목은 꺼내지 않는다. 정보 없음이라고 적지도 않는다.

재료는 확인용이지 옮겨 적는 것이 아니다.
개봉일 · 출연 · 상영시간 · 등급을 한 문장에 늘어놓지 않는다.
글 흐름에 필요한 것만 하나씩 쓴다. 출연 배우 이름은 그 배우 이야기를 할 때만 쓴다.
재료의 줄거리 문장을 그대로 옮기지 않는다. 내 문장으로 다시 쓴다.
재료를 한 문장으로 요약한 것처럼 보이는 문단을 만들지 않는다.

[주변 이야기]
제작 뒷이야기, 개봉했을 때 반응, 영화 밖에서 생긴 일, 나중 작품에 남긴 것은 리뷰의 재료다.
재료의 '주변 이야기' 항목에 있으면 그것을 따른다.
거기 없어도 네가 확실히 아는 일이면 써도 된다. 널리 알려진 일이어야 한다.
확실하지 않으면 쓰지 않는다. 그럴듯하게 지어내지 않는다.
날짜 · 숫자 · 사람 이름이 헷갈리면 그 부분을 빼고 쓴다.

[감상]
영화가 어떻게 보였는지는 네가 쓴다.
장면이 남긴 인상, 인물이 움직이는 방식, 이야기가 흘러가는 속도를 써도 된다.
다만 안 본 사람은 쓸 수 없을 만큼 구체적인 것은 쓰지 않는다.
어느 곡이 어디에 깔리는지, 어느 장면이 정확히 어떻게 찍혔는지 같은 것이다.
확인할 수 없는 것을 확인된 사실처럼 쓰지 않는다.

줄거리와 갈래만으로도 말할 수 있는 것이 있다.
- 인물 설정이 이야기를 어디로 몰고 가는지
- 이야기의 순서가 긴장을 만드는지, 늘어뜨리는지
- 이 갈래에서 흔히 하는 것과 이 영화가 어디서 갈라지는지
- 감독의 이전 작품과 이어지는 점이 있는지 (재료에 있을 때만)
사건을 다시 적는 것으로 감상을 대신하지 않는다.

[대중 평가]
재료에 갈래 값이 있을 때만 쓴다. 없으면 아예 꺼내지 않는다.
갈래가 뜻하는 정도를 넘어서 말하지 않는다.
점수 · 순위 · 인원수 같은 숫자를 쓰지 않는다.
한 편에 한 번만 쓴다.

[하지 않는 것]
실제 평론가 이름을 빌려 "누구처럼"이라고 쓰지 않는다.
별점이나 점수를 매기지 않는다.
결말과 주요 반전을 밝히지 않는다. 재료의 줄거리에 이미 적힌 것까지는 괜찮다.
보러 가라고 부추기지 않는다. 예매 · 개봉관 이야기를 쓰지 않는다.
"""

TOPIC_LAYER = """
이 글은 영화 토픽 글이다.
{tag_line}
"""
TAG_LINE_YES = "태그 토픽은 {tag_topic} 하나다. 그 토픽 이야기는 한 문단을 넘기지 않는다."
TAG_LINE_NO  = "태그 토픽은 없다. 다른 토픽 이야기를 넣지 않는다."

FORM_REVIEW = """
리뷰는 이 영화를 볼지 말지 정하는 데 필요한 글이다.
무엇을 하려는 작품인지 해석만 하고 있으면 그건 평론이다.
다른 작품과 견주는 데 글의 절반을 쓰고 있으면 그건 심층 분석이다.

아래 다섯 가지가 글 안에 들어가야 한다. 순서는 배치 지시를 따른다.
- 이 영화를 어떻게 봤는지가 글 전체에서 드러나야 한다. 한 문장으로 못 박지 않는다.
  "~를 다룬 작품이다", "~로 바꿔 붙인 영화다", "~한 이야기다"로 영화를 규정하는 문장을
  첫 문단에 쓰지 않는다. 그건 독후감이 하는 일이다.
- 줄거리. 결말 앞까지만
- 좋은 점. 무엇을 보고 그렇게 말하는지 같은 문단에 적는다
- 아쉬운 점. 같은 방식으로 적는다. 억지로 흠을 잡지 않는다
- 누구에게 맞는 영화인지. 맞지 않는 사람도 같이 적는다

다섯 가지를 한 문단씩 차례로 늘어놓지 않는다.
대중 평가는 재료에 갈래 값이 있을 때만 넣는다.

분량이 짧을수록 다 넣으려 하지 않는다. 짧은 글은 요약이 아니라 선택이다.
- 넉넉한 분량: 다섯 요소 + 주변 이야기 + 감독 전작 + 대중 평가를 다 쓴다.
- 짧은 분량: 다섯 요소는 넣되, 주변 이야기 · 감독 전작 · 대중 평가 중 하나만 고른다.
  나머지는 쓰지 않는다. 한 줄로 끼워 넣지 않는다.
짧은 글에서 항목 하나를 한 문장으로 처리하느니 그 항목을 빼는 게 낫다.
분량이 짧은지 넉넉한지는 [분량] 지시에 적혀 있다.

리뷰는 영화 안에서 끝나지 않는다. 아래 중 아는 것을 하나 이상 쓴다.
- 이 영화가 어떻게 만들어졌는지. 제작 과정에서 있었던 일
- 개봉했을 때 어떤 반응이 있었는지
- 영화 밖에서 생긴 일. 동상이 세워졌다든지, 대사가 유행어가 됐다든지
- 이 영화가 나중 작품에 남긴 것
- 감독의 이전 작품과 이어지는 점
주변 이야기는 나열하지 않는다. 그 일이 이 영화를 어떻게 보이게 하는지와 엮어서 쓴다.
"동상이 세워졌다"로 끝내지 않는다. 그게 이 영화가 어떤 자리에 있다는 뜻인지 붙인다.

줄거리를 사건 순서대로 늘어놓지 않는다. 사건 하나를 적으면 그게 왜 여기 필요한지 한 문장 붙인다.
"그 뒤에", "이후", "한편", "결국"으로 사건을 잇는 문장을 연달아 쓰지 않는다.
좋은 점과 아쉬운 점의 근거는 사건이 아니라 선택에서 댄다.
"주인공이 도망친다"가 아니라 "주인공을 도망치게 만든 설정이 왜 먹히는지"를 쓴다.
같은 사건을 줄거리에서 한 번, 근거에서 또 한 번 쓰지 않는다.

좋은 점과 아쉬운 점은 글에 들어가야 하는 내용이지, 글에 쓰는 말이 아니다.
"좋은 점은", "아쉬운 점은", "장점은", "단점은", "한계는"으로 문장을 시작하지 않는다.
"다만", "그럼에도", "그러나"로 문단을 시작해서 방향을 바꾸지 않는다.
  나쁜 예: 좋은 점은 가족이 능력 있는 사람들로 꾸며지지 않았다는 데 있다.
  좋은 예: 강두네는 뭘 잘하는 사람들이 아니라서, 딸을 찾으러 나서는 길이 작전이라기보다 몸부림에 가깝다.
읽는 사람이 "이건 칭찬이구나", "이건 지적이구나"를 문장 내용으로 알게 한다. 이름표로 알게 하지 않는다.

문단마다 시작하는 방식을 바꾼다. 같은 방식으로 두 문단 연속 시작하지 않는다.
- 상황을 그리며 시작한다. "대낮 한강 둔치에서, 사람들은 아직 아무것도 모른다."
- 질문으로 시작한다. "왜 이 가족은 도망치지 않았을까."
- 흔한 말을 받아 뒤집으며 시작한다. "괴수 영화라고들 하는데, 괴물이 화면에 있는 시간은 생각보다 짧다."
- 앞 문단 끝을 받아서 시작한다.
- 판단으로 시작할 때는 근거를 같은 문장 안에 붙인다. "괴물이 일찍 나오는 건 실수가 아니라, 관객의 눈을 가족 쪽으로 돌리려는 선택이다."
"X는 Y다"처럼 짧게 못 박는 문장으로 문단을 열지 않는다. 그건 구호지 문장이 아니다.
"이 영화는", "괴물은", "봉준호는"처럼 같은 주어로 문단을 두 번 연속 열지 않는다.
"""

APPROACH = {
"분석": """
[접근 — 분석]
이 글은 뜯어보는 글이다. 이야기가 어떻게 짜였는지, 인물이 왜 그렇게 놓였는지,
어디서 긴장이 생기고 어디서 풀리는지를 본다. "이 영화는 왜 이렇게 만들어졌나"에 답한다.
  예: 이 영화는 괴물을 감춰 두고 뜸을 들이는 대신, 시작하자마자 대낮 한강 한복판에 풀어놓고 본다.
      그건 괴물을 숨길 생각이 없다는 뜻이고, 그 뒤로 거의 내내 괴물이 아니라 가족을 보라는 말이다.
다섯 요소는 다 들어가되 이 뜯어보기가 글의 중심이다.
짧은 분량이면 뜯어볼 것을 하나만 고른다. 그 하나를 끝까지 간다.
""",
"해석": """
[접근 — 해석]
이 글은 뜻을 읽는 글이다. 이 영화가 무엇을 말하려는지, 무엇에 빗대고 있는지를 본다.
"이 영화는 무슨 이야기인가"에 답한다.
  예: 정부가 이 가족을 격리하는 대목에서 이 영화의 진짜 괴물이 누구인지 드러난다.
다만 리뷰 안이라서 해석이 글의 절반을 넘기지 않는다. 볼지 말지에 필요한 만큼만 읽는다.
짧은 분량이면 읽어낼 뜻을 하나만 고른다. 그 하나를 끝까지 간다.
""",
"평가": """
[접근 — 평가]
이 글은 좋고 나쁨을 가르는 글이다. 무엇이 되고 무엇이 안 되는지, 누가 좋아하고
누가 싫어할지를 본다. "볼 만한가"에 답한다.
  예: 공포와 웃음을 오가는 게 이 영화의 힘인데, 그 오감이 피곤한 사람에게는 힘이 아니라 흠이다.
판단을 미루지 않는다. 다만 "관객마다 다르다"고 말하는 것은 판단을 미루는 게 아니라,
누구에게 다른지를 갈라 말하는 것이면 된다.
짧은 분량이면 가를 것을 하나만 고른다. 되는 것 하나, 안 되는 것 하나면 충분하다.
""",
}

STANCE = """
[쓰는 사람]
영화를 오래 많이 본 사람이 쓴다. 이 영화가 처음이 아니다.
그래서 무엇이 눈에 걸렸는지를 말한다. 놀라거나 감탄해도 된다. 다만 왜 놀랐는지가 같이 있어야 한다.
자기 판단이 있다. 판단을 남에게 미루지 않는다.
설명하지 않고 말한다. 독자가 영화를 안 봤다고 해서 하나하나 풀어 주지 않는다.
  독후감: 이 영화는 재난 앞에서 가족이 어떻게 움직이는지를 보여주는 작품이다.
  평론가: 이 가족은 재난보다 서로를 더 못 견디고, 괴물은 그걸 드러내는 핑계에 가깝다.

유머는 관찰에서 나온다. 웃기려고 쓰지 않는다. 정확하게 보면 웃긴 것이 보인다.
  나쁜 예: 괴물이 나오는데 가족이 티격태격해서 웃기다.
  좋은 예: 괴물이 사람을 물어가는 와중에도 이 집 식구들은 서로 탓할 사람부터 찾는다.
농담을 한 뒤에 설명하지 않는다. 웃긴 문장은 그냥 두고 지나간다.

싸우지 않는다. 감독이나 배우를 사람으로 깎지 않는다. 관객을 깎지 않는다.
느낌표를 쓰지 않는다. 물음표는 진짜 질문일 때만 쓴다.
"""

TEMPERATURE = {
"건조": """
[온도 — 건조]
감정을 드러내지 않는다. 관찰과 판단만 놓는다.
좋다 · 싫다 대신 된다 · 안 된다로 말한다. 웃긴 대목이 있어도 웃기다고 말하지 않고 그 대목만 보여준다.
""",
"따뜻함": """
[온도 — 따뜻함]
이 영화를 아끼는 게 보인다. 놀라거나 감탄해도 된다.
아쉬운 점을 말할 때도 깎는 게 아니라 아까워하는 쪽이다. 다만 아쉬운 점을 빼먹지는 않는다.
""",
"짓궂음": """
[온도 — 짓궂음]
정확하게 봐서 웃긴 것을 놓치지 않는다. 한 편에 한두 군데다. 세 번째부터는 가벼워진다.
비꼬지 않는다. 영화가 스스로 드러낸 우스운 대목을 그대로 보여주는 것이지, 영화를 깎는 게 아니다.
사람을 놀려서 웃기지 않는다. 관객을 놀려서 웃기지 않는다.
""",
}

PERSONA = {
"장면부터":    "상황 하나를 먼저 그려 놓고 거기서 시작한다.",
"대비":        "이 영화 안의 두 가지를 마주 놓고 그 차이로 글을 끌고 간다.",
"관객":        "이 영화를 보려는 사람이 무엇을 궁금해할지에서 시작한다.",
"맥락":        "이 영화가 어디에서 나온 작품인지부터 적는다.",
"바꿔 말하기": "이 갈래에 흔히 붙는 말을 한 번 뒤집어 놓고 시작한다.",
"질문":        "질문 하나를 던지고 글 전체로 답한다. 마지막에 답이 나와야 한다.",
"결론부터":    "누구에게 맞는 영화인지를 첫 문장에 놓고 나머지로 받친다.",
}

LAYOUT = {
"줄거리 먼저":
    "줄거리를 앞쪽에 놓고 판단을 뒤에서 낸다.",
"판단 먼저":
    "이 영화를 어떻게 봤는지를 먼저 드러내고, 줄거리는 그 판단을 받치는 자리에만 쓴다.",
"줄거리 나눠 넣기":
    "줄거리를 한 덩어리로 적지 않는다. 좋은 점과 아쉬운 점을 말하는 사이사이에 나눠 넣는다.",
"좋은 점과 아쉬운 점 섞기":
    "좋은 점과 아쉬운 점을 따로 묶지 않는다. 같은 문단 안에서 이어 말한다.",
"질문에서 출발":
    "이 영화를 두고 할 만한 질문 하나로 시작해서, 글이 진행되며 답이 드러나게 한다.",
}

RECEPTION_GUIDE = {
"호평이 많다": """
좋게 본 사람이 많다는 뜻이다. 이런 말을 쓸 수 있다.
반응이 좋은 편이다 / 좋게 본 사람이 많다 / 호의적인 평이 많다 / 평이 나쁘지 않다
같은 뜻의 다른 말을 써도 된다. 다만 "만장일치", "극찬", "역대급"처럼 세게 말하지 않는다.
""",
"평가가 갈린다": """
보는 사람마다 다르게 봤다는 뜻이다. 이런 말을 쓸 수 있다.
평이 갈린다 / 호불호가 나뉜다 / 사람마다 다르게 본다 / 같은 지점을 두고 평이 엇갈린다
"논란", "혹평 세례"처럼 한쪽으로 기울여 말하지 않는다.
무엇을 두고 갈리는지 네 짐작을 덧붙이지 않는다. 갈린다는 사실만 쓴다.
""",
"혹평이 많다": """
좋지 않게 본 사람이 많다는 뜻이다. 이런 말을 쓸 수 있다.
반응이 좋지 않다 / 아쉬워한 사람이 많다 / 평이 박한 편이다
"망작", "최악"처럼 세게 말하지 않는다.
""",
}


def _length_layer(order: WriteOrder) -> str:
    plo, phi = paragraph_range(order.max_chars)
    kind = "짧은 분량" if is_short(order.max_chars) else "넉넉한 분량"
    return (f"[분량]\n{order.min_chars}자에서 {order.max_chars}자 사이다. 이건 {kind}이다.\n"
            f"문단은 빈 줄로 나눈다. 문단은 {plo}개에서 {phi}개다.\n"
            f"문단 하나가 300자를 넘어도 된다. 문단 안에서 이야기를 끝까지 끌고 간다.")


def build_system_prompt(order: WriteOrder) -> str:
    tag = (TAG_LINE_YES.format(tag_topic=order.tag_topic)
           if order.tag_topic else TAG_LINE_NO)
    parts = [
        COMMON,
        TOPIC_LAYER.format(tag_line=tag),
        FORM_REVIEW,
        APPROACH[order.approach],
        STANCE,
        TEMPERATURE[order.temperature],
        "[시작점]\n" + PERSONA[order.persona],
        "[글 배치]\n" + LAYOUT[order.layout],
        _length_layer(order),
    ]
    if order.material.reception:
        parts.append("[대중 평가 쓰는 법]\n" + RECEPTION_GUIDE[order.material.reception])
    if order.rewrite_note:
        parts.append("[다시 쓰기]\n앞 글이 아래 이유로 통과하지 못했다.\n" + order.rewrite_note)
        if order.previous_body:
            parts.append("앞에 쓴 글은 이것이다.\n---\n" + order.previous_body
                         + "\n---\n지적된 부분만 고친다. 통과한 부분은 그대로 둔다.")
        else:
            parts.append("앞 글은 주지 않는다. 같은 구조로 돌아가지 말고 처음부터 다시 쓴다.")
    return "\n\n".join(p.strip() for p in parts)

# ## 셀 5 — 재료 모으기 (TMDB + 위키백과) + 거르기

import requests

TMDB = "https://api.themoviedb.org/3"


def _tmdb(path: str, **params):
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "ko-KR")
    r = requests.get(f"{TMDB}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def search_movie(title: str, year: int | None = None) -> int | None:
    """제목으로 찾는다. 연도를 주면 같은 제목이 여럿일 때 그 해 것을 고른다."""
    params = {"query": title}
    if year:
        params["year"] = year
    hits = _tmdb("/search/movie", **params).get("results", [])
    if not hits and year:
        hits = _tmdb("/search/movie", query=title).get("results", [])
    return hits[0]["id"] if hits else None


def _kr_certification(movie_id: int) -> str | None:
    data = _tmdb(f"/movie/{movie_id}/release_dates", language="en-US")
    for row in data.get("results", []):
        if row.get("iso_3166_1") == "KR":
            for rel in row.get("release_dates", []):
                if rel.get("certification"):
                    return rel["certification"]
    return None


def _director_past_works(person_id: int, exclude_id: int, limit: int = 4) -> list[str]:
    data = _tmdb(f"/person/{person_id}/movie_credits")
    works = [c for c in data.get("crew", [])
             if c.get("job") == "Director" and c.get("id") != exclude_id]
    works.sort(key=lambda c: c.get("release_date") or "", reverse=True)
    return [c["title"] for c in works[:limit]]


def decide_reception(avg, votes) -> Reception | None:
    """갈래를 코드가 정한다. 그래야 에이전트 주장이 아니라 재료에 있는 사실이 된다."""
    if avg is None or votes is None or votes < MIN_VOTES:
        return None
    if avg >= PRAISE_LINE:
        return "호평이 많다"
    if avg <= CRITICISM_LINE:
        return "혹평이 많다"
    return "평가가 갈린다"


# ── 위키백과 주변 이야기 ─────────────────────────────────
_RE_REF      = re.compile(r"<ref[^>]*/>|<ref.*?</ref>", re.S)
_RE_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_RE_LINK     = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_RE_BOLD     = re.compile(r"'{2,}")
_RE_HEAD     = re.compile(r"^==+ ?(.+?) ?==+", re.M)


def fetch_wiki_context(title: str, year: int | None = None) -> tuple[str, str | None]:
    """한국어 위키백과에서 제작 · 반응 · 영향 · 여담 절만 모아 온다. 줄거리 절은 안 가져온다."""
    try:
        q = f"{title} {year} 영화" if year else f"{title} 영화"
        r = requests.get(WIKI_API, params={"action": "query", "list": "search",
                                           "srsearch": q, "format": "json"},
                         timeout=20).json()
        hits = r.get("query", {}).get("search", [])
        if not hits:
            return "", None
        page = hits[0]["title"]
        r = requests.get(WIKI_API, params={"action": "parse", "page": page,
                                           "prop": "wikitext", "format": "json"},
                         timeout=20).json()
        text = r.get("parse", {}).get("wikitext", {}).get("*", "")
    except Exception:
        return "", None

    text = _RE_REF.sub("", text)
    for _ in range(3):
        text = _RE_TEMPLATE.sub("", text)
    text = _RE_LINK.sub(r"\1", text)
    text = _RE_BOLD.sub("", text)

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

    url = "https://ko.wikipedia.org/wiki/" + page.replace(" ", "_")
    return "\n\n".join(chunks)[:CONTEXT_MAX_CHARS], url


def fetch_material(title: str, year: int | None = None,
                   today: str = "2026-09-18") -> Material | None:
    """제목으로 찾아서 재료를 만든다. 연도는 같은 제목이 여럿일 때 쓴다."""
    mid = search_movie(title, year)
    if mid is None:
        return None
    return fetch_material_by_id(mid, today=today, fallback_title=title)


def fetch_material_by_id(mid: int, today: str = "2026-09-18",
                         fallback_title: str = "") -> Material:
    title = fallback_title
    d = _tmdb(f"/movie/{mid}", append_to_response="credits")
    credits = d.get("credits", {})

    director, director_id = None, None
    for c in credits.get("crew", []):
        if c.get("job") == "Director":
            director, director_id = c.get("name"), c.get("id")
            break

    year = int(d["release_date"][:4]) if d.get("release_date") else None
    reception = decide_reception(d.get("vote_average"), d.get("vote_count"))
    context, context_url = fetch_wiki_context(d.get("title") or title, year)

    links = [f"https://www.themoviedb.org/movie/{mid}"]
    if context_url:
        links.append(context_url)

    return Material(
        tmdb_id=mid,
        title=d.get("title") or title,
        year=year,
        director=director,
        cast=[c["name"] for c in credits.get("cast", [])[:5]],
        genres=[g["name"] for g in d.get("genres", [])],
        runtime_min=d.get("runtime"),
        rating=_kr_certification(mid),
        release_date=d.get("release_date"),
        synopsis=d.get("overview", "") or "",
        director_past_works=(_director_past_works(director_id, mid) if director_id else []),
        source_work=None,
        context=context,
        context_source=context_url,
        reception=reception,
        reception_source=(f"TMDB 평점 분포 ({today} 기준)" if reception else None),
        source_links=links,
    )


# ── 거르기 ────────────────────────────────────────────────
def genre_ok(m: Material) -> tuple[bool, str]:
    hit = set(m.genres) & EXCLUDE_GENRES
    if hit:
        return False, f"제외 갈래다 ({', '.join(sorted(hit))})"
    if m.rating and m.rating.strip() in EXCLUDE_RATINGS:
        return False, f"제외 등급이다 ({m.rating})"
    return True, ""


def enough_material(m: Material) -> tuple[bool, str]:
    if len(m.synopsis) < MIN_SYNOPSIS_CHARS:
        return False, f"줄거리가 짧아 리뷰를 못 쓴다 ({len(m.synopsis)}자)"
    if not m.genres and not m.director and not m.source_work:
        return False, "갈래 · 감독 · 원작이 모두 없어 근거를 댈 수 없다"
    return True, ""


def material_to_text(m: Material) -> str:
    rows = []
    def add(label, value):
        if value:
            rows.append(f"{label}: {value}")

    add("제목", m.title)
    add("개봉", m.release_date)
    add("감독", m.director)
    add("출연", ", ".join(m.cast))
    add("갈래", ", ".join(m.genres))
    add("상영시간", f"{m.runtime_min}분" if m.runtime_min else None)
    add("등급", m.rating)
    add("원작", m.source_work)
    add("감독 이전 작품", ", ".join(m.director_past_works))
    add("줄거리", m.synopsis)

    if m.reception:
        rows.append(f"대중 평가 갈래: {m.reception} (출처: {m.reception_source})")
    else:
        rows.append("대중 평가 갈래: 없음 — 대중 평가를 쓰지 않는다")

    if m.context:
        rows.append(f"주변 이야기 (출처: {m.context_source}):\n{m.context}")
    else:
        rows.append("주변 이야기: 재료 없음 — 네가 확실히 아는 것만 쓴다")
    return "\n".join(rows)


# ── 무작위 영화 뽑기 ──────────────────────────────────────
TMDB_GENRE_ID = {"다큐멘터리": 99, "가족": 10751, "애니메이션": 16, "TV 영화": 10770}
DISCOVER_MIN_VOTES = 100
DISCOVER_YEAR_RANGE = (1980, 2026)
DISCOVER_SORTS = ["popularity.desc", "vote_average.desc", "vote_count.desc",
                  "primary_release_date.desc", "revenue.desc"]
DISCOVER_MAX_PAGE = 30


def pick_random_movies(n: int = 5, seed: int | None = None) -> list[dict]:
    """조건에 맞는 영화를 무작위로 n편 뽑는다."""
    rng = random.Random(seed)
    without = ",".join(str(TMDB_GENRE_ID[g]) for g in EXCLUDE_GENRES if g in TMDB_GENRE_ID)
    picked, seen, tries = [], set(), 0
    while len(picked) < n and tries < n * 6:
        tries += 1
        y0 = rng.randint(*DISCOVER_YEAR_RANGE)
        y1 = min(y0 + rng.randint(0, 5), DISCOVER_YEAR_RANGE[1])
        params = dict(sort_by=rng.choice(DISCOVER_SORTS),
                      page=rng.randint(1, DISCOVER_MAX_PAGE),
                      without_genres=without, include_adult="false", language="ko-KR")
        params["vote_count.gte"] = DISCOVER_MIN_VOTES
        params["primary_release_date.gte"] = f"{y0}-01-01"
        params["primary_release_date.lte"] = f"{y1}-12-31"
        try:
            rows = _tmdb("/discover/movie", **params).get("results", [])
        except Exception:
            continue
        rows = [r for r in rows if r.get("overview") and r["id"] not in seen]
        if not rows:
            continue
        r = rng.choice(rows)
        seen.add(r["id"])
        picked.append({"id": r["id"], "title": r.get("title"),
                       "year": (r.get("release_date") or "")[:4]})
    return picked

# ## 셀 6 — 글쓰기 에이전트 (Pydantic AI)

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

_model_settings = ({"openai_reasoning_effort": REASONING_EFFORT}
                   if USE_REASONING_EFFORT else None)
_usage_limits = UsageLimits(request_limit=REQUEST_LIMIT_PER_RUN)

writer = Agent(
    WRITER_MODEL,
    deps_type=WriteOrder,
    output_type=Article,
    model_settings=_model_settings,
    retries=1,
)


@writer.system_prompt
def _writer_system(ctx: RunContext[WriteOrder]) -> str:
    return build_system_prompt(ctx.deps)


async def write(order: WriteOrder) -> Article:
    user = "아래 재료로 리뷰를 쓴다.\n\n" + material_to_text(order.material)
    result = await writer.run(user, deps=order, usage_limits=_usage_limits)
    return result.output

# ## 셀 7 — 형태 검사 (LLM 없이 코드로)

STAR_MARKS = ("★", "☆", "⭐", "/10", "/5", "점 만점")
PUSH_WORDS = ("예매", "극장에서 꼭", "놓치지 마", "강력 추천", "무조건 봐")
LABEL_STARTS = ("좋은 점은", "아쉬운 점은", "장점은", "단점은", "한계는",
                "좋은 점이라면", "아쉬운 점이라면")
DEFINING_ENDS = ("작품이다", "영화이다", "영화다", "이야기다", "이야기이다")
RECEPTION_KEYS = ("호평", "혹평", "평이 갈", "평가가 갈", "호불호",
                  "평점", "관객 평", "반응이", "평이 박")
CONJ_OPENERS = ("그래서", "그러나", "다만", "하지만", "또한", "그럼에도", "그리고", "그만큼", "그래도")
HEDGE_ENDS = re.compile(r"(에 가깝다|인 셈이다|할 만하다|는 편이다|일 수 있다|로 보인다|로 느껴진다)[.]")
HEDGE_MAX = 1            # 흐리는 끝맺음 허용 횟수 (한 편)
NEUNDE_MAX_PER_PARA = 1  # "~는데" 잇기 허용 횟수 (한 문단)

_POLITE = re.compile(r"(습니다|입니다|합니다|됩니다|세요|해요|에요|예요|네요|거죠|죠)\s*[.?!\n]")
_NUMBER_UNIT = re.compile(r"\d+\s*(분|초|시간|번|회|차례|퍼센트|%|명|미터|m|배)")

# 끊김 검사 — 숫자 셋은 임시값이다
CHOP_AVG_MIN      = 30   # 평균 문장 길이가 이보다 짧으면 토막 난 것으로 본다
CHOP_SHORT_LEN    = 22   # 이보다 짧으면 "짧은 문장"이다
CHOP_SHORT_RUN    = 3    # 짧은 문장이 이만큼 연달아 나오면 잡는다
CHOP_SAME_END_RUN = 4
COPY_MIN_LEN      = 14   # 재료와 이만큼 이상 똑같이 겹치면 잡는다

# 이 문제로 걸리면 앞 글을 안 넘기고 새로 쓰게 한다
STRUCTURE_HINTS = ("너무 짧다", "연달아", "끝맺음", "그대로 옮긴", "같은 말로 시작",
                   "접속사", "늘어놓았다", "요소 이름", "규정하는 문장", "숫자로 말한",
                   "문단이 많다", "문단이 적다", "예시 문장", "흐리는", "는데")


def _example_text() -> str:
    """프롬프트에 든 예시 문장 전부. 본문이 이걸 그대로 옮기면 잡는다."""
    chunks = [COMMON, FORM_REVIEW, STANCE] + list(APPROACH.values()) + list(TEMPERATURE.values())
    lines = []
    for c in chunks:
        for ln in c.splitlines():
            t = ln.strip()
            if t.startswith(("예:", "이음:", "토막:", "말:", "숫자:", "독후감:", "평론가:",
                             "나쁜 예:", "좋은 예:")) or t.startswith('"'):
                lines.append(t.split(":", 1)[-1].strip().strip('"'))
    return " ".join(lines)


EXAMPLE_TEXT = _example_text()


def _sentences(body: str) -> list[str]:
    return [t.strip() for t in re.split(r"(?<=[.?!])\s+", body) if t.strip()]


def _reception_sentences(body: str) -> list[str]:
    return [s for s in re.split(r"[.!?\n]", body)
            if any(k in s for k in RECEPTION_KEYS)]


def _polite_endings(body: str) -> int:
    return len(_POLITE.findall(body))


def _choppiness(body: str) -> list[str]:
    sents = _sentences(body)
    if len(sents) < 5:
        return []
    bad = []
    avg = sum(len(t) for t in sents) / len(sents)
    if avg < CHOP_AVG_MIN:
        bad.append(f"문장이 너무 짧다 (평균 {avg:.0f}자). 앞뒤를 이어 쓴다")
    run, max_run = 0, 0
    for t in sents:
        run = run + 1 if len(t) < CHOP_SHORT_LEN else 0
        max_run = max(max_run, run)
    if max_run >= CHOP_SHORT_RUN:
        bad.append(f"짧은 문장이 {max_run}개 연달아 나온다. 사이를 이어 쓴다")
    ends = [t.rstrip(".?!")[-2:] for t in sents]
    same, max_same = 1, 1
    for a, b in zip(ends, ends[1:]):
        same = same + 1 if a == b else 1
        max_same = max(max_same, same)
    if max_same >= CHOP_SAME_END_RUN:
        bad.append(f"같은 끝맺음이 {max_same}문장 연달아 나온다. 끝을 바꿔 쓴다")
    return bad


def _copied_spans(body: str, material_text: str) -> list[str]:
    src = re.sub(r"\s+", "", material_text)
    txt = re.sub(r"\s+", "", body)
    found, i = [], 0
    while i + COPY_MIN_LEN <= len(txt):
        piece = txt[i:i + COPY_MIN_LEN]
        if piece in src:
            j = i + COPY_MIN_LEN
            while j < len(txt) and txt[i:j + 1] in src:
                j += 1
            found.append(txt[i:j])
            i = j
        else:
            i += 1
    return found


def _paragraph_openers(body: str) -> list[str]:
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    heads = [re.split(r"[\s,]", p, 1)[0] for p in paras]
    bad = []
    for a, b in zip(heads, heads[1:]):
        if a == b:
            bad.append(f"문단 두 개가 같은 말로 시작한다 ({a})")
            break
    conj = sum(1 for h in heads if h in CONJ_OPENERS)
    if conj >= 3:
        bad.append(f"문단 첫머리에 접속사가 {conj}번 나온다. 접속사 없이 시작한다")
    return bad


def is_structure_problem(reasons: list[str]) -> bool:
    return any(h in r for r in reasons for h in STRUCTURE_HINTS)


def shape_check(article: Article, order: WriteOrder) -> list[str]:
    bad = []
    body = article.body

    if not article.title.strip():
        bad.append("제목이 비었다")
    if article.title.strip() == order.material.title:
        bad.append("제목이 영화 제목과 똑같다")

    n = len(body)
    if n < order.min_chars:
        bad.append(f"글이 짧다 ({n}자, 최소 {order.min_chars}자)")
    if n > order.max_chars:
        bad.append(f"글이 길다 ({n}자, 최대 {order.max_chars}자)")

    n_para = len([p for p in body.split("\n\n") if p.strip()])
    plo, phi = paragraph_range(order.max_chars)
    if n_para < plo:
        bad.append(f"문단이 적다 ({n_para}개, 최소 {plo}개)")
    if n_para > phi:
        bad.append(f"문단이 많다 ({n_para}개, 최대 {phi}개). 문단을 합쳐 하나를 길게 끌고 간다")

    if not article.sources:
        bad.append("출처가 하나도 없다")

    for mark in STAR_MARKS:
        if mark in body:
            bad.append(f"점수 표시가 들어갔다 ({mark})")
            break
    for w in PUSH_WORDS:
        if w in body:
            bad.append(f"보러 가라고 부추기는 말이 들어갔다 ({w})")
            break
    for w in LABEL_STARTS:
        if w in body:
            bad.append(f"요소 이름을 문장에 그대로 썼다 ({w}). 이름 없이 내용으로 쓴다")
            break

    first_para = body.split("\n\n", 1)[0]
    first_sents = _sentences(first_para)
    if first_sents and first_sents[0].rstrip(".?!").endswith(DEFINING_ENDS):
        bad.append("첫 문장이 영화를 규정하는 문장이다. 본 것 하나로 시작한다")

    if "!" in body:
        bad.append("느낌표가 들어갔다")

    polite = _polite_endings(body)
    if polite:
        bad.append(f"존댓말이 들어갔다 ({polite}곳). 문어체 반말로 쓴다")

    hits = _NUMBER_UNIT.findall(body)
    if hits:
        bad.append(f"숫자로 말한 곳이 {len(hits)}군데다. 말로 바꿔 쓴다")

    bad += _choppiness(body)
    bad += _paragraph_openers(body)

    hedges = HEDGE_ENDS.findall(body)
    if len(hedges) > HEDGE_MAX:
        bad.append(f"판단을 흐리는 끝맺음이 {len(hedges)}번 나온다 ({', '.join(hedges[:3])}). 판단은 '~다'로 끝낸다")

    for p in body.split("\n\n"):
        k = len(re.findall(r"(?:는데|인데),", p))     # 쉼표 뒤로 잇는 "~는데," "~인데,"만 센다
        if k > NEUNDE_MAX_PER_PARA:
            bad.append(f"한 문단에 '~는데'로 잇는 문장이 {k}개다. 문단마다 하나다")
            break

    copied = _copied_spans(body, order.material.synopsis + " " + order.material.context)
    if copied:
        bad.append(f"재료 문장을 그대로 옮긴 곳이 {len(copied)}군데다. 예: '{copied[0][:20]}…'")

    for sent in _sentences(body):
        if sum(1 for c in order.material.cast if c in sent) >= 3:
            bad.append("출연진 이름을 한 문장에 늘어놓았다. 그 배우 이야기를 할 때만 이름을 쓴다")
            break

    ex = _copied_spans(body, EXAMPLE_TEXT)
    if ex:
        bad.append(f"프롬프트 예시 문장을 그대로 옮겼다. 예: '{ex[0][:20]}…'")

    said = _reception_sentences(body)
    if order.material.reception is None and said:
        bad.append("갈래 값이 없는데 대중 평가를 썼다")
    if said and re.search(r"\d", " ".join(said)):
        bad.append("대중 평가 문장에 숫자가 들어갔다")
    if len(said) > 2:
        bad.append("대중 평가를 여러 번 썼다")

    return bad

# ## 셀 8 — 판정관 (Pydantic AI)

JUDGE_SYSTEM = """
너는 영화 리뷰를 채점한다. 글을 고치지 않는다. 점수와 이유만 낸다.
누가 썼는지 묻지 않는다. 아래 기준만 본다.

[리뷰가 무엇인지]
리뷰는 이 영화를 볼지 말지 정하는 데 필요한 글이다.
무엇을 하려는 작품인지 해석만 하고 있으면 평론이다.
다른 작품과 견주는 데 글의 절반을 쓰고 있으면 심층 분석이다.

[항목]

토픽 적합도 (1~5)
  5 처음부터 끝까지 영화 이야기만 한다 / 3 다른 토픽이 한 문단쯤 섞였다 / 1 영화 이야기가 아니다

유형 일치 (1~5)
  5 볼지 말지 정하는 데 쓸 수 있다 / 3 리뷰와 평론이 섞였다 / 1 리뷰가 아니다
  배정된 접근(분석 · 해석 · 평가)이 글의 중심에 있는지도 여기서 본다. 접근이 안 보이면 3점을 넘기지 않는다.

요소 포함 (1~5)
  다섯 가지가 글 안에 있는지만 본다. 순서와 배치는 보지 않는다.
  (1) 이 영화를 어떻게 봤는지가 글에 드러난다
      첫 문단에 "~작품이다", "~영화다"로 영화를 규정하는 문장이 있으면 4점을 넘기지 않는다
  (2) 줄거리 (3) 좋은 점 (4) 아쉬운 점 (5) 누구에게 맞는지
  5 다섯 가지가 다 있다 / 3 하나가 빠졌다 / 1 셋 이상 없다
  한 문단에 여러 요소가 섞여 있어도 있으면 있는 것으로 본다.
  요소 이름이 본문에 단어로 나오면 안 된다. "좋은 점은", "아쉬운 점은"으로 시작하는 문장이 있으면 4점을 넘기지 않는다.

균형 (1~5)
  5 좋은 점과 아쉬운 점이 둘 다 있고 근거가 붙었다
  3 둘 다 있지만 한쪽에 근거가 없다
  1 한쪽만 있다

홍보성 (1~5, 높을수록 홍보 성격이 강하다)
  1 판단을 독자에게 맡긴다 / 3 칭찬 쪽으로 기울었다 / 5 보러 가라고 부추긴다

글 완성도 (1~5)
  같은 말 반복, 문장 끊김, 결론 없음, 앞뒤가 안 맞는 곳을 본다.
  줄거리가 글의 대부분이고 판단이 마지막에만 붙어 있으면 여기서 깎는다.
  독후감처럼 읽히면 여기서 깎는다. 설명하듯 풀어 주는 문장이 많으면 독후감이다.

[기본 사실이 재료와 어긋난 문장 개수]  → unsourced_claims
  기본 사실은 개봉일 · 감독 · 출연 · 상영시간 · 등급 · 원작 · 인물 이름 · 줄거리 사건이다.
  본문에서 이런 문장을 뽑아 아래 재료와 대조한다.
  재료와 다르게 적었거나, 재료에 없는 기본 사실을 적은 문장의 개수를 적는다.
  이 개수가 크면 글이 떨어진다.

  다음 문장도 이 개수에 더한다.
  - 어느 곡이 어디에 깔리는지, 어느 장면이 정확히 어떻게 찍혔는지처럼
    본 사람만 알 수 있는 것을 확인된 사실처럼 적은 문장
  - 대중 평가를 재료의 갈래 값보다 세게 말한 문장

  다음 문장은 이 개수에 더하지 않는다.
  - 좋다 · 아쉽다 같은 평가
  - 장면이 남긴 인상, 이야기 속도, 인물이 움직이는 방식 같은 감상
  - 줄거리를 근거로 댄 해석
  - 주변 이야기. 아래 항목에서 따로 다룬다

[재료에 없는 주변 이야기 문장 목록]  → unverified_context
  주변 이야기는 제작 뒷이야기, 개봉했을 때 반응, 영화 밖에서 생긴 일, 나중 작품에 남긴 것이다.
  본문에서 이런 문장을 뽑아 재료의 '주변 이야기' 항목과 대조한다.
  재료에 있으면 넘어간다.
  재료에 없으면 그 문장을 그대로 목록에 적는다. 틀렸다고 보지 않는다. 점수도 깎지 않는다.
  이 목록은 사람이 나중에 확인한다.

[스포일러]
  결말이나 주요 반전을 밝혔으면 참으로 적는다.
  재료의 줄거리에 이미 적혀 있는 내용은 스포일러가 아니다.

[이유]
  점수를 깎은 자리를 문장으로 적는다. 무엇을 고쳐야 하는지 적는다.
"""

judge = Agent(
    JUDGE_MODEL,
    output_type=JudgeScore,
    system_prompt=JUDGE_SYSTEM,
    model_settings=_model_settings,
    retries=1,
)


async def score(article: Article, order: WriteOrder) -> JudgeScore:
    user = (
        f"[배정 토픽] {order.topic}\n"
        f"[배정 유형] 리뷰\n"
        f"[배정 접근] {order.approach}\n\n"
        f"[재료]\n{material_to_text(order.material)}\n\n"
        f"[제목]\n{article.title}\n\n"
        f"[본문]\n{article.body}\n\n"
        f"[붙은 출처]\n" + "\n".join(article.sources)
    )
    result = await judge.run(user, usage_limits=_usage_limits)
    return result.output

# ## 셀 9 — 업로드 규칙 (LLM 없이 코드로)

def verdict(s: JudgeScore) -> tuple[str, str]:
    """판정관은 정하지 않는다. 규칙이 정한다."""
    drop, again = [], []

    def band(name, value, pass_line, drop_line):
        if value >= pass_line: return
        if value <= drop_line: drop.append(f"{name} {value}점")
        else:                  again.append(f"{name} {value}점")

    band("토픽 적합도", s.topic_fit,    PASS["topic_fit"],    2)
    band("유형 일치",   s.form_fit,     PASS["form_fit"],     2)
    band("요소 포함",   s.element_fit,  PASS["element_fit"],  2)
    band("균형",        s.balance,      PASS["balance"],      1)
    band("글 완성도",   s.completeness, PASS["completeness"], 1)

    if s.promo >= 4:
        drop.append(f"홍보성 {s.promo}점")
    elif s.promo > PASS["promo"]:
        again.append(f"홍보성 {s.promo}점")

    if s.unsourced_claims > UNSOURCED_REWRITE:
        drop.append(f"기본 사실이 재료와 어긋난 문장 {s.unsourced_claims}개")
    elif s.unsourced_claims > UNSOURCED_PASS:
        again.append(f"기본 사실이 재료와 어긋난 문장 {s.unsourced_claims}개")

    if s.spoiler:
        again.append("결말을 밝혔다")

    if drop:  return "탈락", " / ".join(drop) + " — " + s.reason
    if again: return "다시쓰기", " / ".join(again) + " — " + s.reason
    return "올림", ""

# ## 셀 10 — 한 편 돌리기

async def produce(material: Material, topic="영화", tag_topic=None,
                  approach=None, temperature=None, persona=None, layout=None) -> dict:
    ok, why = genre_ok(material)
    if not ok:
        return {"상태": "대상아님", "이유": why, "로그": []}
    ok, why = enough_material(material)
    if not ok:
        return {"상태": "재료부족", "이유": why, "로그": []}

    approach = approach or random.choice(APPROACHES)
    lo, hi = pick_length(approach)

    order = WriteOrder(
        material=material, topic=topic, tag_topic=tag_topic,
        approach=approach,
        temperature=temperature or random.choice(TEMPERATURES),
        persona=persona or random.choice(PERSONAS),
        layout=layout or random.choice(LAYOUTS),
        min_chars=lo, max_chars=hi,
    )

    log, article, unverified = [], None, []
    for attempt in range(MAX_REWRITE + 1):
        article = await write(order)

        bad = shape_check(article, order)
        if bad:
            log.append({"회차": attempt, "단계": "형태검사", "결과": bad})
            order.rewrite_note = " / ".join(bad)
            order.previous_body = None if is_structure_problem(bad) else article.body
            continue

        s = await score(article, order)
        result, reason = verdict(s)
        unverified = s.unverified_context
        log.append({"회차": attempt, "단계": "판정관",
                    "접근": order.approach, "온도": order.temperature,
                    "시작점": order.persona, "배치": order.layout,
                    "분량": [order.min_chars, order.max_chars],
                    "점수": s.model_dump(exclude={"unverified_context", "reason"}),
                    "확인 필요한 주변 이야기": s.unverified_context,
                    "결과": result, "이유": reason})

        if result == "올림":
            return {"상태": "올림", "글": article, "주문": order,
                    "확인필요": unverified, "로그": log}
        if result == "탈락":
            return {"상태": "탈락보관", "글": article, "주문": order,
                    "확인필요": unverified, "로그": log}

        order.rewrite_note = reason
        order.previous_body = article.body

    return {"상태": "탈락보관", "글": article, "주문": order,
            "확인필요": unverified, "로그": log}


def show(out: dict):
    print("상태:", out["상태"])
    if out.get("이유"):
        print("이유:", out["이유"])
    for row in out["로그"]:
        print(json.dumps(row, ensure_ascii=False))
    if out.get("글"):
        o = out["주문"]
        print(f"\n[{o.approach} · {o.temperature} · {o.persona} · {o.layout} · "
              f"{o.min_chars}~{o.max_chars}자]")
        print("=" * 60)
        print(out["글"].title)
        print("-" * 60)
        print(out["글"].body)
        print("-" * 60)
        print("출처:", ", ".join(out["글"].sources))
        if out.get("확인필요"):
            print("\n사람이 확인할 문장:")
            for t in out["확인필요"]:
                print(" -", t)

# ## 셀 11 — 마크다운으로 저장하고 zip으로 묶기

from datetime import date

OUT_DIR = HERE.parent / "out" / "movie_review"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    return re.sub(r"\s+", "_", text.strip())[:60]


def to_markdown(out: dict, today: str | None = None) -> str:
    today = today or date.today().isoformat()
    a, o = out.get("글"), out.get("주문")
    m = o.material if o else None

    lines = []
    if a:
        lines += [f"# {a.title}", ""]
    if m:
        lines += [f"**영화** {m.title}" + (f" ({m.year})" if m.year else ""), ""]
    lines += [f"**상태** {out['상태']} · **버전** {VERSION}"]
    if out.get("이유"):
        lines += [f"**이유** {out['이유']}"]
    if o:
        lines += [f"**접근** {o.approach} · **온도** {o.temperature} · **시작점** {o.persona} · "
                  f"**배치** {o.layout} · **분량** {o.min_chars}~{o.max_chars}자"]
    lines += [f"**만든 날** {today}", "", "---", ""]

    if a:
        lines += [a.body.strip(), "", "---", "", "## 출처", ""]
        lines += [f"- {u}" for u in a.sources]
        lines += [""]

    if out.get("확인필요"):
        lines += ["## 사람이 확인할 문장 — 재료에 없는 주변 이야기", ""]
        lines += [f"- [ ] {t}" for t in out["확인필요"]]
        lines += [""]

    if m:
        lines += ["## 재료", "", "```", material_to_text(m), "```", ""]

    lines += ["## 로그", ""]
    for row in out["로그"]:
        lines += [f"- 회차 {row['회차']} · {row['단계']} · 결과 {row.get('결과')}"]
        if row.get("이유"):
            lines += [f"  - 이유: {row['이유']}"]
        if row.get("점수"):
            lines += [f"  - 점수: `{json.dumps(row['점수'], ensure_ascii=False)}`"]
    return "\n".join(lines)


def save_md(out: dict) -> Path:
    o = out.get("주문")
    title = out["글"].title if out.get("글") else "글없음"
    movie = o.material.title if o else "영화없음"
    path = OUT_DIR / f"{_safe_name(movie)}__{_safe_name(title)}.md"
    path.write_text(to_markdown(out), encoding="utf-8")
    return path


def download_all():
    """out/movie_review 폴더를 zip 하나로 묶는다. 콜랩의 files.download 자리다."""
    import shutil
    zip_path = shutil.make_archive(str(OUT_DIR), "zip", OUT_DIR)
    print("zip:", zip_path)
    return zip_path


# ======================================================================
# 실행 — 콜랩 셀 12~14를 서브커맨드로 옮겼다
#   python movie_review_agent.py one "괴물" 2006      # 셀 12 — 한 편
#   python movie_review_agent.py list                 # 셀 13 — 테스트 목록 다섯 편
#   python movie_review_agent.py random 5 [--seed N]  # 셀 14 — 무작위 N편
# ======================================================================

TEST_TITLES = [
    ("오디세이", 2026),
    ("괴물", 2006),
    ("프로젝트 헤일메리", 2026),
    ("케빈에 대하여", 2011),
    ("판의 미로", 2006),
]


def _list_saved():
    print("\n저장된 파일:")
    for p in sorted(OUT_DIR.glob("*.md")):
        print(" -", p.name)


async def cmd_one(title: str, year: int):
    """셀 12 — 한 편 돌려보기"""
    m = fetch_material(title, year)
    if m is None:
        print(f"[{title}] TMDB에서 못 찾았다")
        return
    print(material_to_text(m))
    print()
    out = await produce(m)
    show(out)

    if out.get("글"):
        p = save_md(out)
        print("\n저장:", p)


async def cmd_list(titles=TEST_TITLES):
    """셀 13 — 테스트 목록 다섯 편 돌리고 zip으로 묶기"""
    results = []
    for t, y in titles:
        mat = fetch_material(t, y)
        if mat is None:
            print(f"[{t}] TMDB에서 못 찾았다")
            continue
        print(f"[{t}] 찾은 것: {mat.title} ({mat.year}) · 감독 {mat.director}")
        r = await produce(mat)
        results.append((mat.title, r))
        tag = f" — {r.get('이유', '')}" if r["상태"] in ("대상아님", "재료부족") else ""
        print(f"[{mat.title}] {r['상태']}{tag}")
        if r.get("글"):
            save_md(r)

    _list_saved()
    download_all()
    return results


async def cmd_random(n: int = 5, seed=None):
    """셀 14 — 무작위 영화로 여러 편 돌리고 zip으로 묶기"""
    cands = pick_random_movies(n, seed=seed)
    print("뽑힌 영화:")
    for c in cands:
        print(f" - {c['title']} ({c['year']})  id={c['id']}")
    print()

    for c in cands:
        mat = fetch_material_by_id(c["id"], fallback_title=c["title"] or "")
        r = await produce(mat)
        tag = f" — {r.get('이유', '')}" if r["상태"] in ("대상아님", "재료부족") else ""
        print(f"[{mat.title}] {r['상태']}{tag}")
        if r.get("글"):
            save_md(r)

    _list_saved()
    download_all()


def main(argv=None):
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # 윈도우 콘솔에서 — · 같은 글자가 깨지지 않게

    ap = argparse.ArgumentParser(description="영화 리뷰 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("one", help="한 편 돌린다 (셀 12)")
    a.add_argument("title"); a.add_argument("year", type=int)

    sub.add_parser("list", help="테스트 목록 다섯 편 (셀 13)")

    a = sub.add_parser("random", help="무작위 N편 (셀 14)")
    a.add_argument("n", type=int, nargs="?", default=5)
    a.add_argument("--seed", type=int, default=None)

    args = ap.parse_args(argv)
    if args.cmd == "one":
        asyncio.run(cmd_one(args.title, args.year))
    elif args.cmd == "list":
        asyncio.run(cmd_list())
    elif args.cmd == "random":
        asyncio.run(cmd_random(args.n, seed=args.seed))


if __name__ == "__main__":
    main()
