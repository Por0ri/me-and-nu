# 관리자 신고 처리·문의 답변 계약

2026-11-23. 관리자 `/admin/reports`에서 신고 검토 시작·해결·반려와 문의 답변 등록을 수행하고, 문의한 회원은 MY PAGE → 문의 및 신고 → **문의 내역 · 답변**에서 등록된 답변을 읽는다.

이 문서는 [기존 회원·신고·문의 조회 계약](ADMIN_MEMBER_SUPPORT_READ_2026-11-23.md)의 **문의 답변 미저장, 관리자 쓰기 비활성, 검토 시작의 의미**를 대체한다. 기존 목록 검색·페이지·기간·개인정보 마스킹 계약은 유지한다.

## 처리 범위

| 대상 | 동작 | 허용 상태 → 결과 |
| --- | --- | --- |
| 콘텐츠 신고 | `start_review` | `received` → `reviewing` |
| 콘텐츠 신고 | `resolve` | `received` 또는 `reviewing` → `resolved` |
| 콘텐츠 신고 | `reject` | `received` 또는 `reviewing` → `rejected` |
| 문의 | `answer` | `received` → `answered` |

완료된 신고의 재개와 등록한 답변의 수정·삭제는 제공하지 않는다. 기존 문의의 `closed` 상태도 이번 API로 변경하지 않는다. 조회 화면은 `resolved`, `rejected`, `answered`, `closed`를 완료 그룹으로 묶되 원본 상태를 별도로 보여준다.

**신고 해결·반려는 신고 건의 처리 상태만 변경한다.** 연결된 콘텐츠의 공개·숨김·검수 상태, Draft, 적재기 판정을 변경하지 않는다. 콘텐츠 수정 또는 공개 상태 변경은 기존 콘텐츠 관리·검수 흐름에서 별도로 수행한다. 콘텐츠 상세를 읽는 기존 버튼은 읽기 전용으로 유지되며, 이번 `검토 시작`은 사유를 확인한 뒤 신고 상태를 실제로 변경하는 별도 동작이다.

**문의 답변 등록은 서비스 내부 게시다.** 이메일·SMS·푸시 알림이나 외부 메시지를 발송하지 않는다. 관리자 확인 문구도 문의한 회원의 문의 내역에 표시되는 동작으로 안내한다.

## 권한과 요청 경계

| 역할 | 회원·신고·문의 조회 | 신고 처리·문의 답변 |
| --- | --- | --- |
| `viewer` | 허용 | 거부 |
| `operator` | 허용 | 허용 |
| `admin` | 허용 | 허용 |
| 일반 회원·권한 회수·비활성 계정 | 관리자 조회 거부 | 거부 |

관리자 GET은 기존 실제 세션과 지원 조회 capability를 확인한다. POST는 같은 실제 세션의 CSRF 토큰을 검증하고, 별도 쓰기 트랜잭션에서 현재 사용자·관리자 권한 행을 잠근 뒤 `operator` 또는 `admin` 권한을 다시 확인한다. `withdrawn_at`이 있는 관리자도 쓰기 권한을 얻지 못한다. 클라이언트 역할값, 개발 인증 우회, 이전 세션에 저장된 역할은 권한을 부여하지 않는다.

두 POST는 다음 헤더를 요구한다.

```http
Content-Type: application/json
X-CSRF-Token: <현재 실제 세션의 CSRF 토큰>
X-Admin-Support-Actor: <상세 GET actions.scope.actorUserId>
X-Admin-Support-Grant: <상세 GET actions.scope.adminAccountId>
```

Actor·Grant는 양의 PostgreSQL bigint를 나타내는 십진 문자열이다. 중복 헤더, 0, 범위 초과는 거부한다. 이 두 값은 **최초 처리 의도를 계정·권한에 묶는 값**이며 인증·권한을 대신하지 않는다. FE는 재확인할 때마다 세션과 상세를 새로 읽되 최초 요청의 Actor·Grant를 새 계정·권한으로 바꾸지 않는다. 상세가 이미 완료 상태여도 현재 권한의 scope는 제공하므로 동일 요청의 처리 결과를 확인할 수 있다.

## 관리자 API

모든 경로의 공통 접두사는 `/api/v1`이다. 성공 응답은 HTTP **200**이며, 응답은 `Cache-Control: private, no-store`를 따른다.

### 신고 처리

`POST /admin/reports/{reportId}/actions`

```json
{
  "requestId": "6f2b9188-91b1-4b82-9f2c-b37547c01411",
  "action": "resolve",
  "rationale": "원문과 정정 내용을 확인했습니다.",
  "expectedStatus": "reviewing",
  "expectedUpdatedAt": "2026-11-23T04:00:00.123456Z"
}
```

`rationale`는 앞뒤 공백을 제거한 1~2,000자이며 필수다. `expectedStatus`는 `received|reviewing`만 받는다. `start_review`는 `received`에서만 허용한다.

### 문의 답변 등록

`POST /admin/inquiries/{inquiryId}/answer`

```json
{
  "requestId": "c8502d42-c10d-4c8d-af23-31bdd941076c",
  "answer": "문의하신 내용을 확인했습니다. 서비스에서 다시 확인해 주세요.",
  "expectedStatus": "received",
  "expectedUpdatedAt": "2026-11-23T04:00:00.123456Z"
}
```

`answer`는 앞뒤 공백을 제거한 1~5,000자이며 필수다. 문의 답변 요청에는 `action`이나 내부 처리 메모를 넣지 않는다. 입력은 일반 텍스트이며 화면에서 HTML로 실행하지 않는다. 허용하지 않는 제어 문자와 surrogate 문자는 거부한다.

두 요청은 UUID 형식의 `requestId`와 timezone offset이 있는 ISO timestamp를 사용한다. 소수점은 최대 6자리이며 `-00:00` 같은 불명확한 offset은 받지 않는다. FE는 GET의 `updatedAt` 문자열을 JavaScript `Date`로 재직렬화하지 않고 그대로 전달하여 마이크로초를 보존한다. 불필요한 필드를 추가하면 422로 거부한다.

### 성공 응답

```json
{
  "recordId": "1",
  "recordType": "inquiry",
  "action": "answer",
  "rawStatus": "answered",
  "updatedAt": "2026-11-23T04:01:00.654321Z",
  "auditLogId": "21",
  "replayed": false
}
```

`recordType`은 `report|inquiry`, `action`은 위 네 동작 중 하나다. 식별자는 문자열로 반환한다. 답변 본문은 성공 응답에 복사하지 않고 상세 GET에서 읽는다. `replayed: true`이면 최초로 커밋된 조치의 응답을 다시 반환한 것이며 새로운 처리·답변·감사 기록을 만들지 않는다.

### 상세 GET 추가 필드

기존 `GET /admin/reports/{id}`, `GET /admin/inquiries/{id}`에 다음 정보가 추가된다.

```json
{
  "actions": {
    "allowedActions": ["answer"],
    "disabledReason": null,
    "scope": {"actorUserId": "7", "adminAccountId": "107"}
  },
  "answer": null,
  "answeredAt": null,
  "answerAvailability": "none"
}
```

- `permission_denied`: 쓰기 권한이 없어 `allowedActions: []`, `scope: null`이다.
- `terminal_state`: 현재 쓰기 권한은 있지만 이미 완료되어 새 조치를 허용하지 않는다. 현재 scope는 유지한다.
- `answerAvailability: available`: 저장된 답변과 `answeredAt`을 반환한다.
- `none`: 아직 답변이 없는 접수 문의다.
- `unavailable`: 과거 완료 상태이지만 답변 본문이 저장되지 않은 경우 또는 답변 대상이 아닌 콘텐츠 신고다. 기존 `answered` 상태만 보고 과거 답변을 생성하지 않는다.

`actions`는 화면 안내이며 POST는 잠금 안에서 권한과 상태를 다시 검증한다.

## 충돌·재시도와 감사

| HTTP·코드 | 의미와 FE 처리 |
| --- | --- |
| 401 `AUTH_REQUIRED` | 실제 세션이 없거나 유효하지 않음. 로그인 상태를 다시 확인한다. |
| 403 `CSRF_INVALID` | 세션 CSRF 또는 Origin 검증 실패. 새 CSRF를 얻어 동일 요청으로 수동 재확인한다. |
| 403 `ADMIN_WRITE_REQUIRED` | 현재 신고·문의 쓰기 권한 없음. 이전 상세·쓰기 권한을 다시 읽는다. |
| 404 `ADMIN_REPORT_NOT_FOUND` / `ADMIN_INQUIRY_NOT_FOUND` | 대상이 없거나 조회·처리할 수 없음. 탈퇴한 회원의 문의도 처리하지 않는다. |
| 409 `SUPPORT_STATE_CONFLICT` | 최신 상태·갱신 시각이 달라졌거나 상태 전이가 불가함. 상세·목록을 다시 읽고 새 판단을 받는다. |
| 409 `SUPPORT_REQUEST_CONFLICT` | 같은 범위의 UUID에 다른 정규화 요청 내용이 사용됨. 기존 요청을 변형해 다시 보내지 않는다. |
| 409 `SUPPORT_SCOPE_CHANGED` | 최초 계정 또는 관리자 권한 ID와 다름. 기존 UUID를 새 계정에 재귀속하지 않는다. |
| 503 `ADMIN_PERMISSION_UNAVAILABLE` | 현재 권한 저장소를 확인할 수 없음. 허용으로 대체하지 않는다. |
| 503 `ADMIN_SUPPORT_WRITE_UNAVAILABLE`, 기타 5xx·timeout·응답 유실 | 커밋 여부가 불확실할 수 있다. 실패로 단정하지 않고 **동일 UUID·동일 내용·동일 scope**로 재확인한다. |

멱등 범위는 **현재 인증 actor + 같은 관리자 grant + 같은 대상 종류·ID + 같은 requestId**다. 요청 내용은 정규화한 JSON의 SHA-256 digest로 비교하며 원본 요청 본문을 감사 기록에 남기지 않는다. 같은 UUID를 다른 대상으로 보내는 것은 별개 범위다. 권한이 회수되거나 계정이 변경된 경우에는 기존 성공 결과의 재조회도 권한 검사를 우회하지 않는다.

멱등 응답은 해당 감사 기록이 보존되고 대상에 접근할 수 있는 동안 제공한다. **정해진 일수의 보존 기간을 새로 보장하지 않는다.** 답변 또는 문의가 삭제되거나 grant가 달라지면 같은 요청의 재전송만으로 최초 성공을 확인할 수 없을 수 있다. 감사 기록 정리 정책을 도입할 때는 재시도 보장 기간과 함께 변경해야 한다.

FE는 불확실한 요청을 메모리에 보존하고 최대 3회 수동 재확인을 제공한다. 목록 필터 변경이나 상세 재선택으로 UUID를 새로 만들지 않으며 자동 백그라운드 재전송도 하지 않는다. 미확인 요청이 있을 때 브라우저를 떠나면 확인 안내를 표시한다. 재확인 중 권한 오류가 발생해도 최초 요청의 저장 실패를 단정하지 않는다.

쓰기 트랜잭션은 사용자·현재 grant·대상 행 잠금, 상태/`updatedAt` 비교, 상태 변경, 답변 삽입, 감사 삽입, 커밋을 한 단위로 처리한다. 감사 삽입이 실패하면 답변과 상태도 롤백한다. 중복·동시 요청은 같은 답변 또는 조치를 한 번만 커밋한다. DB deadlock·serialization 충돌은 409로 반환한다.

감사 기록에는 수행자 grant, 동작, 대상, 이전 상태·시각, 요청 UUID·digest, 결과 상태·시각이 남는다. 신고는 관리자가 작성한 처리 사유를 기록한다. 문의는 고정 사유 `문의 답변 등록`을 사용하고 **문의 원문·답변 본문·문의자 신원을 감사 before/after 값에 복사하지 않는다**.

## 회원 본인의 문의 조회

`GET /inquiries?page=1&pageSize=10`과 `GET /inquiries/{inquiryId}`를 추가한다. 실제 활성 세션·활성 계정·온보딩 완료가 필요하며 개발 인증 우회로 개인 문의 내역을 읽을 수 없다. 조회에는 CSRF 제출이 필요하지 않다.

목록은 본인의 문의만 `{items,page,pageSize,total}`로 반환한다. 페이지 크기는 최대 50, 페이지 번호는 최대 100,000이다. 항목에는 문자열 `inquiryId`, `inquiryType`, `status`, `createdAt`, `updatedAt`, 문의 첫 100자의 `preview`, `answerAvailability`가 있다. 상세는 `detail`, `answer`, `answeredAt`을 추가하며 관리자 ID·grant·처리 사유·감사 정보를 제공하지 않는다. 다른 회원의 ID와 존재하지 않는 ID는 동일한 404 `INQUIRY_NOT_FOUND`로 처리한다.

FE는 `X-Inquiry-Actor-User-Id`에 화면을 연 회원의 ID를 보내 계정 전환 경합을 막는다. 이 선택 헤더가 현재 세션 사용자와 다르거나 중복되면 403 `INQUIRY_SESSION_CHANGED`를 반환한다. 헤더를 생략해도 소유권 검증은 현재 실제 세션을 기준으로 수행하며, 헤더 자체로 다른 회원에게 접근할 수 없다. FE는 401·계정 변경 시 이전 목록·상세를 지우고, 화면 복귀 시 다시 확인한다.

탈퇴 상태 또는 `withdrawn_at`이 있는 회원의 문의는 관리자와 회원 조회에서 제외하고 답변 등록도 거부한다. 실제 문의 삭제 시 `inquiry_answer.inquiry_id`의 `ON DELETE CASCADE`로 답변 본문을 함께 삭제한다. 감사 기록에는 원문·답변이 없어 문의 삭제 후 답변 본문이 감사 테이블에 남지 않는다.

## Migration과 적용 순서

- 새 서비스 revision: **`83a2f9c7d104`**, 부모 revision: `47c1a8d6b302`.
- `inquiry_answer`의 PK·FK는 `inquiry_id`, 추가 필드는 `body`, `admin_account_id`, `answered_at`이다. 문의당 답변 하나와 1~5,000자 제한을 DB에서도 적용한다.
- 새 BE를 활성화하기 전에 지정한 환경 DB에 Migration을 적용한다. 이전 스키마에서는 답변 조회·저장을 사용할 수 없으며 DB 실패를 성공·빈 답변으로 변환하지 않는다.
- `SERVICE_SCHEMA_REVISION`과 `compose.labs.yaml`, `compose.checking.yaml`, `compose.deploy.yaml`의 명시적 적용 대상을 새 revision으로 갱신한다. 보류된 생년 수집 관련 Migration은 포함하지 않는다.
- Checking의 독립 배포 검증기와 bootstrap도 `83a2f9c7d104`를 요구한다. 서비스 revision과 두 검증기의 일치 여부를 회귀 테스트로 확인한다.
- 답변이 존재하면 downgrade를 거부한다. 장애 시 답변을 지우는 downgrade 대신 추가 테이블을 유지한 채 이전 앱으로 되돌리는 방식을 검토한다.
- 이번 개발·로컬 검증은 실제 Checking DB 변경·배포나 실제 회원에 대한 답변 게시를 의미하지 않는다.

## 검증과 재현

아래 명령은 `backend`에서 프로젝트 Python 환경으로 실행한다. `ADMIN_POSTGRES_TEST_URL`에는 **새로 만든 loopback 전용 테스트 DB**만 지정한다. 기존 `admin_pg` fixture는 `admin_be_test` 또는 `admin_be_test_<영소문자·숫자>` DB명, loopback, URL query 없음, public Migration 미적용을 확인하고 테스트마다 임시 schema를 만들고 정리한다. Checking·운영 DB는 허용하지 않는다.

```powershell
$env:DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
$env:ADMIN_POSTGRES_TEST_URL='postgresql+psycopg://<test-user>:<test-password>@127.0.0.1:<test-port>/admin_be_test_actions'
$env:REDIS_URL=''
$env:SESSION_BACKEND='postgres'
$env:ENABLE_DEV_AUTH_BYPASS='false'
python -m pytest tests/test_admin_support_actions.py tests/test_inquiry_answer_migration.py tests/test_admin_support_actions_postgres.py tests/test_own_inquiry_reads.py tests/test_own_inquiry_reads_postgres.py tests/test_admin_support.py tests/test_admin_support_api.py tests/test_admin_resource_permissions.py tests/test_migration_policy.py tests/test_alembic.py -q --tb=short
```

실제 HTTP·세션·CSRF·Migration·PostgreSQL을 연결하는 브라우저 fixture는 합성 회원·신고·문의만 사용하며 production 앱에는 제어 경로를 등록하지 않는다. BE와 FE는 별도 터미널에서 실행한다.

```powershell
# backend: 위 격리 DB 환경을 설정한 뒤
python tests/serve_admin_support_actions_fixture.py --port 8201

# frontend: 별도 터미널
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8201'
$env:NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API='true'
npm run build -- --webpack
npm run start -- --hostname 127.0.0.1 --port 3223

# frontend: 검증 터미널, 설치된 Playwright를 사용
node tests/admin-support-actions-browser-check.cjs http://127.0.0.1:3223 ../.preview-pivot/admin-support-actions/browser
```

Playwright가 별도 위치에 있으면 `PIVOT_PLAYWRIGHT_MODULE`에 해당 모듈 경로를 지정한다. 브라우저 검사는 loopback 이외 트래픽을 차단하고 1728px·390px에서 신고 처리, 답변 등록, 커밋 후 응답 유실과 동일 요청 재확인, 사용자 본인 읽기·타인 404, 텍스트 XSS 차단, viewer의 쓰기 비활성을 검증하도록 구성했다.

최종 확인한 결과 (각 테스트 묶음에는 중복이 있으므로 합산하지 않는다):

- BE 통합 회귀 묶음 184개 통과: 신규 쓰기, 기존 지원 조회, 회원 본인 조회, 관리자 권한, Migration 정책·Alembic.
- 위 묶음에 포함한 실제 PostgreSQL 지원 처리 테스트 11개 통과: 동시 요청, 상태 충돌, 감사 롤백, 권한·소유자 탈퇴 및 답변 삭제 연계. 회원 본인 조회의 별도 PostgreSQL 테스트 1개도 통과.
- 최종 배포 guard 수정 후 Checking 검증·bootstrap 테스트 125개 통과, 하위 검사 16개 통과, 호스트 symlink 제한으로 1개 skip. Migration 정책 테스트 12개 통과.
- 전체 FE 테스트 801개 통과, 기존 선택 테스트 1개 skip. 타입 검사와 webpack 프로덕션 빌드 통과.
- 실제 HTTP·세션·CSRF·격리 PostgreSQL 브라우저 E2E 통과. 1728px·390px에서 신고 검토 시작→해결, 문의 답변 등록→사용자 본인 화면 표시, 커밋 후 503→동일 요청 재확인 시 답변·감사 각각 1회, 타인 문의 404, XSS 텍스트 표시, viewer 쓰기 비활성을 확인했다. 수평 화면 넘침과 브라우저 런타임 오류도 없었다.
- 로컬 실행 결과와 화면은 `.preview-pivot/admin-support-actions/browser/results.json` 및 같은 폴더의 PNG에 저장했다. 이 경로는 커밋 대상이 아니다. 검증 후 전용 서버와 임시 PostgreSQL 컨테이너를 종료했다.

구현 근거: [지원 Router](../backend/app/api/v1/admin/support_router.py), [쓰기 서비스](../backend/app/services/admin_support_action_service.py), [쓰기 권한](../backend/app/services/admin_support_permission_provider.py), [문의 답변 Migration](../backend/alembic/versions/83a2f9c7d104_add_inquiry_answer.py), [회원 문의 조회](../backend/app/services/inquiry_read_service.py), [FE 쓰기 Client](../frontend/lib/admin-support-write.ts), [FE 요청 상태](../frontend/lib/admin-support-flow.ts).
