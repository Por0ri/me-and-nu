# 일일 콘텐츠 생성 — 실행 전 검토안 (2026-11-23)

## 현재 상태와 승인 범위

- 기준 소스: `main`의 `6ec8a47b3ffa0d77ce27f0cb8d3d30694bbeafd8`에서 분리한 `daily-generation-20261123` 작업 폴더. 최종 구현 커밋과 이미지 digest는 검증 완료 후 확정한다.
- 오늘은 코드 구현과 무과금 격리 검증만 수행한다. 이 문서는 서버 접속·배포·유료 실행 결과가 아니다.
- 실행 검토일은 **2026-11-24 KST**다. 팀이 승인한 날짜·종료 시각·예산·계획 해시가 있어야 시작한다. 다음 날 자동 시작하는 예약은 만들지 않는다.
- 목표 한도는 당일 **신규 생성 작업 최대 40건 / 검수 통과 결과 최대 20건**이다. 중복·보류·반려·실패·검수 통과를 구분해서 보고한다. 20건은 생성 보장 수량이나 공개 콘텐츠 수량이 아니다.
- 이번 범위는 기존 ledger의 **`one_piece_official:goods`**다. 원피스 공식 굿즈의 등록 출처·기존 작품 검증을 유지한다. 여러 출처·유형으로 확대하는 구현이나 승인이 아니다.
- **일일 예산은 미확정**이다. `$2`는 검토 제안이며 기본값·승인값으로 입력하지 않는다. 공급자 결제 hard cap과 코드의 추정비용 목표는 다르다.
- 내일 팀 검토 packet에 **수집 → 생성 → LLM 검수 → 서비스 검수 대기 적재(자동 공개 없음)** 범위를 함께 명시한다. 이 범위를 한 번 승인하면 단계마다 다시 승인을 요청하지 않는다. 기존 추천·세션 Redis·작품 데이터는 유지하고, 공개는 기존 운영자 승인 절차를 따른다.

## 실행 단위와 접근 범위

| 실행 단위 | 이미지·명령 역할 | 읽기/쓰기·네트워크 | API 키 |
| --- | --- | --- | --- |
| 일일 Controller | 검증한 커밋의 Agent 포함 generation 이미지에서 `menu_worker.daily_resume` | 작업 DB와 전용 generation Redis, 등록 출처 수집을 위한 외부 통신. checkpoint/state·bundle·delivery inbox에 필요한 범위만 쓰기 | 없음 |
| generation Worker | 같은 generation 이미지, `anime-generation-live` 큐, 동시 실행 1개 | 작업 DB·generation Redis·생성 artifact, 등록 출처/이미지/OpenAI에 필요한 외부 통신 | 이 컨테이너에만 raw key 파일을 `/run/secrets/anime_llm_key`로 읽기 전용 mount |
| 작업 PostgreSQL | 검증한 PostgreSQL 이미지 | 작업·attempt·기사 중복·export 이력. 서비스 DB와 별도. host port 없음 | 없음 |
| generation Redis | 검증한 Redis 이미지 | 전용 broker/result backend. 세션·fake·anime 점검 Redis와 별도. host port 없음 | 없음 |
| pending-review importer | 검증한 backend 이미지의 `app.services.pending_content_import` 모듈 | 승인된 inbox·bundle을 읽기 전용으로 읽고, 승인한 대상 서비스 DB만 접속. 이미지 바이트는 기존 carousel importer를 통해 서비스 DB에 적재 | LLM 키 없음. 서비스 `DATABASE_URL`은 이 실행 단위에만 전달 |

Controller가 사용하는 수집 함수는 `/opt/ani-venv/bin/python`과 `/opt/ani-agent`가 필요하다. Agent가 없는 `me-nu-worker` 기본 이미지로 Controller를 실행하면 안 된다. 생성 Worker와 Controller가 같은 generation 이미지를 쓰더라도 키·환경변수·mount는 별도로 구성한다.

기본 generation 이미지의 Agent 자식 프로세스는 서비스 DSN을 전달받지 않고 시도별 SQLite를 사용한다. 키는 이미지·명령행·Git·로그에 넣지 않는다. 파일 경로만 실행 설정으로 주고 값은 출력하지 않는다. Container root filesystem은 read-only, 비 root UID/GID `10001:10001`, `cap_drop: ALL`, `no-new-privileges`, 제한된 tmpfs·메모리·CPU·PID를 유지한다. Docker socket은 연결하지 않는다.

기존 `generation-egress`·`discovery-egress`는 외부 통신용 bridge이며 도메인별 방화벽 allowlist가 아니다. 출처 URL은 애플리케이션에서 검사한다. 비활성 상태에서는 이 외부 네트워크를 연결하지 않고, 실제 실행 시 부여할 통신 범위를 승인 구성에서 확인한다.

## 기존 Compose를 그대로 켜면 안 되는 이유

- `compose.discovery.yaml`은 `ANIME_DISCOVERY_ROUTES`를 생략하면 `one_piece_official:goods` 정기 수집을 설정한다. 비활성 준비에서 이 overlay를 무조건 `up`하면 안 된다.
- `compose.generation.yaml`의 일반 `generation-dispatcher`는 `dispatch --loop`를 실행하며 기존 대기 작업도 발행한다. 일일 Controller의 40건·20건·예산 한도를 우회할 수 있으므로 **함께 실행하지 않는다**.
- 한정 실행에서는 Controller가 유일한 생성 메시지 발행자여야 한다. 일반 discovery Worker/Beat schedule, 수동 `generate`, 일반 Dispatcher, 다른 일일 Controller를 동시에 사용하지 않는다.
- `generation-worker`만 시작해도 Redis에 이미 남은 live 메시지를 소비할 수 있다. 시작 전 DB·Redis·checkpoint의 소유 관계와 미완료 메시지를 대조한다. 다른 실행의 메시지는 삭제하거나 재발행해서 정리하지 않는다.
- 원래 Compose의 `restart: unless-stopped`를 일일 Controller에 그대로 적용하지 않는다. Controller는 명시적 1회 실행이며, 중단 후 자동으로 새 날짜·새 예산을 부여하지 않는다.

이 제한은 새 코드만으로 다른 발행자를 막았다는 주장이 아니다. 실행 구성과 운영 점검으로 단일 발행자 조건을 함께 충족해야 한다.

구체적인 파일은 `deploy/daily-generation/compose.build.yaml`(이미지 빌드 전용), `compose.runtime.yaml`(세 컨테이너 모두 `--help`·키 없음·외부 egress 없음), `compose.approved.yaml`(승인 후 활성화 overlay), `runtime.env.example`(민감값·예산·해시가 비어 있는 템플릿)로 분리했다. 기본 runtime은 기존 작업 네트워크·서비스 data 네트워크와 원래 artifact/state 경로를 재사용하며 DB 생성·migration·API 컨테이너 교체 명령을 포함하지 않는다. 승인 overlay는 모든 승인값을 명시해야 구성할 수 있고, 생성 Worker 한 곳에만 raw key mount를 추가한다.

승인 overlay는 importer 명령도 `--apply`로 바꾼다. 팀이 전체 packet을 승인하면 세 실행 단위를 승인 범위대로 시작한다. 만약 승인 범위를 수집·생성으로 제한했다면 전체 `manual` 프로필을 무조건 올리지 말고 `generation-worker`·`daily-controller`만 서비스 이름으로 지정한다.

## 비활성 배포의 완료 조건

실제 배포는 아직 승인·실행하지 않았다. 내일 승인 전에 다음 상태를 검토할 수 있게 준비한다.

1. 검증한 커밋으로 runtime/generation/backend 도구 이미지를 빌드하고 immutable tag와 image digest를 기록한다. `:local` 같은 가변 태그만 기록하지 않는다.
2. 소스 bind mount 없이 이미지 내부의 CLI·카탈로그·Controller·Agent 코드를 확인한다. 키·네트워크 없는 이미지 점검을 통과한다.
3. 작업 DB는 기존 이력과 volume을 유지한다. 서비스 DB와 worker task DB의 DSN을 혼용하지 않는다. 현재 schema 버전과 필요한 migration 여부를 읽기 전용으로 확인한 뒤에만 별도 변경한다.
4. 준비한 컨테이너는 `created` 또는 종료 상태다. 키 mount 0, 외부 통신 없음, host port 0, 실행 명령은 `--help` 수준이어야 한다. live Worker/Controller는 시작하지 않는다.
5. 기존 checkpoint·그 계보·accounting reservation·결과 manifest·표지 이력·bundle 해시를 보존한다. 이미 처리한 기사 이력을 지우거나 새 DB로 옮겨 중복 방지를 초기화하지 않는다.
6. API/추천 컨테이너의 이미지·시작 시각·재시작 횟수와, 작업 이력·서비스 공개 상태의 비교 근거를 남긴다. 확인을 위해 추천 배치나 서비스 import를 실행하지 않는다.

## 내일 실행 전 반드시 확정할 값

| 항목 | 확정 기준 |
| --- | --- |
| 실행 날짜 | 승인된 KST 날짜. 검토 중인 날짜는 `2026-11-24` |
| 종료 시각 | 날짜와 `+09:00`을 포함한 ISO 8601 절대 시각. 자동으로 다음 날로 연장하지 않음 |
| 일일 예산 | 팀이 승인한 양수 금액. 현재 미확정 |
| 출처·유형·작품 | 기존 ledger가 고정한 등록 출처·유형 범위와 현재 카탈로그의 작품 ID 일치 |
| 부모 checkpoint | 파일 절대 경로와 실제 SHA-256. 완결된 기존 이력 또는 안전 종료한 이전 일일 이력 |
| 새 checkpoint | 부모와 동일한 보호된 state 디렉터리 안의 신규 파일. 기존 파일 덮어쓰기 금지 |
| delivery inbox | 사전에 만든 전용 디렉터리. bundle 경로와 별도이며 importer가 읽을 승인 envelope만 둠 |
| 실행 이미지 | 무과금 회귀를 통과한 최종 커밋·digest |
| 계획 승인 | plan의 `plan_sha256`과 `day_policy_sha256`, 한도·날짜·예산·기존 비용 요약 검토 |
| 키 | 승인된 기존 키의 private 파일. 키 값은 출력·메시지·문서에 기록하지 않음 |

일일 예산은 **당일 신규 비용 목표**다. 누적 목표는 보존한 과거 계측 비용·미계측 예약에 당일 예산을 더해 계산한다. 과거 미계측 예약을 삭제하거나 당일 사용량으로 위장하지 않는다. 요약에는 당일 비용과 누적 계측/예약 비용을 각각 표시한다.

다음 작업 예약액은 확정 단가가 아니다. 실제 한 작업이 예상보다 비싸면 완료 후 기록되는 비용이 목표를 넘을 수 있으므로, 공급자 측 hard cap이 확인되지 않은 상태에서 코드가 달러 단위 결제를 확실히 차단한다고 설명하지 않는다. 비용 증거 누락·불일치는 0원으로 간주하지 않고 중단한다.

## 계획·실행 인터페이스

아래는 구현된 CLI 계약이다. 이 문서의 placeholder는 실제 실행값이 아니다.

```text
python -B -m menu_worker.daily_resume
  --source-checkpoint <부모 checkpoint 절대 경로>
  --source-sha256 <부모 파일 SHA-256>
  --checkpoint <새 checkpoint 절대 경로>
  --policy-date <승인한 KST 날짜>
  --daily-budget-usd <승인한 당일 예산>
  --deadline <승인한 시간대 포함 종료 시각>
  --delivery-root <기존 전용 inbox 절대 경로>
```

기본은 plan이며 DB·Redis·수집·LLM에 접속하지 않는다. 출력의 비용·날짜·한도·경로와 계획 해시를 검토한 뒤, 동일 인자에 `--apply --expected-plan-sha256 <검토한 정확한 해시>`를 추가하는 방식이다. 계획 파일을 바꾸면 이전 승인을 재사용하지 않는다.

Worker를 켜기 전에는 동일 인자와 계획 해시로 **`--preflight`**를 실행한다(`--apply`와 상호 배타). 이 모드는 승인된 날짜·시간 안에서 기존 작업 DB·봉인 산출물·비용 계보를 읽고, Redis의 live queue priority 0/3/6/9 및 `unacked`·`unacked_index`가 비어 있는지 확인한다. 자식 checkpoint·successor claim·메시지를 만들지 않는다. 잠금 파일은 생길 수 있다. 빈 broker 검사는 일반 발행자와 기존 Worker가 정지된 상태에서 실시하고, 확인과 활성화 사이에 다른 발행자를 켜지 않는다.

40건은 신규 job 한도이며 worker 내부의 시작 전 재시도와 혼동하지 않는다. 같은 날짜에 실패했다고 새 checkpoint나 새 run ID로 한도를 초기화하지 않는다. 같은 실행은 같은 자식 checkpoint·같은 계획으로만 재시작한다. 다음 날짜 실행은 부모/자식 계보와 과거 비용을 보존하고 다시 명시적으로 승인한다.

Controller는 직접 기존 Agent 수집 함수를 호출한다. 별도의 고정 후보 목록을 승인했다는 의미는 아니다. 각 수집 결과와 실제 접수·중복·제외 결과를 checkpoint에서 확인한다. 기존에 끝난 기사는 중복으로 기록하고, 미완료·비용 미확정 작업을 발견하면 새 생성으로 덮지 않는다.

## 검수 통과 결과의 서비스 DB 접점

`ready_for_staging`은 Agent 검수와 봉인 결과 검증을 통과했다는 상태다. 서비스 공개 승인과 동일하지 않다. Controller는 export된 번들에 대해 `delivery_root/{job_uuid}.json` envelope를 만들며, `bundle_path`는 bundle root 아래의 `job_uuid/bundle.json` 상대 경로로 제한한다. envelope에 일일 정책 해시를 포함한다.

별도 backend importer의 계약은 다음과 같다.

```text
python -B -m app.services.pending_content_import
  --policy <승인 정책 JSON>
  --reviewed-policy-sha256 <정확한 정책 SHA-256>
  (--inbox <전용 inbox 경로> | --manifest <명시한 envelope 목록 파일>)
  [--once | --watch]
  [--apply]
```

기본은 `dry-run / once`다. 정책은 run·날짜·출처·유형·최대 건수·bundle root·대상 DB(host/port/database/username/schema/environment)·`day_policy_sha256`를 묶는다. 생성 Controller의 정책 해시와 envelope의 해시도 일치해야 한다. 이 importer 정책을 생성 계획과 같은 팀 검토 packet에 포함해 한 번에 확정한다.

`--watch`는 inbox 모드에만 사용한다. 승인 당일 안에서 반복 확인하며 최대 실행 시간도 제한한다. 다음 날 예산·생성·적재 권한이 자동으로 생기지 않는다. 로컬 저장소 wrapper는 `backend/scripts/import_pending_contents.py`이며 이미지에서는 위 모듈 진입점을 사용한다.

승인한 packet에 따라 적재하는 신규 상태는 `Content.pending_review / judgment.needs_review / Draft.checking`이며 피드에 노출되지 않는다. preview 또는 operator approval을 흉내 낸 입력은 받지 않는다. 중복 적재는 기존 공개·반려 상태나 원래 검수 증거를 되돌리지 않는다. 공개 승인·반려 및 그에 따른 추천 노출은 기존 관리 절차다.

## 중단·재개·되돌리기

- **정상 종료:** 신규 40건, 검수 통과 20건, 예산 또는 종료 시각 중 먼저 도달한 조건에서 새 작업을 그만 넣는다. 성공 20건 미달도 안전한 정상 종료 사유와 함께 보고할 수 있다.
- **수동 중단:** Controller의 신규 발행부터 중지한다. 이미 실행 중인 Worker는 가능하면 결과·사용량을 기록하고 종료하도록 둔다. 이미 공급자에 보낸 요청의 결제 취소나 즉시 중단을 보장하지 않는다.
- **불명확한 실패:** `needs_attention`, 비용 누락/불일치, 결과 해시 불일치, 메시지 발행 여부 불명확, 미완료 외부 작업은 자동 재생성하지 않는다. 원래 job·attempt·result·checkpoint를 보존하고 원인을 확인한다.
- **재개:** 동일 계획과 checkpoint 계보, 파일 잠금·DB 잠금, 원래 작업/결과/비용 증거를 다시 확인한다. 같은 날 한도 초기화, 다른 경로로 재발행, 생성 시작 marker 제거는 금지한다.
- **되돌리기:** Controller/Worker/importer를 비활성화하고 코드·이미지만 마지막 검증 버전으로 되돌린다. job DB·Redis·artifact/state/bundle/inbox volume을 삭제하지 않는다. ledger를 과거 사본으로 덮어 예산·횟수를 줄이지 않는다. 이전 이미지가 새 checkpoint를 해석할 수 있는지 확인하기 전에는 재개하지 않는다.
- **서비스 상태:** 이미 승인된 적재/공개가 있다면 코드 rollback만으로 콘텐츠를 삭제하거나 상태를 되돌리지 않는다. 해당 변경은 별도 검수 기록과 승인 절차를 따른다.

## 리뷰 완료에 필요한 증거

- 40건·20건·당일 예산·KST 경계·마감 직전 새 작업 금지·중복·동시 실행·중단/재개·이전 날짜 되감기 차단 테스트
- 기존 ledger/예약/attempt/result 보존과 API 호출·서비스 DB 접근 0인 기본 plan 검증
- 실제 격리 PostgreSQL·Redis·Celery에서 가짜 생성기로 동일 정책 확인. 오늘 실제 Agent 유료 생성은 실행하지 않음
- 검수 통과 envelope만 전달, 변조/잘못된 정책·DB·경로 거절, importer dry-run 무쓰기, 중복 적재와 승인 전 비노출 확인
- 기존 추천 피드·승인/반려 회귀, 기존 실행 환경의 이미지·시작 시각·재시작 횟수 보존
- 최종 커밋·이미지 digest·테스트 결과·plan hash·중단 사유·비용 요약을 구분해서 기록

이 목록 자체가 검증 완료 주장이나 팀 승인 대체물은 아니다. 아래 실행 기록과 최종 테스트 로그에서 실제 확인한 범위를 구분한다.

## 11월 23일 실제 격리 연결 검증

- `worker/tests/check_daily_delivery.py`는 전용 `jobs-db/menu_content_jobs_test`, `generation-redis`, Celery solo Worker에서 실행했다. 명시 opt-in·서비스 DSN/모델 키 부재·빈 public schema 확인 후에만 fixture를 만든다.
- 과거 작업 4건의 job·attempt·봉인 파일·부모 checkpoint를 보존하면서, 중복 후보를 포함한 수집 결과에서 신규 작업 **1건**만 처리했다. 실제 claim→시작 marker→가짜 생성→실제 봉인 검증→실제 export→delivery envelope까지 연결했다.
- 동일 작업의 재전달은 `ignored`, 다른 요청 키의 동일 기사는 `candidate_exists`로 중복 처리됐다. Worker의 가짜 생성 호출은 1회이며 정지 후 재확인도 envelope를 바꾸거나 작업을 재발행하지 않았다.
- production 한도 40/20을 줄이지 않았다. 1건 성공 뒤 가짜 시계를 마감으로 이동시켜 `deadline_reached` 종료를 검증했다. 합성 장부의 일일 비용 `$0.01`·테스트 예산 `$0.50`은 실제 비용이나 승인값이 아니다. **실제 LLM 호출 0회·실제 비용 $0·서비스 DB 접근 0회**다.
- 합성 검수 protocol을 담은 결과는 `/handoff/OFFLINE_TEST_ONLY.json`과 카드·상세 본문의 테스트 표기로 구분했다. 정상 검증기를 시험하기 위해 `review_mode=llm` 모양의 fixture를 사용한 것이며 실제 LLM 검수에 통과한 콘텐츠가 아니다.
- 이 실제 export 산출물을 별도 backend 격리 DB의 외부 handoff 회귀에 연결한 결과 **1 passed, 6 deselected**를 확인했다. pending 적재·동일 재적재·비노출 검증이며 실제 서비스 적재가 아니다. 전체 backend 관련 회귀는 별도 로그에서 27건 통과했다.
- root가 build/base/approved Compose 구문, 비활성 명령, 키 전달 경계, host port 0, 예산 누락 시 구성 거절을 검사했다. 실제 서버 접속·배포는 하지 않았다.
- 새 broker preflight를 실제 격리 Redis에서도 읽기 전용으로 실행해 priority 0/3/6/9·unacked·unacked_index가 모두 0임을 확인했다. 이 확인의 queue writes·DB 접근·LLM 호출은 0이다. 로그: `broker-readonly-preflight.log`.

근거 로그는 로컬 `out/daily-generation-verification-20261123/worker-delivery-retry.log`, `backend-handoff.log`, 공유 handoff의 `verification-summary.json`에 보존한다.

작은 테스트 오류도 남긴다. 최초 harness가 schema 초기화 전에 fixture marker를 만들자 `JobStore.initialize()`가 비어 있지 않은 미버전 DB를 정상 거절했다. 빈 DB 검사를 유지하고 marker 생성 순서를 초기화 뒤로 옮겼다. 실제 DB 이름·public table 한 개·정확한 fixture ID와 용도를 확인한 후 그 marker만 제거하여 재실행했다. 재실행 명령에서 opt-in을 빠뜨린 1회도 시작 전 거절됐으며, 명시 플래그를 추가한 실행은 통과했다. production DB 가드를 완화하거나 서비스 데이터를 변경하지 않았다.

## 최종 검증과 준비 산출물

| 검증 | 결과 |
| --- | --- |
| 기존 피드·추천·적재·승인 회귀 | 224 passed, 4 subtests passed |
| 새 일일 정책 | 18 passed |
| 기존 bounded 실행 호환 | 41 passed |
| 실제 작업 PostgreSQL fork·되감기·이력 보존 | 3 passed |
| 신규 pending importer | 27 passed |
| 실제 Celery export → 임시 서비스 DB 적재 | 1 passed |
| 최종 이미지의 기존 표지 이력 회귀 | 10 passed |

합계 **324개 테스트 통과**다. 별도로 최종 generation 이미지 내부의 코드만 사용해 새 격리 PostgreSQL·Redis·Celery 통합을 다시 통과했다. 외부 소스 bind mount는 없었다. 과거 job/attempt 4건 보존, 신규 job/attempt 1건, article selection 1건, envelope 1건, 카드 2장, 중복 후보·중복 전달 차단 및 실제 broker preflight 6개 항목 0을 확인했다. `packaged-delivery.log`와 `image-handoff/verification-summary.json`이 근거다.

로컬 빌드 이미지 식별자(서버 배포 결과가 아님):

| 이미지 | 로컬 image ID |
| --- | --- |
| `me-nu-worker:daily-review-20261123` | `sha256:721e9ee60c6da573b7bfe96228688f5e5ae40bc7450ddd6d62ebcb90f3f3bd0b` |
| `me-nu-anime-generation:daily-review-20261123` | `sha256:549b29ce0c43f59410fedc9d4eaf5525dad7be93e7e5dc561a6ade96b2e68f6f` |
| `me-nu-pending-importer:daily-review-20261123` | `sha256:3884f534f2cfb520ce9720c265f9f1ffbc597fa0c09609240550b43d7e2a33f1` |

적재기 이미지의 `python -m app.services.pending_content_import --help`도 네트워크 없이 UID 10001로 통과했다. 최초 도움말 점검은 공통 Settings의 필수 `DATABASE_URL`이 없어 거절됐다. 연결되지 않는 점검용 주소와 `network_mode: none`을 빌드 점검 구성에 명시했다. 서비스 설정이나 공통 Settings 코드를 바꾸지는 않았다.

배포 준비 파일은 `deploy/daily-generation/`에 있다. `compose.build.yaml`은 빌드용, `compose.runtime.yaml`은 전부 `--help`·키 없음·외부 통신 없음인 기본 구성, `compose.approved.yaml`은 승인한 값이 전부 있어야 해석되는 활성화 구성이다. 포트 노출은 없다. API·추천 컨테이너 교체나 DB 생성·마이그레이션을 포함하지 않는다.

독립 리뷰에서 발견한 기존 표지 bootstrap 연결 누락도 보완했다. 승인 구성에는 `ANIME_COVER_HISTORY_BOOTSTRAP_HOST_FILE`과 `ANIME_COVER_HISTORY_BOOTSTRAP_SHA256`이 반드시 필요하며 Worker만 읽기 전용으로 받는다. 이전 점검에서 보존했던 digest는 `cdceca43ad5d661ebc3c4928d01c79918d0d3ebd73697dec408819c2ecdb23a6`다. 실제 실행 전 기존 컨테이너의 mount 경로와 원본 파일 digest를 다시 대조한다. 이 값을 생략하거나 기존 표지 이력을 새로 비우지 않는다.

`DAILY_INBOX_NAME` 하나로 Controller와 importer의 동일 호스트 디렉터리를 계산하므로 두 경로를 따로 설정하지 않는다. `runtime.env.example`의 예산·날짜·계획 해시는 비어 있다. `import-policy.example.json`의 날짜와 `day_policy_sha256`도 실제 승인값으로 대체해야 한다. importer 정책의 최대 20건은 검수 통과 결과 수이며 생성 시도 40건과 별도다. 정책 해시는 JSON을 `sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False`로 직렬화한 UTF-8 바이트의 SHA-256이다.

내일은 검토한 커밋·이미지를 서버에 반입하고, 기존 state·작업 DB·서비스 DB 백업 및 비교값을 확보한 다음 승인된 날짜·예산·종료 시각으로 plan을 확정한다. 원피스 공식 굿즈 수집→생성→LLM 검수→비공개 검수 대기 적재 범위를 한 번에 검토한다. Worker가 꺼진 상태에서 `--preflight`를 통과한 뒤 승인 구성의 Worker/importer/Controller를 명시적으로 시작한다. 일반 Beat·discovery·Dispatcher는 계속 꺼둔다.

종료 시각은 신규 작업의 접수·발행 차단 기준이다. 이미 실행 중인 요청은 사용량을 기록하고 마칠 시간을 주며 즉시 결제 중단을 보장하지 않는다. 다음 날 자동 시작하는 cron이나 예산 갱신은 없다. 오늘은 **추가 LLM 0회, 운영 서비스 DB 쓰기 0회, 실제 배포·활성화 0회**다.
