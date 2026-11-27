# RAG 비활성 운영 배포

이 구성은 기존 기사 생성·추천·공개 콘텐츠를 변경하지 않고 RAG 실행 이미지를
준비한다. 기본 명령은 도움말이다. 혼합 스케줄의 활성 구성은 별도 검토 정책과
명시적 opt-in으로만 선택하며, 파일 준비·테스트만으로 실행 중인 서비스를 바꾸지 않는다.
저장소 루트의 `compose.rag.yaml`은 별도 로컬 실험 스택용이므로 운영에 사용하지 않는다.

| 파일 | 동작 |
| --- | --- |
| `compose.build.yaml` | Worker 기반·RAG·backend 적재기 이미지 빌드만 수행 |
| `compose.runtime.yaml` | digest로 고정한 이미지, 기존 네트워크와 파일 경로, 기본 `--help` |
| `compose.inactive.yaml` | 별도 RAG Worker를 `RAG_GENERATION_ENABLED=false` 상태로 점검 가능 |
| `compose.controller.yaml` | 기존 일일 controller에 실행 사본·runtime·별도 RAG inbox mount만 명시적으로 추가 |
| `compose.storage.yaml` | 서비스 DB와 분리한 새 pgvector DB. 외부 포트 없이 내부 전용 네트워크 사용 |
| `compose.scheduled.yaml` | schema-v2 혼합 일일 스케줄의 RAG Worker·적재기·controller 연결. RAG 기본 비활성 |
| `compose.scheduled.active.yaml` | `rag.enabled=true` 정책에서만 선택하는 기존 키·egress·RAG 실행 구성 |
| `prepare_runtime.py` | 모델·단가 파일 SHA를 검증하고 새 runtime 파일 준비. 기본 `enabled:false` |
| `model-settings-20261126.json` | 확인 날짜·공식 출처를 포함한 명시 모델·토큰 단가. 키 값 없음 |

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
기존 비활성 구성과 `compose.scheduled.yaml`에는 API 키를 mount하지 않는다. 유료 실행을 승인받기 전까지
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

혼합 일일 스케줄은 기존 기간형 `compose.runtime.yaml`과 `compose.approved.yaml`
뒤에 `rag/compose.scheduled.yaml`을 명시적으로 추가한다. schema-v2 스케줄 정책의
`rag.enabled`가 true일 때만 `rag/compose.scheduled.active.yaml`을 추가한다. 두 파일은
호스트 스케줄 journal에서 모두 해시를 고정한다. schema-v1 기사 스케줄에는 자동으로
추가하지 않으며, 현재 서버의 systemd·정책·환경 파일도 이 코드로 변경하지 않는다.

RAG 활성 구성은 기존 `/home/ubuntu/.secrets/anime-openai-key.txt`를 Worker의
`/run/secrets/rag-llm-key`로 읽기 전용 연결한다. 키를 복사하거나 값·해시를 출력하지
않는다. controller와 두 적재기에는 이 키가 없으며, 환경의 `OPENAI_API_KEY`와
`LLM_KEY`도 빈 값이다. RAG Worker는 `content.rag_generate`만 동시성 1로 처리한다.
기존 기사 Worker·적재기의 설정은 보존하고, 단일 controller가 두 원장의 시도 40회와
검수 통과 20건을 합산한다. 이미지 태그 대신 검토한 `RAG_WORKER_IMAGE`,
`RAG_IMPORTER_IMAGE` digest를 명시한다.

호스트 scheduler가 생성하는 `RAG_INBOX_NAME`은
`scheduled/YYYY-MM-DD/rag-inbox`이며 기간형 `inbox`와 다르다. RAG 적재기는
artifact·inbox·`RAG_IMPORT_POLICY_HOST_FILE`을 읽기 전용으로 받고,
`RAG_SERVICE_DSN_HOST_FILE`은 `/run/secrets/rag-service-dsn`으로 연결한다.
명령 기본값은 계속 `--help`이다. 실제 적재 명령에는 별도 정책 SHA·만료 정책·대상과
DSN 파일·`--apply`가 필요하다. 기사와 동일한 `day_policy_sha256` 및 advisory lock으로
합산 20건 제한을 유지하며, 신규 콘텐츠는 `pending_review / needs_review / checking`이다.
`feed_valid_days` 키는 정책에 반드시 포함한다. `null`은 Agent의 `draft.valid_until`을
그대로 사용한다는 뜻이며, Agent도 날짜를 지정하지 않았으면 새 콘텐츠의
`feed_valid_until`도 `null`로 보존한다. 임의로 7일 등의 기간을 넣지 않는다.
기한 없는 새 콘텐츠에는 적재기가 `feed_expiry_policy=no_deadline_v1`을 저장된 검수
메타데이터에 부여하고 운영자 승인 후에만 노출한다. 원문 번들·검수·작업 이력은 바꾸지 않는다.
과거의 모든 `null` 콘텐츠를 일괄 공개하는 설정은 아니다.

기존 정수 `1..365` 정책은 그대로 실행일 한국시간 자정에서 `N+1`일 뒤를 만료 시각으로
정한다. 이는 선택한 명시적 override이며, Agent의 명시 만료일을 넘으면 적재를 거부한다.
`null` 정책은 적재 정책에도 `feed_valid_until: null`을 포함하고 CLI의 `--feed-valid-until`을
생략한다. 유한 날짜 정책에서는 CLI 값이 정책과 정확히 같아야 한다. 빈 문자열·잘못된 날짜·
시간대 없는 날짜·이미 지난 명시 기한은 거부하며, 만료 정책을 바꾸면 새 정책 SHA 검토가 필요하다.
기존 정책·원문 파일은 덮어쓰지 않고, 지난 실행일의 적재는 기존의 명시적 날짜 복구 절차를 유지한다.

runtime 준비에 쓰는 모델은 기존 기사 설정과 같은 `gpt-6-luna`다. 2026-11-26에
확인한 [공식 모델 단가](https://developers.openai.com/api/docs/models/gpt-6-luna)는
Standard 기준 입력 $0.10·출력 $0.50·캐시 입력 $0.01 / 100만 토큰이다.
[text-embedding-3-small 단가](https://developers.openai.com/api/docs/models/text-embedding-3-small)는
입력 $0.02 / 100만 토큰이며, 명시 캐시 상품을 사용하지 않아 캐시 입력도 일반 입력과
같은 $0.02로 계산한다. 이 값은 비용 기록용 단가이고 하루 예산 상한을 추가하지 않는다.
가격 파일에 없는 모델 snapshot 응답은 기존처럼 비용 미확정으로 차단하며 임의로
기본 모델 가격을 대입하지 않는다. 키 파일의 기존 소유자·권한은 확인했지만,
이번 준비에서는 실제 provider 생성 권한이나 유료 호출 성공을 새로 확인하지 않는다.

`prepare_runtime.py`는 기존 runtime을 template로 읽고 새 파일만 만든다. 다음은
비활성 파일 준비 예시이며 Docker·스케줄·Worker·서비스 DB를 실행하지 않는다.
출력을 생략하면 파일 없이 disabled/enabled 후보 SHA만 계산한다.

`--reviewed-model-settings-sha256`는 JSON 의미가 아니라 **실제로 읽을 파일 바이트**의
SHA-256이다. `75a42e9` 배포 준비에서 확인한 두 파일은 JSON 내용과 생성되는 runtime이
같지만 줄바꿈 때문에 해시가 다르다.

| 확인한 파일 | 줄바꿈 | 파일 SHA-256 |
| --- | --- | --- |
| 로컬 worktree의 `deploy/rag-generation/model-settings-20261126.json` | LF | `d6f9e98566dcf333d48427da4dd80ea7ec1edd0c314466132bdbc2ffd4802c62` |
| `75a42e9`의 `git archive`로 만든 배포 release의 `rag/model-settings-20261126.json` | CRLF | `3fb93cc24268160fde6ad6b83c88bed4cc3e08e23f8d0dbd813cb301c6cec90f` |

배포 디렉터리에서 `sha256sum rag/model-settings-20261126.json`으로 실제 파일을 확인하고,
검토한 release manifest의 해당 파일 해시와 일치하는 값을 사용한다. 아래 예시는
`75a42e9` 배포 archive의 CRLF 해시를 사용한다. 다른 checkout이나 release에 그대로
복사하지 않는다. 값이 다르면 파일과 manifest를 다시 확인하고, 해시를 맞추려고 이미
봉인한 release 파일의 줄바꿈을 바꾸거나 검증을 생략하지 않는다.

```sh
python rag/prepare_runtime.py \
  --template /home/ubuntu/me-nu-rag-20261126/runtime.json \
  --model-settings rag/model-settings-20261126.json \
  --reviewed-model-settings-sha256 3fb93cc24268160fde6ad6b83c88bed4cc3e08e23f8d0dbd813cb301c6cec90f \
  --output /home/ubuntu/me-nu-rag-20261126/runtime-mixed-disabled.json
```

나중에 활성 runtime 파일을 준비하려면 새 출력 경로·enabled 후보 SHA를 담은
schema-v2 정책, 그 정책의 canonical SHA, `--enable`을 모두 명시해야 한다.
정책의 `rag.runtime_config`/`runtime_sha256`/`enabled=true` 중 하나라도 다르면 쓰지 않는다.
이 명령도 파일만 만들며 스케줄 실행은 별도 단계다. 이미 존재하는 runtime/준비
영수증을 덮어쓰지 않아 과거 검토 내용과 작업 이력이 유지된다.

`--check-host-files`와 세 경로 `--state-host-dir`, `--image-host-dir`,
`--vector-dsn-host-file`을 명시하면 실제 파일 존재와 안전한 경로를 검사한다.
키·DSN은 stat만 하고, Linux에서 기존 키의 uid 10001/mode 0600을 확인한다.
SQLite는 읽기 전용 연결로 `journal_mode=delete`를 확인한다. WAL 또는 남은 복구
sidecar가 있으면 자동 변경하지 않고 중단한다. controller의 읽기 전용 mount에서
Worker가 닫힌 뒤에도 근거를 읽을 수 있어야 하므로 `immutable=1`로 우회하지 않는다.

무과금 구성·준비 회귀: `python -m unittest discover -s deploy/rag-generation -p 'test_*.py' -v`.
