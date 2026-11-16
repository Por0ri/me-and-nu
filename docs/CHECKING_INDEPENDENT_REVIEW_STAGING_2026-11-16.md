# Checking 독립 검수 결과 → 적재기 연결 검증

## 범위와 결과

- 기준: `origin/Checking`의 `fd69a63c229704ece3c0e54069fcefd9b7d05e7d`.
- 작업 브랜치: `codex/independent-review-staging`. 이 검증에서 커밋·푸시·머지·서버 배포는 하지 않았다.
- 기존 원피스 공식 굿즈 기사 `https://one-piece.com/news/83393/index.html`의 생성물과 이미 완료된 독립 LLM 검수 결과를 재사용했다.
- 추가 LLM 호출 0회, 서비스 DB 쓰기 0회. 자동 수집·유료 생성 Worker는 시작하지 않았다. 기존 추천·Agent·백엔드 production 코드는 변경하지 않았다.
- 실제 번들로 dry-run 무쓰기, 중복 적재 1건 유지, 승인 전 피드 비노출, 격리 DB의 테스트 운영자 승인 후 피드 1건 노출을 확인했다.

## 원본과 승인 근거 분리

| 항목 | 값 |
|---|---|
| 원본 job | `039505e4-4e31-456f-b0cf-399acb9c5364` |
| 원본 generation attempt | `1909c466-246c-422b-bf50-a53eb1d0c163` |
| request | `crawl-53ef7bddae4103` |
| 원본 결과 SHA-256 | `0e2b88fe6ee5f42585287997f02f536f2b9f1ac4ee851fc73c4207ec1f487c6b` |
| 독립 검수 ID | `e707855c-8985-5de2-b52c-4b319930883f` |
| 적재기 bundle digest | `a6cbd2d8c34de6cb1f77d6d64258cfc975ab483759ba61e4d64090150d02862f` |
| 이미지 | 기존 PNG 4장, 바이트 일치 |

원본 작업은 `succeeded / rejected / rejected_archived` 그대로다. 작업·시도·result.json·manifest.json·Agent SQLite·이미지를 수정하지 않았다. 원본 파일 12개와 독립 검수 출력 파일 8개의 전후 해시를 비교했다.

`register-staging-review`는 운영자가 제공한 기존 검수 JSON을 검증해 별도 증거로 등록한다. 외부 공급자에게 재검증하거나 새로운 검수를 요청하는 명령이 아니다. 다음 조건을 검사한다.

- 봉인된 원본과 작업 DB의 job/attempt/결과 해시가 일치한다.
- 검수 package는 `status` 필드를 제외하고 원본과 완전히 같다. 본문·날짜·이미지·출처·작품 ID를 바꿔 승인할 수 없다.
- `review_passed`, LLM 검수, `approved_for_feed`, 무 blocker, 모든 카드의 언어·시각 검수와 상세 본문 검수, 양수 토큰 사용 기록이 있다.
- 원본 PNG의 순서·규격·디코딩·해시를 확인한다. 중복 JSON 필드, 심볼릭 링크, 불완전하거나 변조된 근거는 거부한다.

검수 JSON은 정규화된 JSON의 SHA-256으로 식별한다. 공백 차이는 동일하게 취급하며, 모든 필드 값은 해시에 포함한다. 봉인 파일은 `reviews/{job_id}/{review_id}.json`에 덮어쓰기 없이 추가한다. UUID 표기가 달라도 동일한 등록으로 재처리한다.

## 작업 DB v7

서비스 DB migration이 아니라 전용 Celery 작업 DB migration이다.

- `content_generation_review`에 원본 job/attempt/result 해시와 검수·봉인 해시를 추가 기록한다.
- `content_staging_selection.review_id`를 추가한다. v6의 기존 선택은 `NULL`로 그대로 보존한다.
- 같은 기사에서 원래 통과한 결과와 재검수 통과 결과가 경합해도 선택은 한 건이다. 선택된 작업이나 검수 ID는 다른 것으로 교체할 수 없다.
- 같은 검수 재등록과 같은 선택의 재내보내기는 허용한다. 내보낸 package·이미지가 동일하므로 적재기 digest도 변하지 않는다.

v7은 이번에 만든 격리 DB에서만 적용했다. 기존 로컬 작업 DB와 서버 작업 DB는 변경하지 않았다. 실제 Worker 이미지 교체 전 전용 작업 DB의 명시적인 `init-db` 단계가 필요하다. 운영 전환 절차는 [Worker README](../worker/README.md)의 독립 검수 등록 항목을 따른다.

## 격리 검증 방법

`out/reviewed-staging-2026-11-16` 아래에 실행 자료와 결과를 보관했다.

- 전용 Compose 프로젝트 `menu-reviewed-staging-20261116`, 외부 통신이 차단된 내부 네트워크, 호스트 포트 없음.
- PostgreSQL 데이터는 임시 tmpfs에만 저장했다. 서비스 DB·Redis·키·환경 파일을 마운트하지 않았다.
- 원본 산출물과 기존 검수 출력은 읽기 전용 마운트했다. 원본 공개 작업 스냅샷은 신규 작업 DB에 복원했고, 비공개 claim token만 테스트용 새 값으로 채웠다. 공개 job/attempt 필드는 모두 원본과 대조했다.
- 콘텐츠 검증은 `carousel_test`의 매번 새 임의 스키마에서 실행했다. 운영자 승인은 정확한 bundle digest에 묶인 **테스트용 승인 기록**을 기존 적재기에 전달했다.
- 실제 인증 세션과 피드·상세·이미지 API를 사용했다. 서비스 배포나 실제 운영자 공개 승인을 대신하지 않는다.

| 확인 | 결과 |
|---|---|
| 검수 등록 2회 | review 1건 |
| 내보내기 2회 | 기사 선택 1건, 동일 bundle JSON |
| dry-run | `created=1`은 예정값, Content/Draft/AgentRun 저장 0건 |
| 최초 적재 | Content/Draft/AgentRun 각 1건, 이미지 4건 |
| 동일 번들 두 번째 적재 | `created=0`, `unchanged=1` |
| 승인 전 | `pending_review / needs_review / checking`, 피드 0건, 상세·이미지 404 |
| 테스트 운영자 승인 후 | `active / confirmed / published`, 피드 1건, 상세·이미지 200 |
| 승인 후 재적재 | 같은 콘텐츠 1건 유지, 공개 시각 유지 |
| 추천 테이블 | 원피스 적재 검증 전후 RecommendationRound/Item 0건 |

LLM 검수 승인은 운영자의 공개 승인이 아니다. `reviewEvidence`에는 원본 반려와 독립 검수 증거를 함께 보존하지만 `operatorReviewCreated=false`이며, 기존 적재기의 검수 대기 규칙은 그대로다.

## 회귀 결과

- 기존 Worker 단위 테스트: 95건 통과. 이 실행의 PostgreSQL 테스트 61건은 명시적으로 skip하고 아래에서 별도 실행했다.
- 신규 독립 검수 단위 테스트: 17건 통과.
- Worker PostgreSQL: 61건 통과. v6→v7 이력 보존, 동시 검수 등록/선택, 기존 ready 결과와의 경합 포함.
- 백엔드 콘텐츠·추천 피드·설정·배치 적재 회귀: 최종 207건 통과(206건 최초 통과, 검증 설정 보완 후 남은 1건 통과).
- 새 회귀의 기본 합성 번들 경로: 네트워크 없이 1건 추가 통과.
- 추천 PostgreSQL 통합 3건 + 모델 추천 배치 14건: 17건 및 하위 검사 4건 통과.
- `git diff --check` 통과. 백엔드는 새 테스트 파일만 추가했다.

결과 파일:

- `export-verification.json`: 원본 이력·파일 보존, 등록·내보내기 중복 처리
- `import-verification.json`: dry-run, 적재·승인 전후 피드 수, PNG 비교
- `worker-postgres-final.log`, `review-unit-final.log`
- `backend-regression.log`, `backend-config-final.log`, `synthetic-ci.log`
- `recommendation-final.log`
- `runtime-safety.json`: Beat 자동 수집 경로 미설정, generation/discovery runtime 없음, 기존 12개 컨테이너 실행 유지

검증 종료 후 이번 프로젝트의 임시 DB 컨테이너 2개와 내부 네트워크를 제거했다. 결과 번들·봉인된 검수 증거·로그는 보존했다.

## 검증 중 바로잡은 사항

작은 검증 환경 오류는 수정하고 재실행했다. 서비스 데이터나 추천/Agent 구현은 수정하지 않았다.

1. 공개 작업 스냅샷 복원에서 DB 자동 계산 컬럼 `candidate_url`을 직접 INSERT하지 않도록 수정했다. 공개 스냅샷 비교는 내부 claim token을 제외한 `public_job` 결과로 맞췄다.
2. 신규 v6 fixture가 미커밋 FK 작업 직후 DDL을 실행하던 부분을 트랜잭션으로 분리했다. 실제 마이그레이션 SQL 변경 없이 61건 재통과했다.
3. 백엔드 검증 컨테이너의 `alembic.ini` 마운트와 필수 테스트 `DATABASE_URL`을 명시했다. 누락 설정에 영향받은 검사만 재실행했다.
4. 추천 검증 이미지에 NumPy/OpenPyXL이 없어 테스트 전용 파생 이미지에 설치했다(`numpy 2.5.5`, `openpyxl 3.1.5`). 서비스 이미지는 변경하지 않았다.
5. 코드 리뷰에서 같은 UUID의 다른 표기가 서로 다른 검수 ID를 만들 가능성을 수정하고 회귀 테스트를 추가했다.
