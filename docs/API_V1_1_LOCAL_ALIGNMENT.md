# API V1.1 로컬 정합화와 화면 연결 기준

**현재 코드의 Q2 조건 (2026-10-29, 서비스 배포 별도):** Pivot 온보딩 화면을 작품 1~5개 → Topic 전체 활성 칩에서 장르와 소재 각각 1~7개 → 기존 Q1 옵션의 소비 유형 1개로 연결했다. 검색·후보·버전별 완료 API 계약은 [온보딩 V2 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md), 실제 화면 검증은 [프론트 연결 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 따른다. [실작품 시작 카탈로그](ONBOARDING_CATALOG_SOURCES_2026-10-28.md)는 작품 30개·태그 38개·연결 140개다. 통합 브랜치의 로컬 구현이며 main 병합·푸시·배포는 하지 않았다. 기존 `answers:{q1,q2}`도 API의 V1 호환 경로로 유지한다.

현재 코드 재확인: 2026-10-28, `codex/service-integration` 작업 공간 기준. 최초 2026-09-29에 [Notion API V1.1 명세서](https://app.notion.com/p/94604c45a961835bbd1601fc3ab15ac0), [FE 세션·공통 계약](https://app.notion.com/p/0ce04c45a96183a884988159b10ddc81)를 대조한 기록에 로컬 라우터·OpenAPI의 변경을 반영했다. 이번에는 외부 Notion 명세나 배포 서버를 재검증하지 않았다. V1.1 전체 요구사항의 구현 완료 판정은 아니다.

`create_app(...).openapi()`에서 확인한 HTTP 작업은 공개 V1 **36개**, 상태 확인 **3개**이며, `ENABLE_DEV_API=true`이면 개발용 **6개**가 추가되어 총 **45개**다. 개발 API를 끄면 39개다. 상태 확인은 `/health`, `/health/db`, `/health/redis`다. 아래 공개 경로는 `/api/v1` 접두사를 한 번만 쓰며 끝에 `/`를 붙이지 않는다. `정합`은 기존 대조 범위의 메서드·경로·주요 계약 대응을 뜻하며 실제 환경의 호출 성공이나 기능 전체 완성을 뜻하지 않는다.

**생년월일 정책 보류 유지 (2026-10-27~28):** 수집·저장·연령 확인 방식은 통합 완료 후 결정한다. 새 Pivot 흐름은 닉네임만 수집하고 생년월일 입력·검사·전송을 보류한다. 생년월일 삭제 migration을 새로 적용하지 않으며 레거시 Consumer 화면·유틸리티는 기존 상태를 유지한다.

**현재 로컬 코드 계약:** 온보딩은 `nickname`, `accountType:user`, `consents`, `topicId`, `answers`를 받는다. 새 답변은 `{version:2,favoriteEntityIds,preferenceTagIds,consumptionType}`이며 기존 `{q1,q2}`와 혼합하지 않는다. `birthDate`·`profileImageId`·`subtopicIds`는 `null`도 포함해 422다. 프로필의 birthDate 미지원도 유지한다. 온보딩 도입 revision `e3a7b9c2d451`과 자식 캐러셀 revision `f4b8c1d9a620`은 보류된 생년월일 삭제 d2와 독립되어 있다. 현재 서비스 target은 f4에 추천 연결 표 4개를 더한 자식 `150a601074af`를 명시하며 [DB·API 확장 계약](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)을 따른다. Pivot 제출에서는 생년월일을 제외해 기존 422 불일치를 해소했다. 이를 레거시 Consumer 전체의 계약 갱신이나 최종 생년월일 정책으로 해석하지 않는다.

**2026-10-21 과거 온보딩 변경:** 당시 API-004는 `POST /api/v1/onboarding/complete`에서 `subtopicIds` 대신 필수 `answers: {"q1":"world","q2":"releases"}`를 받도록 변경됐다. Q1·Q2 각각 단일 허용값을 저장하는 revision은 `c0e5f3b8a124`다. 당시 프론트 수정은 포함하지 않았으며, 현재 Pivot은 위 V2 계약으로 연결되어 있다. 당시 두 질문과 아래 2026-09-29 검증 수치를 현재 Pivot 계약으로 사용하지 않는다.

## 현재 API 대조 — 37개 (기존 번호 34개 + 온보딩 2개 + 캐러셀 이미지 1개)

| 번호 | 메서드와 공개 경로 | 상태 | 로컬에서 확인할 계약과 범위 |
| --- | --- | --- | --- |
| API-001 | `POST /api/v1/auth/login/{provider}` | 구현 | 카카오·네이버 로그인 시작. 세션·CSRF와 공급자 설정이 필요하다. |
| API-002 | `GET /api/v1/auth/login/{provider}/callback` | 구현 | state·브라우저 바인딩 검증, 계정 연결, 세션 발급·리다이렉트. |
| API-004 | `POST /api/v1/onboarding/complete` | **부분** | `201`, 닉네임·동의·Topic과 버전별 답변·선택을 원자적으로 저장. v2는 작품 1~5개·현재 켜진 장르·소재 각각 1~7개·소비 유형 1개. v1은 기존 q1/q2. `answers`와 `activeTopic.subtopicIds=[]` 반환. pending 세션·CSRF 필요, 세션 교체 유지. |
| 신규 Q1 | `GET /api/v1/onboarding/works` | 구현 | pending 세션. topicId/q/cursor/limit으로 활성 애니 작품 이름·별칭 검색. 빈 검색어는 빈 목록. 검색된 활성 작품은 모두 selectable=true. |
| 신규 Q2 | `POST /api/v1/onboarding/preference-options` | 구현 | pending 세션·CSRF. `{topicId,entityIds}`로 유효 작품 1~5개를 검사하고 켜진 장르·소재 칩 전체와 힌트용 sourceEntityIds, minSelections(2), minGenreSelections(1), minThemeSelections(1), maxSelectionsPerKind(7) 반환. 선택 저장 없음. |
| API-005 | `GET /api/v1/auth/session` | **부분** | 비로그인도 `200`과 익명 세션 쿠키·`csrfToken`을 받는다. 응답 모델은 `restoration_pending`을 허용하지만 계정 복구 절차(API-010)는 아직 없다. |
| API-006 | `POST /api/v1/auth/logout` | 정합 | 현재 세션 폐기, `204` 빈 본문. 유효 세션의 변경 요청에는 CSRF가 필요하다. |
| API-007 | `GET /api/v1/users/me` | 정합 | 온보딩 완료 사용자의 프로필 조회. 응답에 `birthDate`가 없다. |
| API-008 | `PATCH /api/v1/users/me` | **부분** | 닉네임 수정은 가능하다. 프로필 이미지 업로드·연결은 API-096 미구현으로 현재 `profileImageId`를 새 값으로 설정할 수 없다. |
| API-011 | `GET /api/v1/policies` | 정합 | `consentItems[]`의 실제 `type`·`policyVersion`·`required`를 조회한다. |
| API-012 | `GET /api/v1/users/me/consents` | 정합 | 현재 동의 내역 조회. |
| API-013 | `PATCH /api/v1/users/me/consents` | 정합 | 선택 동의 갱신. 필수 항목을 거짓으로 철회하면 오류를 반환한다. |
| API-017 | `GET /api/v1/topics` | 정합 | Topic 목록과 선택 검색어 `q`. 로컬 seed가 채운 Topic만 나온다. |
| API-018 | `GET /api/v1/topics/{topicId}/subtopics` | 정합 | Topic 아래 평면 목록·검색. 선택 `q`, `cursor`, `limit`을 사용하고 `nextCursor`는 동일한 필터에서 이어 쓴다. 공개 요청에 `parentSubtopicId`가 없다. |
| API-019 | `GET /api/v1/subtopics/{subtopicId}` | 정합 | `subtopicId`·`topicId`·`name`을 반환한다. 공개 응답에 부모 필드가 없다. |
| API-021 | `GET /api/v1/me/topics` | 정합 | 내 활성 Topic 목록; 선택 `includeDeleted`로 삭제 상태를 함께 조회할 수 있다. |
| API-022 | `POST /api/v1/me/topics` | 정합 | `topicId`와 소속된 `subtopicIds`로 추가·활성화, `201`. 기존 활성 Topic은 충돌 오류를 낸다. |
| API-025 | `GET /api/v1/me/topics/{topicId}/subtopics` | 정합 | 해당 Topic의 내 Subtopic 구독 목록. |
| API-026 | `PUT /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 정합 | 해당 Topic에 속한 Subtopic을 구독하고 `200`을 반환한다. |
| API-027 | `DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 정합 | 구독 해제, `204` 빈 본문. |
| API-028 | `GET /api/v1/topics/{topicId}/feed` | **부분** | 공개 콘텐츠의 로컬 피드를 `200`과 `sections[]`·`nextCursor`로 조회한다. 카드에 `slides[]` 추가, 기존 글은 빈 배열. 확인된 검토·3~10장 완성·미만료 캐러셀만 공개한다. 오늘(KST, 없으면 어제) 추천 결과가 있으면 그 순서 다음에 나머지 공개 글을 최신순으로 잇고 카드에 `recommendationId`·`reason`을 채운다. 연 글·그만볼래요 글은 빼고, 오늘 노출된 글은 뒤로 보내고, 같은 대표 작품 글은 연달아 두지 않는다. 추천 결과가 없거나 세부 주제 피드면 최신순이다. 광고·저장 재노출 공급은 없다. |
| API-029 | `GET /api/v1/contents/{contentId}` | 정합 | **필수 query `topicId`**와 함께 공개 글 상세 조회. `slides[]`·전체 `sources[]` 추가. 기존 `body`·`imageUrl`·`sourceUrl`·`displayMode` 유지, 승인된 AI 원본은 `full_body`. |
| 신규 이미지 | `GET /api/v1/contents/{contentId}/slides/{page}/image` | 구현 | **필수 query `topicId`**, page 1~10. 세션·온보딩·활성 Topic·공개/만료 조건 확인 후 `image/png`. private/no-store, nosniff. 불완전·보류·거절·만료·없는 장은 404. |
| API-033 | `POST /api/v1/users/me/activity-events` | 구현 | 온보딩 완료 세션·CSRF. 노출·열람·좋아요·저장·선호·카드 넘김 등 13종 활동을 한 번에 1~100개 기록한다. 이벤트마다 `clientEventId`(UUID)로 중복을 막고 `saved`·`duplicated`·`rejected` 개수를 반환한다. 노출은 화면에 머문 시간 `visibleMs`를 받는다. 추천 계산의 재료다. |
| API-035 | `PUT /api/v1/contents/{contentId}/preference` | 정합 | 본문 `{ "topicId": 1, "value": "positive" }` 또는 `negative`; `200`. |
| API-036 | `DELETE /api/v1/contents/{contentId}/preference` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |
| API-037 | `PUT /api/v1/contents/{contentId}/like` | 정합 | 본문 `{ "topicId": 1 }`, `200`. |
| API-038 | `DELETE /api/v1/contents/{contentId}/like` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |
| API-039 | `PUT /api/v1/contents/{contentId}/bookmark` | 정합 | 본문 `{ "topicId": 1 }`, `200`. |
| API-040 | `DELETE /api/v1/contents/{contentId}/bookmark` | 정합 | **필수 query `topicId`**, `204` 빈 본문. |
| API-041 | `GET /api/v1/users/me/bookmarks` | 구현 | `topicId`, `limit`, `cursor`로 저장 목록을 조회한다. |
| API-048 | `POST /api/v1/chat/sessions` | 구현 | 관심 분야에 연결된 AI 대화 세션을 생성한다. |
| API-052 | `POST /api/v1/chat/sessions/{sessionId}/messages` | 구현 | 질문 전송·답변 생성 작업. LLM 설정과 현재 분야의 공개 콘텐츠 근거가 필요하다. |
| API-053 | `GET /api/v1/chat/jobs/{jobId}` | 구현 | 본인의 답변 작업 상태·결과 조회. |
| API-107 | `GET /api/v1/users/me/notifications` | **부분** | 운영공지(`system_notice`)만 조회. 다른 알림 공급은 후속이다. |
| API-108 | `GET /api/v1/users/me/notifications/unread-count` | **부분** | 운영공지만 미읽음 수에 포함한다. |
| API-109 | `PATCH /api/v1/users/me/notifications/{notificationId}` | **부분** | 본인의 운영공지를 읽음 처리한다. |

### 공통 계약

- 브라우저 클라이언트는 HttpOnly 세션 쿠키를 `credentials: "include"`로 보낸다. CSRF 토큰은 메모리에만 두고 변경 요청의 `X-CSRF-Token`으로 보낸다. 로그인·온보딩처럼 세션 쿠키가 바뀌면 `getSession()`으로 새 토큰을 받는다. `401` 또는 `CSRF_INVALID` 뒤에는 메모리 토큰을 폐기하므로 세션을 다시 조회한 뒤 사용자의 작업을 재시도한다.
- 첫 익명 세션 발급 시 허용된 출처를 확인한다. 헤더가 없는 로컬 Swagger·CLI GET은 허용한다. 오류 응답은 `code`와 `message`, 입력 검증 오류는 `fields[]`의 `field`·`code`·`message`를 사용한다. 깨진 JSON은 `400 MALFORMED_JSON`이다.
- 공개 `/api/v1` 응답에는 `Cache-Control: private, no-store`가 붙는다. 본문이 있는 변경 요청은 JSON `Content-Type`을 사용한다. 다른 본문 형식은 `415 UNSUPPORTED_MEDIA_TYPE`이다.
- `204` 성공 응답에는 본문이 없다. 클라이언트는 JSON 파싱을 시도하지 않는다. 보호된 API는 로그인·온보딩 상태에 따라 `401 AUTH_REQUIRED` 또는 `403 ONBOARDING_REQUIRED`가 될 수 있다.
- `ENABLE_DEV_API=true`의 개발용 가입·로그인·Agent 결과 API와 `ENABLE_DEV_AUTH_BYPASS=true`의 루프백 우회는 **로컬 테스트 수단**이다. 카카오·네이버 OAuth나 정식 운영 인증을 뜻하지 않는다.

## Pivot 화면의 현재 호출 순서

기존 공통 FE 함수는 [frontend/lib/api.ts](../frontend/lib/api.ts)에 있고 Pivot의 검색·후보 연결은 [onboarding-catalog.ts](../frontend/lib/pivot/onboarding-catalog.ts), 제출은 [onboarding-service.ts](../frontend/lib/pivot/onboarding-service.ts)에 있다. 현재 Pivot은 실제 API에서 받은 ID와 정책 버전을 사용하며 데모 카탈로그는 명시적인 demo/preview에만 사용한다. 아래는 Pivot의 현재 호출 흐름이며 레거시 Consumer의 `onboard` 함수와 구분한다. 기본 로컬 구성은 `localhost:3000`의 FE에서 `localhost:8000`의 API를 호출하며 별도 포트의 격리 검증 환경은 [연결 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)에 남겼다.

| 화면 단계 | 현재 쓸 수 있는 FE 함수 | 입력·상태 갱신 |
| --- | --- | --- |
| 시작·인증 | `getSession`, 개발용 `register`, `login`, `logout` | 세션을 조회해 쿠키·CSRF를 받는다. 개발용 로그인 후 세션을 재조회한다. 로그아웃 성공은 빈 `204`; 이어서 세션·보호 화면을 갱신한다. |
| 정책·온보딩 선택 | `getPolicies`, `serviceCatalog.resolveTopic/search/options` | 실제 anime Topic과 작품 검색·후보 API 결과를 사용한다. Q1 작품 1~5개·Q2 장르와 소재 각각 1~7개·Q3 소비 유형 1개. 작품 변경 시 후보를 갱신하고 후보 밖 선택을 정리한다. |
| 온보딩 제출 | `serviceOnboardingService.submitSelection` | `/onboarding/complete`에 닉네임·`accountType:user`·동의·`topicId`와 V2 `answers`를 제출하고 `birthDate`·`profileImageId`·`subtopicIds`는 제외한다. 세션 회전 후 `getSession`으로 `active`·새 CSRF를 확인한다. 중복 클릭과 응답 유실 후 재확인도 처리한다. |
| 마이페이지·동의 | `getProfile`, `updateProfile`, `getConsents`, `updateConsents` | 닉네임·선택 동의 변경 후 관련 조회를 다시 실행한다. `updateProfile({profileImageId:null})`로 기존 이미지 연결 해제만 가능하며 업로드·새 이미지 연결은 아직 지원하지 않는다. |
| 관심사·구독 | `getMyTopics`, `addMyTopic`, `getMySubtopics`, `subscribeSubtopic`, `unsubscribeSubtopic` | `getMyTopics({includeDeleted})` 조회가 가능하다. 추가에는 실제 Topic에 속한 Subtopic ID를 제출한다. 구독 해제의 `204`를 빈 응답으로 처리한다. |
| 홈·글 상세 | `getFeed`, `getContent` | 활성 `topicId`로 피드를 조회하고 카드 `id`를 `contentId`로 전달한다. 상세에는 같은 `topicId`를 필수로 넘긴다. `nextCursor`가 있으면 이어서 가져온다. |
| 글 반응 | `setPreference`, `clearPreference`, `likeContent`, `unlikeContent`, `bookmarkContent`, `unbookmarkContent` | 모든 요청에 글이 속한 활성 `topicId`가 필요하다. 변경 후 피드 카드와 상세를 다시 조회하거나 반환값으로 동기화한다. |

클라이언트의 `ApiError.code`로 `AUTH_REQUIRED`, `ONBOARDING_REQUIRED`, `CSRF_INVALID`, `INVALID_CURSOR` 같은 상태를 분기한다. 만료된 CSRF가 확인되면 `getSession()`으로 현재 토큰을 다시 받은 뒤 사용자의 작업을 재시도한다. 화면에 없는 미구현 동작의 FE 호출 함수는 준비되어 있지 않다.

캐러셀은 현재 **서비스 DB/API까지만** 확장했다. 위 Pivot 호출 함수가 새 `slides[]`·전체 `sources[]`를 렌더링하는 화면 연결은 아직 하지 않았다. `slides[].imageUrl`은 API 상대 경로이며 각 장의 PNG에는 텍스트가 없다. 향후 화면은 `titleLines`·`bodySentences`와 camelCase `layout.textOverlayBox`, 상세 `sources[]`·`AI_GENERATED` 고지를 함께 사용한다. [입력·응답·운영자 검토 계약](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)을 따른다.

정책 `type`은 네 가지 고정값으로 가정하지 않고 `getPolicies()`가 반환한 항목을 그대로 동의 입력에 사용한다. 현재 Pivot은 `answers.version=2`로 작품·태그 ID 배열과 단일 `consumptionType`을 보낸다. Q3 허용값은 `world/relationships/ost/goods/events/news`이며 Q2 태그·Q3 소비 유형은 알림 동의가 아니다. 과거의 `q1/q2` 문자열 두 개는 API의 V1 호환 테스트에만 사용한다. 기존 세부 관심사 데이터·`tap_topic`과 별도 구독 API는 유지한다. 로컬 테스트 화면은 Topic 또는 피드 Subtopic 필터가 바뀌면 이전 피드와 cursor를 지운다.

## Swagger UI 로컬 확인 순서

서버·PostgreSQL·seed 준비와 모든 작업의 Try it out 입력값은 [Swagger UI 조작법](SWAGGER_UI_OPERATION_GUIDE.md)과 [서버 준비 가이드](SWAGGER_TEST_GUIDE.md)를 따른다. `/docs`와 `/openapi.json`은 같은 `localhost:8000`으로 연다. 예시의 숫자 ID를 그대로 쓰지 않고 앞선 GET의 실제 값을 사용한다.

1. **빠른 공개 글 확인:** `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=true`로 로컬 데모 사용자를 준비한다. `GET /api/v1/me/topics`의 활성 `topicId` → `GET /api/v1/topics/{topicId}/subtopics`의 `subtopicId` → `GET /api/v1/topics/{topicId}/feed`의 카드 `id` → `GET /api/v1/contents/{contentId}?topicId=...` 순서로 실행한다. 공개된 Agent 글도 같은 피드·상세에서 확인한다. 내부 초안은 개발용 Agent 실행 조회 경로를 사용한다.
2. **실제 세션 흐름:** `ENABLE_DEV_AUTH_BYPASS=false`로 재시작한다. `GET /api/v1/auth/session`에서 `anonymous`와 `csrfToken`을 받는다. Swagger **Authorize → CsrfToken**에 넣고 개발용 가입·로그인을 실행한다. 세션을 다시 조회해 `onboarding_pending`과 새 토큰을 받는다.
3. **온보딩:** 정책·Topic을 GET으로 읽고 작품 검색 GET → 후보 조회 POST → 완료 POST 순서로 실행한다. 완료에는 실제 ID의 V2 `answers`를 제출한다. `201` 응답의 답변과 `activeTopic.subtopicIds=[]`를 확인한 뒤 세션을 다시 조회해 `active`와 새 토큰을 받는다. 기존 `subtopicIds`, 답변 누락, 질문별 개수·형식·현재 후보 조건 위반은 422다. `q1/q2` 두 문자열을 보내는 V1 호환 검사는 별도로 구분한다.
4. **화면 동작:** 프로필·동의·관심사 조회와 갱신 → 피드·상세 → O/X·좋아요·북마크 PUT 및 DELETE 순서로 실행한다. DELETE와 로그아웃의 `204`에는 본문이 없고, 변경 뒤 GET을 다시 호출해 결과를 본다.
5. **계약 검사:** API-018의 선택 query는 `q`, `cursor`, `limit`이며 부모 필드가 없어야 한다. API-019 응답에도 부모 필드가 없어야 한다. 피드 cursor는 같은 필터에 사용하고, 상세와 반응 취소에는 `topicId`를 입력한다.

코드 회귀 확인 명령은 저장소 루트에서 다음과 같다. 실행 여부와 통과 결과는 작업 결과에 별도로 기록한다.

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run typecheck
npm run build
```

## 이번 범위 밖과 결정이 필요한 항목

- **새 공개 API:** API-023 Topic 삭제, API-034 공유 정보, 계정 복구 API-010, 저장함 전용 수정·상세·삭제, 채널 팔로우·DM·설정·신고·탈퇴는 후속이다. 기존 OAuth, 저장 목록(API-041), AI 대화 3개, 운영공지 3개와 신규 온보딩 조회 2개는 위 표의 구현 범위에 포함한다.
- **온보딩 운영 전 확인:** Pivot의 V2 화면 연결은 완료됐다. 레거시 Consumer의 온보딩 계약 갱신, 실제 OAuth 공급자 신규 가입, 실서비스 적용·배포는 이번 로컬 검증과 구분한다. 생년월일 정책은 계속 보류한다. 실작품 시작 카탈로그의 표기·연도·이미지 결측 및 확장 기준은 [출처 문서](ONBOARDING_CATALOG_SOURCES_2026-10-28.md)를 따른다.
- **홈 공급:** API-028은 추천 결과 표(`recommendation_round`·`recommendation_item`)에 오늘(없으면 어제) 결과가 있으면 그 순서를 쓰고, 없으면 공개 글 최신순이다. 이 표를 채우는 하루 한 번 모델 계산은 아직 연결 전이다. 광고·저장 재노출의 공급 로직, Home_AI의 “원하는 글 요청” 계약은 아직 없다. `Content_AI`의 글 질문·답변 계약과 동일하다고 가정하지 않는다.
- **화면과 목록 계약:** Figma의 사람 구독 UI와 V1.1의 채널 팔로우, 번호 페이지 UI와 cursor 목록, 저장함 카드의 썸네일·작성자·시간, “21시 이후 알림허용”과 일반 문의의 필드는 별도 결정이 필요하다. 세부 화면 매핑은 [Figma API 백로그](FIGMA_API_V1_1_IMPLEMENTATION_BACKLOG.md)에 있다.
- **Agent 공급:** 캐러셀 파일 묶음의 서비스 import CLI와 검토된 묶음의 공개 조회는 구현 범위다. 기본 import는 dry-run이며 승인 기록 없이 저장하면 pending_review로 보관한다. producer 코드·DB는 변경하지 않았고 정기 자동 공급, 공개 검토·승인 API, 프론트 렌더링은 별도 후속 작업이다. 기존 영화 Agent 실행·발행 경로와 혼용하지 않는다.

## 2026-10-28 Pivot·카탈로그 연결 검증

실제 PostgreSQL에 시작 작품 30개·태그 38개·연결 140개를 적재하고 반복 적재의 변경 0건을 확인했다. 격리된 로컬 API에 연결한 모바일·데스크톱 Pivot에서 작품 1개·태그 1개와 작품 5개·태그 12개 가입을 확인했다. 프론트 테스트 259개, TypeScript 검사와 production build가 통과했다. 실제 OAuth 공급자·운영 배포 성공을 뜻하지 않으며 자세한 환경과 확인 범위는 [연결 검증 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 따른다.

## 2026-09-29 로컬 검증 기록

- `backend`에서 `python -m pytest -q`: **98 passed**. Starlette TestClient의 사용 중단 예정 경고 1개가 남았다.
- `frontend`에서 `npm run typecheck`, `npm run build`: 모두 통과. Next.js는 저장소 밖의 상위 `package-lock.json`을 무시한다는 경고를 출력했다.
- FastAPI TestClient의 `GET /docs`와 `GET /openapi.json`: 둘 다 `200`. 개발 API를 켠 OpenAPI는 전체 HTTP 작업 32개, 번호가 있는 공개 API 24개다. API-018 query는 `topicId`, `q`, `cursor`, `limit`이며 API-019의 `SubtopicItem`에는 `subtopicId`, `topicId`, `name`만 있다. 공개 경로 끝에 `/`가 없다.
- 기존 PostgreSQL을 **읽기 전용**으로 확인했다. Topic 1개, Subtopic 2개, Content 2개이며 부모가 지정된 Subtopic은 0개다. 별도 SQLite 회귀 테스트에서 자식 Subtopic도 평면 목록·검색·커서에 포함됨을 확인했다. 로컬 ASGI 요청에서는 세션, Topic, Subtopic, 피드, 글 상세가 모두 `200`이었다. 이 요청은 실제 TCP 서버를 띄운 브라우저 조작은 아니다.
- DB의 Alembic 현재 버전은 `7c8e1a9b4d2f`다. 저장소 head `b5d2c8f1a7e3`는 이번 공개 API 정합화와 무관한 채팅 테이블 추가 리비전이므로 이 작업에서 적용하지 않았다. 기존 DB 초기화·seed 재실행·데이터 삭제는 하지 않았다.
