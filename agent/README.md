# agent — 콘텐츠 크리에이팅 에이전트

토픽별로 콘텐츠를 만드는 에이전트를 모아 둔다. 공통 뼈대는 pydantic-ai + OpenAI 모델이다.
원본은 Google Colab 노트북이었고, 여기 있는 것은 그것을 스크립트로 옮긴 것이다.

```
agent/
├── requirements.txt        공통 의존성
├── .env.example            키 이름 (LLM_KEY, TMDB_KEY)
├── movie/
│   ├── movie_info_agent.py     영화 · 정보 전달 (V2.3)
│   └── movie_review_agent.py   영화 · 리뷰 (V1.8)
├── music/
│   └── music_review_agent.py   음악 · 리뷰 (v2.5)
└── out/                    결과물 (git에 안 올라간다)
```

## 흐름

| 파일 | 토픽 · 유형 | 흐름 |
|---|---|---|
| `movie/movie_info_agent.py` | 영화 · 정보 전달 | 재료 모으기(TMDB + 위키백과 + 기사) → 재료 판정 → 재료 정리 → 글쓰기 → 편집국장 → 형태 검사 → 판정관 → 업로드 규칙 → 고치기 |
| `movie/movie_review_agent.py` | 영화 · 리뷰 | 재료 모으기(TMDB + 위키백과) → 글쓰기 → 형태 검사 → 판정관 → 업로드 규칙 |
| `music/music_review_agent.py` | 음악 · 리뷰 | 앨범 뽑기(MusicBrainz · 매체 목록) → 위키백과 맥락 → 평론 링크 검색 → 본문 읽기 → 코드로 거르기 → 재료 판정 → 관점 묶기 → 기획자 → 작가 → 교정자 → 형태 검사 → 편집국장 |

세 에이전트 모두 마디(node)가 상태 하나를 받아 자기 칸만 채우고 돌려주는 구조다.
갈림길 함수가 다음 마디를 고른다. 한 편에 도는 마디 수와 모델 요청 수에 상한이 있다.

## 준비

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env      # 키를 채운다
```

`.env`에 넣는 키

• `LLM_KEY` — OpenAI API 키. 세 에이전트 공통
• `TMDB_KEY` — TMDB API 키. movie/ 두 에이전트

키는 코드에 적지 않는다. `.env` 또는 환경변수로만 읽는다.

## 돌리기

```bash
# 영화 · 정보 전달
python movie/movie_info_agent.py material "프로젝트 헤일메리" 2026   # 재료만 본다
python movie/movie_info_agent.py one "프로젝트 헤일메리" 2026        # 한 편
python movie/movie_info_agent.py released 5                          # 개봉작 무작위 5편
python movie/movie_info_agent.py upcoming 5                          # 개봉 전 무작위 5편

# 영화 · 리뷰
python movie/movie_review_agent.py one "괴물" 2006
python movie/movie_review_agent.py list                 # 테스트 목록 다섯 편
python movie/movie_review_agent.py random 5 --seed 1

# 음악 · 리뷰
python music/music_review_agent.py check                # 검색 엔진 · 매체 · RSS 점검
python music/music_review_agent.py run --n 5            # 올린 글이 5편 될 때까지
python music/music_review_agent.py zip
```

결과는 `agent/out/<이름>/`에 마크다운으로 쌓인다. 여러 편 돌리면 끝에 zip으로도 묶는다.

## 콜랩에서 돌릴 때

```
!pip -q install -r agent/requirements.txt
import os; from google.colab import userdata
os.environ["LLM_KEY"] = userdata.get("LLM_KEY"); os.environ["TMDB_KEY"] = userdata.get("TMDB_KEY")
!python agent/movie/movie_info_agent.py one "프로젝트 헤일메리" 2026
```

## 설정 고치는 곳

각 파일 위쪽 "키와 설정" 자리 하나만 고친다. 모델 이름 · 상한 · 통과선 · 글 길이가 거기 있다.
