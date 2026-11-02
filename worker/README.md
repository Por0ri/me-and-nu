# Celery Worker · Beat · Flower 로컬 실행 기반

Celery 작업 전달, 주기 실행, 재시도와 모니터링을 확인하는 독립 실행 환경이다. Python 3.12, Celery 5.6.3, Flower 2.2.0을 사용하며 Compose 프로젝트 이름은 `me-nu-worker`다. 단건 콘텐츠 큐는 가짜 Agent로 접수·상태·저장·복구를 구현했다. 실제 애니 Agent·LLM·서비스 적재 연결은 후속 범위다.

| 서비스 | 역할 |
| --- | --- |
| `redis-tasks` | 작업 메시지와 작업 결과 저장. 결과 TTL은 1시간 |
| `worker` | 점검용 작업 실행 |
| `beat` | 기본 30초 간격으로 heartbeat 작업 전송 |
| `flower` | Worker·작업 상태 조회. 로컬 접속·인증·읽기 전용 설정 사용 |
| `jobs-db` | 전용 PostgreSQL 17. 콘텐츠 작업·실행 이력 저장, 호스트 포트 없음 |
| `job-init` | 작업 전용 DB를 v3로 초기화하거나 기존 v1/v2 데이터에 순차 마이그레이션 적용 |
| `job-worker` | `content-generation` 큐의 가짜 Agent 실행, 동시 실행 1개 |
| `job-dispatcher` | fake 작업 전달 및 중단된 fake 작업 정리 |
| `anime-redis`, `anime-worker`, `anime-dispatcher` | 선택형 `anime` 프로필. 별도 Redis와 산출물 볼륨에서 애니 큐 전달을 점검 |

이 Compose는 콘텐츠 작업용 PostgreSQL을 별도로 실행한다. 서비스 API·서비스 PostgreSQL·실제 콘텐츠 Agent·추천 모델은 실행하지 않는다. 기존 로그인 세션 Redis와도 분리되어 있다. Checking의 추천 컨테이너와 매일 추천 계산 일정은 기존 구성을 따른다. 작업 이미지에는 기존 backend/model/agents 소스나 서비스 환경 파일이 포함되지 않는다.

## 시작

Docker Desktop의 Linux 컨테이너와 Docker Compose가 필요하다. 명령은 저장소 루트에서 실행한다. `.env` 초기화에 사용하는 호스트 Python과 실제 Worker를 실행하는 컨테이너 Python은 별개다.

기존 환경은 아래 시작 명령 전에 이전 버전의 콘텐츠 소비자를 종료한다. v1에서는 `docker compose --env-file worker/.env -f compose.worker.yaml stop job-dispatcher job-worker`를 사용한다. v2에서 anime 프로필도 사용했다면 `--profile anime stop job-dispatcher anime-dispatcher job-worker anime-worker`로 네 서비스를 종료한다. PostgreSQL과 산출물 볼륨은 유지한다. 빌드에는 Compose 설정의 추가 빌드 컨텍스트가 필요하다.

```powershell
python worker/setup_local.py
docker compose --env-file worker/.env -f compose.worker.yaml up -d --build --wait
docker compose --env-file worker/.env -f compose.worker.yaml ps
```

초기화 명령은 Flower와 전용 DB 비밀번호를 `worker/.env`에 저장한다. 기존 파일에는 누락된 `CONTENT_JOB_DB_PASSWORD`만 추가하며 기존 Flower 설정은 보존한다. 비밀번호는 출력하지 않는다. 파일은 Git에서 제외된다. DB 비밀번호는 최초 볼륨 초기화 때 적용되므로 볼륨을 만든 뒤 임의로 바꾸지 않는다. Flower 접속 정보는 본인의 로컬 파일에서 확인한다.

[Flower](http://127.0.0.1:5555)는 기본적으로 `127.0.0.1:5555`에만 열린다. 로그인에는 `FLOWER_BASIC_AUTH`의 `사용자:비밀번호`를 사용한다. 포트가 이미 사용 중이면 `worker/.env`의 `FLOWER_PORT`를 바꾸고 위 기동 명령을 다시 실행한다. Redis는 호스트 포트를 열지 않는다.

로그인이 실패하면 `FLOWER_BASIC_AUTH=사용자:비밀번호`에서 첫 번째 `:` 앞부분을 사용자 이름에, 뒷부분을 비밀번호에 입력했는지 확인한다. 설정 줄 전체를 입력하지 않는다. `worker/.env`를 편집한 뒤에는 아래 명령으로 Flower 컨테이너를 다시 생성해야 적용된다. `restart`만으로는 변경된 환경변수를 읽지 않는다. 적용 후에도 이전 로그인 정보가 계속 사용되면 브라우저의 새 시크릿 창에서 접속한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml up -d --no-deps --force-recreate --wait flower
```

Beat 간격은 `worker/.env`의 `CELERY_BEAT_INTERVAL_SECONDS`로 설정하며 기본 30초, 허용 범위는 5~3600초다. Beat 건강 상태는 Worker가 실제 heartbeat를 처리했는지 확인한다. 간격을 늘리면 첫 정상 상태와 점검 완료까지 그만큼 더 기다릴 수 있다.

## 동작 확인

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml exec flower python -m menu_worker.checks smoke
```

점검은 실제 Redis와 Worker를 통해 수동 작업 완료, 정해진 횟수의 재시도, 새 Beat heartbeat 수신, Flower 인증과 성공 이벤트를 확인한다. 존재하지 않는 Worker 이름에 대한 제어 요청이 읽기 전용 설정으로 거부되는지도 검사한다. Flower 자격 증명은 `flower` 컨테이너에만 전달하므로 통합 점검도 해당 컨테이너에서 실행한다. LLM 호출이나 서비스 DB 쓰기는 수행하지 않는다.

Flower의 작업 `SUCCESS`는 Celery 함수 실행 결과다. 후속 콘텐츠 작업에서 필요한 검수 보류·발행 승인·DB 적재 완료를 대신하는 업무 상태가 아니다.

## Worker를 멈춘 동안 접수한 작업 확인

Worker를 멈춰도 Redis·Beat·Flower는 계속 실행된다. 아래 순서로 작업을 접수하고 Worker를 다시 시작해 대기 메시지가 처리되는지 확인한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml stop worker
docker compose --env-file worker/.env -f compose.worker.yaml run --rm --no-deps worker python -m menu_worker.checks submit --label restart
docker compose --env-file worker/.env -f compose.worker.yaml start worker
docker compose --env-file worker/.env -f compose.worker.yaml run --rm --no-deps worker python -m menu_worker.checks result --task-id <앞에서_반환된_task_id>
```

이 확인은 정지 중 접수한 메시지의 처리를 검사한다. 실행 도중 종료된 유료 Agent 작업의 안전한 재개나 콘텐츠 중복 방지를 검증하는 절차는 아니다. 해당 기능은 후보별 영속 작업 기록과 출력 파일을 연결할 때 다룬다.

## 로그·테스트·종료

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml logs --tail 100 worker beat flower
docker compose --env-file worker/.env -f compose.worker.yaml run --rm --no-deps worker python -m unittest discover -s tests -v
docker compose --env-file worker/.env -f compose.worker.yaml down
```

`down`은 컨테이너와 네트워크를 종료하고 영속 볼륨은 보존한다. 이후 같은 기동 명령으로 다시 시작할 수 있다. Beat는 스케줄 볼륨의 프로세스 잠금을 사용해 두 번째 인스턴스 실행을 거부한다. 프로세스 종료 시 잠금은 자동 해제된다.

Redis는 작업 전용 볼륨에 AOF를 저장하며 `appendfsync everysec`을 사용한다. 비정상 호스트 종료 시 최근 약 1초의 기록이 유실될 수 있으므로, 이 설정만으로 콘텐츠 작업의 무손실 실행을 보장하지 않는다. 결과는 1시간 뒤 만료되고, Flower는 최근 2,000개 작업 상태를 별도 볼륨에 보존한다. `tasks` 네트워크는 내부 전용이며, Flower만 localhost 포트 연결을 위한 `monitor` 네트워크에도 참여한다.

## 구현 범위

현재 단계는 로컬 실행 기반과 가짜 Agent 단건 큐다. 서버 배포·도메인 연결·실제 콘텐츠 정기 생성은 포함하지 않는다. 실제 Agent를 연결할 때는 다음 항목이 추가로 필요하다.

- 실제 출처 등록·입력 검증과 기존 콘텐츠 중복 검사 연결.
- 기존 애니 Agent의 Python 3.12 실행 환경·한글 폰트·영속 출력 연결.
- 생성 결과와 원본 PNG 보존, 기존 검수·bundle 검증·적재 계약 유지.
- 유료 생성 재시도와 완료 결과 재적재의 분리, 후보·시도·비용 제한.
- 실제 출처 discovery와 업무별 Beat 일정.

기존 `agent_run.queue_task_id`의 캐러셀 적재 식별자와 새 Celery 실행 ID는 별도로 관리해야 한다. 추천 계산을 Celery로 이전하는 작업은 이 실행 기반에 포함하지 않는다.

각 Worker는 동시 실행 1개다. 점검 작업의 soft/hard 제한은 15/20초, 가짜 콘텐츠 작업은 45/50초, Redis visibility timeout은 90초다. 실제 장시간 Agent를 연결할 때는 실행 제한·재전달·중단 복구 정책을 함께 바꿔야 한다. 가짜 Agent의 성공 결과를 유료 생성 복구 검증으로 간주하지 않는다.

## 가짜 Agent 단건 콘텐츠 큐

먼저 위 시작 명령으로 환경을 기동한다. `job-init`이 종료 코드 0으로 끝나면 정상이다. 다음 한 줄은 실제 Redis·Worker를 통해 정상·보류·생략·실패와 중복 요청·중복 메시지를 확인한다. 실제 LLM 호출은 0회이며 테스트 작업 기록은 조회를 위해 보존한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-dispatcher python -m menu_worker.jobs smoke
```

직접 한 건을 접수하고 조회하려면 다음 명령을 사용한다. 같은 `demo-1` 입력으로 다시 실행하면 동일한 작업을 반환한다. 새 작업은 요청 키와 URL의 `demo-1`을 함께 변경한다.

```powershell
$job = docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-dispatcher python -m menu_worker.jobs submit --idempotency-key demo-1 --source fixture --url https://example.invalid/success/demo-1 --content-type news | ConvertFrom-Json
$job.job_id
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-dispatcher python -m menu_worker.jobs show --job-id $job.job_id
```

입력은 `idempotency_key`, `source_id`, `source_url`, `content_type` 네 개로 고정한다. `fixture` 출처와 `https://example.invalid/시나리오/식별자` URL만 허용한다. 이 주소는 네트워크로 요청하지 않는다. 활성 콘텐츠 유형 10개를 허용하며 서버 경로·모델·자격 증명은 입력할 수 없다.

| URL 시나리오 | `execution_status` | `outcome` |
| --- | --- | --- |
| `/success/demo-1` | `succeeded` | `ready_for_staging` |
| `/held/demo-2` | `succeeded` | `held` |
| `/skipped/demo-3` | `succeeded` | `skipped` |
| `/fail/demo-4` | `failed` | null |
| `/slow/demo-5` | 약 20초 동안 `running`, 이후 `succeeded` | `ready_for_staging` |

같은 요청 키에 다른 입력은 `idempotency_conflict`, 다른 키로 같은 정규화 URL을 제출하면 `candidate_exists`와 기존 작업 ID를 반환한다. `utm_*`, `fbclid`, `gclid`는 URL 중복 판단에서 제외한다. 현재 실제 Agent 식별자가 URL 기반이므로 유형만 바꿔 동일 URL을 다시 접수할 수 없다.

DB의 `content_generation_job`이 영속 상태의 기준이며 `content_generation_attempt`가 실행 점유·Celery ID·중단 이력을 보관한다. `menu_worker_schema`는 마이그레이션 버전만 관리한다. 초기화는 명시된 전용 DB에만 적용하고 비어 있지 않은 미등록 DB는 거부한다. 서비스 Alembic 마이그레이션과는 분리되어 있다.

dispatcher는 2초마다 확인하며 전송된 미실행 작업은 30초 뒤 재전송할 수 있다. 생성 시작 전 만료된 점유는 다시 대기 상태로 돌린다. 생성 시작 기록 이후의 중단은 `needs_attention`으로 남기며 자동 재생성하지 않는다. 만료된 점유 토큰은 새 작업 상태를 덮어쓸 수 없다.

산출물은 `job-artifacts` 볼륨의 `/var/lib/menu-jobs/jobs/{job_id}/attempts/{generation_attempt_id}/`에 저장한다. 원본 결과 JSON·fixture PNG·로그를 기록하고 파일 해시가 담긴 `manifest.json`을 마지막에 원자적으로 저장한다. 모든 결과는 `simulated: true`, `producer: fake_ani_menu_agent`이며 서비스 캐러셀 적재 형식이 아니다. LLM/Agent SQLite나 실제 카드 렌더링은 사용하지 않는다.

`needs_attention` 작업에 완성된 파일이 있다면 기존 파일 검증·DB 저장만 재개할 수 있다. 이 명령은 Agent를 다시 실행하지 않는다. 불완전·변조 파일은 확인 필요 상태를 유지한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-worker python -m menu_worker.jobs recover --job-id $job.job_id
```

대기 중인 작업은 `jobs cancel --job-id ...`로 취소할 수 있다. 실행 중 강제 취소는 제공하지 않는다. Flower의 Celery `SUCCESS`와 DB의 업무 상태는 다를 수 있으므로 결과는 `jobs show`로 확인한다.

## v2 작업 종류와 큐 분리

v2에서는 작업 생성 시 `job_kind`가 결정된다. 기존 작업은 `fake`이고 ID, 입력 해시, 상태, 실행 이력과 산출물 경로를 유지한다. `jobs show`는 작업 종류와 관계없이 ID로 조회한다. 두 종류가 요청 키와 URL 중복 검사를 공유하므로 새 테스트에는 새 키와 URL을 사용한다.

| 작업 종류 | Redis / Celery 큐 | Worker | 점검 성공 결과 |
| --- | --- | --- | --- |
| `fake` | `redis-tasks` / `content-generation` | `content@...` | 기존 시나리오별 결과 |
| `anime` | `anime-redis` / `anime-generation` | `anime@...` | `routing_verified` |

v3의 anime Worker는 아래 출처·작품 검증을 거친 작업의 실행 확인 JSON을 기록한다. v2에서 완료한 anime fixture의 기존 ID와 결과 파일은 계속 조회할 수 있다. 기본 Flower는 기존 `redis-tasks`를 모니터링하므로 anime 작업은 `jobs show` 또는 아래 smoke의 Worker 이름과 결과로 확인한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml --profile anime up -d --wait
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-dispatcher python -m menu_worker.jobs smoke
docker compose --env-file worker/.env -f compose.worker.yaml --profile anime exec -T anime-dispatcher python -m menu_worker.jobs smoke
```

단건 접수 방법은 아래 v3 예시를 따른다. 종류를 생략하면 해당 Dispatcher의 종류를 사용한다. anime 프로필이 꺼진 상태에서 접수된 anime 작업은 해당 프로필이 시작될 때까지 작업 DB에서 대기한다.

두 Worker의 교차 전달 거부를 직접 검증하려면 로컬 테스트 환경에서 두 Dispatcher만 잠시 정지한다. 아래 명령은 각 Worker에 다른 종류의 대기 작업 ID를 전송한다. 성공 시 `attempt_count`는 0이며 Dispatcher를 다시 시작하면 원래 담당 Worker가 처리한다.

```powershell
docker compose --env-file worker/.env -f compose.worker.yaml --profile anime stop job-dispatcher anime-dispatcher
try {
    docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-worker python tests/check_wrong_worker.py
    docker compose --env-file worker/.env -f compose.worker.yaml --profile anime exec -T anime-worker python tests/check_wrong_worker.py
} finally {
    docker compose --env-file worker/.env -f compose.worker.yaml --profile anime start job-dispatcher anime-dispatcher
}
```

anime 프로필까지 기동한 환경 전체를 종료할 때는 `docker compose --env-file worker/.env -f compose.worker.yaml --profile anime down`을 사용한다. 기본 종료 명령은 데이터 볼륨을 보존한다.

## v3 anime 출처·작품 입력 검증

anime 작업은 출처 ID, 원문 HTTPS URL, 콘텐츠 유형과 기존 `menu_work_id`를 함께 제출한다. 다음은 저장소의 원피스 공식 출처 예시다. 같은 키와 입력을 다시 제출하면 기존 작업을 반환한다.

```powershell
$job = docker compose --env-file worker/.env -f compose.worker.yaml --profile anime exec -T anime-dispatcher python -m menu_worker.jobs submit --kind anime --idempotency-key one-piece-82812 --source one_piece_official --url https://one-piece.com/news/82812/index.html --content-type goods --menu-work-id 8 | ConvertFrom-Json
docker compose --env-file worker/.env -f compose.worker.yaml --profile anime exec -T anime-dispatcher python -m menu_worker.jobs show --job-id $job.job_id
```

Worker 이미지는 애니 Agent의 `sources.json`, `source_type_map.json`, `menu_works.csv`를 읽기 전용으로 포함한다. 레지스트리 변경은 Compose로 이미지를 다시 빌드해 반영한다. 검증 규칙은 다음과 같다.

- 출처가 활성 상태이고 단건 실행에 허용된 역할·수집 방식·콘텐츠 유형이어야 한다. URL의 호스트와 경로는 등록된 출처 범위에 있어야 한다.
- 작품 ID가 카탈로그에 있고, 출처에 기재된 작품명이 정확히 그 작품 하나로 연결돼야 한다. 종합 매체처럼 본문을 읽어야 작품을 특정할 수 있는 출처는 `source_work_unresolved`로 거부한다.
- 잘못된 입력은 DB 접수 전에 오류 코드와 종료 코드 1을 반환한다. Worker는 실행 직전에 출처·작품과 입력 해시를 다시 확인한다.

검증 완료 시 `routing_verified`와 결과 JSON의 `validated_input`에서 출처, 정규화 URL, 유형, 작품 ID를 확인할 수 있다. 이 단계는 URL 등록 범위를 오프라인으로 검사한다. 기사 존재·본문 검증, LLM 생성, 새 작품 등록은 후속 단계다. smoke는 중복되지 않는 합성 기사 경로를 사용한다.

작업 DB의 v3 마이그레이션은 이전 완료 작업의 ID·상태·해시·산출물 경로를 보존한다. 작품 ID 없이 대기하던 v2 anime fixture는 `LEGACY_ANIME_FIXTURE_RETIRED`로 종료한다. 기존 실행의 점유가 만료되면 생성 시작 전에는 취소하고, 시작 후에는 `needs_attention`으로 기록한다.

## 콘텐츠 큐 회귀 검증

단위 테스트는 네트워크 없이, DB 통합 테스트는 `jobs-db` 안의 별도 `menu_content_jobs_test` DB에서 실행한다. 통합 테스트가 테스트 DB를 생성하고 자신이 만든 작업만 정리한다. 실제 서비스 DB 이름은 허용하지 않는다.

```powershell
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --cap-drop ALL me-nu-worker:local python -m unittest discover -s tests -v
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-worker python tests/run_postgres.py
```

DB 통합 테스트는 동시 접수·점유, 전송 실패, 점유 만료, 오래된 토큰 차단, 생성 결과 저장 실패 후 재생성 없는 복구, 종류별 점유·완료 및 복구를 검증한다. v1 업그레이드 검사는 실험용 DB `menu_content_jobs_migration_test`를 새로 만들어 사용한 뒤 정리하며, 같은 이름의 기존 DB가 있으면 중단한다. 네트워크 없는 실행에서는 DB 테스트가 명시적으로 skip된다.

## 2026-11-02 로컬 검증

`backend-labs`에서 Docker Desktop Linux 컨테이너로 확인했다.

- 네트워크 없는 Worker 이미지에서 단위 테스트 21개 통과.
- 실제 Redis를 통한 수동 작업, 1회 재시도, 새 Beat heartbeat, Flower 성공 이벤트 확인.
- 호스트 `127.0.0.1:5555` 접속과 비인증 401, 인증된 제어 요청의 읽기 전용 403 확인.
- 두 번째 Beat 실행 거부와 기존 Beat 재시작 확인.
- Worker 정지 중 접수한 작업이 작업 전용 Redis 재시작 후에도 보존되어 처리됨.
- 기존 PostgreSQL 2개와 로그인 세션 Redis의 컨테이너 ID·시작 시각이 전후 동일함.

콘텐츠 Agent·LLM·추천 배치 실행과 서비스 DB 마이그레이션은 수행하지 않았다. 이 결과는 로컬 실행 기반 검증이며 운영 배포나 유료 콘텐츠 생성의 복구 검증을 뜻하지 않는다.

## 2026-11-02 가짜 콘텐츠 큐 검증

- 최종 이미지의 네트워크 없는 단위 테스트 48개 통과. DB 통합 테스트 13개는 이 실행에서 skip하고 별도 실행했다.
- 전용 `menu_content_jobs_test` PostgreSQL에서 통합 테스트 13개 통과.
- 실제 Redis·dispatcher·Worker로 정상/보류/생략/실패 처리와 동일 요청·메시지 중복 방지 확인.
- `slow` 가짜 작업 실행 중 `job-worker` 컨테이너를 SIGKILL한 뒤 재시작: `needs_attention`, `attempt_count=1`, 자동 재생성 없음 확인.
- 완성 산출물 이후 DB 저장 실패는 파일을 다시 생성하지 않고 복구. 부분·변조 산출물은 확인 필요 상태 유지.
- 기존 runtime smoke의 재시도·Beat heartbeat·Flower 인증/읽기 전용/SUCCESS 확인, `pip check` 통과.
- 기존 서비스 PostgreSQL 2개와 세션 Redis의 컨테이너 ID·시작 시각이 작업 전후 동일. backend/model/agents/배포 설정 변경 없음.
- 실제 LLM 호출 0회, 서비스 콘텐츠 적재·추천 계산·서비스 DB 마이그레이션 없음. 테스트용 업무 DB·볼륨은 보존했고 서비스들은 정상 기동 상태다.
