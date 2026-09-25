# Swagger UI에서 V1 로컬 API 테스트

Swagger에서 로그인 없이 기능을 확인하려면 로컬 개발 전용 인증 우회와 데모 사용자 seed를 사용합니다. PostgreSQL 마이그레이션과 두 seed가 필요합니다.

## 1. 서버와 데이터 준비

루트 `.env`, `backend/.env`의 DB 접속값을 맞춥니다. 기존 환경 파일을 덮어쓰지 말고, 새 설치일 때만 `.env.example`을 복사합니다. 로그인 없이 테스트할 때는 `backend/.env`에 `ENABLE_DEV_API=true`와 `ENABLE_DEV_AUTH_BYPASS=true`를 설정합니다. 서버는 아래처럼 `localhost`에만 바인딩합니다.

```powershell
# 저장소 루트
docker compose up -d postgres
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host localhost --port 8000
```

의존성이 아직 없다면 먼저 `backend/.venv`를 만들고 `pip install -r requirements.txt`를 실행합니다. Swagger UI는 [http://localhost:8000/docs](http://localhost:8000/docs), OpenAPI 원문은 [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json), ReDoc은 [http://localhost:8000/redoc](http://localhost:8000/redoc)입니다.

브라우저에서 Swagger UI도 `localhost:8000`으로 여세요. 인증 우회는 루프백 주소에서 보낸 요청에만 적용됩니다. `ENABLE_DEV_AUTH_BYPASS=false`로 되돌리면 일반 세션 인증이 다시 적용됩니다.

## 2. 로그인 없이 기능 테스트

두 개발용 설정이 `true`이고 데모 사용자 seed를 실행한 상태에서는 Swagger의 **Authorize**를 사용하거나 세션 쿠키와 `X-CSRF-Token` 헤더를 보낼 필요가 없습니다. 보호된 로컬 요청은 데모 사용자로 처리됩니다.

1. **정책 → GET `/api/v1/policies`**, **Topic → GET `/api/v1/topics`**로 준비된 데이터를 확인합니다.
2. **사용자 → GET `/api/v1/users/me`**, **내 관심사 → GET `/api/v1/me/topics`**로 데모 사용자와 활성 Topic ID를 확인합니다.
3. **피드 → GET `/api/v1/topics/{topicId}/feed`**를 호출하고, 응답의 실제 콘텐츠 ID로 **GET `/api/v1/contents/{contentId}?topicId={topicId}`**를 호출합니다.
4. 아래 표의 O/X·좋아요·북마크 PUT/DELETE를 호출한 뒤 피드와 상세에서 결과를 확인합니다.

온보딩과 로그인·로그아웃에 따른 인증 상태를 검증할 때는 `ENABLE_DEV_AUTH_BYPASS=false`로 바꾸고 서버를 다시 시작한 뒤 아래 일반 로그인 순서를 따릅니다.

## 영화 Agent 초안과 판정 조회

영화 Agent는 글을 먼저 내부 초안으로 저장합니다. 실행하려면 `agent/.env`에 `LLM_KEY`, `TMDB_KEY`가 있어야 하며 모델 API 사용량이 발생할 수 있습니다. `backend` 폴더에서 한 편을 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/run_movie_agent_once.py "기생충" 2019
```

Swagger의 **개발용 Agent 결과 → GET `/api/v1/dev/agent-runs`**에서 실행 목록을 조회하고, 반환된 `agentRunId`로 **GET `/api/v1/dev/agent-runs/{agentRunId}`**를 호출합니다. 상세 응답에는 초안의 제목·본문·상태, 형식 검사 단계, 판정 요약, 확인 필요 항목과 출처 URL이 포함됩니다. `draft=null`이면 해당 실행에서 글을 만들지 않은 것입니다.

`outcome=rejected_archive`인 글도 개발용 조회에서는 확인할 수 있지만, `draft.status=discarded`로 표시됩니다. `publish_candidate`로 승인되고 추가 사실 확인이 필요 없는 글은 저장 직후 공개 피드에 자동 발행되며, 성공하면 `draft.status=published`가 됩니다. 자동 발행에 실패한 초안은 `approved` 상태로 남으며 서버 로그에 이유가 기록됩니다. 이 조회 경로는 로컬 요청과 개발용 인증에서만 사용할 수 있습니다.

자동 발행된 글은 **GET `/api/v1/topics/{topicId}/feed`**에서 확인합니다. 이전 실행이나 자동 발행에 실패한 승인 초안을 다시 게시하려면 **POST `/api/v1/dev/agent-runs/{agentRunId}/publish`**를 호출합니다. 이 경로도 `publish_candidate`·`approved`이고 추가 사실 확인이 필요 없는 초안만 발행합니다. 탈락 초안을 로컬 화면에서 시험하려면 인증 생략 모드에서 **POST `/api/v1/dev/agent-runs/{agentRunId}/publish-preview`**를 명시적으로 호출합니다. 테스트 게시는 피드 요약에 표식을 남기고 `judgmentStatus=needs_review`로 저장됩니다. 같은 실행을 다시 게시해도 새 콘텐츠를 중복 생성하지 않습니다.

게시 응답의 `contentId`, `topicId`, `feedUrl`, `detailUrl`을 사용해 **GET `/api/v1/topics/{topicId}/feed`**와 **GET `/api/v1/contents/{contentId}?topicId=...`**를 확인합니다. 영화 Subtopic 필터에서도 보이려면 **GET `/api/v1/topics/{topicId}/subtopics`**에서 `영화 리뷰`의 ID를 확인해 피드의 `subtopicId`로 넣습니다. 상세 응답은 AI 원본 글의 `body`를 반환합니다.

## 3. 일반 로그인과 온보딩 테스트

이 절은 `ENABLE_DEV_AUTH_BYPASS=false`, `ENABLE_DEV_API=true`에서 실행합니다. 로그인은 Swagger의 **개발용 인증 → POST `/api/v1/dev/auth/login`**에 있습니다.

1. **인증·세션 → GET `/api/v1/auth/session`**을 실행합니다. 비로그인 상태도 200이며 `authenticated=false`, `sessionState=anonymous`, `csrfToken`이 반환됩니다. 이 요청이 익명 세션 쿠키도 발급합니다.
2. 응답의 `csrfToken`을 복사하고 Swagger의 **Authorize** 버튼에서 `X-CSRF-Token` API key에 입력합니다. 토큰은 문서나 로그에 붙여 넣지 마세요.
3. **개발용 인증 → POST `/api/v1/dev/auth/register`**로 새 이메일, 8자 이상의 비밀번호, 닉네임, `role=consumer`를 제출합니다. 새 회원은 201입니다. 이미 가입한 이메일이면 409이므로 기존 계정으로 로그인하거나 다른 이메일을 사용합니다.
4. **POST `/api/v1/dev/auth/login`**을 같은 계정으로 실행합니다. 성공하면 200과 HttpOnly 쿠키가 발급됩니다.
5. **GET `/api/v1/auth/session`**을 다시 실행합니다. `sessionState=onboarding_pending`을 확인하고 **새로 반환된 `csrfToken`으로 Authorize 값을 갱신**합니다. 로그인 과정에서 세션과 토큰이 바뀝니다.

개발용 인증 경로는 `ENABLE_DEV_API=false`일 때 라우트와 OpenAPI에서 사라집니다. 운영 OAuth 시작·콜백은 이번 로컬 범위에 없습니다.

### 정책·Topic 조회와 온보딩

1. **정책 → GET `/api/v1/policies`**의 `consentItems`에서 `type`, `policyVersion`, `required`를 확인합니다. `terms`와 `privacy`는 반드시 `agreed=true`로 제출합니다. `advertising`과 `marketing`은 선택입니다.
2. **Topic → GET `/api/v1/topics`**에서 사용하려는 Topic의 실제 `id`를 기록합니다.
3. **Subtopic → GET `/api/v1/topics/{topicId}/subtopics`**에 방금 조회한 Topic ID를 입력합니다. `items[].subtopicId`에서 같은 Topic의 Subtopic ID를 하나 이상 고릅니다. 필요하면 **GET `/api/v1/subtopics/{subtopicId}`**로 소속을 확인합니다.
4. **온보딩 → POST `/api/v1/onboarding`**에 닉네임, `birthDate`, `accountType=consumer`, `profileImageId=null`, 정책의 실제 버전·동의 상태, 선택한 `topicId`와 `subtopicIds`를 제출합니다. 성공은 201입니다. 임의의 ID나 과거 버전 문자열을 그대로 붙여 넣지 마세요.
5. **GET `/api/v1/auth/session`**을 다시 실행해 `sessionState=active`, `onboardingCompleted=true`를 확인합니다. `csrfToken`이 바뀌었다면 Authorize 값도 갱신합니다.

`profileImageId`는 현재 `null`만 지원합니다. 필수 동의 누락은 `REQUIRED_CONSENT_MISSING`, 버전 불일치는 `POLICY_VERSION_CONFLICT`, 다른 Topic의 Subtopic은 `SUBTOPIC_TOPIC_MISMATCH`, 중복 온보딩은 `ONBOARDING_ALREADY_COMPLETED` 오류로 확인할 수 있습니다.

## 4. 프로필·구독·피드

1. **사용자 → GET `/api/v1/users/me`**에서 프로필을 조회합니다. **PATCH**에는 `nickname`만 넣어 변경할 수 있습니다.
2. **내 관심사 → GET `/api/v1/me/topics`**에서 활성 Topic을 확인합니다. 새 Topic을 추가하려면 앞의 조회 API로 실제 Topic/Subtopic ID를 얻고 **POST `/api/v1/me/topics`**에 `topicId`와 `subtopicIds`를 보냅니다.
3. **GET `/api/v1/me/topics/{topicId}/subtopics`**에서 구독을 확인합니다. **PUT/DELETE `/api/v1/me/topics/{topicId}/subtopics/{subtopicId}`**로 설정·해제합니다. DELETE 성공은 본문 없는 204입니다.
4. **피드 → GET `/api/v1/topics/{topicId}/feed`**에 활성 Topic ID를 사용합니다. 응답은 `sections[].contents[]`와 `nextCursor`입니다. `subtopicId` 필터는 해당 Topic의 ID로만 지정합니다. `nextCursor`가 있으면 동일 필터로 다음 페이지를 요청합니다.
5. 피드 카드의 실제 `id`를 복사해 **콘텐츠 → GET `/api/v1/contents/{contentId}?topicId={topicId}`**를 호출합니다. 이 상세는 Agent의 Draft가 아닌 공개 Content입니다.

로컬 seed에 공개 콘텐츠가 없거나 해당 Topic에 맞는 콘텐츠가 없으면 피드는 200과 빈 `contents`를 반환할 수 있습니다. 추천 알고리즘과 이벤트 로그가 아직 연결되지 않아 `recommendationReason=null`, `seen=false`일 수 있습니다.

## 5. O/X·좋아요·북마크와 로그아웃

각 PUT에는 앞 단계에서 확인한 `contentId`와 `topicId`를 사용합니다. 인증 우회가 켜져 있으면 이 변경 요청에도 CSRF 헤더가 필요하지 않습니다.

| 기능 | 설정 요청 본문 | 취소 요청 |
| --- | --- | --- |
| O/X | `PUT /contents/{contentId}/preference` — `{"topicId": 실제 Topic ID, "value": "positive"}` 또는 `negative` | `DELETE /contents/{contentId}/preference?topicId=...` |
| 좋아요 | `PUT /contents/{contentId}/like` — `{"topicId": 실제 Topic ID}` | `DELETE /contents/{contentId}/like?topicId=...` |
| 북마크 | `PUT /contents/{contentId}/bookmark` — `{"topicId": 실제 Topic ID}` | `DELETE /contents/{contentId}/bookmark?topicId=...` |

표의 경로에는 모두 `/api/v1` 접두사가 붙습니다. Swagger가 생성한 JSON 입력란에서 `실제 Topic ID` 자리를 조회한 숫자로 바꿔 입력합니다. 설정과 취소 뒤 피드와 상세를 다시 요청해 `myReaction`, `saved`, `savedItemId` 또는 상세의 북마크 상태가 바뀌었는지 확인합니다. 각 DELETE는 204이며 JSON 본문이 없습니다.

일반 로그인 흐름에서는 마지막으로 **인증·세션 → POST `/api/v1/auth/logout`**을 실행합니다. 204 후 `GET /auth/session`은 비로그인을 반환하고, 보호 API는 401 `AUTH_REQUIRED`여야 합니다. 이 401 확인은 `ENABLE_DEV_AUTH_BYPASS=false`일 때만 유효합니다.

## 오류 확인

오류 응답은 `code`, `message`를 포함하며 필요 시 `fields`, `requestId`가 붙습니다. 주로 401 `AUTH_REQUIRED`, 403 `CSRF_INVALID` 또는 `ONBOARDING_REQUIRED`, 404 `TOPIC_NOT_FOUND`·`CONTENT_NOT_FOUND`, 409 상태 충돌, 422 `VALIDATION_ERROR`·`INVALID_CURSOR`를 확인합니다. 일반 인증에서 변경 요청이 403이면 현재 `GET /auth/session`의 토큰을 다시 Authorize에 입력합니다.
