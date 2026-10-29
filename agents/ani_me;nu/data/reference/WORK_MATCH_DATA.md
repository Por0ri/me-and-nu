# 작품 매칭용 이름 사전

기사 제목에 작품명 없이 캐릭터나 가수만 나오는 경우가 많다(예: 「ロック・リーのグッズが登場」, 「SPYAIR 내한」).
아래 두 파일은 그런 이름을 `menu_works.csv`의 `menu_work_id`로 연결한다.
LLM 추측이 아니라 공개 데이터로 확인된 연결만 담는다.

두 CSV는 서버(git)에 올리지 않는다(`.gitignore`). 슬랙으로 공유받거나 아래 스크립트로 만들어서 로컬에만 둔다.
기본 위치는 이 폴더(`agents/ani_me;nu/data/reference/`)이고, 다른 곳(예: `model/catalog/`)에 두면
환경변수 `ANI_MENU_WORK_MATCH_DIR`에 그 폴더를 적는다. 파일이 없으면 매칭은 아무것도 찾지 않을 뿐 오류는 나지 않는다.

| 파일 | 내용 | 출처 | 라이선스·이용 조건 |
|---|---|---|---|
| `work_characters.csv` | 캐릭터 등 작품 속 고유명사의 일본어·영어·한국어 이름과 별칭 | Wikidata: 작품에 등장(P1441). 애니 항목(AniList 번호 P8729)에 직접 연결된 것, 원작(P144)·시리즈(P179)를 거쳐 연결된 것 | CC0 (대량 이용 가능) |
| `work_theme_songs.csv` | 작품별 OP·ED 곡명과 가수 | AnimeThemes API, AniList 번호로 조회 | 참고용(robots.txt `Content-Signal: use=reference, ai-train=no`). 학습 데이터로 쓰지 않는다 |

## 열

`work_characters.csv`

- `menu_work_id`, `anilist_id`: 연결된 작품. AniList 번호는 카탈로그의 `anilist_ids` 중 하나
- `wikidata_id`: 캐릭터(또는 작품 속 조직·장소 등) Wikidata 항목
- `via`: 연결 경로. `anime`(애니 항목에 직접) > `source`(원작) > `series`(시리즈) 순으로 확실하다
- `lang`, `kind`: 이름 언어(`ja`·`en`·`ko`), 대표 이름(`label`)인지 별칭(`alias`)인지
- `name`: 이름

`work_theme_songs.csv`

- `menu_work_id`, `anilist_id`, `theme`(OP1·ED2 등), `song_title`(로마자 표기), `artists`(` / `로 구분), `animethemes_anime`

## 쓰는 법

`src/ani_menu_agent/work_aliases.py`의 `find_named_works(text)`가 문장 속 캐릭터·곡명·가수 이름을 찾아 작품을 돌려준다.

- 여러 작품에 같은 이름이 있으면(예: 서로 다른 작품의 「サンジ」) 쓰지 않는다.
- 로마자 캐릭터 이름은 쓰지 않는다. 「Episode」「Live」「Reborn」「Red Hood」처럼 영어 단어와 겹쳐 오탐이 많았고, 영어 기사는 대개 제목에 작품명이 있다.
- 짧은 이름은 쓰지 않는다. 가타카나는 4자 이상(「ロック・リー」처럼 가운뎃점으로 이은 전체 이름은 3자부터), 한자 3자, 한글 2자부터. 「リード」「スピカ」 같은 외래어 오탐을 막는다.
- 다른 단어 안에 든 이름은 무시한다(「쓰나미」 속 「나미」, 「ルフィーナ」 속 「ルフィ」). 한글 이름은 뒤에 조사·띄어쓰기·문장부호가 올 때만 인정한다.
- 로마자 곡명은 세 단어 이상만 쓴다(「Guren no Yumiya」는 되고 「Every Day」는 안 된다).
- 가수 이름은 4자 이상이고 대소문자까지 같을 때만 인정한다(「LiSA」, 「SPYAIR」, 「SPY AIR」).
- 가수는 여러 작품에 걸치므로 그 가수가 부른 작품을 모두 돌려준다. 밴드 자체 소식(투어·페스티벌)도 잡히므로, 한 작품의 근거가 아니라 "이 가수가 부른 애니" 목록을 알려 주는 약한 근거로만 쓴다.

## 실제 크롤링 데이터로 잰 효과 (2026-10-29)

`ani_menu_generation_200` DB의 기사 후보·원문 캡처 제목 388개 기준:

- 제목에 작품명 있음 234개, 작품 하나만 다루는 공식 사이트 35개
- 나머지 119개 중 캐릭터 이름으로 찾은 것 약 10개, 모두 맞음(「フリーレン」「シュタルク」→ 장송의 프리렌, 「烏鷺亨子」→ 주술회전 등)
- 가수 이름으로 잡힌 약 8개는 대부분 밴드 자체 소식이라 작품 근거로는 부적합
- 남은 약 97개는 대부분 애니 기사가 아니거나(편성표 조각, 개그·음악 소식) 작품 목록에 없는 작품

## 다시 만들기

```
uv run python scripts/build_work_match_data.py                    # 둘 다
uv run python scripts/build_work_match_data.py --only characters  # 캐릭터만
```

요청 사이에 2초씩 쉬고, Wikidata는 AniList 번호 200개, AnimeThemes는 50개씩 묶어 조회한다. 전체 수집에 10~20분 걸린다.
AniList API는 약관상 대량 수집이 금지라 이 사전을 만드는 데 쓰지 않는다. 사전에 없는 마이너 캐릭터는 기사를 처리할 때 한 건씩 조회하는 방식이 맞다.

## 한계

- 일본어 곡명이 없다. AnimeThemes는 로마자 곡명만 준다. 「紅蓮の弓矢」 같은 일본어 곡명은 아직 못 찾는다.
- 한국어 이름은 일부만 있다. Wikidata 한국어 이름이 있는 캐릭터만 찾는다.
- Wikidata에 캐릭터가 등록되지 않은 작품은 빠진다.
