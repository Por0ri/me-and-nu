# Celery Worker · Beat · Flower 로컬 실행 기반

Celery 작업 전달, 주기 실행, 재시도와 모니터링을 확인하는 독립 실행 환경이다. Python 3.12, Celery 5.6.3, Flower 2.2.0을 사용하며 Compose 프로젝트 이름은 `me-nu-worker`다. 실제 애니 콘텐츠 생성·검수·적재 작업은 후속 연결 범위다.

| 서비스 | 역할 |
| --- | --- |
| `redis-tasks` | 작업 메시지와 작업 결과 저장. 결과 TTL은 1시간 |
| `worker` | 점검용 작업 실행 |
| `beat` | 기본 30초 간격으로 heartbeat 작업 전송 |
| `flower` | Worker·작업 상태 조회. 로컬 접속·인증·읽기 전용 설정 사용 |

이 Compose는 API·PostgreSQL·콘텐츠 Agent·추천 모델을 실행하지 않는다. 기존 로그인 세션 Redis와도 분리되어 있다. Checking의 추천 컨테이너와 매일 추천 계산 일정은 기존 구성을 따른다.

## 시작

Docker Desktop의 Linux 컨테이너와 Docker Compose가 필요하다. 명령은 저장소 루트에서 실행한다. `.env` 초기화에 사용하는 호스트 Python과 실제 Worker를 실행하는 컨테이너 Python은 별개다.

```powershell
python worker/setup_local.py
docker compose --env-file worker/.env -f compose.worker.yaml up -d --build --wait
docker compose --env-file worker/.env -f compose.worker.yaml ps
```

초기화 명령은 임의의 Flower 비밀번호를 생성해 `worker/.env`에 저장하며 기존 파일을 덮어쓰지 않는다. 이 파일은 Git에서 제외된다. Flower 접속 정보는 본인의 로컬 파일에서 확인한다.

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

현재 단계는 로컬 실행 기반이다. 서버 배포·도메인 연결·실제 콘텐츠 정기 생성은 포함하지 않는다. 콘텐츠 큐를 연결할 때는 다음 항목이 추가로 필요하다.

- 후보별 DB 작업 기록, 실행 점유와 중복 방지, 큐 전송 실패 복구.
- 기존 애니 Agent의 Python 3.12 실행 환경·한글 폰트·영속 출력 연결.
- 생성 결과와 원본 PNG 보존, 기존 검수·bundle 검증·적재 계약 유지.
- 유료 생성 재시도와 완료 결과 재적재의 분리, 후보·시도·비용 제한.
- 실제 출처 discovery와 업무별 Beat 일정.

기존 `agent_run.queue_task_id`의 캐러셀 적재 식별자와 새 Celery 실행 ID는 별도로 관리해야 한다. 추천 계산을 Celery로 이전하는 작업은 이 실행 기반에 포함하지 않는다.

현재 Worker는 동시 실행 1개이며 점검 작업의 soft/hard 제한은 15/20초, Redis visibility timeout은 30초다. 실제 장시간 Agent를 연결할 때는 실행 제한·재전달·중단 복구 정책을 함께 바꿔야 한다. 점검 작업의 재전달 설정을 유료 생성에 그대로 적용하지 않는다.

## 2026-11-02 로컬 검증

`backend-labs`에서 Docker Desktop Linux 컨테이너로 확인했다.

- 네트워크 없는 Worker 이미지에서 단위 테스트 21개 통과.
- 실제 Redis를 통한 수동 작업, 1회 재시도, 새 Beat heartbeat, Flower 성공 이벤트 확인.
- 호스트 `127.0.0.1:5555` 접속과 비인증 401, 인증된 제어 요청의 읽기 전용 403 확인.
- 두 번째 Beat 실행 거부와 기존 Beat 재시작 확인.
- Worker 정지 중 접수한 작업이 작업 전용 Redis 재시작 후에도 보존되어 처리됨.
- 기존 PostgreSQL 2개와 로그인 세션 Redis의 컨테이너 ID·시작 시각이 전후 동일함.

콘텐츠 Agent·LLM·추천 배치 실행과 서비스 DB 마이그레이션은 수행하지 않았다. 이 결과는 로컬 실행 기반 검증이며 운영 배포나 유료 콘텐츠 생성의 복구 검증을 뜻하지 않는다.
