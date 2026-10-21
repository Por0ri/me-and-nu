# 팀 테스트용 백엔드 배포

FastAPI, PostgreSQL 17 + pgvector, Redis를 한 서버에서 실행하는 준비 구성입니다. OAuth는 기본 비활성이며, 앱 키·마이그레이션 준비 후 [OAuth 설정과 백엔드 단독 검증](../docs/OAUTH_T02_1_SETUP.md)에 따라 활성화할 수 있습니다. 프런트엔드의 새 화면 연결은 후속 작업입니다.

Lightsail 초기 구성, 이미지 저장소 분리, 서버 증설 조건과 이전 절차는 [아키텍처와 증설 계획](ARCHITECTURE.md)을 참고하세요.

`compose.deploy.yaml`은 독립 실행 파일입니다. 로컬 개발용 `compose.yaml`과 합치지 마세요. 프로젝트 이름은 `me-nu-deploy`이며, 새 데이터 볼륨을 사용하므로 기존 로컬 DB 데이터는 복사되지 않습니다.

## 실행 범위

- 백엔드: Python 3.13, 일반 사용자 권한, Uvicorn 작업 프로세스 1개.
- PostgreSQL·Redis: 호스트 포트를 열지 않는 내부 네트워크. 백엔드에는 외부 API 호출용 네트워크도 연결됩니다.
- API: 호스트의 `127.0.0.1:8000`에만 연결됩니다. `API_PORT`로 포트 번호를 바꿀 수 있습니다.
- Redis: 비밀번호 인증, 전용 볼륨, AOF, `appendfsync always`, `noeviction`, 메모리 한도 128MB를 사용합니다. 한도 도달 시 새 세션 저장이 실패할 수 있습니다.
- 개발용 API와 인증 우회는 꺼져 있고, 세션은 Redis에 저장하며 쿠키는 HTTPS 전용입니다.

클라우드 서버 생성, 도메인, HTTPS 인증서, 리버스 프록시는 이 구성에 포함되지 않습니다. API의 루프백 포트는 호스트 프록시 또는 SSH 포트 전달로 연결할 수 있습니다. 원격 호스트 밖에서 접속하려고 DB·Redis 포트를 열지 마세요.

## 1. 환경 파일 준비

Docker Engine과 Docker Compose 플러그인이 준비된 서버에서 저장소 루트로 이동합니다. 아래 명령은 모두 저장소 루트 기준입니다.

`deploy/.env.example`을 `deploy/.env`로 복사하고 다음 값을 설정합니다. 실제 환경 파일은 커밋하지 않습니다.

| 값 | 설정 |
| --- | --- |
| `POSTGRES_PASSWORD` | 필수. 별도로 생성한 비밀번호 |
| `REDIS_PASSWORD` | 필수. DB 비밀번호와 다른 값 |
| `POSTGRES_USER`, `POSTGRES_DB` | 기본값 각각 `me_nu` |
| `BACKEND_IMAGE` | 기본값 `me-nu-backend:staging` |
| `API_PORT` | 기본값 `8000` |
| `FRONTEND_ORIGINS` | 실제 프런트엔드 출처. 예시 도메인은 교체 |
| `FORWARDED_ALLOW_IPS` | 신뢰할 프록시 IP. 기본값 `127.0.0.1` |

비밀번호는 아래 명령을 **두 번** 실행해 각각 생성합니다. 연결 URL에 그대로 들어가므로 이 설정에서는 16진수 문자열만 사용합니다.

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

서버에서 Python 실행 파일이 `python3`이면 명령의 `python`을 바꾸세요. 두 필수 비밀번호가 비어 있으면 Compose 설정 검증이 실패합니다.

Linux 서버에서는 `chmod 600 deploy/.env`로 읽기 권한을 제한하세요. `config --quiet`는 값 출력 없이 검사합니다. 실제 비밀번호가 채워진 상태에서 `config` 결과 전체를 공유하지 마세요.

`FRONTEND_ORIGINS`는 `https://test.example.com`처럼 프로토콜을 포함한 정확한 출처를 쉼표로 구분해 입력합니다. 경로나 끝 슬래시는 넣지 않습니다. `SameSite=Lax` 쿠키를 사용하므로 프런트엔드와 API는 같은 사이트에서 제공하는 구성을 권장합니다. 서로 다른 사이트를 CORS 목록에 추가하는 것만으로 쿠키 전송 문제가 해결되지는 않습니다.

호스트 프록시에서 컨테이너에 연결하면 백엔드가 보는 프록시 주소가 Docker 브리지 게이트웨이일 수 있습니다. 실제 연결 경로를 확인해 `FORWARDED_ALLOW_IPS`에 그 신뢰할 주소만 넣으세요. 무조건 `*`로 지정하지 않습니다.

## 2. 최초 실행

```sh
docker compose --env-file deploy/.env -f compose.deploy.yaml config --quiet
docker compose --env-file deploy/.env -f compose.deploy.yaml build
docker compose --env-file deploy/.env -f compose.deploy.yaml up -d --wait --wait-timeout 180
```

PostgreSQL이 준비되면 `migrate`가 `python -m alembic upgrade head`를 한 번 실행합니다. 마이그레이션이 성공하고 PostgreSQL·Redis가 정상인 경우 API가 시작됩니다. `migrate`가 종료 코드 0으로 멈춰 있는 것은 정상입니다. 마이그레이션 실패 시 로그를 확인하고 원인을 해결한 뒤 재실행합니다.

이미지는 백엔드 코드와 마이그레이션을 포함합니다. Agent 실행 코드, Git 이력·적재 도구, 프런트엔드는 포함하지 않습니다. `agent_contents`, `agent_interests`, `content_images`, 영화 Agent 실행 명령은 이 이미지의 지원 범위가 아닙니다.

## 3. 상태 확인

```sh
docker compose --env-file deploy/.env -f compose.deploy.yaml ps -a
docker compose --env-file deploy/.env -f compose.deploy.yaml logs --tail=100 api migrate
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/health/db
curl --fail http://127.0.0.1:8000/health/redis
```

`API_PORT`를 바꿨으면 curl의 포트도 맞춥니다. API 상태 검사는 세 경로가 모두 `status: ok`인지 확인합니다. `/health` 하나만 성공한 것으로 DB·Redis 연결까지 정상이라고 판단하지 않습니다.

외부 연결은 HTTPS 프록시를 설정한 뒤 해당 주소에서도 확인합니다. SSH 전달을 쓰려면 개인 컴퓨터에서 `ssh -L 8000:127.0.0.1:8000 사용자@서버`로 연결한 뒤 로컬 주소로 상태를 확인할 수 있습니다. HTTP 상태 확인이 성공해도 HTTPS 전용 쿠키의 로그인 동작까지 검증한 것은 아닙니다.

로그를 계속 보려면 다음을 실행하고, 종료할 때 Ctrl+C를 누릅니다.

```sh
docker compose --env-file deploy/.env -f compose.deploy.yaml logs -f --tail=100 api postgres redis
```

## 4. 선택: 테스트 초기 데이터

빈 DB에서 테스트용 정책·영화 분류·샘플 공개 콘텐츠가 필요하면 API 기동 후 한 번 실행합니다.

```sh
docker compose --env-file deploy/.env -f compose.deploy.yaml run --rm --no-deps api python -m app.seeds.v1_local
```

이 데이터에는 로컬 테스트용 약관과 샘플 콘텐츠가 포함됩니다. 실제 운영 문안으로 사용하지 마세요. 공개 정책 목록은 `/api/v1/policies`로 확인할 수 있습니다. 데모 사용자 seed는 실행하지 않습니다. 이 배포에서는 개발용 로그인과 인증 우회가 비활성화되어 있습니다.

## 5. 중지와 다시 실행

```sh
docker compose --env-file deploy/.env -f compose.deploy.yaml down
docker compose --env-file deploy/.env -f compose.deploy.yaml up -d --wait --wait-timeout 180
```

`down`은 데이터 볼륨을 남깁니다. `down -v`는 PostgreSQL·Redis 데이터를 삭제하므로 일상적인 중지·업데이트에 사용하지 마세요. 환경 파일의 PostgreSQL 비밀번호를 바꾸는 것만으로 이미 만들어진 DB 계정의 비밀번호가 변경되지는 않습니다.

## 6. 코드 업데이트

DB 백업을 확보하고 이전 이미지를 별도 태그로 보존한 뒤 코드를 업데이트합니다. 아래 이미지 이름은 기본 설정 기준이며, `BACKEND_IMAGE`를 바꿨다면 동일하게 맞춥니다.

현재 이미지는 기존 `backend/requirements.txt`를 사용하므로 재빌드 시 의존성 버전이 달라질 수 있습니다. 검증한 배포 이미지는 커밋별 태그로 보존하고, 동일한 설치 결과가 필요한 단계에서는 의존성 버전도 고정하세요.

```sh
docker image tag me-nu-backend:staging me-nu-backend:before-update
docker compose --env-file deploy/.env -f compose.deploy.yaml stop api
docker compose --env-file deploy/.env -f compose.deploy.yaml rm -f migrate
docker compose --env-file deploy/.env -f compose.deploy.yaml build
docker compose --env-file deploy/.env -f compose.deploy.yaml up -d --wait --wait-timeout 180
```

완료된 `migrate` 컨테이너를 제거해 새 코드의 마이그레이션을 다시 실행합니다. 오류가 나면 API를 다시 열기 전에 `migrate` 로그를 확인합니다. 성공 후 세 상태 경로와 공개 정책 조회를 다시 확인합니다.

이전 API 이미지로 되돌릴 때는 **현재 DB 스키마와 이전 코드가 호환되는지 먼저 확인**합니다. Linux 셸에서는 다음처럼 이미지 값을 일시적으로 바꿀 수 있습니다.

```sh
BACKEND_IMAGE=me-nu-backend:before-update docker compose --env-file deploy/.env -f compose.deploy.yaml up -d --no-build --no-deps --wait --wait-timeout 180 api
```

이 명령은 API만 교체하며 DB 스키마를 되돌리지 않습니다. 다음 전체 실행에도 이전 이미지를 유지하려면 `deploy/.env`의 이미지 값도 맞추세요. 스키마 변경이 호환되지 않으면 별도의 복원 계획이 필요하며, 무조건 `alembic downgrade`를 실행하지 않습니다.

## 남은 배포 작업

- OAuth 연결, 프런트엔드 로그인·세션 통합 검증.
- 도메인·HTTPS 프록시와 팀 접근 제한 구성.
- 서버 외부 PostgreSQL 백업, 복원 연습, 디스크·Redis 메모리·오류 모니터링. 영속 볼륨은 백업을 대신하지 않습니다.
- 현재 채팅 생성 작업은 프로세스 안에서 실행됩니다. 작업 프로세스 1개를 유지하며, 재시작 시 진행 중 작업이 중단될 수 있습니다. 지속적인 작업 처리는 별도 큐 구성이 필요합니다.

## 로컬 검증 기록 — 2026-10-16

기존 로컬 DB와 분리된 Compose 프로젝트와 새 볼륨으로 다음을 확인했습니다.

- 이미지 빌드, 빈 PostgreSQL DB 마이그레이션, pgvector 확장, 기본 seed 적재 성공.
- API·DB·Redis 상태 검사 성공 및 Redis 중단 시 상태 검사 실패 확인.
- 컨테이너를 제거하고 다시 생성한 뒤 PostgreSQL 샘플 데이터와 Redis AOF 데이터 유지 확인.
- 일반 사용자 실행, 환경파일 제외, 개발용 API 비활성화, Redis 비밀번호 인증 확인.
- 빌드한 Linux 이미지에서 마이그레이션·API·인증·CORS·Redis 관련 기존 테스트 94개 통과.

클라우드 서버 배포와 도메인·HTTPS 연결은 아직 수행하지 않았습니다.
