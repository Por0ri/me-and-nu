# 현재 구현 API의 Swagger UI 조작법

현재 로컬 코드 기준: **2026-10-28**. 등록된 HTTP 작업은 공개 V1 **36개**, 상태 확인 **3개**이며 `ENABLE_DEV_API=true`이면 개발용 **6개**가 추가되어 총 **45개**다. 최초 2026-09-29의 32개(공개 24·개발 6·상태 2)는 당시 기록이다. 전체 현재 목록과 한계는 [V1.1 로컬 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)를 따른다. 이 문서는 **Swagger 화면에서 주요 기능을 확인하는 순서와 입력 방법**을 설명한다. 서버·DB 준비는 [로컬 테스트 가이드](SWAGGER_TEST_GUIDE.md)를 참고한다. 로컬 구현 갱신이 원격 배포 완료를 뜻하지 않는다.

> JSON의 숫자 ID와 정책 버전 `v1`은 입력 형식을 보여주는 예시다. 팀원은 **자기 환경의 조회 응답에서 받은 값**으로 바꾼다. 작품은 works GET, 태그는 preference-options POST 응답의 ID를 사용한다. 현재 서버가 실행 중이지 않으면 `/docs`를 열기 전에 백엔드와 PostgreSQL을 먼저 시작한다.

2026-10-21 당시 온보딩 계약은 세부 관심사를 Q1·Q2 각 1개로 대체하고 프로필 이미지 입력을 제거한 V1 형식이다. 당시 추가 revision은 `c0e5f3b8a124`였으며 V1 답변은 호환 분기로 유지한다. 프로필 이미지 입력 제거 자체에는 DB 변경이 필요하지 않았다.

**2026-10-28 온보딩 V2:** 작품 1~5개 → 작품의 장르·소재에서 1개 이상 → 소비 유형 1개를 받는다. 아래 기본 흐름은 V2이고 별도로 V1 호환 예시를 제공한다. 온보딩 도입 revision은 `e3a7b9c2d451`이며 현재 서비스 DB 준비에는 캐러셀 확장을 포함한 자식 **`f4b8c1d9a620`**을 명시한다. 생년월일 삭제 d2와는 별도 분기이므로 `upgrade head`나 `upgrade heads`를 사용하지 않는다. [V2 구현 명세](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md)와 [캐러셀 migration 안내](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#2-db-변경과-명시적-migration-대상)를 따른다.

**생년월일 정책 보류 유지 (2026-10-27~28):** 수집·저장·연령 확인 방식은 통합 완료 후 결정한다. 새 Pivot은 닉네임만 수집하고 생년월일 입력·검사·전송을 보류한다. 생년월일 삭제 migration을 새로 적용하지 않으며 레거시 Consumer 화면·유틸리티는 기존 상태를 유지한다.

아래 JSON은 현재 로컬 코드용이므로 `birthDate`나 연령 확인값을 넣지 않는다. 이 코드에서 `birthDate`는 `null`도 포함해 422 `VALIDATION_ERROR`이며 `GET/PATCH /api/v1/users/me` 응답에도 없다. 기존 삭제 revision은 `d2f6a9c4e731`이다. 이는 최종 수집 정책이 아니며, 실제 테스트에서는 대상 환경의 배포 계약과 [DB 적용 상태](ERD_API_ALIGNMENT_2026-10-27.md)를 확인한다. [OAuth 안내의 로컬 구현 기록](OAUTH_T02_1_SETUP.md#2026-10-27-생년월일연령-확인-제거-계약)도 같은 구분을 따른다.

**2026-10-28 Pivot 연결 완료:** 검색·후보·Q3와 V2 제출을 화면까지 연결했고 `birthDate`를 제출에서 제외해 기존 Pivot의 422 불일치를 해소했다. [실작품 시작 카탈로그](ONBOARDING_CATALOG_SOURCES_2026-10-28.md) 30개로 모바일·데스크톱 가입을 확인했다. 프론트 테스트 259개, TypeScript 검사와 production build가 통과했다. main 병합·푸시·배포는 하지 않았으며, 격리된 로컬 환경의 [연결 검증 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 따른다.

## 1. 모든 작업에 공통인 Swagger 조작

1. **[http://localhost:8000/docs](http://localhost:8000/docs)**를 연다. 세션 쿠키가 이어지도록 테스트 내내 `localhost`를 사용하고 `127.0.0.1`과 섞지 않는다.
2. 아래 표의 **Swagger 태그**를 펼치고 해당 메서드·경로를 클릭한다. **Try it out**을 누른다.
3. `Parameters`의 **path/query** 칸과, 필요한 작업에서만 `Request body`의 JSON을 입력한다. 빈 선택 query는 그대로 비운다. **Execute**를 누른다.
4. 아래 `Server response`의 **Code**와 **Response body**를 확인한다. 다음 요청에는 예시 ID가 아니라 여기서 받은 실제 `id`, `entityId`, `tagId`, `subtopicId`, `contentId`, `agentRunId`, `nextCursor`를 사용한다. **204**는 성공이어도 본문이 없다.
5. 변경 요청이 403 `CSRF_INVALID`라면 `GET /api/v1/auth/session`을 다시 실행하고 새 `csrfToken`을 우상단 **Authorize → CsrfToken**에 입력한다. `SessionCookie` 칸은 수동으로 채우지 않는다. 같은 브라우저의 HttpOnly 쿠키는 자동 전송된다.

### 테스트 모드 선택

| 모드 | `backend/.env` | 사용처 | Swagger 인증 |
| --- | --- | --- | --- |
| **A. 빠른 로컬 조회·피드·Agent 미리보기** | `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=true` | 데모 사용자 seed 후 보호 API와 탈락 초안 미리보기 | `Authorize` 불필요. 서버·브라우저 요청이 모두 루프백이어야 한다. |
| **B. 실제 세션·온보딩 흐름** | `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=false` | 가입, 로그인, CSRF, 온보딩, 로그아웃 검증 | 익명 세션 조회 → `CsrfToken` 설정 → 로그인·온보딩 뒤 토큰 갱신. |

설정 변경 뒤에는 **백엔드를 재시작하고 `/docs`를 새로고침**한다. `ENABLE_DEV_API=false`이면 개발용 6개 작업은 Swagger와 라우트에서 사라진다. 모드 A의 데모 사용자는 이미 온보딩이 완료되어 있으므로, **작품 검색·후보 조회를 포함한** 신규 온보딩과 로그아웃 후 401 검사는 **모드 B**에서 한다. 개발용 인증 대신 설정된 카카오·네이버 OAuth로 로그인해도 되며 [OAuth 안내](OAUTH_T02_1_SETUP.md)를 따라 복귀 후 실제 `onboarding_pending` 세션과 현재 CSRF를 확인한다.

## 2. 그대로 따라 하는 기본 흐름

### A. 로그인 없이 공개 글·Agent 미리보기 확인

1. `상태 확인`의 `GET /health`, `GET /health/db`를 실행해 각각 200을 확인한다.
2. `정책`의 `GET /api/v1/policies`, `Topic`의 `GET /api/v1/topics`, `내 관심사`의 `GET /api/v1/me/topics`를 실행한다. 응답에서 활성 `topicId`를 기록한다.
3. `Subtopic`의 `GET /api/v1/topics/{topicId}/subtopics`에 Topic ID를 넣어 **영화 리뷰**의 `subtopicId`를 기록한다.
4. `피드`의 `GET /api/v1/topics/{topicId}/feed`를 실행한다. 필요하면 `subtopicId` query에 영화 리뷰 ID를 넣고 다시 실행한다. `sections[].contents[].id`가 공개 글의 `contentId`다.
5. `콘텐츠`의 `GET /api/v1/contents/{contentId}`에 카드 ID를 path로, 같은 `topicId`를 필수 query로 넣어 상세를 확인한다.
6. 저장된 Agent 실행이 있다면 `개발용 Agent 결과`의 목록·상세를 조회한다. 탈락 초안은 아래 표의 `publish-preview`를 **의도적으로** 실행한 뒤 피드·상세를 다시 조회한다. 화면 확인은 `http://localhost:3000/`의 **새로고침**을 누른다.

### B. 신규 계정부터 로그아웃까지

1. `인증·세션` → `GET /api/v1/auth/session` → **Try it out → Execute**. 200의 `authenticated=false`, `sessionState="anonymous"`, `csrfToken`을 확인한다.
2. 화면 우상단 **Authorize** → **CsrfToken**에 `csrfToken`을 붙여 넣고 **Authorize**를 누른 뒤 창을 닫는다. `SessionCookie`는 입력하지 않는다.
3. `개발용 인증` → `POST /api/v1/dev/auth/register`의 `Request body`에 아래 가입 JSON을 입력해 201을 확인한다. 이메일은 테스트마다 고유하게 바꾼다. 이어서 같은 계정으로 `POST /api/v1/dev/auth/login`을 실행해 200을 확인한다.
4. `GET /api/v1/auth/session`을 다시 실행한다. `sessionState="onboarding_pending"`과 **새 `csrfToken`**을 확인하고 **Authorize → CsrfToken** 값을 교체한다.
5. `GET /api/v1/policies`에서 **표시된 모든 동의 항목과 실제 `policyVersion`**을 기록하고, `GET /api/v1/topics`에서 카탈로그가 준비된 실제 Topic ID를 얻는다. Topic은 로그인 뒤, 온보딩 전에도 조회할 수 있다.
6. `온보딩` → `GET /api/v1/onboarding/works`의 query `topicId`와 검색어 `q`를 입력한다. `limit`은 기본 20, 최대 50이다. `items[]`에서 `selectable:true`인 작품을 1~5개 고르고 실제 `entityId`를 기록한다. 작품명·별칭을 검색하며 빈 검색어는 빈 목록이다. 다음 페이지는 같은 Topic·검색어와 반환된 `nextCursor`를 사용한다.
7. `POST /api/v1/onboarding/preference-options`에 아래 후보 요청을 실제 작품 ID로 바꿔 입력한다. **현재 pending 세션과 CSRF**가 필요하다. `genres`와 `themes` 전체에서 최소 1개를 고르고 실제 `tagId`를 기록한다. 최대 선택 수는 없고 두 종류를 각각 선택할 필요도 없다. 작품을 바꿨다면 후보를 다시 조회한다.
8. `POST /api/v1/onboarding/complete`에 아래 V2 JSON을 실제 Topic·작품·태그·정책 버전과 소비 유형으로 바꿔 제출한다. **201**, `onboardingCompleted=true`, `answers.version=2`, 선택 ID 집합·소비 유형 일치, `activeTopic.subtopicIds=[]`를 확인한다. 선택 동의도 배열에서 빼지 말고 원하지 않으면 `agreed:false`로 보낸다. 답변의 ID 배열은 오름차순으로 반환된다.
9. 다시 `GET /api/v1/auth/session`을 실행해 `sessionState="active"`를 확인하고 새 `csrfToken`으로 **Authorize**를 갱신한다. 완료된 계정으로 작품 검색·후보 조회·완료를 다시 요청하면 409이므로 기존 계정의 답변이나 완료 상태를 초기화하지 않는다.
10. 아래 주요 작업표에서 프로필·동의·관심사·피드·반응을 순서대로 실행한다. 해당 보호 API의 오류 분기 422/404를 시험한다면 **로그아웃 전에** 실행한다.
11. 마지막에 `POST /api/v1/auth/logout`을 실행해 **204**를 확인한다. 이어서 세션 GET은 `anonymous`, 보호된 사용자 GET은 **401 `AUTH_REQUIRED`**여야 한다.

작품 검색·후보의 빈 결과를 임의 ID로 우회하지 않는다. `v1_local`·`dev_user` seed는 V2 카탈로그를 준비하지 않는다. [실작품 시작 카탈로그](ONBOARDING_CATALOG_SOURCES_2026-10-28.md)의 `onboarding_catalog_starter.json`은 작품 30개·태그 38개·연결 140개이며 실제 PostgreSQL 적재와 반복 적재의 변경 0건을 확인했다. 대상 환경의 활성 anime Topic과 e3를 포함한 서비스 target f4를 확인하고 [카탈로그 적재 도구](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md#5-검토한-카탈로그의-적재-도구)로 준비한다. 실작품 JSON에는 `--demo`를 붙이지 않는다. 별도의 `[DEMO]` JSON은 합성 fixture다.

가입 입력 예시 (`POST /api/v1/dev/auth/register`):

```json
{"email":"swagger.tester01@example.com","password":"ExamplePass!26","nickname":"테스트회원","role":"user"}
```

로그인 입력 예시 (`POST /api/v1/dev/auth/login`):

```json
{"email":"swagger.tester01@example.com","password":"ExamplePass!26"}
```

후보 요청 예시 (`POST /api/v1/onboarding/preference-options`). 아래 숫자는 works 응답에서 고른 실제 ID로 바꾼다.

```json
{"topicId":1,"entityIds":[101,102]}
```

200 응답은 `entityIds`, `genres[]`, `themes[]`, `minSelections:1`, `maxSelections:null`을 포함한다. 각 후보의 `tagId`, `kind`, `code`, `label`, `sourceEntityIds`를 확인한다. 같은 태그는 하나로 합쳐지고 출처 작품 ID를 함께 반환한다. 이 POST는 선택·동의·답변을 저장하지 않는다.

V2 온보딩 입력 예시 (`POST /api/v1/onboarding/complete`). 작품·태그 ID와 정책 버전은 형식 예시이며 위 조회 결과로 바꾼다.

```json
{
  "nickname": "테스트회원",
  "accountType": "user",
  "topicId": 1,
  "answers": {
    "version": 2,
    "favoriteEntityIds": [101, 102],
    "preferenceTagIds": [201, 301],
    "consumptionType": "world"
  },
  "consents": [
    {"type":"terms","policyVersion":"v1","agreed":true},
    {"type":"privacy","policyVersion":"v1","agreed":true},
    {"type":"advertising","policyVersion":"v1","agreed":false},
    {"type":"marketing","policyVersion":"v1","agreed":false}
  ]
}
```

위 JSON은 형식 예시다. `GET /policies`가 반환한 항목이 달라졌다면 **그 항목 전부**를 제출한다. 필수 항목은 `agreed:true`여야 한다. `accountType`은 `user`이고 `topicId`와 `answers`는 필수다. 온보딩에서는 프로필 이미지를 받지 않으므로 `profileImageId` 항목을 넣지 않는다. `null`을 포함해 전송하면 추가 필드 검증으로 422 `VALIDATION_ERROR`를 반환한다. 이 제거는 온보딩에만 적용하며 아래 별도 프로필 수정 API와 이미지 업로드 백로그는 변경하지 않는다.

- Q1은 현재 선택 가능한 작품 ID 1~5개, Q2는 그 작품의 현재 장르·소재 후보 ID 1개 이상이다. 중복 ID는 거절하며 Q2의 고정 최대 개수는 없다.
- `topicId`와 ID 배열의 원소는 양의 BIGINT JSON 정수다. 문자열·실수·bool·범위 초과는 422다. `version`은 정수 2만 허용한다.
- Q3 `consumptionType`은 `world`, `relationships`, `ost`, `goods`, `events`, `news` 중 단일 문자열이다. 누락·배열·다른 값은 422다.
- `birthDate`, `profileImageId`, `subtopicIds` 등 추가 필드는 null도 포함해 422다. Q2/Q3는 알림·마케팅 동의가 아니며 신규 온보딩에서 세부 관심사를 자동 구독하지 않는다.
- 작품 검색에서 `selectable:false`이면 `unavailableReason=PREFERENCE_TAGS_NOT_READY`다. 현재 활성 태그 매핑이 없는 작품을 선택해 후보 계산이나 완료를 요청하면 422다. 후보 조회 이후 카탈로그가 바뀌어도 완료 단계에서 다시 검증한다.

### C. 기존 V1 답변 호환 확인

전환 기간에는 같은 완료 API의 `answers`만 아래 형태로 제출하면 V1으로 저장하고 같은 형식으로 응답한다. 나머지 닉네임·계정 유형·Topic·정책 동의·pending 세션·CSRF 조건은 유지한다.

```json
{"q1":"world","q2":"releases"}
```

V1 Q1은 `world/relationships/ost/goods/events/news`, V1 Q2는 `releases/characters/restock/events` 중 각각 하나다. 두 질문 모두 `events`도 허용한다. `version:1`은 보내지 않으며 V1·V2 필드를 섞거나 버전 없는 V2를 보내면 422다. 기존 회원을 V2로 추정 변환하거나 재온보딩시키지 않는다. 현재 Pivot은 V2를 제출하고 위 V1은 과거 답변 계약의 API 호환 확인용이다. 레거시 Consumer 전체와 호환된다는 의미는 아니다.

### D. Pivot 화면과 대응시키기

Pivot은 정책 동의와 닉네임 입력 뒤 Q1 작품 검색·선택 → Q2 장르·소재 선택 → Q3 소비 유형 → 완료 순서로 진행한다. [onboarding-catalog.ts](../frontend/lib/pivot/onboarding-catalog.ts)의 `serviceCatalog.resolveTopic/search/options`가 실제 anime Topic과 검색·후보를 읽고, [onboarding-service.ts](../frontend/lib/pivot/onboarding-service.ts)의 `serviceOnboardingService.submitSelection`이 V2 완료와 세션 재확인을 담당한다. demo/preview의 합성 카탈로그는 서비스 API 실패 시 대체 데이터로 사용하지 않는다.

로컬 연결 검증에서는 작품 1개·태그 1개와 작품 5개·태그 12개로 가입했다. 별도 레거시 Consumer나 실제 OAuth 공급자·운영 배포까지 검증한 결과는 아니며 상세 범위는 [연결 검증 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 따른다.

## 3. 주요 작업별 Swagger 입력·확인표

표의 모든 `/api/v1/...` 경로는 Swagger에 표시되는 **전체 경로**다. `Request body`가 적히지 않은 작업은 본문 없이 실행한다. `topicId`·`contentId` 등 숫자는 앞선 조회 결과로 바꾼다. 아래는 주요 검증 작업이며 OAuth·저장 목록·AI 대화·알림 등을 포함한 전체 현재 등록 목록은 [API 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)에 있다.

### 상태·개발 인증·정책·온보딩 — 11개

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| — | 상태 확인 → `GET /health` | 없음 | **200** `status="ok"` |
| — | 상태 확인 → `GET /health/db` | 없음 | **200** `status="ok"`, `database=1` |
| — | 상태 확인 → `GET /health/redis` | 없음 | **200** `status="ok"` 또는 Redis 미설정 시 `disabled`; 연결 실패는 503 |
| — | 개발용 인증 → `POST /api/v1/dev/auth/register` | 위 가입 JSON. 모드 B에서는 익명 세션·CSRF 먼저 | **201** 새 `id`, `email`, `nickname`, `accountType`; 중복 이메일은 409 |
| — | 개발용 인증 → `POST /api/v1/dev/auth/login` | 위 로그인 JSON. 모드 B의 현재 CSRF | **200** `message`, `user`; 로그인 쿠키 발급. 이후 세션·CSRF 재조회 |
| API-005 | 인증·세션 → `GET /api/v1/auth/session` | 없음 | **200** `authenticated`, `sessionState`, `onboardingCompleted`, `csrfToken` |
| API-006 | 인증·세션 → `POST /api/v1/auth/logout` | 본문 없음. 모드 B의 현재 CSRF | **204** 본문 없음. 모드 B에서만 이후 보호 API 401 확인 |
| API-011 | 정책 → `GET /api/v1/policies` | 없음. 비로그인도 가능 | **200** `consentItems[]`의 `type`, `policyVersion`, `required` 기록 |
| — | 온보딩 → `GET /api/v1/onboarding/works` | pending 세션, 필수 query `topicId`, 선택 `q`, `cursor`, `limit`(1~50, 기본 20) | **200** `items[].entityId`, `selectable`, `unavailableReason`, `nextCursor`. 빈 검색어는 빈 목록 |
| — | 온보딩 → `POST /api/v1/onboarding/preference-options` | `{"topicId":1,"entityIds":[101,102]}`를 실제 ID로 교체. pending 세션·현재 CSRF | **200** `genres`, `themes`, `sourceEntityIds`, `minSelections:1`, `maxSelections:null`; 선택 미저장 |
| API-004 | 온보딩 → `POST /api/v1/onboarding/complete` | 위 V2 JSON 또는 구분한 V1 JSON. pending 세션·현재 CSRF | **201** `onboardingCompleted=true`, 버전에 맞는 `answers`, `activeTopic.subtopicIds=[]`; 이후 세션·CSRF 재조회 |

### 사용자·Topic·Subtopic·관심사 — 12개

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| API-007 | 사용자 → `GET /api/v1/users/me` | 온보딩 완료 세션 또는 모드 A | **200** `userId`, `nickname`, `accountType` |
| API-008 | 사용자 → `PATCH /api/v1/users/me` | `Request body`: `{"nickname":"테스트회원2"}` | **200** 변경된 `nickname`; `profileImageId`는 현재 `null`만 사용 가능 |
| API-012 | 사용자 → `GET /api/v1/users/me/consents` | 없음 | **200** 최신 동의 `items[]` |
| API-013 | 사용자 → `PATCH /api/v1/users/me/consents` | `{"items":[{"type":"marketing","policyVersion":"v1","agreed":true}]}`; 실제 정책 버전 사용 | **200** `items[].agreed`, `updatedAt`; 필수 동의 철회는 거절 |
| API-017 | Topic → `GET /api/v1/topics` | 선택 query `q`는 검색할 때만 입력. **모드 B는 로그인 후** | **200** `topics[].id`, `code`, `name` |
| API-018 | Subtopic → `GET /api/v1/topics/{topicId}/subtopics` | path `topicId`; 선택 query `q`, `cursor`, `limit`(1~100) | **200** 평면 `items[].subtopicId`, `topicId`, `nextCursor` |
| API-019 | Subtopic → `GET /api/v1/subtopics/{subtopicId}` | path `subtopicId` | **200** 소속 `topicId`, `name` |
| API-021 | 내 관심사 → `GET /api/v1/me/topics` | 선택 query `includeDeleted` 기본 `false` | **200** `topics[]`의 `id`, `status`, `subtopicIds` |
| API-022 | 내 관심사 → `POST /api/v1/me/topics` | `{"topicId":1,"subtopicIds":[2]}`를 **아직 활성화하지 않은 실제 Topic/Subtopic ID**로 교체 | **201** `topic`; 이미 활성화한 Topic이면 409 `TOPIC_ALREADY_ACTIVE` |
| API-025 | 내 관심사 → `GET /api/v1/me/topics/{topicId}/subtopics` | path `topicId` | **200** 구독 `items[]`, `orderingBasis` |
| API-026 | 내 관심사 → `PUT /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 두 path ID. 본문 없음 | **200** `subscribed=true`; 반복해도 구독 하나 |
| API-027 | 내 관심사 → `DELETE /api/v1/me/topics/{topicId}/subtopics/{subtopicId}` | 두 path ID. 본문 없음 | **204** 본문 없음; 구독 목록을 재조회해 확인 |

### 피드·콘텐츠·이미지·반응 — 9개

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| API-028 | 피드 → `GET /api/v1/topics/{topicId}/feed` | path **활성** `topicId`; 선택 query `subtopicId`, `cursor`, `limit`(1~100, 기본 20) | **200** `sections[].contents[].id`, `slides[]`, `nextCursor`; 기존 글의 slides는 [], 빈 피드도 200 가능 |
| API-029 | 콘텐츠 → `GET /api/v1/contents/{contentId}` | path 카드의 `id`; **필수 query `topicId`** | **200** `contentId`, `displayMode`, `body`/`excerpt`, `slides[]`, `sources[]`, `myReaction`. 새 캐러셀은 `AI_GENERATED` 고지. `channel` 키 없음 |
| 신규 이미지 | 콘텐츠 → `GET /api/v1/contents/{contentId}/slides/{page}/image` | 공개 캐러셀의 `contentId`, 응답에 있는 page(1~10), **필수 query `topicId`** | **200 image/png** 바이너리; `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`. 현재 세션 필요, GET의 CSRF 불필요 |
| API-035 | O/X 선호도 → `PUT /api/v1/contents/{contentId}/preference` | path `contentId`; JSON `{"topicId":1,"value":"positive"}` 또는 `negative` | **200** `value`; 상세 재조회에서 `myReaction.preference` 확인 |
| API-036 | O/X 선호도 → `DELETE /api/v1/contents/{contentId}/preference` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 상세 재조회에서 `preference=null` |
| API-037 | 좋아요 → `PUT /api/v1/contents/{contentId}/like` | path `contentId`; JSON `{"topicId":1}` | **200** `liked=true`; 피드·상세 재조회 |
| API-038 | 좋아요 → `DELETE /api/v1/contents/{contentId}/like` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 재조회에서 `liked=false` |
| API-039 | 북마크 → `PUT /api/v1/contents/{contentId}/bookmark` | path `contentId`; JSON `{"topicId":1}` | **200** `savedItemId`, `tags`, `savedAt`; 재조회에서 `saved=true`·`bookmarked=true` |
| API-040 | 북마크 → `DELETE /api/v1/contents/{contentId}/bookmark` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 재조회에서 북마크 해제 |

반응 PUT/DELETE는 **모드 B의 현재 CSRF**가 필요하다. `topicId=1`은 예시이며, 콘텐츠가 속한 **내 활성 Topic ID**로 바꾼다. 피드의 `recommendationReason=null`, `seen=false`는 추천·노출 이벤트 미연결 상태에서 정상일 수 있다.

캐러셀은 [서비스 import CLI](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#3-적재-경계)로 별도 준비한다. Swagger에 캐러셀 import·검토 승인 작업은 없다. 기본 보류 적재본은 피드에 나오지 않으며 별도 운영자가 정확한 digest에 연결해 승인한 묶음만 공개한다. 공개본에서 `slides`의 page순 텍스트·camelCase layout·1308×1632 크기, 상세의 `sources`·AI 고지를 확인하고 이미지 GET을 호출한다. 이미지 응답은 JSON으로 파싱하지 않는다. 만료·미검토·거절·불완전 캐러셀과 없는 장은 404여야 하며, 다른 Topic이나 비로그인에서도 노출되면 안 된다. 현재 프론트 화면의 캐러셀 표시를 확인하는 단계는 포함하지 않는다.

### 개발용 Agent 결과·발행 — 4개

Agent 새 실행을 만드는 Swagger 작업은 없다. 기존 실행이 없으면 [기존 가이드의 CLI 절차](SWAGGER_TEST_GUIDE.md#영화-agent-초안과-판정-조회)로 먼저 준비한다. 실행에는 외부 모델/TMDB 키와 사용량이 필요할 수 있다. 아래 작업은 `ENABLE_DEV_API=true`와 **로컬 요청**이 필요하다.

아래 `publish`·`publish-preview`는 기존 영화 Agent 경로다. 캐러셀 묶음의 검토·승인은 별도 운영자 기록과 [서비스 import CLI](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md#3-적재-경계)를 사용하며 이 개발용 작업으로 우회하지 않는다.

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| — | 개발용 Agent 결과 → `GET /api/v1/dev/agent-runs` | 선택 query `limit` 기본 20(1~50), `offset` 기본 0 | **200** `items[].agentRunId`, `outcome`, `draft.status`, `hasMore` |
| — | 개발용 Agent 결과 → `GET /api/v1/dev/agent-runs/{agentRunId}` | 목록에서 얻은 path `agentRunId` | **200** `draft`, `steps`, `judgments`, `sourceUrls`; 글이 없으면 `draft=null` |
| — | 개발용 Agent 결과 → `POST /api/v1/dev/agent-runs/{agentRunId}/publish` | **승인된** 실행 ID. 본문 없음. 모드 B에서는 CSRF 필요 | **200** `contentId`, `topicId`, `status`, `judgmentStatus`, `feedUrl`, `detailUrl`; 조건 미충족은 409 |
| — | 개발용 Agent 결과 → `POST /api/v1/dev/agent-runs/{agentRunId}/publish-preview` | **탈락한** 실행 ID. 본문 없음. **모드 A만** | **200** `preview=true`, `judgmentStatus="needs_review"`; 모드 B는 403 `DEV_PREVIEW_DISABLED` |

`publish`는 `outcome=publish_candidate`, `draft.status=approved`, 사실 확인 추가 검토 불필요 조건을 요구한다. 정상 자동 발행이 이미 완료됐다면 같은 Content를 반환한다. 탈락 원고를 `publish-preview`로 게시한 뒤 같은 실행에 일반 `publish`를 요청하면 **409 `AGENT_DRAFT_PREVIEW_ONLY`**가 정상이다. `publish-preview`로 만든 공개 글은 개발용·검토 필요 표식이 붙는다. 발행 응답의 `feedUrl`과 `detailUrl`을 Swagger의 피드·콘텐츠 GET에 대응시켜 확인한다.

## 4. 오류와 결과 기록

- **401 `AUTH_REQUIRED`**: 모드 B에서 보호 API를 비로그인 호출. `GET /auth/session`으로 상태부터 확인한다.
- **403 `ONBOARDING_REQUIRED`**: 로그인은 했지만 온보딩 완료 전의 피드·반응·내 정보 요청.
- **403 `CSRF_INVALID`**: 변경 요청의 토큰 누락·만료 또는 허용되지 않은 Origin. 현재 세션을 다시 조회해 Swagger **Authorize → CsrfToken**을 갱신한다.
- **400 `MALFORMED_JSON`**: `Request body`의 JSON 문법이 잘못됐다. JSON 형식을 바로잡아 다시 실행한다.
- **415 `UNSUPPORTED_MEDIA_TYPE`**: 본문을 보내는 변경 요청의 `Content-Type`이 JSON이 아니다. `application/json`으로 실행한다.
- **404 `TOPIC_NOT_FOUND`**: 온보딩 검색·후보·완료의 Topic이 없거나 비활성이다.
- **422 `VALIDATION_ERROR`**: V2 작품/태그 선택·정수 형식·중복·소비 유형을 `fields[]`의 실제 필드 경로로 확인한다. 후보 요청은 `entityIds`, 완료는 `answers.favoriteEntityIds`·`answers.preferenceTagIds` 등이며 배열 형식 오류에는 원소 번호가 붙을 수 있다. 조회 이후 후보가 바뀌면 다시 조회하고 선택을 확인한다.
- **409 `ONBOARDING_ALREADY_COMPLETED` / `STATE_CONFLICT`**: 작품 검색·후보·완료는 pending 전용이다. 완료 회원이나 다른 세션 상태로 호출하지 않는다.
- **404 `CONTENT_NOT_FOUND`**, **422 `INVALID_CURSOR`**: 존재하지 않는 Content ID 또는 잘못된 cursor. 작품 cursor는 동일 Topic·검색어에, 피드 cursor는 동일 피드 조건에 사용한다. 온보딩 cursor 오류는 완료 전에, 일반 보호 API 오류는 로그아웃 전에 확인한다.
- **204**: DELETE와 로그아웃은 본문이 없는 것이 정상이다.

테스트 기록에는 모드 A/B 또는 실제 OAuth, 답변 V1/V2, 실제 Topic·작품·태그·Subtopic·Content·AgentRun ID, 각 요청의 Code·오류 `code`, 후보·저장·피드/상세 재조회 결과를 남긴다. 원본 세션·CSRF·OAuth code/state는 기록하지 않는다. 현재 구현과 V1.1 계약의 차이는 [로컬 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)에서 확인한다.
