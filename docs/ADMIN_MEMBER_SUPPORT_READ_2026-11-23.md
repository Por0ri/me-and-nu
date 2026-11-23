# 관리자 회원·신고·문의 조회 계약 — 2026-11-23

관리자 회원 목록·상세와 신고·문의 목록·상세를 기존 DB에 연결한다. 이번 범위는 **업무 데이터 GET 조회**이며 회원 수정·제재·삭제, 신고 처리, 문의 답변, 알림 발송은 포함하지 않는다. 기존 인증 과정의 세션 접속 시각 갱신은 유지된다. 새 테이블이나 Migration은 추가하지 않는다.

## API와 권한

아래 경로는 모두 `/api/v1` 기준이며 성공 시 HTTP 200, camelCase JSON을 반환한다.

| 메서드·경로 | 범위 | 목록 기본값·상한 |
| --- | --- | --- |
| `GET /admin/members` | 회원 목록 | `page=1`, `pageSize=20`; page ≤ 1,000,000, pageSize ≤ 100 |
| `GET /admin/members/{memberId}` | 회원 상세 | 양의 PostgreSQL bigint ID |
| `GET /admin/reports` | 콘텐츠 신고와 서비스·버그 문의의 통합 목록 | `page=1`, `pageSize=5`; page ≤ 100,000, pageSize ≤ 100 |
| `GET /admin/reports/{reportId}` | 콘텐츠 신고 상세 | 양의 PostgreSQL bigint ID |
| `GET /admin/inquiries` | 문의만 조회 | 통합 목록과 같은 페이지 기본값·상한 |
| `GET /admin/inquiries/{inquiryId}` | 문의 상세 | 양의 PostgreSQL bigint ID |

실제 활성 세션과 현재 DB 권한을 매 요청 확인한다. `viewer`, `operator`, `admin` 모두 조회할 수 있지만 리소스별 capability는 별개다. 회원은 `can_read_members`, 신고·문의는 `can_read_support`를 요구하므로 콘텐츠 조회 권한만으로 접근을 허용하지 않는다. 권한 회수, 비활성 관리자, 인증·권한 조회 실패는 즉시 차단한다. 클라이언트 역할 헤더와 개발 인증 우회로 권한을 얻을 수 없다.

응답은 공통 미들웨어에서 `Cache-Control: private, no-store`를 적용한다. FE 요청도 쿠키를 포함하고 `cache: no-store`를 사용한다. GET에는 변경 작업용 CSRF 제출을 요구하지 않으며, 이번 API가 쓰기 권한을 부여하지 않는다.

오류는 기존 `{code,message,fields?}` 계약을 사용한다. 미인증 401, 권한 없음 403, 유효하지 않은 조건 422, 조회 불가 503이다. 존재하지 않는 상세는 404와 `MEMBER_NOT_FOUND`, `ADMIN_REPORT_NOT_FOUND`, `ADMIN_INQUIRY_NOT_FOUND`를 반환한다. 빈 목록은 200과 `items: []`이며 오류를 빈 목록으로 바꾸지 않는다.

근거: [회원 Router](../backend/app/api/v1/admin/members_router.py), [지원 Router](../backend/app/api/v1/admin/support_router.py), [공통 관리자 권한](../backend/app/api/admin_deps.py), [권한 Provider](../backend/app/services/admin_permission_provider.py), [공통 응답](../backend/app/main.py).

## 검색과 페이지

검색어는 URL의 `q` 대신 **`X-Admin-Search` 헤더**에 UTF-8 percent encoding으로 보낸다. FE는 검색어를 컴포넌트 메모리에 유지하며 URL·localStorage·sessionStorage에 저장하지 않는다.

```javascript
const search = "김하루";
fetch("/api/v1/admin/members?page=1&pageSize=5", {
  credentials: "include",
  cache: "no-store",
  headers: { "X-Admin-Search": encodeURIComponent(search) },
});
// X-Admin-Search: %EA%B9%80%ED%95%98%EB%A3%A8
```

목록 조회 서버는 한 번만 엄격하게 디코딩하고 양끝 공백을 제거한다. 디코딩 전 ASCII 헤더 최대 3,600자, 디코딩 후 최대 300자다. 중복 헤더, 잘못된 `%` escape, 유효하지 않은 UTF-8, 제어 문자, 목록 URL의 `q`는 422로 거부한다. 오류에 입력 검색어를 되돌려주지 않는다. `%`, `_`, escape 문자도 SQL wildcard가 아닌 문자 그대로 검색하며 검색값은 SQL parameter로 전달한다.

회원 검색 대상은 닉네임·이메일이다. 탈퇴 회원의 원본 닉네임·이메일은 검색에서도 제외한다. 신고·문의 검색 대상은 표시 ID, 콘텐츠 제목, 정형 신고 사유, 유형명이다. 제출자 신원과 상세 본문은 목록 검색에 사용하지 않는다.

목록 건수·페이지는 각각 단일 SQL statement에서 읽어 동시 등록·변경 시 snapshot이 어긋나지 않게 한다. 범위를 벗어난 페이지도 실제 `total`과 빈 `items`를 반환한다. 회원은 가입 시각과 ID로, 신고·문의는 접수 시각·레코드 유형·ID로 순서를 결정한다.

근거: [검색 헤더](../backend/app/api/admin_search.py), [회원 조회](../backend/app/services/admin_member_service.py), [신고·문의 조회](../backend/app/services/admin_support_service.py).

## 회원 응답과 개인정보

회원 필터는 실제 DB 값만 사용한다.

| 항목 | 값 |
| --- | --- |
| `status` | `active`, `warned`, `suspended`, `banned`, `withdrawn`; 생략하면 전체 |
| `provider` | `local`, `kakao`, `naver`, `apple`; 생략하면 전체 |
| `sort` | `recent` 기본값, `oldest` |

목록 응답은 `{items,page,pageSize,total}`이다. 각 항목은 `id`, `nickname`, `maskedEmail`, `provider`, `domain`, `joinedAt`, `lastLoginAt`, `status`만 제공한다. bigint 식별자는 JavaScript 정밀도 손실을 막기 위해 **십진 문자열**로 반환한다. `domain`은 현재 활성 Tap과 활성 Topic의 실제 이름이다.

`maskedEmail`은 서버에서 이메일 앞부분을 최대 두 글자만 보여주며 나머지를 마스킹한다. 한 글자 계정명도 그대로 드러내지 않는다. 원본 이메일, 공급자 사용자 ID, 비밀번호·세션 비밀값, 탈퇴 사유, 생년은 응답에 포함하지 않는다.

`withdrawn_at`이 있거나 상태가 `withdrawn`이면 `id`, `nickname: "탈퇴 회원"`, `status: "withdrawn"`만 유지한다. 이메일·공급자·분야·가입일·최근 로그인과 상세 개인정보는 `null`이다. 공급자 필터로 과거 공급자를 추론할 수도 없다.

`lastLoginAt`은 실제 `user_account.last_login_at`이다. **최근 로그인이지 최근 접속·활동 시각이 아니다.** FE에도 “최근 로그인”으로 표시한다. 실제 저장되지 않는 `dormant`나 포괄적인 `restricted` 상태를 생성하지 않는다.

상세에는 다음 필드를 추가한다.

| 필드 | 의미 |
| --- | --- |
| `onboarding` | 현재 활성 Tap에 저장된 v2 작품·장르·소재 선택의 실제 label과 소비 유형. `{works,genres,themes,contentTypes}` |
| `savedCount` | 활성 Tap·Topic 및 active/nondeleted 콘텐츠 중 `kept` 또는 `resurface_off` 저장의 distinct 콘텐츠 수 |
| `likedCount` | 같은 활성 범위에서 `is_liked=true`인 distinct 콘텐츠 수 |
| `history`, `notes` | 저장소가 아직 없는 닉네임 변경 이력·관리 메모. 항상 `null` |

v1 온보딩은 현재 화면과 문항이 다르므로 임의로 v2 답변으로 변환하지 않고 `onboarding: null`을 반환한다. 미작성도 `null`이다. 실제로 관측한 저장·좋아요 0건은 `0`, 탈퇴로 감춘 수치나 미수집 항목은 `null`로 구분한다. 회원 통계 카드, CSV 내보내기, 변경·제재·메모 쓰기는 이번 범위에 없다.

근거: [회원 스키마](../backend/app/schemas/admin_members.py), [회원 서비스](../backend/app/services/admin_member_service.py).

## 신고·문의 응답과 상태

통합 목록의 `kind`는 `all|content|service|bug`, 문의 전용 목록은 `all|service|bug`다. `status`는 `all|waiting|reviewing|completed`이며 기본값은 `all`이다. `period`는 `7d` 기본값, `30d`, `3m`, `6m`을 지원한다. 각각 요청 시각 기준 UTC 7·30·90·180일의 **`[periodStart, periodEnd)`** 구간이며 달력 월 단위가 아니다.

목록 항목에는 `id`, `recordId`, `recordType`, `kind`, `title`, `reasonCode`, `reason`, `status`, `rawStatus`, `receivedAt`, `updatedAt`이 있다. 목록에는 상세 제출 본문이나 제출자 식별정보를 넣지 않는다. 접수·갱신 시각은 timezone이 있는 실제 DB timestamp다.

| DB `rawStatus` | 표시·필터용 `status` | 보존해야 하는 의미 |
| --- | --- | --- |
| `received` | `waiting` | 접수 |
| `reviewing` | `reviewing` | 검토 중 |
| `resolved` | `completed` | 해결 |
| `rejected` | `completed` | 반려 |
| `answered` | `completed` | 답변 완료 상태 |
| `closed` | `completed` | 종료 |

`completed` 전체를 “해결”로 바꾸지 않는다. FE는 `rawStatus`도 표시한다. 신고와 문의 테이블의 숫자 ID는 충돌할 수 있으므로 `report_id=1`은 `id: "R-1", recordId: "1", recordType: "report"`, `inquiry_id=1`은 `id: "I-1", recordId: "1", recordType: "inquiry"`다. 상세 요청은 `recordType`으로 `/reports/1`과 `/inquiries/1`을 선택한다.

목록의 `statistics: {all,waiting,reviewing,completed}`는 기간·유형·검색 조건을 적용한 뒤 **상태 필터를 적용하기 전** 건수다. `total`은 상태 필터까지 적용한 페이지 대상 건수다. 이 통계와 목록은 같은 SQL snapshot에서 계산한다. 전 기간 대비 증감률은 계산하지 않는다.

신고 상세에는 제출 내용 `message`와 해당 콘텐츠의 본문·출처·제작 유형 등을 추가한다. 문의 상세는 `message`를 제공하되 `target`·`content`는 `null`이다. 상세 본문에는 제출자가 적은 개인정보가 있을 수 있어 관리자 권한 안에서만 조회한다. 문의의 `answer`는 `null`, `answerAvailability`는 `unavailable`이다. DB에 답변 본문 저장소가 없으므로 `answered` 상태만 보고 답변 내용을 생성하지 않는다.

탈퇴 회원의 문의는 목록과 상세에서 제외한다. 신고는 제출자 신원을 노출하지 않고 보존된 신고를 조회한다. “검토 시작” 버튼은 연결된 콘텐츠를 읽는 동작이며 검토 상태를 변경하지 않는다.

근거: [지원 스키마](../backend/app/schemas/admin_support.py), [지원 서비스](../backend/app/services/admin_support_service.py), [상세 패널](../frontend/components/admin/report-detail-panel.tsx).

## FE 연결과 검증

`/admin/users`는 회원 목록, `/admin/users/{memberId}`는 회원 상세, `/admin/reports`는 신고·문의 통합 목록과 상세에 연결한다. 현재 FE 페이지 크기는 5다. [회원 client](../frontend/lib/admin-members.ts)와 [신고·문의 client](../frontend/lib/admin-reports.ts)는 문자열 ID·실제 enum·탈퇴 redaction·응답 구조를 검증한다. [공통 읽기](../frontend/lib/admin-read-contract.ts)는 쿠키·timeout·오류 처리를 제공한다. [읽기 hook](../frontend/components/admin/use-admin-read.ts)은 요청 취소와 권한 변경 후 이전 데이터 정리를 담당한다.

BE 검증은 아래 명령으로 재현한다. `backend` 디렉터리에서 해당 프로젝트 Python 가상환경을 사용한다. `ADMIN_POSTGRES_TEST_URL`은 **별도로 생성한 빈 loopback 테스트 PostgreSQL** 주소로 치환한다. 일반 `DATABASE_URL`은 연결할 수 없는 dummy 값으로 둔다. PostgreSQL fixture는 `admin_be_test` 또는 `admin_be_test_<영소문자·숫자>` DB명, loopback, URL query 없음, public Migration 미적용을 검사하고 테스트마다 임시 schema를 생성·정리한다. 운영·Checking DB 주소를 넣지 않는다.

```powershell
$env:DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
$env:REDIS_URL=''
$env:SESSION_BACKEND='postgres'
$env:ENABLE_DEV_AUTH_BYPASS='false'
$env:ADMIN_POSTGRES_TEST_URL='postgresql+psycopg://<test-user>:<test-password>@127.0.0.1:<test-port>/admin_be_test_support'
$testFiles = @(
  'tests/test_admin_resource_permissions.py',
  'tests/test_admin_members.py', 'tests/test_admin_members_api.py', 'tests/test_admin_members_postgres.py',
  'tests/test_admin_support.py', 'tests/test_admin_support_api.py', 'tests/test_admin_support_postgres.py'
)
python -m pytest @testFiles -q
```

검증 대상은 역할별 허용·즉시 회수, 리소스 권한 분리, malformed/Unicode 검색, bigint, literal wildcard, 개인정보 최소화, 빈 페이지·404 구분, 실제 Migration/AsyncSession, READ COMMITTED에서 동시 commit에도 유지되는 목록·건수·통계 snapshot이다.

2026-11-23 로컬 검증에서 위 7개 수트와 기존 관리자 권한·분석·콘텐츠·검수 범위 4개 수트를 함께 실행해 **264개 통과, skip 0개**를 확인했다. 실제 PostgreSQL 17에 기존 Migration을 적용한 격리 schema로 수행했으며 서비스 DB는 사용하지 않았다. 기존 4개 수트는 `test_admin_permissions.py`, `test_admin_analytics_api.py`, `test_admin_content_api.py`, `test_admin_review_scope.py`다.

FE 계약 검증은 `frontend` 디렉터리에서 실행한다.

```powershell
node --test tests/admin-members.test.cjs tests/admin-reports.test.cjs tests/admin-member-support-api.test.cjs
```

실제 브라우저 → HTTP → 권한 Provider → 격리 PostgreSQL 경로는 [전용 fixture 서버](../backend/tests/serve_admin_member_support_fixture.py)와 [브라우저 검사](../frontend/tests/admin-member-support-browser-check.cjs)로 검증한다. fixture는 테스트용 세션·데이터·제어 endpoint를 임시 schema에만 만들며 일반 앱 실행에는 등록하지 않는다.

```powershell
# 별도 BE 터미널: 위 테스트 DB 환경을 설정한 상태, backend 디렉터리
$env:FRONTEND_ORIGINS='http://127.0.0.1:3221'
python tests/serve_admin_member_support_fixture.py --port 8199

# 별도 FE 터미널: frontend 디렉터리
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8199'
npm run build -- --webpack
npm run start -- --hostname 127.0.0.1 --port 3221

# 검사 터미널: frontend 디렉터리, 설치된 Playwright를 사용
$env:ADMIN_READ_FIXTURE_URL='http://127.0.0.1:8199'
node tests/admin-member-support-browser-check.cjs http://127.0.0.1:3221 ../.preview-pivot/admin-member-support/results
```

Playwright가 별도 위치에 설치되어 있으면 `PIVOT_PLAYWRIGHT_MODULE`에 그 모듈 경로를 지정한다. 기본 브라우저는 설치된 Chrome이며 `PIVOT_BROWSER_EXECUTABLE`로 실행 파일을 지정할 수 있다. 브라우저 검사는 loopback 이외 트래픽을 거부하고 1728px·390px 화면, 개인정보 검색, ID 충돌, 상세 선택 경합, 권한 회수, 503·빈 응답, 역할별 접근을 확인한다. 결과 JSON과 화면 캡처는 지정한 로컬 결과 디렉터리에 남긴다.

두 화면 크기의 실제 HTTP 통합 검사를 모두 통과했다. 테스트는 API 응답을 가로채서 만들지 않고 실제 세션·권한 Provider·PostgreSQL을 거친다. viewer/operator/admin의 정상 조회, 일반 회원 403, 미인증 401, 상세 선택 경합 및 조회 중 권한 회수 후 목록·상세 정리를 확인했다. 결과는 `.preview-pivot/admin-member-support/results/results.json`과 10개 화면 캡처에 남겼다. 프론트 타입 검사와 webpack 프로덕션 빌드도 통과했다. 이 결과는 합성 데이터 기반 로컬 통합 검증이며 Checking 배포·실계정 E2E 결과를 의미하지 않는다.

전체 `npm test`는 **785개 통과, 실패 0개, 기존 선택 테스트 1개 skip**으로 완료했다. 이 중 이번 회원·지원 조회 관련 테스트는 37개다. 기존 공통 요청의 제한 시간 기능을 테스트 VM에서도 실행하도록 타이머와 모듈 의존성 설정을 보완했으며 consumer 서비스 코드는 변경하지 않았다. 전체 결과는 `.preview-pivot/admin-member-support/frontend-tests.log`에 남겼다.
