# 관리자 콘텐츠 조회·승인/반려 E2E 준비 (11/19)

기준은 fetch로 확인한 `origin/Checking` `a0a1af2e990ae6e007a47da9c7ff7dfdf0a8e0ef`이다. PR #86(승인·반려 FE Client)과 PR #87(알림·공지 UI)이 포함되어 있다. 작업 브랜치는 독립 worktree의 `claude/admin-review-e2e-prep-1119`이다. 이전 브랜치 `claude/menu-admin-api-setup-7702cd`는 그대로 보존했다.

PR #71·Migration, viewer/operator/admin 권한 Provider, 운영 Router, 감사 트랜잭션·PostgreSQL 검증, Agent 적재기의 반려 상태 보호는 BE 담당이며 변경하지 않았다. 승인·반려 버튼은 계속 비활성이다.

## 이번 준비

- **병합 코드 확인**: PR #86의 GET·승인/반려 Client는 병합 전과 동일하다. PR #87은 공용 `admin-status.tsx`·`admin-navigation.tsx`의 문구와 메뉴만 바꿨고, 콘텐츠 검수 경로에는 영향이 없다.
- **앱 코드 변경 없음**: 아래 권한표로 점검한 결과, 현재 FE Client가 모든 경우를 이미 맞게 분류한다. 지금 안전하게 고칠 수 있는 앱 코드 결함은 찾지 못했다.
- **격리 E2E 리허설 추가**: 실제 FE Client(`lib/api.ts`, `admin-content.ts`, `admin-content-review.ts`)를 실제 HTTP로 루프백 fake API에 붙여 시나리오 7개를 검증한다.
  - `frontend/tests/helpers/admin-review-api-fixture.cjs`: 127.0.0.1에서만 열리는 fake API다. 현재 BE 검수 서비스의 순서(세션 → 읽기 권한 → CSRF → DTO → 쓰기 권한 → replay → 버전·상태 → 커밋·감사)와 11/19 권한표를 따라 한다.
  - `frontend/tests/admin-review-e2e.test.cjs`: `npm test`에 포함된다.
  - fake API는 BE의 대체가 아니다. 통과해도 실제 BE·PostgreSQL 동작을 증명하지 않는다.

## 권한표 기준 FE 동작

| 역할·상태 | 목록·상세 GET | 승인·반려 POST | FE 분류 |
| --- | --- | --- | --- |
| viewer | 200 | 403 `ADMIN_WRITE_REQUIRED`, 변경·감사 없음 | forbidden, 상세 재조회 |
| operator / admin | 200 | 200, `auditLogId` 확인 | 성공 |
| 권한 회수 (같은 세션) | 403 `ADMIN_REQUIRED` | 403 `ADMIN_REQUIRED` (이미 커밋된 요청의 재전송 포함) | 접근 권한 없음 |
| operator → viewer 강등 | 200 | 403 `ADMIN_WRITE_REQUIRED` | forbidden |
| 비로그인·세션 만료 | 401 | 401 | 로그인 필요 |
| 관리자 아닌 회원 | 403 `ADMIN_REQUIRED` | 403 | 접근 권한 없음 |

## 격리 시나리오 (자동)

1. viewer는 목록(검수 대기 2건)과 상세를 읽고, 승인·반려는 모두 403이며 콘텐츠·감사가 그대로다.
2. operator가 승인한다.
   - 응답 10개 필드와 감사 행 1개(관리자 계정, 작성자, 양끝 공백 제거된 사유)를 확인한다.
   - 전송 본문은 6개 필드이고, `expectedUpdatedAt`은 상세 값과 같은 문자열이다.
   - 같은 요청을 재전송하면 `replayed: true`와 같은 `auditLogId`를 받고, 감사 행은 1개로 유지된다.
   - 다시 조회한 상세의 `updatedAt`이 응답 값과 같고, 이후 같은 ID로 다른 사유를 보내면 409 `REVIEW_REQUEST_CONFLICT`다.
3. admin이 반려하면 hidden/failed/discarded가 된다. 다른 세션이 들고 있던 오래된 버전으로 승인하면 409 `REVIEW_STATE_CONFLICT`다.
4. 권한을 회수하면 같은 세션의 다음 GET·POST·재전송이 모두 403이다. 역할을 viewer로 내리면 GET은 200, POST는 403이다.
5. 비로그인·세션 만료·관리자 아닌 회원은 각각 로그인 필요 또는 접근 권한 없음으로 분류된다.
6. 결과를 알 수 없는 경우 5종을 확인한다: 권한 확인 503, 커밋 전 503, 커밋 후 503, 커밋 후 응답 유실, 커밋 후 감사 번호 불일치.
   - 모두 중립 문구의 retry로 분류되고, 같은 본문으로 재전송된다.
   - 커밋된 경우는 replay로, 커밋되지 않은 경우는 1회 커밋으로 끝난다. 감사 행은 항상 1개다.
7. 세션의 CSRF가 바뀌면 403 `CSRF_INVALID`가 나고, 다음 시도에서 새 토큰을 받아 같은 요청이 1회 커밋된다.

## BE 연결 후 실제 E2E 체크리스트

### 0. 사전 조건 (BE)

- [ ] PR #71 Migration이 Checking DB에 적용되고, 실제 읽기·쓰기 Provider가 연결된다.
- [ ] 읽기 Router와 review Router가 운영 앱에 등록된다.
- [ ] 테스트 계정이 준비된다: viewer, operator, admin, 관리자 아닌 회원. 권한은 BE가 부여하며 FE는 부여하지 않는다.
  - Checking은 `ENABLE_DEV_API=false`이므로 계정은 `https://checking.me-nu.kr`에서 실제 로그인한다.
- [ ] 테스트 콘텐츠가 준비된다: Draft가 checking/approved인 `needs_review` 콘텐츠 3건(승인용, 반려용, 충돌용) + Draft 없는 기존 콘텐츠 1건. E2E가 Checking DB 상태를 바꾸므로 재실행용 초기화 방법도 정한다.
- [ ] `FRONTEND_ORIGINS`에 `https://checking.me-nu.kr`이 포함된다. `compose.checking.yaml`에는 이미 있다. CSRF Origin 검사와 CORS credentials가 이 값에 의존한다.
- [ ] Checking FE 빌드가 PR #86을 포함하고 `NEXT_PUBLIC_API_BASE_URL=https://api.checking.me-nu.kr`로 만들어진다.

### 1. 조회 (브라우저 UI, 버튼 활성화 불필요)

- [ ] viewer·operator·admin
  - `/admin/contents` 전체 목록과 검수 대기 모드(`needs_review`만)가 보인다.
  - 상세가 열리고, "응답을 확인하지 못했습니다" 오류가 없다. 이 오류가 나면 `updatedAt`의 소수 6자리·offset을 확인한다.
- [ ] 관리자 아닌 회원은 "접근 권한이 없습니다", 로그아웃 상태는 "로그인이 필요합니다"가 보인다.
- [ ] 권한을 회수한 뒤 같은 탭으로 돌아오거나 "다시 확인"을 누르면 403 화면으로 바뀐다.
- [ ] Network 탭: GET에 세션 쿠키가 포함되고, CORS 오류가 없고, `Cache-Control: private, no-store`가 붙는다.

### 2. 승인·반려 (API 호출, 버튼은 비활성 유지)

실제 POST 실행은 BE 승인 이후에만 하고, BE가 Swagger나 스크립트로 실행한다. 각 항목마다 BE가 `content`·`draft`·`admin_action_log`를 함께 확인한다.

- [ ] viewer POST → 403 `ADMIN_WRITE_REQUIRED`. 세 테이블 모두 변화 없음.
- [ ] operator 승인 → 200.
  - 상태가 active/confirmed/published로 바뀐다.
  - `auditLogId`가 새 감사 행 ID와 같고, 행의 관리자 계정·사유·before/after 스냅샷을 확인한다.
- [ ] 같은 `requestId`·같은 본문 재전송 → 200 `replayed: true`, 같은 `auditLogId`, 감사 행 1개.
- [ ] 같은 `requestId`로 다른 사유 → 409 `REVIEW_REQUEST_CONFLICT`.
- [ ] admin 반려 → 200 hidden/failed/discarded, `publishedAt` 유지.
- [ ] 다른 세션의 오래된 `expectedUpdatedAt` → 409 `REVIEW_STATE_CONFLICT`. Draft 없는 콘텐츠도 409.
- [ ] CSRF 헤더 없음, 다른 값, 허용되지 않은 Origin → 403 `CSRF_INVALID`.
- [ ] 권한 회수 → 같은 세션의 다음 GET·POST·재전송이 모두 403. 비로그인 → 401.
- [ ] 응답 JSON이 10개 키이고 `publishedAt`·`updatedAt`에 offset이 있다(FE 파서 조건).
- [ ] Agent 적재기가 반려된 콘텐츠를 다시 검수 대기나 공개 상태로 되돌리지 않는다(BE).
- 503과 응답 유실은 실환경에서 일부러 만들지 않는다. 격리 시나리오 6번으로 대신하고, BE는 격리 PostgreSQL에서 커밋 장애를 검증한다.

### 3. 버튼 활성화 후 브라우저 E2E (BE 승인 후 별도 FE 작업)

- [ ] 확인창에서 만든 요청 객체를 POST 중과 재시도 동안 보존한다.
  - 현재 `useAdminRead`는 focus·visibility·pageshow 때 다시 조회하면서 패널을 다시 그린다. 그러면 결과 불명 상태의 요청 객체가 사라진다.
  - POST 중에는 재조회를 멈추거나, 요청을 패널 바깥에 보관해야 한다.
- [ ] 실행 중 중복 클릭을 막는다. 성공하면 상세를 재조회하고 감사 번호를 표시한다.
- [ ] retry 분류는 중립 문구와 "같은 요청으로 다시 확인"으로 처리하고, reload 분류는 요청을 버리고 재조회한다.
- [ ] `admin-review-actions-browser-check.cjs`의 "POST 없음" 조건을 실제 흐름 검사로 교체한다.

## 막혀 있는 계약 사항

1. **역할별 오류 코드**: viewer의 POST는 `ADMIN_WRITE_REQUIRED`, 회수된 계정은 `ADMIN_REQUIRED`를 유지할지 확정한다. FE는 둘 다 forbidden으로 처리하지만, 코드가 바뀌면 시나리오를 조정해야 한다.
2. **쓰기 권한 노출**: GET에 쓰기 권한 정보가 없으면 viewer에게도 승인·반려 버튼이 보이고, 누르면 403을 받는다. 버튼 활성화 전에 결정이 필요하다.
3. **검수 가능 여부 노출**: Draft 없음이나 Draft 상태 불일치를 GET에서 알 수 있게 할지 결정한다.
4. **응답 시각 offset 보장**: `AwareDatetime` 또는 PostgreSQL `timestamptz`로 보장한다.
5. **재전송 계약**: 같은 `requestId`·같은 본문의 replay를 유지하는지, 회수 후 재전송이 403인지, replay 유효 기간과 감사 보존 기간을 확정한다.
6. **BE 503 메시지**: "검수 저장에 실패했습니다"를 중립 문구로 바꿀지 결정한다.
7. **경로와 오류 코드 고정**: 운영 경로와 성공 200, 오류 코드 목록을 고정한다.
8. **테스트 데이터 준비**: 테스트 계정·콘텐츠를 누가 준비하고 E2E 후 어떻게 초기화할지 정한다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 격리 E2E 리허설 (`admin-review-e2e.test.cjs`) | 7개 통과 |
| 관리자 FE 단위 테스트 (`admin-content`, `admin-content-input`) | 36개 통과 |
| FE 전체 `npm test` | 684개 중 681개 통과, 1개 skip, 2개 실패 (아래 설명) |
| `npm run typecheck`, `npm run build -- --webpack` | 통과 |
| 관리자 BE 격리 테스트 (BE 무변경, 새 Checking 기준) | 257개 통과 |

FE 전체 테스트의 실패 2개는 이번 변경과 무관하다. `admin-reports`·`admin-notices` 테스트가 `public/admin/previous.svg`의 원본 해시를 검사하는데, 이 Windows worktree에서는 해당 파일이 CRLF로 체크아웃되어 해시가 달라진다(index는 LF이고 `-text` 속성이 없다). 이번 변경은 해당 파일과 테스트를 건드리지 않는다.

```text
cd frontend
node --test tests/admin-review-e2e.test.cjs tests/admin-content.test.cjs tests/admin-content-input.test.cjs
npm run typecheck
npm run build -- --webpack
```
