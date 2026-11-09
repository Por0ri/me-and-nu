# MY PAGE 알림 설정 — FE 준비 및 API·DB 계약 초안

작성일: 2026-11-09 (Asia/Seoul). **아래 정책·필드·기본값·오류 코드는 제안이며 팀 확정값이 아니다.** 이번 변경은 FE와 테스트·문서에 한정한다. BE·DB·Migration·실서버 설정 저장은 수정하거나 실행하지 않았다.

## 1. 기준과 보존

- Repository: `Por0ri/me-and-nu`; worktree: `C:\dev\me-and-nu-nav-fidelity-1105`.
- 시작: `codex/detail-report-inquiry-1109`, HEAD `dc9a9eac15046e5e173ac363750924b0ac906311`, tracked/untracked 작업 변경 없음.
- `git fetch origin Checking` 후 `origin/Checking` = **`ca25947b531ce061bc77780e92f51707e7842a81`**. 기대 SHA 일치. 최상위 커밋은 PR #41 병합(`cde002a`, `dc9a9ea` 부모).
- 위 기준에서 `codex/my-page-notification-1109` 생성. 기존 정책·신고 브랜치와 커밋을 수정하지 않았다.
- Git 제외 로컬 원문 `frontend/dev/policy-review/team-policy-2026-10-23.md` 보존. 내용은 보고서에 복사하지 않음. 원문 SHA-256: `DACD5F1A169B7CC6D774671C6E0D2C330BB910C552A6465DE8C90BBF8EA987F2`.
- 최초 준비 작업 종료 시점에는 Commit·Push·PR·Merge·배포를 수행하지 않았다. 이후 로컬 Commit 검수는 별도 요청에 따른다. 연결된 운영 DB는 조회하지 않았다. DB 상태는 ORM·Alembic·DDL 스냅샷 기준이다.

## 2. 확인한 현재 구현

| 영역 | 현재 코드의 사실 | 의미 |
| --- | --- | --- |
| Pivot Service MY | `frontend/components/pivot/my-page-screen.tsx`에서 두 토글이 비활성. 기존에는 OFF 그림과 클릭 시 준비 중 안내 | 사용자 저장값을 조회한 결과가 아님 |
| Pivot Demo MY | 화면 내부 로컬 상태, 상위 ON·하위 OFF로 시작 | 영속 저장·브라우저 알림 권한·실제 세션 사용 없음 |
| 설정 API | `backend/README.md`에 API-068·069 계획 경로만 존재. `backend/app/api/v1/router.py`에 설정 라우트 등록 없음 | 조회·변경 어댑터 연결 불가 |
| 알림함 API | `backend/app/api/v1/users/me/notifications.py`: API-107 목록, API-108 미확인 수, API-109 단건 읽음 구현 | 설정 API가 아님. `GET /notifications`, `GET /notifications/unread-count`, `PATCH /notifications/{notificationId}` |
| 알림함 서비스 | `backend/app/services/notification_service.py`: 현재 `system_notice`만 목록·개수·읽음 대상, 첫 조회 때 환영 공지 생성 | 알림 생성·외부 푸시 발송 제어와 구분. 이 서비스에는 설정을 적용하는 발송 경계가 없음 |
| 설정 DB | `backend/app/models/v1.py`의 `NotificationSetting`, `docs/erd/current_schema.sql` | `user_id` PK/FK(CASCADE), `report_result=true`, `creator_new_post=true`, `saved_resurface=true`, `marketing=false`, created/updated_at |
| 설정 행 생성 | `backend/app/services/onboarding_service.py`에서 온보딩 완료 시 기본 행 생성 | 과거/누락 행 존재 여부는 별도 DB 점검 필요 |
| 동의 | `backend/app/api/v1/users/me/consents.py`, `policy_service.py`, `ConsentHistory` | 정책 버전과 동의·철회 이력을 별도로 관리. 설정 토글로 동의 이력을 바꾸면 안 됨 |

기존 필드 중 어느 것도 전체 허용·21시 이후 허용을 뜻하지 않는다. `report_result`, `creator_new_post`, `saved_resurface`, `marketing`을 새 스위치에 재해석하거나 조합하지 않았다. 이전 `FIGMA_API_V1_1_IMPLEMENTATION_BACKLOG.md`의 “107~109 없음” 문장은 과거 기록이며, 현재 알림함 API 구현과 구분해야 한다.

레거시 `frontend/components/my/my-page-screen.tsx`에도 로컬 토글이 있지만 이번 대상은 최신 Pivot MY이다. 레거시 화면은 수정하지 않았다.

## 3. 최신 Figma 비교와 접근성

[범수님 / 알림 설정 1363:5753](https://www.figma.com/design/iLGLjporSKRHdHBdhsKEvV?node-id=1363-5753)을 2026-11-09 Figma MCP 디자인 컨텍스트와 렌더 이미지로 확인했다.

| 항목 | 최신 디자인 | FE 처리 |
| --- | --- | --- |
| 문구·샘플 | “알림 허용” ON, “21시 이후 알림 허용” OFF | Demo 샘플만 유지. Service 기본값으로 해석하지 않음 |
| 카드 | 327px 기준, 흰색, radius 8px, #e0bafb 4px 그림자 | 기존 `.card` 재사용 |
| 제목·행 | 11px/16px 제목, 12px/18px 행, 행 높이 32px, 좌우 24px | 기존 스타일 재사용, 화면 너비에 반응 |
| 스위치 | 33×18 SVG, ON #9c79e6 / OFF 회색 | 기존 `/pivot/my/switch-{on,off}.svg` 재사용. 최신 다운로드와 LF 정규화 SHA-256이 각각 일치 |
| 구분선 | 첫 행 아래 .6px #e4e4e7 | 기존 `.row` 규칙 재사용 |
| 조작·보조 설명 | Figma는 시각 샘플 | native button + role=switch, aria-checked, aria-disabled, aria-describedby, focus-visible. 행 전체 조작 영역, Enter/Space 지원 |

Figma 스위치 ON SHA-256: `989eafb635201fcc4eb7de67063c9a602a16e611ca146cc6d6d73aeb0fa216e3`; OFF: `495367bb7706228c4dc954ec20e31955a3cfb71bc125be877b2c0e1c196d4f97`.

Service의 OFF 그림은 미확인 상태의 비활성 placeholder다. 실제 OFF로 오해하지 않도록 “현재 설정값을 확인하거나 변경할 수 없어요”를 화면과 접근성 설명으로 제공한다. 스위치의 ARIA checked는 mixed를 표현하지 않으므로 false placeholder와 설명을 함께 사용한다. 준비·로딩·오류·저장 중 설명은 디자인 외 보완이다. 실제 화면 높이는 설명만큼 증가한다. 32px 행은 기존 디자인을 유지했으며 44px 행으로의 확대 여부는 디자인 논의 항목이다.

## 4. 이번 로컬 FE 변경

| 파일 | 변경 |
| --- | --- |
| `frontend/lib/pivot/notification-settings.ts` (신규) | FE 전용 모델·source 경계·요청 controller. 미연동/로딩/준비/저장 중/오류/세션 만료 상태 |
| `frontend/components/pivot/my-notification-settings.tsx` (신규) | 기존 스위치·행 재사용, Demo 로컬 상태 분리, Service 상태 안내·오류 재조회 UI |
| `frontend/components/pivot/my-page-screen.tsx` | 기존 두 스위치 인라인 코드를 설정 section으로 분리 |
| `frontend/components/pivot/my-page-controller.tsx` | 세션에 귀속된 프로필 source를 설정 scope로 전달 |
| `frontend/components/pivot/my-page.module.css` | 설정 전용 포커스·상태·재조회 스타일 |
| `frontend/tests/pivot-notification-settings.test.cjs` (신규) | 미연동 차단·저장 잠금·실패·검증·취소·세션 교체·Demo 격리 테스트 |
| `frontend/tests/pivot-notification-settings-browser-check.cjs` (신규) | 로컬 375/390px Demo·Service 조작/접근성/자산/미호출 검증. 모든 API를 메모리 fixture로 처리 |
| `docs/MY_PAGE_NOTIFICATION_FE_API_PLAN_2026-11-09.md` (신규) | 본 계약·개발 계획·검증 보고 |

`allowed`, `after21Allowed`는 **FE view model 이름**이며 wire DTO나 DB 필드 확정이 아니다. 실제 HTTP adapter, URL 호출 함수, 활성화 환경변수는 추가하지 않았다. 앱은 `available: false` source만 사용한다. `available: true` 경계는 합의 후 어댑터를 넣기 위한 구조이며 현재는 테스트에서만 사용한다.

- Demo: 각각의 토글만 메모리에서 변경. 상하위 연동 정책을 새로 만들지 않음. 이탈·새로고침·재마운트 시 초기화.
- Service: 준비 중 설명, 두 스위치 조작 차단. 설정 조회·변경·동의·알림함 읽음 요청을 보내지 않음. 저장 성공 메시지 없음.
- 향후 adapter: 최초 GET 완료 전 조작 차단. 저장 중 확인된 이전 값 유지, 중복 PATCH·저장 중 재조회 차단. 검증된 응답만 반영.
- 저장 실패: 서버 반영 여부가 불명확하므로 값을 미확인으로 전환하고 “설정 다시 불러오기”는 GET 수행. 자동 PATCH 재전송·성공 추정·롤백 주장 없음.
- 세션: source/scope 변경 시 render 단계부터 이전 값 숨김. cleanup AbortSignal·disposed 경계로 늦은 응답 무시. 어댑터는 같은 userId로 세션이 교체되는 경우도 `isCurrent()`로 거부해야 함.
- 취소는 서버 commit 취소를 보장하지 않음. 재진입은 반드시 최신 GET으로 시작.
- 데이터·오류 원문·개인 설정을 localStorage/sessionStorage에 저장하지 않음. 오류 표시는 고정된 사용자 문구.

## 5. 정책 결정표 — 모두 제안, 승인 전 구현 금지

| 결정 항목 | 권장안 | 대안/확인할 점 |
| --- | --- | --- |
| “알림 허용”의 범위 | 선택적 **외부 푸시 수신**의 master preference. 내부 알림함 기록·읽음은 유지 | 앱 내 배지/실시간 안내까지 끌지, 시스템 필수 알림 예외가 있는지. 푸시 전용이면 라벨/설명도 그 범위를 밝혀야 함 |
| “21시 이후” | 고정 정책이라면 `Asia/Seoul`의 **[21:00, 다음날 08:00)**를 야간으로 제안 | 종료 시간은 Figma에 없음. 08:00은 제안일 뿐. 사용자 지역/기기 시간대인지, 여행·DST 정책도 결정 필요 |
| 시간 판정 | 서버 실제 발송 시각에 IANA 시간대로 판정. 저장 timestamp는 UTC | 이벤트 생성 시각 기준이면 대기열에서 야간 제한을 우회할 수 있음. 20:59:59/21:00/07:59:59/08:00 경계 테스트 |
| 앱 내 알림 vs 외부 푸시 | inbox 생성/조회/읽음과 push delivery를 분리. 브라우저/OS 권한과 서버 preference도 분리 | OS 권한이 denied여도 서버 설정이 동의로 바뀌지 않음. 구독 토큰/기기별 권한·전송 구현은 별도 범위 |
| 마케팅 동의 | 기존 consent history가 권한 근거. 알림 ON만으로 마케팅 동의·야간 광고 동의를 부여하지 않음 | 기존 `marketing` 필드의 향후 역할(채널 preference인지 폐기할지), 야간 마케팅 별도 동의 필요 여부를 담당 정책 검토로 확정 |
| 신규/기존 기본값 | 명시적 선택 근거가 없다면 push OFF·night OFF를 제안 | 기존 동작 보존·온보딩 동의 의미·안내/재동의 여부 확인. Figma ON 예시나 종류별 true에서 추론 금지 |
| 상위 OFF | 하위 저장 선택은 유지하되 효력은 false, UI 조작 비활성. 재ON 시 이전 하위 선택 복원 | 하위를 강제 false로 지울지 팀 결정 필요. 이번 Demo에는 이 정책 미적용 |
| 야간 제한 중 이벤트 | inbox는 유지. 푸시는 종료 시점에 TTL/중복/묶음 정책에 따라 재검사 후 전달하는 방안 제안 | 즉시 폐기 대 다음날 지연, 만료 콘텐츠, 마케팅, 필수 알림 예외와 재개 폭주 제한 결정 |
| 계정 상태 | 비활성/탈퇴 유예 계정은 발송 직전에도 제외 | 복구 후 이전 preference 복원 여부와 30일 보관/삭제 정책 연계 |

최종 발송 판단은 master + 야간 설정 + 알림 종류별 preference + 필요한 별도 consent + 기기 구독/권한 + 계정 상태의 교집합으로 설계한다. 설정 API만 만들어서는 실제 푸시 제어가 완성되지 않는다. 현재 알림함 조회 서비스에 master OFF 필터를 넣는 방식은 위 권장안과 맞지 않는다.

## 6. API-068·069 계약 제안 (외부 푸시 권장안 채택 시)

기존 계획 경로 유지:

```http
GET /api/v1/users/me/notification-settings
Cookie: <기존 HttpOnly 세션>
```

Request body/query 없음. 200, `Cache-Control: no-store`, `ETag: "1"` 예시:

```json
{
  "pushEnabled": false,
  "nightPushEnabled": false,
  "quietHours": { "start": "21:00", "end": "08:00", "timeZone": "Asia/Seoul" },
  "version": 1,
  "updatedAt": "2026-11-09T07:00:00Z"
}
```

위 boolean과 시각은 예시값이다. `quietHours`는 서버 정책을 설명하는 읽기 전용 metadata. 정책 버전을 응답에 추가할지는 운영 정책 변경 방식과 함께 결정한다. 스위치 의미가 push 이외까지 확대되면 `pushEnabled` 이름·저장 모델·발송 적용 범위를 다시 검토한다.

```http
PATCH /api/v1/users/me/notification-settings
Content-Type: application/json
Cookie: <기존 HttpOnly 세션>
X-CSRF-Token: <현재 세션 토큰>
If-Match: "1"

{"pushEnabled": true}
```

- 최소 한 필드를 포함한 부분 변경. `pushEnabled`, `nightPushEnabled`는 strict boolean만 허용. 생략은 유지, null/문자열/숫자/빈 body/알 수 없는 key는 422. `marketing`, `userId`, `quietHours`, `updatedAt`, body의 `version`도 쓰기 불가.
- 두 필드 동시 PATCH 허용, 원자적 변경. 상위 false + 하위 true는 휴면 선택으로 보존하는 권장안. 실제 야간 push 효력은 항상 상위 AND 하위 조건.
- 저장된 값 전체를 GET과 같은 shape로 200 반환, 갱신된 ETag 포함. 예: 위 요청의 응답은 pushEnabled=true, nightPushEnabled=false, version=2, 서버 updatedAt.
- 두 번째 스위치 예: `If-Match: "2"` + `{"nightPushEnabled": true}` → 동일 shape, 두 값 true, version=3. 값이 실제 바뀌지 않은 PATCH는 기존 version/updatedAt 유지 제안.
- version 비교와 update는 같은 transaction/행 lock 또는 조건부 UPDATE로 처리. 동시 다중 탭 lost update 방지. stale If-Match는 412, 헤더 누락은 428 제안. FE는 재조회 후 사용자가 다시 변경하도록 함.
- GET은 설정 행 생성/수정을 하지 않음. Migration backfill과 온보딩 초기화로 행 존재를 보장. 누락 행은 409 `NOTIFICATION_SETTINGS_NOT_INITIALIZED` 제안, 운영 복구 후 재조회. 이 처리 방식도 확정 필요.
- 기존 `apiRequest`의 camelCase, credentialed fetch, no-store, CSRF, abort/401 이벤트를 재사용. 교차 origin이면 CORS의 허용 헤더 `If-Match`/`X-CSRF-Token`, 노출 헤더 `ETag` 검토 필요. 현재 공통 helper는 JSON만 반환하므로 adapter는 response version으로 If-Match를 만들거나 헤더 노출 지원을 별도 설계한다.

### 인증·권한·오류

GET/PATCH 모두 기존 `require_onboarding`으로 active 세션과 완료된 온보딩을 요구. 소유자는 세션의 user_id만 사용하고 클라이언트 ID는 받지 않는다. 기존 `require_onboarding`은 account_type을 제한하지 않으므로 **Consumer 전용 여부는 명시적 결정 후 별도 검사**가 필요하다. 권장안은 user 계정만 허용(그 외 403). admin API를 만들거나 관리자 화면과 공유하지 않는다.

PATCH에는 기존 `require_csrf` 재사용: 세션 결합 CSRF token, 허용 Origin 검사. 임의로 세션 검증을 우회하거나 쿠키·token을 로그에 남기지 않는다.

| HTTP | 기존/제안 코드 | FE 반응 |
| --- | --- | --- |
| 401 | 기존 `AUTH_REQUIRED` | 세션 무효화, 값 제거, 기존 로그인 흐름. 자동 저장 재시도 없음 |
| 403 | 기존 `CSRF_INVALID`, `ONBOARDING_REQUIRED`; 제안 `FORBIDDEN` | 입력 잠금/세션 상태 재확인. CSRF를 갱신해도 이전 PATCH 자동 재전송 금지 |
| 409 | 제안 `NOTIFICATION_SETTINGS_NOT_INITIALIZED` | 기본값을 지어내지 않음. 일반 오류 안내 및 GET 재시도 |
| 412 / 428 | 제안 `NOTIFICATION_SETTINGS_CONFLICT` / `PRECONDITION_REQUIRED` | 최신 GET 후 사용자가 변경하도록 안내 |
| 422 | 기존 `VALIDATION_ERROR` | 검증 실패 표시, 잘못된 값을 성공으로 보이지 않음 |
| 429 / 5xx / network | 실제 제공 코드 / 일반 실패 | 자동 PATCH 재시도 없음. 결과 불명확이면 GET으로 재확인, rate limit 시 서버 지연 준수 |
| 404 / 405 | 미구현/경로 불일치 | 성공·기본값으로 대체하지 않음. 출시 전 어댑터 활성화 차단 |

오류 envelope는 기존 `ErrorResponse`의 `{code, message, fields?, requestId?}`를 재사용한다. 제안 오류 코드는 아직 서버에 존재하지 않는다. 이번 FE controller는 일반 사용자 오류/재조회 UI까지만 준비했고, HTTP별 분기·인증 오류 매핑은 실제 adapter 후속 작업이다.

## 7. BE·DB 구현 범위 (이번에는 수정하지 않음)

기존 `notification_setting`의 user_id 1:1 관계와 created/updated_at, 종류별 기존 필드는 재사용 가능하다. 새 master/night 의미는 기존 boolean 재사용 불가. 아래는 정책 승인 뒤의 migration 후보다.

| 후보 | 목적/적용 조건 |
| --- | --- |
| `push_enabled BOOLEAN` | master preference 신규 필드. 기본값·backfill 명시적 승인 필요 |
| `night_push_enabled BOOLEAN` | 야간 허용 신규 필드. master OFF에도 저장 선택 보존 |
| `version INTEGER` | optimistic concurrency, 초기 1, 변경 때 원자 증가 |
| 사용자 timezone/quiet 시작·끝 | 전 사용자 고정 KST면 DB에 불필요. 개인화 정책을 채택할 때만 추가 |
| 동의 schema 확장 | 야간/마케팅 동의 분리가 필요하다고 결정되면 별도 이력/정책으로 설계. 설정 boolean으로 동의 증거 대체 금지 |

Migration 순서 제안: 기존 행/누락 사용자 분포 read-only 점검 → 명시적 기본값 승인 → 새 열을 우선 nullable로 추가 → 승인된 값으로 backfill 및 누락 행 생성(기존 category/marketing/consent 값 보존) → NOT NULL/default/version 제약 적용 → 온보딩 생성 일치 → API 배포/검증 → 발송 계층 적용 → FE adapter 활성화. 단일 migration으로 할지 단계 배포할지는 실제 데이터량/잠금 영향에 따라 결정한다. downgrade 때 신규 선택값 소실 가능성 및 백업·rollback 방안도 검토한다.

| 예상 BE 파일 | 담당 내용 |
| --- | --- |
| `backend/app/api/v1/users/me/notification_settings.py` 신규 | GET/PATCH, session/CSRF/Consumer 권한, If-Match 처리 |
| `backend/app/api/v1/router.py` | 새 라우터 등록 |
| `backend/app/schemas/notification_settings.py` 신규 | CamelModel 기반 응답, strict/extra forbid 부분 PATCH, metadata/오류 |
| `backend/app/services/notification_settings_service.py` 신규 | 자기 행 조회·version 검증·원자 업데이트·누락 행 처리 |
| `backend/app/models/v1.py` | NotificationSetting 신규 열 |
| `backend/app/services/onboarding_service.py` | 승인된 초기화 값 및 새 행 invariant |
| `backend/alembic/versions/<new_revision>_notification_preferences.py` 신규 | backfill/제약/upgrade·downgrade. 기존 migration 수정 금지 |
| `backend/tests/test_notification_settings_api.py` 신규 | 인증·CSRF·권한·strict PATCH·누락 행·동시 변경·영속 재조회·세션 만료 |
| `backend/tests/test_alembic.py`, 온보딩 관련 테스트 | upgrade/downgrade·기존 설정/동의 보존·새 초기값 |
| `backend/README.md`, `docs/erd/current_schema.sql` | 구현 후 실제 계약/OpenAPI와 DDL 재생성 |
| 실제 push dispatch service/worker (경로 미정) | 발송 직전 정책/consent/계정/시간대 재확인. 새 구현 범위·담당자 합의 필요 |

이번에 찾은 `notification_service.py`는 inbox 담당이므로 외부 푸시 worker로 가정하지 않는다. 그 파일의 목록·읽음 기능에 새 설정을 억지로 적용하지 않는다.

후속 FE 예상 파일: `frontend/lib/api.ts`(GET/PATCH 타입·함수), `frontend/lib/pivot/notification-settings-service.ts` 신규(adapter·응답 projection·세션 guard), `home-route.tsx`/`home-detail-experience.tsx`/`my-page-controller.tsx`(session-bound source 전달), `my-notification-settings.tsx`(합의된 하위 잠금·권한 안내), `notification-settings.ts`(최종 오류·정책 처리), 관련 unit/browser tests. 실제 push 구독/OS 권한 화면은 별도 범위.

PM + FE + 일부 BE 담당자가 정책 표와 API 계약을 팀에서 확인한 뒤 위 BE 라우트·schema·service·테스트를 구현할 수 있다. 다른 팀원이 만든 inbox/프로필·동의 API는 재사용 경계를 명확히 하고 충돌 없이 연동한다.

## 8. 팀 결정 및 출시 조건

1. PM/디자인: 전체 허용 범위, UI 라벨, 필수 알림 예외, inbox/배지 동작.
2. PM/BE: 야간 종료 시간·시간대·여행/DST·발송 시점 판정, 지연/폐기/TTL.
3. 정책 담당: 마케팅과 야간 동의 구분, 기존 consent/marketing 필드 역할.
4. PM/BE: 기존 사용자·신규 사용자 default와 backfill, master OFF 시 하위 보존 방식.
5. FE/BE: DTO 필드, 읽기 전용 metadata, version/If-Match, 누락 행·오류·Consumer 권한.
6. BE: 실제 외부 푸시 채널·토큰 수명·worker·발송 정책 적용 담당과 테스트 환경.
7. 합의/BE 구현/migration/정책 적용/테스트 완료 후에만 FE HTTP adapter를 연결한다. 단순 환경변수로 현재 화면을 활성화하지 않는다.

## 9. 후속 의존성·발견 사항

- **알림 출시 Blocker:** API-068·069, 두 스위치 의미·DB 필드·발송 적용 계약이 아직 없음. 이번 준비 작업의 오류가 아니라 기능 활성화 선행 조건이다.
- **회원탈퇴 Major (기존 문서 불일치 추가 확인):** 확정 정책은 신청 즉시 비활성화 → 30일 후 삭제, 기간 내 복구 가능. 현재 `users/me/router.py`·`schemas/withdrawal.py`·`withdrawal_service.py`는 immediate 삭제만 구현하며 route description/query에는 오래된 **3개월 유예** 문구가 남아 있다. 3개월은 현재 확정 정책이 아니다. 이번에는 BE나 타 기능을 고치지 않았다.
- 탈퇴 후속: 30일 예약 삭제 시각·비활성화 transaction, 모든 기기 세션/푸시 차단, 기간 내 복구 API와 재인증 경로, 삭제 job/재실행 안전성, 보관·복구 시 설정/동의 처리부터 구현·검증해야 한다. FE는 기존 immediate 삭제 API를 연결하지 않았다.
- 관리자: 대시보드·회원 관리는 BE의 1차 시연 디자인 초안이다. 제품 범위·권한·감사·계정 상태 API가 확정된 뒤 별도 착수. 이번 알림 준비의 선행 FE 작업으로 관리자 화면/API를 만들 필요는 없다.
- 로컬 환경: 기본 Turbopack은 기존 `node_modules` 외부 symlink를 root 밖으로 판단하여 시작 실패했다. 저장소 설정/의존성은 변경하지 않고 `next dev --webpack`으로 검증한다.

## 10. 검증 결과

- `npm run typecheck`: 통과.
- `node --test tests/pivot-my-page.test.cjs`: 26/26 통과.
- `node --test tests/pivot-notification-settings.test.cjs`: 9/9 통과.
- `npm test`: 전체 571/571 통과.
- Figma 두 스위치: 원본과 기존 SVG의 해시·root 33×18·비어 있지 않음 확인.
- `pivot-notification-settings-browser-check.cjs`: 375px·390px 각각 Demo/Service **4/4 통과**. 기존 Playwright 설치와 Edge headless 사용, 로컬 프론트 이외 요청은 차단하고 API는 전부 메모리 fixture 응답.
- 브라우저: Demo Enter/Space 토글, 두 선택의 독립성, 새로고침 초기화, 저장소 미변경, API 0건. Service 클릭/Enter/Space에도 두 값 유지, 상태 설명 연결, 설정·알림함·탈퇴·관리자 요청 0건, 성공 표시 없음. 테스트 전체 쓰기 요청 0건, pageerror 0건.
- 레이아웃: 카드 327px(375 viewport)/342px(390 viewport), 두 행 32px, 스위치 33×18 및 로드 성공, 가로 넘침 없음. 두 모드 375px 캡처를 직접 열어 Figma와 비교. Service의 추가 설명/포커스 표시는 의도된 차이.
- 로컬 프론트 HTTP 200 및 서버 컴파일 로그 확인. 실제 알림 설정 API 호출 경계는 의도적으로 비활성 상태이며 BE→DB/발송 경계는 미구현으로 남음.
- `git diff --check`: 통과. Backend 변경 없음. 최초 준비 검증 시점의 HEAD는 기준 SHA 그대로였다. 원문 SHA-256 동일 및 Git ignored 상태 유지.
- 증거(ignored): `agent/out/my-page-notification-1109/browser/results.json`, `demo-375.png`, `service-375.png`, `demo-390.png`, `service-390.png`. Figma 비교 SVG도 같은 검증 폴더에만 보관. 로컬 preview는 검증 후 종료.

브라우저 재현: frontend에서 `NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true`, `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8197`을 해당 프로세스에만 설정하고 `npm run dev -- --webpack --hostname 127.0.0.1 --port 3197`. 별도 터미널에서 `PIVOT_PLAYWRIGHT_MODULE`을 기존 설치 경로로 지정한 뒤 `node tests/pivot-notification-settings-browser-check.cjs http://127.0.0.1:3197 <output-directory>`. 8197에는 실제 API 서버를 띄우지 않는다. 테스트가 네트워크 요청을 가로채므로 BE/DB 쓰기가 발생하지 않는다.

실제 BE API 저장, 실제 DB migration, 푸시 발송, 실기기 OS 알림 권한은 이번 검증 범위가 아니다.
