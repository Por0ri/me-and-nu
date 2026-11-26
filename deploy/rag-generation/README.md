# RAG 비활성 운영 배포

이 구성은 기존 기사 생성·추천·공개 콘텐츠를 변경하지 않고 RAG 실행 이미지를
준비한다. 기본 명령은 도움말이며 유료 활성화용 구성은 포함하지 않는다.
저장소 루트의 `compose.rag.yaml`은 별도 로컬 실험 스택용이므로 운영에 사용하지 않는다.

| 파일 | 동작 |
| --- | --- |
| `compose.build.yaml` | Worker 기반·RAG·backend 적재기 이미지 빌드만 수행 |
| `compose.runtime.yaml` | digest로 고정한 이미지, 기존 네트워크와 파일 경로, 기본 `--help` |
| `compose.inactive.yaml` | 별도 RAG Worker를 `RAG_GENERATION_ENABLED=false` 상태로 점검 가능 |
| `compose.controller.yaml` | 기존 일일 controller에 실행 사본·runtime·별도 RAG inbox mount만 명시적으로 추가 |
| `compose.storage.yaml` | 서비스 DB와 분리한 새 pgvector DB. 외부 포트 없이 내부 전용 네트워크 사용 |

`compose.runtime.yaml`은 `menu-beta-generation-20261116_tasks`,
`me-nu-checking_data`를 재사용한다. `jobs-db`, Redis, 서비스 DB, 스케줄러를
새로 만들거나 마이그레이션하는 명령은 없다. 비활성 Worker가 받는 메시지는
생성하지 않고 반환하므로 점검 전에 `content.rag_generate`가 비었는지 확인한다.

다음 값은 운영자가 준비한 명시 파일/리소스를 가리켜야 한다. 비밀값은 명령
출력에 표시하지 않는다. 환경 파일은 root 또는 배포 계정만 읽게 제한한다.

- `RAG_RELEASE_TAG`: 빌드 태그. 실행에는 태그 대신 아래 이미지 ID를 사용한다.
- `RAG_WORKER_IMAGE`, `RAG_IMPORTER_IMAGE`: `sha256:` 뒤 64자리인 검증 완료 이미지 ID.
- `CONTENT_JOB_DB_PASSWORD`: 기존 전용 작업 DB 자격증명. 서비스 DB 자격증명 아님.
- `RAG_ISOLATED_STATE_HOST_DIR`: 백업에서 만든 실행 사본 디렉터리. 원본 DB 디렉터리 사용 금지.
- `RAG_RUNTIME_CONFIG_HOST_FILE`: `enabled:false`, `confirm_isolated_copy:true`인 검토한 JSON.
- `RAG_IMAGE_HOST_DIR`: 원본 이미지와 해시를 보존한 디렉터리.
- `RAG_VECTOR_DSN_HOST_FILE`: 전용 `menu_rag_vector[_suffix]` DB의 읽기 전용 DSN 파일.
- `RAG_VECTOR_NETWORK`: 별도로 준비한 내부 Docker 네트워크. 인터넷 egress 네트워크 금지.
- `RAG_INBOX_NAME`: 기존 기간형 inbox와 다른 디렉터리 이름. 하루별 이름을 사용하고 `/`, `..`를 넣지 않는다.

RAG Worker와 controller에는 실행 사본이 `/var/lib/menu-rag`, runtime 파일이
`/run/rag/runtime.json`으로 보인다. Worker의 이미지 경로는 `/opt/rag-images`이며,
사본의 `rag_image_asset.local_path`도 실제 mount 내부 절대 경로여야 한다.
경로 재매핑은 사본에서만 수행하고 ID·이미지 바이트·해시는 유지한다.
runtime의 vector DSN 경로는 `/run/secrets/rag-vector-dsn`으로 맞춘다.
API 키는 이 구성에 mount하지 않는다. 유료 실행을 승인받기 전까지
`enabled`와 `RAG_GENERATION_ENABLED` 둘 다 false로 유지한다.

기존 artifact는 `/home/ubuntu/me-nu-beta-20261116/artifacts`에서
`/var/lib/menu-jobs`로 동일하게 연결한다. RAG inbox도 controller와 적재기에서
`/runtime/state/<RAG_INBOX_NAME>`라는 동일한 절대 경로를 사용한다.
적재기는 artifact/inbox를 읽기 전용으로 받고 `--help`로 종료한다. 실제 적재는
별도 승인 정책과 `--database-url-file`을 명시해야 하며 운영자 승인 전 공개하지 않는다.

모든 컨테이너는 UID/GID 10001로 실행한다. 실행 사본·artifact·inbox는 해당
UID가 필요한 범위만 쓰게 하고, 이미지/runtime/vector DSN은 읽을 수 있게 준비한다.
SQLite WAL 사용 시 controller가 읽을 수 있는 `-wal`/`-shm` 상태도 확인한다.
`bind.create_host_path=false`이므로 경로가 빠지면 빈 디렉터리를 만들지 않고 실패한다.

작업 DB v8은 별도 백업과 검증 후 명시적으로 적용한다. 기사 테이블 변경 없이
RAG 테이블 두 개와 버전 표식만 추가하지만, v7 이미지의 init 명령은 v8을 거부한다.
v8 적용 후 구버전 `job-init`을 재실행하거나 기존 v7 초기화 이미지로 되돌리지 않는다.
기존 기사 Worker/controller의 실행 상태는 이 구성으로 변경하지 않는다.

`compose.controller.yaml`은 기존 daily compose에 자동으로 포함되지 않는다.
기존 이미지/명령/네트워크를 보존하는 mount 전용 파일이며 그 자체로 실행 중인
controller를 교체하거나 systemd 파일 목록을 수정하지 않는다. 단, 작업 DB v8 적용은
별도 전환 작업이다. 구버전의 실패 복구 schema 검사와 충돌할 수 있으므로 진행 중인
작업이 없는지 확인하고 기존 실행을 안전하게 정지한 뒤, 같은 원장·복구 영수증을
보존하며 v8 호환 Worker/controller 이미지로 함께 전환해야 한다. 이는 RAG 비활성
연결과 구분하며, 기존 기사 실행 상태 복원은 검증한 전환 절차에서만 수행한다.
최종 이미지 안에서 무과금 도움말·파일 읽기·v8 조회·vector 읽기 전용 검색을
확인한 뒤 실제 유료 활성화는 별도 승인 단계로 남긴다.

전용 저장소는 `menu_rag_vector`이며 이미지도 검증한 SHA로 고정한다. 초기화 비밀번호는
`POSTGRES_PASSWORD_FILE`로 읽는다. 복원 뒤 생성기는 owner 대신 SELECT 권한만 가진
별도 reader 계정을 사용한다. `RAG_VECTOR_DATA_HOST_DIR`에는 기존 서비스 DB 디렉터리를
지정하지 않는다. DB 메모리는 1GiB, CPU는 0.75로 제한한다.

로컬 구성 검증: `python -m unittest discover -s deploy/rag-generation -p test_inactive_deploy.py -v`.
테스트는 가짜 환경값으로 `docker compose config`만 실행하며 컨테이너를 시작하지 않는다.
