# ani_rag — me;nu 정보사전 (RAG) 에이전트

작품 · 캐릭터 · 인물 · 곡처럼 이미 알려진 사실을 출처와 함께 모아 둔 정보사전과, 그 사전으로 글을 만드는 새 에이전트.
기존 에이전트(`agents/ani_me;nu`)는 그대로 두고 따로 만든다.

- 맡는 콘텐츠 유형: 작품 가이드 · 캐릭터 · 인물 · 음악 · 큐레이션 (소식 · 행사 · 굿즈 · 리뷰 · 유머는 기존 에이전트)
- 사전은 나중에 챗봇 사실 검증에도 쓴다
- 출처 · 약관 정리: 노션 [RAG 에이전트 정리](https://app.notion.com/p/RAG-410b1e30068b80d88b49ec93b1791f01)

## 사전 DB 띄우기

1. `.env.example` 을 `.env` 로 복사하고 값을 채운다 (`.env` 는 레포에 안 올라감)
2. 컨테이너 띄우기

   ```
   docker compose -f agents/rag/compose.yaml up -d
   ```

3. 표 만들기 (여러 번 돌려도 같은 결과)

   ```
   docker exec -i menu-dict sh -c 'psql -U "$POSTGRES_USER" -d menu_dict -v ON_ERROR_STOP=1' < agents/rag/db/schema.sql
   ```

## 사전 채우기

`agents/rag` 폴더에서 돌린다. 모두 여러 번 돌려도 같은 결과.

```
uv sync
docker exec -i menu-dict sh -c 'psql -U "$POSTGRES_USER" -d menu_dict -v ON_ERROR_STOP=1' < db/sources.sql
uv run python -m ani_rag.load_anilist
uv run python -m ani_rag.fetch_anilist_status
uv run python -m ani_rag.load_wikidata
uv run python -m ani_rag.fetch_anilist_characters
uv run python -m ani_rag.load_animethemes
uv run python -m ani_rag.load_kmrb
uv run python -m ani_rag.fetch_anilist_media_extra
uv run python -m ani_rag.fetch_anilist_sources
uv run python -m ani_rag.fetch_anilist_cast
uv run python -m ani_rag.fetch_anilist_staff
uv run python -m ani_rag.fetch_anilist_characters
uv run python -m ani_rag.load_syoboi
uv run python -m ani_rag.load_madb
uv run python -m ani_rag.load_wikidata_extra
uv run python -m ani_rag.load_wikipedia
uv run python -m ani_rag.link_theme_songs
uv run python -m ani_rag.load_musicbrainz
uv run python -m ani_rag.report_verification
uv run python -m ani_rag.report_title_ko
```

위 순서대로 돌린다. `fetch_anilist_characters` 는 `fetch_anilist_cast` 뒤에 (새로 생긴 캐릭터까지 받으려고),
`link_theme_songs` 는 `load_animethemes` · `load_syoboi` 뒤에, `load_musicbrainz` 는 `load_wikidata_extra` · `link_theme_songs` 뒤에 돌린다.

| 순서 | 무엇 | 밖으로 보내는 요청 |
|---|---|---|
| `db/sources.sql` | 출처 목록 (약관 요점 · 출처 표시 의무 · 상업 전환 시 재확인) | 없음 |
| `ani_rag.load_anilist` | 모델 DB 의 AniList 원본 → 작품 · 시즌/극장판 · 스튜디오 · 인물 · 캐릭터 + 사실 + 별칭. 모델 DB 에 없는 번호(신작)는 AniList 에서 받아 합침 | 모델 DB 에 없는 시즌/극장판 ÷ 10번, 4초 간격 |
| `ani_rag.fetch_anilist_status` | AniList 에서 형식 · 방영 상태 · 시작/끝 날짜 · 다음 방영 회차 | 시즌/극장판 ÷ 50번 (3,887개면 78번), 4초 간격 |
| `ani_rag.load_wikidata` | 위키데이터: 다른 사이트 번호 · 방영 날짜 · 화수 · 제작사 · 감독 · 한국어 이름 · 생일 | 질의 22번, 2초 간격 |
| `ani_rag.fetch_anilist_characters` | AniList 캐릭터 생일 · 나이 · 혈액형 · 성별 · 다른 이름 | 캐릭터 ÷ 50번 (23,581명이면 472번), 4초 간격 |
| `ani_rag.load_animethemes` | AnimeThemes 주제가(OP/ED) · 곡 · 가수 · 회차 | 시즌/극장판 ÷ 100번 (39번), 1.2초 간격 |
| `ani_rag.load_kmrb` | 영상물등급위원회: 극장판 한국 공식 제목 · 국내 관람등급, OTT 국내 서비스 (`.env` 의 `KMRB_SERVICE_KEY` 필요) | 전체 ÷ 1,000번 (67번), 1초 간격 |
| `ani_rag.fetch_anilist_media_extra` | AniList 공식 사이트 · 해시태그 · 외부 링크 · 한국어 스트리밍 링크 · 순위 | 시즌/극장판 ÷ 50번, 4초 간격 |
| `ani_rag.fetch_anilist_sources` | AniList 원작(만화 · 소설) 권수 · 연재 상태 · 작가 | 원작 ÷ 25번, 4초 간격 |
| `ani_rag.fetch_anilist_cast` | AniList 캐릭터 목록 · 일본 성우 · 한국 성우 (상위 300편은 더 깊게) | 1차 시즌 ÷ 10번 + 2차 쪽마다 1번, 4초 간격 |
| `ani_rag.fetch_anilist_staff` | AniList 인물 직업 · 생년월일 · 출신지 · 활동 시기 · 소개 글(참고용) | 인물 ÷ 50번, 4초 간격 |
| `ani_rag.load_syoboi` | 쇼보이 회차 제목 · 주제가 일본어 원제 · 작사 작곡 · 스태프 · 캐스트 · 공식 사이트 (arm 대응표로 연결) | TID ÷ 100번, 1.5초 간격 |
| `ani_rag.load_madb` | 일본 미디어예술DB 방영일 · 화수 · 감독 · 제작사 · 주제가 (세 번째 검산 출처) | 파일 3개 |
| `ani_rag.load_wikidata_extra` | 위키데이터 공식 사이트 · X 계정 · 수상 · 배경 장소 · 흥행 수입 · 원작 정보 · 인물 직업 · 출신지 · 소속 · 스튜디오 설립일 | 항목 800개씩, 2초 간격 |
| `ani_rag.load_wikipedia` | 위키백과 작품 본문(한 · 영) · 인물 첫 문단(한) — 참고용 글 | 문서마다 1번, 1초 간격 |
| `ani_rag.link_theme_songs` | AnimeThemes 곡 ↔ 쇼보이 일본어 원제 · 크레딧 잇기 | 없음 |
| `ani_rag.load_musicbrainz` | 주제가 가수 정보 · 한국어 이름 · 곡 발매일 (번호로만 찾음) | 가수마다 2번 안팎, 1.2초 간격 |
| `ani_rag.report_verification` | 칸별 '여러 곳 일치 / 서로 다름 / 한 곳뿐' + 출처 쌍 일치율 → `.cache/reports/verification.md` | 없음 |
| `ani_rag.report_title_ko` | 확정 한국어 제목이 없는 작품의 후보표 → `.cache/reports/title_ko_candidates.csv` (작품 목록 담당이 확인용) | 없음 |
| `ani_rag.report_agreement` | AniList ↔ 위키데이터 두 출처 일치율 + 안 맞는 예시 | 없음 |

- 칸마다 맡는 코드는 하나다 (같은 출처 · 같은 칸을 두 코드가 쓰면 서로 지움). 맡는 칸은 각 파일 맨 위에 적어 둠
- 받은 원본은 `.cache/raw/<출처>/<날짜>/` 에 저장하고 같은 날 다시 돌리면 다시 요청하지 않는다 (레포에 안 올림)
- 다른 출처를 우리 작품에 맞출 때는 정리한 제목이 별칭과 똑같고 연도 · 나라가 맞을 때만 붙이고, 후보가 둘 이상이면 넣지 않는다.
  맞추기 재료에서 자기 출처가 만든 별칭은 뺀다 (돌릴 때마다 결과가 달라지지 않게)
- 위키데이터 항목 하나가 시즌 여러 개에 붙어 있으면(시리즈 전체 항목) 그 사실은 작품에 붙인다

`.env` 칸: `MENU_DICT_DB_USER` · `MENU_DICT_DB_PASSWORD` (사전 DB), `MODEL_DB_ENV` (모델 DB 설정 파일 위치 —
비밀번호를 복사하지 않고 그 파일을 읽음), `MENU_CATALOG_DIR` (작품 목록 폴더, 비우면 레포 맨 위의 `catalog`)

- 작품 한국어 이름(`entity.name_ko`)은 작품 목록에서 확인이 끝난 것만 넣는다. 확인 필요는 후보 별칭
- 영어 줄거리(`synopsis_en_ref`)는 참고용이고 글에 문장을 옮기지 않는다
- 받은 날짜는 모델 DB 에 넣은 날짜(10/21)로 대신한다

## 표 (`db/schema.sql`)

| 표 | 한 줄 |
|---|---|
| `dict.source` | 출처 하나 (약관 요점 · 출처 표시 의무 · 상업 전환 시 재확인) |
| `dict.entity` | 대상 하나 (작품 · 시즌/극장판 · 캐릭터 · 인물 · 곡 · 스튜디오) |
| `dict.external_id` | 외부 번호 하나 → 대상 (AniList · MAL · 위키데이터 · AnimeThemes …) |
| `dict.alias` | 별칭 하나 (기사 속 제목을 대상에 연결, 비슷한 글자 찾기) |
| `dict.fact` | 사실 하나 (대상 · 칸 · 값 · 출처 · 근거 주소 · 받은 날짜). 출처마다 한 줄 |
| `dict.fact_agreement` (보기) | 같은 사실을 몇 출처에서 확인했나 |
