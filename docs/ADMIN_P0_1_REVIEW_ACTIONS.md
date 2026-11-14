# ADMIN P0-1: 콘텐츠 승인·반려 첫 Slice

기준 Checking: `104fd9b006b5ccda35d4fd3d219f544c4c8e6db4` (PR #73 병합).
로컬 브랜치: `codex/admin-p0-1-review-actions-1113`. 작성일: 2026-11-13 KST.

## 구현 범위와 안전 경계

기존 검수 대기 목록·상세·출처·세션 인증·CSRF 검사·Content/Draft 모델·공개 조건을 재사용한다. 새로운 enum, 모델, Migration, Worker, Consumer 변경은 없다. 운영/Checking DB에 연결하거나 DDL을 실행하지 않았다.

`POST /api/v1/admin/contents/{contentId}/review` 계약과 별도 Router, 전환 Service, SQL Repository를 구현했다. **검수 Router는 기존 읽기 Router, V1 Router, 기본 앱 어디에도 등록하지 않았다.** 환경변수나 FE 플래그로 활성화할 수 없다. 기본 쓰기 Permission Provider는 항상 거부한다. 허용 Provider와 Router 등록은 테스트 코드에만 있다.

FE는 기존 상세 화면에서 승인·반려 내용과 조치 사유를 확인하는 단계다. `입력 확인 완료 · 미저장`을 표시하며 실행 버튼은 항상 disabled다. 쓰기 transport, mock 저장 성공, DB 상태의 낙관적 변경은 없다. 읽기 성공을 쓰기 권한으로 취급하지 않는다.

## 요청·응답 계약

```json
{
  "requestId": "6a019f7c-ae76-4450-b357-dd31798b91ca",
  "action": "approve",
  "rationale": "출처와 본문을 확인했습니다.",
  "expectedStatus": "pending_review",
  "expectedJudgmentStatus": "needs_review",
  "expectedUpdatedAt": "2026-11-13T00:00:00Z"
}
```

- action: `approve | reject`. requestId: UUID. 사유: 양쪽 공백 제거 후 1..2,000 Unicode code point. 기존 입력 검증의 제어문자·단독 surrogate 거부 정책을 재사용한다.
- expectedStatus: `active | pending_review`, expectedJudgmentStatus: `needs_review`, expectedUpdatedAt: timezone을 포함한 ISO 시각. 숫자 시각·임의 actor/admin ID·상태·출처·기타 필드 및 타입 강제 변환을 거부한다.
- 콘텐츠 ID, 사용자 ID는 서버에서 검사한다. actor는 실제 세션 사용자이며 요청 본문에서 받지 않는다. admin_account_id는 쓰기 Provider에서 받는다.
- 성공은 트랜잭션 commit 이후에만 반환한다. 응답은 contentId, draftId, action, status, judgmentStatus, draftStatus, publishedAt, updatedAt, auditLogId, replayed다. 피드 공개 여부를 의미하는 필드는 없다.
- 401: 실제 세션 없음/회수/만료/비활성 계정. 403: CSRF 또는 읽기/쓰기 권한 없음. 404: 콘텐츠 없음. 409: 상태·버전·요청 충돌. 422: 입력 계약 위반. 503: 권한 확인/DB/감사 기록 실패. 상세 내부 오류는 노출하지 않는다.

## 허용 전환

첫 Slice는 **deleted_at이 없는 Content**, `needs_review`, `active | pending_review`, **연결된 Draft의 checking | approved**만 전환한다. 현재 Content의 상태·판정·updated_at이 요청과 모두 같아야 한다. Draft 없는 레거시 콘텐츠, writing/published/discarded Draft, hidden/deleted 콘텐츠 및 상태 불일치는 409로 보류한다. 이 보수적인 시작 상태 범위는 BE/기획 확인 대상이다.

| 조치 | Content.status | Content.judgment_status | Draft.status | published_at | 미리보기 메타데이터 |
| --- | --- | --- | --- | --- | --- |
| 승인 | active | confirmed | published | null일 때만 승인 UTC 시각, 과거·미래 값 보존 | Content.ai_judgment_basis, Draft.factcheck_result의 preview/local_test_preview/checking_test_preview 키 제거 |
| 반려 | hidden | failed | discarded | 기존 값 보존 | 기존 값 보존 |

두 모델의 updated_at을 조치 시각으로 갱신한다. 승인 시 AI 근거의 method/digest/operator_review 등 나머지 키를 보존하며 사람의 판단으로 덮어쓰지 않는다. 인간 검수 이력은 감사 로그에 기록한다. 제목·본문·요약·출처 provenance·슬라이드·feed_valid_until·소유자·AgentRun은 수정하지 않는다.

기존 `carousel_repository.public_content_condition()`을 그대로 사용해 결과를 검증했다. 승인되어도 미래 발행 시각, 기한 만료, 출처 없음, 슬라이드 부족/불연속이면 기존 조건에 따라 공개되지 않는다. 승인 API가 공개 조건을 우회하거나 만료 기한을 연장하지 않는다.

## 트랜잭션·동시 요청·중복 처리

Service는 사용 중인 트랜잭션이 없는 새 AsyncSession을 요구한다. HTTP 인증 조회용 세션과 검수 저장 세션을 분리해, 인증 조회의 autobegin이나 호출자의 미완료 변경을 함께 commit하지 않는다. 중첩 savepoint 완료를 저장 성공으로 반환하지 않는다.

순서: 새 트랜잭션 → 현재 쓰기 grant 확인/잠금 → Content FOR UPDATE → 기존 요청 감사 기록 확인 → Draft FOR UPDATE → Content/Draft 조건부 UPDATE → 감사 INSERT RETURNING → commit → 응답.

- 쓰기 Provider는 해당 사용자와 콘텐츠 범위의 현재 권한을 확인하고, 활성 admin_account 행에 회수와 충돌하는 잠금을 commit까지 유지해야 한다. 회수 구현도 같은 행 잠금을 사용해야 한다. 기본 구현은 거부뿐이며 실제 DB Provider/역할 매핑은 제공하지 않는다.
- Content/Draft는 읽은 updated_at과 상태를 UPDATE 조건으로도 비교한다. 영향받은 행이 1개가 아니면 409와 전체 rollback이다. PostgreSQL 직렬화 충돌/교착/잠금 충돌과 SQLite busy/locked도 rollback 후 409다.
- 중복 키 범위는 **admin_account_id + content_id + requestId**다. 동일한 정규화 요청의 감사 기록이 이미 있으면 기록에 저장된 원래 응답과 auditLogId를 `replayed=true`로 반환하고 새 로그/업데이트를 만들지 않는다. 이는 현재 상태 재조회가 아닌 원래 commit의 재응답이다.
- 같은 키에 다른 action/사유/기대 상태·시각이면 `REVIEW_REQUEST_CONFLICT`다. 다른 키로 완료 상태를 다시 전환하거나 반대 조치를 하면 `REVIEW_STATE_CONFLICT`다. 최종 상태가 같다는 이유만으로 성공을 만들지 않는다.
- 동일 요청 재시도에도 현재 권한 확인이 먼저 수행된다. 회수된 grant는 과거 성공 요청도 재응답받을 수 없다. 권한 재부여로 admin_account_id가 달라지면 이전 grant의 요청을 재사용하지 않는다.
- 감사 테이블 누락, INSERT/FK 실패, Draft UPDATE 실패, commit 실패 시 성공하지 않는다. 실패 후 같은 requestId로 재시도할 수 있다. 네트워크 단절로 commit 여부가 불분명해도 새 UUID를 만들지 않고 같은 요청을 재전송하는 것이 향후 클라이언트 계약이다.
- 실패·거부 요청 자체의 감사 로그는 이 성공 트랜잭션에 남기지 않는다. 별도 보안 이벤트 기록/보존 정책은 BE 확인 대상이다.

## PR #71 계약 확인

[PR #71](https://github.com/Por0ri/me-and-nu/pull/71)의 조사 시 HEAD는 `e96e2671a2a47a4857513c83a22bf1bcb177e0ab`, Draft/open이었다. [admin.py](https://github.com/Por0ri/me-and-nu/blob/e96e2671a2a47a4857513c83a22bf1bcb177e0ab/backend/app/models/admin.py)를 확인했으며 해당 코드/브랜치/Migration은 변경·병합하지 않았다.

- admin_account: admin_account_id, user_id, role, granted_by_admin_id, granted_at, revoked_at. 활성 권한은 user별 revoked_at IS NULL 부분 고유 인덱스로 제한된다. role 값별 허용 행위 정책은 PR에 확정되지 않았다.
- admin_action_log: admin_action_log_id, admin_account_id(FK), action_type(40), target_type(20), target_id, before_value/after_value(JSONB), rationale(Text), is_reverted(default false), acted_at.
- 이 Slice의 Repository는 그 테이블을 참조하는 SQLAlchemy `table()`만 사용한다. ORM 모델/MetaData/DDL을 새로 등록하지 않는다. 감사 테이블이 없으면 fail closed다.
- 제안 action_type은 `content.approve | content.reject`, target_type은 `content`. before/after에는 Content/Draft 변경 대상 스냅샷을 기록한다. after에는 contract_version=1, request_id, 정규화 request, actor_user_id, 원래 응답을 함께 저장한다. rationale은 기존 전용 컬럼, admin_account_id는 기존 FK를 사용한다.
- PR #71에는 전용 request_id/unique 컬럼이 없으므로 새 Migration을 만들지 않고 Content 잠금으로 요청 직렬화를 보장한다. 향후 감사 JSON 보존·검색 성능·중복 키 보존 기간·모든 writer의 잠금 규칙을 합의해야 한다.

## 권한·CSRF 및 FE 확인 UI

기존 `require_admin`의 실제 세션·활성 사용자·매 요청 읽기 권한 검사를 재사용한다. 별도 `AdminReviewPermissionProvider.lock_grant()`에서 쓰기 허용과 대상을 확인한다. read-only Provider를 write Provider로 대체할 수 없다. 브라우저의 role/header, 개발용 인증 bypass, FE 상태는 쓰기 근거가 아니다.

CSRF는 기존 `require_csrf`를 실제 관리자 AuthContext로 직접 호출한다. Consumer의 개발 bypass를 지원하는 의존성을 다시 조회하지 않는다. 허용 Origin, 세션의 CSRF hash, 현재 쿠키에서 파생된 CSRF 값이 모두 일치해야 한다.

UI는 native modal dialog를 사용한다. 사유 검증, 승인/반려 효과, 취소·Escape·포커스 복귀를 제공한다. focus/visibility/pageshow 등 기존 읽기 재검사 시 화면이 loading/401/403으로 전환되면 dialog와 사유를 폐기한다. 작성값을 브라우저 저장소에 기록하지 않는다. 목록 query 복귀 흐름과 기존 상세·출처 표현을 유지한다.

## 검증 증거

- 기존 관리자 BE 157개 통과 + 신규 검수 테스트 최종 **80개 통과**, 합계 **237개**. 새 검수 테스트는 추가 검증 후 다시 실행했으며, 변경 없는 기존 테스트는 앞선 합동 실행의 결과를 사용한다.
- 실제 격리 SQLite 파일/독립 SQL 세션 및 기존 실제 HTTP 인증 fixture 사용. 허용 grant는 테스트 전용 Provider다. DB 대상은 테스트 fixture에서 override한다.
- 승인·반려/시작 상태, null·과거·미래 발행, 두 모델 미리보기 제거/근거 보존, 출처·슬라이드 불변, 기존 공개 조건, 중복/상반 조치, 오래된 버전, 감사 누락/FK/INSERT 실패, Draft 실패, commit 실패, HTTP 실패 후 동일 ID 재시도를 검사했다.
- 두 별도 연결/스레드가 동일 원본을 읽은 뒤 동시에 승인 또는 승인·반려하도록 barrier로 제어했다. 한 건의 상태 전환/감사 기록만 남고 패자는 충돌 또는 기존 결과 재응답으로 끝난다. SQLite의 실제 rollback/CAS 검증이며 PostgreSQL 행 잠금 검증으로 간주하지 않는다.
- CSRF 누락/오류/다른 쿠키/조작된 저장 hash/허용되지 않은 Origin, 세션·계정·권한 회수, 읽기 전용/기본 거부/대상 범위/Provider 장애, 개발 bypass, 운영 404/OpenAPI 미노출을 검사했다.
- FE 관련 단위 테스트 **23개**, TypeScript, Webpack production build 통과.
- 브라우저 **1728px·390px 통과**: 두 확인창, 빈 사유/길이 오류, 미저장 표시, 실행 비활성, GET만 발생, Escape/포커스 복귀, 취소 후 사유 비움, 잘못된 상태 비활성, 401/403 시 창·사유 제거, 가로 넘침 없음, 목록 query 유지. 스크린샷을 직접 확인했다. ignored `.preview-pivot/admin-review-actions-1113/`에 결과와 스크린샷을 저장한다.
- 브라우저 검증은 로컬 읽기 응답 interception을 사용한다. FE→쓰기 API→DB 저장 E2E를 통과했다고 주장하지 않는다. API→Service→DB→응답은 별도 격리 HTTP 테스트로 검증한다.

재현: backend에서 기존 Python venv + 이 worktree의 `.venv-deps`, `DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:1/admin_review_test`로 아래를 실행한다. 이 주소에 연결하지 않으며 모든 DB가 격리 fixture다.

```text
python -m pytest tests/test_admin_content.py tests/test_admin_content_api.py tests/test_admin_content_input.py tests/test_admin_review_queue.py tests/test_admin_content_review.py -q
node --test tests/admin-content.test.cjs tests/admin-content-input.test.cjs
npm run typecheck
npm run build -- --webpack
```

FE build/preview는 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8197`, `npm run start -- --hostname 127.0.0.1 --port 3197`. 브라우저 검사는 기존 Playwright 경로를 PIVOT_PLAYWRIGHT_MODULE로 지정한 뒤 `node tests/admin-review-actions-browser-check.cjs http://127.0.0.1:3197 ../.preview-pivot/admin-review-actions-1113`로 실행한다.

## 운영 연결에 필요한 BE 확인

1. **PR #71 모델/테이블 승인·적용** 및 실제 grant 조회/잠금/회수 Provider 구현. 허용 role, 콘텐츠 대상 범위, read와 review 권한, 회수와 조치가 경합할 때의 순서를 확정한다.
2. **감사 정책**: action_type·JSON 버전/스냅샷·사유 필수/상한·requestId 범위 및 보존 기간, 실패/거부 이벤트 기록, 감사 행 삭제/수정 금지와 replay 보존, JSON 검색 성능을 합의한다. DB 모델 변경이 필요하면 별도 승인한다.
3. **시작 상태 정책**: Draft 없는 레거시, hidden+needs_review 등 불일치, Draft checking/approved 허용, 반려 후 재검수/복구를 확인한다. 이 Slice는 임의 복구/재검수 전환을 구현하지 않는다.
4. **버전 조회 계약**: 현재 관리자 상세 읽기 DTO에는 updatedAt이 없다. expectedUpdatedAt을 클라이언트에 제공할 신뢰 가능한 읽기 필드/버전 계약과 향후 모든 writer의 updated_at 갱신·잠금 순서를 확정한다. FE에서 시각을 추측하거나 createdAt으로 대체하지 않는다.
5. **격리 PostgreSQL 검증**: 실제 AsyncSession, FOR UPDATE, grant 회수 경합, 동일/다른 관리자 동시 조치, deadlock/serialization/commit 장애를 승인된 격리 DB에서 실행한다. 해당 환경에서 PR #71 실제 FK/JSONB 및 모델로 재검증한다.
6. 위 조건 충족 후 별도 작업에서 운영 Router를 등록하고 FE 쓰기 transport·버전·requestId 유지·CSRF·응답 검증·충돌 재조회·감사 성공 표시를 연결한다.

## 미완료 범위

- **운영 Blocker**: 실제 권한 DB Provider 및 승인된 감사 DB가 연결되지 않았고 운영 Router가 미등록이다. 이는 안전 조건에 따른 의도된 제한이다.
- **검증 한계**: Docker daemon이 `Docker Desktop is unable to start`를 반환해 PostgreSQL을 실행하지 못했다. PostgreSQL SQL의 FOR UPDATE 컴파일은 검사했으나 실제 PostgreSQL 잠금/격리 수준 및 운영 grant 회수 경합은 검증하지 않았다.
- 실제 저장 가능한 FE, 상세 버전 읽기 확장, 실패 감사 파이프라인, 재검수/복구, 타 Writer/PR #68과의 통합은 미완료다. 운영 데이터/실계정 테스트·Migration·Push·PR·Merge·배포는 수행하지 않는다.
