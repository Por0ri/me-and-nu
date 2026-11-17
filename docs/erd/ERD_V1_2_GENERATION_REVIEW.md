# ERD V1.2 수정안: Celery 단건 콘텐츠 생성 큐

**[서비스 ERD V1.2](https://www.erdcloud.com/d/hnxGFGtxCbitLF2fq)는 이번 작업으로 수정할 곳이 없다.** Celery 작업과 실행 이력은 별도 PostgreSQL DB인 `menu_content_jobs`에 저장한다. 아래는 `backend-labs`의 v3와 작업 중인 v4 마이그레이션을 기준으로 한 **작업 DB 도면**의 표기안이다.

## 작업 DB에 표시할 관계

| 테이블 | 도면에 표시할 내용 |
| --- | --- |
| `content_generation_job` | PK `job_id`. 요청 한 건의 출처·URL·작품, 작업 종류(`job_kind`), 실행 상태(`execution_status`), 생성 결과(`outcome`), 산출물 경로를 기록한다. `idempotency_key`는 모든 종류에서 유일하다. |
| `content_generation_attempt` | PK `attempt_id`. FK `job_id` → `content_generation_job.job_id`. 접수 직후 시도는 0건이며, 실행하거나 복구할 때마다 시도가 생긴다. `(job_id, attempt_no)`는 유일하다. 각 시도의 Celery 실행 ID는 `celery_task_id`다. |
| `menu_worker_schema` | PK `version`. 설치 시각과 스키마 버전을 기록하는 독립 메타데이터 테이블이다. 물리 도면에만 표시하면 된다. |

작업에서 시도를 가리키는 선택적 FK도 두 개 있다. `current_attempt_id`는 가장 최근에 점유한 시도를, `generation_attempt_id`는 산출물을 생성하는 시도를 가리킨다. 복구 시에는 새 시도가 기존 생성 시도의 파일을 검증하므로 두 값이 다를 수 있다. 두 FK의 대상은 모두 `content_generation_attempt.attempt_id`다.

`source_id`와 `menu_work_id`는 각각 Agent의 등록 출처와 작품 카탈로그에서 확인하는 값이다. 서비스 DB로 FK를 연결하지 않는다. Worker·Beat·Flower는 실행 구성 요소이므로 테이블로 표시하지 않는다.

## v3 작업 도면에서 v4로 바꿀 항목

**테이블·컬럼·FK 추가는 없다.** 기존 도면의 `content_generation_job` 제약과 스키마 버전 표기만 갱신한다.

| 항목 | v4 표기 |
| --- | --- |
| 애니 콘텐츠 작업과 입력 | `anime`는 등록 출처·작품과 큐 전달을 검증한다. v4에서 추가하는 `anime_generate`는 해당 기사의 콘텐츠를 수동으로 생성한다. 두 작업 모두 등록 출처와 기존 작품 ID를 입력받는다. |
| 기사 중복 기준 | 같은 `canonical_url`에 검증 작업과 생성 작업을 각각 최대 한 건 기록할 수 있다. 검증한 기사를 생성할 때는 별도 `idempotency_key`를 사용한다. |
| 생성 결과 | `anime`의 실행 성공 결과는 `routing_verified`다. `anime_generate`의 실행 성공 결과는 `ready_for_staging`, `held`, `skipped`, `rejected` 중 하나다. 예를 들어 `succeeded` + `held`는 작업은 끝났지만 콘텐츠는 검수 보류라는 뜻이다. |
| 스키마 버전 | `menu_worker_schema.version`은 3에서 4로 바뀐다. |

근거: [v1 테이블·FK](../../worker/menu_worker/migrations/001_content_jobs.sql) · [v2 작업 종류](../../worker/menu_worker/migrations/002_job_kind.sql) · [v3 출처·작품 입력](../../worker/menu_worker/migrations/003_anime_work_input.sql) · [v4 수동 생성 제약·인덱스](../../worker/menu_worker/migrations/004_anime_generation.sql)
