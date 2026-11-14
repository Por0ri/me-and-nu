# Celery Worker · Beat · Flower 로컬 실행 기반

콘텐츠 작업 전용 Celery 환경이다. Python 3.12, Celery 5.6.3, Flower 2.2.0을 사용하며 Compose 프로젝트 이름은 `me-nu-worker`다. 기본 구성은 무과금 점검용이다. 선택 구성으로 기존 애니 Agent의 정기 기사 수집과 단건 콘텐츠 생성을 연결할 수 있다.

| 서비스 | 역할 |
| --- | --- |
| `redis-tasks` | 작업 메시지와 작업 결과 저장. 결과 TTL은 1시간 |
| `worker` | 점검용 작업 실행 |
| `beat` | 기본 30초 간격으로 heartbeat 작업 전송 |
| `flower` | Worker·작업 상태 조회. 로컬 접속·인증·읽기 전용 설정 사용 |
| `jobs-db` | 전용 PostgreSQL 17. 콘텐츠 작업·실행 이력 저장, 호스트 포트 없음 |
| `job-init` | 작업 전용 DB를 v6로 초기화하거나 기존 v1~v5 작업을 마이그레이션 |
| `job-worker` | `content-generation` 큐의 가짜 Agent 실행, 동시 실행 1개 |
| `job-dispatcher` | fake 작업 전달 및 중단된 fake 작업 정리 |
| `anime-redis`, `anime-worker`, `anime-dispatcher` | 선택형 `anime` 프로필. 별도 Redis와 산출물 볼륨에서 애니 큐 전달을 점검 |
| `discovery-worker` | `compose.discovery.yaml`에서 Beat가 보낸 출처별 수집을 실행하고 확인된 기사만 작업 DB에 접수 |
| `generation-redis`, `generation-dispatcher`, `generation-worker` | `compose.generation.yaml`의 `generation` 프로필. 접수된 기사를 읽고 실제 Agent로 콘텐츠 생성 |

작업은 전용 PostgreSQL과 Redis를 사용한다. 수집·생성용 이미지에 기존 Agent와 출처·작품 카탈로그를 포함한다. 서비스 API와 추천 배치는 기존 배포 구성에서 실행한다.

## 시작

Docker Desktop의 Linux 컨테이너와 Docker Compose가 필요하다. 명령은 저장소 루트에서 실행한다. `.env` 초기화에 사용하는 호스트 Python과 실제 Worker를 실행하는 컨테이너 Python은 별개다.

기존 환경은 아래 시작 명령 전에 이전 버전의 콘텐츠 소비자를 종료한다. v1에서는 `docker compose --env-file worker/.env -f compose.worker.yaml stop job-dispatcher job-worker`를 사용한다. v2에서 anime 프로필도 사용했다면 `--profile anime stop job-dispatcher anime-dispatcher job-worker anime-worker`로 네 서비스를 종료한다. PostgreSQL과 산출물 볼륨은 유지한다. 빌드에는 Compose 설정의 추가 빌드 컨텍스트가 필요하다.

```powershell
python worker/setup_local.py
docker compose --env-file worker/.env -f compose.worker.yaml up -d --build --wait
docker compose --env-file worker/.env -f compose.worker.yaml ps
```

초기화 명령은 Flower와 전용 DB 비밀번호를 `worker/.env`에 저장한다. 기존 파일에는 누락된 `CONTENT_JOB_DB_PASSWORD`만 추가하며 기존 Flower 설정은 보존한다. 비밀번호는 출력하지 않는다. 파일은 Git에서 제외된다. DB 비밀번호는 최초 볼륨 초기화 때 적용되므로 볼륨을 만든 뒤 임의로 바꾸지 않는다. Flower 접속 정보는 본인의 로컬 파일에서 확인한다.

[Flower](http://127.0.0.1:5555)는 기본적으로 `127.0.0.1:5555`에만 열린다. 로그인에는 `FLOWER_BASIC_AUTH`의 `사용자:비밀번호`를 사용한다. 포트가 이미 사용 중이면 `worker/.env`의 `FLOWER_PORT`를 바꾸고 위 기동 명령을 다시 실행한다.

호스트 PC의 Redis Insight에서 기본 작업 큐는 `redis://127.0.0.1:6380/0`, `anime` 프로필의 애니 작업 큐는 `redis://127.0.0.1:6381/0`으로 연결한다. 두 Redis 모두 DB 0에 대기 메시지를, DB 1에 Celery 결과를 저장한다. 포트는 `worker/.env`의 `REDIS_TASKS_PORT`와 `ANIME_REDIS_PORT`로 조정할 수 있다.

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

Redis는 작업 전용 볼륨에 AOF를 저장하며 `appendfsync everysec`을 사용한다. 비정상 호스트 종료 시 최근 약 1초의 기록이 유실될 수 있으므로, 이 설정만으로 콘텐츠 작업의 무손실 실행을 보장하지 않는다. 결과는 1시간 뒤 만료되고, Flower는 최근 2,000개 작업 상태를 별도 볼륨에 보존한다. `tasks` 네트워크는 내부 전용이며, Flower와 `redis-tasks`, `anime-redis`는 localhost 포트 연결을 위해 `monitor` 네트워크에도 참여한다.

## 기사 수집부터 콘텐츠 생성까지

`compose.discovery.yaml`을 추가하면 Beat가 기본으로 원피스 공식 굿즈(`one_piece_official:goods`) 한 경로를 예약한다. 15분마다 ONE PIECE.com 공식 뉴스 목록에서 발행사가 굿즈(`グッズ`)로 분류한 개별 기사를 최대 5건 찾아 원문과 작품 ID `8`을 확인한다. 이 수집 권한은 출처와 유형을 명시한 경로에 적용한다. 목록 조회에는 LLM 키가 필요 없다.

수집 Worker는 검증을 통과한 기사를 전용 작업 DB에 `anime_generate`로 접수한다. 같은 출처·기사 URL의 기존 수동 작업과 반복 수집이 한 작업을 공유한다. 원피스 공식 뉴스의 숫자 기사 경로에서 `ref`만 다른 URL도 접수 시 기존 작업을 조회한다. 다른 기사 식별 쿼리는 보존한다. 이 규칙을 적용하기 전에 `ref` 차이로 이미 두 작업이 만들어졌다면 그 두 작업의 실행·내보내기 이력은 별개로 남는다.

첫 회차에는 현재 목록에서 한도 안에 들어오는 기사도 접수 대상이다. 이후 회차도 현재 목록을 기준으로 수집하므로 중단 중이거나 한 주기에 한도보다 많은 새 기사가 올라오면 일부 기사가 빠질 수 있다.

수집만 켜려면 저장소 루트에서 아래 명령을 실행한다. `generation-dispatcher`와 `generation-worker`가 꺼져 있으면 접수된 기사는 작업 DB에서 대기한다. 두 서비스가 이미 켜져 있으면 새로 접수한 기사도 바로 생성 대상이 된다.

```powershell
$discovery = @('--env-file','worker/.env','-f','compose.worker.yaml','-f','compose.discovery.yaml')
docker compose @discovery up -d --build --wait
docker compose @discovery logs --tail 50 beat discovery-worker
```

`worker/.env`에서 `ANIME_DISCOVERY_ROUTES`를 생략하면 원피스 공식 굿즈를 수집한다. 기존 파일에 `prtimes_rss:news` 등 경로를 직접 지정했다면 그 값이 우선한다. 여러 경로는 등록된 `출처ID:유형`을 쉼표로 구분하고, 모든 일정을 끄려면 이 값을 빈 문자열로 설정한다. 각 출처는 허용한 유형만 수집한다. `ANIME_DISCOVERY_INTERVAL_SECONDS`는 5~86400초, `ANIME_DISCOVERY_LIMIT`은 출처·유형별 회차당 1~20건이다.

수집 결과는 Flower의 `content.anime_discover`에서 확인한다. `discovered`는 발견 후보, `submitted`는 신규 작업, `duplicates`는 기존 작업, `unresolved`는 원문·작품을 확정할 수 없는 후보, `rejected`는 접수 검증 탈락, `fetch_failed`는 회차 내에 읽지 못한 후보 수다. 일부만 읽으면 `partial`, 같은 출처·유형의 이전 수집이 실행 중이면 `already_running`으로 표시된다. 목록 전체 호출이 실패하면 해당 작업은 실패로 기록되고 다음 Beat 주기에 다시 시도한다.

실제 생성을 함께 가동할 때는 아래 **유료 단건 실행**의 전용 키와 비용 한도를 준비한 뒤 두 Compose 파일을 함께 사용한다. 생성 Worker는 이미 대기 중인 작업도 처리한다. 생성 결과는 기존 검수와 `export-staging` 흐름을 따른다.

```powershell
docker compose @discovery -f compose.generation.yaml --profile generation up -d --build --wait discovery-worker beat generation-worker generation-dispatcher
```

정기 수집을 끄려면 수집 Worker를 중지하고 기본 설정으로 Beat를 다시 기동한다.

```powershell
docker compose @discovery stop discovery-worker
docker compose --env-file worker/.env -f compose.worker.yaml up -d beat
```

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

Dispatcher는 2초마다 미발행 작업을 확인한다. 같은 작업의 발행은 DB 행 잠금으로 직렬화하고, 브로커가 반환한 Celery ID를 기록한 뒤에는 Worker의 점유를 기다린다. 발행 실패로 기록이 없으면 다음 순회에서 재시도한다. Redis 전송 직후 DB 커밋 전에 프로세스가 중단되면 같은 작업이 다시 전달될 수 있다. 이때 Worker는 DB 점유를 획득한 한 건만 실행한다.

브로커에서 메시지가 유실됐다고 확인한 경우, 해당 큐의 Dispatcher에서 `jobs show --job-id <ID>`로 `last_task_id`를 조회한 뒤 `jobs requeue-delivery --job-id <ID> --task-id <last_task_id>`를 실행한다. 조회한 발행 ID가 일치하고 작업이 아직 점유되지 않았을 때만 다음 순회에서 다시 발행한다. 생성 시작 전 점유가 만료된 작업은 자동으로 다시 대기 상태가 된다.

세 Dispatcher 모두 최근 30초 이내에 실패 없이 순회를 마쳤고 해당 브로커가 Redis `PING`에 응답할 때 헬스체크를 통과한다. 일부 작업만 발행에 실패해도 그 순회에서 직전 정상 표시를 지운다.

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

애니 점검(`submit`)과 수동 생성(`generate`) 모두 출처가 제공한 절대 HTTPS canonical URL을 `--canonical-url`로 받을 수 있다. 요청 URL과 같은 등록 출처의 기사로 검증되면 이 주소를 사용하고, 생략했거나 유효하지 않으면 요청 URL로 기사를 판별한다. 큐는 이 과정에서 웹 페이지를 요청하지 않는다.

작업 DB v5의 기사 중복 키는 각 큐에서 `source_id`와 정규화한 URL의 조합이다. 기사 경로 끝의 슬래시와 fragment는 제거하고 사이트 루트의 `/`는 유지한다. `utm_*`, `fbclid`, `gclid`는 대소문자와 관계없이 제외한다. 허용된 다른 쿼리 값은 보존하므로 `?id=1`과 `?id=2`는 서로 다른 기사다. 같은 키로 재시도하면 기존 작업을 반환하고, 다른 키로 같은 기사를 제출하면 `candidate_exists`와 기존 작업 ID를 반환한다. 무과금 점검과 수동 생성은 같은 기사를 각각 한 번 처리할 수 있다.

v4에서 이미 두 주소로 접수한 경우에는 기존 작업 ID, 입력 해시, 산출물과 시도 이력을 보존한다. 완료했거나 생성이 시작된 작업을 우선 대표로 두고, 실행 전인 중복 작업은 `DUPLICATE_ARTICLE`로 취소한다. 이력에서 `duplicate_of_job_id`로 대표 작업을 조회할 수 있다.

검증 완료 시 `routing_verified`와 결과 JSON의 `validated_input`에서 출처, 정규화 URL, 유형, 작품 ID를 확인할 수 있다. 이 단계는 URL 등록 범위를 오프라인으로 검사한다. 기사 존재·본문 검증, LLM 생성, 새 작품 등록은 후속 단계다. smoke는 중복되지 않는 합성 기사 경로를 사용한다.

작업 DB의 v3 마이그레이션은 이전 완료 작업의 ID·상태·해시·산출물 경로를 보존한다. 작품 ID 없이 대기하던 v2 anime fixture는 `LEGACY_ANIME_FIXTURE_RETIRED`로 종료한다. 기존 실행의 점유가 만료되면 생성 시작 전에는 취소하고, 시작 후에는 `needs_attention`으로 기록한다.

## 수동 실제 생성과 무과금 점검

작업 DB v4는 같은 원문 URL의 점검 작업과 실제 생성을 각각 한 건씩 기록한다. 요청 키는 모든 종류에서 유일하므로 실제 생성에는 별도 키를 쓴다. 일반 `anime` 큐의 `submit`과 `smoke`는 v3의 오프라인 검증을 실행한다. 서비스 도면과 작업 DB 도면의 반영 사항은 [ERD V1.2 검토](../docs/erd/ERD_V1_2_GENERATION_REVIEW.md)를 참고한다.

**무과금 이미지 검사.** 전용 키 없이 빌드한 뒤 네트워크를 끈 컨테이너에서 실행한다. 실제 Agent CLI, 두 카탈로그의 일치, 한글 텍스트 렌더링, PNG 디코딩과 오프라인 검수 그래프를 확인한다.

```powershell
$stack = @('--env-file','worker/.env','-f','compose.worker.yaml','-f','compose.generation.yaml','--profile','generation')
docker compose @stack build generation-worker
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m me-nu-anime-generation:local python tests/check_generation_image.py
```

**유료 단건 실행.** 운영자가 출처·작품과 공급자 측 비용 한도를 정한 경우에 사용한다. `ANIME_LLM_KEY_HOST_FILE`에는 키 한 줄만 담긴 전용 파일의 절대 경로를 지정한다. 이 파일은 generation Worker에만 읽기 전용으로 연결된다. 기본 이미지와 점검 명령은 이 파일을 요구하지 않는다.

```powershell
$env:ANIME_LLM_KEY_HOST_FILE = 'C:\private\anime-api-key.txt'
docker compose @stack up -d --wait generation-worker generation-dispatcher
$job = docker compose @stack exec -T generation-dispatcher python -m menu_worker.jobs generate --idempotency-key live-one-piece-82812 --source one_piece_official --url https://one-piece.com/news/82812/index.html --content-type goods --menu-work-id 8 | ConvertFrom-Json
docker compose @stack exec -T generation-dispatcher python -m menu_worker.jobs show --job-id $job.job_id
```

전용 Redis·Worker가 `anime_generate`를 점유하고 Agent의 `crawl-generate --expected-work-id`를 실행한다. 원문을 수집한 뒤 기사 증거에서 확인한 작품이 요청과 일치해야 유료 추출로 진행한다. 이미지 후보는 최대 4개, 실행 제한은 20분이다. 한 번의 생성 안에 여러 모델 호출과 검수·수정이 발생할 수 있다. 이 두 제한은 금액 상한이 아니다.

`ready_for_staging`은 독립 검수와 원본 카드 PNG 검증을 통과한 결과다. 뉴스·이벤트·굿즈는 2~10장, 나머지는 3~10장을 허용한다. Agent가 승인으로 표시해도 검수 요약에 `hard_blockers`가 남거나 개별 QA에 `severity=blocker`가 있으면 Worker는 `needs_attention`과 `agent_review_incomplete`를 기록한다. 파일을 복구할 때도 같은 검사를 거친다.

`held`, `skipped`, `rejected`는 원래 producer 상태와 함께 기록한다. 결과 JSON·검수/사용량 기록·카드·manifest는 전용 `generation-artifacts` 볼륨의 원래 생성 시도 디렉터리에 보관한다. 서비스 공개는 별도 검수·적재 단계다.

`agent.log`에는 자식 프로세스의 시작과 종료 코드가 기록된다. 원문·키·공급자 응답을 보호하기 위해 stdout·stderr는 보관하지 않는다. 생성 도중 문제가 생기면 작업의 `error_code`와 해당 시도의 결과·검수 기록을 함께 확인한다.

키·실행 환경 점검 또는 Agent 프로세스 시작에 실패해 자식 프로세스가 실행되지 않은 것이 확인되면, 원인을 수정한 뒤 **generation-dispatcher**에서 `jobs retry-preflight --job-id <ID>`로 같은 작업을 다시 접수한다. Agent 실행 후 시간 초과나 Worker 중단처럼 유료 호출 여부가 불확실한 작업은 `needs_attention`으로 남긴다. 이 경우 **generation-worker**에서 `jobs recover --job-id <ID>`로 기존 산출물만 다시 검증할 수 있다.

v4에서 같은 기사로 여러 생성 작업이 접수됐다면 재시도는 v5 대표 작업 ID로 요청한다. 같은 기사로 묶인 생성 작업 중 하나라도 실행 중이거나 결과가 남았거나 Agent가 시작됐을 가능성이 있으면 `requeued: false`를 반환한다. 모든 시도가 시작 전에 끝났다고 확인된 경우에만 대표 작업을 한 번 다시 대기열에 넣는다. 같은 URL의 무과금 라우팅 점검은 별도로 취급한다.

### 완료된 생성 결과를 콘텐츠 적재기에서 미리 확인

`generation-export`는 `succeeded / ready_for_staging`이고 생산자 검수 상태가 `ready_for_feed`인 `anime_generate` 작업을 내보낸다. 원본 산출물은 읽기 전용으로 열고, 해당 시도의 manifest·요청·검수 결과·PNG를 검증한다. 검증이 끝나면 전용 작업 DB에 기사별 선택을 기록하고 새 출력 경로에 `bundle.json`과 카드를 공개한다. 번들의 `reviewEvidence`에서 선택된 작업 ID, 생성 시도 ID, 결과 해시를 확인할 수 있다. 내보내기에는 Agent 키가 필요 없다.

v4에서 같은 출처와 정규화된 기사 URL로 접수한 작업이 여러 건이면 검수를 통과한 작업 ID를 지정한다. 대표 작업이 보류됐어도 다른 작업을 지정할 수 있다. 둘 다 통과했다면 먼저 검증을 마치고 DB에 선택을 기록한 결과만 내보낸다. 이후 다른 작업 ID로 요청하면 `staging_result_already_selected`와 선택된 `existing_job_id`가 반환된다. 출력 파일 쓰기가 중단됐을 때는 같은 작업 ID와 결과 해시로 새 경로에 다시 내보낼 수 있다. 작업·시도 이력은 그대로 조회된다.

v6 전환 시에는 이전 버전의 내보내기를 종료하고 최신 Worker 및 생성 이미지를 빌드한다. 아래 명령은 `job-init`이 전용 작업 DB 마이그레이션을 마친 뒤 실행된다. v5까지는 내보낸 기록이 없으므로 이미 전달한 번들이 있다면 해당 번들의 `reviewEvidence.resultSha256`을 작업 기록과 대조한 뒤 `reviewEvidence.jobId`를 v6에서 먼저 내보낸다.

저장소 루트에서 실행한다. `$jobId`에는 완료한 작업의 UUID를 지정하고, 백엔드 실행 환경의 `DATABASE_URL`에는 미리 볼 PostgreSQL의 읽기 전용 연결을 설정한다. `$expectedDatabase`는 해당 DB의 정확한 이름이다.

```powershell
$stack = @('--env-file','worker/.env','-f','compose.worker.yaml','-f','compose.generation.yaml')
$exportRoot = (New-Item -ItemType Directory -Force -Path 'out/carousel-staging').FullName
docker compose @stack run --rm -T -v "${exportRoot}:/export" generation-export --job-id $jobId --output "/export/$jobId"
if ($LASTEXITCODE -ne 0) { throw 'Bundle export failed' }
$bundle = Join-Path $exportRoot "$jobId/bundle.json"
Push-Location backend
try {
    .\.venv\Scripts\python.exe -m app.seeds.carousel_contents --file $bundle --expected-database $expectedDatabase
    if ($LASTEXITCODE -ne 0) { throw 'Importer dry-run failed' }
} finally { Pop-Location }
```

처음 내보낼 때는 새 출력 경로가 필요하다. 같은 번들의 dry-run을 반복하려면 적재기 명령만 다시 실행한다. 새 콘텐츠에서 `mode=dry_run`, `created=1`, `status=pending_review`, `content_id=null`은 생성 예정 1건을 뜻한다. 서비스 적재기는 이 경로에서 읽기 전용 트랜잭션을 사용한다. 운영자 승인과 서비스 DB의 작품 링크 생성은 각각 별도 단계다.

## 콘텐츠 큐 회귀 검증

단위 테스트는 네트워크 없이, DB 통합 테스트는 `jobs-db` 안의 별도 `menu_content_jobs_test` DB에서 실행한다. 통합 테스트가 테스트 DB를 생성하고 자신이 만든 작업만 정리한다. 실제 서비스 DB 이름은 허용하지 않는다.

```powershell
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --cap-drop ALL me-nu-worker:local python -m unittest discover -s tests -v
docker compose --env-file worker/.env -f compose.worker.yaml exec -T job-worker python tests/run_postgres.py
```

DB 통합 테스트는 동시 접수·점유, 전송 실패, 점유 만료, 오래된 토큰 차단, 생성 결과 저장 실패 후 재생성 없는 복구, 종류별 점유·완료 및 복구를 검증한다. v1 업그레이드 검사는 실험용 DB `menu_content_jobs_migration_test`를 새로 만들어 사용한 뒤 정리하며, 같은 이름의 기존 DB가 있으면 중단한다. 네트워크 없는 실행에서는 DB 테스트가 명시적으로 skip된다.

정기 수집 회귀는 두 단계로 확인한다.

- 수집 이미지에서 외부 네트워크를 끄고 `PYTHONPATH=/app:/opt/ani-agent/src /opt/ani-venv/bin/python tests/check_discovery_agent.py`를 실행한다. 저장된 RSS·기사 HTML을 사용해 실제 Agent의 출처·작품 확인을 검증한다.
- 임시 Docker 내부 네트워크에 PostgreSQL 17(`jobs-db`)과 Redis 두 개(`redis-tasks`, `generation-redis`)를 준비한다. 생성 이미지에서 `CONTENT_JOB_TEST_DATABASE_URL`을 비어 있는 `menu_content_jobs_test`로 지정한 뒤 `python -B tests/check_discovery_cycle.py`를 실행한다. 테스트 프로세스가 Beat와 두 Worker를 기동한다. 실제 API 키는 필요 없다.

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
