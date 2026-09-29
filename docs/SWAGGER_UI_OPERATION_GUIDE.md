# 현재 구현 API의 Swagger UI 조작법

기준: 2026-09-29, 현재 로컬 코드. 등록된 HTTP 작업은 `ENABLE_DEV_API=true`에서 **32개**(명세 번호 대응 24개, 개발용 6개, 상태 확인 2개)다. 번호별 V1.1 대응과 한계는 [V1.1 로컬 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)를 따른다. 이 문서는 **Swagger 화면에서 누를 순서와 입력 방법**을 설명한다. 서버·DB 준비 명령은 [기존 로컬 테스트 가이드](SWAGGER_TEST_GUIDE.md)를 참고한다.

> JSON의 숫자 ID와 정책 버전 `v1`은 입력 형식을 보여주는 예시다. 팀원은 **자기 환경에서 GET으로 조회한 값**으로 바꾼다. 현재 서버가 실행 중이지 않으면 `/docs`를 열기 전에 백엔드와 PostgreSQL을 먼저 시작한다.

## 1. 모든 작업에 공통인 Swagger 조작

1. **[http://localhost:8000/docs](http://localhost:8000/docs)**를 연다. 세션 쿠키가 이어지도록 테스트 내내 `localhost`를 사용하고 `127.0.0.1`과 섞지 않는다.
2. 아래 표의 **Swagger 태그**를 펼치고 해당 메서드·경로를 클릭한다. **Try it out**을 누른다.
3. `Parameters`의 **path/query** 칸과, 필요한 작업에서만 `Request body`의 JSON을 입력한다. 빈 선택 query는 그대로 비운다. **Execute**를 누른다.
4. 아래 `Server response`의 **Code**와 **Response body**를 확인한다. 다음 요청에는 예시 ID가 아니라 여기서 받은 실제 `id`, `subtopicId`, `contentId`, `agentRunId`, `nextCursor`를 사용한다. **204**는 성공이어도 본문이 없다.
5. 변경 요청이 403 `CSRF_INVALID`라면 `GET /api/v1/auth/session`을 다시 실행하고 새 `csrfToken`을 우상단 **Authorize → CsrfToken**에 입력한다. `SessionCookie` 칸은 수동으로 채우지 않는다. 같은 브라우저의 HttpOnly 쿠키는 자동 전송된다.

### 테스트 모드 선택

| 모드 | `backend/.env` | 사용처 | Swagger 인증 |
| --- | --- | --- | --- |
| **A. 빠른 로컬 조회·피드·Agent 미리보기** | `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=true` | 데모 사용자 seed 후 보호 API와 탈락 초안 미리보기 | `Authorize` 불필요. 서버·브라우저 요청이 모두 루프백이어야 한다. |
| **B. 실제 세션·온보딩 흐름** | `ENABLE_DEV_API=true`, `ENABLE_DEV_AUTH_BYPASS=false` | 가입, 로그인, CSRF, 온보딩, 로그아웃 검증 | 익명 세션 조회 → `CsrfToken` 설정 → 로그인·온보딩 뒤 토큰 갱신. |

설정 변경 뒤에는 **백엔드를 재시작하고 `/docs`를 새로고침**한다. `ENABLE_DEV_API=false`이면 개발용 6개 작업은 Swagger와 라우트에서 사라진다. 모드 A의 데모 사용자는 이미 온보딩이 완료되어 있으므로, 신규 온보딩과 로그아웃 후 401 검사는 **모드 B**에서 한다.

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
5. `GET /api/v1/policies`에서 **표시된 모든 동의 항목과 실제 `policyVersion`**을 기록한다. `GET /api/v1/topics`와 `GET /api/v1/topics/{topicId}/subtopics`에서 실제 Topic/Subtopic ID를 얻는다. Topic·Subtopic은 로그인 뒤, 온보딩 전에도 조회할 수 있다.
6. `온보딩` → `POST /api/v1/onboarding`에 아래 JSON을 실제 ID·버전으로 바꿔 제출한다. **201**과 `onboardingCompleted=true`를 확인한다. 선택 동의도 배열에서 빼지 말고 원하지 않으면 `agreed:false`로 보낸다.
7. 다시 `GET /api/v1/auth/session`을 실행해 `sessionState="active"`를 확인하고 새 `csrfToken`으로 **Authorize**를 갱신한다.
8. 아래 전체 작업표에서 프로필·동의·관심사·피드·반응을 순서대로 실행한다. 오류 분기 422/404를 시험한다면 **로그아웃 전에** 실행한다.
9. 마지막에 `POST /api/v1/auth/logout`을 실행해 **204**를 확인한다. 이어서 세션 GET은 `anonymous`, 보호된 사용자 GET은 **401 `AUTH_REQUIRED`**여야 한다.

가입 입력 예시 (`POST /api/v1/dev/auth/register`):

```json
{"email":"swagger.tester01@example.com","password":"ExamplePass!26","nickname":"테스트회원","role":"consumer"}
```

로그인 입력 예시 (`POST /api/v1/dev/auth/login`):

```json
{"email":"swagger.tester01@example.com","password":"ExamplePass!26"}
```

온보딩 입력 예시 (`POST /api/v1/onboarding`):

```json
{
  "nickname": "테스트회원",
  "birthDate": "2000-01-01",
  "accountType": "consumer",
  "profileImageId": null,
  "topicId": 1,
  "subtopicIds": [1],
  "consents": [
    {"type":"terms","policyVersion":"v1","agreed":true},
    {"type":"privacy","policyVersion":"v1","agreed":true},
    {"type":"advertising","policyVersion":"v1","agreed":false},
    {"type":"marketing","policyVersion":"v1","agreed":false}
  ]
}
```

위 JSON은 로컬 seed의 형식 예시다. `GET /policies`가 반환한 항목이 달라졌다면 **그 항목 전부**를 제출한다. 필수 항목은 `agreed:true`여야 한다. Creator 계정은 온보딩 요청에서 `topicId`와 `subtopicIds`를 생략한다.

## 3. 전체 작업별 Swagger 입력·확인표

표의 모든 `/api/v1/...` 경로는 Swagger에 표시되는 **전체 경로**다. `Request body`가 적히지 않은 작업은 본문 없이 실행한다. `topicId`·`contentId` 등 숫자는 앞선 GET 결과로 바꾼다.

### 상태·인증·정책·온보딩 — 8개

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| — | 상태 확인 → `GET /health` | 없음 | **200** `status="ok"` |
| — | 상태 확인 → `GET /health/db` | 없음 | **200** `status="ok"`, `database=1` |
| — | 개발용 인증 → `POST /api/v1/dev/auth/register` | 위 가입 JSON. 모드 B에서는 익명 세션·CSRF 먼저 | **201** 새 `id`, `email`, `nickname`, `accountType`; 중복 이메일은 409 |
| — | 개발용 인증 → `POST /api/v1/dev/auth/login` | 위 로그인 JSON. 모드 B의 현재 CSRF | **200** `message`, `user`; 로그인 쿠키 발급. 이후 세션·CSRF 재조회 |
| API-005 | 인증·세션 → `GET /api/v1/auth/session` | 없음 | **200** `authenticated`, `sessionState`, `onboardingCompleted`, `csrfToken` |
| API-006 | 인증·세션 → `POST /api/v1/auth/logout` | 본문 없음. 모드 B의 현재 CSRF | **204** 본문 없음. 모드 B에서만 이후 보호 API 401 확인 |
| API-011 | 정책 → `GET /api/v1/policies` | 없음. 비로그인도 가능 | **200** `consentItems[]`의 `type`, `policyVersion`, `required` 기록 |
| API-004 | 온보딩 → `POST /api/v1/onboarding` | 위 온보딩 JSON. 모드 B의 `onboarding_pending` 세션·현재 CSRF | **201** `onboardingCompleted=true`, `activeTopic`; 이후 세션·CSRF 재조회 |

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

### 피드·콘텐츠·반응 — 8개

| 명세 번호 | Swagger 태그 → 작업 | Try it out 입력 | 정상 Code · Response body 확인 |
| --- | --- | --- | --- |
| API-028 | 피드 → `GET /api/v1/topics/{topicId}/feed` | path **활성** `topicId`; 선택 query `subtopicId`, `cursor`, `limit`(1~100, 기본 20) | **200** `sections[].contents[].id`, `nextCursor`; 빈 피드도 200 가능 |
| API-029 | 콘텐츠 → `GET /api/v1/contents/{contentId}` | path 카드의 `id`; **필수 query `topicId`** | **200** `contentId`, `displayMode`, `body`/`excerpt`, `myReaction` |
| API-035 | O/X 선호도 → `PUT /api/v1/contents/{contentId}/preference` | path `contentId`; JSON `{"topicId":1,"value":"positive"}` 또는 `negative` | **200** `value`; 상세 재조회에서 `myReaction.preference` 확인 |
| API-036 | O/X 선호도 → `DELETE /api/v1/contents/{contentId}/preference` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 상세 재조회에서 `preference=null` |
| API-037 | 좋아요 → `PUT /api/v1/contents/{contentId}/like` | path `contentId`; JSON `{"topicId":1}` | **200** `liked=true`; 피드·상세 재조회 |
| API-038 | 좋아요 → `DELETE /api/v1/contents/{contentId}/like` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 재조회에서 `liked=false` |
| API-039 | 북마크 → `PUT /api/v1/contents/{contentId}/bookmark` | path `contentId`; JSON `{"topicId":1}` | **200** `savedItemId`, `tags`, `savedAt`; 재조회에서 `saved=true`·`bookmarked=true` |
| API-040 | 북마크 → `DELETE /api/v1/contents/{contentId}/bookmark` | path `contentId`; **필수 query `topicId`**. 본문 없음 | **204** 본문 없음; 재조회에서 북마크 해제 |

반응 PUT/DELETE는 **모드 B의 현재 CSRF**가 필요하다. `topicId=1`은 예시이며, 콘텐츠가 속한 **내 활성 Topic ID**로 바꾼다. 피드의 `recommendationReason=null`, `seen=false`는 추천·노출 이벤트 미연결 상태에서 정상일 수 있다.

### 개발용 Agent 결과·발행 — 4개

Agent 새 실행을 만드는 Swagger 작업은 없다. 기존 실행이 없으면 [기존 가이드의 CLI 절차](SWAGGER_TEST_GUIDE.md#영화-agent-초안과-판정-조회)로 먼저 준비한다. 실행에는 외부 모델/TMDB 키와 사용량이 필요할 수 있다. 아래 작업은 `ENABLE_DEV_API=true`와 **로컬 요청**이 필요하다.

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
- **404 `CONTENT_NOT_FOUND`**, **422 `INVALID_CURSOR`**: 존재하지 않는 Content ID 또는 잘못된 피드 cursor. 모드 B라면 **로그아웃 전에** 시험해야 401에 가리지 않는다.
- **204**: DELETE와 로그아웃은 본문이 없는 것이 정상이다.

테스트 기록에는 모드 A/B, 실제 Topic·Subtopic·Content·AgentRun ID, 각 요청의 Code·오류 `code`, 피드/상세 재조회 결과를 남긴다. 현재 구현과 V1.1 계약의 차이는 [로컬 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)에서 확인한다.
