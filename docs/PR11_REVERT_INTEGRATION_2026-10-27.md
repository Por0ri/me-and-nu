# PR #11 리버트 및 별도 서비스 통합

기준일: 2026-10-27. 사용자가 PR #11 리버트, 원본 코드 보존, 별도 브랜치에서 통합을 계속하도록 승인했다.

## main 복원과 원본 보존

- 원본 PR: https://github.com/Por0ri/me-and-nu/pull/11
- 원본 커밋: `8a0dee093cd983b8f87fa3912228aceeef11a77c`
- 원본 보존 브랜치: `codex/pr-11-original`
- PR #11 병합 커밋: `2b24bedfa292562b2b19bbb175708e8c7beabf32`
- 리버트 커밋: `ce8cb73d3db9fd6230996dcc07e17dc3083f577b`
- 리버트 PR: https://github.com/Por0ri/me-and-nu/pull/12 (병합 완료)
- 리버트 반영 후 main: `ca949aced7a4fc580cd0a0ead75bb205a8aeadfd`

리버트 후 main의 Git tree는 `fae937a771ebfeefce52729b562f461c9a30ea78`로, PR #11 병합 직전 커밋 `9f1439919328541fd16000082dd2da7234e73939`의 tree와 일치한다. PR #11의 순변경 101개 파일은 모두 `agents/ani_me;nu/` 내부다. 과거 커밋 이력과 원본 보존 브랜치는 유지한다. 코드 리버트가 실행 중인 서비스나 이미 생성된 DB 데이터의 복원을 의미하지는 않는다.

## 통합 작업 기준

- 작업 브랜치: `codex/service-integration`
- 작업 공간: `C:\Users\USER\.codex\worktrees\service-integration\me_nu`
- 기존 서비스 작업 기준: `backend-labs@407039433efc0be1eb13d574f5ee7f69c5d9eb33`
- main 리버트 이력 병합: `904ca5f`
- 통합 브랜치에만 원본을 다시 적용한 커밋: `2908396`

`2908396`은 리버트 커밋을 다시 되돌려 원본 변경을 명시적으로 재적용했다. 이 시점의 `agents/ani_me;nu/` 전체 tree가 원본 `8a0dee0`와 같은 것을 확인했다. 이는 향후 원본 브랜치를 단순 재머지했을 때 취소된 변경이 복원되지 않는 문제를 피하기 위한 것이다.

기존 `C:\Users\USER\me_nu` 작업 공간의 미커밋 파일 37개를 새 작업 공간으로 복사하고 SHA-256 일치를 확인했다. 기존 작업 공간은 유지한다. 복사한 DB·API·문서 작업은 미커밋 상태이며, 이 문서와 에이전트 검수 수정의 커밋에 함께 포함하지 않는다. 기존 `.env`나 인증 정보는 새 작업 공간으로 복사하지 않았다.

## 통합 순서와 미결정 사항

1. 임시 integration 모드의 출처 검수 및 콘텐츠 검수 우회를 제거하고 실제 검수 경로의 회귀 테스트를 수행한다.
2. 에이전트 저장 결과와 서비스 `content`·카드·이미지 계약을 대조하고 발행 어댑터를 정한다. 에이전트 큐를 서비스 피드에 직접 연결하지 않는다.
3. 작품 ID의 기준과 AniList/MAL 매핑을 맞춘다. 새 온보딩 요구사항은 로컬 미커밋 문서 `docs/ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md`에 정리했다: Q1 작품 1~5개, Q2 해당 작품의 장르·소재 칩 합계 1개 이상, Q3 기존 여섯 소비 유형 중 하나.
4. 격리된 환경에서 로그인, 콘텐츠 발행, 피드·상세, 저장 동작을 검증한 뒤 새 main PR을 준비한다.

생년월일 수집 정책은 사용자가 통합 완료 후 결정하기로 했다. 복사된 `d2f6a9c4e731_remove_user_birth_date.py`를 정책 확정이나 배포 승인으로 해석하지 않는다. 온보딩 후속 마이그레이션이 해당 삭제 migration을 암묵적으로 실행하지 않도록 경로를 검토해야 한다. 이번 리버트·브랜치 분리에서 DB migration이나 테스트/Labs 서버 재배포는 실행하지 않았다.

에이전트는 Python `>=3.12,<3.13`, 서비스 백엔드는 Python 3.13을 사용한다. 실행 환경을 분리하고, 에이전트가 런타임에 생성하는 저장 테이블의 소유권과 마이그레이션 방식을 통합 전에 결정한다.

## 첫 통합 수정 및 검증 결과

통합 브랜치에서 reviewer의 기본 프로필을 `standard`로 복원하고 임시 `integration` 조기 승인 및 출처 검수 우회 분기를 제거했다. 실행 환경에 `integration` 값이 남아 있어도 정상 검수 경로를 거친다. 기존 `strict`·`exploratory` 임곗값은 유지한다. 실제 reviewer와 preflight 경로를 호출하는 회귀 테스트 35개를 추가해 미검증 사실, API 키 부재, fixture, 언어·안전 검사 실패의 게시 차단과 정상 검수 승인을 확인했다.

- Python 3.12.14 및 `uv sync --frozen --group dev`로 잠금 파일의 의존성을 설치했다.
- 관련 테스트: 156개 통과. 변경한 Python 파일의 Ruff 검사 통과.
- 전체 에이전트 테스트: 447개 중 444개 통과, 3개 실패. 전체 검증이 통과한 상태는 아니다.
- 실패 3개는 원본 `8a0dee0`의 독립 Git archive에서도 같은 원인으로 재현했다. 스냅샷 코드의 실제 import 경로를 확인했으며 이번 검수 수정의 회귀가 아니다.

남은 기존 테스트 실패:

1. `test_editorial_language_prompt.py::test_detail_writer_distinguishes_attributed_review_opinions_from_facts`: 상세 본문 프롬프트의 기대 문구 불일치.
2. `test_editorial_language_prompt.py::test_reviewer_gives_specific_repairs_and_calibrates_opinion_support`: 검수 프롬프트의 기대 문구 불일치.
3. `test_page_collector.py::test_image_candidates_ignore_sidebar_and_related_article_images`: OG 이미지를 포함하는 실제 결과와 테스트 기대 목록 불일치.

위 실패는 원래 의도한 프롬프트·이미지 수집 정책을 확인한 뒤 해결할 후속 과제다. 테스트 기대값만 바꿔 통과시키지 않았다. 이 검증은 외부 LLM과 실제 서비스 DB를 사용하지 않았으며, DB·API·화면 전체 통합 완료를 의미하지 않는다.

## 2026-10-28 온보딩 DB·API 통합

사용자의 후속 요청으로 [온보딩 V2 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)에 따른 DB·API·관련 문서를 통합 작업 공간에 구현했다. 작품 1~5개 → 작품의 장르·소재 합계 1개 이상 → 기존 Q1 소비 유형 1개를 받는다. 신규 카탈로그·선택 테이블 6개, 답변 버전 구분, 작품 검색과 후보 조회 API를 추가했고 기존 V1 제출·답변도 보존한다. 검토한 JSON 카탈로그를 멱등 적재하는 도구와 가상 fixture를 포함한다.

온보딩 도입 당시 target `e3a7b9c2d451`은 c0에서 분기하여 보류된 생년월일 삭제 d2를 실행하지 않았다. 당시 runner와 배포 Compose는 e3를 명시했고, 후속 [캐러셀 DB·API 변경](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)에서는 그 자식 **`f4b8c1d9a620`**을 서비스 target으로 명시한다. 실제 기존 DB에 적용하지 않았으며, 두 생년월일 상태의 보존은 별도 임시 PostgreSQL에서 검증했다. 아래 868개 통과 수치는 온보딩 도입 당시 기록이다.

백엔드 전체 검증은 **868 passed, 2 skipped, 경고 1개**다. PostgreSQL migration·동시 완료·rollback·카탈로그 적재 검증 8개가 포함되며, skip 2개는 설정하지 않은 실제 Redis 통합 테스트다. ERD 생성 결과 일치와 `git diff --check`도 통과했다. 위의 에이전트 테스트 3개 실패는 별개이며 이번에 수정하거나 재실행하지 않았다.

DB·API·문서 변경은 미커밋 상태로 통합 작업 공간에 보관한다. main 병합·푸시·배포는 하지 않았다.

## 2026-10-28 실작품 카탈로그·프론트 후속 연결

[실작품 30개·태그 38개](ONBOARDING_CATALOG_SOURCES_2026-10-28.md)를 준비하고 새 Pivot 온보딩 Q1/Q2/Q3를 실제 API에 연결했다. 생년월일은 새 화면에서 입력·검사·전송을 보류하고 닉네임만 받는다. 프론트 테스트 259개·타입 검사·프로덕션 빌드가 통과했으며 실제 모바일/데스크톱 Chrome과 격리 PostgreSQL에서 가입·세션 전환·검색 지연/오류 복구를 검증했다. [연결 결과와 한계](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)를 따른다. 추천·캐러셀 어댑터, 실제 공급자 로그인, 원격 적용은 후속이며 main 병합·배포는 계속 보류한다.
