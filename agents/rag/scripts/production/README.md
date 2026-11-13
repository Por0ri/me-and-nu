# 대량 생산 · 서버 넣기 스크립트 (11/11~11/13)

정보사전 RAG 콘텐츠를 대량으로 만들어 Checking 서버에 넣을 때 쓴 스크립트 모음이다.
원래 임시 폴더에서 쓰던 것을 그대로 옮겼다. 모두 `agents/rag` 에서 실행한다.

## 순서

| 단계 | 스크립트 | 돈 |
|---|---|---|
| 1. 글감 만들기 | `build_like_manifests(_shard).py` 팬 추천 · `build_intro_manifests.py` 주연 소개 · `build_origin_manifests.py` · `build_origin_more_manifests.py` 원작 이야기 · `build_basic_more_manifests.py` 입문 가이드 그림 보강 | 안 듦 |
| 2. 글 만들기 | `run_part.py --root .cache/massNN --stamp <이름> --part k --queue --wait-flag .cache/massNN/manifests_done.flag` (일꾼마다 `ANI_MENU_DATABASE_PATH` 를 따로) | 듦 (글 하나 약 1센트) |
| 3. 표지 고치기 | `basic_cover_vision.py` · `basic_anilist_cover.py` · `basic_charcover_fix.py` · `intro_coverA_patch.py` → 서원 OK 뒤 `*.approved.json` 만 묶음에 반영 | 안 듦 (`basic_cover_vision.py` 만 AI 사용) |
| 4. 묶음 | `bundles_v3.py <출력 폴더> [글 번호 ...]` (글마다 `bundle.json` + 카드 PNG 하드 링크) | 안 듦 |
| 5. 서버용 작은 묶음 | `make_upload_batches_v3.py <묶음 폴더> <출력 폴더>` (표지 겹침 거르기, 작은 묶음 그림 35MB 이하) | 안 듦 |
| 6. 서버 가져오기 검사 | `check_bundles.py <묶음 폴더>` (backend 폴더에서 backend 가상환경 python 으로, DB 없이 읽기만) | 안 듦 |

- 현황 · 미리보기: `production_status.py` · `morning_pages.py` · `intro_preview.py` · `intro_content_view.py` · `cover_mockups.py`
- 복구 · 점검: `recover_l3.py` (디스크가 꽉 차 깨진 결과 파일 되살리기) · `resume_check.py` · `charcover_dup_check.py` · `held_basic_covers.py` · `make_fix_v2.py`
- `performer_names.py`: 가수 이름 바로잡기 (묶음 만들 때 씀)

## 곁 파일 (저장소에 안 올림)

스크립트는 자기 옆 폴더에서 결정 기록 · 목록 파일을 읽는다 (`basic_cover_choice.json`, `namu_song_rewrites.json`,
`themes_improved.txt`, `basic_skipped.txt`, `mass9_build.log`, `*.approved.json` 등).
데이터라서 공개 저장소에 올리지 않고, 만든 컴퓨터의 `agents/rag/.cache/production_side_files/` 에 보관한다.
다시 쓸 때는 필요한 파일을 이 폴더 옆에 복사한다.

## 주의

- 공개 저장소라 데이터 · 캐시 · 키 · 서버 주소는 올리지 않는다.
- 생산 중 일꾼 저장소(`.cache/content/agent-*.sqlite3`)가 글 하나에 약 15MB 씩 커진다. 디스크 공간을 먼저 확인한다.
