# MY PAGE 내부 알림 설정 — 구현 계약 및 로컬 검증

2026-11-09 (Asia/Seoul). **PM의 현재 구현 기준안에 따라 로컬 구현한 계약이며 팀 전체 승인·배포 완료를 의미하지 않는다.** 이전 푸시 중심 제안은 이 문서로 대체한다. 이전 준비 작업의 기록은 Commit `be2d77614cdd8d2272cd9e13adeccc8aea5966f9`에 보존되어 있다.

## 1. 작업 기준과 제한

- Repository: `Por0ri/me-and-nu`, worktree: `C:\dev\me-and-nu-nav-fidelity-1105`.
- Branch: `codex/my-page-notification-1109`; 시작 HEAD: `be2d77614cdd8d2272cd9e13adeccc8aea5966f9`, 시작 상태 clean.
- fetch 후 최신 `origin/Checking`: `ca25947b531ce061bc77780e92f51707e7842a81` (PR #41 병합), 기존 기준과 일치.
- 새 브랜치/worktree 없이 기존 준비 Commit 위에서 작업. 최초 구현 단계에서는 Commit하지 않았다. 후속 최종 검수 요청에 따라 검증된 21개 파일만 로컬 Commit하며 Push·PR·Merge·배포는 하지 않는다.
- Checking/Production DB에는 연결하거나 migration을 적용하지 않는다. 검증 DB는 별도 Docker 컨테이너 `menu-notifications-test-1109`, loopback `127.0.0.1:15439`, DB 이름 `notification_settings_test`, tmpfs 저장소다. 기존 `me-nu-postgres`에는 접근하지 않는다.
- Git 제외 정책 원문은 수정·게시하지 않는다. `frontend/dev/policy-review/team-policy-2026-10-23.md` SHA-256은 `DACD5F1A169B7CC6D774671C6E0D2C330BB910C552A6465DE8C90BBF8EA987F2`로 유지한다. 검증 결과·임시 인증 fixture·가상환경은 ignored `agent/out/`에만 둔다.

## 2. 현재 PM 기준과 실제 구현 범위

| 항목 | 현재 기준/구현 |
| --- | --- |
| 전체 알림 | 서비스 내부(In-App) 알림 preference, 기본 ON |
| 21시 이후 | 기본 OFF, 기준 시간은 Asia/Seoul **21:00 이상~익일 08:00 미만** |
| 마케팅 | 별도 관리, 기존 기본 OFF 유지. 이미 존재하는 동의·marketing 값은 변경하지 않음 |
| 필수 운영 공지 | 설정과 무관하게 기존 알림함에서 확인 가능 |
| 기존 알림 | 스위치 OFF로 삭제하거나 숨기지 않음 |
| 외부 푸시 | Web Push·FCM·기기 구독·OS 권한은 이번 MVP 범위 제외 |
| 이번 완성 범위 | 설정 API·DB 저장·FE 실연동 |
| 아직 없는 범위 | 선택적 내부 알림 생성/표시 경로, 저장된 설정·야간 기준을 적용하는 수신 제어 |

기존 `system_notice` 중심 inbox는 그대로다. 새 이벤트나 선택적 알림 유형을 발명하지 않았다. 야간 시각을 검사하는 미사용 helper/worker도 추가하지 않았다. **설정 저장 성공은 실제 알림 수신/야간 차단 성공을 뜻하지 않는다.** 화면에도 선택적 알림 수신 제어는 준비 중이며 필수 공지는 계속 확인할 수 있다는 안내를 제공한다.

상위 OFF 시 하위 선택을 삭제하거나 강제로 OFF로 바꾸지 않는다. 저장 API는 두 boolean의 부분 변경을 독립적으로 보존한다. 하위 UI의 잠금/복원 및 실제 선택적 알림에 적용할 조합은 추후 PM·팀 결정 대상으로 남긴다. 현재 화면과 Demo에는 기존 독립 토글 동작을 유지한다.

## 3. 현재 GET/PATCH 구현 계약

경로: `/api/v1/users/me/notification-settings` (API-068/069).

### GET

로그인 및 온보딩 완료 세션으로 본인 행만 조회한다. Body 없음. userId를 받지 않으며 임의 query의 userId로 소유자가 바뀌지 않는다. 200 응답 예:

```json
{"inAppEnabled": true, "nightInAppEnabled": false, "version": 1}
```

GET은 행 생성이나 설정 version 변경을 하지 않는다. 응답은 공통 middleware의 `Cache-Control: private, no-store`를 따른다. 응답에 마케팅/종류별 설정/동의/사용자 식별자는 포함하지 않는다.

### PATCH

현재 조회한 version과 하나 이상의 boolean을 JSON body로 전달한다. 세션 쿠키와 현재 세션의 `X-CSRF-Token`이 필수다.

```http
PATCH /api/v1/users/me/notification-settings
Content-Type: application/json
X-CSRF-Token: <현재 세션 토큰>

{"version": 1, "inAppEnabled": false}
```

성공 시 200, 실제 저장된 전체 설정:

```json
{"inAppEnabled": false, "nightInAppEnabled": false, "version": 2}
```

- `inAppEnabled`, `nightInAppEnabled`: strict boolean. 생략한 값은 보존. null/문자열/숫자 불가.
- `version`: 필수 strict integer, 1~2147483647. 문자열/boolean/소수점 불가.
- 최소 하나의 설정 boolean 필요. 빈 body, version만 있는 body, unknown key, snake_case key, userId, marketing 등은 422.
- 두 boolean 동시 변경 가능. 상위 false + 하위 true도 저장 가능하며, 이를 실제 야간 수신이 허용됐다는 뜻으로 해석하지 않는다.
- SQL `UPDATE ... WHERE user_id = 세션 사용자 AND version = 요청 version RETURNING ...`으로 원자적 비교·변경한다.
- **같은 값으로 보내도 성공한 PATCH마다 version이 1 증가**한다. 동시에 같은 version으로 보낸 요청은 하나만 성공한다. UI 잠금 외에도 서버에서 충돌을 막는다.
- Commit 성공 후에만 응답한다. 실패 시 rollback. version 정수 상한에서는 증가를 시도하지 않고 충돌 오류를 반환한다.
- MVP에는 body version을 사용한다. 이전 제안의 If-Match/ETag/412/428은 채택하지 않았다. CORS/공통 HTTP client 변경을 줄이고 기존 JSON 오류 규칙을 유지한다.
- 시간대 metadata와 updatedAt은 응답에 추가하지 않았다. 야간 기준은 현재 PM 구현 기준으로 문서화했으며 수신 제어 경로에서 후속 적용해야 한다.

| HTTP | 코드 | 처리 |
| --- | --- | --- |
| 401 | AUTH_REQUIRED | 세션 없음/만료/철회/비활성 계정. FE는 값 제거·세션 흐름 복귀 |
| 403 | ONBOARDING_REQUIRED | 미완료 온보딩 접근 차단 |
| 403 | CSRF_INVALID | CSRF 또는 Origin 불일치. 자동 PATCH 재전송 없음 |
| 409 | NOTIFICATION_SETTINGS_CONFLICT | 다른 변경 또는 version 상한. FE는 값 미확인 처리 후 GET 재조회 |
| 409 | NOTIFICATION_SETTINGS_NOT_INITIALIZED | migration/온보딩 invariant에서 벗어난 누락 행. 기본값 생성으로 숨기지 않고 오류 반환 |
| 422 | VALIDATION_ERROR | strict 타입·필드·version 검증 실패 |
| 400 / 415 | MALFORMED_JSON / UNSUPPORTED_MEDIA_TYPE | 기존 공통 JSON 요청 규칙 |
| 5xx / network | 공통 오류 | 저장 여부 불명확. FE는 성공/롤백으로 단정하지 않고 GET 재조회 |

공통 `ErrorResponse`의 `{code,message,fields?}`를 사용한다. 서버는 `require_onboarding`, `require_csrf`를 재사용한다. 현재 `UserAccount.account_type`은 DB 필드가 아닌 일반 회원 `user` 속성이다. 이 작업에서 관리자 권한/새 계정 유형은 도입하지 않는다.

## 4. DB·Migration

기존 `notification_setting`의 PK/FK·report_result·creator_new_post·saved_resurface·marketing·created_at·updated_at은 보존한다. 기존 컬럼을 새 의미로 재사용하지 않는다.

| 신규 컬럼 | 정의 |
| --- | --- |
| in_app_enabled | BOOLEAN NOT NULL, DB/ORM 기본 true |
| night_in_app_enabled | BOOLEAN NOT NULL, DB/ORM 기본 false |
| version | INTEGER NOT NULL, DB/ORM 기본 1, CHECK version >= 1 |

Migration `91c7a2e4b630`의 parent는 `6d2f8b4a1c37`이며 단일 head를 유지한다.

1. 상수 server default와 NOT NULL로 새 열을 추가해 기존 행에 true/false/1을 채운다. 기존 종류별 선택·marketing·timestamp를 UPDATE하지 않는다.
2. 온보딩 완료 사용자 중 설정 행이 없는 사용자만 `INSERT ... ON CONFLICT DO NOTHING`으로 보완한다.
3. 온보딩 미완료 사용자는 선제적으로 행을 만들지 않는다. 기존 `onboarding_service.py`의 `NotificationSetting(user_id=...)` 생성이 새 ORM/DB 기본값을 자동 적용한다. 이 파일의 동작 변경은 불필요하다.
4. 설정 PATCH는 요청한 새 컬럼과 version/updated_at만 변경한다. 기존 marketing 동의가 true였던 회원을 일괄 OFF로 돌리지 않는다.
5. downgrade는 table lock 후 명시적으로 저장된 흔적(version != 1) 또는 기본값과 다른 신규 설정이 하나라도 있으면 예외로 중단한다. 설정을 다시 기본값으로 바꿔도 version이 증가했으므로 차단한다.
6. 저장 흔적이 없는 경우에만 신규 제약·열을 제거한다. 기존 행·컬럼과 backfill로 보완한 행은 삭제하지 않는다. 실제 rollback 시 명시적 설정 데이터의 보관·복원 방안은 별도 판단이 필요하다.

실제 Checking/Production 적용은 하지 않았다. 배포 전에 DB migration → BE → FE 순서와 누락 행 운영 복구 절차를 팀에서 확인해야 한다. 과거 BE 버전에서는 신규 API가 없으므로 FE만 먼저 활성화하는 배포는 피해야 한다.

## 5. FE 연동과 보존

- 기존 `notification-settings.ts` controller와 `my-notification-settings.tsx`를 재사용한다. UI 키 allowed/after21Allowed는 어댑터에서 inAppEnabled/nightInAppEnabled로 명시적으로 변환한다.
- `createServiceMyPageSource`가 동일 세션에 귀속된 notificationSettings source를 제공하고 Controller → Screen → 설정 section으로 전달한다. 준비 Commit은 보존한다.
- GET 시작 전/응답 후 현재 세션·사용자·CSRF를 재확인한다. 같은 userId의 세션 교체도 구분한다. 모든 요청은 기존 허용 origin guard와 credentialed/no-store 공통 client를 사용한다.
- PATCH 직전 세션 확인, 요청에 귀속된 CSRF 사용, 응답 후 세션 재확인. 응답 boolean과 기대 version+1을 검증한 뒤에만 “알림 설정을 저장했어요” 표시.
- 저장 중 이전 확인값 유지, 입력·중복 PATCH 잠금. 실패는 값 미확인 처리. “설정 다시 불러오기”는 GET이며 자동 PATCH 재실행 없음.
- 409 conflict는 별도 안내. 401/403/세션 교체는 이전 값 제거. source/scope 변경 시 effect 정리 전 render부터 이전 값이 노출되지 않음.
- 취소된 옛 요청은 새 조회의 version을 지우지 못하도록 generation으로 구분한다. localStorage/sessionStorage에 설정·version·token을 저장하지 않는다.
- Demo는 API source를 사용하지 않고 독립적인 로컬 토글만 변경한다. 재마운트/새로고침 시 ON/OFF 샘플로 초기화한다.
- 기존 33×18 스위치 SVG·32px 행·카드 스타일·문구 유지. role=switch/aria-checked/aria-disabled/aria-describedby·키보드 조작·상태 안내 유지. 야간 정책으로 이벤트가 제어된다고 표시하지 않는다.
- HOME/DETAIL/SAVED·프로필/내 활동/문의/로그아웃·신고·정책·OAuth/온보딩·알림함·캐러셀 코드는 관련 회귀 테스트로 확인한다. 회원탈퇴 구현은 수정하지 않는다.

## 6. 변경 파일

| 영역 | 파일 |
| --- | --- |
| BE 신규 | `backend/app/api/v1/users/me/notification_settings.py`, `backend/app/schemas/notification_settings.py`, `backend/app/services/notification_settings_service.py` |
| BE 등록·모델 | `backend/app/api/v1/router.py`, `backend/app/models/v1.py` |
| Migration | `backend/alembic/versions/91c7a2e4b630_add_in_app_notification_settings.py` |
| BE 테스트 | `backend/tests/test_notification_settings_api.py`, `backend/tests/test_alembic.py` |
| FE API·source | `frontend/lib/api.ts`, `frontend/lib/pivot/notification-settings-service.ts`(신규), `frontend/lib/pivot/notification-settings.ts`, `frontend/lib/pivot/my-page-service.ts`, `frontend/lib/pivot/my-page.ts` |
| FE UI 연결 | `frontend/components/pivot/my-page-controller.tsx`, `frontend/components/pivot/my-page-screen.tsx`, `frontend/components/pivot/my-notification-settings.tsx` |
| FE 테스트 | `frontend/tests/pivot-notification-settings-service.test.cjs`(신규), `frontend/tests/pivot-notification-settings-browser-check.cjs` |
| 문서 | `backend/README.md`, `docs/erd/current_schema.sql`(ORM에서 재생성), 본 문서 |

기존 CSS와 정적 자산은 변경하지 않았다. 실제 DB 브라우저 검증용 임시 서버·fixture·스크린샷·가상환경은 ignored 산출물이며 구현 파일 목록에 포함하지 않는다.

## 7. 검증 결과와 한계

- FE 관련 단위/회귀 테스트 **318/318 통과**. 설정 controller·실제 어댑터·MY PAGE·정책/원문 차단·신고·문의·활동·HOME/DETAIL/SAVED·OAuth 세션 연결·온보딩·2장 허용/1장 거부 포함.
- BE 관련 기존 단위/API/회귀 **253/253 통과**. 알림 목록·읽음·신고/문의·인증/CSRF·OAuth·온보딩·정책·모델·저장함·Alembic chain 포함.
- 신규 설정 API 검증 **7/7 통과**: OpenAPI/strict schema 1개, 격리 PostgreSQL의 실제 migration/API/저장 검증 6개. skip 없음.
- 실제 PG에서 기본값·기존 회원 backfill·미완료 회원 제외·NOT NULL·version 제약·기존 값/timestamp/동의 보존, upgrade/downgrade 재적용·저장 후 downgrade 차단 확인.
- 실제 HTTP 인증·온보딩·CSRF/Origin·교체된 쿠키+옛 토큰 차단·다른 회원 격리·변조 필드 거절·세션 철회 확인. 동시 같은 version PATCH는 **200 1건 + 409 1건**. 응답·재조회·DB 저장값 일치.
- 스위치 OFF 이후에도 실제 system_notice의 목록·미확인 수·읽음 API 정상, 기록 삭제 없음.
- Mock 브라우저 **4/4 통과**: 375/390px Demo 및 Service. 지연 저장 중 중복 차단, 서버 응답 후 변경, 새로고침, 실패/GET 재시도, 409/GET 재시도, 쿠키 사용자 변경 시 PATCH 차단, Demo API 0건.
- **실제 로컬 API+DB 브라우저 검증 1/1 통과**: 375px에서 FE가 FastAPI auth/profile/settings를 실제 호출. master OFF → night ON → 새로고침 결과 false/true/version3, 별도 클라이언트 변경 뒤 실제409 → 재조회 결과 true/true/version4. DB 별도 SQL 조회로 true/true/4 및 legacy true/true/true/marketing=false, 필수 공지 1건을 확인했다. HOME의 비관련 feed/topic/bookmark 요청만 mock이었다.
- 브라우저 pageerror 0. 로컬 캡처를 열어 스위치·행·추가 안내를 직접 확인했다. 실제 환경/실기기 검증은 아니다.
- TypeScript 및 `git diff --check` 통과. 전체 FE 테스트와 Production Build는 요청대로 최종 통합 단계에 남긴다. 이전 준비 Commit의 571/571 결과를 이번 전체 테스트 결과로 재사용하지 않는다.
- 기본 Turbopack은 기존 node_modules symlink를 지원하지 않아 기존과 같이 `next dev --webpack`으로 검증했다. 첫 DB 포트는 Windows 예약 범위라 15439로 변경했다. 동시 HTTP 테스트는 Windows의 다중 TestClient portal 대기 문제를 피하도록 단일 event loop의 두 독립 DB transaction으로 검증했다.
- 산출물: `agent/out/my-page-notification-1109/in-app-browser/results.json`, `live-results.json`, `live-server-375.png` 등. 토큰 원문은 보고서에 포함하지 않는다.
- 검증 후 임시 FE/API 서버와 `menu-notifications-test-1109` 컨테이너를 종료했다. 컨테이너의 tmpfs 테스트 DB도 제거했으며 기존 DB 컨테이너는 변경하지 않았다. 최초 구현 단계 종료 시 Git index는 비어 있었고 수정 14개·신규 7개 파일이 작업 트리에 남아 있었다.

BE 재현 시 `DATABASE_URL`과 `NOTIFICATION_SETTINGS_TEST_DATABASE_URL`을 명시적으로 **격리된 loopback notification_settings_test DB**로 설정하고 backend에서 `python -m pytest tests/test_notification_settings_api.py -q`를 실행한다. 테스트는 임의 생성 schema만 사용·정리한다. 실제 서비스 DB URL은 허용하지 않는다. 기존 pytest-asyncio 경고를 피하려면 `-o asyncio_default_fixture_loop_scope=function`을 사용할 수 있다.

## 8. 잔여 의존성·팀 결정

1. **실제 선택적 내부 알림 제어 미완료:** 생성/표시할 대상 이벤트가 아직 없다. 필수 운영 공지를 선택적으로 변경하지 않는다. 대상·기록 생성 여부·화면 표시 방식·야간 판정 시점·지연/폐기/TTL을 정한 뒤 저장값과 연결해야 한다. 현재 야간 차단이 작동한다고 주장할 수 없다.
2. PM의 ON/OFF 기본값·KST 시간 기준은 이번 구현 근거이며 팀 전체 합의로 기록하지 않는다. 팀 리뷰에서 적용 대상, 상하위 UI 동작, 과거 기록·배지 관계와 계약을 확인해야 한다.
3. API/DB 저장·FE 연동에는 로컬 검증상 새 Blocker·Major 없음. 배포 전 migration 적용과 전체 FE/Production Build·실제 환경 통합 검증은 남아 있다.
4. 마케팅 동의는 기존 별도 흐름 유지, 외부 푸시는 MVP 제외. 새 구독·기기 토큰·광고 이벤트를 추가하지 않는다.
5. 회원탈퇴는 기존 즉시 삭제 API와 확정 30일 유예 정책의 차이가 남아 있지만 이번에는 파일/API 연결 모두 수정하지 않았다. 관리자 화면/API도 별도 작업이다.
6. 일정은 PM 제공 기준: 11/15 Consumer+Admin 베타 준비, 11/16~20 최소 5일 테스트, 11/20 개발 마감. 본 작업 완료가 전체 Consumer/Admin 준비 완료를 뜻하지 않는다.

## 9. 후속 최종 검수와 로컬 Commit

2026-11-09 최종 검수에서 변경 범위 21개 파일, DTO·저장값·version 비교, 인증·온보딩·CSRF·본인 설정 격리, 기존 데이터 보존, migration 연결·backfill·downgrade 차단, FE 재조회·409·세션 변경 및 Demo 분리를 확인했다. 새로운 코드 수준 Blocker·Major는 발견하지 못했다. 코드와 테스트는 변경하지 않았으며 기존 통과 결과를 유지하고 동일 테스트를 반복하지 않는다. 이 문서의 작업 단계 기록만 보완했다.

조건부 로컬 Commit 메시지는 `feat(pivot): connect my page notification settings API`다. 실제 선택적 알림 생성·표시 및 야간 수신 제어는 계속 미구현이며, 전체 FE 테스트·Production Build·실제 환경 통합 검증은 후속 단계다. 실제 서비스 DB에는 migration을 적용하지 않는다.
