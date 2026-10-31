# model — 콘텐츠 추천 모델

me;nu 피드에 콘텐츠를 어떤 순서로 보여줄지 정하는 코드. 기준 문서는 Notion의 콘텐츠 명세서 v0.2, 로그 명세서 v0.2, ERD V0.7이다.

## 폴더

| 경로 | 내용 |
|---|---|
| `algo/content_v0.py` | v0 추천. 규칙 점수(유형·장르·작품·최신·반응) → 같은 작품 연달아 금지 → 스포일러 흐림 |
| `scripts/make_sample_data.py` | 테스트용 샘플 작품·콘텐츠·유저 생성 |
| `sample/` | 샘플 데이터 (생성 결과, 커밋함) |
| `notebooks/v0_demo.ipynb` | 결과 확인용 노트북. 유저별 추천과 점수 내역, 가중치 바꿔 보기 |
| `data/` | 공개 데이터·AniList 원본 등 큰 파일. **커밋 안 함** |

## 실행

저장소 루트에서 실행한다. 표준 라이브러리와 numpy를 쓴다 (`pip install -r model/requirements.txt`).

```bash
python model/scripts/make_sample_data.py   # 샘플 데이터 다시 만들기
python -m model.algo.content_v0            # 샘플 유저 6명 추천 결과
```

## 버전

| 버전 | 내용 | 상태 |
|---|---|---|
| v0 | 규칙 점수. 학습 없음 | 완료 (샘플 데이터) |
| v0.5 | 작품 유사도(MAL), 태그 관련도, 장기·단기 취향, 채점기로 비교 | 예정 10/19~24 |
| v1 | 매일 배치 → `recommendation_round`/`recommendation_item` 저장 → 피드 연결 | 예정 10/24~30 |

## 서비스 추천 배치 로컬 검증 (2026-10-29)

`model/scripts/daily_recommend.py`는 온보딩·현재 좋아요/저장/OX·활동 로그를 읽고 `recommendation_round`와 `recommendation_item`에 하루 순위를 저장한다. 알고리즘 가중치는 변경하지 않았다. API 컨테이너와 분리한 모델 환경에 `model/requirements.txt`를 설치한다. 이 배치에는 임베딩 생성 환경(`requirements-embed.txt`, PyTorch)이나 LLM 호출이 필요하지 않다.

### 원본 데이터 사전 검사

저장소 루트에서 실행한다. 다음 두 명령은 실제 DB에 접속하거나 `backend/.env`를 읽거나 데이터를 다운로드하지 않는다.

```bash
python -m model.algo.preflight
python -m model.scripts.daily_recommend --preflight
```

첫 번째 명령은 NumPy/psycopg가 아직 없는 환경에서도 표준 라이브러리만으로 누락을 보고한다. 종료 코드 0은 파일 존재·크기와 의존성 모듈 존재 확인만 통과한 뜻이며, 형식·모델 품질·실제 최대 메모리·DB 연결 성공을 보증하지 않는다. 누락은 종료 코드 2다. 일반 배치도 DB 주소를 읽기 전에 이 검사를 실행한다.

필수 파일:

- 커밋된 `model/catalog/chips.csv`, `menu_works.csv`, `menu_work_entries.csv`.
- 별도 공급해야 하는 `model/data/anilist/{media,media_recent,detail,links}.jsonl`.
- `model/data/mal_sample/anime.csv` 및 `interactions.csv` 또는 사용 가능한 `interactions.npz`. MAL 표본 위치는 기존 `MAL_SAMPLE_DIR` 환경변수로 지정할 수 있다.
- `model/data/anilist_users/entries.csv`는 선택 사항이다. 없으면 기존 MAL 전용 같이 본 기록과 최근 인기 0 대체 동작을 그대로 사용한다.

`model/data/`는 Git에서 제외되어 clone만으로 준비되지 않는다. 승인된 데이터 사본을 별도로 공급해야 하며, 합성 파일로 운영 입력을 대체하지 않는다. CSV가 NPZ보다 새롭거나 캐시가 없으면 기존 로더가 `interactions.npz`를 생성/갱신한다. DB dry-run과 로컬 파일 쓰기는 별개이므로 데이터 디렉터리 권한도 확인한다. 원본 데이터의 보존·삭제 조건은 기존 `algo/service.py` 주석을 따른다.

현재 3,887개 작품의 주요 상시 행렬만 약 519MiB이며 특징·사용 기록·행렬 계산의 임시 메모리는 별도다. API의 512MiB 메모리 한도 안에서 배치를 함께 돌리지 않는다. 원본 데이터 전체를 사용한 시간·최대 메모리는 아직 검증하지 않았다.

### 서버에서 돌리기: 통계 문맥 파일 (2026-10-30)

서버에는 원본 (`model/data/`의 AniList · MAL)을 두지 않는다. AniList 유저 표본은 허락 조건상 원본을 PC 한 대에만 두고 12/31에 삭제한다. 원본이 있는 PC에서 추천 문맥을 통계만 담은 파일 하나로 만들고, 서버는 그 파일로 계산한다.

```bash
python model/scripts/save_context.py --check    # 원본 PC: model/data/service_context.pkl.gz 생성 + 원본 문맥과 칸마다 비교
python -m model.scripts.daily_recommend --preflight --context <파일>                # 서버: 원본 없이 이 파일만 검사
python model/scripts/daily_recommend.py --expected-database <DB이름> --context <파일>  # 서버 계산 (--apply · --every 는 그대로)
```

- 파일에는 작품끼리 통계 (같이 본 횟수 · 유사도 · 작품 특징 · 날짜별 인기 합계)만 있다. 저장 전에 유저 단위 자료가 없는지 검사한다. 공개 레포에 올리지 않고 서버로 직접 옮긴다.
- pickle 형식이라 `save_context.py`로 만든 파일만 읽는다.
- 결과는 원본으로 계산한 것과 같다. 시험용 DB 18명의 추천 1,078개가 순서 · 점수 · 이유까지 일치했다.
- 측정 (같은 PC): 파일 약 372MB. 원본으로 켜면 준비 약 90초 · 최대 메모리 약 1.6GB (예약 약 2.3GB), 파일로 켜면 준비 약 5초 · 최대 메모리 약 0.9GB (예약 약 1.15GB).

### 기존 starter Q2 선택 호환

DB 선택 값과 태그 종류는 다시 쓰지 않는다. `algo/preferences.py`에서 모델 입력만 명시적으로 대응한다. 직접 일치 19개와 아래 별칭 11개로 starter 38개 중 30개의 모델 특징을 전달한다.

| 기존 kind/code | 모델 kind/code | 카탈로그의 AniList 이름 |
| --- | --- | --- |
| genre/science_fiction | genre/sci_fi | Sci-Fi |
| theme/growth | theme/coming_of_age | Coming of Age |
| theme/supernatural | genre/supernatural | Supernatural |
| theme/superpowers | theme/super_power | Super Power |
| theme/journey | theme/travel | Travel |
| theme/body_swap | theme/body_swapping | Body Swapping |
| theme/other_world | theme/isekai | Isekai |
| theme/music | genre/music | Music |
| theme/mecha | genre/mecha | Mecha |
| theme/dungeons | theme/dungeon | Dungeon |
| theme/time_travel | theme/time_manipulation | Time Manipulation |

근거는 기존 `backend/app/seeds/onboarding_catalog_starter.json`의 코드·표시명, 커밋된 `catalog/chips.csv`의 정규 이름, `scripts/make_chip_catalog.py`의 한국어 이름 대응이다. 명시적 대상이 카탈로그에서 빠지면 오류로 드러내며 의미가 비슷하다는 이유로 새 대상을 추측하지 않는다. 정규·별칭 태그를 둘 다 선택했어도 같은 모델 특징의 가중치를 두 번 더하지 않는다.

`friendship`, `family`, `heroes`, `mind_games`, `science`, `civilization`, `letters`, `bounty_hunters` 8개는 확실한 일대일 대응이 없어 모델 점수에 반영되지 않는다. 원본 `tag_id/kind/code/label`을 배치 메모리의 `preference_tags`와 `unmapped_chips`에 보존하고, 사용자 식별자 없는 코드별 집계 진단을 출력한다. 이 진단은 `--show 0`이어도 출력된다. 원본 DB 선택은 그대로 남아 이후 대응표를 보완할 수 있다. 이 8개까지 취향 반영이 완료되었다고 해석하지 않는다.

### 네트워크·실DB 없는 합성 검증

```bash
python -m model.scripts.daily_recommend --self-test
python -m unittest discover -s model/tests -p 'test_*.py'
```

`--self-test`는 320개 합성 작품으로 실제 문맥·같이 본 행렬·EASE·콘텐츠 순위 계산을 호출하고, 메모리 SQLite를 사용하는 SQL 어댑터에서 신규 저장·중복 건너뛰기·교체·실패 시 rollback을 확인한다. DB URL/환경파일을 읽지 않는다. `--apply`, DB 주소/이름, 반복·날짜 옵션과 함께 쓸 수 없다. PostgreSQL advisory lock의 동작이나 실제 모델 품질까지 검증하는 모드는 아니다.

별도 격리 PostgreSQL 통합 테스트는 `model.tests.fixtures.build_synthetic_world()`가 반환하는 `(ctx, config, catalog_ids, chip_features)`를 `daily_recommend.run_once`에 전달할 수 있다. 합성 작품 및 `menu_work` 외부 ID는 1~320, 시리즈는 1:1이다. 이 helper는 운영 배치에서 자동으로 선택되지 않는다.

### 실제 배치 실행 경계

원본 데이터와 별도 모델 실행환경을 준비한 뒤에만, 운영자가 지정한 DB 이름과 주소를 사용한다. 주소를 명령 인자에 넣기보다 별도 환경의 `DATABASE_URL`을 구성한다.

```bash
python -m model.scripts.daily_recommend --expected-database APPROVED_DATABASE --show 0
# 위 명령은 DB 읽기 전용 미리보기. 저장 승인을 받은 환경에서만 --apply 추가.
```

기본은 DB dry-run이며 `--apply`가 있어야 저장한다. `--replace`는 해당 날짜·버전의 순위를 원자적으로 교체하지만 진행 중인 피드 커서에 영향이 있으므로 운영 중 무조건 반복하지 않는다. `--every`는 양의 초, `--daily-at`은 유효한 24시간 시각이어야 한다. 새 배포나 스케줄러 등록은 이 로컬 테스트에 포함하지 않았다. 실제 원본 데이터의 `service.build()`와 실제 모델 품질은 입력 데이터 부재로 미검증이다.
