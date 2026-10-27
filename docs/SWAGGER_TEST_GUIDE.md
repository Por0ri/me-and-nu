# Swagger UI에서 V1 로컬 API 테스트

주요 작업의 Swagger 화면 입력값과 예상 응답은 [Swagger UI 조작법](SWAGGER_UI_OPERATION_GUIDE.md)을 참고하세요. 현재 전체 등록 목록은 [API 정합화 표](API_V1_1_LOCAL_ALIGNMENT.md)를 따릅니다.

**생년월일 정책 보류 유지 (2026-10-27~28):** 수집·저장·연령 확인 방식은 통합 완료 후 결정합니다. 새 Pivot은 닉네임만 수집하고 생년월일 입력·검사·전송을 보류합니다. 생년월일 삭제 migration을 새로 적용하지 않으며 레거시 Consumer 화면·유틸리티는 기존 상태를 유지합니다.

**2026-10-28 V2 로컬 구현:** 작품 검색 → 장르·소재 후보 → 소비 유형을 Pivot 화면까지 연결했습니다. 온보딩 도입 migration은 `e3a7b9c2d451`이며 부모는 `c0e5f3b8a124`입니다. 현재 서비스 target은 캐러셀 확장을 포함한 e3의 자식 `f4b8c1d9a620`입니다. 기존 생년월일 삭제 `d2f6a9c4e731`과 분리한 복수 head이므로 준비 명령은 정확한 f4 revision을 사용합니다. `upgrade head` 또는 `upgrade heads`로 바꾸지 않습니다. 실제 작품 30개로 수행한 기존 가입 검증은 [프론트 연결 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md), 새 캐러셀 계약과 검증 범위는 [캐러셀 DB·API 문서](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)를 따릅니다. main 병합·푸시·원격 배포는 하지 않았습니다.

아래 요청 예시는 현재 로컬 코드용입니다. 이 코드에서는 온보딩에 `birthDate`나 연령 확인값을 받지 않고 `GET/PATCH /api/v1/users/me` 응답에도 `birthDate`가 없습니다. 기존 삭제 revision은 `d2f6a9c4e731`(이전 `c0e5f3b8a124`)이며 삭제된 값은 downgrade로 복원되지 않습니다. 실제 테스트는 대상 환경의 배포 계약과 [DB 적용 상태](ERD_API_ALIGNMENT_2026-10-27.md)를 확인한 뒤 진행합니다. 현재 코드 상태는 최종 수집 정책을 뜻하지 않습니다.

Swagger에서 로그인 없이 기능을 확인하려면 로컬 개발 전용 인증 우회와 데모 사용자 seed를 사용합니다. PostgreSQL 마이그레이션과 두 seed가 필요합니다.

## 1. 서버와 데이터 준비

루트 `.env`, `backend/.env`의 DB 접속값을 맞춥니다. 기존 환경 파일을 덮어쓰지 말고, 새 설치일 때만 `.env.example`을 복사합니다. 로그인 없이 테스트할 때는 `backend/.env`에 `ENABLE_DEV_API=true`와 `ENABLE_DEV_AUTH_BYPASS=true`를 설정합니다. 서버는 아래처럼 `localhost`에만 바인딩합니다.

```powershell
# 저장소 루트
docker compose up -d postgres
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade f4b8c1d9a620
.\.venv\Scripts\python.exe -m app.seeds.v1_local
.\.venv\Scripts\python.exe -m app.seeds.dev_user
.\.venv\Scripts\python.exe scripts/run_local_api.py
```

Windows용 실행 스크립트는 PostgreSQL 비동기 연결에 맞는 이벤트 루프를 사용하며 자동 재시작은 하지 않습니다. 코드 변경 후에는 기존 서버를 종료하고 다시 실행하세요. 이미 `http://localhost:8000/health/db`가 정상 응답하면 추가로 실행할 필요가 없습니다. 같은 포트를 중복 사용하면 `WinError 10013` 또는 `10048`이 발생할 수 있습니다.

의존성이 아직 없다면 먼저 `backend/.venv`를 만들고 `pip install -r requirements.txt`를 실행합니다. Swagger UI는 [http://localhost:8000/docs](http://localhost:8000/docs), OpenAPI 원문은 [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json), ReDoc은 [http://localhost:8000/redoc](http://localhost:8000/redoc)입니다.

브라우저에서 Swagger UI도 `localhost:8000`으로 여세요. 인증 우회는 루프백 주소에서 보낸 요청에만 적용됩니다. `ENABLE_DEV_AUTH_BYPASS=false`로 되돌리면 일반 세션 인증이 다시 적용됩니다.

### V2 작품 카탈로그 준비

위 `v1_local`·`dev_user` seed만으로 작품·장르·소재 사전이 준비되지는 않습니다. V2 테스트에는 활성 anime Topic과 그 Topic의 작품·활성 태그 매핑이 필요합니다. [실작품 시작 카탈로그](ONBOARDING_CATALOG_SOURCES_2026-10-28.md)는 `backend/app/seeds/onboarding_catalog_starter.json`에 작품 30개·태그 38개·연결 140개로 준비했습니다. 실제 PostgreSQL 적재와 반복 적재의 변경 0건도 확인했습니다. 대상 DB명을 확인한 뒤 [적재 도구 안내](ONBOARDING_V2_ERD_API_PLAN_2026-10-27.md#5-검토한-카탈로그의-적재-도구)에 따라 미리보기 후 적용합니다. 실작품 JSON에는 `--demo`를 붙이지 않습니다. 별도로 남아 있는 `[DEMO]` JSON은 명시적으로 선택하는 합성 fixture입니다.

검색 결과가 없거나 모든 작품이 `selectable:false`이면 임의 ID·태그로 가입을 통과시키지 말고 해당 Topic의 카탈로그를 확인합니다. 새 조회 두 개도 `onboarding_pending` 전용이므로 이미 완료된 데모 사용자의 인증 우회 모드에서는 V2 온보딩을 검증할 수 없습니다.

## 2. 로그인 없이 기능 테스트

두 개발용 설정이 `true`이고 데모 사용자 seed를 실행한 상태에서는 Swagger의 **Authorize**를 사용하거나 세션 쿠키와 `X-CSRF-Token` 헤더를 보낼 필요가 없습니다. 보호된 로컬 요청은 데모 사용자로 처리됩니다.

1. **정책 → GET `/api/v1/policies`**, **Topic → GET `/api/v1/topics`**로 준비된 데이터를 확인합니다.
2. **사용자 → GET `/api/v1/users/me`**, **내 관심사 → GET `/api/v1/me/topics`**로 데모 사용자와 활성 Topic ID를 확인합니다.
3. **피드 → GET `/api/v1/topics/{topicId}/feed`**를 호출하고, 응답의 실제 콘텐츠 ID로 **GET `/api/v1/contents/{contentId}?topicId={topicId}`**를 호출합니다.
4. 아래 표의 O/X·좋아요·북마크 PUT/DELETE를 호출한 뒤 피드와 상세에서 결과를 확인합니다.

온보딩과 로그인·로그아웃에 따른 인증 상태를 검증할 때는 `ENABLE_DEV_AUTH_BYPASS=false`로 바꾸고 서버를 다시 시작한 뒤 아래 일반 로그인 순서를 따릅니다.

## 캐러셀 DB·API 준비와 조회

현재 캐러셀 확장은 서비스 DB/API 범위이며 에이전트 생성 코드와 프론트 화면은 변경하지 않았습니다. [캐러셀 입력 묶음·운영자 검토 계약](CAROUSEL_DB_API_INTEGRATION_2026-10-28.md)에 따라 `backend`에서 다음 명령으로 먼저 검사합니다. 실제 대상의 migration·적재 승인이 없는 상태에서 `--apply`를 추가하지 않습니다.

```powershell
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database <대상_DB명>
# 실제 검토 기록이 있는 경우에도 먼저 dry-run
python -m app.seeds.carousel_contents --file <bundle.json> --expected-database <대상_DB명> --review-record-file <review.json>
```

기본은 읽기 전용입니다. 저장하면 검토 기록 없는 묶음은 Content·Draft·슬라이드를 `pending_review`로 보관하고 공개 API에서는 차단합니다. 원본 Agent의 `ready`·점수·`reviewEvidence`는 발행 승인이 아닙니다. 정확한 묶음 digest에 연결된 별도 운영자 승인 기록이 있어야 공개할 수 있습니다. Swagger에 캐러셀 import·승인 API는 없고 아래 기존 영화 Agent의 `publish`·`publish-preview`를 캐러셀 승인 수단으로 사용하지 않습니다.

검토되어 공개된 캐러셀을 별도로 준비한 환경에서 다음을 확인합니다.

1. 기존 피드 GET에서 실제 `contentId`, `topicId`, `slides[]`를 확인합니다. page는 1부터 연속이고 각 장에 `titleLines`, `bodySentences`, camelCase `layout`, `width=1308`, `height=1632`가 있어야 합니다.
2. 같은 Topic의 상세 GET에서 `displayMode=full_body`, 원본 상세 `body`, 전체 `sources[]`, `AI_GENERATED` 고지를 확인합니다. 기존 `sourceUrl`은 대표 출처로 유지합니다.
3. `GET /api/v1/contents/{contentId}/slides/{page}/image?topicId=...`에 응답의 실제 ID·page를 넣습니다. 세션이 있는 경우 `200 image/png`이며 `private, no-store`와 `nosniff` 헤더를 확인합니다. GET에는 CSRF가 필요하지 않고 본문을 JSON으로 파싱하지 않습니다.
4. `ENABLE_DEV_AUTH_BYPASS=false`에서 비로그인은 401, 가입 대기는 403을 확인합니다. 다른 Topic·보류·거절·만료·불완전 캐러셀이나 없는 page는 404, page 0/11 또는 필수 topicId 누락은 422여야 합니다. 만료·불완전 캐러셀은 피드·북마크 목록에도 나오지 않습니다.

슬라이드 없는 기존 글은 `slides:[]`이며 기존 `imageUrl`·`body`·`displayMode` 동작을 유지합니다. 이 순서는 Swagger/API 계약 확인이며 프론트의 HTML 오버레이·캐러셀 화면 검증을 의미하지 않습니다.

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
3. **개발용 인증 → POST `/api/v1/dev/auth/register`**로 새 이메일, 8자 이상의 비밀번호, 닉네임, `role=user`를 제출합니다. 새 회원은 201입니다. 이미 가입한 이메일이면 409이므로 기존 계정으로 로그인하거나 다른 이메일을 사용합니다.
4. **POST `/api/v1/dev/auth/login`**을 같은 계정으로 실행합니다. 성공하면 200과 HttpOnly 쿠키가 발급됩니다.
5. **GET `/api/v1/auth/session`**을 다시 실행합니다. `sessionState=onboarding_pending`을 확인하고 **새로 반환된 `csrfToken`으로 Authorize 값을 갱신**합니다. 로그인 과정에서 세션과 토큰이 바뀝니다.

개발용 인증 경로는 `ENABLE_DEV_API=false`일 때 라우트와 OpenAPI에서 사라집니다. OAuth 시작·콜백은 별도의 공개 경로로 등록되며 공급자 설정은 기본 비활성입니다. [OAuth 백엔드 단독 검증](OAUTH_T02_1_SETUP.md)의 앱 등록·마이그레이션·Swagger 절차를 따릅니다.

### 정책·Topic 조회와 V2 온보딩

1. **정책 → GET `/api/v1/policies`**의 `consentItems`에서 모든 `type`, 실제 `policyVersion`, `required`를 확인합니다. 필수 항목은 `agreed=true`, 원하지 않는 선택 항목은 `agreed=false`로 제출합니다. 예시의 과거 정책 버전을 그대로 쓰지 않습니다.
2. **Topic → GET `/api/v1/topics`**에서 사용하려는 Topic의 실제 `id`를 기록합니다.
3. **온보딩 → GET `/api/v1/onboarding/works`**에 실제 `topicId`, 작품명·별칭 일부를 `q`로 입력합니다. `limit`은 기본 20, 범위 1~50입니다. 빈 검색어는 빈 목록입니다. 응답 `items[]`에서 `selectable:true`인 작품을 1~5개 고르고 실제 `entityId`를 기록합니다. `nextCursor`는 같은 Topic·검색어의 다음 페이지에서만 사용합니다.
4. **POST `/api/v1/onboarding/preference-options`**에 `{"topicId": 실제 숫자, "entityIds": [선택한 작품 ID들]}`을 JSON 숫자로 입력합니다. 현재 pending 세션과 Authorize의 CSRF가 필요합니다. 응답 `genres`·`themes` 합계에서 최소 1개를 선택하고 `tagId`를 기록합니다. 최대 선택 수는 없으며 종류별로 하나씩 고를 필요도 없습니다. 후보 조회만으로 선택·동의·답변을 저장하지 않습니다.
5. Q3 `consumptionType`은 `world`, `relationships`, `ost`, `goods`, `events`, `news` 중 정확히 1개를 고릅니다. Q1 작품을 바꾸면 후보를 다시 조회하고 현재 후보에 있는 태그만 남깁니다.
6. **POST `/api/v1/onboarding/complete`**에 닉네임, `accountType:user`, 실제 정책 동의, `topicId`와 아래 V2 `answers`를 제출합니다. 전체 JSON은 [Swagger 조작법의 V2 예시](SWAGGER_UI_OPERATION_GUIDE.md#b-신규-계정부터-로그아웃까지)를 참고합니다. 성공은 201이며 `answers.version=2`, 선택 ID 집합·소비 유형 일치, `activeTopic.subtopicIds=[]`를 확인합니다. ID 배열은 응답에서 오름차순으로 반환됩니다.
7. **GET `/api/v1/auth/session`**을 다시 실행해 `sessionState=active`, `onboardingCompleted=true`를 확인하고 교체된 `csrfToken`으로 Authorize를 갱신합니다. 완료된 계정의 상태나 답변을 초기화하여 테스트를 반복하지 않습니다.

V2 답변 부분 예시입니다. `101`, `102`, `201`, `301`은 형식 설명용이며 검색·후보 응답의 실제 ID로 바꿉니다.

```json
{
  "version": 2,
  "favoriteEntityIds": [101, 102],
  "preferenceTagIds": [201, 301],
  "consumptionType": "world"
}
```

온보딩의 `topicId`와 작품·태그 ID는 양의 BIGINT JSON 정수이며 문자열·실수·bool은 거절합니다. `version`도 정수 2만 허용합니다. 작품 0개/6개·중복·미존재·비활성·다른 Topic·태그 미준비 작품, 태그 0개·중복·현재 후보 밖, 잘못된 소비 유형은 422 `VALIDATION_ERROR`입니다. `fields[].field`의 `answers.favoriteEntityIds`, `answers.preferenceTagIds`, `answers.consumptionType` 등을 확인합니다. 후보 조회 후 카탈로그가 바뀌면 완료 단계의 재검증에서 거절될 수 있으므로 후보를 다시 조회합니다.

`profileImageId`, `subtopicIds`, `birthDate`는 `null`도 포함해 전송하면 422이므로 항목 자체를 제거합니다. 필수 동의 누락은 `REQUIRED_CONSENT_MISSING`, 정책 버전 불일치는 `POLICY_VERSION_CONFLICT`, 중복 완료는 `ONBOARDING_ALREADY_COMPLETED`입니다. Q2 태그와 Q3 소비 유형은 알림·마케팅 동의가 아닙니다.

### 기존 V1 요청의 호환 검증

전환 기간에는 버전 없는 `answers:{"q1":"world","q2":"releases"}`도 같은 완료 경로에서 V1로 처리하며 기존 형식의 답변을 반환합니다. V1의 Q1은 `world/relationships/ost/goods/events/news`, Q2는 `releases/characters/restock/events` 중 각각 단일 문자열입니다. 두 질문 모두 `events`를 고르는 것도 가능합니다. 명시적인 `version:1`, V1·V2 필드 혼합, 버전 없는 V2는 거절합니다. 이 검사는 과거 답변 계약의 API 호환 확인이며, 현재 Pivot은 V2를 제출합니다. 레거시 Consumer 전체가 새 API와 호환된다는 뜻은 아닙니다.

현재 로컬 API는 `birthDate`도 `null`을 포함해 제출하면 422 `VALIDATION_ERROR`를 반환하며 연령 확인용 대체 필드는 없습니다. Pivot의 새 프로필 단계는 닉네임만 받으며 제출에서 `birthDate`를 제외해 기존 422 불일치를 해소했습니다. 레거시 Consumer는 변경하지 않았습니다. 수집·저장·연령 확인 정책의 최종 방향은 통합 완료 후 결정합니다.

프로필 이미지 입력 제거에는 DB 변경이나 추가 마이그레이션이 필요하지 않습니다. 이 변경은 온보딩에만 적용하며 별도 프로필 수정 API와 이미지 업로드 백로그는 변경하지 않습니다.

2026-10-21 당시 계약은 `onboarding_answers` 추가 migration `c0e5f3b8a124`를 사용했습니다. 현재 V2에는 그 뒤 온보딩 전용 분기 `e3a7b9c2d451`이 필요합니다. 대상 DB의 실제 적용 revision을 확인하고 서버를 재시작해 새 Swagger 요청을 사용합니다. 기존 세부 관심사 데이터와 아래 구독 API는 유지되며 신규 V2도 세부 관심사를 자동 구독하지 않습니다. 현재 Pivot의 검색·후보는 [onboarding-catalog.ts](../frontend/lib/pivot/onboarding-catalog.ts)의 `serviceCatalog`, 제출은 [onboarding-service.ts](../frontend/lib/pivot/onboarding-service.ts)의 `serviceOnboardingService.submitSelection`에 연결되어 있습니다.

### 2026-10-28 화면 연결 검증

실제 로컬 API·PostgreSQL에 연결한 모바일·데스크톱 Pivot에서 작품 1개·태그 1개와 작품 5개·태그 12개 가입을 확인했습니다. 프론트 테스트 259개, TypeScript 검사와 production build가 통과했습니다. 이 결과는 Swagger를 수동으로 모두 실행했다거나 실제 OAuth 공급자·운영 배포를 검증했다는 뜻이 아닙니다. 환경과 요청·저장 확인 범위는 [프론트 연결 검증 기록](ONBOARDING_FRONTEND_INTEGRATION_2026-10-28.md)을 참고합니다.

## 4. 프로필·구독·피드

1. **사용자 → GET `/api/v1/users/me`**에서 프로필을 조회합니다. **PATCH**에는 `nickname`만 넣어 변경할 수 있습니다. 두 응답 모두 `birthDate` 키가 없는지 확인합니다.
2. **내 관심사 → GET `/api/v1/me/topics`**에서 활성 Topic을 확인합니다. 새 Topic을 추가하려면 앞의 조회 API로 실제 Topic/Subtopic ID를 얻고 **POST `/api/v1/me/topics`**에 `topicId`와 `subtopicIds`를 보냅니다.
3. **GET `/api/v1/me/topics/{topicId}/subtopics`**에서 구독을 확인합니다. **PUT/DELETE `/api/v1/me/topics/{topicId}/subtopics/{subtopicId}`**로 설정·해제합니다. DELETE 성공은 본문 없는 204입니다.
4. **피드 → GET `/api/v1/topics/{topicId}/feed`**에 활성 Topic ID를 사용합니다. 응답은 `sections[].contents[]`와 `nextCursor`입니다. `subtopicId` 필터는 해당 Topic의 ID로만 지정합니다. `nextCursor`가 있으면 동일 필터로 다음 페이지를 요청합니다.
5. 피드 카드의 실제 `id`를 복사해 **콘텐츠 → GET `/api/v1/contents/{contentId}?topicId={topicId}`**를 호출합니다. 이 상세는 Agent의 Draft가 아닌 공개 Content입니다. 응답과 Swagger의 Schema·Example Value에 `channel` 키가 없는지 확인합니다. 기존 DB에 채널이 연결된 콘텐츠도 동일합니다. 캐러셀이면 `slides[]`·`sources[]`와 앞 절의 보호된 이미지 GET을 추가 확인합니다.

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
