# 관리자 콘텐츠 승인·반려 FE 연동 준비

> BE 후속 답변과 구현: [관리자 BE 1차 통합 계약](ADMIN_BE_INTEGRATION_2026-11-18.md). 아래는 FE PR #86 당시 기록이다. 현재 Router 등록·DB Provider·timezone 응답·상세 review 메타데이터·재시도 보존 범위는 후속 계약을 따른다. FE 실행 버튼은 여전히 후속 연결 대상이다.

기준은 fetch로 확인한 `origin/Checking` `66494e1406e7be20ae45df2f47f5b130a7e9ba7b`이다. 작업 브랜치는 독립 worktree의 `claude/menu-admin-api-setup-7702cd`이다.

BE의 권한 Provider·PR #71·Migration·Router 등록은 BE 담당이며 이번 작업에서 변경하지 않았다. 관리자 권한도 부여하지 않았다. 승인·반려 POST는 여전히 어떤 화면에서도 호출하지 않는다.

## 기존 구현 점검

### 콘텐츠 GET (FE 변경 없음)

- `loadAdminContents`·`loadAdminContent`는 기존 `apiRequest`로 `/api/v1/admin/contents`를 호출한다. `credentials: include`, `cache: no-store`, AbortSignal을 사용한다.
- 응답을 엄격하게 검증한다. 페이지·건수·중복 ID, review 모드의 `needs_review`, 상세 ID, 출처 필드를 확인하고, 어긋나면 `INVALID_ADMIN_RESPONSE`로 실패한다.
- 상세 `updatedAt`은 Date로 바꾸지 않고 문자열 그대로 보존한다. 소수는 생략하거나 1–6자리이며 offset은 필수이고 `-00:00`은 거부한다.
- 오류 상태 대응: 401 → 로그인 필요, 403 → 권한 없음, 404 `CONTENT_NOT_FOUND` → 없음, 그 밖의 404 → 연결 준비 중(Router 미등록), 나머지 → 오류.
- `useAdminRead`는 세션 미인증, focus·visibility·pageshow 재조회, `api:unauthorized`를 처리한다.

### 승인·반려 BE 계약 (`review_router`, 테스트 전용·미등록)

- **경로**: `POST /api/v1/admin/contents/{contentId}/review`, 성공 시 200, `Cache-Control: private, no-store`.
- **요청**: 아래 6개 필드만 받는다. strict 모드이며 추가 필드를 금지한다.
  - `requestId`(UUID), `action`(`approve`|`reject`)
  - `rationale`: 1–2,000자. 양끝 공백을 제거하고 제어문자는 거부한다.
  - `expectedStatus`(`active`|`pending_review`), `expectedJudgmentStatus`(`needs_review`)
  - `expectedUpdatedAt`: ISO 문자열
- **응답**: `contentId`, `draftId`, `action`, `status`, `judgmentStatus`, `draftStatus`, `publishedAt`, `updatedAt`, `auditLogId`, `replayed`. 감사 행은 상태 전환과 같은 트랜잭션에서 기록된다.
- **CSRF**: `X-CSRF-Token`은 세션의 `csrfToken`이며, 쿠키 결합과 Origin을 검사한다. 기존 `apiRequest`가 이 헤더를 자동으로 붙인다.
- **버전 비교**: `expectedUpdatedAt`은 시각으로 비교한다. offset 표기가 달라도 같은 시각이면 통과하지만, 마이크로초는 정확히 일치해야 한다.
- **재전송**: 같은 `requestId`를 재전송하면 커밋된 결과를 `replayed: true`로 돌려준다. 다만 비교 기준에는 offset 표기까지 들어간다. 그래서 같은 시각이라도 표기를 바꿔 재전송하면 `REVIEW_REQUEST_CONFLICT`가 된다.
- **부족했던 점**: FE에 요청 생성, 전송, 응답 검증, 오류·재시도 분류가 없었다. 확인창은 입력 검증까지만 했다.

## 이번 변경

| 파일 | 내용 |
| --- | --- |
| `frontend/lib/admin-content-review.ts` | `prepareReviewRequest`, `submitAdminContentReview`, `reviewFailure`와 요청·응답 타입 추가 |
| `frontend/lib/admin-content.ts` | 기존 `contentVersion` 검사기를 export만 함 (동작 변경 없음) |
| `frontend/tests/admin-content.test.cjs` | 승인·반려 전송 계약 테스트 9개 추가 |
| `backend/tests/test_admin_review_fe_contract.py` | FE 전송 형식을 실제 HTTP 경로로 검증하는 격리 테스트 10개 추가 (운영 코드 변경 없음) |

- `prepareReviewRequest`는 상세에서 받은 `updatedAt`과 `status`를 그대로 넣고, 사유의 양끝 공백만 제거한다. 확인한 조치마다 한 번 만들고, 재시도할 때도 같은 객체를 쓴다. `requestId`는 호출부에서 `crypto.randomUUID()`로 만든다.
- `submitAdminContentReview`는 6개 필드만 전송한다. 200 응답이라도 조치별 상태 전환, `auditLogId`, 버전 형식이 맞지 않으면 성공으로 처리하지 않는다.
- 확인창의 실행 버튼은 계속 비활성이다. 관리자 화면 어디에서도 `submitAdminContentReview`를 참조하지 않도록 테스트로 고정했다.

### FE 실패 처리 규칙 (`reviewFailure`)

| 응답 | 분류 | 같은 요청 재전송 | 상세 재조회 |
| --- | --- | --- | --- |
| 네트워크 단절·응답 유실·중단, 200 형식 오류 | retry (결과 불명) | 예 | 아니요 |
| 403 `CSRF_INVALID` | retry | 예 (다음 요청이 새 CSRF를 받음) | 아니요 |
| 503 `ADMIN_*_UNAVAILABLE`, 그 밖의 5xx, 408, 429 | retry (결과 불명) | 예 | 아니요 |
| 401 | login-required | 아니요 | 예 |
| 403 `ADMIN_REQUIRED`·`ADMIN_WRITE_REQUIRED` | forbidden | 아니요 | 예 |
| 404 `CONTENT_NOT_FOUND` / 그 밖의 404 | not-found / unavailable | 아니요 | 예 / 아니요 |
| 409 `REVIEW_STATE_CONFLICT`·`REVIEW_REQUEST_CONFLICT` | conflict | 아니요 (새 requestId 필요) | 예 |
| 422 및 그 밖의 4xx | invalid | 아니요 | 아니요 |

서버 message는 화면에 노출하지 않고, 위 분류에 맞춘 고정 문구만 사용한다.

"결과 불명" retry는 커밋 여부를 알 수 없는 상태다. 503은 COMMIT이 성공한 뒤 응답만 유실된 경우에도 나올 수 있고, 앞단 프록시가 5xx·408·429를 대신 돌려줄 수도 있다.

- FE는 이 경우 "저장 실패"나 "미저장"으로 표시하지 않는다. 문구는 "처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요."로 통일한다.
- 다시 확인할 때는 같은 요청 객체(같은 `requestId`, 같은 본문)를 그대로 보낸다. 이미 커밋됐다면 `replayed: true`로 원래 결과를 받고, 아니라면 한 번만 커밋된다.
- 새 `requestId`로 바꾸면 이미 커밋된 조치를 확인하지 못하고 409 충돌로 끝나므로 바꾸지 않는다.

## BE에 요청할 API 계약

1. **경로·응답 코드 확정**: `POST /api/v1/admin/contents/{contentId}/review`, 성공 200을 운영에서도 유지할지 확정한다.
2. **요청 DTO 고정**: 6개 필드와 strict 정책을 고정한다. 아래 두 규칙을 계약 문서에 명시한다.
   - `expectedUpdatedAt`은 시각으로 비교하고 마이크로초까지 일치해야 한다.
   - replay 비교는 요청 원문 기준이다. FE는 문자열을 그대로 재전송한다.
3. **응답 시각 offset 보장**: `publishedAt`과 `updatedAt`은 항상 offset이 붙어야 한다. 현재 응답 스키마 타입은 `datetime`이라 offset 없는 값도 통과할 수 있다. `AwareDatetime`으로 바꾸거나, PostgreSQL `timestamptz`로 보장하는 것을 확정한다. FE는 offset이 없으면 성공으로 보지 않고 결과 불명(retry)으로 처리한다.
4. **오류 코드 목록 고정**:
   - 401 `AUTH_REQUIRED`
   - 403 `CSRF_INVALID`·`ADMIN_REQUIRED`·`ADMIN_WRITE_REQUIRED`
   - 404 `CONTENT_NOT_FOUND`
   - 409 `REVIEW_STATE_CONFLICT`·`REVIEW_REQUEST_CONFLICT`
   - 422 `VALIDATION_ERROR`
   - 503 `ADMIN_AUTH_UNAVAILABLE`·`ADMIN_PERMISSION_UNAVAILABLE`·`ADMIN_REVIEW_UNAVAILABLE`
5. **멱등 재전송과 replay 확인**: 503·5xx·응답 유실은 커밋 여부를 알 수 없는 상태로 본다. FE는 BE에 "503이면 미커밋"을 보장하라고 요구하지 않는다. 대신 다음을 운영 계약으로 확인한다.
   - 같은 `requestId`와 같은 요청 내용을 다시 보내면, 이미 커밋된 경우 원래 응답을 `replayed: true`로 돌려주고, 아니면 한 번만 커밋한다. 지금 구현이 이렇게 동작하며, 실제 권한 Provider와 PostgreSQL을 연결한 뒤에도 유지되는지 확인한다.
   - 이미 커밋된 `requestId`로 다른 내용(조치, 사유, 기대 상태, 버전 문자열)을 보내면 409 `REVIEW_REQUEST_CONFLICT`다.
   - 재전송할 때도 현재 쓰기 권한을 먼저 확인한다. 그 사이 권한이 회수되면 이미 커밋된 조치도 403으로 끝난다. FE는 이때 상세를 다시 조회해 실제 상태를 확인한다. 이 동작을 계약으로 유지할지 확인한다.
   - replay가 유효한 기간과 감사 로그 보존 기간을 정한다. 감사 행이 남아 있는 동안 replay가 가능한지도 정한다.
6. **BE 503 메시지 개선 여부**: `ADMIN_REVIEW_UNAVAILABLE`의 메시지는 "검수 저장에 실패했습니다. 같은 요청 ID로 다시 시도해 주세요."이다. COMMIT 성공 뒤 연결이 끊긴 경우에도 같은 응답이 나올 수 있어 실제와 다를 수 있다. "처리 결과를 확인하지 못했습니다. 같은 요청 ID로 다시 확인해 주세요." 같은 중립 문구로 바꿀지 BE가 결정한다. FE는 서버 message를 표시하지 않으므로 화면에는 영향이 없다.
7. **검수 가능 여부 노출**: Draft 연결 없음이나 Draft 상태 불일치는 POST 후 409로만 알 수 있다. 상세 GET에 검수 가능 여부(또는 Draft 상태) 필드를 추가할지 결정한다.
8. **쓰기 권한 노출**: 읽기 권한만 있는 관리자는 POST 후 403 `ADMIN_WRITE_REQUIRED`를 받는다. 버튼 표시용 capability를 GET에서 내려줄지 결정한다.

## 운영 연결 선행 조건 (BE 담당)

1. PR #71(`admin_account`, `admin_action_log`) 승인·병합. 현재 Draft/open이며 Checking에는 없다.
2. 실제 읽기 Provider(`can_read_contents`)와 쓰기 Provider(`lock_grant`) 구현. role별 허용 행위와 대상 범위, 회수와 조치가 겹칠 때의 순서를 정한다.
3. 감사 정책 확정: action_type, JSON 버전, 보존 기간, 실패·거부 이벤트 기록.
4. 승인된 격리 PostgreSQL에서 검증: FOR UPDATE, grant 회수 경합, 동시 조치.
5. 운영 Router 등록: 읽기 Router와 review Router, 그리고 `get_review_db` 운영 세션.
6. 위 조건이 충족된 뒤 별도 FE 작업으로 확인창 실행 버튼에 `submitAdminContentReview`를 연결한다. 범위는 다음과 같다.
   - 요청 객체를 확인창 상태에 보관한다.
   - 실행 중 중복 클릭을 막는다.
   - 성공하면 상세를 재조회한다.
   - `reviewFailure`에 따라 재시도하거나 재조회한다.
   - `admin-review-actions-browser-check.cjs`의 "POST 없음" 검증을 갱신한다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 관리자 BE 격리 테스트 (기존 5개 파일 + 신규 1개) | 257개 통과 (기존 247 + 신규 10) |
| FE 단위 테스트 (`admin-content`, `admin-content-input`) | 36개 통과 (기존 27 + 신규 9) |
| `npm run typecheck` | 통과 |
| `npm run build -- --webpack` | 통과 |

- BE 테스트는 임시 SQLite와 테스트 전용 Provider만 사용한다. 연결하지 않는 가짜 `DATABASE_URL`을 쓰며, 운영·개발 DB에는 접근하지 않는다.
- 브라우저 E2E는 실행하지 않았다. 화면 동작은 바뀌지 않았다.

```text
# backend (requirements.txt를 설치한 venv)
DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:1/admin_review_test \
  python -m pytest tests/test_admin_content.py tests/test_admin_content_api.py tests/test_admin_content_input.py \
  tests/test_admin_review_queue.py tests/test_admin_content_review.py tests/test_admin_review_fe_contract.py -q
# frontend
node --test tests/admin-content.test.cjs tests/admin-content-input.test.cjs
npm run typecheck
npm run build -- --webpack
```
