# PR #77 Agent–Celery 무과금 통합 검증

검증일: 2026-11-16 KST. 대상은 원격 Checking `80dc426a0659ca7acb18c31b33fb345022040e41`이며 PR #77 `f45b858`을 포함한다. 종료 시 원격도 같은 SHA임을 확인했다.

이 문서는 수정 전 진단 기록이다. 이후 승인된 표지 이력 연결 작업과 검증·배포 결과는 [CELERY_COVER_HISTORY_2026-11-16.md](CELERY_COVER_HISTORY_2026-11-16.md)에 별도로 기록한다.

## 결론

**서버 배포를 보류했다.** 새 Agent 이미지의 실행·큐 계약과 추천 회귀는 통과했고 이전 코드 대비 새로 실패한 테스트는 없었다. 하지만 PR #77의 표지 이미지 재사용 방지에 필요한 이전 콘텐츠 이력이 Celery 작업 사이에 전달되지 않는 것을 무과금으로 재현했다. 사용자의 “문제없으면 배포” 조건을 충족했다고 보고하지 않는다.

이번에 실제 LLM 호출·추가 유료 생성·서비스 DB 쓰기·공개 콘텐츠 수정·추천 재계산은 하지 않았다. 서버 소스/활성 Compose/이미지/컨테이너를 교체하지 않았고, 기존 `d78de66` 배포를 유지했다. 서버의 유료 Worker와 Controller는 계속 종료 상태다. 새 이미지는 로컬 검증용으로만 빌드했다.

## 확인한 통합 공백

- `worker/menu_worker/real_generation.py`의 `_environment()`는 생성 시도별 `agent.sqlite3`를 사용하고 `ANI_MENU_DUPLICATE_BASELINE_PATH`를 빈 값으로 고정한다.
- 새 `AgentRepository.prior_cover_image_fingerprints()`는 현재 Agent DB의 `content_drafts`, `feed_queue`, `rejected_archive`만 조회한다. 기존 읽기 전용 duplicate baseline 로더는 호출하지 않는다.
- `generate_copy_deck()`는 위 조회 결과를 `package.prior_image_fingerprints`에 대입한다.
- 따라서 같은 Agent DB를 재사용하면 이전 표지가 차단되지만, 현재 Celery처럼 작업마다 DB를 분리하면 앞 작업의 표지를 알 수 없다. URL 중복 방지와 별개의 **서로 다른 기사의 표지 이미지 재사용 문제**다.

진단 테스트 3건은 다음을 재현했다. 2·3번의 테스트 통과는 결함 재현 성공이며 기능 정상이라는 뜻이 아니다.

1. 같은 DB에서는 이전 표지의 visual key가 조회되고 `IMAGE_USED_BEFORE`가 발생한다.
2. 별도 attempt DB에서는 같은 표지가 있어도 이력이 빈 목록이고 위 차단이 발생하지 않는다.
3. 유효한 읽기 전용 baseline DB를 별도로 지정해도 새 cover 조회 메서드는 이를 읽지 않는다. baseline 파일은 변경되지 않는다.

Agent 자체의 cover 조회를 Celery에 연결하는 변경이 필요하다. 단순히 키나 환경변수를 켜는 배포로 해결되지 않는다. 기존 공개 4건의 표지·본문은 그대로 보존했다.

## 검증 결과

| 범위 | 결과 |
|---|---|
| 최신 Worker/생성 이미지 빌드 | 성공, `me-nu-worker:checking-80dc426`, `me-nu-anime-generation:checking-80dc426` |
| 실제 생성 이미지 오프라인 검사 | CLI 작품 ID 거절, 레지스트리·카탈로그 일치, 한글 폰트·PNG, 오프라인 LangGraph 통과 |
| Worker 단위 | 112건 통과. 이 실행의 PostgreSQL 61건은 의도적으로 건너뛰고 별도 실행 |
| Worker PostgreSQL | 61건 모두 통과. v6→v7 이력 보존, 동시 중복 접수, 독립 검수/내보내기, 대체 Agent subprocess 계약 포함 |
| 실제 Redis/Celery 라우팅 | 같은 원피스 기사 5회 접수→작업 1건, `routing_verified`; 다른 큐 전달은 claim하지 않음; 중복 배달 무시 |
| 실제 Redis/Celery 재시도 | 6개 시나리오·12작업 통과. 최대 3회 후 다음 작업 진행. 실행 여부 불명확한 경우 1회 후 `needs_attention` |
| 콘텐츠·추천 피드·추천 배치 | 222건 및 하위 검사 4건 통과, 격리 PostgreSQL 사용 |
| 최신 Agent 전체 | 599건 중 564건 통과·35건 실패 |
| 이전 d78 Agent 동일 환경 비교 | 590건 중 553건 통과·37건 실패 |
| 실패 목록 차이 | 신규 실패 0건. 기존 Mastodon 관련 실패 2건 해결 |
| 표지 이력 진단 | 3건 통과: 작업 간 전달 누락과 baseline 미사용을 재현 |

새 Mastodon 필터의 추가 9개 사례도 통과했다. 기존 실패 목록에서 해결된 항목은 `test_mastodon_parser_accepts_engaged_english_discussion_and_skips_bots`, `test_work_specific_public_post_does_not_need_trend_tags_or_engagement`다.

Agent 전체 테스트가 모두 통과했다고 주장하지 않는다. 남은 35건은 이전 코드에서도 같은 환경에서 실패했다. mock 메서드/함수 인자 불일치, 고정 카탈로그 ID, DNS mocking 누락, 프롬프트/검수 정책 기대값, 날짜·정렬 기대, 깨진 기존 이미지 설명 문자열 등이 포함된다. 네트워크 차단 환경에 맞지 않는 테스트 실패도 있어 35건 전체를 서비스 런타임 오류로 해석하지 않는다. 이 테스트 정비와 표지 이력 연결은 별도 문제다.

## 격리와 서비스 보존

Agent 테스트는 실제 키 없이 `--network none`으로 실행했다. 테스트 중 생성되는 mock 키는 실제 자격 증명이 아니며 외부 통신도 차단했다. Worker 통합은 새 internal 네트워크의 임시 PostgreSQL·Redis 3개만 사용했다. 추천 검증은 별도 pgvector 테스트 DB를 사용했다. 운영 env·세션 Redis·서비스 DB를 테스트에 연결하지 않았다.

서버에서는 기존 운영 검증 하니스로 읽기 전용 확인만 수행했다.

- 공개 콘텐츠 **4044·4045·4046·4047**, 각각 1건 및 `active / confirmed / published` 유지.
- 각 번들의 재적재 dry-run은 `unchanged`; 원본 검수 근거·본문·출처·PNG 12장 바이트 일치.
- 추천 **63 rounds / 6,300 items** 유지.
- API와 기존 9개 컨테이너의 ID·시작 시각·이미지 및 API 환경 해시 유지.
- 기존 전용 작업 DB는 v7·작업 4건 유지. 유료 Worker/Controller를 시작하지 않음.
- API `/health`, `/health/db`, `/health/redis` 정상.

검증 종료 후 이번에 만든 `menu-pr77-integration-20261116`, `menu-pr77-recommendation-20261116` 프로젝트만 종료했다. 기존 로컬 컨테이너 12개는 계속 실행 중이며 로그와 빌드 이미지는 보존했다.

## 다음 보강 범위

1. 검증·봉인된 과거 결과에서 표지 visual key를 읽어 실행별 불변 이력 snapshot을 만든다. 서비스 DB/추천 로직을 수정하지 않는다.
2. Agent의 cover 조회가 이 snapshot을 읽도록 명시적인 입력 계약을 연결한다. 입력 누락·변조를 조용히 빈 이력으로 처리하지 않도록 검증한다.
3. 같은 표지/다른 기사, 다른 표지, 자기 요청 재검토, baseline 변조, 원본 이력 불변을 무과금 회귀로 남긴다.
4. 현재 단일 Worker·concurrency=1의 순차 처리 범위를 명시한다. 여러 Worker 동시 생성은 snapshot만으로 중복 선택을 원자적으로 막을 수 없어 별도 제어가 필요하다.

이 보강을 수행하거나 실패 테스트의 기대값을 임의로 바꾸지는 않았다. 이번 요청은 검증 후 조건부 배포이며 새 이력 전달 계약은 별도 구현 검토가 필요하다.

## 검증 환경에서 바로잡은 사항

- 독립 Agent 테스트 이미지가 Compose build label을 상속해 빈 테스트 프로젝트 검사에 걸렸다. 기존 프로젝트를 지우지 않고 전용 integration 프로젝트 이름으로 구분했다.
- 추천 테스트 첫 실행은 `backend/scripts` 마운트와 DB 드라이버 설정이 잘못돼 collection에서 중단됐다. 실제 설치된 `psycopg` 드라이버와 `/workspace/backend`, `/workspace/model` 구조로 수정한 뒤 222건 모두 통과했다. 서비스 코드 수정은 없었다.

## 근거 파일

로컬 실행 기록: `out/pr77-offline-2026-11-16/`.

- `generation-image-smoke.log`, `worker-unit.log`, `worker-postgres.log`
- `queue-routing.log`, `queue-retry.log`, `recommendation-regression-final.log`
- `agent-full-regression.log`, `agent-baseline-regression.log`, `agent-regression-comparison.json`
- `cover-history-diagnostic.log`, `harness/test_cover_history_isolation_audit.py`
- `server-baseline-audit.json`, `server-final-audit.json`, `published-content-final.log`

`harness/preflight_runtime.py`, `prepare_source.py`, `build_offline.sh`와 배포 계획은 준비만 했고 서버에서 실행하지 않았다. 이 파일의 존재는 배포 완료를 뜻하지 않는다. 저장소 실행 코드와 원격 브랜치는 변경하지 않았다.
